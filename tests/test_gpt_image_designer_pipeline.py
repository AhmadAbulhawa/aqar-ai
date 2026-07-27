import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import app.routes.design_routes as design_routes
from app.models import RoomDimensions
from app.services.architectural_constraint_service import (
    ArchitecturalConstraintMap,
    ArchitecturalZone,
    GeometryConfidence,
    build_architectural_constraint_map,
)
from app.services.composition_fit_service import (
    CompositionFitStatus,
    validate_composition_fit,
)
from app.services.openai_image_service import (
    MODEL_NAME,
    RENDER_PROMPT_VERSION,
    build_generation_prompt,
    generate_furnished_room_image,
)
from app.services.render_manifest_service import build_render_manifest
from app.services.room_analysis_service import RoomAnalysis
from app.services.room_completeness_service import analyze_room_completeness


def product(
    product_id: int,
    category: str,
    *,
    quantity: int = 1,
    origin: str = "system",
    role: str = "system_completion",
    width_cm: float = 100,
    depth_cm: float = 60,
    height_cm: float = 70,
) -> dict:
    return {
        "id": product_id,
        "name": f"Product {product_id}",
        "category": category,
        "quantity": quantity,
        "width_cm": width_cm,
        "depth_cm": depth_cm,
        "height_cm": height_cm,
        "selection_origin": origin,
        "selection_role": role,
        "functional_zone": "seating" if category in {"sofa", "armchair"} else category,
        "functional_zones": [],
        "slot_keys": [],
        "availability": True,
        "catalog_status": "active",
        "room_types": ["living_room"],
        "price": 100,
        "currency": "JOD",
        "line_total": 100 * quantity,
    }


class GPTImageDesignerPipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.room_path = self.root / "room.jpg"
        self.template_path = self.root / "template.jpg"
        self.room_path.write_bytes(b"room-image")
        self.template_path.write_bytes(b"template-image")
        self.dimensions = RoomDimensions(length_cm=500, width_cm=350, height_cm=280)
        self.analysis = RoomAnalysis(
            room_size="medium",
            layout_shape="rectangular",
            provided_dimensions_cm=self.dimensions,
            obstacles=[
                "left-side foreground door",
                "left-side narrow vertical window",
            ],
            circulation_paths=["preserve main left-side entry circulation"],
            usable_walls=[
                "uninterrupted far wall",
                "long unobstructed right wall",
            ],
        )
        self.architecture = build_architectural_constraint_map(
            room_analysis=self.analysis,
            room_dimensions=self.dimensions,
        )

    def tearDown(self):
        self.directory.cleanup()

    def reference(self, item: dict) -> dict:
        path = self.root / f"product-{item['id']}.jpg"
        path.write_bytes(f"product-image-{item['id']}".encode())
        return {"id": item["id"], "local_image_path": str(path)}

    def manifest(self, items: list[dict], *, template: bool = False, mode: str = "auto"):
        return build_render_manifest(
            selection_id="00000000-0000-4000-8000-000000000000",
            selection_signature="a" * 64,
            room_request_id=7,
            template_id=7 if template else None,
            design_mode=mode,
            room_image_path=self.room_path,
            template_image_path=self.template_path if template else None,
            selected_items=items,
            furniture_reference_images=[self.reference(item) for item in items],
            room_analysis=self.analysis,
            room_analysis_signature="b" * 64,
            architectural_constraints=self.architecture,
            template={"id": 7, "name": "Facing sofas"} if template else None,
            template_blueprint_signature="c" * 64 if template else None,
            render_prompt_version=RENDER_PROMPT_VERSION,
            renderer_model=MODEL_NAME,
        )

    def test_01_template_flow_has_no_active_placement_planner_call(self):
        source = inspect.getsource(design_routes.get_ready_template_application_context)
        self.assertNotIn("get_or_create_placement_plan", source)
        manifest = self.manifest([product(82, "sofa")], template=True, mode="template")
        self.assertFalse(manifest.placement_planner_called)

    def test_02_custom_flow_has_no_active_placement_planner_call(self):
        source = inspect.getsource(design_routes.get_room_recommendation)
        self.assertNotIn("get_or_create_placement_plan", source)
        manifest = self.manifest([product(82, "sofa", origin="user", role="user_selected")], mode="custom")
        self.assertFalse(manifest.pre_render_placement_required)

    def test_03_auto_flow_has_no_active_placement_planner_call(self):
        source = inspect.getsource(design_routes.render_design_for_room)
        self.assertNotIn("get_or_create_placement_plan", source)
        manifest = self.manifest([product(65, "coffee_table")], mode="auto")
        self.assertEqual(manifest.visual_design_authority, "gpt-image-2")

    def test_04_exact_selection_and_quantities_are_preserved(self):
        manifest = self.manifest(
            [product(82, "sofa", quantity=2), product(65, "coffee_table")]
        )
        self.assertEqual(
            [(item["id"], item["quantity"]) for item in manifest.selected_products],
            [(82, 2), (65, 1)],
        )

    def test_05_door_constraint_is_passed_to_renderer(self):
        manifest = self.manifest([product(82, "sofa")])
        prompt = build_generation_prompt(
            [product(82, "sofa")], "living_room", "modern", render_manifest=manifest
        )
        self.assertIn("Never block a visible door", prompt)
        self.assertIn("door-operating", prompt)

    def test_06_window_constraint_is_passed_to_renderer(self):
        manifest = self.manifest([product(82, "sofa")])
        prompt = build_generation_prompt(
            [product(82, "sofa")], "living_room", "modern", render_manifest=manifest
        )
        self.assertIn("Never block a visible window", prompt)
        self.assertIn("reasonable window access", prompt)

    def test_07_template_flow_input_order_is_room_then_products(self):
        manifest = self.manifest([product(82, "sofa"), product(65, "coffee_table")], template=True)
        self.assertEqual([ref.kind for ref in manifest.references], ["room", "product", "product"])
        self.assertEqual([ref.index for ref in manifest.references], [1, 2, 3])
        self.assertEqual(manifest.template_id, 7)
        self.assertEqual(manifest.render_reference_budget["template_images"], 0)
        source = inspect.getsource(design_routes.render_ready_design_template)
        self.assertNotIn("template_reference_image_path=", source)

    def test_08_normal_input_order_is_room_then_products(self):
        manifest = self.manifest([product(82, "sofa"), product(65, "coffee_table")])
        self.assertEqual([ref.kind for ref in manifest.references], ["room", "product", "product"])

    def test_09_reference_limits_fail_explicitly(self):
        normal_items = [product(index, "side_table") for index in range(1, 17)]
        with self.assertRaises(HTTPException) as normal_error:
            build_render_manifest(
                selection_id="00000000-0000-4000-8000-000000000000",
                selection_signature="a" * 64,
                room_request_id=7,
                room_image_path=self.room_path,
                selected_items=normal_items,
                furniture_reference_images=[{"id": item["id"], "local_image_path": "missing"} for item in normal_items],
            )
        self.assertEqual(normal_error.exception.detail["maximum_product_references"], 15)

        template_items = [product(index, "side_table") for index in range(1, 17)]
        with self.assertRaises(HTTPException) as template_error:
            build_render_manifest(
                selection_id="00000000-0000-4000-8000-000000000000",
                selection_signature="a" * 64,
                room_request_id=7,
                room_image_path=self.room_path,
                template_id=7,
                template_image_path=self.template_path,
                selected_items=template_items,
                furniture_reference_images=[{"id": item["id"], "local_image_path": "missing"} for item in template_items],
            )
        self.assertEqual(template_error.exception.detail["maximum_product_references"], 15)

    def test_10_repeated_product_quantity_is_explicit(self):
        sofa = product(82, "sofa", quantity=2)
        prompt = build_generation_prompt([sofa], "living_room", "modern")
        self.assertIn("quantity EXACTLY 2", prompt)
        self.assertIn("exactly TWO visible physical units of product 82", prompt)
        normalized_prompt = " ".join(prompt.split()).casefold()
        self.assertIn("two separate complete units", normalized_prompt)
        self.assertIn("Never merge repeated units", prompt)

    def test_10b_product_reference_is_an_immutable_construction_blueprint(self):
        prompt = build_generation_prompt(
            [product(82, "sofa"), product(65, "floor_lamp")],
            "living_room",
            "modern",
        )
        self.assertIn("immutable construction blueprint", prompt)
        self.assertIn("module/section count", prompt)
        self.assertIn("lamp stem and lamp head", prompt)
        self.assertIn("same-category lookalike is prohibited", prompt)

    def test_11_new_manifest_requires_no_synthetic_coordinates(self):
        manifest = self.manifest([product(82, "sofa")])
        self.assertIsNone(manifest.placement_plan)
        self.assertFalse(manifest.pre_render_placement_required)
        self.assertIsNone(manifest.placement_signature)

    def test_12_legacy_placement_data_is_ignored_by_prompt(self):
        prompt = build_generation_prompt(
            [product(82, "sofa")],
            "living_room",
            "modern",
            placement_plan={"placements": [{"position": "x=146,y=20"}]},
        )
        self.assertNotIn("x=146", prompt)
        self.assertNotIn("legacy placement", prompt.casefold())

    def test_13_room_completeness_can_add_policy_allowed_media(self):
        sofa = product(82, "sofa", origin="template", role="template_selected")
        table = product(65, "coffee_table", origin="template", role="template_selected")
        rug = product(84, "rug", origin="template", role="template_selected", width_cm=200, depth_cm=280, height_cm=1)
        tv = product(31, "television", width_cm=120, depth_cm=8, height_cm=75)
        stand = product(13, "tv_stand", width_cm=150, depth_cm=42, height_cm=55)
        constraints = ArchitecturalConstraintMap(
            room_dimensions_cm=self.dimensions,
            tv_suitable_walls=[
                ArchitecturalZone(
                    zone_id="right_media_wall",
                    zone_type="tv_wall",
                    confidence=GeometryConfidence.ESTIMATED,
                    description="long solid right wall",
                )
            ],
        )
        result = analyze_room_completeness(
            room_type="living_room",
            room_analysis=self.analysis,
            current_selected_items=[sofa, table, rug],
            architectural_constraints=constraints,
            catalog_items=[sofa, table, rug, tv, stand],
            room_dimensions=self.dimensions,
            explicit_intent="family_media",
        )
        self.assertEqual(
            {slot.category for slot in result.additional_requirements if slot.functional_zone == "media"},
            {"television", "tv_stand"},
        )

    def test_14_conversation_living_room_does_not_force_tv(self):
        sofa = product(82, "sofa")
        result = analyze_room_completeness(
            room_type="living_room",
            room_analysis=self.analysis,
            current_selected_items=[sofa],
            architectural_constraints=ArchitecturalConstraintMap(room_dimensions_cm=self.dimensions),
            catalog_items=[sofa, product(31, "television"), product(13, "tv_stand")],
            room_dimensions=self.dimensions,
            explicit_intent="conversation_only",
        )
        self.assertFalse(any(slot.functional_zone == "media" for slot in result.additional_requirements))

    def test_15_user_product_remains_authoritative(self):
        manifest = self.manifest([product(82, "sofa", quantity=2, origin="user", role="user_selected")], mode="custom")
        selected = manifest.selected_products[0]
        self.assertEqual((selected["id"], selected["quantity"], selected["selection_origin"]), (82, 2, "user"))

    def test_16_template_product_remains_authoritative(self):
        manifest = self.manifest([product(82, "sofa", quantity=2, origin="template", role="template_selected")], template=True)
        selected = manifest.selected_products[0]
        self.assertEqual((selected["id"], selected["quantity"], selected["selection_role"]), (82, 2, "template_selected"))

    def test_17_obviously_oversized_inventory_fails_broad_fit(self):
        result = validate_composition_fit(
            [product(82, "sofa", width_cm=600, depth_cm=400)],
            self.dimensions,
        )
        self.assertEqual(result.status, CompositionFitStatus.INVALID)
        self.assertIn("product_exceeds_room_in_every_orientation", {item["code"] for item in result.violations})

    def test_18_coordinate_alignment_warning_is_not_a_manifest_gate(self):
        manifest = self.manifest([product(82, "sofa")])
        self.assertNotIn("central_zone_alignment_drift", manifest.model_dump_json())
        self.assertEqual(manifest.placement_plan_status, "not_required_gpt_image_2_is_visual_designer")

    def test_19_prompt_grants_primary_designer_visual_freedom(self):
        prompt = build_generation_prompt([product(82, "sofa")], "living_room", "modern")
        self.assertIn("PRIMARY INTERIOR DESIGNER AND RENDERER", prompt)
        self.assertIn("Choose the complete furniture arrangement yourself", prompt)

    def test_20_architecture_and_safety_precede_visual_preferences(self):
        prompt = build_generation_prompt([product(82, "sofa")], "living_room", "modern")
        self.assertLess(
            prompt.index("HARD CONSTRAINTS"),
            prompt.index("COMPLETE VISUAL-DESIGN AUTHORITY"),
        )
        self.assertIn("Preserve the original room architecture", prompt)

    def test_render_signature_changes_with_architectural_constraints(self):
        item = product(82, "sofa")
        first = self.manifest([item])
        changed_architecture = self.architecture.model_copy(deep=True)
        changed_architecture.uncertainty_notes.append("new measured uncertainty")
        second = build_render_manifest(
            selection_id=first.selection_id,
            selection_signature=first.selection_signature,
            room_request_id=7,
            room_image_path=self.room_path,
            selected_items=[item],
            furniture_reference_images=[self.reference(item)],
            room_analysis=self.analysis,
            room_analysis_signature="b" * 64,
            architectural_constraints=changed_architecture,
            render_prompt_version=RENDER_PROMPT_VERSION,
            renderer_model=MODEL_NAME,
        )
        self.assertNotEqual(first.render_signature, second.render_signature)

    def test_final_renderer_rejects_template_image_before_client_call(self):
        with patch("app.services.openai_image_service.get_openai_client") as client:
            with self.assertRaises(HTTPException) as raised:
                generate_furnished_room_image(
                    room_request_id=7,
                    room_image_path=self.room_path,
                    furniture_reference_images=[self.reference(product(82, "sofa"))],
                    recommended_items=[product(82, "sofa")],
                    room_type="living_room",
                    style="modern",
                    template_reference_image_path=self.template_path,
                )
        self.assertEqual(raised.exception.status_code, 422)
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
