from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
import sqlite3
from contextlib import closing
import tempfile
import unittest

from PIL import Image

import app.database as database
from app.catalog_import.adapters.ikea_jo_ceiling_lights import (
    IkeaJordanCeilingLightsAdapter,
)
from app.catalog_import.adapters.leaders_jo import LeadersJordanAdapter
from app.catalog_import.adapters.midas_jo import MidasJordanAdapter
from app.catalog_import.adapters.shahwan_jo import ShahwanJordanAdapter
from app.catalog_import.base import RetailerAdapter
from app.catalog_import.checkpoint import CheckpointStore
from app.catalog_import.http import CachedResponse
from app.catalog_import.media import ProductImageStore, validate_image_bytes
from app.catalog_import.models import DiscoveredProduct, NormalizedProduct
from app.catalog_import.policy import (
    EligibilityDecision,
    evaluate_design_eligibility,
    has_floor_footprint,
)
from app.catalog_import.repository import CatalogRepository
from app.catalog_import.runner import CatalogImportRunner, ImportOptions


FIXTURES = Path(__file__).parent / "fixtures" / "catalog_import"


def fixture_text(*parts: str) -> str:
    return FIXTURES.joinpath(*parts).read_text(encoding="utf-8")


def fixture_json(*parts: str):
    return json.loads(fixture_text(*parts))


class FixtureClient:
    def __init__(self):
        self.texts: dict[str, str] = {}
        self.json_values: dict[str, object] = {}
        self.posts: dict[int, object] = {}

    def get_text(self, url: str, **_kwargs) -> str:
        return self.texts[url]

    def get_json(self, url: str, **_kwargs):
        return self.json_values[url]

    def post_json(self, _url: str, payload, **_kwargs):
        return self.posts[payload["components"][0]["window"]["offset"]]


