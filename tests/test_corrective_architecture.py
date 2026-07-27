import tempfile
import unittest
import sqlite3
from pathlib import Path

from pydantic import ValidationError
from fastapi import HTTPException

from app.models import RoomDimensions, TemplateRenderRequest
from app.services.composition_fit_service import (
    CompositionFitStatus,
    validate_composition_fit,
)
from app.services.ikea_product_import_service import (
    IkeaProductMetadata,
    validate_metadata_for_insert,
)
from app.services.selection_persistence_service import (
    create_or_reuse_design_selection,
    ensure_design_selection_tables,
    load_design_selection,
    load_latest_template_design_selection,
)
from app.services.placement_planner_service import (
    PlacementPlan,
    ProductPlacement,
    build_selection_signature,
    validate_placement_plan,
)
from app.services.render_manifest_service import build_render_manifest
from app.services.room_analysis_service import (
    RoomAnalysis,
    build_room_analysis_signature,
)
from app.services.design_director_service import build_design_strategy_signature
from app.services.layout_quality_service import (
    LayoutQualityResult,
    LayoutQualityStatus,
)
import app.services.template_blueprint_service as blueprint_service
from app.services.template_blueprint_service import (
    TemplateBlueprint,
    normalize_template_blueprint,
)
from app.services.template_slot_resolver_service import resolve_template_slots


def product(
    product_id: int,
    category: str,
    *,
    name: str | None = None,
    width_cm: float = 100,
    depth_cm: float = 70,
    height_cm: float = 70,
    source: bool = True,
) -> dict:
    return {
        "id": product_id,
        "name": name or f"Product {product_id}",
        "category": category,
        "style": "modern",
        "color": "beige",
        "material": "wood",
        "width_cm": width_cm,
        "depth_cm": depth_cm,
        "height_cm": height_cm,
        "price": 100.0,
        "currency": "JOD",
        "image_filename": f"{product_id}.png",
        "product_url": f"https://example.com/{product_id}",
        "store_name": "Test",
        "room_types": ["living_room"],
        "tags": [],
        "availability": True,
        "catalog_status": "active",
        "is_template_source_candidate": source,
    }


def facing_room_blueprint() -> TemplateBlueprint:
    return TemplateBlueprint.model_validate(
        {
            "layout_type": "facing_sofas_conversation",
            "focal_point": "none",
            "density_target": "balanced",
            "composition_summary": "Two matching sofas face one another.",
            "visual_anchor_summary": "One coffee table and one rug anchor the room.",
            "product_slots": [
                {
                    "slot_key": "left_sofa",
                    "category": "sofa",
                    "quantity": 1,
                    "importance": "essential",
                    "visual_role": "left seating anchor",
                    "placement_zone": "left",
                },
                {
                    "slot_key": "right_sofa",
                    "category": "sofa",
                    "quantity": 1,
                    "importance": "essential",
                    "visual_role": "right seating anchor",
                    "placement_zone": "right",
                },
                {
                    "slot_key": "central_table",
                    "category": "coffee_table",
                    "quantity": 1,
                    "importance": "essential",
                    "visual_role": "central surface",
                    "placement_zone": "center",
                },
                {
                    "slot_key": "main_rug",
                    "category": "rug",
                    "quantity": 1,
                    "importance": "essential",
                    "visual_role": "zone anchor",
                    "placement_zone": "center",
                },
            ],
            "key_visual_rules": ["Keep the sofas facing each other."],
        }
    )


