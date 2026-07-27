import unittest

from app.models import DesignRequest, RoomDimensions
from app.services.design_director_service import DesignStrategy
from app.services.recommendation_service import generate_recommendation
from app.services.composition_fit_service import (
    CompositionFitStatus,
    validate_composition_fit,
)
from app.services.room_analysis_service import RoomAnalysis
from app.services.unified_selection_service import (
    DesignRequirementSlot,
    resolve_product_requirements,
)


def product(
    product_id: int,
    category: str,
    *,
    quantity: int = 1,
    name: str | None = None,
    color: str = "beige",
    material: str | None = "fabric",
    style: str = "modern",
    width_cm: float = 100,
    depth_cm: float = 70,
    height_cm: float = 70,
    availability: bool = True,
    catalog_status: str = "active",
    tags: list[str] | None = None,
) -> dict:
    return {
        "id": product_id,
        "name": name or f"Product {product_id}",
        "category": category,
        "style": style,
        "color": color,
        "material": material,
        "width_cm": width_cm,
        "depth_cm": depth_cm,
        "height_cm": height_cm,
        "price": 100.0,
        "currency": "JOD",
        "image_filename": f"{product_id}.png",
        "product_url": f"https://example.com/{product_id}",
        "store_name": "Test",
        "room_types": ["living_room"],
        "tags": tags or [],
        "availability": availability,
        "catalog_status": catalog_status,
        "quantity": quantity,
    }


def slot(
    key: str,
    category: str,
    quantity: int = 1,
    *,
    importance: str = "essential",
    colors: list[str] | None = None,
    materials: list[str] | None = None,
    visual_role: str = "required room product",
    repeat_group_id: str | None = None,
    same_product_required: bool = False,
    solution_type: str | None = None,
) -> DesignRequirementSlot:
    return DesignRequirementSlot(
        slot_key=key,
        category=category,
        required_quantity=quantity,
        importance=importance,
        preferred_colors=colors or [],
        preferred_materials=materials or [],
        visual_role=visual_role,
        placement_zone="room composition",
        repeat_group_id=repeat_group_id,
        same_product_required=same_product_required,
        solution_type=solution_type,
    )


def resolve(requirements, included=None, preferred=None, catalog=None, **kwargs):
    return resolve_product_requirements(
        requirements=requirements,
        included_products=included or [],
        preferred_candidates=preferred or [],
        completion_candidates=catalog or [],
        included_origin=kwargs.pop("included_origin", "user"),
        preferred_origin=kwargs.pop("preferred_origin", "template"),
        room_dimensions=kwargs.pop(
            "room_dimensions",
            RoomDimensions(length_cm=600, width_cm=500),
        ),
        room_type="living_room",
        requested_style=kwargs.pop("requested_style", "modern"),
        preferred_colors=kwargs.pop("preferred_colors", []),
        **kwargs,
    )


class IncludedSatisfactionTests(unittest.TestCase):
    def test_user_already_satisfies_sofa_quantity(self):
        result = resolve(
            [slot("main_sofas", "sofa", 2)],
            included=[product(82, "sofa", quantity=2)],
            catalog=[product(86, "sofa")],
        )
        satisfaction = result.slot_satisfaction[0]
        self.assertEqual(satisfaction.fulfilled_quantity, 2)
        self.assertEqual(satisfaction.missing_quantity, 0)
        self.assertEqual([(item["id"], item["quantity"]) for item in result.selected_items], [(82, 2)])

    def test_user_partially_satisfies_only_missing_unit(self):
        result = resolve(
            [slot("main_sofas", "sofa", 2)],
            included=[product(82, "sofa", quantity=1)],
            catalog=[product(86, "sofa")],
        )
        satisfaction = result.slot_satisfaction[0]
        self.assertEqual(satisfaction.fulfilled_quantity, 2)
        self.assertEqual(satisfaction.missing_quantity, 0)
        self.assertEqual(
            sum(item["quantity"] for item in result.selected_items if item["category"] == "sofa"),
            2,
        )
        self.assertEqual(
            sum(item.get("completion_quantity", 0) for item in result.selected_items),
            1,
        )

    def test_template_included_products_are_not_reselected(self):
        included = [
            product(82, "sofa", quantity=2),
            product(65, "coffee_table"),
            product(84, "rug"),
            product(81, "storage_cabinet"),
        ]
        result = resolve(
            [
                slot("sofas", "sofa", 2),
                slot("table", "coffee_table"),
                slot("rug", "rug"),
                slot("cabinet", "storage_cabinet"),
                slot("lamp", "floor_lamp", importance="supporting"),
            ],
            included=included,
            catalog=[product(28, "floor_lamp"), product(99, "sofa")],
            included_origin="template",
        )
        selected = {item["id"]: item for item in result.selected_items}
        self.assertTrue({82, 65, 84, 81, 28} <= set(selected))
        self.assertNotIn(99, selected)
        self.assertEqual(selected[82]["quantity"], 2)
        self.assertEqual(selected[28]["selection_origin"], "system")


