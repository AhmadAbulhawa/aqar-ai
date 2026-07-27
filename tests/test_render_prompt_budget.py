import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import app.routes.design_routes as design_routes
from app.models import RoomDimensions
from app.services.architectural_constraint_service import (
    build_architectural_constraint_map,
)
from app.services.openai_image_service import (
    HARD_PROMPT_LIMIT,
    TARGET_PROMPT_LENGTH,
    build_generation_prompt,
    build_render_prompt_debug_metadata,
    build_template_blueprint_text,
    generate_furnished_room_image,
)
from app.services.render_manifest_service import build_render_manifest
from app.services.room_analysis_service import RoomAnalysis


class GeneralLayoutGuidanceTests(unittest.TestCase):
    def guidance(self, layout_type: str, items: list[dict]) -> str:
        return build_template_blueprint_text(
            {
                "layout_type": layout_type,
                "density_target": "balanced",
                "product_slots": [],
                "key_visual_rules": [],
            },
            items,
        ).casefold()

    def test_all_supported_layouts_have_actionable_guidance(self):
        sofa_pair = [product(1, "Sofa", "sofa", quantity=2)]
        cases = {
            "facing_sofas_conversation": (sofa_pair, "face-to-face"),
            "u_shape_conversation": (sofa_pair, "open u-shaped"),
            "l_shape_conversation": (sofa_pair, "l-shaped seating"),
            "media_wall_focused": (
                [product(2, "TV", "television")],
                "comfortable viewing relationship",
            ),
            "single_sofa_minimal": (
                [product(3, "Sofa", "sofa")],
                "primary anchor",
            ),
            "floating_zone": (
                [product(4, "Table", "coffee_table")],
                "float the main selected furniture group",
            ),
            "bed_wall_focused": (
                [product(5, "Bed", "bed")],
                "balanced bedside access",
            ),
            "workstation_focused": (
                [product(6, "Desk", "desk")],
                "functional workstation",
            ),
            "dining_centered": (
                [product(7, "Dining table", "dining_table")],
                "chair pull-out clearance",
            ),
            "storage_optimized": (
                [product(8, "Wardrobe", "wardrobe")],
                "consolidate selected storage",
            ),
            "functional_zoning": (
                [product(9, "Sofa", "sofa")],
                "clear functional zones",
            ),
        }
        for layout_type, (items, expected) in cases.items():
            with self.subTest(layout_type=layout_type):
                self.assertIn(expected, self.guidance(layout_type, items))

    def test_template_finish_rules_are_removed_for_every_layout(self):
        text = build_template_blueprint_text(
            {
                "layout_type": "functional_zoning",
                "density_target": "balanced",
                "product_slots": [],
                "key_visual_rules": [
                    "Use a dark blue accent wall behind the sofa.",
                    "Replace the room character with warm wood flooring.",
                    "Keep the selected sofa as the primary anchor.",
                ],
            },
            [product(10, "Sofa", "sofa")],
        ).casefold()
        self.assertNotIn("dark blue accent wall", text)
        self.assertNotIn("wood flooring", text)
        self.assertIn("selected sofa as the primary anchor", text)

    def test_auto_strategy_uses_the_same_general_layout_guidance(self):
        prompt = build_generation_prompt(
            [product(11, "Sofa", "sofa", quantity=2)],
            "living_room",
            "modern",
            render_manifest={
                "room_analysis": {},
                "architectural_constraints": {},
                "explicit_user_requirements": [],
                "design_intent": {
                    "design_strategy": {
                        "layout": {
                            "final_layout_strategy": "u_shape_conversation"
                        },
                        "density_target": "balanced",
                        "symmetry": "asymmetric",
                        "focal_point": "none",
                    }
                },
            },
        ).casefold()
        self.assertIn("design strategy", prompt)
        self.assertIn("open u-shaped conversation zone", prompt)