class TemplateResolutionTests(unittest.TestCase):
    def test_template_7_regression_uses_one_table_one_rug_and_two_of_sofa_82(self):
        blueprint = normalize_template_blueprint(facing_room_blueprint())
        sources = [
            product(80, "storage_cabinet"),
            product(81, "storage_cabinet"),
            product(65, "coffee_table"),
            product(83, "coffee_table"),
            product(82, "sofa", width_cm=178, depth_cm=99),
            product(84, "rug", width_cm=200, depth_cm=300, height_cm=1),
            product(85, "rug", width_cm=160, depth_cm=230, height_cm=1),
        ]

        result = resolve_template_slots(
            blueprint=blueprint,
            source_candidates=sources,
            database_candidates=[],
            room_dimensions=RoomDimensions(length_cm=600, width_cm=500),
            room_type="living_room",
            requested_style="modern",
            preferred_colors=["beige"],
        )

        by_category = {
            item["category"]: item for item in result.selected_items
        }
        self.assertEqual(by_category["sofa"]["id"], 82)
        self.assertEqual(by_category["sofa"]["quantity"], 2)
        self.assertEqual(by_category["coffee_table"]["quantity"], 1)
        self.assertEqual(by_category["rug"]["quantity"], 1)
        self.assertEqual(len(result.unresolved_slots), 0)
        self.assertTrue({80, 81} <= set(result.unassigned_source_candidate_ids))

    def test_repeat_group_requires_same_product(self):
        blueprint = normalize_template_blueprint(facing_room_blueprint())
        sofa_slots = [
            slot for slot in blueprint.product_slots if slot.category == "sofa"
        ]
        self.assertEqual(
            {slot.repeat_group_id for slot in sofa_slots},
            {"main_facing_sofas"},
        )
        self.assertTrue(all(slot.same_product_required for slot in sofa_slots))


class CompositionTests(unittest.TestCase):
    def test_combined_dense_footprint_warns_when_each_item_fits(self):
        items = [
            product(1, "sofa", width_cm=220, depth_cm=100),
            product(2, "sofa", width_cm=220, depth_cm=100),
            product(3, "armchair", width_cm=100, depth_cm=100),
            product(4, "armchair", width_cm=100, depth_cm=100),
            product(5, "coffee_table", width_cm=140, depth_cm=90),
        ]
        result = validate_composition_fit(
            selected_items=items,
            room_dimensions=RoomDimensions(length_cm=350, width_cm=300),
        )
        self.assertEqual(result.status, CompositionFitStatus.VALID_WITH_WARNINGS)
        self.assertTrue(result.confirmation_allowed)
        self.assertEqual(result.density_classification, "high_density")
        self.assertFalse(result.violations)


class CatalogValidationTests(unittest.TestCase):
    def metadata(self, **changes) -> IkeaProductMetadata:
        values = {
            "source_url": "https://example.com/item",
            "article_key": "item",
            "name": "Normal 3-seat sofa",
            "category": "sofa",
            "style": "modern",
            "color": "beige",
            "material": "fabric",
            "width_cm": 220,
            "depth_cm": 95,
            "height_cm": 80,
            "price": 500,
            "currency": "JOD",
            "image_url": None,
            "description": None,
            "room_types": ["living_room"],
            "tags": [],
        }
        values.update(changes)
        return IkeaProductMetadata(**values)

    def test_tea_towel_cannot_enter_rug_catalog(self):
        error = validate_metadata_for_insert(
            self.metadata(name="RINNIG tea towel", category="rug")
        )
        self.assertIn("semantic_category_mismatch", error or "")

    def test_nine_centimetre_sofa_is_quarantinable(self):
        error = validate_metadata_for_insert(self.metadata(width_cm=9))
        self.assertIn("implausible_dimensions", error or "")


