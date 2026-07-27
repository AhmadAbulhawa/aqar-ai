"""Template adapter for the shared product satisfaction/completion engine."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models import RoomDimensions
from app.services.template_blueprint_service import (
    TemplateBlueprint,
    normalize_template_blueprint,
)
from app.services.unified_selection_service import (
    DesignRequirementSlot,
    SlotSatisfaction,
    build_catalog_signature,
    resolve_product_requirements,
)
from app.services.design_requirement_service import functional_zone_for_category


SLOT_RESOLVER_VERSION = "2026-07-15.2-unified"


class ResolvedSlotAssignment(BaseModel):
    slot_key: str
    category: str
    requested_quantity: int
    product_id: int
    quantity: int
    source_type: str
    selection_role: str
    score: int
    reasons: list[str] = Field(default_factory=list)
    match_quality: str = "strong"
    repeat_group_id: str | None = None
    same_product_required: bool = False


class UnresolvedTemplateSlot(BaseModel):
    slot_key: str
    category: str
    quantity: int
    importance: str
    reason: str
    reason_code: str | None = None
    repeat_group_id: str | None = None
    best_candidate_id: int | None = None
    best_candidate_score: int | None = None


class SlotResolutionResult(BaseModel):
    selected_items: list[dict] = Field(default_factory=list)
    assignments: list[ResolvedSlotAssignment] = Field(default_factory=list)
    slot_satisfaction: list[SlotSatisfaction] = Field(default_factory=list)
    unresolved_slots: list[UnresolvedTemplateSlot] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unassigned_source_candidate_ids: list[int] = Field(default_factory=list)
    catalog_signature: str
    requirements_signature: str
    resolver_version: str = SLOT_RESOLVER_VERSION


def _resolution_result(resolved, requirements: list[DesignRequirementSlot]) -> SlotResolutionResult:
    requirements_by_key = {requirement.slot_key: requirement for requirement in requirements}
    assignments = [
        ResolvedSlotAssignment(
            slot_key=assignment.slot_key,
            category=assignment.category,
            requested_quantity=requirements_by_key[assignment.slot_key].required_quantity,
            product_id=assignment.product_id,
            quantity=assignment.quantity,
            source_type=(
                "template_source" if assignment.source == "template"
                else "database_completion"
            ),
            selection_role=assignment.selection_role,
            score=assignment.match_score or 0,
            reasons=assignment.match_reasons,
            match_quality=assignment.match_quality,
            repeat_group_id=assignment.repeat_group_id,
            same_product_required=assignment.same_product_required,
        )
        for assignment in resolved.assignments
    ]
    unresolved = [
        UnresolvedTemplateSlot(
            slot_key=slot.slot_key,
            category=slot.category,
            quantity=slot.missing_quantity,
            importance=slot.importance,
            reason=slot.reason,
            reason_code=slot.reason_code,
            repeat_group_id=slot.repeat_group_id,
            best_candidate_id=slot.best_candidate_id,
            best_candidate_score=slot.best_candidate_score,
        )
        for slot in resolved.unresolved_slots
    ]
    return SlotResolutionResult(
        selected_items=resolved.selected_items,
        assignments=assignments,
        slot_satisfaction=resolved.slot_satisfaction,
        unresolved_slots=unresolved,
        warnings=resolved.warnings,
        unassigned_source_candidate_ids=resolved.unassigned_preferred_candidate_ids,
        catalog_signature=resolved.catalog_signature,
        requirements_signature=resolved.requirements_signature,
    )


def build_catalog_capability_signature(items: list[dict]) -> str:
    """Compatibility alias for callers of the previous template resolver."""

    return build_catalog_signature(items)


def blueprint_to_requirements(
    blueprint: TemplateBlueprint,
) -> list[DesignRequirementSlot]:
    blueprint = normalize_template_blueprint(blueprint)
    return [
        DesignRequirementSlot(
            slot_key=slot.slot_key,
            category=slot.category,
            required_quantity=slot.quantity,
            importance=slot.importance,
            visual_role=slot.visual_role,
            placement_zone=slot.placement_zone,
            relationships=slot.relationships,
            preferred_colors=slot.preferred_colors,
            preferred_materials=slot.preferred_materials,
            repeat_group_id=slot.repeat_group_id,
            same_product_required=slot.same_product_required,
            layer_group_id=slot.layer_group_id,
            functional_zone=functional_zone_for_category(slot.category),
        )
        for slot in blueprint.product_slots
    ]


def resolve_template_slots(
    *,
    blueprint: TemplateBlueprint,
    source_candidates: list[dict],
    database_candidates: list[dict],
    room_dimensions: RoomDimensions | None,
    room_type: str,
    requested_style: str | None,
    preferred_colors: list[str],
    max_unique_products: int = 14,
    additional_requirements: list[DesignRequirementSlot] | None = None,
) -> SlotResolutionResult:
    additions = list(additional_requirements or [])
    media_additions = [
        requirement
        for requirement in additions
        if requirement.functional_zone == "media"
        and requirement.importance == "essential"
    ]
    other_additions = [
        requirement
        for requirement in additions
        if requirement not in media_additions
    ]
    requirements = [
        # Reserve required media capacity before choosing large template
        # seating. The resolver can then select the closest sofa that fits the
        # complete functional room instead of greedily locking an oversized
        # sofa and later dropping the television/console pair.
        *media_additions,
        *blueprint_to_requirements(blueprint),
        *other_additions,
    ]
    resolved = resolve_product_requirements(
        requirements=requirements,
        included_products=[],
        preferred_candidates=source_candidates,
        completion_candidates=database_candidates,
        included_origin="template",
        preferred_origin="template",
        room_dimensions=room_dimensions,
        room_type=room_type,
        requested_style=requested_style,
        preferred_colors=preferred_colors,
        max_unique_products=max_unique_products,
        allow_optional_slots=False,
        density_target=blueprint.density_target,
    )

    return _resolution_result(resolved, requirements)


def resolve_template_product_inventory(
    *,
    template_products: list[dict],
    database_candidates: list[dict],
    completion_requirements: list[DesignRequirementSlot],
    room_dimensions: RoomDimensions | None,
    room_type: str,
    requested_style: str | None,
    preferred_colors: list[str],
    max_unique_products: int = 15,
) -> SlotResolutionResult:
    """Preserve matched template products and fill only functional gaps.

    Template products are hard included inventory. Template blueprint slots,
    topology, relationships, placement zones, and camera composition are not
    inputs to this product-only completion path.
    """

    template_ids = {int(item["id"]) for item in template_products}
    resolved = resolve_product_requirements(
        requirements=list(completion_requirements),
        included_products=template_products,
        preferred_candidates=[],
        completion_candidates=[
            item for item in database_candidates if int(item["id"]) not in template_ids
        ],
        included_origin="template",
        preferred_origin="template",
        room_dimensions=room_dimensions,
        room_type=room_type,
        requested_style=requested_style,
        preferred_colors=preferred_colors,
        max_unique_products=max_unique_products,
        allow_optional_slots=False,
        density_target="balanced",
    )
    return _resolution_result(resolved, list(completion_requirements))