class PreferredCandidateTests(unittest.TestCase):
    def test_one_coffee_table_slot_selects_one_source_candidate(self):
        result = resolve(
            [slot("table", "coffee_table")],
            preferred=[product(65, "coffee_table"), product(83, "coffee_table")],
            preferred_origin="template",
        )
        self.assertEqual(len(result.selected_items), 1)
        self.assertEqual(result.selected_items[0]["selection_origin"], "template")
        self.assertEqual(len(result.unassigned_preferred_candidate_ids), 1)

    def test_one_rug_slot_selects_one_unless_layers_are_explicit(self):
        result = resolve(
            [slot("rug", "rug")],
            preferred=[product(84, "rug"), product(85, "rug")],
            preferred_origin="template",
        )
        self.assertEqual(sum(item["quantity"] for item in result.selected_items), 1)

    def test_low_glass_cabinet_beats_tall_solid_cabinet(self):
        requirement = slot(
            "cabinet",
            "storage_cabinet",
            importance="supporting",
            colors=["warm wood", "clear glass"],
            materials=["wood frame", "glass doors"],
            visual_role="low transparent storage layer",
        )
        result = resolve(
            [requirement],
            preferred=[
                product(80, "storage_cabinet", name="Tall solid cabinet", height_cm=126, tags=["oak"]),
                product(81, "storage_cabinet", name="Low glass-door cabinet", height_cm=86, tags=["oak", "glass"]),
            ],
            preferred_origin="template",
        )
        self.assertEqual(result.selected_items[0]["id"], 81)


class OptionalAndQualityTests(unittest.TestCase):
    def test_optional_slot_is_not_forced_in_dense_room(self):
        result = resolve(
            [slot("optional_table", "side_table", importance="optional")],
            catalog=[product(5, "side_table")],
            density_target="layered",
        )
        self.assertEqual(result.selected_items, [])
        self.assertEqual(result.unresolved_slots[0].reason_code, "optional_not_forced")

    def test_poor_oversized_art_match_is_not_strong(self):
        result = resolve(
            [
                slot(
                    "oversized_art",
                    "wall_art",
                    colors=["umber", "taupe", "parchment"],
                    materials=["canvas"],
                    # The persisted Template 7 blueprint carries the scale
                    # semantic in slot_key, so structured identity must be
                    # part of deterministic visual scoring too.
                    visual_role="dominant earth-toned artwork",
                )
            ],
            catalog=[
                product(
                    39,
                    "wall_art",
                    name="Small blue picture",
                    color="blue and natural",
                    material="canvas",
                    width_cm=52,
                    depth_cm=3,
                    height_cm=72,
                    tags=["blue", "calm"],
                )
            ],
        )
        self.assertEqual(result.selected_items, [])
        self.assertEqual(
            result.unresolved_slots[0].reason_code,
            "below_visual_threshold",
        )