class RetailerFixtureTests(unittest.TestCase):
    def test_ikea_window_pagination_and_product_detail(self):
        adapter = IkeaJordanCeilingLightsAdapter()
        adapter.window_size = 1
        client = FixtureClient()
        client.posts = {
            0: fixture_json("ikea", "search_page_1.json"),
            1: fixture_json("ikea", "search_page_2.json"),
        }
        products = list(adapter.discover(client))
        self.assertEqual(len(products), 2)
        self.assertEqual(adapter.last_discovery_pages_scanned, 2)
        self.assertEqual(products[0].normalized_category, "pendant_light")
        self.assertEqual(products[1].normalized_category, "ceiling_spotlight")
        self.assertFalse(products[1].availability_hint)

        client.texts[products[0].url] = fixture_text("ikea", "product.html")
        parsed = adapter.parse_product(products[0], client)
        self.assertEqual(parsed.source_product_id, "105.224.81")
        self.assertEqual(parsed.current_price, Decimal("4.99"))
        self.assertEqual(parsed.original_price, Decimal("6.99"))
        self.assertEqual(parsed.diameter_cm, Decimal("46"))
        self.assertEqual(parsed.height_cm, Decimal("24"))
        self.assertEqual(parsed.mounting_type, "ceiling")
        self.assertTrue(parsed.availability)

    def test_leaders_path_pagination_and_store_api_parse(self):
        adapter = LeadersJordanAdapter()
        start = adapter.category_pages["televisions"]
        client = FixtureClient()
        client.texts[start] = fixture_text("leaders", "category_page_1.html")
        client.texts[start + "page/2/"] = fixture_text("leaders", "category_page_2.html")
        products = list(adapter.discover(client, ["televisions"]))
        self.assertEqual(len(products), 1)
        self.assertNotIn("?page=", client.texts)
        self.assertNotIn("monitor", products[0].url)
        first = products[0]
        client.texts[first.url] = fixture_text("leaders", "product.html")
        client.json_values[adapter.store_api_base + "412436"] = fixture_json(
            "leaders", "product.json"
        )
        parsed = adapter.parse_product(first, client)
        self.assertEqual(parsed.source_product_id, "0193554610")
        self.assertEqual(parsed.current_price, Decimal("599"))
        self.assertEqual(parsed.original_price, Decimal("699"))
        self.assertEqual(parsed.width_cm, Decimal("145"))
        self.assertEqual(parsed.depth_cm, Decimal("8"))
        self.assertEqual(parsed.height_cm, Decimal("83"))
        self.assertEqual(parsed.model_number, "65Q71Q")
        self.assertEqual(parsed.category, "television")
        self.assertNotIn("srsltid", parsed.canonical_url)

    def test_leaders_discovery_ignores_wordpress_trash_placeholders(self):
        adapter = LeadersJordanAdapter()
        start = adapter.category_pages["televisions"]
        client = FixtureClient()
        client.texts[start] = """
            <a href="https://leaders.jo/en/product/__trashed-289">Removed</a>
            <a href="https://leaders.jo/en/product/real-tv">Real TV</a>
        """

        products = list(adapter.discover(client, ["televisions"]))

        self.assertEqual([product.url for product in products], [
            "https://leaders.jo/en/product/real-tv"
        ])

    def test_shahwan_load_more_price_sku_and_unknown_stock(self):
        adapter = ShahwanJordanAdapter()
        start = adapter.category_pages["coffee_tables"]
        second = "https://shahwan.net/shop/category/25/page/2"
        client = FixtureClient()
        client.texts[start] = fixture_text("shahwan", "category_page_1.html")
        client.texts[second] = fixture_text("shahwan", "category_page_2.html")
        products = list(adapter.discover(client, ["coffee_tables"]))
        self.assertEqual(len(products), 2)
        client.texts[products[0].url] = fixture_text("shahwan", "product.html")
        parsed = adapter.parse_product(products[0], client)
        self.assertEqual(parsed.source_product_id, "16538/1")
        self.assertEqual(parsed.current_price, Decimal("156.4"))
        self.assertEqual(parsed.original_price, Decimal("170.0"))
        self.assertEqual(parsed.category, "coffee_table")
        self.assertEqual(parsed.width_cm, Decimal("100"))
        self.assertEqual(parsed.depth_cm, Decimal("60"))
        self.assertEqual(parsed.height_cm, Decimal("45"))
        self.assertIsNone(parsed.availability)

    def test_midas_magento_pagination_and_product_parse(self):
        adapter = MidasJordanAdapter()
        start = adapter.category_pages["living_room"]
        second = start + "?p=2"
        client = FixtureClient()
        client.texts[start] = fixture_text("midas", "category_page_1.html")
        client.texts[second] = fixture_text("midas", "category_page_2.html")
        products = list(adapter.discover(client, ["living_room"]))
        self.assertEqual(len(products), 2)
        client.texts[products[0].url] = fixture_text("midas", "product.html")
        parsed = adapter.parse_product(products[0], client)
        self.assertEqual(parsed.source_product_id, "167140")
        self.assertEqual(parsed.current_price, Decimal("821.28"))
        self.assertEqual(parsed.original_price, Decimal("1368.80"))
        self.assertEqual(parsed.category, "sofa")
        self.assertEqual(parsed.width_cm, Decimal("228"))
        self.assertEqual(parsed.depth_cm, Decimal("96"))
        self.assertEqual(parsed.height_cm, Decimal("88"))
        self.assertTrue(parsed.availability)

    def test_midas_sets_remain_sets_and_shop_only(self):
        category, tags, is_set = MidasJordanAdapter._category(
            "SECTIONAL SOFA 5 PCS DARK GREY W132 D89 H69 CM", "living_room"
        )
        self.assertEqual(category, "shop_only")
        self.assertIn("sectional_sofa", tags)
        self.assertTrue(is_set)

    def test_midas_specific_categories_win_over_generic_arabic_terms(self):
        dining_category, _, _ = MidasJordanAdapter._category(
            "كرسي طعام لون بيج العرض 50 العمق 55 الارتفاع 88 سم",
            "dining",
        )
        wardrobe_category, _, _ = MidasJordanAdapter._category(
            "خزانة ملابس 6 أبواب العرض 268 العمق 61 الارتفاع 220 سم",
            "bedroom",
        )
        self.assertEqual(dining_category, "dining_chair")
        self.assertEqual(wardrobe_category, "wardrobe")


