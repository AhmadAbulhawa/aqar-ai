import inspect
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import app.routes.design_routes as design_routes
from app.models import RoomDimensions
from app.services.architectural_constraint_service import (
    build_architectural_constraint_map,
)
from app.services.openai_image_service import build_generation_prompt
from app.services.render_compliance_service import (
    AUTOMATIC_PAID_RENDER_RETRY_ENABLED,
    _VisualComplianceAssessment,
    build_compliance_result,
    build_render_compliance_prompt,
    build_template_compliance_scope,
    validate_render_compliance,
)
from app.services.room_analysis_service import RoomAnalysis


def assessment(**updates) -> _VisualComplianceAssessment:
    payload = {
        "room_architecture_preserved": True,
        "room_finish_materials_preserved": True,
        "camera_view_preserved": True,
        "template_composition_preserved": True,
        "selected_products_present": True,
        "product_quantity_compliance": True,
        "unselected_major_products_absent": True,
        "product_identity_preserved": True,
        "realistic_scale": True,
        "door_accessible": True,
        "windows_unblocked": True,
        "circulation_usable": True,
        "door_evidence": "OBSERVED_CONFIRMED",
        "window_evidence": "OBSERVED_CONFIRMED",
        "circulation_evidence": "OBSERVED_CONFIRMED",
        "door_obstruction_visually_obvious": True,
        "window_obstruction_visually_obvious": True,
        "circulation_obstruction_visually_obvious": True,
        "no_major_furniture_overlap": True,
        "functionally_plausible": True,
        "violations": [],
        "warnings": [],
    }
    payload.update(updates)
    return _VisualComplianceAssessment.model_validate(payload)


def room_7_manifest(*, include_wall_art: bool = False) -> dict:
    selected_products = [
        {
            "id": 82,
            "name": "Selected sofa",
            "category": "sofa",
            "quantity": 2,
            "slot_keys": ["left_facing_sofa", "right_facing_sofa"],
        },
        {
            "id": 31,
            "name": "LG 55-inch NanoCell TV",
            "category": "television",
            "quantity": 1,
            "slot_keys": [],
        },
        {
            "id": 13,
            "name": "BOASTAD TV bench",
            "category": "tv_stand",
            "quantity": 1,
            "slot_keys": [],
        },
    ]
    if include_wall_art:
        selected_products.append(
            {
                "id": 91,
                "name": "Selected earth-toned artwork",
                "category": "wall_art",
                "quantity": 1,
                "slot_keys": ["oversized_artwork"],
            }
        )
    return {
        "selected_products": selected_products,
        "design_intent": {
            "locked_blueprint": {
                "layout_type": "facing_sofas_conversation",
                "product_slots": [
                    {
                        "slot_key": "left_facing_sofa",
                        "category": "sofa",
                        "quantity": 1,
                        "importance": "essential",
                    },
                    {
                        "slot_key": "right_facing_sofa",
                        "category": "sofa",
                        "quantity": 1,
                        "importance": "essential",
                    },
                    {
                        "slot_key": "oversized_artwork",
                        "category": "wall_art",
                        "quantity": 1,
                        "importance": "essential",
                        "fulfilled_quantity": 1 if include_wall_art else 0,
                        "missing_quantity": 0 if include_wall_art else 1,
                    },
                ],
            }
        },
    }


