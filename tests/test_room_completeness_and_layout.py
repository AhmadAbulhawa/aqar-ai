import inspect
import unittest

import app.services.template_application_service as template_application_service
import app.services.unified_recommendation_service as unified_recommendation_service

from app.models import RoomDimensions
from app.room_templates import ROOM_TEMPLATES
from app.services.architectural_constraint_service import (
    ArchitecturalConstraintMap,
    ArchitecturalEvidence,
    ArchitecturalZone,
    GeometryConfidence,
    SpatialRect,
    build_architectural_constraint_map,
)
from app.services.composition_fit_service import (
    CompositionFitStatus,
    reduce_overcrowded_composition,
    validate_composition_fit,
)
from app.services.layout_quality_service import (
    LayoutQualityStatus,
    layout_requires_replan,
    reduce_selection_for_layout,
    validate_layout_quality,
)
from app.services.placement_planner_service import (
    PlacementPlan,
    ProductPlacement,
)
from app.services.room_analysis_service import RoomAnalysis
from app.services.room_completeness_service import analyze_room_completeness
from app.services.template_blueprint_service import TemplateBlueprint
from app.services.template_slot_resolver_service import resolve_template_slots
from app.services.unified_selection_service import (
    DesignRequirementSlot,
    resolve_product_requirements,
)


# Floor coordinates use x across width and y across length.
ROOM = RoomDimensions(length_cm=500, width_cm=600)


def product(
    product_id: int,
    category: str,
    *,
    width_cm: float = 100,
    depth_cm: float = 70,
    height_cm: float = 70,
    quantity: int = 1,
    origin: str = "system",
    importance: str = "supporting",
    completeness_level: str | None = None,
    store_name: str = "Catalog Store",
) -> dict:
    return {
        "id": product_id,
        "name": f"Product {product_id}",
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
        "store_name": store_name,
        "room_types": ["living_room"],
        "tags": [],
        "availability": True,
        "catalog_status": "active",
        "quantity": quantity,
        "selection_origin": origin,
        "selection_role": {
            "user": "user_selected",
            "template": "template_selected",
            "system": "system_completion",
        }[origin],
        "requirement_importance": importance,
        "completeness_level": completeness_level,
        "functional_group_ids": [],
        "slot_keys": [],
    }


def placement(
    item: dict,
    rect: SpatialRect | None,
    *,
    unit_index: int = 1,
    relationships: list[str] | None = None,
    related_product_ids: list[int] | None = None,
    faces_product_id: int | None = None,
    confidence: GeometryConfidence = GeometryConfidence.KNOWN,
    clearance_notes: list[str] | None = None,
) -> ProductPlacement:
    return ProductPlacement(
        product_id=int(item["id"]),
        unit_index=unit_index,
        category=str(item["category"]),
        position="Measured test position",
        orientation="Inward toward the functional zone",
        anchor="Test anchor",
        relationships=relationships or [],
        geometry_confidence=confidence,
        footprint=rect,
        related_product_ids=related_product_ids or [],
        faces_product_id=faces_product_id,
        clearance_notes=clearance_notes or [],
    )


def plan(placements: list[ProductPlacement], focal_point: str = "none") -> PlacementPlan:
    return PlacementPlan(
        layout_strategy="functional test layout",
        focal_point=focal_point,
        circulation_strategy="Preserve measured protected corridors",
        placements=placements,
        design_rationale="Deterministic regression test.",
    )


def constraints(*zones: ArchitecturalZone) -> ArchitecturalConstraintMap:
    result = ArchitecturalConstraintMap(room_dimensions_cm=ROOM)
    destinations = {
        "door": result.door_zones,
        "door_swing": result.door_swing_zones,
        "window": result.window_zones,
        "no_block": result.no_block_zones,
        "circulation": result.circulation_corridors,
        "usable_wall": result.usable_walls,
        "tv_wall": result.tv_suitable_walls,
        "storage_wall": result.storage_suitable_walls,
        "seating": result.seating_zones,
        "corner": result.corner_zones,
        "focal_wall": result.focal_wall_candidates,
    }
    for zone in zones:
        destinations[zone.zone_type].append(zone)
    return result


def known_zone(
    zone_id: str,
    zone_type: str,
    rect: SpatialRect,
    **updates,
) -> ArchitecturalZone:
    return ArchitecturalZone(
        zone_id=zone_id,
        zone_type=zone_type,
        confidence=GeometryConfidence.KNOWN,
        description=f"Known {zone_type} test zone",
        bounds=rect,
        **updates,
    )


