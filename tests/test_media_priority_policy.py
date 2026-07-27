import unittest

from app.models import RoomDimensions
from app.services.architectural_constraint_service import (
    ArchitecturalConstraintMap,
    ArchitecturalZone,
    GeometryConfidence,
    SpatialRect,
)
from app.services.room_analysis_service import RoomAnalysis
from app.services.room_completeness_service import (
    analyze_room_completeness,
    finalize_media_zone_assessment,
    reduce_composition_with_media_priority,
)


ROOM = RoomDimensions(length_cm=500, width_cm=600)
TIGHT_ROOM = RoomDimensions(length_cm=400, width_cm=300)


def product(
    product_id: int,
    category: str,
    *,
    width_cm: float = 100,
    depth_cm: float = 70,
    origin: str = "system",
    importance: str = "supporting",
    completeness_level: str | None = None,
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
        "height_cm": 70,
        "price": 100.0,
        "currency": "JOD",
        "image_filename": f"{product_id}.png",
        "product_url": f"https://example.com/{product_id}",
        "store_name": "Test Store",
        "room_types": ["living_room"],
        "tags": [],
        "availability": True,
        "catalog_status": "active",
        "quantity": 1,
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


def media_pair(*, stand_width_cm: float = 150, stand_depth_cm: float = 45):
    television = product(
        900,
        "television",
        width_cm=min(stand_width_cm * 1.1, 220),
        depth_cm=8,
    )
    stand = product(
        901,
        "tv_stand",
        width_cm=stand_width_cm,
        depth_cm=stand_depth_cm,
    )
    return television, stand


def media_wall(dimensions: RoomDimensions = ROOM) -> ArchitecturalConstraintMap:
    wall = ArchitecturalZone(
        zone_id="test_media_wall",
        zone_type="tv_wall",
        confidence=GeometryConfidence.KNOWN,
        description="Measured uninterrupted media wall.",
        bounds=SpatialRect(
            x_cm=dimensions.width_cm - 20,
            y_cm=40,
            width_cm=20,
            depth_cm=min(dimensions.length_cm - 80, 250),
        ),
    )
    return ArchitecturalConstraintMap(
        room_dimensions_cm=dimensions,
        tv_suitable_walls=[wall],
    )


def room_analysis(dimensions: RoomDimensions = ROOM) -> RoomAnalysis:
    return RoomAnalysis(
        room_size="medium",
        layout_shape="rectangular",
        provided_dimensions_cm=dimensions,
    )


def media_zone(result):
    return next(
        zone for zone in result.zones if zone.functional_zone == "media"
    )


def completeness_for(
    intent: str,
    *,
    selected_items: list[dict] | None = None,
    catalog_items: list[dict] | None = None,
    dimensions: RoomDimensions = ROOM,
    user_prompt: str | None = None,
):
    sofa = product(
        100,
        "sofa",
        width_cm=200,
        depth_cm=90,
        origin="template",
        importance="essential",
    )
    television, stand = media_pair()
    selected = selected_items or [sofa]
    catalog = catalog_items or [*selected, television, stand]
    return analyze_room_completeness(
        room_type="living_room",
        room_analysis=room_analysis(dimensions),
        current_selected_items=selected,
        architectural_constraints=media_wall(dimensions),
        catalog_items=catalog,
        room_dimensions=dimensions,
        explicit_intent=intent,
        user_prompt=user_prompt,
    )


def resolved_media_item(item: dict) -> dict:
    return {
        **item,
        "selection_origin": "system",
        "selection_role": "system_completion",
        "requirement_importance": "essential",
        "completeness_level": "required",
        "functional_zone": "media",
        "functional_zones": ["media"],
        "functional_group_ids": ["living_room_media_pair"],
    }


class MediaIntentPolicyTests(unittest.TestCase):
    def assert_required_pair(self, intent: str):
        result = completeness_for(intent)
        zone = media_zone(result)
        requirements = [
            slot
            for slot in result.additional_requirements
            if slot.functional_zone == "media"
        ]
        self.assertEqual(result.functional_intent, intent)
        self.assertEqual(zone.policy_level, "required")
        self.assertEqual(zone.status, "added")
        self.assertTrue(zone.addition_attempted)
        self.assertEqual(
            {slot.category for slot in requirements},
            {"television", "tv_stand"},
        )
        self.assertEqual(
            {slot.importance for slot in requirements},
            {"essential"},
        )
        self.assertEqual(
            {slot.functional_group_id for slot in requirements},
            {"living_room_media_pair"},
        )

    def test_01_general_living_adds_tv_and_stand_by_default(self):
        self.assert_required_pair("general_living")

    def test_02_family_living_adds_tv_and_stand_by_default(self):
        self.assert_required_pair("family_living")

    def test_03_media_room_requires_tv_and_stand(self):
        self.assert_required_pair("media_room")

    def test_04_conversation_room_may_omit_media(self):
        for intent in ("conversation_room", "formal_living"):
            with self.subTest(intent=intent):
                result = completeness_for(intent)
                zone = media_zone(result)
                self.assertEqual(zone.policy_level, "optional")
                self.assertEqual(zone.status, "skipped")
                self.assertFalse(zone.addition_attempted)
                self.assertFalse(
                    any(
                        slot.functional_zone == "media"
                        for slot in result.additional_requirements
                    )
                )

    def test_05_non_media_primary_focal_keeps_secondary_media(self):
        result = completeness_for("non_media_focal")
        zone = media_zone(result)
        self.assertEqual(zone.policy_level, "required")
        self.assertEqual(zone.status, "added")
        self.assertTrue(zone.addition_attempted)

        explicitly_excluded = completeness_for(
            "general_living",
            user_prompt="Create a living room without television.",
        )
        excluded_zone = media_zone(explicitly_excluded)
        self.assertEqual(excluded_zone.status, "skipped")
        self.assertFalse(excluded_zone.addition_attempted)


class MediaCapacityPolicyTests(unittest.TestCase):
    def setUp(self):
        self.user_sofa = product(
            200,
            "sofa",
            width_cm=200,
            depth_cm=90,
            origin="user",
            importance="essential",
        )
        self.template_chair = product(
            201,
            "armchair",
            width_cm=100,
            depth_cm=100,
            origin="template",
            importance="essential",
        )
        self.template_table = product(
            202,
            "coffee_table",
            width_cm=100,
            depth_cm=60,
            origin="template",
            importance="essential",
        )

    def _completeness_and_pair(
        self,
        stand_width_cm: float,
        stand_depth_cm: float,
        extra_items: list[dict] | None = None,
    ):
        television, stand = media_pair(
            stand_width_cm=stand_width_cm,
            stand_depth_cm=stand_depth_cm,
        )
        selected = [
            self.user_sofa,
            self.template_chair,
            self.template_table,
            *(extra_items or []),
        ]
        completeness = completeness_for(
            "general_living",
            selected_items=selected,
            catalog_items=[*selected, television, stand],
            dimensions=TIGHT_ROOM,
        )
        resolved = [
            *selected,
            resolved_media_item(television),
            resolved_media_item(stand),
        ]
        return completeness, resolved

    def test_06_required_media_preserves_the_exact_dense_inventory(self):
        optional = product(
            203,
            "side_table",
            width_cm=60,
            depth_cm=60,
            importance="optional",
            completeness_level="optional",
        )
        completeness, selected = self._completeness_and_pair(
            250,
            100,
            [optional],
        )
        reduction = reduce_composition_with_media_priority(
            selected_items=selected,
            completeness=completeness,
            room_dimensions=TIGHT_ROOM,
        )
        final = finalize_media_zone_assessment(
            completeness=completeness,
            selected_items=reduction.selected_items,
            reduction=reduction,
        )
        zone = media_zone(final)
        self.assertFalse(reduction.lower_priority_reduction_attempted)
        self.assertEqual(zone.status, "added")
        self.assertEqual(reduction.removed_media_items, [])
        self.assertEqual(reduction.removed_lower_priority_items, [])
        self.assertEqual(
            [(item["id"], item["quantity"]) for item in reduction.selected_items],
            [(item["id"], item["quantity"]) for item in selected],
        )

    def test_07_user_selected_products_are_never_removed(self):
        optional = product(
            204,
            "side_table",
            width_cm=100,
            depth_cm=100,
            importance="optional",
            completeness_level="optional",
        )
        completeness, selected = self._completeness_and_pair(
            200,
            80,
            [optional],
        )
        reduction = reduce_composition_with_media_priority(
            selected_items=selected,
            completeness=completeness,
            room_dimensions=TIGHT_ROOM,
        )
        self.assertIn(
            self.user_sofa["id"],
            {item["id"] for item in reduction.selected_items},
        )
        self.assertNotIn(
            self.user_sofa["id"],
            {item["id"] for item in reduction.removed_lower_priority_items},
        )

    def test_08_template_selected_essentials_are_never_removed(self):
        optional = product(
            205,
            "side_table",
            width_cm=100,
            depth_cm=100,
            importance="optional",
            completeness_level="optional",
        )
        completeness, selected = self._completeness_and_pair(
            200,
            80,
            [optional],
        )
        reduction = reduce_composition_with_media_priority(
            selected_items=selected,
            completeness=completeness,
            room_dimensions=TIGHT_ROOM,
        )
        remaining_ids = {item["id"] for item in reduction.selected_items}
        self.assertIn(self.template_chair["id"], remaining_ids)
        self.assertIn(self.template_table["id"], remaining_ids)

    def test_09_tv_and_stand_are_never_removed_automatically(self):
        completeness, selected = self._completeness_and_pair(250, 100)
        reduction = reduce_composition_with_media_priority(
            selected_items=selected,
            completeness=completeness,
            room_dimensions=TIGHT_ROOM,
        )
        final_categories = {
            item["category"]
            for item in reduction.selected_items
            if item["category"] in {"television", "tv_stand"}
        }
        self.assertEqual(final_categories, {"television", "tv_stand"})
        self.assertEqual(reduction.removed_media_items, [])

        singleton_reduction = reduce_composition_with_media_priority(
            selected_items=[
                *selected[:-2],
                selected[-2],
            ],
            completeness=completeness,
            room_dimensions=TIGHT_ROOM,
        )
        self.assertEqual(
            {
                item["category"]
                for item in singleton_reduction.selected_items
                if item["category"] in {"television", "tv_stand"}
            },
            {"television"},
        )
        self.assertEqual(singleton_reduction.removed_media_items, [])
        self.assertTrue(singleton_reduction.hard_conflict)
        self.assertIn("preserved unchanged", singleton_reduction.media_omission_reason)

    def test_10_room_7_like_dense_dry_run_still_attempts_media(self):
        dense_system_chairs = product(
            206,
            "armchair",
            width_cm=100,
            depth_cm=100,
            importance="recommended",
            completeness_level="recommended",
        )
        dense_system_chairs["quantity"] = 2
        television, stand = media_pair()
        selected = [
            product(
                207,
                "sofa",
                width_cm=220,
                depth_cm=100,
                origin="template",
                importance="essential",
            ),
            product(
                208,
                "coffee_table",
                width_cm=140,
                depth_cm=90,
                origin="template",
                importance="essential",
            ),
            dense_system_chairs,
        ]
        result = completeness_for(
            "general_living",
            selected_items=selected,
            catalog_items=[*selected, television, stand],
            dimensions=TIGHT_ROOM,
        )
        zone = media_zone(result)
        self.assertEqual(zone.status, "added")
        self.assertTrue(zone.addition_attempted)
        self.assertNotIn("density ceiling", zone.reason.casefold())


if __name__ == "__main__":
    unittest.main()
