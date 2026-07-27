import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.models import RoomDimensions
from app.routes.design_routes import (
    InventoryRevisionLine,
    InventoryRevisionRequest,
    revise_design_selection,
)
from app.services.inventory_revision_service import build_inventory_revision
from app.services.selection_persistence_service import PersistedDesignSelection


def product(product_id: int, category: str = "armchair") -> dict:
    return {
        "id": product_id,
        "name": f"Product {product_id}",
        "category": category,
        "style": "modern",
        "color": "walnut",
        "material": "wood",
        "width_cm": 70,
        "depth_cm": 70,
        "height_cm": 80,
        "price": 100.0,
        "currency": "JOD",
        "image_filename": f"{product_id}.png",
        "product_url": f"https://example.com/{product_id}",
        "store_name": "IKEA Jordan",
        "room_types": ["living_room"],
        "tags": [],
        "availability": True,
        "catalog_status": "active",
    }


def sourced_product(
    product_id: int,
    origin: str,
    *,
    quantity: int = 1,
) -> dict:
    item = product(product_id)
    if origin == "from_template":
        item.update(
            selection_origin="template",
            selection_role="template_selected",
            product_origin=origin,
            template_id=7,
            was_included=True,
        )
    elif origin == "added_to_complete_room":
        item.update(
            selection_origin="system",
            selection_role="system_completion",
            product_origin=origin,
            added_for_completion=True,
        )
    else:
        item.update(
            selection_origin="user",
            selection_role="user_selected",
            product_origin="user_selected",
            was_included=True,
        )
    item.update(quantity=quantity, line_total=100.0 * quantity)
    return item


class InventoryRevisionServiceTests(unittest.TestCase):
    def setUp(self):
        self.base = [
            sourced_product(1, "from_template"),
            sourced_product(2, "added_to_complete_room"),
            sourced_product(3, "user_selected"),
        ]
        self.catalog = [product(index) for index in range(1, 6)]
        self.dimensions = RoomDimensions(length_cm=800, width_cm=700)

    def test_edit_remove_and_add_preserve_real_product_origins(self):
        revised, composition = build_inventory_revision(
            base_items=self.base,
            catalog_items=self.catalog,
            requested_lines=[
                {"product_id": 1, "quantity": 2},
                {"product_id": 3, "quantity": 1},
                {"product_id": 4, "quantity": 1},
            ],
            room_type="living_room",
            room_dimensions=self.dimensions,
        )

        by_id = {item["id"]: item for item in revised}
        self.assertEqual(set(by_id), {1, 3, 4})
        self.assertEqual(by_id[1]["quantity"], 2)
        self.assertEqual(by_id[1]["product_origin"], "from_template")
        self.assertEqual(by_id[1]["included_quantity"], 2)
        self.assertEqual(by_id[3]["product_origin"], "user_selected")
        self.assertEqual(by_id[4]["product_origin"], "user_selected")
        self.assertEqual(by_id[4]["selection_role"], "user_selected")
        self.assertEqual(by_id[4]["selection_origin"], "user")
        self.assertNotEqual(composition.status.value, "invalid")
        # The previously locked source object was never mutated.
        self.assertEqual(self.base[0]["quantity"], 1)
        self.assertIn(2, {item["id"] for item in self.base})

    def test_duplicate_ids_and_ineligible_catalog_products_are_rejected(self):
        with self.assertRaises(HTTPException) as duplicate_error:
            build_inventory_revision(
                base_items=self.base,
                catalog_items=self.catalog,
                requested_lines=[
                    {"product_id": 1, "quantity": 1},
                    {"product_id": 1, "quantity": 2},
                ],
                room_type="living_room",
                room_dimensions=self.dimensions,
            )
        self.assertEqual(duplicate_error.exception.status_code, 422)

        unavailable = product(9)
        unavailable["availability"] = False
        with self.assertRaises(HTTPException) as unavailable_error:
            build_inventory_revision(
                base_items=self.base,
                catalog_items=[*self.catalog, unavailable],
                requested_lines=[{"product_id": 9, "quantity": 1}],
                room_type="living_room",
                room_dimensions=self.dimensions,
            )
        self.assertEqual(unavailable_error.exception.status_code, 422)


class InventoryRevisionRouteTests(unittest.TestCase):
    def test_route_creates_a_new_lock_and_calls_no_ai_or_render_service(self):
        base = PersistedDesignSelection(
            selection_id="11111111-1111-4111-8111-111111111111",
            room_request_id=25,
            template_id=7,
            mode="template_product_source",
            status="locked",
            selection_signature="a" * 64,
            blueprint_signature="b" * 64,
            catalog_signature="c" * 64,
            room_analysis_signature="d" * 64,
            blueprint={"design_strategy_signature": "e" * 64},
            composition_validation={"status": "valid"},
            selected_items=[sourced_product(1, "from_template")],
        )
        revised_item = sourced_product(1, "from_template", quantity=2)
        revised = base.model_copy(
            update={
                "selection_id": "22222222-2222-4222-8222-222222222222",
                "selected_items": [revised_item],
            }
        )
        room = {
            "id": 25,
            "room_type": "living_room",
            "preferred_colors": None,
            "room_length_cm": 800,
            "room_width_cm": 700,
            "room_height_cm": 300,
            "budget": None,
            "style": None,
        }
        with (
            patch(
                "app.routes.design_routes.load_design_selection",
                return_value=base,
            ),
            patch(
                "app.routes.design_routes.load_room_and_furniture",
                return_value=(room, [product(1)]),
            ),
            patch(
                "app.routes.design_routes.create_or_reuse_design_selection",
                return_value=(revised, False),
            ) as create_lock,
        ):
            response = revise_design_selection(
                base.selection_id,
                InventoryRevisionRequest(
                    items=[InventoryRevisionLine(product_id=1, quantity=2)]
                ),
            )

        self.assertEqual(response["selection_id"], revised.selection_id)
        self.assertEqual(response["base_selection_id"], base.selection_id)
        self.assertEqual(response["selected_products"], [
            {
                "id": 1,
                "quantity": 2,
                "product_origin": "from_template",
                "selection_role": "template_selected",
            }
        ])
        self.assertFalse(response["ai_selector_called"])
        self.assertFalse(response["room_analysis_called"])
        self.assertFalse(response["image_generation_called"])
        self.assertTrue(
            create_lock.call_args.kwargs["blueprint"]["inventory_user_edited"]
        )
        self.assertEqual(base.selected_items[0]["quantity"], 1)


if __name__ == "__main__":
    unittest.main()