def layout_quality(
    items: list[dict],
    placements: list[ProductPlacement],
    architecture: ArchitecturalConstraintMap | None = None,
):
    composition = validate_composition_fit(items, ROOM)
    return validate_layout_quality(
        plan=plan(placements),
        selected_items=items,
        architectural_constraints=architecture or constraints(),
        room_dimensions=ROOM,
        composition_validation=composition,
    )


class ArchitecturalSafetyTests(unittest.TestCase):
    def test_room_bounds_use_width_for_x_and_length_for_y(self):
        cabinet = product(99, "storage_cabinet", width_cm=100, depth_cm=40)
        dimensions = RoomDimensions(length_cm=500, width_cm=350)
        architecture = ArchitecturalConstraintMap(
            room_dimensions_cm=dimensions
        )
        composition = validate_composition_fit([cabinet], dimensions)
        fitting = validate_layout_quality(
            plan=plan([
                placement(
                    cabinet,
                    SpatialRect(
                        x_cm=225,
                        y_cm=460,
                        width_cm=100,
                        depth_cm=40,
                    ),
                )
            ]),
            selected_items=[cabinet],
            architectural_constraints=architecture,
            room_dimensions=dimensions,
            composition_validation=composition,
        )
        self.assertNotIn(
            "product_outside_room_bounds",
            {item["code"] for item in fitting.violations},
        )

        outside = validate_layout_quality(
            plan=plan(
                [
                    placement(
                        cabinet,
                        SpatialRect(
                            x_cm=320,
                            y_cm=100,
                            width_cm=40,
                            depth_cm=40,
                        ),
                    )
                ]
            ),
            selected_items=[cabinet],
            architectural_constraints=architecture,
            room_dimensions=dimensions,
            composition_validation=composition,
        )
        self.assertIn(
            "product_outside_room_bounds",
            {item["code"] for item in outside.violations},
        )

    def test_01_sofa_blocking_door_is_invalid(self):
        sofa = product(1, "sofa", width_cm=200, depth_cm=90, origin="user", importance="essential")
        door = known_zone(
            "left_entry",
            "door",
            SpatialRect(x_cm=0, y_cm=0, width_cm=110, depth_cm=120),
        )
        result = layout_quality(
            [sofa],
            [placement(sofa, SpatialRect(x_cm=20, y_cm=20, width_cm=200, depth_cm=90))],
            constraints(door),
        )
        self.assertEqual(result.status, LayoutQualityStatus.INVALID)
        self.assertIn("door_opening_blocked", {item["code"] for item in result.violations})

    def test_02_tall_cabinet_blocking_window_is_invalid(self):
        cabinet = product(2, "storage_cabinet", width_cm=120, depth_cm=45, height_cm=190)
        window = known_zone(
            "rear_window",
            "window",
            SpatialRect(x_cm=200, y_cm=0, width_cm=180, depth_cm=55),
        )
        result = layout_quality(
            [cabinet],
            [placement(cabinet, SpatialRect(x_cm=230, y_cm=10, width_cm=120, depth_cm=45))],
            constraints(window),
        )
        self.assertEqual(result.status, LayoutQualityStatus.INVALID)
        self.assertIn("window_blocked", {item["code"] for item in result.violations})

    def test_03_primary_circulation_below_minimum_is_invalid(self):
        cabinet = product(3, "storage_cabinet", width_cm=70, depth_cm=80)
        corridor = known_zone(
            "main_entry_route",
            "circulation",
            SpatialRect(x_cm=240, y_cm=0, width_cm=90, depth_cm=500),
            axis="vertical",
            is_primary=True,
            minimum_clearance_cm=60,
            target_clearance_cm=85,
        )
        result = layout_quality(
            [cabinet],
            [placement(cabinet, SpatialRect(x_cm=250, y_cm=180, width_cm=70, depth_cm=80))],
            constraints(corridor),
        )
        self.assertEqual(result.status, LayoutQualityStatus.INVALID)
        self.assertIn("main_circulation_blocked", {item["code"] for item in result.violations})

    def test_04_coffee_table_too_close_requires_revision(self):
        sofa = product(4, "sofa", width_cm=200, depth_cm=90, origin="user", importance="essential")
        table = product(5, "coffee_table", width_cm=100, depth_cm=60)
        result = layout_quality(
            [sofa, table],
            [
                placement(sofa, SpatialRect(x_cm=40, y_cm=150, width_cm=200, depth_cm=90)),
                placement(table, SpatialRect(x_cm=250, y_cm=150, width_cm=100, depth_cm=60)),
            ],
        )
        self.assertEqual(result.status, LayoutQualityStatus.INVALID)
        self.assertIn("coffee_table_too_close", {item["code"] for item in result.violations})

    def test_14_unknown_door_swing_uses_uncertainty_clearance(self):
        analysis = RoomAnalysis(
            room_size="medium",
            layout_shape="rectangular",
            provided_dimensions_cm=ROOM,
            obstacles=["Door on left entrance; hinge direction not visible"],
        )
        architecture = build_architectural_constraint_map(
            room_analysis=analysis,
            room_dimensions=ROOM,
        )
        sofa = product(14, "sofa", width_cm=200, depth_cm=90)
        result = layout_quality(
            [sofa],
            [
                placement(
                    sofa,
                    SpatialRect(x_cm=250, y_cm=250, width_cm=200, depth_cm=90),
                    clearance_notes=[
                        "Preserve a conservative clear envelope around the uncertain entry door swing."
                    ],
                )
            ],
            architecture,
        )
        self.assertEqual(result.status, LayoutQualityStatus.UNCERTAIN_GEOMETRY)
        self.assertTrue(architecture.door_swing_zones)
        self.assertEqual(
            architecture.door_swing_zones[0].confidence,
            GeometryConfidence.UNCERTAIN,
        )
        self.assertIn("unmeasured_protected_zones", {item["code"] for item in result.warnings})

    def test_15_continuous_unobstructed_wall_is_tv_suitable(self):
        analysis = RoomAnalysis(
            room_size="medium",
            layout_shape="square",
            provided_dimensions_cm=ROOM,
            usable_walls=[
                "The large wall facing the camera is continuous and unobstructed."
            ],
        )
        architecture = build_architectural_constraint_map(
            room_analysis=analysis,
            room_dimensions=ROOM,
        )
        self.assertTrue(architecture.has_tv_suitable_wall)
        self.assertEqual(
            architecture.tv_suitable_walls[0].description,
            analysis.usable_walls[0],
        )


