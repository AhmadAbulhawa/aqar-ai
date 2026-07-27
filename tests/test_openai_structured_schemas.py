import unittest
from pathlib import Path
from unittest.mock import patch

from openai.lib._parsing._responses import type_to_text_format_param

from app.services.placement_planner_service import (
    PlacementPlan,
    PlacementPlanResponse,
    ProductPlacement,
)
from app.models import RoomDimensions
from app.services.architectural_constraint_service import (
    GeometryConfidence,
    SpatialRect,
)
from app.services.composition_fit_service import validate_composition_fit
from app.services.layout_quality_service import (
    LayoutQualityStatus,
    layout_requires_replan,
)
from app.services.room_analysis_service import RoomAnalysis
import app.services.placement_planner_service as planner_service


class PlacementStructuredOutputTests(unittest.TestCase):
    def test_ai_schema_contains_only_model_generated_fields(self):
        properties = PlacementPlanResponse.model_json_schema()["properties"]

        self.assertNotIn("architectural_constraint_map", properties)
        self.assertNotIn("layout_quality", properties)
        self.assertIn(
            "architectural_constraint_map",
            PlacementPlan.model_json_schema()["properties"],
        )

    def test_every_ai_schema_object_is_closed(self):
        response_format = type_to_text_format_param(PlacementPlanResponse)
        schema = response_format["schema"]
        open_objects: list[str] = []

        self.assertTrue(response_format["strict"])
        self.assertEqual(response_format["name"], "PlacementPlanResponse")

        def visit(value, path: str = "$") -> None:
            if isinstance(value, dict):
                if (
                    value.get("type") == "object"
                    and value.get("additionalProperties") is not False
                ):
                    open_objects.append(path)
                for key, child in value.items():
                    visit(child, f"{path}.{key}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, f"{path}[{index}]")

        visit(schema)
        self.assertEqual(open_objects, [])

    def test_invalid_draft_is_replanned_and_only_valid_revision_is_cached(self):
        dimensions = RoomDimensions(length_cm=500, width_cm=350)
        analysis = RoomAnalysis(
            room_size="medium",
            layout_shape="rectangular",
            provided_dimensions_cm=dimensions,
        )
        item = {
            "id": 1,
            "name": "Test sofa",
            "category": "sofa",
            "style": "modern",
            "color": "beige",
            "material": "fabric",
            "width_cm": 100,
            "depth_cm": 50,
            "height_cm": 80,
            "quantity": 1,
            "selection_origin": "template",
            "selection_role": "template_selected",
            "requirement_importance": "essential",
            "slot_keys": [],
        }

        def placement(rect: SpatialRect) -> ProductPlacement:
            return ProductPlacement(
                product_id=1,
                unit_index=1,
                category="sofa",
                position="Test position",
                orientation="Facing inward",
                anchor="Test wall",
                geometry_confidence=GeometryConfidence.KNOWN,
                footprint=rect,
            )

        def plan(rect: SpatialRect) -> PlacementPlan:
            return PlacementPlan(
                layout_strategy="Test layout",
                focal_point="Conversation zone",
                circulation_strategy="Keep circulation clear",
                placements=[placement(rect)],
                design_rationale="Test plan",
            )

        invalid = plan(
            SpatialRect(x_cm=320, y_cm=100, width_cm=100, depth_cm=50)
        )
        valid = plan(
            SpatialRect(x_cm=100, y_cm=100, width_cm=100, depth_cm=50)
        )
        composition = validate_composition_fit([item], dimensions)

        with (
            patch.object(
                planner_service,
                "build_selection_signature",
                return_value="test-signature",
            ),
            patch.object(
                planner_service,
                "get_saved_placement_plan",
                return_value=None,
            ),
            patch.object(
                planner_service,
                "create_placement_plan",
                side_effect=[invalid, valid],
            ) as create_plan,
            patch.object(planner_service, "save_placement_plan") as save_plan,
        ):
            result, cached, signature = (
                planner_service.get_or_create_placement_plan(
                    room_request_id=7,
                    room_image_path=Path("unused.jpg"),
                    room_type="living_room",
                    style="modern",
                    room_analysis=analysis,
                    selected_items=[item],
                    composition_validation=composition.model_dump(mode="json"),
                )
            )

        self.assertFalse(cached)
        self.assertEqual(signature, "test-signature")
        self.assertEqual(create_plan.call_count, 2)
        revision_context = create_plan.call_args_list[1].kwargs[
            "revision_context"
        ]
        self.assertEqual(
            revision_context["layout_quality"]["status"],
            LayoutQualityStatus.INVALID.value,
        )
        self.assertEqual(
            result.layout_quality["status"],
            LayoutQualityStatus.VALID.value,
        )
        save_plan.assert_called_once()

    def test_invalid_revision_is_not_cached(self):
        dimensions = RoomDimensions(length_cm=500, width_cm=350)
        analysis = RoomAnalysis(
            room_size="medium",
            layout_shape="rectangular",
            provided_dimensions_cm=dimensions,
        )
        item = {
            "id": 1,
            "name": "Test sofa",
            "category": "sofa",
            "style": "modern",
            "color": "beige",
            "material": "fabric",
            "width_cm": 100,
            "depth_cm": 50,
            "height_cm": 80,
            "quantity": 1,
            "selection_origin": "template",
            "selection_role": "template_selected",
            "requirement_importance": "essential",
            "slot_keys": [],
        }
        invalid = PlacementPlan(
            layout_strategy="Invalid test layout",
            focal_point="Conversation zone",
            circulation_strategy="Keep circulation clear",
            placements=[
                ProductPlacement(
                    product_id=1,
                    unit_index=1,
                    category="sofa",
                    position="Outside room",
                    orientation="Facing inward",
                    anchor="Test wall",
                    geometry_confidence=GeometryConfidence.KNOWN,
                    footprint=SpatialRect(
                        x_cm=320,
                        y_cm=100,
                        width_cm=100,
                        depth_cm=50,
                    ),
                )
            ],
            design_rationale="Invalid test plan",
        )
        composition = validate_composition_fit([item], dimensions)

        with (
            patch.object(
                planner_service,
                "build_selection_signature",
                return_value="invalid-signature",
            ),
            patch.object(
                planner_service,
                "get_saved_placement_plan",
                return_value=None,
            ),
            patch.object(
                planner_service,
                "create_placement_plan",
                side_effect=[invalid, invalid],
            ),
            patch.object(planner_service, "save_placement_plan") as save_plan,
        ):
            result, _, _ = planner_service.get_or_create_placement_plan(
                room_request_id=7,
                room_image_path=Path("unused.jpg"),
                room_type="living_room",
                style="modern",
                room_analysis=analysis,
                selected_items=[item],
                composition_validation=composition.model_dump(mode="json"),
            )

        self.assertEqual(
            result.layout_quality["status"],
            LayoutQualityStatus.INVALID.value,
        )
        save_plan.assert_not_called()

    def test_major_alignment_warning_triggers_replan_before_cache(self):
        dimensions = RoomDimensions(length_cm=500, width_cm=600)
        analysis = RoomAnalysis(
            room_size="medium",
            layout_shape="rectangular",
            provided_dimensions_cm=dimensions,
        )

        def item(product_id: int, category: str, width: float, depth: float):
            return {
                "id": product_id,
                "name": f"Product {product_id}",
                "category": category,
                "style": "modern",
                "color": "beige",
                "material": "fabric",
                "width_cm": width,
                "depth_cm": depth,
                "height_cm": 80,
                "quantity": 1,
                "selection_origin": "template",
                "selection_role": "template_selected",
                "requirement_importance": "essential",
                "slot_keys": [],
            }

        left = item(10, "sofa", 200, 90)
        right = item(11, "sofa", 200, 90)
        table = item(12, "coffee_table", 100, 60)
        items = [left, right, table]

        def unit(product: dict, rect: SpatialRect) -> ProductPlacement:
            return ProductPlacement(
                product_id=product["id"],
                unit_index=1,
                category=product["category"],
                position="Test position",
                orientation="Facing inward",
                anchor="Conversation zone",
                geometry_confidence=GeometryConfidence.KNOWN,
                footprint=rect,
            )

        def draft(table_x: float) -> PlacementPlan:
            return PlacementPlan(
                layout_strategy="symmetric facing sofas",
                focal_point="conversation zone",
                circulation_strategy="Keep circulation clear",
                placements=[
                    unit(left, SpatialRect(x_cm=10, y_cm=100, width_cm=200, depth_cm=90)),
                    unit(right, SpatialRect(x_cm=390, y_cm=100, width_cm=200, depth_cm=90)),
                    unit(table, SpatialRect(x_cm=table_x, y_cm=230, width_cm=100, depth_cm=60)),
                ],
                design_rationale="Alignment retry test.",
            )

        drifted = draft(20)
        aligned = draft(250)
        composition = validate_composition_fit(items, dimensions)
        with (
            patch.object(
                planner_service,
                "build_selection_signature",
                return_value="alignment-signature",
            ),
            patch.object(
                planner_service,
                "get_saved_placement_plan",
                return_value=None,
            ),
            patch.object(
                planner_service,
                "create_placement_plan",
                side_effect=[drifted, aligned],
            ) as create_plan,
            patch.object(planner_service, "save_placement_plan") as save_plan,
        ):
            result, cached, _ = planner_service.get_or_create_placement_plan(
                room_request_id=7,
                room_image_path=Path("unused.jpg"),
                room_type="living_room",
                style="modern",
                room_analysis=analysis,
                selected_items=items,
                composition_validation=composition.model_dump(mode="json"),
            )

        self.assertFalse(cached)
        self.assertEqual(create_plan.call_count, 2)
        first_quality = create_plan.call_args_list[1].kwargs["revision_context"][
            "layout_quality"
        ]
        self.assertIn(
            "central_zone_alignment_drift",
            {warning["code"] for warning in first_quality["warnings"]},
        )
        self.assertFalse(layout_requires_replan(result.layout_quality))
        save_plan.assert_called_once()


if __name__ == "__main__":
    unittest.main()