class MediaValidationTests(unittest.TestCase):
    @staticmethod
    def png_bytes() -> bytes:
        output = BytesIO()
        Image.new("RGB", (4, 3), (20, 40, 60)).save(output, format="PNG")
        return output.getvalue()

    def test_valid_image_is_hashed_and_reused(self):
        validated = validate_image_bytes(self.png_bytes(), "image/png")
        with tempfile.TemporaryDirectory() as directory:
            store = ProductImageStore(Path(directory))
            first = store.store_validated(validated, store_slug="fixture_store")
            second = store.store_validated(validated, store_slug="another_store")
            self.assertTrue(first.downloaded)
            self.assertFalse(first.reused)
            self.assertEqual(second.filename, first.filename)
            self.assertTrue(second.reused)
            self.assertEqual((Path(directory) / first.filename).read_bytes(), self.png_bytes())

    def test_html_or_mismatched_content_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_bytes(b"<html>error</html>", "text/html")
        with self.assertRaises(ValueError):
            validate_image_bytes(b"<html>error</html>", "image/jpeg")


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "catalog.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        database.init_db()
        self.repository = CatalogRepository(self.db_path)

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.temp.cleanup()

    @staticmethod
    def product(**overrides) -> NormalizedProduct:
        values = dict(
            adapter_id="fixture",
            source_store="Fixture Store",
            source_store_slug="fixture_store",
            source_product_id="SKU-1",
            canonical_url="https://example.test/product/one?utm_source=test",
            name="Fixture Sofa",
            category="sofa",
            current_price=Decimal("100"),
            currency="JOD",
            availability=True,
            image_urls=["https://example.test/image.png"],
            width_cm=Decimal("200"),
            depth_cm=Decimal("90"),
            height_cm=Decimal("80"),
            room_types=["living_room"],
        )
        values.update(overrides)
        return NormalizedProduct(**values)

    def test_store_scoped_upsert_is_idempotent_and_updates_unavailable(self):
        eligible = EligibilityDecision(True, ())
        first = self.repository.upsert(
            self.product(), decision=eligible, image_filename="fixture.png",
            image_sha256="abc", run_id="run-1"
        )
        second = self.repository.upsert(
            self.product(), decision=eligible, image_filename="fixture.png",
            image_sha256="abc", run_id="run-2"
        )
        changed = self.repository.upsert(
            self.product(current_price=Decimal("90")), decision=eligible,
            image_filename="fixture.png", image_sha256="abc", run_id="run-3"
        )
        unavailable_product = self.product(
            current_price=Decimal("90"), availability=False
        )
        unavailable = self.repository.upsert(
            unavailable_product,
            decision=EligibilityDecision(False, ("unavailable",)),
            image_filename="fixture.png", image_sha256="abc", run_id="run-4"
        )
        self.assertEqual(
            (first.action, second.action, changed.action, unavailable.action),
            ("inserted", "unchanged", "updated", "updated"),
        )
        with closing(sqlite3.connect(self.db_path)) as connection:
            row = connection.execute(
                "SELECT COUNT(*), availability, design_eligible, catalog_status "
                "FROM furniture WHERE source_store_slug = 'fixture_store'"
            ).fetchone()
        self.assertEqual(row, (1, 0, 0, "shop_only"))

    def test_same_identity_in_another_store_is_not_deduplicated(self):
        eligible = EligibilityDecision(True, ())
        self.repository.upsert(
            self.product(), decision=eligible, image_filename="fixture.png",
            image_sha256="abc", run_id="run-1"
        )
        other = self.product(
            source_store="Other Store",
            source_store_slug="other_store",
        )
        outcome = self.repository.upsert(
            other, decision=eligible, image_filename="fixture.png",
            image_sha256="abc", run_id="run-1"
        )
        self.assertEqual(outcome.action, "inserted")

    def test_full_sync_deactivation_is_scoped_to_the_adapter(self):
        eligible = EligibilityDecision(True, ())
        self.repository.upsert(
            self.product(), decision=eligible, image_filename="fixture.png",
            image_sha256="abc", run_id="old-fixture-run"
        )
        self.repository.upsert(
            self.product(
                adapter_id="other_adapter",
                source_product_id="SKU-2",
                canonical_url="https://example.test/product/two",
                name="Other Adapter Sofa",
            ),
            decision=eligible,
            image_filename="other.png",
            image_sha256="def",
            run_id="old-other-run",
        )

        deactivated = self.repository.deactivate_missing(
            store_slug="fixture_store",
            adapter_id="fixture",
            run_id="new-fixture-run",
        )

        self.assertEqual(deactivated, 1)
        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = connection.execute(
                "SELECT source_product_id, availability, catalog_status "
                "FROM furniture ORDER BY source_product_id"
            ).fetchall()
        self.assertEqual(
            rows,
            [
                ("SKU-1", 0, "shop_only"),
                ("SKU-2", 1, "active"),
            ],
        )