class OriginAndSafetyTests(unittest.TestCase):
    def test_normalized_role_and_origin_metadata(self):
        user = resolve([slot("sofa", "sofa")], included=[product(1, "sofa")])
        template = resolve(
            [slot("table", "coffee_table")],
            preferred=[product(2, "coffee_table")],
            preferred_origin="template",
        )
        system = resolve([slot("rug", "rug")], catalog=[product(3, "rug")])
        self.assertEqual((user.selected_items[0]["selection_role"], user.selected_items[0]["selection_origin"]), ("user_selected", "user"))
        self.assertEqual((template.selected_items[0]["selection_role"], template.selected_items[0]["selection_origin"]), ("template_selected", "template"))
        self.assertEqual((system.selected_items[0]["selection_role"], system.selected_items[0]["selection_origin"]), ("system_completion", "system"))
        self.assertTrue(user.selected_items[0]["was_included"])
        self.assertTrue(system.selected_items[0]["added_for_completion"])

    def test_auto_mode_uses_same_engine_with_no_included_products(self):
        result = resolve(
            [slot("sofa", "sofa"), slot("rug", "rug")],
            catalog=[product(1, "sofa"), product(2, "rug")],
        )
        self.assertEqual({item["category"] for item in result.selected_items}, {"sofa", "rug"})
        self.assertTrue(all(item["selection_origin"] == "system" for item in result.selected_items))

    def test_quarantined_candidate_is_never_selected(self):
        result = resolve(
            [slot("sofa", "sofa")],
            catalog=[
                product(62, "sofa", catalog_status="quarantined"),
                product(82, "sofa"),
            ],
        )
        self.assertEqual([item["id"] for item in result.selected_items], [82])

    def test_dense_composition_is_warned_but_not_rejected_by_a_ratio(self):
        items = [
            product(1, "sofa", width_cm=220, depth_cm=100),
            product(2, "sofa", width_cm=220, depth_cm=100),
            product(3, "armchair", width_cm=100, depth_cm=100),
            product(4, "armchair", width_cm=100, depth_cm=100),
            product(5, "coffee_table", width_cm=140, depth_cm=90),
        ]
        validation = validate_composition_fit(
            items,
            RoomDimensions(length_cm=350, width_cm=300),
        )
        self.assertEqual(
            validation.status,
            CompositionFitStatus.VALID_WITH_WARNINGS,
        )
        self.assertTrue(validation.confirmation_allowed)
        self.assertEqual(validation.density_classification, "high_density")

    def test_capacity_ranking_allows_an_individually_fitting_dense_sofa(self):
        room = RoomDimensions(length_cm=380, width_cm=350)
        result = resolve(
            [slot("primary_sofa", "sofa")],
            catalog=[
                product(1, "sofa", name="Oversized corner sofa", width_cm=243, depth_cm=243),
                product(2, "sofa", name="Compact two-seat sofa", width_cm=142, depth_cm=77),
            ],
            room_dimensions=room,
        )
        self.assertEqual([item["id"] for item in result.selected_items], [1])
        self.assertNotEqual(
            validate_composition_fit(result.selected_items, room).status,
            CompositionFitStatus.INVALID,
        )

    def test_capacity_ranking_allows_a_feasible_layered_combination(self):
        room = RoomDimensions(length_cm=380, width_cm=350)
        result = resolve(
            [slot("primary_sofa", "sofa"), slot("central_table", "coffee_table")],
            catalog=[
                product(1, "sofa", width_cm=243, depth_cm=243),
                product(2, "sofa", width_cm=142, depth_cm=77),
                product(3, "coffee_table", width_cm=330, depth_cm=170),
                product(4, "coffee_table", width_cm=90, depth_cm=55),
            ],
            room_dimensions=room,
        )
        self.assertEqual({item["id"] for item in result.selected_items}, {1, 3})
        self.assertNotEqual(
            validate_composition_fit(result.selected_items, room).status,
            CompositionFitStatus.INVALID,
        )

    def test_standard_sofa_requirement_excludes_corner_and_sectional_products(self):
        result = resolve(
            [slot("primary_sofa", "sofa", solution_type="standard_sofa")],
            catalog=[
                product(
                    1,
                    "sofa",
                    name="Corner sofa",
                    width_cm=243,
                    depth_cm=243,
                    tags=["corner_sofa", "sectional", "family_seating"],
                ),
                product(
                    2,
                    "sofa",
                    name="Compact two-seat sofa",
                    width_cm=142,
                    depth_cm=77,
                ),
            ],
            room_dimensions=RoomDimensions(length_cm=400, width_cm=400),
        )
        self.assertEqual([item["id"] for item in result.selected_items], [2])

    def test_corner_sofa_requirement_excludes_standard_sofa_products(self):
        result = resolve(
            [slot("primary_sofa", "sofa", solution_type="corner_sofa")],
            catalog=[
                product(1, "sofa", name="Standard sofa", width_cm=142, depth_cm=77),
                product(
                    2,
                    "sofa",
                    name="Compact corner sofa",
                    width_cm=148,
                    depth_cm=76,
                    tags=["corner_sofa", "sectional", "l_shape"],
                ),
            ],
            room_dimensions=RoomDimensions(length_cm=400, width_cm=400),
        )
        self.assertEqual([item["id"] for item in result.selected_items], [2])

    def test_room_25_shape_can_satisfy_strategy_and_media_from_real_categories(self):
        room = RoomDimensions(length_cm=400, width_cm=400, height_cm=390)
        strategy = DesignStrategy.model_validate(
            {
                "layout": {
                    "requested_layout": None,
                    "requested_layout_supported": None,
                    "final_layout_strategy": "l_shape_conversation",
                    "reason": "A standard sofa and movable armchairs fit the square room.",
                },
                "focal_point": "art_wall",
                "seating": [
                    {
                        "category": "sofa",
                        "solution_type": "standard_sofa",
                        "quantity": 1,
                        "role": "primary",
                    },
                    {
                        "category": "armchair",
                        "solution_type": None,
                        "quantity": 2,
                        "role": "secondary",
                    },
                ],
                "decor": [
                    {
                        "category": "rug",
                        "min_quantity": 1,
                        "max_quantity": 1,
                        "priority": "essential",
                    },
                    {
                        "category": "coffee_table",
                        "min_quantity": 1,
                        "max_quantity": 1,
                        "priority": "essential",
                    },
                    {
                        "category": "wall_art",
                        "min_quantity": 1,
                        "max_quantity": 1,
                        "priority": "essential",
                    },
                ],
                "density_target": "balanced",
                "symmetry": "asymmetric",
                "lighting_layers_needed": ["ambient"],
                "design_rationale": "Compact products preserve functional room capacity.",
            }
        )
        request = DesignRequest(
            room_type="living_room",
            room_dimensions=room,
        )
        catalog = [
            product(
                1,
                "sofa",
                name="Large corner sofa",
                width_cm=243,
                depth_cm=243,
                tags=["corner_sofa", "sectional", "family_seating"],
            ),
            product(2, "sofa", name="Compact standard sofa", width_cm=142, depth_cm=77),
            product(3, "armchair", width_cm=80, depth_cm=70),
            product(4, "rug", width_cm=170, depth_cm=240),
            product(5, "coffee_table", width_cm=90, depth_cm=55),
            product(6, "wall_art", width_cm=50, depth_cm=2),
            product(7, "television", width_cm=96, depth_cm=7),
            product(8, "tv_stand", width_cm=121, depth_cm=35),
        ]
        recommendation = generate_recommendation(
            room_items=catalog,
            request=request,
            room_analysis=RoomAnalysis(
                room_size="medium",
                layout_shape="square",
                provided_dimensions_cm=room,
                usable_walls=[
                    "A broad continuous unobstructed wall is suitable for media."
                ],
                obstacles=[],
                circulation_paths=[],
            ),
            design_strategy=strategy,
        )
        selected = recommendation["recommended_items"]
        self.assertNotIn(1, {item["id"] for item in selected})
        self.assertIn(2, {item["id"] for item in selected})
        self.assertEqual(recommendation["unresolved_slots"], [])
        self.assertTrue(
            {"sofa", "armchair", "coffee_table", "television", "tv_stand"}
            <= {item["category"] for item in selected}
        )