class ImmutableSelectionTests(unittest.TestCase):
    def setUp(self):
        import app.database as database

        self._old_db_path = database.DB_PATH
        self._temporary_directory = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self._temporary_directory.name) / "test.db"
        connection = database.get_connection()
        connection.executescript(
            """
            CREATE TABLE room_requests (id INTEGER PRIMARY KEY);
            CREATE TABLE design_templates (id INTEGER PRIMARY KEY);
            CREATE TABLE furniture (id INTEGER PRIMARY KEY);
            INSERT INTO room_requests(id) VALUES (1);
            INSERT INTO design_templates(id) VALUES (7);
            INSERT INTO furniture(id) VALUES (82);
            """
        )
        connection.commit()
        connection.close()
        ensure_design_selection_tables()

    def tearDown(self):
        import app.database as database

        database.DB_PATH = self._old_db_path
        self._temporary_directory.cleanup()

    def test_apply_snapshot_is_the_exact_render_identity(self):
        selected = product(82, "sofa")
        selected["quantity"] = 2
        selected["slot_keys"] = ["left_sofa", "right_sofa"]
        selected["selection_role"] = "template_selected"
        selected["selection_origin"] = "template"
        selected["was_included"] = True
        selected["added_for_completion"] = False
        selected["included_quantity"] = 2
        selected["completion_quantity"] = 0
        selected["inclusion_reason"] = "Resolved from template."
        saved, reused = create_or_reuse_design_selection(
            room_request_id=1,
            template_id=7,
            mode="auto_template_blueprint",
            selection_signature="a" * 64,
            blueprint_signature="b" * 64,
            catalog_signature="c" * 64,
            room_analysis_signature="d" * 64,
            selected_items=[selected],
            blueprint={"layout_type": "facing_sofas_conversation"},
            composition_validation={"status": "valid"},
        )
        loaded = load_design_selection(saved.selection_id, room_request_id=1)
        loaded_by_template = load_latest_template_design_selection(
            room_request_id=1,
            template_id=7,
        )

        self.assertFalse(reused)
        self.assertEqual(loaded.selection_id, saved.selection_id)
        self.assertEqual(loaded_by_template.selection_id, saved.selection_id)
        self.assertEqual(loaded.selected_items[0]["id"], 82)
        self.assertEqual(loaded.selected_items[0]["quantity"], 2)
        self.assertEqual(
            loaded.selected_items[0]["slot_keys"],
            ["left_sofa", "right_sofa"],
        )
        self.assertEqual(loaded.selected_items[0]["selection_origin"], "template")
        self.assertTrue(loaded.selected_items[0]["was_included"])
        import app.database as database

        connection = database.get_connection()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE design_selection_items SET quantity = 1 WHERE selection_id = ?",
                    (saved.selection_id,),
                )
        finally:
            connection.rollback()
            connection.close()

    def test_legacy_invalid_coordinate_layout_does_not_block_new_lock(self):
        selected = product(82, "sofa")
        selected["selection_role"] = "template_selected"
        selected["selection_origin"] = "template"
        saved, _ = create_or_reuse_design_selection(
            room_request_id=1,
            template_id=7,
            mode="auto_template_blueprint",
            selection_signature="e" * 64,
            blueprint_signature="f" * 64,
            catalog_signature="a" * 64,
            room_analysis_signature="b" * 64,
            selected_items=[selected],
            blueprint={"layout_type": "facing_sofas_conversation"},
            composition_validation={
                "status": "valid",
                "layout_quality": {"status": "invalid"},
            },
        )
        self.assertEqual(saved.status, "locked")

    def test_major_alignment_drift_is_legacy_non_authoritative_metadata(self):
        selected = product(82, "sofa")
        selected["selection_role"] = "template_selected"
        selected["selection_origin"] = "template"
        quality = LayoutQualityResult(
            status=LayoutQualityStatus.VALID_WITH_WARNINGS,
            functional_score=90,
            circulation_score=100,
            visual_balance_score=82,
            comfort_score=100,
            density_score=100,
            violations=[],
            warnings=[
                {
                    "code": "central_zone_alignment_drift",
                    "severity": "serious",
                    "message": "Central layer is materially misaligned.",
                    "product_units": [],
                    "product_ids": [],
                }
            ],
            recommended_changes=[],
            metrics={},
            assumptions=[],
        )
        saved, _ = create_or_reuse_design_selection(
            room_request_id=1,
            template_id=7,
            mode="auto_template_blueprint",
            selection_signature="1" * 64,
            blueprint_signature="2" * 64,
            catalog_signature="3" * 64,
            room_analysis_signature="4" * 64,
            selected_items=[selected],
            blueprint={"layout_type": "facing_sofas_conversation"},
            composition_validation={
                "status": "valid",
                "layout_quality": quality.model_dump(mode="json"),
            },
        )
        self.assertEqual(saved.status, "locked")

    def test_template_render_contract_requires_only_template_id(self):
        request = TemplateRenderRequest.model_validate(
            {"template_id": 7}
        )
        self.assertEqual(request.template_id, 7)

        with self.assertRaises(ValidationError):
            TemplateRenderRequest.model_validate(
                {
                    "template_id": 7,
                    "template_name": "Living room",
                }
            )

        with self.assertRaises(ValidationError):
            TemplateRenderRequest.model_validate(
                {
                    "template_id": 7,
                    "selection_id": "00000000-0000-4000-8000-000000000000",
                }
            )

    def test_general_render_contract_accepts_immutable_selection_id(self):
        from app.routes.design_routes import RenderDesignRequest

        request = RenderDesignRequest.model_validate(
            {"selection_id": "00000000-0000-4000-8000-000000000000"}
        )
        self.assertEqual(
            request.selection_id,
            "00000000-0000-4000-8000-000000000000",
        )
        with self.assertRaises(ValidationError):
            RenderDesignRequest.model_validate(
                {
                    "selection_id": "00000000-0000-4000-8000-000000000000",
                    "selected_products": [{"id": 82, "quantity": 2}],
                }
            )
        with self.assertRaises(ValidationError):
            RenderDesignRequest.model_validate(
                {"selected_products": [{"id": 82, "quantity": 2}]}
            )