class TvBasedLivingRoomPromptTests(unittest.TestCase):
    def build_prompt(
        self,
        *,
        room_type: str = "living_room",
        include_tv: bool = True,
        include_coffee_table: bool = True,
        sofa_quantity: int = 1,
        explicit_user_requirements: list[str] | None = None,
        template: dict | None = None,
        locked_blueprint: dict | None = None,
        design_intent_fields: dict | None = None,
    ) -> str:
        items = [product(201, "Primary sofa", "sofa", quantity=sofa_quantity)]
        if include_coffee_table:
            items.append(product(202, "Coffee table", "coffee_table"))
        if include_tv:
            items.append(product(203, "Selected television", "television"))
        design_intent = {
            "locked_blueprint": locked_blueprint,
            "design_strategy": {},
        }
        design_intent.update(design_intent_fields or {})
        return build_generation_prompt(
            items,
            room_type,
            "modern",
            template_context=template,
            render_manifest={
                "room_analysis": {},
                "architectural_constraints": {},
                "explicit_user_requirements": explicit_user_requirements or [],
                "template": template,
                "design_intent": design_intent,
            },
        )

    def test_default_principle_applies_to_living_room_with_tv(self):
        prompt = self.build_prompt()
        self.assertIn(
            "PROFESSIONAL SPACE-UTILIZATION AND FURNITURE-ORIENTATION PRINCIPLE",
            prompt,
        )
        self.assertIn("selected television as the primary functional focal point", prompt)
        self.assertIn("primary sofa or sectional directly or predominantly", prompt)
        self.assertIn("comfortable, unobstructed viewing relationship", prompt)
        self.assertIn("Secondary seating may angle toward both", prompt)
        self.assertIn("coffee table naturally accessible", prompt)
        self.assertIn("primary sofa may show its back or side to the camera", prompt)
        self.assertIn("Camera visibility must never control furniture orientation", prompt)

    def test_principle_leaves_exact_arrangement_to_renderer(self):
        prompt = self.build_prompt()
        principle = prompt.split(
            "PROFESSIONAL SPACE-UTILIZATION AND FURNITURE-ORIENTATION PRINCIPLE", 1
        )[1].split("MANDATORY PRE-OUTPUT CLEARANCE SCAN", 1)[0]
        self.assertIn(
            "Exact placement remains the responsibility of GPT Image 2",
            principle,
        )
        for forbidden in ("x_cm", "y_cm", "north wall", "south wall", "placement planner"):
            self.assertNotIn(forbidden, principle.casefold())

    def test_ordinary_conversation_template_still_gets_tv_principle(self):
        prompt = self.build_prompt(
            template={"id": 7, "name": "Balanced conversation"},
            locked_blueprint={
                "layout_type": "facing_sofas_conversation",
                "composition_summary": (
                    "Two sofas support conversation while the room remains flexible."
                ),
                "product_slots": [],
                "key_visual_rules": [],
            },
        )
        self.assertIn("selected television as the primary functional focal point", prompt)

    def test_no_tv_stand_sentence_does_not_exempt_media_layout(self):
        prompt = self.build_prompt(
            explicit_user_requirements=[
                "Use no TV stand because the television is wall-mounted."
            ]
        )
        self.assertIn("selected television as the primary functional focal point", prompt)

    def test_template_non_media_text_cannot_exempt_product_source_mode(self):
        prompt = self.build_prompt(
            template={"id": 8, "name": "Conversation-only salon"},
            locked_blueprint={
                "layout_type": "facing_sofas_conversation",
                "composition_summary": (
                    "A conversation-only non-media living room without television focus."
                ),
                "product_slots": [],
                "key_visual_rules": [],
            },
        )
        self.assertIn("selected television as the primary functional focal point", prompt)

    def test_template_tv_facing_prohibition_cannot_influence_renderer(self):
        prompt = self.build_prompt(
            template={"id": 9, "name": "Facing conversation"},
            locked_blueprint={
                "layout_type": "facing_sofas_conversation",
                "composition_summary": "Two sofas create a conversation group.",
                "product_slots": [],
                "key_visual_rules": [
                    "Do not convert it into an L-shaped or television-facing layout."
                ],
            },
        )
        self.assertIn("selected television as the primary functional focal point", prompt)
        self.assertNotIn("Do not convert it", prompt)
        self.assertNotIn("television-facing layout", prompt)

    def test_explicit_alternate_room_purpose_is_exempt(self):
        prompt = self.build_prompt(
            explicit_user_requirements=[
                "Use this living space as a reading room with conversation seating."
            ]
        )
        self.assertNotIn("selected television as the primary functional focal point", prompt)

    def test_structured_reading_room_purpose_is_exempt(self):
        prompt = self.build_prompt(
            design_intent_fields={"room_purpose": "reading_room"}
        )
        self.assertNotIn("selected television as the primary functional focal point", prompt)

    def test_structured_home_office_purpose_is_exempt(self):
        prompt = self.build_prompt(
            design_intent_fields={"purpose": "home_office"}
        )
        self.assertNotIn("selected television as the primary functional focal point", prompt)

    def test_coffee_table_instruction_requires_selected_coffee_table(self):
        with_coffee_table = self.build_prompt()
        without_coffee_table = self.build_prompt(include_coffee_table=False)
        instruction = "Keep the selected coffee table naturally accessible"
        self.assertIn(instruction, with_coffee_table)
        self.assertNotIn(instruction, without_coffee_table)

    def test_immutable_quantities_remain_unchanged(self):
        prompt = self.build_prompt(sofa_quantity=2)
        self.assertIn("quantity EXACTLY 2 physical units", prompt)
        self.assertIn("exactly TWO visible physical units of product 201", prompt)

    def test_repeated_corner_sofas_use_wall_tv_and_rear_route_without_reshaping(self):
        items = [
            product(
                201,
                "HYLTARP corner sofa, 4-seat",
                "sofa",
                quantity=2,
                width_cm=264,
                depth_cm=264,
            ),
            product(203, "Selected television", "television"),
        ]
        prompt = build_generation_prompt(
            items,
            "living_room",
            "modern",
            render_manifest={
                "room_analysis": {},
                "architectural_constraints": {},
                "explicit_user_requirements": [],
                "design_intent": {"design_strategy": {}},
            },
        )

        self.assertIn("complete unit safely aligned with an appropriate wall", prompt)
        self.assertIn("circulation route may run behind the second unit", prompt)
        self.assertIn("Never\n  straighten it", prompt)
        self.assertIn("remove or shorten its corner return/chaise", prompt)

    def test_existing_architecture_and_safety_rules_remain(self):
        prompt = self.build_prompt()
        for rule in (
            "ORIGINAL CAMERA VIEWPOINT AND FRAMING ARE AUTHORITATIVE",
            "Preserve the original room architecture from Image 1",
            "Never block a visible window or reasonable window access",
            "VISIBLE DOOR, ENTRY, HALLWAY, AND CIRCULATION SAFETY",
            "continuous body-width route",
            "EXACT IMMUTABLE INVENTORY",
        ):
            self.assertIn(rule, prompt)

    def test_prompt_building_never_calls_paid_image_client(self):
        with patch("app.services.openai_image_service.get_openai_client") as client:
            prompt = self.build_prompt()
        self.assertIn("selected television as the primary functional focal point", prompt)
        client.assert_not_called()

    def test_principle_does_not_apply_without_tv_or_outside_living_room(self):
        self.assertNotIn(
            "selected television as the primary functional focal point",
            self.build_prompt(include_tv=False),
        )
        self.assertNotIn(
            "PROFESSIONAL SPACE-UTILIZATION AND FURNITURE-ORIENTATION PRINCIPLE",
            self.build_prompt(room_type="bedroom"),
        )