class RecommendationAdapterTests(unittest.TestCase):
    def strategy(self, sofa_quantity: int, include_rug: bool = False) -> DesignStrategy:
        decor = [
            {
                "category": "coffee_table",
                "min_quantity": 1,
                "max_quantity": 1,
                "priority": "essential",
            }
        ]
        if include_rug:
            decor.append(
                {
                    "category": "rug",
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "priority": "essential",
                }
            )
        return DesignStrategy.model_validate(
            {
                "layout": {
                    "requested_layout": None,
                    "requested_layout_supported": None,
                    "final_layout_strategy": "facing_sofas_conversation",
                    "reason": "Measured rectangular room supports a conversation zone.",
                },
                "focal_point": "none",
                "seating": [
                    {
                        "category": "sofa",
                        "solution_type": "standard_sofa",
                        "quantity": sofa_quantity,
                        "role": "primary",
                    }
                ],
                "decor": decor,
                "density_target": "balanced",
                "symmetry": "symmetric",
                "lighting_layers_needed": ["ambient"],
                "design_rationale": "Test strategy.",
            }
        )

    def test_custom_adapter_preserves_exact_sofas_and_completes_only_table(self):
        request = DesignRequest.model_validate(
            {
                "room_type": "living_room",
                "required_products": [{"product_id": 82, "quantity": 2}],
                "room_dimensions": {"length_cm": 600, "width_cm": 500},
            }
        )
        recommendation = generate_recommendation(
            room_items=[
                product(82, "sofa", width_cm=178, depth_cm=99),
                product(99, "sofa"),
                product(65, "coffee_table"),
            ],
            request=request,
            design_strategy=self.strategy(2),
        )
        sofas = [
            item for item in recommendation["recommended_items"]
            if item["category"] == "sofa"
        ]
        self.assertEqual([(item["id"], item["quantity"]) for item in sofas], [(82, 2)])
        self.assertEqual(sofas[0]["selection_origin"], "user")
        table = next(
            item for item in recommendation["recommended_items"]
            if item["category"] == "coffee_table"
        )
        self.assertEqual(table["selection_origin"], "system")
        self.assertFalse(recommendation["ai_selector_called"])

    def test_auto_adapter_uses_same_engine_with_empty_included_inventory(self):
        request = DesignRequest.model_validate(
            {
                "room_type": "living_room",
                "room_dimensions": {"length_cm": 600, "width_cm": 500},
            }
        )
        recommendation = generate_recommendation(
            room_items=[
                product(82, "sofa", width_cm=178, depth_cm=99),
                product(65, "coffee_table"),
                product(84, "rug", width_cm=170, depth_cm=240, height_cm=1),
            ],
            request=request,
            design_strategy=self.strategy(1, include_rug=True),
        )
        self.assertEqual(
            {item["category"] for item in recommendation["recommended_items"]},
            {"sofa", "coffee_table", "rug"},
        )
        self.assertTrue(
            all(
                item["selection_origin"] == "system"
                for item in recommendation["recommended_items"]
            )
        )


if __name__ == "__main__":
    unittest.main()