class CompletenessTests(unittest.TestCase):
    def setUp(self):
        self.analysis = RoomAnalysis(
            room_size="medium",
            layout_shape="rectangular",
            provided_dimensions_cm=ROOM,
        )
        self.sofa = product(20, "sofa", width_cm=190, depth_cm=90, origin="template", importance="essential")
        self.table = product(21, "coffee_table", width_cm=100, depth_cm=60, origin="template", importance="essential")
        self.rug = product(22, "rug", width_cm=200, depth_cm=280, height_cm=1, origin="template", importance="essential")
        self.tv = product(23, "television", width_cm=120, depth_cm=8, height_cm=75, store_name="TCL")
        self.stand = product(24, "tv_stand", width_cm=150, depth_cm=42, height_cm=55, store_name="Local Furniture")
        self.tv_wall = known_zone(
            "solid_right_media_wall",
            "tv_wall",
            SpatialRect(x_cm=560, y_cm=80, width_cm=40, depth_cm=250),
        )

    def test_all_non_living_room_policies_add_available_essential_functions(self):
        for room_type in (
            "bedroom",
            "office",
            "dining_room",
            "bathroom",
            "kitchen",
        ):
            policy = ROOM_TEMPLATES[room_type]
            expected_categories = [
                *policy["required"],
                *(group[0] for group in policy["one_of"]),
            ]
            catalog = [
                product(1000 + index, category)
                for index, category in enumerate(expected_categories)
            ]
            with self.subTest(room_type=room_type):
                result = analyze_room_completeness(
                    room_type=room_type,
                    room_analysis=self.analysis,
                    current_selected_items=[],
                    architectural_constraints=constraints(),
                    catalog_items=catalog,
                    room_dimensions=ROOM,
                )
                self.assertEqual(result.status, "complete")
                self.assertEqual(
                    {slot.category for slot in result.additional_requirements},
                    set(expected_categories),
                )

    def test_non_living_room_policy_reports_real_catalog_gaps(self):
        result = analyze_room_completeness(
            room_type="bedroom",
            room_analysis=self.analysis,
            current_selected_items=[],
            architectural_constraints=constraints(),
            catalog_items=[],
            room_dimensions=ROOM,
        )
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(
            set(result.missing_essential_zones),
            {"required_bed", "required_wardrobe"},
        )

    def test_05_family_living_room_adds_missing_media_pair(self):
        result = analyze_room_completeness(
            room_type="living_room",
            room_analysis=self.analysis,
            current_selected_items=[self.sofa, self.table, self.rug],
            architectural_constraints=constraints(self.tv_wall),
            catalog_items=[self.sofa, self.table, self.rug, self.tv, self.stand],
            room_dimensions=ROOM,
            explicit_intent="family_media",
        )
        media = next(zone for zone in result.zones if zone.functional_zone == "media")
        self.assertEqual(media.status, "added")
        self.assertEqual(
            {slot.category for slot in result.additional_requirements if slot.functional_zone == "media"},
            {"television", "tv_stand"},
        )

    def test_06_no_suitable_tv_wall_does_not_force_media(self):
        result = analyze_room_completeness(
            room_type="living_room",
            room_analysis=self.analysis,
            current_selected_items=[self.sofa, self.table, self.rug],
            architectural_constraints=constraints(),
            catalog_items=[self.sofa, self.table, self.rug, self.tv, self.stand],
            room_dimensions=ROOM,
            explicit_intent="family_media",
        )
        media = next(zone for zone in result.zones if zone.functional_zone == "media")
        self.assertEqual(media.status, "missing")
        self.assertEqual(result.status, "incomplete")
        self.assertFalse(
            any(slot.functional_zone == "media" for slot in result.additional_requirements)
        )

    def test_07_template_completion_can_mix_stores(self):
        blueprint = TemplateBlueprint.model_validate(
            {
                "layout_type": "facing_sofas_conversation",
                "focal_point": "none",
                "density_target": "balanced",
                "composition_summary": "Facing seating with a clear secondary wall.",
                "visual_anchor_summary": "Sofa, table, and rug anchor the zone.",
                "functional_additions_allowed": ["television", "tv_stand"],
                "product_slots": [
                    {
                        "slot_key": "main_sofa",
                        "category": "sofa",
                        "quantity": 1,
                        "importance": "essential",
                        "visual_role": "main seating",
                        "placement_zone": "left seating wall",
                    },
                    {
                        "slot_key": "main_table",
                        "category": "coffee_table",
                        "quantity": 1,
                        "importance": "essential",
                        "visual_role": "central table",
                        "placement_zone": "center",
                    },
                    {
                        "slot_key": "main_rug",
                        "category": "rug",
                        "quantity": 1,
                        "importance": "essential",
                        "visual_role": "central rug",
                        "placement_zone": "center",
                    },
                ],
            }
        )
        ikea_sources = [
            {**self.sofa, "store_name": "IKEA"},
            {**self.table, "store_name": "IKEA"},
            {**self.rug, "store_name": "IKEA"},
        ]
        initial = resolve_template_slots(
            blueprint=blueprint,
            source_candidates=ikea_sources,
            database_candidates=[self.tv, self.stand],
            room_dimensions=ROOM,
            room_type="living_room",
            requested_style="modern",
            preferred_colors=["beige"],
        )
        completeness = analyze_room_completeness(
            room_type="living_room",
            room_analysis=self.analysis,
            current_selected_items=initial.selected_items,
            architectural_constraints=constraints(self.tv_wall),
            catalog_items=[*ikea_sources, self.tv, self.stand],
            room_dimensions=ROOM,
            blueprint=blueprint,
        )
        completed = resolve_template_slots(
            blueprint=blueprint,
            source_candidates=ikea_sources,
            database_candidates=[self.tv, self.stand],
            room_dimensions=ROOM,
            room_type="living_room",
            requested_style="modern",
            preferred_colors=["beige"],
            additional_requirements=completeness.additional_requirements,
        )
        by_category = {item["category"]: item for item in completed.selected_items}
        self.assertEqual(by_category["sofa"]["store_name"], "IKEA")
        self.assertEqual(by_category["television"]["store_name"], "TCL")
        self.assertEqual(by_category["tv_stand"]["store_name"], "Local Furniture")
        self.assertEqual(by_category["television"]["selection_origin"], "system")
        self.assertEqual(by_category["television"]["functional_zone"], "media")

    def test_required_media_reserves_capacity_before_large_template_seating(self):
        dimensions = RoomDimensions(length_cm=400, width_cm=400)
        blueprint = TemplateBlueprint.model_validate(
            {
                "layout_type": "l_shape_conversation",
                "focal_point": "window_view",
                "density_target": "balanced",
                "composition_summary": "One primary sofa with a secondary media zone.",
                "visual_anchor_summary": "The sofa anchors the room.",
                "product_slots": [
                    {
                        "slot_key": "primary_sofa",
                        "category": "sofa",
                        "quantity": 1,
                        "importance": "essential",
                        "visual_role": "primary seating",
                        "placement_zone": "safe seating zone",
                    }
                ],
                "key_visual_rules": [],
                "functional_additions_allowed": ["television", "tv_stand"],
            }
        )
        oversized_sofa = product(
            101,
            "sofa",
            width_cm=243,
            depth_cm=243,
            origin="template",
            importance="essential",
        )
        fitting_sofa = product(
            102,
            "sofa",
            width_cm=180,
            depth_cm=90,
            origin="template",
            importance="essential",
        )
        television = product(
            103,
            "television",
            width_cm=123,
            depth_cm=8,
            origin="system",
            importance="essential",
        )
        stand = product(
            104,
            "tv_stand",
            width_cm=140,
            depth_cm=40,
            origin="system",
            importance="essential",
        )
        media_requirements = [
            DesignRequirementSlot(
                slot_key="completeness_media_television",
                category="television",
                required_quantity=1,
                importance="essential",
                functional_zone="media",
                functional_group_id="living_room_media_pair",
                completeness_level="required",
                allowed_product_ids=[103],
            ),
            DesignRequirementSlot(
                slot_key="completeness_media_console",
                category="tv_stand",
                required_quantity=1,
                importance="essential",
                functional_zone="media",
                functional_group_id="living_room_media_pair",
                completeness_level="required",
                allowed_product_ids=[104],
            ),
        ]
        resolved = resolve_template_slots(
            blueprint=blueprint,
            source_candidates=[oversized_sofa, fitting_sofa],
            database_candidates=[television, stand],
            room_dimensions=dimensions,
            room_type="living_room",
            requested_style="modern",
            preferred_colors=[],
            additional_requirements=media_requirements,
        )
        by_category = {item["category"]: item for item in resolved.selected_items}
        self.assertEqual(by_category["sofa"]["id"], 101)
        self.assertEqual(
            {"television", "tv_stand", "sofa"},
            set(by_category),
        )

    def test_08_final_completeness_uses_only_post_reduction_inventory(self):
        final_items = [
            product(82, "sofa", quantity=2, origin="template", importance="essential"),
            product(65, "coffee_table", origin="template", importance="essential"),
            product(84, "rug", origin="template", importance="essential"),
            product(31, "television"),
            product(13, "tv_stand"),
            product(81, "storage_cabinet", origin="template"),
        ]
        removed_catalog_items = [
            product(20, "armchair", quantity=2),
            product(23, "armchair"),
            product(27, "floor_lamp"),
            product(52, "side_table"),
        ]
        result = analyze_room_completeness(
            room_type="living_room",
            room_analysis=self.analysis,
            current_selected_items=final_items,
            architectural_constraints=constraints(self.tv_wall),
            catalog_items=[*final_items, *removed_catalog_items],
            room_dimensions=ROOM,
            explicit_intent="general_living",
            allow_additions=False,
        )
        zones = {zone.functional_zone: zone for zone in result.zones}
        self.assertEqual(zones["seating"].fulfilled_categories, ["sofa"])
        self.assertNotIn("armchair", zones["seating"].fulfilled_categories)
        self.assertNotIn("floor_lamp", zones["lighting"].fulfilled_categories)
        self.assertNotIn(
            "side_table", zones["supporting_surfaces"].fulfilled_categories
        )
        self.assertFalse(result.additional_requirements)
        self.assertEqual(
            [(item["id"], item["quantity"]) for item in final_items],
            [(82, 2), (65, 1), (84, 1), (31, 1), (13, 1), (81, 1)],
        )
        for service in (
            template_application_service,
            unified_recommendation_service,
        ):
            source = inspect.getsource(service)
            self.assertGreater(
                source.rindex("analyze_room_completeness("),
                source.index("reduce_composition_with_media_priority("),
            )

    def test_09_unseen_and_inferred_geometry_receive_nonobserved_evidence(self):
        analysis = RoomAnalysis(
            room_size="small",
            layout_shape="rectangular",
            provided_dimensions_cm=ROOM,
            obstacles=["The room entrance or doorway is not visible in this image."],
            circulation_paths=[
                "Movement from the unseen camera-side entry area.",
                "Maintain access for cleaning along the window wall.",
            ],
        )
        architecture = build_architectural_constraint_map(
            room_analysis=analysis,
            room_dimensions=ROOM,
        )
        self.assertEqual(
            architecture.door_zones[0].evidence,
            ArchitecturalEvidence.NOT_VISIBLE,
        )
        self.assertEqual(
            architecture.circulation_corridors[0].evidence,
            ArchitecturalEvidence.NOT_VISIBLE,
        )
        self.assertEqual(
            architecture.circulation_corridors[1].evidence,
            ArchitecturalEvidence.INFERRED,
        )
        self.assertFalse(any(zone.is_primary for zone in architecture.circulation_corridors))


