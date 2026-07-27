"""Custom/auto adapter for the shared deterministic selection engine."""

from __future__ import annotations

from fastapi import HTTPException

from app.models import DesignRequest
from app.room_templates import ROOM_TEMPLATES
from app.services.ai_selector_service import resolve_design_constraints
from app.services.composition_fit_service import (
    CompositionFitStatus,
    validate_composition_fit,
)
from app.services.architectural_constraint_service import (
    build_architectural_constraint_map,
)
from app.services.catalog_compatibility_service import is_design_eligible
from app.services.design_director_service import DesignStrategy
from app.services.design_requirement_service import build_strategy_requirement_slots
from app.services.room_analysis_service import RoomAnalysis
from app.services.room_completeness_service import (
    analyze_room_completeness,
    finalize_media_zone_assessment,
    reduce_composition_with_media_priority,
)
from app.services.room_fit_service import (
    evaluate_product_fit,
    validate_exact_required_products_fit,
)
from app.services.unified_selection_service import resolve_product_requirements


def generate_exact_custom_recommendation(
    *,
    room_items: list[dict],
    request: DesignRequest,
    room_analysis: RoomAnalysis | None,
) -> dict:
    """Prepare only the user's exact inventory for immutable custom rendering.

    Custom mode is intentionally not a room-completion workflow. It still runs
    the same catalog eligibility, exact-fit, architecture, completeness, and
    broad composition checks, but it never inserts a system-selected product.
    """

    room_type = request.room_type.value
    room_template = ROOM_TEMPLATES.get(room_type)
    if room_template is None:
        raise HTTPException(
            status_code=400,
            detail=f"No room template exists for: {room_type}",
        )

    constraints = resolve_design_constraints(
        request=request,
        room_template=room_template,
        candidate_items=room_items,
    )
    excluded = set(constraints["excluded_categories"])
    candidate_items = [
        item
        for item in room_items
        if is_design_eligible(item)
        and room_type in (item.get("room_types") or [])
        and item.get("category") not in excluded
        and evaluate_product_fit(item, request.room_dimensions).get("fits") is not False
    ]
    validate_exact_required_products_fit(
        required_products=request.required_products,
        candidate_items=candidate_items,
        room_dimensions=request.room_dimensions,
    )
    candidate_by_id = {int(item["id"]): item for item in candidate_items}
    included_products: list[dict] = []
    for requirement in request.required_products:
        product_id = int(requirement.product_id)
        item = candidate_by_id.get(product_id)
        if item is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Exact user-selected product is unavailable or invalid.",
                    "product_id": product_id,
                },
            )
        included_products.append({**item, "quantity": int(requirement.quantity)})

    result = resolve_product_requirements(
        requirements=[],
        included_products=included_products,
        preferred_candidates=[],
        completion_candidates=[],
        included_origin="user",
        room_dimensions=request.room_dimensions,
        room_type=room_type,
        requested_style=None,
        preferred_colors=[],
        max_unique_products=int(room_template.get("max_unique_products", 15)),
        allow_optional_slots=False,
        density_target="balanced",
    )
    effective_room_analysis = room_analysis or RoomAnalysis(
        room_size="medium",
        layout_shape="unknown",
        provided_dimensions_cm=request.room_dimensions,
    )
    architectural_constraints = build_architectural_constraint_map(
        room_analysis=effective_room_analysis,
        room_dimensions=request.room_dimensions,
    )
    completeness = analyze_room_completeness(
        room_type=room_type,
        room_analysis=effective_room_analysis,
        current_selected_items=result.selected_items,
        architectural_constraints=architectural_constraints,
        catalog_items=candidate_items,
        room_dimensions=request.room_dimensions,
        excluded_categories=excluded,
        user_prompt=request.user_prompt,
        allow_additions=False,
    )
    composition = validate_composition_fit(
        selected_items=result.selected_items,
        room_dimensions=request.room_dimensions,
        obstacles=(room_analysis.obstacles if room_analysis else None),
        circulation_paths=(
            room_analysis.circulation_paths if room_analysis else None
        ),
        room_type=room_type,
    )
    if composition.status == CompositionFitStatus.INVALID:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Blocking error: the exact selected composition is "
                    "physically impossible for the measured room."
                ),
                "composition_validation": composition.model_dump(mode="json"),
            },
        )

    selected_ids = [int(item["id"]) for item in result.selected_items]
    selected_products = [
        {"id": int(item["id"]), "quantity": int(item["quantity"])}
        for item in result.selected_items
    ]
    total_price = round(
        sum(float(item["line_total"]) for item in result.selected_items), 2
    )
    currencies = {
        item.get("currency") for item in result.selected_items if item.get("currency")
    }

    return {
        "room_type": room_type,
        "room_analysis": room_analysis.model_dump() if room_analysis else None,
        "design_strategy": None,
        "selection_mode": "custom",
        "selection_source": "exact_custom_inventory",
        "selector_model": None,
        "ai_selector_called": False,
        "engine_version": result.engine_version,
        "requirements_signature": result.requirements_signature,
        "catalog_signature": result.catalog_signature,
        "requirements": [],
        "slot_satisfaction": [],
        "slot_assignments": [],
        "unresolved_slots": [],
        "selection_warnings": result.warnings,
        "architectural_constraint_map": architectural_constraints.model_dump(
            mode="json"
        ),
        "room_completeness": completeness.model_dump(mode="json"),
        "composition_reduction": {
            "removed_product_ids": [],
            "removed_lower_priority_product_ids": [],
            "removed_media_product_ids": [],
            "lower_priority_reduction_attempted": False,
            "hard_conflict": False,
        },
        "composition_validation": composition.model_dump(mode="json"),
        "applied_requirements": {
            "required_quantities": constraints["required_quantities"],
            "excluded_categories": constraints["excluded_categories"],
            "explicit_auto_categories": constraints["explicit_auto_categories"],
        },
        "selected_furniture_ids": selected_ids,
        "selected_products": selected_products,
        "recommended_items": result.selected_items,
        "ai_design_summary": (
            "The immutable custom inventory contains exactly the products and "
            "quantities selected by the user."
        ),
        "selection_breakdown": {
            "user_required_selected_ids": selected_ids,
            "required_selected_ids": selected_ids,
            "one_of_selected_ids": [],
            "optional_selected_ids": [],
            "system_completion_ids": [],
        },
        "missing_required_categories": [],
        "missing_one_of_groups": [],
        "total_price": total_price,
        "currency": next(iter(currencies)) if len(currencies) == 1 else None,
        "budget_applied": False,
    }