class RenderHardComplianceTests(unittest.TestCase):
    def test_01_door_blockage_is_hard_failure(self):
        result = build_compliance_result(
            assessment(
                door_accessible=False,
                violations=["Sofa overlaps the visible left-side door plane."],
            ),
            template_used=True,
        )
        self.assertEqual(result.status, "failed_compliance")
        self.assertTrue(result.hard_compliance_failure)
        self.assertIn("door_blocked", result.hard_failure_codes)
        self.assertIn(
            "Sofa overlaps the visible left-side door plane.",
            result.failure_reasons,
        )

    def test_02_window_blockage_is_hard_failure(self):
        result = build_compliance_result(
            assessment(
                windows_unblocked=False,
                violations=["Cabinet blocks the narrow left-side window."],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "failed_compliance")
        self.assertIn("window_blocked", result.hard_failure_codes)

    def test_03_primary_circulation_blockage_is_hard_failure(self):
        result = build_compliance_result(
            assessment(
                circulation_usable=False,
                violations=["Armchair blocks the immediate entrance route."],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "failed_compliance")
        self.assertIn(
            "primary_circulation_blocked",
            result.hard_failure_codes,
        )

    def test_04_any_failed_compliance_is_not_returned_as_success(self):
        compliance = build_compliance_result(
            assessment(
                selected_products_present=False,
                violations=["Selected television is absent."],
            ),
            template_used=False,
        )
        with self.assertRaises(HTTPException) as raised:
            design_routes._raise_if_render_compliance_failed(
                compliance=compliance,
                generated_design_id="generated-audit-id",
                generated_result={
                    "generated_image_filename": "failed.png",
                    "generated_image_path": "D:/real-estate-ai/uploads/generated_designs/failed.png",
                },
                generated_image_url="/uploads/generated_designs/failed.png",
            )
        self.assertEqual(raised.exception.status_code, 422)
        detail = raised.exception.detail
        self.assertEqual(detail["render_status"], "failed_compliance")
        self.assertEqual(detail["failure_reasons"], ["Selected television is absent."])
        self.assertTrue(detail["generated_image"]["preserved"])
        self.assertEqual(detail["generated_design_id"], "generated-audit-id")

    def test_05_failed_image_is_persisted_before_failure_response(self):
        for render_function in (
            design_routes.render_design_for_room,
            design_routes.render_ready_design_template,
        ):
            with self.subTest(render_function=render_function.__name__):
                source = inspect.getsource(render_function)
                self.assertLess(
                    source.index("save_generated_design("),
                    source.index("_raise_if_render_compliance_failed("),
                )

    @patch("app.routes.design_routes.load_latest_generated_design")
    def test_05b_failed_render_can_be_recovered_without_regeneration(self, load):
        load.return_value = {
            "id": "audit-32",
            "room_request_id": 32,
            "selection_id": "selection-32",
            "generated_image_filename": "design-32.png",
            "compliance_status": "failed_compliance",
            "compliance": {
                "status": "failed_compliance",
                "hard_compliance_failure": False,
                "hard_failure_codes": [],
                "failure_codes": ["product_identity_not_preserved"],
                "failure_reasons": ["The selected sofa identity changed."],
                "violations": ["The selected sofa identity changed."],
                "warnings": [],
                "automatic_paid_retry_enabled": False,
                "automatic_retry_attempted": False,
            },
            "manifest": {},
            "model_name": "gpt-image-2",
            "created_at": "2026-07-20 17:01:23",
        }

        recovered = design_routes.get_latest_generated_design_audit(
            32,
            "selection-32",
        )

        self.assertEqual(recovered["render_status"], "failed_compliance")
        self.assertEqual(recovered["generated_design_id"], "audit-32")
        self.assertTrue(recovered["generated_image"]["preserved"])
        self.assertTrue(recovered["recovered_from_audit"])
        load.assert_called_once_with(
            room_request_id=32,
            selection_id="selection-32",
        )

    def test_06_automatic_paid_retry_is_disabled(self):
        self.assertFalse(AUTOMATIC_PAID_RENDER_RETRY_ENABLED)
        for render_function in (
            design_routes.render_design_for_room,
            design_routes.render_ready_design_template,
        ):
            source = inspect.getsource(render_function)
            self.assertEqual(source.count("generate_furnished_room_image("), 1)
        result = build_compliance_result(assessment(), template_used=False)
        self.assertFalse(result.automatic_paid_retry_enabled)
        self.assertFalse(result.automatic_retry_attempted)

    def test_07_door_clearance_instruction_is_explicit(self):
        dimensions = RoomDimensions(length_cm=500, width_cm=350, height_cm=280)
        room_analysis = RoomAnalysis(
            room_size="medium",
            layout_shape="rectangular",
            provided_dimensions_cm=dimensions,
            obstacles=[
                "A full-height door is located on the left wall near the foreground."
            ],
            circulation_paths=["Keep the immediate entry path clear."],
        )
        architecture = build_architectural_constraint_map(
            room_analysis=room_analysis,
            room_dimensions=dimensions,
        )
        prompt = build_generation_prompt(
            [
                {
                    "id": 82,
                    "name": "Test sofa",
                    "category": "sofa",
                    "quantity": 1,
                    "width_cm": 178,
                    "depth_cm": 99,
                    "height_cm": 70,
                    "selection_role": "template_selected",
                }
            ],
            "living_room",
            "modern",
            room_analysis=room_analysis.model_dump(mode="json"),
            architectural_constraints=architecture.model_dump(mode="json"),
        )
        self.assertIn(
            "Never block a visible door",
            prompt,
        )
        self.assertIn(
            "immediate entry landing",
            prompt,
        )
        self.assertIn("full opening width, threshold", prompt)
        self.assertIn("immediate entry landing", prompt)
        self.assertIn("MANDATORY PRE-OUTPUT CLEARANCE SCAN", prompt)

    def test_08_verified_render_is_not_rejected(self):
        compliance = build_compliance_result(assessment(), template_used=True)
        self.assertEqual(compliance.status, "verified")
        self.assertIsNone(
            design_routes._raise_if_render_compliance_failed(
                compliance=compliance,
                generated_design_id="verified-id",
                generated_result={
                    "generated_image_filename": "verified.png",
                    "generated_image_path": "verified.png",
                },
                generated_image_url="/uploads/generated_designs/verified.png",
            )
        )

    def test_09_unresolved_essential_wall_art_absence_is_not_a_violation(self):
        manifest = room_7_manifest()
        scope = build_template_compliance_scope(manifest)
        result = build_compliance_result(
            assessment(
                template_composition_preserved=False,
                violations=[
                    "Template composition is not fully preserved: the required "
                    "oversized earth-toned leaning artwork on the rear wall is "
                    "absent and is replaced as the dominant focal point by the "
                    "television and TV bench."
                ],
            ),
            template_used=True,
            template_compliance_scope=scope,
            selected_products=manifest["selected_products"],
        )
        self.assertEqual(result.status, "verified")
        self.assertTrue(result.template_composition_preserved)
        self.assertEqual(result.violations, [])
        self.assertEqual(len(scope["unresolved_template_slots"]), 1)
        self.assertEqual(
            scope["unresolved_template_slots"][0]["slot_key"],
            "oversized_artwork",
        )
        self.assertEqual(len(result.ignored_unresolved_template_violations), 1)

    def test_10_resolved_selected_wall_art_missing_is_a_violation(self):
        manifest = room_7_manifest(include_wall_art=True)
        scope = build_template_compliance_scope(manifest)
        result = build_compliance_result(
            assessment(
                template_composition_preserved=False,
                selected_products_present=False,
                violations=["Selected earth-toned artwork is absent from the render."],
            ),
            template_used=True,
            template_compliance_scope=scope,
            selected_products=manifest["selected_products"],
        )
        self.assertEqual(result.status, "failed_compliance")
        self.assertIn(
            "Selected earth-toned artwork is absent from the render.",
            result.violations,
        )
        self.assertEqual(scope["unresolved_template_slots"], [])

    def test_11_selected_tv_missing_still_fails(self):
        manifest = room_7_manifest()
        result = build_compliance_result(
            assessment(
                selected_products_present=False,
                violations=["Selected television product 31 is absent."],
            ),
            template_used=True,
            template_compliance_scope=build_template_compliance_scope(manifest),
            selected_products=manifest["selected_products"],
        )
        self.assertEqual(result.status, "failed_compliance")
        self.assertIn("Selected television product 31 is absent.", result.violations)

    def test_12_safe_l_shape_does_not_fail_compliance_by_itself(self):
        manifest = room_7_manifest()
        result = build_compliance_result(
            assessment(
                template_composition_preserved=False,
                violations=[
                    "The facing-sofas relationship became L-shaped to keep the "
                    "left-side door and primary entrance path clear."
                ],
            ),
            template_used=True,
            template_compliance_scope=build_template_compliance_scope(manifest),
            selected_products=manifest["selected_products"],
        )
        self.assertEqual(result.status, "verified_with_warnings")
        self.assertFalse(result.template_composition_preserved)
        self.assertEqual(result.violations, [])
        self.assertEqual(len(result.accepted_template_adaptations), 1)

    def test_12a_facing_sofas_pass_when_safely_preserved(self):
        manifest = room_7_manifest()
        result = build_compliance_result(
            assessment(template_composition_preserved=True),
            template_used=True,
            template_compliance_scope=build_template_compliance_scope(manifest),
            selected_products=manifest["selected_products"],
        )
        self.assertEqual(result.status, "verified")
        self.assertTrue(result.template_composition_preserved)
        self.assertEqual(result.accepted_template_adaptations, [])

    def test_13_validator_tests_do_not_call_render_or_placement_planner(self):
        validator_source = inspect.getsource(build_compliance_result)
        self.assertNotIn("generate_furnished_room_image", validator_source)
        self.assertNotIn("plan_furniture_placement", validator_source)
        for render_function in (
            design_routes.render_design_for_room,
            design_routes.render_ready_design_template,
        ):
            source = inspect.getsource(render_function)
            self.assertNotIn("plan_furniture_placement(", source)

    def test_14_selected_quantity_mismatch_still_fails(self):
        manifest = room_7_manifest()
        result = build_compliance_result(
            assessment(
                product_quantity_compliance=False,
                violations=["Only one of the two selected sofas is visible."],
            ),
            template_used=True,
            template_compliance_scope=build_template_compliance_scope(manifest),
            selected_products=manifest["selected_products"],
        )
        self.assertEqual(result.status, "failed_compliance")
        self.assertFalse(result.product_quantity_compliance)

    def test_15_unselected_major_furniture_invention_still_fails(self):
        manifest = room_7_manifest()
        result = build_compliance_result(
            assessment(
                unselected_major_products_absent=False,
                violations=["An unselected storage cabinet was invented."],
            ),
            template_used=True,
            template_compliance_scope=build_template_compliance_scope(manifest),
            selected_products=manifest["selected_products"],
        )
        self.assertEqual(result.status, "failed_compliance")
        self.assertFalse(result.unselected_major_products_absent)

    def test_16_template_topology_change_alone_is_not_a_failure(self):
        manifest = room_7_manifest()
        result = build_compliance_result(
            assessment(
                template_composition_preserved=False,
                violations=[
                    "Template topology changed to a perpendicular sofa arrangement."
                ],
            ),
            template_used=True,
            template_compliance_scope=build_template_compliance_scope(manifest),
            selected_products=manifest["selected_products"],
        )
        self.assertEqual(result.status, "verified_with_warnings")
        self.assertEqual(result.violations, [])
        self.assertEqual(len(result.accepted_template_adaptations), 1)

    def test_17_severe_unrealistic_scale_still_fails(self):
        result = build_compliance_result(
            assessment(
                realistic_scale=False,
                violations=["The selected sofa is rendered at an impossible scale."],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "failed_compliance")
        self.assertFalse(result.realistic_scale)

    def test_18_compliance_does_not_send_template_image(self):
        source = inspect.getsource(validate_render_compliance)
        self.assertIn("del template_image_path", source)
        self.assertNotIn("Path(template_image_path)", source)

    def test_19_compliance_treats_hallway_narrowing_as_hard_failure(self):
        prompt = build_render_compliance_prompt({})
        self.assertIn("pinch point", prompt)
        self.assertIn("continuous body-width empty route", prompt)
        result = build_compliance_result(
            assessment(
                circulation_usable=False,
                violations=["A side table narrows the immediate entrance hallway."],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "failed_compliance")
        self.assertIn("primary_circulation_blocked", result.hard_failure_codes)

    def test_20_major_camera_rotation_fails_with_camera_code(self):
        result = build_compliance_result(
            assessment(
                camera_view_preserved=False,
                violations=[
                    "The generated camera rotates to a substantially different wall."
                ],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "failed_compliance")
        self.assertFalse(result.hard_compliance_failure)
        self.assertIn("camera_view_materially_changed", result.failure_codes)
        self.assertNotIn(
            "camera_view_materially_changed", result.hard_failure_codes
        )

    def test_21_reverse_angle_fails_compliance(self):
        result = build_compliance_result(
            assessment(
                camera_view_preserved=False,
                violations=[
                    "The result is a reverse angle from the opposite room corner."
                ],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "failed_compliance")
        self.assertIn("camera_view_materially_changed", result.failure_codes)
        with self.assertRaises(HTTPException) as raised:
            design_routes._raise_if_render_compliance_failed(
                compliance=result,
                generated_design_id="camera-audit-id",
                generated_result={
                    "generated_image_filename": "camera-failed.png",
                    "generated_image_path": "camera-failed.png",
                },
                generated_image_url="/uploads/generated_designs/camera-failed.png",
            )
        self.assertIn(
            "camera_view_materially_changed",
            raised.exception.detail["failure_codes"],
        )
        self.assertFalse(raised.exception.detail["camera_view_preserved"])

    def test_22_minor_framing_variation_does_not_fail(self):
        result = build_compliance_result(
            assessment(
                camera_view_preserved=True,
                warnings=["The framing differs by a few pixels."],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "verified_with_warnings")
        self.assertNotIn("camera_view_materially_changed", result.failure_codes)
        self.assertTrue(result.camera_view_preserved)
        prompt = build_render_compliance_prompt({})
        self.assertIn("minor crop", prompt)
        self.assertIn("lighting/lens-like visual", prompt)

    def test_23_low_sofa_beneath_window_is_not_automatic_hard_failure(self):
        result = build_compliance_result(
            assessment(
                windows_unblocked=False,
                window_evidence="OBSERVED_UNCERTAIN",
                window_obstruction_visually_obvious=False,
                violations=[
                    "A low sofa is beneath the window, but aperture obstruction is uncertain."
                ],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "verified_with_warnings")
        self.assertIsNone(result.windows_unblocked)
        self.assertNotIn("window_blocked", result.hard_failure_codes)
        self.assertTrue(
            any(
                "window_access_not_fully_verifiable" in warning
                for warning in result.warnings
            )
        )

    def test_24_tall_cabinet_covering_window_is_hard_failure(self):
        result = build_compliance_result(
            assessment(
                windows_unblocked=False,
                window_evidence="OBSERVED_CONFIRMED",
                window_obstruction_visually_obvious=True,
                violations=["A tall cabinet materially covers the window aperture."],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "failed_compliance")
        self.assertIn("window_blocked", result.hard_failure_codes)

    def test_25_blocked_operable_balcony_door_is_window_hard_failure(self):
        result = build_compliance_result(
            assessment(
                windows_unblocked=False,
                window_evidence="OBSERVED_CONFIRMED",
                window_obstruction_visually_obvious=True,
                violations=[
                    "The sofa blocks the visible operable walk-through balcony door."
                ],
            ),
            template_used=False,
        )
        self.assertIn("window_blocked", result.hard_failure_codes)

    def test_26_unseen_camera_side_entry_cannot_fail_circulation(self):
        result = build_compliance_result(
            assessment(
                circulation_usable=False,
                circulation_evidence="NOT_VISIBLE",
                circulation_obstruction_visually_obvious=False,
                violations=[
                    "Furniture may narrow a route from the unseen camera-side entry."
                ],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "verified_with_warnings")
        self.assertIsNone(result.circulation_usable)
        self.assertNotIn(
            "primary_circulation_blocked", result.hard_failure_codes
        )
        self.assertTrue(
            any(
                "circulation_not_fully_verifiable" in warning
                for warning in result.warnings
            )
        )

    def test_27_visible_confirmed_hallway_obstruction_remains_hard(self):
        result = build_compliance_result(
            assessment(
                circulation_usable=False,
                circulation_evidence="OBSERVED_CONFIRMED",
                circulation_obstruction_visually_obvious=True,
                violations=["A cabinet visibly blocks the confirmed main hallway."],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "failed_compliance")
        self.assertIn(
            "primary_circulation_blocked", result.hard_failure_codes
        )

    def test_28_concrete_floor_changed_to_wood_has_finish_code(self):
        result = build_compliance_result(
            assessment(
                room_finish_materials_preserved=False,
                violations=[
                    "The original smooth concrete floor changed to wood planks."
                ],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "failed_compliance")
        self.assertIn("room_finish_material_changed", result.failure_codes)
        self.assertFalse(result.hard_compliance_failure)
        self.assertEqual(result.hard_failure_codes, [])

    def test_29_material_wall_finish_change_fails_non_hard_compliance(self):
        result = build_compliance_result(
            assessment(
                room_finish_materials_preserved=False,
                violations=[
                    "The original plaster wall material changed to timber cladding."
                ],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "failed_compliance")
        self.assertIn("room_finish_material_changed", result.failure_codes)
        self.assertFalse(result.hard_compliance_failure)
        self.assertEqual(result.hard_failure_codes, [])

    def test_30_validator_prompt_is_evidence_based(self):
        prompt = build_render_compliance_prompt({})
        self.assertIn("OBSERVED_CONFIRMED", prompt)
        self.assertIn("INFERRED and NOT_VISIBLE", prompt)
        self.assertIn("low sofa merely", prompt)
        self.assertIn("cleaning convenience", prompt)
        self.assertIn("room_finish_materials_preserved", prompt)
        self.assertIn("Product construction is part of identity", prompt)
        self.assertIn(
            "repeated units were fused or merged",
            " ".join(prompt.split()),
        )

    def test_31_sofa_back_facing_camera_is_not_rejected(self):
        result = build_compliance_result(
            assessment(
                camera_view_preserved=False,
                violations=["The sofa back faces the camera."],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "verified_with_warnings")
        self.assertTrue(result.camera_view_preserved)
        self.assertEqual(result.violations, [])

    def test_32_sofa_side_facing_camera_is_not_rejected(self):
        result = build_compliance_result(
            assessment(
                camera_view_preserved=False,
                violations=["The sofa side faces the camera."],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "verified_with_warnings")
        self.assertTrue(result.camera_view_preserved)

    def test_33_natural_visual_overlap_is_not_physical_collision(self):
        result = build_compliance_result(
            assessment(
                no_major_furniture_overlap=False,
                violations=[
                    "Natural partial visual overlap occurs from the camera viewpoint."
                ],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "verified_with_warnings")
        self.assertTrue(result.no_major_furniture_overlap)
        self.assertEqual(result.violations, [])

    def test_34_impossible_physical_collision_still_fails(self):
        result = build_compliance_result(
            assessment(
                no_major_furniture_overlap=False,
                violations=[
                    "The sofa and cabinet have an impossible physical overlap."
                ],
            ),
            template_used=False,
        )
        self.assertEqual(result.status, "failed_compliance")
        self.assertFalse(result.no_major_furniture_overlap)

    def test_35_layout_differing_from_template_is_not_rejected(self):
        result = build_compliance_result(
            assessment(
                template_composition_preserved=False,
                violations=["The layout differs from the template image."],
            ),
            template_used=True,
        )
        self.assertEqual(result.status, "verified_with_warnings")
        self.assertEqual(result.violations, [])

    def test_36_compliance_prompt_builder_makes_no_paid_request(self):
        with patch("app.services.render_compliance_service.OpenAI") as client:
            prompt = build_render_compliance_prompt(room_7_manifest())
        client.assert_not_called()
        self.assertIn("Natural perspective overlap", prompt)
        self.assertIn(
            "directly evidenced material problems",
            " ".join(prompt.split()),
        )


if __name__ == "__main__":
    unittest.main()
