from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from app.catalog_import.base import RetailerAdapter, SkipProduct
from app.catalog_import.http import CachedHttpClient
from app.catalog_import.models import DiscoveredProduct, NormalizedProduct
from app.catalog_import.parsing import (
    canonicalize_url,
    clean_text,
    parse_labeled_dimensions,
)
from app.catalog_import.policy import default_room_types


class LeadersJordanAdapter(RetailerAdapter):
    adapter_id = "leaders_jo"
    source_store = "Leaders Center"
    source_store_slug = "leaders_jo"
    base_url = "https://leaders.jo/en/"
    store_api_base = "https://leaders.jo/wp-json/wc/store/v1/products/"
    category_pages = {
        "televisions": "https://leaders.jo/en/product-category/all-products/home-appliance/tv-and-video-screens/tv-screens/",
        "refrigerators": "https://leaders.jo/en/product-category/all-products/refrigerators-en/",
        "washing_machines": "https://leaders.jo/en/product-category/all-products/home-appliance/washing-machines/",
        "dryers": "https://leaders.jo/en/product-category/all-products/home-appliance/dryers/",
        "dishwashers": "https://leaders.jo/en/product-category/all-products/home-appliance/dishwashers/",
        "gas_ovens": "https://leaders.jo/en/product-category/all-products/home-appliance/gas-ovens/",
        "built_in": "https://leaders.jo/en/product-category/all-products/home-appliance/built-in/",
    }
    category_mapping = {
        "televisions": "television",
        "refrigerators": "refrigerator",
        "washing_machines": "washing_machine",
        "dryers": "dryer",
        "dishwashers": "dishwasher",
        "gas_ovens": "freestanding_cooker",
        "built_in": "shop_only",
    }

    def discover(
        self,
        client: CachedHttpClient,
        category_filter: Sequence[str] | None = None,
    ) -> Iterable[DiscoveredProduct]:
        selected = self.selected_category_pages(category_filter)
        discovered: list[DiscoveredProduct] = []
        seen: set[str] = set()
        self.last_discovery_pages_scanned = 0
        for source_category, start_url in selected.items():
            page_number = 1
            while True:
                page_url = (
                    start_url
                    if page_number == 1
                    else urljoin(start_url, f"page/{page_number}/")
                )
                html_text = client.get_text(page_url, max_bytes=6_000_000)
                parser = _LeadersCategoryParser()
                parser.feed(html_text)
                self.last_discovery_pages_scanned += 1
                new_count = 0
                for raw_url, hint in parser.products.items():
                    canonical = canonicalize_url(raw_url, base_url=page_url)
                    parsed = urlparse(canonical)
                    if (
                        "/en/product/" not in parsed.path
                        or "/__trashed" in parsed.path.casefold()
                        or canonical in seen
                    ):
                        continue
                    normalized_hint = str(hint or "").casefold()
                    if source_category == "televisions" and (
                        "monitor" in normalized_hint
                        or "computer screen" in normalized_hint
                    ):
                        # Category pages currently mix computer monitors into
                        # televisions. Exclude them before --limit is applied.
                        continue
                    seen.add(canonical)
                    normalized = self.category_mapping[source_category]
                    discovered.append(
                        DiscoveredProduct(
                            url=canonical,
                            source_category=source_category,
                            normalized_category=normalized,
                            name_hint=clean_text(hint),
                        )
                    )
                    new_count += 1
                if not parser.has_next or new_count == 0:
                    break
                page_number += 1
        return discovered

    @staticmethod
    def _minor_price(prices: dict[str, Any], key: str) -> Decimal | None:
        raw = clean_text(prices.get(key))
        if not raw or not re.fullmatch(r"-?\d+", raw):
            return None
        minor_unit = int(prices.get("currency_minor_unit", 2))
        return Decimal(raw) / (Decimal(10) ** minor_unit)

    @staticmethod
    def _category(reference: DiscoveredProduct, payload: dict[str, Any]) -> str:
        if reference.source_category != "built_in":
            return reference.normalized_category or "shop_only"
        slugs = {
            str(category.get("slug", "")).casefold()
            for category in payload.get("categories", [])
            if isinstance(category, dict)
        }
        if any("refriger" in slug for slug in slugs):
            return "refrigerator"
        if any("oven" in slug for slug in slugs):
            return "oven"
        if any("gas" in slug or "cooktop" in slug for slug in slugs):
            return "cooktop"
        return "shop_only"

    @staticmethod
    def _plain_html(value: str | None) -> str | None:
        if not value:
            return None
        parser = _TextParser()
        parser.feed(value)
        return clean_text(" ".join(parser.parts))

    def parse_product(
        self,
        reference: DiscoveredProduct,
        client: CachedHttpClient,
    ) -> NormalizedProduct:
        product_page = client.get_text(reference.url, max_bytes=3_000_000)
        match = re.search(r"\bpostid-(\d+)\b", product_page)
        if not match:
            raise ValueError("Leaders page has no public WordPress product ID")
        post_id = match.group(1)
        payload = client.get_json(self.store_api_base + post_id, max_bytes=8_000_000)
        name = clean_text(payload.get("name"))
        if not name:
            raise ValueError("Leaders product has no name")
        lowered_name = name.casefold()
        if reference.source_category == "televisions" and (
            "monitor" in lowered_name or "computer screen" in lowered_name
        ):
            raise SkipProduct("out_of_scope_monitor")

        prices = payload.get("prices") or {}
        if clean_text(prices.get("currency_code")) != "JOD":
            raise ValueError("Leaders product is not priced in JOD")
        current_price = self._minor_price(prices, "price")
        if current_price is None:
            raise ValueError("Leaders product has no public price")
        regular_price = self._minor_price(prices, "regular_price")
        original_price = (
            regular_price
            if regular_price is not None and regular_price > current_price
            else None
        )
        purchasable = payload.get("is_purchasable")
        in_stock = payload.get("is_in_stock")
        availability = (
            False
            if purchasable is False or in_stock is False
            else True
            if purchasable is True and in_stock is True
            else None
        )
        description = self._plain_html(
            " ".join(
                value
                for value in (
                    payload.get("short_description"),
                    payload.get("description"),
                )
                if value
            )
        )
        dimensions = parse_labeled_dimensions(description)
        source_id = clean_text(payload.get("sku"))
        if not source_id:
            source_id = post_id
        category = self._category(reference, payload)
        images = [
            str(image.get("src"))
            for image in payload.get("images", [])
            if isinstance(image, dict) and image.get("src")
        ]
        brands = [
            clean_text(value.get("name"))
            for value in payload.get("brands", [])
            if isinstance(value, dict) and clean_text(value.get("name"))
        ]
        model_match = re.search(
            r"\bmodel\s*[:#-]\s*([\w./-]+)", description or "", re.I
        )
        size_match = re.search(
            r"\b(?:screen\s+)?size\s*[:#-]\s*(\d+(?:\.\d+)?)\s*(?:inches|inch|\")",
            description or "",
            re.I,
        )
        arabic_image_name = next(
            (
                clean_text(image.get("name"))
                for image in payload.get("images", [])
                if isinstance(image, dict)
                and clean_text(image.get("name"))
                and re.search(r"[\u0600-\u06ff]", str(image.get("name")))
            ),
            None,
        )
        return NormalizedProduct(
            adapter_id=self.adapter_id,
            source_store=self.source_store,
            source_store_slug=self.source_store_slug,
            source_product_id=source_id,
            canonical_url=canonicalize_url(
                str(payload.get("permalink") or reference.url)
            ),
            name=name,
            name_en=name,
            name_ar=arabic_image_name,
            category=category,
            subcategory=clean_text(
                (payload.get("categories") or [{}])[-1].get("name")
                if isinstance((payload.get("categories") or [{}])[-1], dict)
                else reference.source_category
            ),
            current_price=current_price,
            original_price=original_price,
            currency="JOD",
            availability=availability,
            image_urls=list(dict.fromkeys(images)),
            width_cm=dimensions["width_cm"],
            depth_cm=dimensions["depth_cm"],
            height_cm=dimensions["height_cm"],
            diameter_cm=dimensions["diameter_cm"],
            room_types=default_room_types(category),
            description=description,
            brand=brands[0] if brands else None,
            model_number=model_match.group(1) if model_match else None,
            tags=[
                value
                for value in (
                    f"screen_size_inches:{size_match.group(1)}" if size_match else None,
                    f"wordpress_id:{post_id}",
                )
                if value
            ],
            source_metadata={
                "wordpress_product_id": post_id,
                "screen_size_inches": size_match.group(1) if size_match else None,
                "category_slugs": [
                    category_value.get("slug")
                    for category_value in payload.get("categories", [])
                    if isinstance(category_value, dict)
                ],
                "purchasable": purchasable,
                "in_stock": in_stock,
            },
        )


class _LeadersCategoryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.products: dict[str, str] = {}
        self.has_next = False
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value for key, value in attrs}
        href = values.get("href")
        classes = set(str(values.get("class") or "").split())
        rel = set(str(values.get("rel") or "").casefold().split())
        if href and "/en/product/" in href and "/product-category/" not in href:
            self._href = href
            self._parts = []
            self.products.setdefault(href, "")
        if href and ("next" in rel or "next" in classes):
            self.has_next = True

    def handle_data(self, data: str) -> None:
        if self._href:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._href:
            text = clean_text(" ".join(self._parts))
            if text and len(text) > len(self.products[self._href]):
                self.products[self._href] = text
            self._href = None
            self._parts = []


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = clean_text(data)
        if value:
            self.parts.append(value)
