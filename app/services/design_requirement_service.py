"""Mode adapters that express design intent as shared requirement slots."""

from __future__ import annotations

from app.services.design_director_service import DesignStrategy
from app.services.unified_selection_service import DesignRequirementSlot


FUNCTIONAL_ZONE_BY_CATEGORY = {
    "sofa": "seating",
    "armchair": "seating",
    "coffee_table": "central",
    "rug": "central",
    "television": "media",
    "tv_stand": "media",
    "sideboard": "storage",
    "storage_cabinet": "storage",
    "bookshelf": "storage",
    "wall_shelf": "storage",
    "floor_lamp": "lighting",
    "side_table": "supporting_surfaces",
    "wall_art": "visual_focal",
}


def functional_zone_for_category(category: str) -> str | None:
    return FUNCTIONAL_ZONE_BY_CATEGORY.get(category)


def build_strategy_requirement_slots(
    *,
    design_strategy: DesignStrategy | None,
    constraints: dict,
    room_template: dict,
) -> list[DesignRequirementSlot]:
    slots: list[DesignRequirementSlot] = []

    if design_strategy is not None:
        for index, target in enumerate(design_strategy.seating, start=1):
            slots.append(
                DesignRequirementSlot(
                    slot_key=f"strategy_{target.category}_{target.role}_{index}",
                    category=target.category,
                    required_quantity=target.quantity,
                    importance="essential",
                    visual_role=(
                        f"{target.role} {target.solution_type or target.category} seating"
                    ),
                    placement_zone="main seating composition",
                    functional_zone="seating",
                    solution_type=target.solution_type,
                )
            )

        for index, target in enumerate(design_strategy.decor, start=1):
            if target.min_quantity > 0:
                quantity = target.min_quantity
                importance = (
                    "essential"
                    if target.priority == "essential"
                    else "supporting"
                    if target.priority == "recommended"
                    else "optional"
                )
            elif target.max_quantity > 0:
                quantity = target.max_quantity
                importance = "optional"
            else:
                continue
            slots.append(
                DesignRequirementSlot(
                    slot_key=f"strategy_{target.category}_{index}",
                    category=target.category,
                    required_quantity=quantity,
                    importance=importance,
                    visual_role=f"{target.priority} design-strategy product",
                    placement_zone="supporting room composition",
                    functional_zone=functional_zone_for_category(target.category),
                )
            )
    else:
        for index, category in enumerate(room_template.get("required", []), start=1):
            slots.append(
                DesignRequirementSlot(
                    slot_key=f"room_required_{category}_{index}",
                    category=category,
                    required_quantity=1,
                    importance="essential",
                    visual_role="required room-template product",
                    placement_zone="main room composition",
                    functional_zone=functional_zone_for_category(category),
                )
            )

    excluded = set(constraints.get("excluded_categories", []))
    slots = [slot for slot in slots if slot.category not in excluded]

    # Explicit guided/custom quantities override the strategy quantity while
    # preserving the strategy slot identity and visual role when one exists.
    for category, raw_quantity in constraints.get("required_quantities", {}).items():
        if category in excluded:
            continue
        quantity = int(raw_quantity)
        category_slots = [slot for slot in slots if slot.category == category]
        if category_slots:
            first = category_slots[0].model_copy(
                update={"required_quantity": quantity, "importance": "essential"}
            )
            slots = [slot for slot in slots if slot.category != category]
            slots.append(first)
        else:
            slots.append(
                DesignRequirementSlot(
                    slot_key=f"user_required_{category}",
                    category=category,
                    required_quantity=quantity,
                    importance="essential",
                    visual_role="explicit user-required category quantity",
                    placement_zone="room composition",
                    functional_zone=functional_zone_for_category(category),
                )
            )

    return slots
