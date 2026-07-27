"""Public recommendation API backed by the unified selection engine."""

from __future__ import annotations

from collections import defaultdict

from fastapi import HTTPException

from app.models import DesignRequest
from app.services.design_director_service import (
    DesignStrategy,
    is_corner_sofa,
)
from app.services.room_analysis_service import RoomAnalysis


def validate_selection_against_design_strategy(
    selected_items: list[dict],
    design_strategy: DesignStrategy | None,
    constraints: dict,
) -> None:
    """Validate persisted inventory against hard structured strategy targets."""

    del constraints  # Guided hard requirements are resolved before persistence.
    if design_strategy is None:
        return

    category_quantities = defaultdict(int)
    standard_sofa_quantity = 0
    corner_sofa_quantity = 0
    for item in selected_items:
        quantity = int(item.get("quantity", 1))
        category = str(item.get("category", ""))
        category_quantities[category] += quantity
        if category == "sofa":
            if is_corner_sofa(item):
                corner_sofa_quantity += quantity
            else:
                standard_sofa_quantity += quantity

    if corner_sofa_quantity > 1:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Selected corner/sectional sofa quantity exceeds 1.",
                "corner_sofa_quantity": corner_sofa_quantity,
            },
        )

    violations = []
    for target in design_strategy.seating:
        if target.category == "armchair":
            actual_quantity = category_quantities["armchair"]
        elif target.solution_type == "standard_sofa":
            actual_quantity = standard_sofa_quantity
        elif target.solution_type in {"corner_sofa", "sectional"}:
            actual_quantity = corner_sofa_quantity
        else:
            actual_quantity = category_quantities[target.category]
        if actual_quantity != int(target.quantity):
            violations.append(
                {
                    "category": target.category,
                    "solution_type": target.solution_type,
                    "expected": int(target.quantity),
                    "selected": actual_quantity,
                }
            )

    for target in design_strategy.decor:
        if target.priority != "essential":
            continue
        actual_quantity = category_quantities[target.category]
        if actual_quantity < int(target.min_quantity):
            violations.append(
                {
                    "category": target.category,
                    "priority": target.priority,
                    "minimum": int(target.min_quantity),
                    "selected": actual_quantity,
                }
            )

    if violations:
        raise HTTPException(
            status_code=502,
            detail={
                "message": (
                    "Persisted selection does not satisfy the hard Design "
                    "Director strategy targets."
                ),
                "strategy_violations": violations,
            },
        )


def generate_recommendation(
    room_items: list[dict],
    request: DesignRequest,
    room_analysis: RoomAnalysis | None = None,
    design_strategy: DesignStrategy | None = None,
) -> dict:
    """Resolve custom/auto requirements through the shared deterministic engine."""

    from app.services.unified_recommendation_service import (
        generate_unified_recommendation,
    )

    return generate_unified_recommendation(
        room_items=room_items,
        request=request,
        room_analysis=room_analysis,
        design_strategy=design_strategy,
    )


def generate_custom_recommendation(
    room_items: list[dict],
    request: DesignRequest,
    room_analysis: RoomAnalysis | None = None,
) -> dict:
    """Prepare an immutable exact-product selection without system additions."""

    from app.services.unified_recommendation_service import (
        generate_exact_custom_recommendation,
    )

    return generate_exact_custom_recommendation(
        room_items=room_items,
        request=request,
        room_analysis=room_analysis,
    )