class PreservationAndDensityTests(unittest.TestCase):
    def test_08_density_validation_preserves_optional_system_additions(self):
        committed = product(30, "sofa", width_cm=220, depth_cm=100, origin="user", importance="essential")
        optional = [
            product(31 + index, "armchair", width_cm=100, depth_cm=100, importance="optional", completeness_level="optional")
            for index in range(3)
        ]
        table = product(35, "coffee_table", width_cm=140, depth_cm=90, importance="supporting", completeness_level="recommended")
        result = reduce_overcrowded_composition(
            selected_items=[committed, *optional, table],
            room_dimensions=RoomDimensions(length_cm=350, width_cm=300),
        )
        self.assertFalse(result.hard_conflict)
        self.assertEqual(result.removed_items, [])
        self.assertEqual(
            {30, 31, 32, 33, 35},
            {int(item["id"]) for item in result.selected_items},
        )

    def test_09_user_selected_quantity_is_preserved(self):
        user_sofa = product(40, "sofa", width_cm=220, depth_cm=100, quantity=2, origin="user", importance="essential")
        armchair = product(41, "armchair", width_cm=100, depth_cm=100, importance="recommended", completeness_level="recommended")
        result = reduce_overcrowded_composition(
            selected_items=[user_sofa, armchair],
            room_dimensions=RoomDimensions(length_cm=400, width_cm=350),
        )
        preserved = next(item for item in result.selected_items if int(item["id"]) == 40)
        self.assertEqual(preserved["quantity"], 2)
        self.assertEqual(preserved["selection_origin"], "user")
        self.assertIn(41, {int(item["id"]) for item in result.selected_items})
        self.assertEqual(result.removed_items, [])

    def test_10_template_essential_products_are_preserved(self):
        sofa = product(50, "sofa", width_cm=220, depth_cm=100, origin="template", importance="essential")
        rug = product(51, "rug", width_cm=200, depth_cm=280, height_cm=1, origin="template", importance="essential")
        chair = product(52, "armchair", width_cm=120, depth_cm=110, importance="supporting", completeness_level="recommended")
        result = reduce_overcrowded_composition(
            selected_items=[sofa, rug, chair],
            room_dimensions=RoomDimensions(length_cm=300, width_cm=250),
        )
        retained_ids = {int(item["id"]) for item in result.selected_items}
        self.assertEqual({50, 51, 52}, retained_ids)
        self.assertEqual(result.removed_items, [])