def generate_unified_recommendation(
    *,
    room_items: list[dict],
    request: DesignRequest,
    room_analysis: RoomAnalysis | None,
    design_strategy: DesignStrategy | None,
) -> dict:
    room_type = request.room_type.value
    room_template = ROOM_TEMPLATES.get(room_type)
    if room_template is None:
        raise HTTPException(
            status_code=400,
            detail=f"No room template exists for: {room_type}",
        )

    constraints = resolve_design_constraints(
        request=request,
        room_template=room_template,
        candidate_items=room_items,
    )
    excluded = set(constraints["excluded_categories"])
    candidate_items = [
        item
        for item in room_items
        if is_design_eligible(item)
        and room_type in (item.get("room_types") or [])
        and item.get("category") not in excluded
        and evaluate_product_fit(item, request.room_dimensions).get("fits") is not False
    ]
    if not candidate_items:
        raise HTTPException(
            status_code=404,
            detail="No active, physically eligible catalog products remain.",
        )

    validate_exact_required_products_fit(
        required_products=request.required_products,
        candidate_items=candidate_items,
        room_dimensions=request.room_dimensions,
    )
    candidate_by_id = {int(item["id"]): item for item in candidate_items}
    included_products = []
    for requirement in request.required_products:
        product_id = int(requirement.product_id)
        item = candidate_by_id.get(product_id)
        if item is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Exact user-selected product is unavailable or invalid.",
                    "product_id": product_id,
                },
            )
        included_products.append(
            {**item, "quantity": int(requirement.quantity)}
        )

    requirements = build_strategy_requirement_slots(
        design_strategy=design_strategy,
        constraints=constraints,
        room_template=room_template,
    )
    style = request.style.value if request.style else None
    result = resolve_product_requirements(
        requirements=requirements,
        included_products=included_products,
        preferred_candidates=[],
        completion_candidates=candidate_items,
        included_origin="user",
        preferred_origin="template",
        room_dimensions=request.room_dimensions,
        room_type=room_type,
        requested_style=style,
        preferred_colors=request.preferred_colors,
        max_unique_products=int(room_template.get("max_unique_products", 15)),
        allow_optional_slots=False,
        density_target=(
            design_strategy.density_target if design_strategy else "balanced"
        ),
    )

    effective_room_analysis = room_analysis or RoomAnalysis(
        room_size="medium",
        layout_shape="unknown",
        provided_dimensions_cm=request.room_dimensions,
    )
    architectural_constraints = build_architectural_constraint_map(
        room_analysis=effective_room_analysis,
        room_dimensions=request.room_dimensions,
    )
    completeness = analyze_room_completeness(
        room_type=room_type,
        room_analysis=effective_room_analysis,
        current_selected_items=result.selected_items,
        architectural_constraints=architectural_constraints,
        catalog_items=candidate_items,
        room_dimensions=request.room_dimensions,
        design_strategy=design_strategy,
        slot_satisfaction=result.slot_satisfaction,
        excluded_categories=excluded,
        user_prompt=request.user_prompt,
        density_target=(
            design_strategy.density_target if design_strategy else "balanced"
        ),
    )
    if completeness.additional_requirements:
        requirements = [
            *requirements,
            *completeness.additional_requirements,
        ]
        result = resolve_product_requirements(
            requirements=requirements,
            included_products=included_products,
            preferred_candidates=[],
            completion_candidates=candidate_items,
            included_origin="user",
            preferred_origin="template",
            room_dimensions=request.room_dimensions,
            room_type=room_type,
            requested_style=style,
            preferred_colors=request.preferred_colors,
            max_unique_products=int(room_template.get("max_unique_products", 15)),
            allow_optional_slots=False,
            density_target=(
                design_strategy.density_target if design_strategy else "balanced"
            ),
        )

    unresolved_essential = [
        slot.model_dump()
        for slot in result.unresolved_slots
        if slot.importance == "essential"
    ]
    if unresolved_essential:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "The active catalog cannot satisfy essential design requirements.",
                "unresolved_slots": unresolved_essential,
            },
        )

    composition_reduction = reduce_composition_with_media_priority(
        selected_items=result.selected_items,
        completeness=completeness,
        room_dimensions=request.room_dimensions,
        obstacles=(room_analysis.obstacles if room_analysis else None),
        circulation_paths=(
            room_analysis.circulation_paths if room_analysis else None
        ),
        room_type=room_type,
    )
    result.selected_items = composition_reduction.selected_items
    composition = composition_reduction.validation
    removed_items = [
        *composition_reduction.removed_lower_priority_items,
        *composition_reduction.removed_media_items,
    ]
    if removed_items:
        result.warnings.append(
            "Media-aware capacity validation removed only permitted system additions before finalization."
        )
    completeness = analyze_room_completeness(
        room_type=room_type,
        room_analysis=effective_room_analysis,
        current_selected_items=result.selected_items,
        architectural_constraints=architectural_constraints,
        catalog_items=candidate_items,
        room_dimensions=request.room_dimensions,
        design_strategy=design_strategy,
        slot_satisfaction=result.slot_satisfaction,
        excluded_categories=excluded,
        user_prompt=request.user_prompt,
        density_target=(
            design_strategy.density_target if design_strategy else "balanced"
        ),
        allow_additions=False,
    )
    completeness = finalize_media_zone_assessment(
        completeness=completeness,
        selected_items=result.selected_items,
        reduction=composition_reduction,
    )
    if composition.status == CompositionFitStatus.INVALID:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Blocking error: the combined selected composition is "
                    "physically impossible for the measured room."
                ),
                "composition_validation": composition.model_dump(mode="json"),
            },
        )

    selected_ids = [int(item["id"]) for item in result.selected_items]
    selected_products = [
        {"id": int(item["id"]), "quantity": int(item["quantity"])}
        for item in result.selected_items
    ]
    total_price = round(
        sum(float(item["line_total"]) for item in result.selected_items), 2
    )
    currencies = {
        item.get("currency")
        for item in result.selected_items
        if item.get("currency")
    }
    currency = next(iter(currencies)) if len(currencies) == 1 else None

    return {
        "room_type": room_type,
        "room_analysis": room_analysis.model_dump() if room_analysis else None,
        "design_strategy": design_strategy.model_dump() if design_strategy else None,
        "selection_mode": constraints["selection_mode"],
        "selection_source": "unified_deterministic_engine",
        "selector_model": None,
        "ai_selector_called": False,
        "engine_version": result.engine_version,
        "requirements_signature": result.requirements_signature,
        "catalog_signature": result.catalog_signature,
        "requirements": [slot.model_dump() for slot in requirements],
        "slot_satisfaction": [
            satisfaction.model_dump() for satisfaction in result.slot_satisfaction
        ],
        "slot_assignments": [
            assignment.model_dump() for assignment in result.assignments
        ],
        "unresolved_slots": [slot.model_dump() for slot in result.unresolved_slots],
        "selection_warnings": result.warnings,
        "architectural_constraint_map": architectural_constraints.model_dump(
            mode="json"
        ),
        "room_completeness": completeness.model_dump(mode="json"),
        "composition_reduction": {
            "removed_product_ids": [
                int(item["id"]) for item in removed_items
            ],
            "removed_lower_priority_product_ids": [
                int(item["id"])
                for item in composition_reduction.removed_lower_priority_items
            ],
            "removed_media_product_ids": [
                int(item["id"])
                for item in composition_reduction.removed_media_items
            ],
            "lower_priority_reduction_attempted": (
                composition_reduction.lower_priority_reduction_attempted
            ),
            "hard_conflict": composition_reduction.hard_conflict,
        },
        "composition_validation": composition.model_dump(mode="json"),
        "applied_requirements": {
            "required_quantities": constraints["required_quantities"],
            "excluded_categories": constraints["excluded_categories"],
            "explicit_auto_categories": constraints["explicit_auto_categories"],
        },
        "selected_furniture_ids": selected_ids,
        "selected_products": selected_products,
        "recommended_items": result.selected_items,
        "ai_design_summary": (
            "Included products were preserved first; only missing design slots "
            "were completed deterministically from active database products."
        ),
        "selection_breakdown": {
            "user_required_selected_ids": [
                int(item["id"])
                for item in result.selected_items
                if item.get("selection_origin") == "user"
            ],
            "required_selected_ids": selected_ids,
            "one_of_selected_ids": [],
            "optional_selected_ids": [
                assignment.product_id
                for assignment in result.assignments
                if next(
                    slot.importance
                    for slot in requirements
                    if slot.slot_key == assignment.slot_key
                ) == "optional"
            ],
            "system_completion_ids": [
                int(item["id"])
                for item in result.selected_items
                if item.get("selection_origin") == "system"
            ],
        },
        "missing_required_categories": [],
        "missing_one_of_groups": [],
        "total_price": total_price,
        "currency": currency,
        "budget_applied": False,
    }