def product(
    product_id: int,
    name: str,
    category: str,
    *,
    quantity: int = 1,
    width_cm: float = 100,
    depth_cm: float = 60,
    height_cm: float = 70,
    role: str = "system_completion",
    slot_keys: list[str] | None = None,
) -> dict:
    return {
        "id": product_id,
        "name": name,
        "category": category,
        "quantity": quantity,
        "width_cm": width_cm,
        "depth_cm": depth_cm,
        "height_cm": height_cm,
        "selection_origin": "template" if role == "template_selected" else "system",
        "selection_role": role,
        "functional_zone": "media" if category in {"television", "tv_stand"} else category,
        "functional_zones": [],
        "slot_keys": slot_keys or [],
        "availability": True,
        "catalog_status": "active",
        "room_types": ["living_room"],
        "price": 100,
        "currency": "JOD",
        "line_total": 100 * quantity,
    }


class Room7RenderPromptBudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.room_path = self.root / "room-7.jpg"
        self.template_path = self.root / "template-7.jpg"
        self.room_path.write_bytes(b"room-7-reference")
        self.template_path.write_bytes(b"template-7-reference")
        self.dimensions = RoomDimensions(
            length_cm=500,
            width_cm=350,
            height_cm=280,
        )
        self.analysis = RoomAnalysis(
            room_size="medium",
            layout_shape="rectangular",
            provided_dimensions_cm=self.dimensions,
            usable_walls=[
                "The far wall appears uninterrupted and usable across most of its visible span.",
                "The right wall appears long and unobstructed.",
            ],
            obstacles=[
                "A full-height door is located on the left wall near the foreground.",
                "A narrow vertical window is located farther along the left wall.",
            ],
            circulation_paths=[
                "Maintain a clear route from the left-side door into the central area of the room."
            ],
            floor_material="smooth concrete or cement-finish flooring",
            wall_color_family="off-white",
        )
        self.architecture = build_architectural_constraint_map(
            room_analysis=self.analysis,
            room_dimensions=self.dimensions,
        )
        self.items = [
            product(
                82,
                "STOCKHOLM 2025 2-seat sofa, Alhamn beige",
                "sofa",
                quantity=2,
                width_cm=178,
                depth_cm=99,
                role="template_selected",
                slot_keys=["left_facing_sofa", "right_facing_sofa"],
            ),
            product(
                65,
                "STOCKHOLM 2025 Coffee table, oak veneer glass",
                "coffee_table",
                width_cm=97,
                depth_cm=97,
                height_cm=38,
                role="template_selected",
                slot_keys=["central_upholstered_coffee_table"],
            ),
            product(
                84,
                "STOCKHOLM 2025 Rug, flatwoven",
                "rug",
                width_cm=170,
                depth_cm=240,
                height_cm=1,
                role="template_selected",
                slot_keys=["grounding_area_rug"],
            ),
            product(31, "LG 55-inch NanoCell TV", "television", width_cm=123.5, depth_cm=5.75, height_cm=71.5),
            product(13, "BOASTAD TV bench", "tv_stand", width_cm=121.4, depth_cm=42.1, height_cm=45.1),
            product(20, "PERSBOL Armchair", "armchair", quantity=2, width_cm=69, depth_cm=70, height_cm=84, slot_keys=["left_edge_armchairs"]),
            product(27, "LAUTERS Floor lamp", "floor_lamp", width_cm=62, depth_cm=62, height_cm=151, slot_keys=["art_front_floor_lamp"]),
            product(52, "TINGBY Side table", "side_table", width_cm=50, depth_cm=50, height_cm=45, slot_keys=["rear_round_side_table"]),
            product(76, "BURVIK Side table", "side_table", width_cm=38, depth_cm=38, height_cm=78, slot_keys=["right_black_side_table"]),
            product(81, "STOCKHOLM 2025 Glass-door cabinet", "storage_cabinet", width_cm=100, depth_cm=40, height_cm=85.5, role="template_selected", slot_keys=["rear_glass_storage_cabinet"]),
        ]
        self.blueprint = {
            "layout_type": "facing_sofas_conversation",
            "focal_point": "oversized leaning artwork behind the conversation area",
            "density_target": "layered",
            "composition_summary": "Facing sofas around a table and rug with unresolved oversized artwork.",
            "visual_anchor_summary": "Unresolved oversized artwork is the source template anchor.",
            "product_slots": [
                {"slot_key": "left_facing_sofa", "category": "sofa", "quantity": 1, "importance": "essential"},
                {"slot_key": "right_facing_sofa", "category": "sofa", "quantity": 1, "importance": "essential"},
                {"slot_key": "central_upholstered_coffee_table", "category": "coffee_table", "quantity": 1, "importance": "essential"},
                {"slot_key": "grounding_area_rug", "category": "rug", "quantity": 1, "importance": "essential"},
                {"slot_key": "oversized_artwork", "category": "wall_art", "quantity": 1, "importance": "essential"},
            ],
            "key_visual_rules": [
                "Preserve the face-to-face sofa arrangement; do not convert it into an L-shaped or television-facing layout.",
                "Use two similarly scaled matching sofas as the dominant horizontal masses.",
                "Keep the coffee table naturally centered between the sofas.",
                "Ground the seating group with the selected rug.",
                "Render the oversized artwork as the strongest focal element.",
                "Preserve open negative space around the unresolved artwork.",
            ],
        }
        references = []
        for item in self.items:
            path = self.root / f"product-{item['id']}.jpg"
            path.write_bytes(f"product-reference-{item['id']}".encode())
            references.append({"id": item["id"], "local_image_path": str(path)})
        self.manifest = build_render_manifest(
            selection_id="00000000-0000-4000-8000-000000000007",
            selection_signature="a" * 64,
            room_request_id=7,
            template_id=7,
            design_mode="auto_template_blueprint",
            room_image_path=self.room_path,
            template_image_path=self.template_path,
            selected_items=self.items,
            furniture_reference_images=references,
            room_analysis=self.analysis,
            room_analysis_signature="b" * 64,
            architectural_constraints=self.architecture,
            template={
                "id": 7,
                "name": "A modern living room for cosy mingles",
                "style": "modern",
                "wall_color_family": "dark charcoal",
                "floor_color_family": "warm brown",
                "floor_material": "wood planks",
            },
            template_blueprint_signature="c" * 64,
            design_intent={
                "design_strategy": {
                    "layout": {"final_layout_strategy": "facing_sofas_conversation"},
                    "focal_point": "art_wall",
                    "density_target": "layered",
                    "symmetry": "symmetric",
                    "design_rationale": "verbose internal rationale " * 100,
                },
                "locked_blueprint": {
                    **self.blueprint,
                    "verbose_internal_debug_payload": "must-not-leak " * 1000,
                },
                "composition_validation": {"status": "uncertain_geometry"},
            },
        )
        self.prompt = build_generation_prompt(
            self.items,
            "living_room",
            "modern",
            template_context={"id": 7, "name": "A modern living room for cosy mingles"},
            template_blueprint=self.blueprint,
            template_reference_used=True,
            render_manifest=self.manifest,
        )

    def tearDown(self):
        self.directory.cleanup()

    def test_01_room_7_prompt_stays_below_hard_limit(self):
        self.assertLess(len(self.prompt), HARD_PROMPT_LIMIT)
        self.assertLess(len(self.prompt), TARGET_PROMPT_LENGTH)
        self.assertEqual(
            build_render_prompt_debug_metadata(self.prompt),
            {
                "render_prompt_length": len(self.prompt),
                "render_prompt_target_limit": 18_000,
                "render_prompt_hard_limit": 28_000,
                "render_prompt_compacted": True,
            },
        )

    def test_02_exact_product_ids_remain(self):
        for item in self.items:
            self.assertIn(f"Product {item['id']}", self.prompt)

    def test_03_exact_quantities_remain(self):
        for item in self.items:
            self.assertIn(f"quantity EXACTLY {item['quantity']}", self.prompt)

    def test_04_tv_31_remains(self):
        self.assertIn("Product 31", self.prompt)
        self.assertIn("LG 55-inch NanoCell TV", self.prompt)

    def test_05_tv_stand_13_remains(self):
        self.assertIn("Product 13", self.prompt)
        self.assertIn("BOASTAD TV bench", self.prompt)

    def test_06_two_sofa_82_units_are_explicit(self):
        self.assertIn("quantity EXACTLY 2 physical units", self.prompt)
        self.assertIn("exactly TWO visible physical units of product 82", self.prompt)

    def test_07_door_constraint_remains(self):
        self.assertIn("Never block a visible door", self.prompt)
        self.assertIn("immediate entry landing", self.prompt)

    def test_08_window_constraint_remains(self):
        self.assertIn("Never block a visible window", self.prompt)
        self.assertIn("reasonable window access", self.prompt)

    def test_09_template_composition_does_not_reach_renderer(self):
        normalized = self.prompt.casefold()
        self.assertNotIn("face-to-face", normalized)
        self.assertNotIn("three-quarter back", normalized)
        self.assertIn("template supplied product identities only", normalized)
        self.assertIn("design a completely new arrangement", normalized)

    def test_10_no_template_topologies_reach_renderer(self):
        for phrase in ("l-shaped", "perpendicular", "angled", "partially offset"):
            self.assertNotIn(phrase, self.prompt.casefold())

    def test_11_unresolved_oversized_artwork_is_not_instructed(self):
        self.assertNotIn("oversized artwork", self.prompt.casefold())
        self.assertIn("do not invent unselected major furniture", self.prompt.casefold())

    def test_11b_selected_media_has_functional_zone_instruction(self):
        self.assertIn("primary functional focal point", self.prompt.casefold())
        self.assertIn("visually stable", self.prompt.casefold())
        self.assertIn("believable media zone", self.prompt.casefold())

    def test_11c_template_finishes_cannot_override_original_room(self):
        self.assertNotIn("dark charcoal", self.prompt.casefold())
        self.assertNotIn("warm brown", self.prompt.casefold())
        self.assertNotIn("wood planks", self.prompt.casefold())
        self.assertIn(
            "template supplied product identities only",
            self.prompt.casefold(),
        )
        self.assertIn("preserve every existing permanent finish", self.prompt.casefold())

    def test_12_full_raw_json_is_not_embedded(self):
        for marker in (
            "provided_dimensions_cm",
            "architectural_constraints",
            "locked_blueprint",
            "product_slots",
            "catalog_signature",
            "verbose_internal_debug_payload",
            "must-not-leak",
        ):
            self.assertNotIn(marker, self.prompt)

    def test_13_placement_planner_remains_unused(self):
        source = inspect.getsource(design_routes.render_ready_design_template)
        self.assertNotIn("get_or_create_placement_plan", source)
        self.assertFalse(self.manifest.placement_planner_called)

    def test_14_over_limit_prompt_stops_before_openai_client(self):
        with (
            patch(
                "app.services.openai_image_service.build_generation_prompt",
                return_value="x" * (HARD_PROMPT_LIMIT + 1),
            ),
            patch("app.services.openai_image_service.get_openai_client") as client,
        ):
            with self.assertRaises(HTTPException) as raised:
                generate_furnished_room_image(
                    room_request_id=7,
                    room_image_path=self.room_path,
                    furniture_reference_images=[],
                    recommended_items=[self.items[0]],
                    room_type="living_room",
                    style="modern",
                )
        self.assertEqual(raised.exception.status_code, 422)
        client.assert_not_called()

    def test_15_complete_design_authority_and_inventory_are_explicit(self):
        self.assertIn(
            "Choose the complete furniture arrangement yourself",
            self.prompt,
        )
        self.assertIn(
            "Do not follow any predefined sofa topology",
            self.prompt,
        )
        self.assertIn("Product 82", self.prompt)
        self.assertIn("Product 31", self.prompt)
        self.assertIn("Product 13", self.prompt)

    def test_16_product_52_has_no_forced_wall_or_zone(self):
        product_52_block = self.prompt.split("Product 52", 1)[1].split(
            "Product 76", 1
        )[0]
        self.assertIn("TINGBY Side table", product_52_block)
        for forbidden in (" wall", " zone", "corner", "near the", "place "):
            self.assertNotIn(forbidden, product_52_block.casefold())

    def test_17_template_is_not_an_input_image(self):
        self.assertEqual(
            [reference.kind for reference in self.manifest.references],
            ["room", *("product" for _ in self.items)],
        )
        self.assertIn("Images 2+: exact selected product references", self.prompt)

    def test_18_door_and_confirmed_hallway_remain_hard_constraints(self):
        self.assertIn(
            "VISIBLE DOOR, ENTRY, HALLWAY, AND CIRCULATION SAFETY",
            self.prompt,
        )
        self.assertIn("continuous body-width route", self.prompt)
        self.assertIn("Do not create a pinch point beside a visible doorway", self.prompt)
        self.assertIn("without creating an oversized empty zone", self.prompt)

    def test_19_clearance_scan_is_mandatory_and_last(self):
        self.assertLess(
            self.prompt.index("COMPLETE VISUAL-DESIGN AUTHORITY"),
            self.prompt.index("VISIBLE DOOR, ENTRY, HALLWAY"),
        )
        self.assertIn("MANDATORY PRE-OUTPUT CLEARANCE SCAN", self.prompt)
        self.assertTrue(
            self.prompt.endswith(
                "large empty prohibited zones."
            )
        )

    def test_20_room_7_does_not_invent_shared_door_window_empty_band(self):
        self.assertNotIn("Confirmed protected access band", self.prompt)
        self.assertNotIn("continuous floor and access band", self.prompt)
        self.assertIn("Use the complete available room footprint intelligently", self.prompt)

    def test_21_evidence_based_safety_gate_precedes_inventory(self):
        gate_index = self.prompt.index(
            "HARD CONSTRAINTS: EVIDENCE-BASED ARCHITECTURAL SAFETY GATE"
        )
        inventory_index = self.prompt.index("EXACT IMMUTABLE INVENTORY")
        self.assertLess(gate_index, inventory_index)
        self.assertIn(
            "Inventory compliance never overrides architectural clearance",
            self.prompt,
        )

    def test_22_original_camera_viewpoint_is_explicitly_preserved(self):
        self.assertIn(
            "ORIGINAL CAMERA VIEWPOINT AND FRAMING ARE AUTHORITATIVE",
            self.prompt,
        )
        self.assertIn(
            "same\ncamera position, viewing direction, eye level, perspective, field of view",
            self.prompt,
        )
        self.assertIn("Do not rotate the camera", self.prompt)
        self.assertIn("reverse angle", self.prompt)
        self.assertIn("exact original viewpoint", self.prompt)

    def test_23_furniture_may_show_back_or_side_to_camera(self):
        self.assertIn(
            "Furniture orientation is independent from camera viewpoint",
            self.prompt,
        )
        self.assertIn("may show its back", self.prompt)
        self.assertIn("or side to the camera", self.prompt)
        self.assertIn("not a furniture catalog showcase", self.prompt)

    def test_24_camera_rule_does_not_prescribe_furniture_placement(self):
        camera_block = self.prompt.split(
            "ORIGINAL CAMERA VIEWPOINT AND FRAMING ARE AUTHORITATIVE", 1
        )[1].split("HARD CONSTRAINTS: EVIDENCE-BASED", 1)[0]
        for forbidden in (
            "Product ",
            "place the sofa",
            "sofa on the",
            "chair near the",
            "television on the",
            "face-to-face",
            "L-shaped",
        ):
            self.assertNotIn(forbidden, camera_block)

    def test_25_permanent_room_finishes_are_preserved(self):
        normalized_prompt = " ".join(self.prompt.split())
        self.assertIn("Original permanent floor material", normalized_prompt)
        self.assertIn(
            "floor material, wall material, ceiling form", normalized_prompt
        )
        self.assertIn("fixed architectural trim", normalized_prompt)
        self.assertIn(
            "Do not redesign these permanent finishes unless the user explicitly requests it",
            normalized_prompt,
        )

    def test_26_professional_space_utilization_is_explicit_and_late(self):
        heading = (
            "PROFESSIONAL SPACE-UTILIZATION AND FURNITURE-ORIENTATION PRINCIPLE"
        )
        self.assertIn(heading, self.prompt)
        self.assertGreater(
            self.prompt.index(heading),
            self.prompt.index("OTHER HARD CONSTRAINTS"),
        )
        for required in (
            "Use the complete available room footprint intelligently",
            "arbitrary floating main sofa",
            "meaningless or unjustified dead space",
            "one coherent composition",
            "senior residential interior designer",
        ):
            self.assertIn(required, self.prompt)

    def test_27_camera_presentation_cannot_control_the_layout(self):
        self.assertIn(
            "Camera visibility must never control furniture orientation",
            self.prompt,
        )
        self.assertIn("primary sofa may show its back or side", self.prompt)
        self.assertIn("need not face\n  the camera or be fully unobstructed", self.prompt)
        self.assertIn("Natural perspective may\n  partially occlude", self.prompt)

    def test_28_no_coordinates_fixed_topology_or_placement_planner_added(self):
        principle = self.prompt.split(
            "PROFESSIONAL SPACE-UTILIZATION AND FURNITURE-ORIENTATION PRINCIPLE",
            1,
        )[1]
        for forbidden in (
            "x_cm",
            "y_cm",
            "north wall",
            "south wall",
            "placement planner",
        ):
            self.assertNotIn(forbidden, principle.casefold())
        self.assertIn("without coordinates, wall numbers, or fixed topology", principle)

    def test_29_prompt_building_does_not_call_image_generation(self):
        with patch("app.services.openai_image_service.get_openai_client") as client:
            prompt = build_generation_prompt(
                self.items,
                "living_room",
                "modern",
                render_manifest=self.manifest,
            )
        client.assert_not_called()
        self.assertIn("EXACT IMMUTABLE INVENTORY", prompt)


if __name__ == "__main__":
    unittest.main()
