from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.models import RoomDimensions
from app.services.catalog_compatibility_service import is_design_eligible
from app.services.composition_fit_service import (
    CompositionFitStatus,
    validate_composition_fit,
)
from app.services.room_fit_service import evaluate_product_fit


UNIFIED_SELECTION_VERSION = "2026-07-18.2-sofa-solution-type"

SelectionOrigin = Literal["user", "template", "system"]
SlotImportance = Literal["essential", "supporting", "optional"]


class DesignRequirementSlot(BaseModel):
    slot_key: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=80)
    required_quantity: int = Field(ge=1, le=10)
    importance: SlotImportance = "essential"
    visual_role: str = "required product"
    placement_zone: str = "room composition"
    relationships: list[str] = Field(default_factory=list)
    preferred_colors: list[str] = Field(default_factory=list)
    preferred_materials: list[str] = Field(default_factory=list)
    repeat_group_id: str | None = None
    same_product_required: bool = False
    layer_group_id: str | None = None
    functional_zone: str | None = Field(default=None, max_length=80)
    functional_group_id: str | None = Field(default=None, max_length=100)
    solution_type: Literal[
        "standard_sofa",
        "corner_sofa",
        "sectional",
    ] | None = None
    completeness_level: Literal[
        "required",
        "conditionally_required",
        "recommended",
        "optional",
        "incompatible",
    ] | None = None
    allowed_product_ids: list[int] = Field(default_factory=list)


class FulfilledByProduct(BaseModel):
    product_id: int
    quantity: int
    source: SelectionOrigin
    match_quality: Literal["included", "strong", "weak"]


class SlotSatisfaction(BaseModel):
    slot_key: str
    category: str
    required_quantity: int
    fulfilled_quantity: int
    missing_quantity: int
    fulfilled_by: list[FulfilledByProduct] = Field(default_factory=list)
    importance: SlotImportance
    repeat_group_id: str | None = None
    same_product_required: bool = False


class UnresolvedRequirement(BaseModel):
    slot_key: str
    category: str
    missing_quantity: int
    importance: SlotImportance
    reason_code: Literal[
        "no_eligible_candidate",
        "below_visual_threshold",
        "optional_not_forced",
        "reference_limit",
        "repeat_product_unavailable",
    ]
    reason: str
    repeat_group_id: str | None = None
    best_candidate_id: int | None = None
    best_candidate_score: int | None = None


class UnifiedSlotAssignment(BaseModel):
    slot_key: str
    category: str
    product_id: int
    quantity: int
    source: SelectionOrigin
    selection_role: str
    match_score: int | None = None
    match_quality: Literal["included", "strong", "weak"]
    match_reasons: list[str] = Field(default_factory=list)
    repeat_group_id: str | None = None
    same_product_required: bool = False


class UnifiedSelectionResult(BaseModel):
    selected_items: list[dict] = Field(default_factory=list)
    slot_satisfaction: list[SlotSatisfaction] = Field(default_factory=list)
    assignments: list[UnifiedSlotAssignment] = Field(default_factory=list)
    unresolved_slots: list[UnresolvedRequirement] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unassigned_preferred_candidate_ids: list[int] = Field(default_factory=list)
    catalog_signature: str
    requirements_signature: str
    engine_version: str = UNIFIED_SELECTION_VERSION