class CacheContractTests(unittest.TestCase):
    def test_placement_signature_changes_with_dimensions_and_room_image(self):
        analysis = RoomAnalysis.model_validate(
            {"room_size": "small", "layout_shape": "square"}
        )
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "room.png"
            image_path.write_bytes(b"first-room-image")
            item = product(1, "sofa", width_cm=200)
            first = build_selection_signature(
                [item], analysis, "living_room", "modern", room_image_path=image_path
            )
            changed_item = {**item, "width_cm": 201}
            changed_dimensions = build_selection_signature(
                [changed_item],
                analysis,
                "living_room",
                "modern",
                room_image_path=image_path,
            )
            image_path.write_bytes(b"different-room-image")
            changed_image = build_selection_signature(
                [item], analysis, "living_room", "modern", room_image_path=image_path
            )
        self.assertNotEqual(first, changed_dimensions)
        self.assertNotEqual(first, changed_image)

    def test_room_analysis_signature_includes_measured_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "room.png"
            image_path.write_bytes(b"room-image")
            first = build_room_analysis_signature(
                image_path,
                "living_room",
                RoomDimensions(length_cm=400, width_cm=300),
            )
            second = build_room_analysis_signature(
                image_path,
                "living_room",
                RoomDimensions(length_cm=401, width_cm=300),
            )
        self.assertNotEqual(first, second)

    def test_blueprint_signature_includes_prompt_version(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "template.png"
            image_path.write_bytes(b"template-image")
            first = blueprint_service.build_template_blueprint_signature(image_path)
            old_version = blueprint_service.TEMPLATE_BLUEPRINT_PROMPT_VERSION
            try:
                blueprint_service.TEMPLATE_BLUEPRINT_PROMPT_VERSION = "changed-test-version"
                second = blueprint_service.build_template_blueprint_signature(image_path)
            finally:
                blueprint_service.TEMPLATE_BLUEPRINT_PROMPT_VERSION = old_version
        self.assertNotEqual(first, second)

    def test_design_strategy_signature_includes_catalog_capabilities(self):
        analysis = RoomAnalysis.model_validate(
            {"room_size": "small", "layout_shape": "square"}
        )
        common = {
            "room_analysis": analysis,
            "room_type": "living_room",
            "style": "modern",
            "preferred_colors": [],
            "user_prompt": None,
            "layout_preference": None,
            "constraints": {},
        }
        first = build_design_strategy_signature(
            **common,
            candidate_items=[product(1, "sofa", width_cm=200)],
        )
        second = build_design_strategy_signature(
            **common,
            candidate_items=[product(1, "sofa", width_cm=201)],
        )
        self.assertNotEqual(first, second)


class PlacementContractTests(unittest.TestCase):
    def test_template_unit_must_keep_its_slot_key(self):
        item = product(82, "sofa")
        item["slot_keys"] = ["left_sofa"]
        plan = PlacementPlan(
            layout_strategy="facing",
            focal_point="none",
            circulation_strategy="central path",
            placements=[
                ProductPlacement(
                    product_id=82,
                    unit_index=1,
                    slot_key="right_sofa",
                    category="sofa",
                    position="left wall",
                    orientation="inward",
                    anchor="left wall",
                )
            ],
            design_rationale="test",
        )
        with self.assertRaises(HTTPException):
            validate_placement_plan(plan, [item])

    def test_template_manifest_fails_before_silent_reference_drop(self):
        items = [product(index, "side_table") for index in range(1, 17)]
        references = [
            {"id": item["id"], "local_image_path": "missing.png"}
            for item in items
        ]
        with self.assertRaises(HTTPException) as raised:
            build_render_manifest(
                selection_id="00000000-0000-4000-8000-000000000000",
                selection_signature="a" * 64,
                room_request_id=1,
                template_id=7,
                room_image_path=Path("missing-room.png"),
                template_image_path=Path("missing-template.png"),
                selected_items=items,
                furniture_reference_images=references,
                placement_signature="b" * 64,
            )
        self.assertEqual(raised.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