class EligibilityPolicyTests(unittest.TestCase):
    def test_ceiling_light_is_eligible_without_floor_footprint(self):
        product = NormalizedProduct(
            adapter_id="fixture", source_store="IKEA Jordan", source_store_slug="ikea_jo",
            source_product_id="1", canonical_url="https://example.test/light",
            name="Ceiling light", category="ceiling_light", current_price=Decimal("10"),
            availability=True, image_urls=["https://example.test/light.png"],
            diameter_cm=Decimal("40"), height_cm=Decimal("10"),
            mounting_type="ceiling", room_types=["living_room"]
        )
        decision = evaluate_design_eligibility(product, has_valid_local_image=True)
        self.assertTrue(decision.eligible)
        self.assertFalse(has_floor_footprint(product))

    def test_unknown_stock_or_product_set_is_shop_only(self):
        product = RepositoryTests.product(availability=None, is_product_set=True)
        decision = evaluate_design_eligibility(product, has_valid_local_image=True)
        self.assertFalse(decision.eligible)
        self.assertIn("availability_unknown", decision.reasons)
        self.assertIn("ambiguous_product_set", decision.reasons)


class _FailureIsolationAdapter(RetailerAdapter):
    adapter_id = "failure_fixture"
    source_store = "Failure Fixture"
    source_store_slug = "failure_fixture"
    base_url = "https://example.test"
    category_pages = {"fixtures": "https://example.test/category"}
    category_mapping = {"fixtures": "sofa"}

    def discover(self, _client, category_filter=None):
        return [
            DiscoveredProduct("https://example.test/good", source_product_id="good", normalized_category="sofa"),
            DiscoveredProduct("https://example.test/bad", source_product_id="bad", normalized_category="sofa"),
        ]

    def parse_product(self, reference, _client):
        if reference.source_product_id == "bad":
            raise ValueError("fixture parse failure")
        return RepositoryTests.product(
            adapter_id=self.adapter_id,
            source_store=self.source_store,
            source_store_slug=self.source_store_slug,
            source_product_id="good",
            canonical_url=reference.url,
            image_urls=[],
        )


class RunnerIsolationTests(RepositoryTests):
    def test_failed_product_does_not_roll_back_successful_product(self):
        runner = CatalogImportRunner(
            repository=self.repository,
            image_store=ProductImageStore(Path(self.temp.name) / "media"),
            checkpoint_store=CheckpointStore(Path(self.temp.name) / "checkpoints"),
            report_dir=Path(self.temp.name) / "reports",
            http_client=object(),
        )
        report = runner.run(_FailureIsolationAdapter(), ImportOptions(dry_run=False))
        self.assertEqual(report.inserted_products, 1)
        self.assertEqual(report.parsing_failures, 1)
        self.assertEqual(self.repository.count_store("failure_fixture"), 1)


if __name__ == "__main__":
    unittest.main()