class VisualComfortTests(unittest.TestCase):
    def test_16_major_alignment_drift_requires_replan_and_reduces_scores(self):
        left_sofa = product(100, "sofa", width_cm=200, depth_cm=90, origin="template", importance="essential")
        right_sofa = product(101, "sofa", width_cm=200, depth_cm=90, origin="template", importance="essential")
        table = product(102, "coffee_table", width_cm=100, depth_cm=60, origin="template", importance="essential")
        quality = validate_layout_quality(
            plan=PlacementPlan(
                layout_strategy="symmetric facing sofas",
                focal_point="conversation zone",
                circulation_strategy="Keep circulation clear",
                placements=[
                    placement(left_sofa, SpatialRect(x_cm=10, y_cm=100, width_cm=200, depth_cm=90)),
                    placement(right_sofa, SpatialRect(x_cm=390, y_cm=100, width_cm=200, depth_cm=90)),
                    placement(table, SpatialRect(x_cm=20, y_cm=230, width_cm=100, depth_cm=60)),
                ],
                design_rationale="Deliberately drifted central layer.",
            ),
            selected_items=[left_sofa, right_sofa, table],
            architectural_constraints=constraints(),
            room_dimensions=ROOM,
            composition_validation=validate_composition_fit(
                [left_sofa, right_sofa, table], ROOM
            ),
        )

        warning_by_code = {item["code"]: item for item in quality.warnings}
        self.assertIn("central_zone_alignment_drift", warning_by_code)
        self.assertEqual(
            warning_by_code["central_zone_alignment_drift"]["severity"],
            "serious",
        )
        self.assertTrue(layout_requires_replan(quality))
        self.assertLess(quality.functional_score, 100)
        self.assertLess(quality.visual_balance_score, 100)
        self.assertGreater(
            quality.metrics["serious_warning_score_penalties"]["visual"], 0
        )

    def test_17_rug_table_seating_relational_scale_and_anchoring(self):
        left_sofa = product(110, "sofa", width_cm=200, depth_cm=90, origin="template", importance="essential")
        right_sofa = product(111, "sofa", width_cm=200, depth_cm=90, origin="template", importance="essential")
        table = product(112, "coffee_table", width_cm=100, depth_cm=60, origin="template", importance="essential")
        rug = product(113, "rug", width_cm=240, depth_cm=110, height_cm=1, origin="template", importance="essential")
        items = [left_sofa, right_sofa, table, rug]
        quality = validate_layout_quality(
            plan=PlacementPlan(
                layout_strategy="balanced facing sofas",
                focal_point="centered conversation zone",
                circulation_strategy="Keep circulation clear",
                placements=[
                    placement(left_sofa, SpatialRect(x_cm=10, y_cm=100, width_cm=200, depth_cm=90)),
                    placement(right_sofa, SpatialRect(x_cm=390, y_cm=100, width_cm=200, depth_cm=90)),
                    placement(table, SpatialRect(x_cm=250, y_cm=100, width_cm=100, depth_cm=60)),
                    placement(rug, SpatialRect(x_cm=180, y_cm=75, width_cm=240, depth_cm=110)),
                ],
                design_rationale="Centered, correctly scaled relational layer.",
            ),
            selected_items=items,
            architectural_constraints=constraints(),
            room_dimensions=ROOM,
            composition_validation=validate_composition_fit(items, ROOM),
        )

        relational_codes = {
            item["code"] for item in [*quality.violations, *quality.warnings]
            if item["code"].startswith("rug_")
            or "seating_scale" in item["code"]
        }
        self.assertEqual(relational_codes, set())
        self.assertEqual(quality.metrics["rug_seating_intersection_count"], 2)
        self.assertAlmostEqual(
            quality.metrics["coffee_table_seating_long_span_ratio"], 0.5
        )

    def test_18_tiny_rug_is_rejected_for_table_and_seating(self):
        left_sofa = product(120, "sofa", width_cm=200, depth_cm=90, origin="template", importance="essential")
        right_sofa = product(121, "sofa", width_cm=200, depth_cm=90, origin="template", importance="essential")
        table = product(122, "coffee_table", width_cm=100, depth_cm=60, origin="system", importance="supporting")
        rug = product(123, "rug", width_cm=80, depth_cm=60, height_cm=1, origin="system", importance="supporting")
        items = [left_sofa, right_sofa, table, rug]
        quality = validate_layout_quality(
            plan=PlacementPlan(
                layout_strategy="balanced facing sofas",
                focal_point="conversation zone",
                circulation_strategy="Keep circulation clear",
                placements=[
                    placement(left_sofa, SpatialRect(x_cm=10, y_cm=100, width_cm=200, depth_cm=90)),
                    placement(right_sofa, SpatialRect(x_cm=390, y_cm=100, width_cm=200, depth_cm=90)),
                    placement(table, SpatialRect(x_cm=250, y_cm=100, width_cm=100, depth_cm=60)),
                    placement(rug, SpatialRect(x_cm=260, y_cm=100, width_cm=80, depth_cm=60)),
                ],
                design_rationale="Deliberately underscaled rug.",
            ),
            selected_items=items,
            architectural_constraints=constraints(),
            room_dimensions=ROOM,
            composition_validation=validate_composition_fit(items, ROOM),
        )

        violation_by_code = {item["code"]: item for item in quality.violations}
        self.assertIn("rug_too_small_for_seating", violation_by_code)
        self.assertIn("rug_too_small_for_table", violation_by_code)
        self.assertEqual(quality.status, LayoutQualityStatus.INVALID)
        change_by_code = {
            item["code"]: item for item in quality.recommended_changes
        }
        self.assertIn(
            123,
            change_by_code["rug_too_small_for_seating"][
                "removable_product_ids"
            ],
        )

    def test_11_all_heavy_furniture_on_one_side_is_rejected(self):
        first = product(60, "sofa", width_cm=180, depth_cm=80, origin="user", importance="essential")
        second = product(61, "storage_cabinet", width_cm=160, depth_cm=45, origin="template", importance="essential")
        result = layout_quality(
            [first, second],
            [
                placement(first, SpatialRect(x_cm=10, y_cm=40, width_cm=180, depth_cm=80)),
                placement(second, SpatialRect(x_cm=20, y_cm=200, width_cm=160, depth_cm=45)),
            ],
        )
        self.assertIn(result.status, {LayoutQualityStatus.INVALID, LayoutQualityStatus.VALID_WITH_WARNINGS})
        codes = {item["code"] for item in [*result.violations, *result.warnings]}
        self.assertTrue({"severe_visual_imbalance", "visual_weight_imbalance"} & codes)

    def test_12_random_system_side_table_is_removed_or_revised(self):
        sofa = product(70, "sofa", width_cm=190, depth_cm=90, origin="user", importance="essential")
        table = product(71, "side_table", width_cm=45, depth_cm=45, importance="optional", completeness_level="optional")
        quality = layout_quality(
            [sofa, table],
            [
                placement(sofa, SpatialRect(x_cm=30, y_cm=100, width_cm=190, depth_cm=90)),
                placement(table, SpatialRect(x_cm=520, y_cm=400, width_cm=45, depth_cm=45)),
            ],
        )
        self.assertIn("isolated_side_table", {item["code"] for item in quality.violations})
        reduction = reduce_selection_for_layout(
            selected_items=[sofa, table],
            layout_quality=quality,
        )
        self.assertEqual([int(item["id"]) for item in reduction.selected_items], [70])

    def test_13_tv_without_viewing_relationship_is_invalid(self):
        sofa = product(80, "sofa", width_cm=190, depth_cm=90, origin="user", importance="essential")
        television = product(81, "television", width_cm=120, depth_cm=8, height_cm=75, completeness_level="conditionally_required")
        stand = product(82, "tv_stand", width_cm=150, depth_cm=42, height_cm=55, completeness_level="conditionally_required")
        television["functional_group_ids"] = ["living_room_media_pair"]
        stand["functional_group_ids"] = ["living_room_media_pair"]
        quality = layout_quality(
            [sofa, television, stand],
            [
                placement(sofa, SpatialRect(x_cm=30, y_cm=250, width_cm=190, depth_cm=90)),
                placement(television, SpatialRect(x_cm=420, y_cm=40, width_cm=120, depth_cm=8)),
                placement(stand, SpatialRect(x_cm=405, y_cm=50, width_cm=150, depth_cm=42)),
            ],
            constraints(
                known_zone(
                    "media_wall",
                    "tv_wall",
                    SpatialRect(x_cm=400, y_cm=0, width_cm=180, depth_cm=60),
                )
            ),
        )
        self.assertEqual(quality.status, LayoutQualityStatus.INVALID)
        self.assertIn("no_tv_viewing_relationship", {item["code"] for item in quality.violations})

    def test_15_complete_room_with_balance_and_clearance_is_valid(self):
        left_sofa = product(90, "sofa", width_cm=200, depth_cm=90, origin="template", importance="essential")
        right_sofa = product(91, "sofa", width_cm=200, depth_cm=90, origin="template", importance="essential")
        table = product(92, "coffee_table", width_cm=100, depth_cm=60, origin="template", importance="essential")
        quality = layout_quality(
            [left_sofa, right_sofa, table],
            [
                placement(left_sofa, SpatialRect(x_cm=10, y_cm=100, width_cm=200, depth_cm=90)),
                placement(right_sofa, SpatialRect(x_cm=390, y_cm=100, width_cm=200, depth_cm=90)),
                placement(table, SpatialRect(x_cm=250, y_cm=100, width_cm=100, depth_cm=60)),
            ],
        )
        self.assertEqual(quality.status, LayoutQualityStatus.VALID)
        self.assertFalse(quality.violations)
        self.assertGreaterEqual(quality.circulation_score, 90)
        self.assertGreaterEqual(quality.visual_balance_score, 90)


if __name__ == "__main__":
    unittest.main()
