import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.models import RoomDimensions
from app.routes.design_routes import (
    _build_renderer_contract,
    build_selected_furniture_response,
)
from app.services.design_requirement_service import functional_zone_for_category
from app.services.selection_persistence_service import PersistedDesignSelection
from app.services.template_slot_resolver_service import (
    resolve_template_product_inventory,
)
from app.services.template_application_service import load_template_application_data
from app.services.room_analysis_service import RoomAnalysis
from app.services.unified_selection_service import DesignRequirementSlot


def product(product_id: int, category: str, *, quantity: int = 1) -> dict:
    return {
        "id": product_id,
        "name": f"Product {product_id}",
        "category": category,
        "style": "modern",
        "color": "black" if category == "television" else "oak",
        "material": "mixed",
        "width_cm": 90,
        "depth_cm": 40,
        "height_cm": 55,
        "price": 100.0,
        "currency": "JOD",
        "image_filename": f"{product_id}.png",
        "product_url": f"https://example.com/{product_id}",
        "store_name": "Database Store",
        "room_types": ["living_room"],
        "tags": [],
        "availability": True,
        "catalog_status": "active",
        "quantity": quantity,
    }


def requirement(key: str, category: str) -> DesignRequirementSlot:
    return DesignRequirementSlot(
        slot_key=key,
        category=category,
        required_quantity=1,
        importance="essential",
        visual_role=f"required {category}",
        placement_zone="functional room composition",
        functional_zone=functional_zone_for_category(category),
        completeness_level="required",
    )


class TemplateProductSourceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.template_products = [
            product(82, "sofa", quantity=2),
            product(52, "coffee_table"),
        ]
        for item in self.template_products:
            item.update(
                selection_origin="template",
                selection_role="template_selected",
                template_id=7,
            )

    def test_template_inventory_is_preserved_and_completion_is_db_only(self):
        result = resolve_template_product_inventory(
            template_products=self.template_products,
            database_candidates=[
                *self.template_products,
                product(31, "television"),
                product(13, "tv_stand"),
            ],
            completion_requirements=[
                requirement("media_tv", "television"),
                requirement("media_console", "tv_stand"),
            ],
            room_dimensions=RoomDimensions(length_cm=700, width_cm=600),
            room_type="living_room",
            requested_style="modern",
            preferred_colors=[],
        )

        by_id = {int(item["id"]): item for item in result.selected_items}
        self.assertEqual(set(by_id), {82, 52, 31, 13})
        self.assertEqual(by_id[82]["quantity"], 2)
        self.assertEqual(by_id[52]["quantity"], 1)
        for product_id in (82, 52):
            self.assertEqual(by_id[product_id]["selection_origin"], "template")
            self.assertEqual(by_id[product_id]["selection_role"], "template_selected")
        for product_id in (31, 13):
            self.assertEqual(by_id[product_id]["selection_origin"], "system")
            self.assertEqual(by_id[product_id]["selection_role"], "system_completion")
            self.assertTrue(by_id[product_id]["added_for_completion"])

        public_items = build_selected_furniture_response(result.selected_items)
        public_by_id = {item["id"]: item for item in public_items}
        self.assertEqual(public_by_id[82]["product_origin"], "from_template")
        self.assertEqual(
            public_by_id[31]["product_origin"],
            "added_to_complete_room",
        )

    def test_template_path_does_not_create_blueprint_requirements(self):
        result = resolve_template_product_inventory(
            template_products=self.template_products,
            database_candidates=[*self.template_products, product(99, "rug")],
            completion_requirements=[],
            room_dimensions=RoomDimensions(length_cm=700, width_cm=600),
            room_type="living_room",
            requested_style="modern",
            preferred_colors=[],
        )
        self.assertEqual(
            [(item["id"], item["quantity"]) for item in result.selected_items],
            [(82, 2), (52, 1)],
        )
        self.assertNotIn(99, {item["id"] for item in result.selected_items})

    def test_application_analyzes_completion_from_template_products_first(self):
        catalog = [
            *self.template_products,
            product(31, "television"),
            product(13, "tv_stand"),
        ]
        room_analysis = RoomAnalysis(
            room_size="large",
            layout_shape="rectangular",
            usable_walls=["wide continuous unobstructed wall suitable for media"],
            circulation_paths=["clear route through the center of the room"],
        )
        template = {
            "id": 7,
            "name": "Source collection",
            "room_type": "living_room",
            "style": "modern",
            "preview_image_filename": None,
            "is_active": True,
        }
        with (
            patch(
                "app.services.template_application_service.load_design_template",
                return_value=template,
            ),
            patch(
                "app.services.template_application_service.prepare_template_source_candidates",
                return_value=(self.template_products, []),
            ),
        ):
            result = load_template_application_data(
                template_id=7,
                room_type="living_room",
                room_analysis=room_analysis,
                room_dimensions=RoomDimensions(length_cm=700, width_cm=600),
                all_items=catalog,
                requested_style="modern",
                preferred_colors=[],
            )

        product_source = result[1]
        selected_items = result[6]
        self.assertEqual(product_source["semantics"], "product_source_only")
        self.assertFalse(product_source["layout_constraints_applied"])
        self.assertEqual(
            {(item["id"], item["quantity"]) for item in selected_items},
            {(82, 2), (52, 1), (31, 1), (13, 1)},
        )


