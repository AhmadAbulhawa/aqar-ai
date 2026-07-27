from __future__ import annotations

import unittest
from unittest.mock import patch

from app.models import RoomDimensions
from app.services.composition_fit_service import (
    CompositionFitStatus,
    DensityClassification,
    reduce_overcrowded_composition,
    validate_composition_fit,
)


ROOM = RoomDimensions(length_cm=400, width_cm=400, height_cm=280)


def product(
    product_id: int,
    category: str,
    *,
    width_cm: float,
    depth_cm: float,
    quantity: int = 1,
    name: str | None = None,
) -> dict:
    return {
        "id": product_id,
        "name": name or f"Product {product_id}",
        "category": category,
        "width_cm": width_cm,
        "depth_cm": depth_cm,
        "quantity": quantity,
        "availability": True,
        "catalog_status": "active",
        "selection_origin": "user",
    }


class OverlapAwareCapacityTests(unittest.TestCase):
    def validate(self, items: list[dict], room: RoomDimensions = ROOM):
        return validate_composition_fit(
            selected_items=items,
            room_dimensions=room,
            room_type="living_room",
        )

    def test_rug_overlaps_the_seating_zone(self):
        sofa = product(1, "sofa", width_cm=220, depth_cm=100)
        rug = product(2, "rug", width_cm=300, depth_cm=200)

        result = self.validate([sofa, rug])

        self.assertEqual(result.raw_product_footprint_m2, 8.2)
        self.assertEqual(result.overlap_adjusted_effective_footprint_m2, 2.2)
        excluded = {
            item["product_id"]: item
            for item in result.excluded_from_independent_floor_area
        }
        self.assertEqual(excluded[2]["effective_footprint_m2"], 0.0)
        self.assertIn("overlaps", excluded[2]["reason"])

    def test_coffee_table_shares_the_rug_and_seating_composition(self):
        items = [
            product(1, "sofa", width_cm=220, depth_cm=100),
            product(2, "rug", width_cm=300, depth_cm=200),
            product(3, "coffee_table", width_cm=140, depth_cm=90),
        ]

        result = self.validate(items)
        adjustment = next(
            item for item in result.overlap_adjustments if item["product_id"] == 3
        )

        self.assertEqual(adjustment["raw_footprint_m2"], 1.26)
        self.assertEqual(adjustment["effective_footprint_m2"], 0.32)
        self.assertAlmostEqual(
            result.overlap_adjusted_effective_footprint_m2,
            2.52,
            places=2,
        )

    def test_wall_mounted_tv_is_not_double_counted_with_tv_stand(self):
        television = product(10, "television", width_cm=140, depth_cm=6)
        stand = product(11, "tv_stand", width_cm=180, depth_cm=40)

        result = self.validate([television, stand])
        tv_adjustment = next(
            item for item in result.overlap_adjustments if item["product_id"] == 10
        )

        self.assertEqual(result.raw_product_footprint_m2, 0.8)
        self.assertEqual(result.overlap_adjusted_effective_footprint_m2, 0.72)
        self.assertEqual(tv_adjustment["effective_footprint_m2"], 0.0)
        self.assertIn("above the TV stand", tv_adjustment["reason"])

    def test_side_tables_use_existing_seating_edge_gaps(self):
        sofa = product(20, "sofa", width_cm=220, depth_cm=100)
        side_tables = product(
            21,
            "side_table",
            width_cm=50,
            depth_cm=50,
            quantity=2,
        )

        result = self.validate([sofa, side_tables])
        side_adjustment = next(
            item for item in result.overlap_adjustments if item["product_id"] == 21
        )

        self.assertEqual(side_adjustment["raw_footprint_m2"], 0.5)
        self.assertEqual(side_adjustment["effective_footprint_m2"], 0.15)
        self.assertEqual(side_adjustment["quantity"], 2)

    def test_high_density_but_feasible_living_room_is_allowed(self):
        items = [
            product(30, "sofa", width_cm=220, depth_cm=100, quantity=3),
            product(31, "armchair", width_cm=100, depth_cm=100, quantity=3),
            product(32, "rug", width_cm=300, depth_cm=250),
            product(33, "coffee_table", width_cm=140, depth_cm=90),
            product(34, "television", width_cm=140, depth_cm=6),
            product(35, "tv_stand", width_cm=180, depth_cm=40),
        ]

        result = self.validate(items)

        self.assertEqual(result.status, CompositionFitStatus.VALID_WITH_WARNINGS)
        self.assertEqual(
            result.density_classification,
            DensityClassification.HIGH_DENSITY,
        )
        self.assertTrue(result.confirmation_allowed)
        self.assertTrue(any("Generate is allowed" in item for item in result.warnings))
        self.assertTrue(result.largest_space_consuming_products)

    def test_genuinely_impossible_major_inventory_is_blocked(self):
        room = RoomDimensions(length_cm=300, width_cm=300)
        sofas = product(
            40,
            "sofa",
            width_cm=250,
            depth_cm=200,
            quantity=2,
        )

        result = self.validate([sofas], room)

        self.assertEqual(result.status, CompositionFitStatus.INVALID)
        self.assertEqual(
            result.density_classification,
            DensityClassification.PHYSICALLY_IMPLAUSIBLE,
        )
        self.assertFalse(result.confirmation_allowed)
        self.assertIn(
            "major_seating_footprint_exceeds_room_area",
            {item["code"] for item in result.violations},
        )

    def test_exact_inventory_above_advisory_usable_area_is_allowed(self):
        items = [
            product(
                35,
                "sofa",
                name="HYLTARP corner sofa, 4-seat",
                width_cm=264,
                depth_cm=264,
                quantity=2,
            ),
            product(60, "armchair", width_cm=69, depth_cm=70),
            product(15, "tv_stand", width_cm=156, depth_cm=37.5),
            product(5, "coffee_table", width_cm=180, depth_cm=59),
        ]

        result = self.validate(items)

        self.assertEqual(result.status, CompositionFitStatus.VALID_WITH_WARNINGS)
        self.assertTrue(result.confirmation_allowed)
        self.assertFalse(result.violations)
        self.assertGreater(result.effective_occupancy_ratio or 0, 1)
        self.assertTrue(
            any("behind or around" in warning for warning in result.warnings)
        )

    def test_confirmed_doors_and_circulation_reduce_advisory_usable_area(self):
        sofa = product(45, "sofa", width_cm=220, depth_cm=100)
        unconfirmed = validate_composition_fit(
            [sofa],
            ROOM,
            obstacles=["The entrance is not visible."],
            circulation_paths=["An inferred route may exist near the camera."],
            room_type="living_room",
        )
        confirmed = validate_composition_fit(
            [sofa],
            ROOM,
            obstacles=["Visible door on the left wall."],
            circulation_paths=["Confirmed circulation path through the center."],
            room_type="living_room",
        )

        self.assertLess(
            confirmed.usable_floor_area_m2,
            unconfirmed.usable_floor_area_m2,
        )
        self.assertTrue(confirmed.confirmation_allowed)
        self.assertFalse(confirmed.violations)

    def test_capacity_validation_never_removes_products_or_changes_quantities(self):
        items = [
            product(50, "sofa", width_cm=250, depth_cm=200, quantity=2),
            product(51, "armchair", width_cm=100, depth_cm=100, quantity=3),
        ]
        expected = [(item["id"], item["quantity"]) for item in items]

        result = reduce_overcrowded_composition(
            selected_items=items,
            room_dimensions=RoomDimensions(length_cm=300, width_cm=300),
        )

        self.assertTrue(result.hard_conflict)
        self.assertEqual(result.removed_items, [])
        self.assertEqual(
            [(item["id"], item["quantity"]) for item in result.selected_items],
            expected,
        )

    @patch("app.services.openai_image_service.generate_furnished_room_image")
    def test_capacity_checks_do_not_call_render_or_paid_generation(self, generate):
        result = self.validate(
            [product(60, "sofa", width_cm=220, depth_cm=100)]
        )

        self.assertTrue(result.confirmation_allowed)
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