def build_catalog_signature(items: list[dict]) -> str:
    unique = {int(item["id"]): item for item in items}
    payload = sorted(
        [
            {
                "id": product_id,
                "category": item.get("category"),
                "style": item.get("style"),
                "color": item.get("color"),
                "material": item.get("material"),
                "width_cm": item.get("width_cm"),
                "depth_cm": item.get("depth_cm"),
                "height_cm": item.get("height_cm"),
                "availability": bool(item.get("availability", True)),
                "catalog_status": item.get("catalog_status", "active"),
                "design_eligible": bool(item.get("design_eligible", True)),
                "image_filename": item.get("image_filename"),
                "mounting_type": item.get("mounting_type"),
            }
            for product_id, item in unique.items()
        ],
        key=lambda item: item["id"],
    )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_requirements_signature(requirements: list[DesignRequirementSlot]) -> str:
    payload = {
        "engine_version": UNIFIED_SELECTION_VERSION,
        "requirements": [
            requirement.model_dump(mode="json") for requirement in requirements
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _tokens(*values) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            tokens.update(_tokens(*value))
            continue
        tokens.update(re.findall(r"[a-z0-9]+", str(value).lower()))
    return tokens


COLOR_GROUPS = (
    {"beige", "cream", "ivory", "parchment", "taupe", "tan", "camel", "ochre", "umber", "brown"},
    {"black", "charcoal", "anthracite", "dark", "grey", "gray"},
    {"white", "ivory", "cream", "off"},
    {"oak", "wood", "brown", "tan", "birch", "ash"},
    {"blue", "navy", "teal"},
    {"green", "olive", "sage"},
)

MATERIAL_GROUPS = (
    {"fabric", "textile", "polyester", "boucle", "upholstery", "upholstered", "canvas", "woven"},
    {"wood", "oak", "birch", "ash", "veneer", "timber"},
    {"metal", "steel", "aluminium", "bronze", "iron"},
    {"glass", "transparent", "clear"},
)


def _expanded_groups(tokens: set[str], groups) -> set[str]:
    expanded = set(tokens)
    for group in groups:
        if tokens & group:
            expanded.update(group)
    return expanded


def _eligible(
    item: dict,
    requirement: DesignRequirementSlot,
    room_type: str,
    room_dimensions: RoomDimensions | None,
) -> bool:
    if (
        requirement.allowed_product_ids
        and int(item.get("id", 0)) not in requirement.allowed_product_ids
    ):
        return False
    if str(item.get("category")) != requirement.category:
        return False
    if requirement.category == "sofa" and requirement.solution_type is not None:
        product_tokens = _tokens(item.get("name"), item.get("tags"))
        is_corner_or_sectional = bool(
            product_tokens
            & {
                "corner",
                "corner_sofa",
                "sectional",
                "l_shape",
                "chaise_longue",
                "complete_seating_set",
            }
        )
        if requirement.solution_type == "standard_sofa" and is_corner_or_sectional:
            return False
        if requirement.solution_type in {"corner_sofa", "sectional"} and not (
            is_corner_or_sectional
        ):
            return False
    if not is_design_eligible(item):
        return False
    room_types = item.get("room_types") or []
    if room_types and room_type not in room_types:
        return False
    return evaluate_product_fit(item, room_dimensions).get("fits") is not False


def _semantic_size_score(
    item: dict,
    requirement: DesignRequirementSlot,
    product_tokens: set[str],
) -> tuple[int, list[str]]:
    visual_tokens = _tokens(
        requirement.slot_key,
        requirement.visual_role,
        requirement.placement_zone,
        requirement.relationships,
    )
    width = float(item.get("width_cm") or 0)
    depth = float(item.get("depth_cm") or 0)
    height = float(item.get("height_cm") or 0)
    score = 0
    reasons: list[str] = []

    if visual_tokens & {"oversized", "monumental", "dominant"}:
        if max(width, height) >= 90:
            score += 15
            reasons.append("oversized scale compatible")
        else:
            # Scale is defining for an oversized focal anchor. A small item
            # must not recover to a strong match merely through style/color
            # points accumulated elsewhere in the score.
            score -= 50
            reasons.append("too small for oversized visual anchor")

    if "low" in visual_tokens:
        maximum_height = 100 if requirement.category in {
            "storage_cabinet", "sideboard", "tv_stand"
        } else 55
        if height and height <= maximum_height:
            score += 15
            reasons.append("low-profile proportions compatible")
        else:
            score -= 15
            reasons.append("too tall for low-profile slot")

    if visual_tokens & {"transparent", "glass"}:
        if product_tokens & {"glass", "transparent", "clear"}:
            score += 15
            reasons.append("transparent/glass construction compatible")
        else:
            score -= 12
            reasons.append("missing transparent/glass character")

    if visual_tokens & {"slender", "slim"}:
        if width and depth and max(width, depth) <= 45:
            score += 12
            reasons.append("slender footprint compatible")
        else:
            score -= 8
            reasons.append("footprint is not slender")

    if "tall" in visual_tokens:
        if height >= 140:
            score += 10
            reasons.append("tall proportions compatible")
        else:
            score -= 10
            reasons.append("too short for tall slot")

    if visual_tokens & {"upholstered", "padded", "soft"}:
        if product_tokens & {
            "fabric", "textile", "polyester", "upholstery", "upholstered",
            "padded", "ottoman", "boucle",
        }:
            score += 10
            reasons.append("soft/upholstered character compatible")
        else:
            score -= 8
            reasons.append("missing soft/upholstered character")

    return score, reasons


def score_candidate(
    item: dict,
    requirement: DesignRequirementSlot,
    *,
    requested_style: str | None,
    preferred_colors: list[str],
    preferred_source: bool,
    already_used: bool,
) -> tuple[int, list[str]]:
    score = 30
    reasons = ["exact category match"]
    if preferred_source:
        score += 12
        reasons.append("template source relationship")

    item_style = str(item.get("style") or "").strip().lower()
    if requested_style and item_style == requested_style.strip().lower():
        score += 8
        reasons.append("requested style match")

    product_tokens = _tokens(
        item.get("name"), item.get("color"), item.get("material"), item.get("tags")
    )
    desired_color_tokens = _tokens(requirement.preferred_colors, preferred_colors)
    if desired_color_tokens:
        desired_colors = _expanded_groups(desired_color_tokens, COLOR_GROUPS)
        product_colors = _expanded_groups(
            _tokens(item.get("color")), COLOR_GROUPS
        )
        if desired_colors & product_colors:
            score += 18
            reasons.append("color family compatible")
        else:
            score -= 12
            reasons.append("color family mismatch")

    desired_material_tokens = _tokens(requirement.preferred_materials)
    if desired_material_tokens:
        desired_materials = _expanded_groups(
            desired_material_tokens, MATERIAL_GROUPS
        )
        product_materials = _expanded_groups(product_tokens, MATERIAL_GROUPS)
        if desired_materials & product_materials:
            score += 15
            reasons.append("material family compatible")
        else:
            score -= 10
            reasons.append("material family mismatch")

        shade_required = bool(
            desired_material_tokens & {"shade", "fabric", "textile"}
        )
        shade_present = bool(
            product_tokens & {
                "shade", "fabric", "textile", "polyester", "canvas", "woven"
            }
        )
        if shade_required and not shade_present:
            score -= 30
            reasons.append("required fabric/shade character missing")

    semantic_tokens = _tokens(
        requirement.slot_key,
        requirement.visual_role,
        requirement.placement_zone,
        requirement.relationships,
    )
    semantic_overlap = len(semantic_tokens & product_tokens)
    if semantic_overlap:
        score += min(semantic_overlap * 3, 12)
        reasons.append("visual-role keyword match")

    size_score, size_reasons = _semantic_size_score(
        item, requirement, product_tokens
    )
    score += size_score
    reasons.extend(size_reasons)

    if not already_used:
        score += 2
        reasons.append("unused model preserves slot identity")
    return score, reasons


def _thresholds(
    requirement: DesignRequirementSlot,
    preferred_source: bool,
) -> tuple[int, int]:
    visually_specific = bool(
        requirement.preferred_colors
        or requirement.preferred_materials
        or _tokens(requirement.slot_key, requirement.visual_role)
        & {
            "oversized", "monumental", "dominant", "low", "transparent",
            "glass", "slender", "slim", "tall", "upholstered", "padded",
        }
    )
    if preferred_source:
        return (30 if visually_specific else 25, 15)
    if not visually_specific:
        return 28, 20
    strong = {
        "essential": 50,
        "supporting": 45,
        "optional": 60,
    }[requirement.importance]
    return strong, strong - 15


def _role_for_origin(origin: SelectionOrigin) -> str:
    return {
        "user": "user_selected",
        "template": "template_selected",
        "system": "system_completion",
    }[origin]


def _prepare_final_item(
    item: dict,
    origin: SelectionOrigin,
    quantity: int,
    *,
    was_included: bool,
    match_score: int | None = None,
    match_quality: str = "included",
    inclusion_reason: str,
) -> dict:
    prepared = dict(item)
    prepared.update(
        {
            "quantity": quantity,
            "included_quantity": quantity if was_included else 0,
            "completion_quantity": 0 if was_included else quantity,
            "selection_role": _role_for_origin(origin),
            "selection_origin": origin,
            "was_included": was_included,
            "added_for_completion": not was_included,
            "inclusion_reason": inclusion_reason,
            "slot_keys": [],
            "slot_assignments": [],
            "repeat_group_id": None,
            "score": match_score or 0,
            "match_quality": match_quality,
            "functional_zone": None,
            "functional_zones": [],
            "functional_group_ids": [],
            "requirement_importance": None,
            "requirement_importances": [],
            "completeness_level": None,
        }
    )
    prepared["line_total"] = round(
        float(prepared.get("price") or 0) * quantity, 2
    )
    return prepared


def resolve_product_requirements(
    *,
    requirements: list[DesignRequirementSlot],
    included_products: list[dict],
    preferred_candidates: list[dict],
    completion_candidates: list[dict],
    included_origin: SelectionOrigin,
    preferred_origin: SelectionOrigin = "template",
    room_dimensions: RoomDimensions | None,
    room_type: str,
    requested_style: str | None,
    preferred_colors: list[str],
    max_unique_products: int = 15,
    allow_optional_slots: bool = False,
    density_target: str = "balanced",
) -> UnifiedSelectionResult:
    """Satisfy structured requirements from included inventory, then gaps.

    Included quantities are allocated before any candidate ranking. Candidate
    completion is deterministic and never increases a requirement whose
    missing quantity is already zero.
    """

    keys = [requirement.slot_key for requirement in requirements]
    if len(keys) != len(set(keys)):
        raise ValueError("Requirement slot_key values must be unique.")

    requirements_by_key = {
        requirement.slot_key: requirement for requirement in requirements
    }
    fulfilled: dict[str, int] = {key: 0 for key in keys}
    fulfilled_by: dict[str, list[FulfilledByProduct]] = defaultdict(list)
    assignments: list[UnifiedSlotAssignment] = []
    warnings: list[str] = []
    unresolved: list[UnresolvedRequirement] = []
    final_by_id: dict[int, dict] = {}
    repeat_owner: dict[str, int] = {}

    all_catalog = [
        *included_products,
        *preferred_candidates,
        *completion_candidates,
    ]

    # Included inventory is a hard authority after availability/data/fit checks.
    remaining_included: dict[int, int] = {}
    original_included_by_id = {
        int(item["id"]): item for item in included_products
    }
    for item in included_products:
        product_id = int(item["id"])
        quantity = max(int(item.get("quantity", 1)), 1)
        matching_requirement = next(
            (
                requirement
                for requirement in requirements
                if requirement.category == item.get("category")
            ),
            None,
        )
        hard_valid = (
            is_design_eligible(item)
            and (
                not (item.get("room_types") or [])
                or room_type in (item.get("room_types") or [])
            )
            and evaluate_product_fit(item, room_dimensions).get("fits") is not False
        )
        if matching_requirement is not None:
            hard_valid = _eligible(
                item, matching_requirement, room_type, room_dimensions
            )
        if not hard_valid:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Included product failed a hard availability, design-eligibility, catalog, room, or physical-fit rule.",
                    "product_id": product_id,
                },
            )
        final_by_id[product_id] = _prepare_final_item(
            item,
            included_origin,
            quantity,
            was_included=True,
            inclusion_reason=(
                "Exact user-selected product preserved."
                if included_origin == "user"
                else "Product visually resolved from the selected template."
            ),
        )
        remaining_included[product_id] = quantity

    importance_order = {"essential": 0, "supporting": 1, "optional": 2}
    ordered_requirements = sorted(
        requirements,
        key=lambda requirement: (importance_order[requirement.importance], keys.index(requirement.slot_key)),
    )

    def allocate(
        requirement: DesignRequirementSlot,
        product_id: int,
        quantity: int,
        source: SelectionOrigin,
        quality: Literal["included", "strong", "weak"],
        score: int | None,
        reasons: list[str],
    ) -> None:
        if quantity <= 0:
            return
        fulfilled[requirement.slot_key] += quantity
        role = _role_for_origin(source)
        fulfilled_by[requirement.slot_key].append(
            FulfilledByProduct(
                product_id=product_id,
                quantity=quantity,
                source=source,
                match_quality=quality,
            )
        )
        assignments.append(
            UnifiedSlotAssignment(
                slot_key=requirement.slot_key,
                category=requirement.category,
                product_id=product_id,
                quantity=quantity,
                source=source,
                selection_role=role,
                match_score=score,
                match_quality=quality,
                match_reasons=reasons,
                repeat_group_id=requirement.repeat_group_id,
                same_product_required=requirement.same_product_required,
            )
        )
        final_item = final_by_id[product_id]
        importance_order = {"essential": 0, "supporting": 1, "optional": 2}
        existing_importance = final_item.get("requirement_importance")
        if (
            existing_importance is None
            or importance_order[requirement.importance]
            < importance_order.get(str(existing_importance), 99)
        ):
            final_item["requirement_importance"] = requirement.importance
        if requirement.importance not in final_item["requirement_importances"]:
            final_item["requirement_importances"].append(requirement.importance)
        if requirement.functional_zone:
            if final_item.get("functional_zone") is None:
                final_item["functional_zone"] = requirement.functional_zone
            if requirement.functional_zone not in final_item["functional_zones"]:
                final_item["functional_zones"].append(requirement.functional_zone)
        if (
            requirement.functional_group_id
            and requirement.functional_group_id
            not in final_item["functional_group_ids"]
        ):
            final_item["functional_group_ids"].append(
                requirement.functional_group_id
            )
        if requirement.completeness_level:
            final_item["completeness_level"] = requirement.completeness_level
        if requirement.repeat_group_id:
            final_item["repeat_group_id"] = requirement.repeat_group_id
        for unit_offset in range(quantity):
            final_item["slot_keys"].append(requirement.slot_key)
            final_item["slot_assignments"].append(
                {
                    "slot_key": requirement.slot_key,
                    "unit_offset": unit_offset + 1,
                    "repeat_group_id": requirement.repeat_group_id,
                }
            )

    # Explicit slot keys win; category fallback is used only for remaining units.
    for product_id, remaining in list(remaining_included.items()):
        item = final_by_id[product_id]
        explicit_keys = list(
            original_included_by_id[product_id].get("slot_keys") or []
        )
        for slot_key in explicit_keys:
            requirement = requirements_by_key.get(slot_key)
            if requirement is None or remaining <= 0:
                continue
            if requirement.category != item.get("category"):
                continue
            missing = requirement.required_quantity - fulfilled[slot_key]
            quantity = min(remaining, max(missing, 0))
            if quantity:
                allocate(requirement, product_id, quantity, included_origin, "included", None, ["explicit included slot assignment"])
                remaining -= quantity
        remaining_included[product_id] = remaining

    for requirement in ordered_requirements:
        missing = requirement.required_quantity - fulfilled[requirement.slot_key]
        if missing <= 0:
            continue
        for product_id in list(remaining_included):
            if missing <= 0:
                break
            remaining = remaining_included[product_id]
            item = final_by_id[product_id]
            if remaining <= 0 or item.get("category") != requirement.category:
                continue
            if requirement.repeat_group_id and requirement.same_product_required:
                owner = repeat_owner.get(requirement.repeat_group_id)
                if owner is not None and owner != product_id:
                    continue
                repeat_owner[requirement.repeat_group_id] = product_id
            quantity = min(remaining, missing)
            allocate(requirement, product_id, quantity, included_origin, "included", None, ["included quantity satisfies category fallback"])
            remaining_included[product_id] -= quantity
            missing -= quantity

    preferred_ids = {int(item["id"]) for item in preferred_candidates}
    assigned_preferred_ids: set[int] = set()
    used_product_ids = set(final_by_id)

    def choose_candidate(
        requirement: DesignRequirementSlot,
        candidates: list[dict],
        preferred_source: bool,
        restricted_product_id: int | None,
        projected_quantity: int,
    ):
        ranked = []
        for item in candidates:
            product_id = int(item["id"])
            if (
                preferred_source
                and product_id in assigned_preferred_ids
                and restricted_product_id != product_id
            ):
                continue
            if restricted_product_id is not None and product_id != restricted_product_id:
                continue
            if not _eligible(item, requirement, room_type, room_dimensions):
                continue
            # Individual fit is not enough: an otherwise valid product can
            # consume the entire circulation envelope (for example a 243 cm
            # corner sofa in a 380 x 350 cm room). Rank only candidates that
            # keep the composition broadly feasible at this exact step. This
            # lets the resolver try a smaller real catalog alternative instead
            # of selecting an impossible essential item that later reduction
            # is forbidden to remove.
            if room_dimensions is not None:
                projected_items = [dict(selected) for selected in final_by_id.values()]
                projected_existing = next(
                    (
                        selected
                        for selected in projected_items
                        if int(selected["id"]) == product_id
                    ),
                    None,
                )
                if projected_existing is not None:
                    projected_existing["quantity"] = (
                        int(projected_existing.get("quantity", 0))
                        + projected_quantity
                    )
                else:
                    projected_items.append(
                        {**item, "quantity": projected_quantity}
                    )
                projected_fit = validate_composition_fit(
                    selected_items=projected_items,
                    room_dimensions=room_dimensions,
                    room_type=room_type,
                )
                if projected_fit.status == CompositionFitStatus.INVALID:
                    continue
            score, reasons = score_candidate(
                item,
                requirement,
                requested_style=requested_style,
                preferred_colors=preferred_colors,
                preferred_source=preferred_source,
                already_used=product_id in used_product_ids,
            )
            ranked.append((score, -product_id, item, reasons))
        if not ranked:
            return None
        return max(ranked, key=lambda entry: (entry[0], entry[1]))

    processed_repeat_groups: set[str] = set()
    for requirement in ordered_requirements:
        group_requirements = [requirement]
        if requirement.repeat_group_id and requirement.same_product_required:
            if requirement.repeat_group_id in processed_repeat_groups:
                continue
            processed_repeat_groups.add(requirement.repeat_group_id)
            group_requirements = [
                candidate
                for candidate in ordered_requirements
                if candidate.repeat_group_id == requirement.repeat_group_id
                and candidate.same_product_required
            ]

        missing_total = sum(
            max(candidate.required_quantity - fulfilled[candidate.slot_key], 0)
            for candidate in group_requirements
        )
        if missing_total <= 0:
            continue

        if all(candidate.importance == "optional" for candidate in group_requirements) and not allow_optional_slots:
            for candidate in group_requirements:
                missing = candidate.required_quantity - fulfilled[candidate.slot_key]
                if missing > 0:
                    unresolved.append(
                        UnresolvedRequirement(
                            slot_key=candidate.slot_key,
                            category=candidate.category,
                            missing_quantity=missing,
                            importance=candidate.importance,
                            reason_code="optional_not_forced",
                            reason=(
                                "Optional slot was not forced; included inventory, density, and reference capacity take priority."
                            ),
                            repeat_group_id=candidate.repeat_group_id,
                        )
                    )
            continue

        restricted_product_id = None
        if requirement.repeat_group_id and requirement.same_product_required:
            restricted_product_id = repeat_owner.get(requirement.repeat_group_id)

        chosen = choose_candidate(
            requirement,
            preferred_candidates,
            True,
            restricted_product_id,
            missing_total,
        )
        source: SelectionOrigin = preferred_origin
        preferred_source = True
        if chosen is None:
            candidates = completion_candidates
            if restricted_product_id is not None:
                included_match = [
                    item for item in included_products
                    if int(item["id"]) == restricted_product_id
                ]
                candidates = [*included_match, *completion_candidates]
            chosen = choose_candidate(
                requirement,
                candidates,
                False,
                restricted_product_id,
                missing_total,
            )
            source = "system"
            preferred_source = False

        if chosen is None:
            reason_code = (
                "repeat_product_unavailable"
                if restricted_product_id is not None
                else "no_eligible_candidate"
            )
            for candidate in group_requirements:
                missing = candidate.required_quantity - fulfilled[candidate.slot_key]
                if missing > 0:
                    unresolved.append(
                        UnresolvedRequirement(
                            slot_key=candidate.slot_key,
                            category=candidate.category,
                            missing_quantity=missing,
                            importance=candidate.importance,
                            reason_code=reason_code,
                            reason="No active, room-compatible candidate can satisfy the missing quantity.",
                            repeat_group_id=candidate.repeat_group_id,
                        )
                    )
            continue

        score, _, chosen_item, reasons = chosen
        strong_threshold, weak_threshold = _thresholds(
            requirement, preferred_source
        )
        if score < weak_threshold:
            for candidate in group_requirements:
                missing = candidate.required_quantity - fulfilled[candidate.slot_key]
                if missing > 0:
                    unresolved.append(
                        UnresolvedRequirement(
                            slot_key=candidate.slot_key,
                            category=candidate.category,
                            missing_quantity=missing,
                            importance=candidate.importance,
                            reason_code="below_visual_threshold",
                            reason="Best eligible product is below the minimum visual compatibility threshold.",
                            repeat_group_id=candidate.repeat_group_id,
                            best_candidate_id=int(chosen_item["id"]),
                            best_candidate_score=score,
                        )
                    )
            continue

        quality: Literal["strong", "weak"] = (
            "strong" if score >= strong_threshold else "weak"
        )
        if quality == "weak":
            warnings.append(
                f"Slot '{requirement.slot_key}' uses weak visual match product {chosen_item['id']} (score {score})."
            )

        product_id = int(chosen_item["id"])
        if product_id not in final_by_id and len(final_by_id) >= max_unique_products:
            for candidate in group_requirements:
                missing = candidate.required_quantity - fulfilled[candidate.slot_key]
                if missing > 0:
                    unresolved.append(
                        UnresolvedRequirement(
                            slot_key=candidate.slot_key,
                            category=candidate.category,
                            missing_quantity=missing,
                            importance=candidate.importance,
                            reason_code="reference_limit",
                            reason="Unique product reference limit reached.",
                            repeat_group_id=candidate.repeat_group_id,
                        )
                    )
            continue

        if product_id not in final_by_id:
            if source == "template":
                inclusion_reason = (
                    "Best compatible source product resolved to a visible blueprint slot."
                )
            elif requirement.completeness_level and requirement.functional_zone:
                readable_zone = requirement.functional_zone.replace("_", " ")
                inclusion_reason = (
                    f"Added to complete the living-room {readable_zone} zone."
                )
            else:
                inclusion_reason = "Added only to satisfy a missing design requirement."
            final_by_id[product_id] = _prepare_final_item(
                chosen_item,
                source,
                0,
                was_included=source != "system",
                match_score=score,
                match_quality=quality,
                inclusion_reason=inclusion_reason,
            )
        if source == "template":
            assigned_preferred_ids.add(product_id)
        if requirement.repeat_group_id and requirement.same_product_required:
            repeat_owner[requirement.repeat_group_id] = product_id

        for candidate in group_requirements:
            missing = candidate.required_quantity - fulfilled[candidate.slot_key]
            if missing <= 0:
                continue
            final_item = final_by_id[product_id]
            final_item["quantity"] += missing
            if source == "system":
                final_item["completion_quantity"] += missing
                final_item["added_for_completion"] = True
            else:
                final_item["included_quantity"] += missing
                final_item["was_included"] = True
            allocate(
                candidate,
                product_id,
                missing,
                source,
                quality,
                score,
                reasons,
            )
            used_product_ids.add(product_id)

    selected_items = list(final_by_id.values())
    for item in selected_items:
        item["line_total"] = round(
            float(item.get("price") or 0) * int(item.get("quantity", 1)), 2
        )

    satisfaction = [
        SlotSatisfaction(
            slot_key=requirement.slot_key,
            category=requirement.category,
            required_quantity=requirement.required_quantity,
            fulfilled_quantity=min(
                fulfilled[requirement.slot_key], requirement.required_quantity
            ),
            missing_quantity=max(
                requirement.required_quantity - fulfilled[requirement.slot_key], 0
            ),
            fulfilled_by=fulfilled_by[requirement.slot_key],
            importance=requirement.importance,
            repeat_group_id=requirement.repeat_group_id,
            same_product_required=requirement.same_product_required,
        )
        for requirement in requirements
    ]

    return UnifiedSelectionResult(
        selected_items=selected_items,
        slot_satisfaction=satisfaction,
        assignments=assignments,
        unresolved_slots=unresolved,
        warnings=warnings,
        unassigned_preferred_candidate_ids=sorted(
            preferred_ids - assigned_preferred_ids
        ),
        catalog_signature=build_catalog_signature(all_catalog),
        requirements_signature=build_requirements_signature(requirements),
    )