class TemplateProductSourceRendererTests(unittest.TestCase):
    def test_renderer_receives_no_template_layout_or_camera_rules(self):
        selected = product(31, "television")
        selected.update(
            selection_origin="template",
            selection_role="template_selected",
            product_origin="from_template",
        )
        persisted = PersistedDesignSelection(
            selection_id="11111111-1111-1111-1111-111111111111",
            room_request_id=18,
            template_id=7,
            mode="template_product_source",
            status="locked",
            selection_signature="selection-signature",
            blueprint_signature="product-source-signature",
            catalog_signature="catalog-signature",
            room_analysis_signature="analysis-signature",
            blueprint={
                "layout_type": "face_to_face",
                "key_visual_rules": ["force two sofas opposite"],
                "camera_composition": "copy template camera",
            },
            composition_validation={"status": "valid"},
            selected_items=[selected],
        )

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            temp = Path(temp_dir)
            room_path = temp / "room.png"
            product_path = temp / "31.png"
            room_path.write_bytes(b"room-test-reference")
            product_path.write_bytes(b"product-test-reference")
            with patch(
                "app.services.openai_image_service.get_openai_client",
                side_effect=AssertionError("paid image client must not be called"),
            ):
                manifest, prompt = _build_renderer_contract(
                    persisted_selection=persisted,
                    room_image_path=room_path,
                    room_analysis={
                        "room_size": "large",
                        "layout_shape": "rectangular",
                    },
                    architectural_constraints={},
                    selected_items=[selected],
                    furniture_reference_images=[
                        {"id": 31, "local_image_path": str(product_path)}
                    ],
                    room_type="living_room",
                    style="modern",
                    template={
                        "id": 7,
                        "name": "Template 7",
                        "layout_type": "face_to_face",
                        "key_visual_rules": ["force two sofas opposite"],
                        "camera_composition": "copy template camera",
                    },
                )

        self.assertIsNone(manifest.design_intent["locked_blueprint"])
        self.assertIsNone(manifest.design_intent["design_strategy"])
        self.assertEqual(manifest.template["semantics"], "product_source_only")
        self.assertNotIn("layout_type", manifest.template)
        self.assertNotIn("key_visual_rules", manifest.template)
        self.assertNotIn("camera_composition", manifest.template)
        self.assertEqual(
            [(item["id"], item["quantity"]) for item in manifest.selected_products],
            [(31, 1)],
        )
        self.assertEqual(
            manifest.selected_products[0]["product_origin"],
            "from_template",
        )
        self.assertNotIn("force two sofas opposite", prompt)
        self.assertNotIn("copy template camera", prompt)
        self.assertNotIn("face_to_face", prompt)
        self.assertIn(
            "selected television as the primary functional focal point",
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
