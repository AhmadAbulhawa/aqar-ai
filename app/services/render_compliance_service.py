"""Post-render visual compliance validation for immutable selections."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field

from app.services.room_analysis_service import encode_image_as_data_url


RENDER_COMPLIANCE_MODEL = "gpt-5.6-terra"
RENDER_COMPLIANCE_PROMPT_VERSION = "2026-07-18.3-functional-presentation-policy"
AUTOMATIC_PAID_RENDER_RETRY_ENABLED = False
CAMERA_VIEW_FAILURE_CODE = "camera_view_materially_changed"
CAMERA_VIEW_FAILURE_REASON = (
    "Camera-view preservation failure: the generated image materially changes "
    "the original camera viewpoint, perspective, field of view, framing, or crop."
)
ROOM_FINISH_FAILURE_CODE = "room_finish_material_changed"
ROOM_FINISH_FAILURE_REASON = (
    "Permanent room-finish preservation failure: the generated image materially "
    "changes an original floor, wall, ceiling, or fixed-trim material."
)

ComplianceEvidenceLevel = Literal[
    "OBSERVED_CONFIRMED",
    "OBSERVED_UNCERTAIN",
    "INFERRED",
    "NOT_VISIBLE",
]

_CATEGORY_ALIASES = {
    "wall_art": ("wall art", "artwork", "painting", "canvas"),
    "sofa": ("sofa", "sofas", "couch"),
    "coffee_table": ("coffee table",),
    "rug": ("rug",),
    "television": ("television", "tv"),
    "tv_stand": ("tv stand", "tv bench", "media console"),
}

_RELATIONSHIP_TERMS = {
    "facing_sofas": (
        "face-to-face",
        "face to face",
        "facing sofa",
        "sofas face",
        "sofas no longer face",
        "conversation arrangement",
        "television-facing layout",
        "tv-facing layout",
        "all seating",
        "side-by-side",
        "side by side",
        "l-shaped",
        "l shaped",
        "same direction",
        "not opposite",
        "no longer opposite",
    ),
    "centered_coffee_table": ("coffee table", "center table"),
    "grounding_rug": ("grounding rug", "selected rug", "rug is absent"),
}

_ABSENCE_TERMS = (
    "absent",
    "missing",
    "omitted",
    "not present",
    "not visible",
    "not rendered",
    "not shown",
    "not depicted",
    "not reproduced",
    "not included",
    "unfulfilled",
    "requirement was not met",
    "lack of",
    "lacks",
    "was not included",
    "is not included",
)

HARD_COMPLIANCE_FAILURES = {
    "door_accessible": (
        "door_blocked",
        "Door blockage: furniture obstructs the visible door opening, door swing area, or immediate entry path.",
    ),
    "windows_unblocked": (
        "window_blocked",
        "Window blockage: furniture obstructs a visible window or its required access and daylight area.",
    ),
    "circulation_usable": (
        "primary_circulation_blocked",
        "Primary circulation blockage: furniture obstructs or materially narrows the main entrance, hallway, or walking route.",
    ),
}


class RenderComplianceResult(BaseModel):
    status: Literal[
        "verified",
        "verified_with_warnings",
        "failed_compliance",
        "unverified",
    ]
    room_architecture_preserved: bool | None = None
    room_finish_materials_preserved: bool | None = None
    camera_view_preserved: bool | None = None
    template_composition_preserved: bool | None = None
    explicit_user_requirements_satisfied: bool | None = None
    selected_products_present: bool | None = None
    product_quantity_compliance: bool | None = None
    unselected_major_products_absent: bool | None = None
    product_identity_preserved: bool | None = None
    realistic_scale: bool | None = None
    door_accessible: bool | None = None
    windows_unblocked: bool | None = None
    circulation_usable: bool | None = None
    door_evidence: ComplianceEvidenceLevel | None = None
    window_evidence: ComplianceEvidenceLevel | None = None
    circulation_evidence: ComplianceEvidenceLevel | None = None
    no_major_furniture_overlap: bool | None = None
    functionally_plausible: bool | None = None
    violations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    hard_compliance_failure: bool = False
    hard_failure_codes: list[str] = Field(default_factory=list)
    failure_codes: list[str] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)
    ignored_unresolved_template_violations: list[str] = Field(
        default_factory=list
    )
    accepted_template_adaptations: list[str] = Field(default_factory=list)
    automatic_paid_retry_enabled: bool = AUTOMATIC_PAID_RENDER_RETRY_ENABLED
    automatic_retry_attempted: bool = False
    model_name: str | None = None
    prompt_version: str = RENDER_COMPLIANCE_PROMPT_VERSION


class _VisualComplianceAssessment(BaseModel):
    room_architecture_preserved: bool
    room_finish_materials_preserved: bool
    camera_view_preserved: bool
    template_composition_preserved: bool | None = None
    explicit_user_requirements_satisfied: bool | None = None
    selected_products_present: bool
    product_quantity_compliance: bool
    unselected_major_products_absent: bool
    product_identity_preserved: bool
    realistic_scale: bool
    door_accessible: bool
    windows_unblocked: bool
    circulation_usable: bool
    door_evidence: ComplianceEvidenceLevel = "OBSERVED_UNCERTAIN"
    window_evidence: ComplianceEvidenceLevel = "OBSERVED_UNCERTAIN"
    circulation_evidence: ComplianceEvidenceLevel = "OBSERVED_UNCERTAIN"
    door_obstruction_visually_obvious: bool = False
    window_obstruction_visually_obvious: bool = False
    circulation_obstruction_visually_obvious: bool = False
    no_major_furniture_overlap: bool
    functionally_plausible: bool
    violations: list[str] = Field(default_factory=list, max_length=16)
    warnings: list[str] = Field(default_factory=list, max_length=16)


def _unique_messages(messages: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for message in messages:
        normalized = str(message).strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


_PERMITTED_PRESENTATION_TERMS = (
    "sofa back faces the camera",
    "sofa back facing the camera",
    "sofa side faces the camera",
    "sofa side facing the camera",
    "shown from the back",
    "shown from the side",
    "not presented frontally",
    "partial visual overlap",
    "partially visually overlaps",
    "camera-view overlap",
    "occluded from this viewpoint",
    "differs from the template",
    "different from the template",
    "template layout differs",
    "catalog lineup",
    "showroom lineup",
)
_PHYSICAL_COLLISION_TERMS = (
    "physical collision",
    "physically collides",
    "impossible physical overlap",
    "interpenetrat",
    "occupies the same physical space",
    "passes through",
    "embedded in",
)


def _is_permitted_presentation_variation(message: str) -> bool:
    normalized = " ".join(str(message or "").casefold().split())
    return (
        any(term in normalized for term in _PERMITTED_PRESENTATION_TERMS)
        and not any(term in normalized for term in _PHYSICAL_COLLISION_TERMS)
    )


def _apply_functional_presentation_policy(
    assessment: _VisualComplianceAssessment,
) -> _VisualComplianceAssessment:
    """Prevent camera-presentation preferences from becoming failures."""

    accepted = [
        message
        for message in assessment.violations
        if _is_permitted_presentation_variation(message)
    ]
    if not accepted:
        return assessment

    accepted_text = " ".join(accepted).casefold()
    updates: dict[str, bool | list[str]] = {
        "violations": [
            message for message in assessment.violations if message not in accepted
        ],
        "warnings": _unique_messages(
            [
                *assessment.warnings,
                *(
                    "Accepted functional presentation variation: " + message
                    for message in accepted
                ),
            ]
        ),
    }
    if assessment.camera_view_preserved is False and any(
        term in accepted_text
        for term in ("back", "side", "frontally", "camera-view")
    ):
        updates["camera_view_preserved"] = True
    if assessment.no_major_furniture_overlap is False and any(
        term in accepted_text
        for term in ("visual overlap", "visually overlaps", "camera-view", "occluded")
    ):
        updates["no_major_furniture_overlap"] = True
    if assessment.functionally_plausible is False and len(accepted) == len(
        assessment.violations
    ):
        updates["functionally_plausible"] = True
    return assessment.model_copy(update=updates)


_SAFETY_EVIDENCE_RULES = {
    "door_accessible": (
        "door_evidence",
        "door_obstruction_visually_obvious",
        "door_access_not_fully_verifiable",
        ("door", "doorway", "threshold", "swing", "entrance"),
    ),
    "windows_unblocked": (
        "window_evidence",
        "window_obstruction_visually_obvious",
        "window_access_not_fully_verifiable",
        ("window", "glazed", "glazing", "balcony"),
    ),
    "circulation_usable": (
        "circulation_evidence",
        "circulation_obstruction_visually_obvious",
        "circulation_not_fully_verifiable",
        ("circulation", "route", "hallway", "walking", "entry"),
    ),
}


def _apply_safety_evidence_gate(
    assessment: _VisualComplianceAssessment,
) -> tuple[_VisualComplianceAssessment, dict[str, bool | None]]:
    """Suppress guessed safety failures while retaining direct visible ones."""

    reported: dict[str, bool | None] = {}
    updates: dict[str, bool | list[str]] = {}
    suppressed: list[tuple[str, tuple[str, ...]]] = []
    evidence_warnings: list[str] = []

    for field_name, (
        evidence_field,
        obvious_field,
        warning_code,
        tokens,
    ) in _SAFETY_EVIDENCE_RULES.items():
        value = getattr(assessment, field_name)
        reported[field_name] = value
        if value is not False:
            continue
        evidence = getattr(assessment, evidence_field)
        visually_obvious = bool(getattr(assessment, obvious_field))
        hard_evidence = evidence == "OBSERVED_CONFIRMED" or (
            evidence == "OBSERVED_UNCERTAIN" and visually_obvious
        )
        if hard_evidence:
            continue
        reported[field_name] = None
        updates[field_name] = True
        suppressed.append((warning_code, tokens))
        evidence_warnings.append(
            f"{warning_code}: the potential obstruction is {evidence.lower()} "
            "and lacks direct high-confidence visual evidence."
        )

    if not suppressed:
        return assessment, reported

    retained_violations: list[str] = []
    moved_to_warnings: list[str] = []
    for message in assessment.violations:
        normalized = message.casefold()
        matching_code = next(
            (
                warning_code
                for warning_code, tokens in suppressed
                if any(token in normalized for token in tokens)
            ),
            None,
        )
        if matching_code:
            moved_to_warnings.append(f"{matching_code}: {message}")
        else:
            retained_violations.append(message)

    updates["violations"] = retained_violations
    updates["warnings"] = _unique_messages(
        [*assessment.warnings, *evidence_warnings, *moved_to_warnings]
    )
    return assessment.model_copy(update=updates), reported


def _slot_payload(slot: dict, *, fulfilled_quantity: int) -> dict:
    required_quantity = max(int(slot.get("quantity") or 1), 1)
    return {
        "slot_key": str(slot.get("slot_key") or "").strip(),
        "category": str(slot.get("category") or "").strip(),
        "importance": slot.get("importance"),
        "required_quantity": required_quantity,
        "fulfilled_quantity": min(max(fulfilled_quantity, 0), required_quantity),
        "missing_quantity": max(required_quantity - fulfilled_quantity, 0),
    }


def build_template_compliance_scope(manifest: dict) -> dict:
    """Resolve which template requirements are enforceable after inventory lock.

    A template image may contain products that could not be resolved from the
    catalog. Those source-only objects are intentionally excluded. Selected
    inventory remains enforceable independently of template-slot resolution.
    """

    selected_products = list(manifest.get("selected_products") or [])
    locked_blueprint = (
        (manifest.get("design_intent") or {}).get("locked_blueprint") or {}
    )
    product_slots = list(locked_blueprint.get("product_slots") or [])
    selected_categories = {
        str(product.get("category") or "").strip()
        for product in selected_products
        if product.get("category")
    }

    resolved_slots: list[dict] = []
    unresolved_slots: list[dict] = []
    for slot in product_slots:
        slot_key = str(slot.get("slot_key") or "").strip()
        category = str(slot.get("category") or "").strip()
        directly_assigned = [
            product
            for product in selected_products
            if slot_key
            and slot_key
            in {
                str(key).strip()
                for key in (product.get("slot_keys") or [])
            }
        ]
        category_assigned = [
            product
            for product in selected_products
            if category
            and not (product.get("slot_keys") or [])
            and str(product.get("category") or "").strip() == category
        ]
        assigned = directly_assigned or category_assigned
        derived_fulfilled = sum(
            max(int(product.get("quantity") or 1), 1) for product in assigned
        )
        explicit_fulfilled = max(int(slot.get("fulfilled_quantity") or 0), 0)
        fulfilled_quantity = max(derived_fulfilled, explicit_fulfilled)
        payload = _slot_payload(slot, fulfilled_quantity=fulfilled_quantity)
        payload["selected_product_ids"] = sorted(
            {int(product["id"]) for product in assigned if product.get("id") is not None}
        )
        if payload["fulfilled_quantity"] > 0:
            resolved_slots.append(payload)
        else:
            payload["enforceable"] = False
            payload["reason"] = (
                "No immutable selected product fulfills this template slot or category."
            )
            unresolved_slots.append(payload)

    layout_type = str(locked_blueprint.get("layout_type") or "").strip()
    relationships: list[dict] = []
    if "facing_sofas" in layout_type.casefold() and "sofa" in selected_categories:
        relationships.append(
            {
                "code": "facing_sofas",
                "requirement": (
                    "Prefer the selected face-to-face sofa conversation arrangement "
                    "when safely feasible."
                ),
                "precedence": "preferred_when_safely_feasible",
                "allowed_safe_adaptations": [
                    "perpendicular",
                    "L-shaped",
                    "angled",
                    "partially_offset",
                    "asymmetric",
                ],
                "higher_priority_constraints": [
                    "room_architecture",
                    "door_clearance",
                    "window_access",
                    "primary_circulation",
                    "safe_entry_path",
                    "functional_usability",
                    "comfortable_spacing",
                ],
            }
        )
    if "coffee_table" in selected_categories:
        relationships.append(
            {
                "code": "centered_coffee_table",
                "requirement": (
                    "Keep the selected coffee table naturally centered within the "
                    "primary conversation group."
                ),
                "precedence": "preferred_when_safely_feasible",
            }
        )
    if "rug" in selected_categories:
        relationships.append(
            {
                "code": "grounding_rug",
                "requirement": "Use the selected rug to ground the primary seating group.",
                "precedence": "preferred_when_safely_feasible",
            }
        )

    return {
        "selected_product_ids": sorted(
            int(product["id"])
            for product in selected_products
            if product.get("id") is not None
        ),
        "selected_categories": sorted(selected_categories),
        "layout_type": layout_type or None,
        "resolved_template_slots": resolved_slots,
        "unresolved_template_slots": unresolved_slots,
        "enforceable_template_relationships": relationships,
        "policy": (
            "Unresolved template slots are source-only inspiration: their absence "
            "is not a violation and GPT Image must not invent them."
        ),
        "precedence_policy": (
            "Architecture, door/window access, primary circulation, safe entry, "
            "and functional spacing outrank template topology. Preserve exact "
            "inventory; adapt relationships instead of removing products."
        ),
    }


def _scope_term_variants(slot: dict) -> set[str]:
    slot_key = str(slot.get("slot_key") or "").casefold().strip()
    category = str(slot.get("category") or "").casefold().strip()
    variants = {
        slot_key,
        slot_key.replace("_", " "),
        category,
        category.replace("_", " "),
    }
    variants.update(_CATEGORY_ALIASES.get(category, ()))
    return {variant for variant in variants if len(variant) >= 3}


def _mentions_enforceable_relationship(message: str, scope: dict) -> bool:
    normalized = message.casefold()
    for relationship in scope.get("enforceable_template_relationships") or []:
        code = str(relationship.get("code") or "")
        if any(term in normalized for term in _RELATIONSHIP_TERMS.get(code, ())):
            return True
    return False


def _mentions_adaptable_relationship(message: str, scope: dict) -> bool:
    normalized = message.casefold()
    for relationship in scope.get("enforceable_template_relationships") or []:
        if relationship.get("precedence") != "preferred_when_safely_feasible":
            continue
        code = str(relationship.get("code") or "")
        if any(term in normalized for term in _RELATIONSHIP_TERMS.get(code, ())):
            return True
    return False


def _is_default_template_layout_message(message: str) -> bool:
    normalized = message.casefold()
    return any(
        term in normalized
        for term in (
            "template topology",
            "template composition",
            "face-to-face",
            "face to face",
            "facing sofa",
            "l-shaped",
            "l shaped",
            "perpendicular sofa",
            "angled sofa",
            "offset sofa",
            "focal hierarchy",
        )
    )


def _claims_selected_product_absence(message: str, selected_products: list[dict]) -> bool:
    normalized = message.casefold()
    for product in selected_products:
        category = str(product.get("category") or "").casefold().strip()
        aliases = set(_CATEGORY_ALIASES.get(category, ()))
        name = str(product.get("name") or "").casefold().strip()
        if name:
            aliases.add(name)
        product_id = product.get("id")
        if product_id is not None:
            aliases.update({f"product {product_id}", f"id {product_id}"})
        for alias in aliases:
            escaped = re.escape(alias)
            absence = "|".join(re.escape(term) for term in _ABSENCE_TERMS)
            if re.search(
                rf"(?:{escaped}).{{0,32}}(?:{absence})|(?:{absence}).{{0,32}}(?:{escaped})",
                normalized,
            ):
                return True
    return False


def _is_unresolved_slot_absence(
    message: str,
    *,
    scope: dict,
    selected_products: list[dict],
) -> bool:
    normalized = message.casefold()
    unresolved_terms = {
        term
        for slot in (scope.get("unresolved_template_slots") or [])
        for term in _scope_term_variants(slot)
    }
    return (
        bool(unresolved_terms)
        and any(term in normalized for term in unresolved_terms)
        and any(term in normalized for term in _ABSENCE_TERMS)
        and not _mentions_enforceable_relationship(message, scope)
        and not _claims_selected_product_absence(message, selected_products)
    )


def _apply_template_scope(
    assessment: _VisualComplianceAssessment,
    *,
    template_compliance_scope: dict | None,
    selected_products: list[dict],
) -> tuple[_VisualComplianceAssessment, list[str], list[str]]:
    if not template_compliance_scope:
        return assessment, [], []

    ignored_violations = [
        message
        for message in assessment.violations
        if _is_unresolved_slot_absence(
            message,
            scope=template_compliance_scope,
            selected_products=selected_products,
        )
    ]
    ignored_warnings = [
        message
        for message in assessment.warnings
        if _is_unresolved_slot_absence(
            message,
            scope=template_compliance_scope,
            selected_products=selected_products,
        )
    ]
    scope_filtered_violations = [
        message for message in assessment.violations if message not in ignored_violations
    ]
    remaining_warnings = [
        message for message in assessment.warnings if message not in ignored_warnings
    ]
    higher_priority_checks_pass = all(
        [
            assessment.room_architecture_preserved,
            assessment.room_finish_materials_preserved,
            assessment.camera_view_preserved,
            assessment.selected_products_present,
            assessment.product_quantity_compliance,
            assessment.unselected_major_products_absent,
            assessment.product_identity_preserved,
            assessment.door_accessible,
            assessment.windows_unblocked,
            assessment.circulation_usable,
            assessment.no_major_furniture_overlap,
            assessment.functionally_plausible,
        ]
    )
    accepted_adaptations = (
        [
            message
            for message in scope_filtered_violations
            if (
                _mentions_adaptable_relationship(
                    message, template_compliance_scope
                )
                or _is_default_template_layout_message(message)
            )
        ]
        if higher_priority_checks_pass
        else []
    )
    remaining_violations = [
        message
        for message in scope_filtered_violations
        if message not in accepted_adaptations
    ]
    relationship_failure_remains = any(
        _mentions_enforceable_relationship(message, template_compliance_scope)
        for message in remaining_violations
    )
    if accepted_adaptations:
        remaining_warnings = _unique_messages(
            [
                *remaining_warnings,
                *(
                    "Accepted safe template-topology adaptation: " + message
                    for message in accepted_adaptations
                ),
            ]
        )
    effective_template_preserved = assessment.template_composition_preserved
    if effective_template_preserved is False and not relationship_failure_remains:
        if not accepted_adaptations:
            effective_template_preserved = True

    ignored = _unique_messages([*ignored_violations, *ignored_warnings])
    if (
        not ignored
        and not accepted_adaptations
        and effective_template_preserved
        == assessment.template_composition_preserved
    ):
        return assessment, [], []

    return (
        assessment.model_copy(
            update={
                "template_composition_preserved": effective_template_preserved,
                "violations": remaining_violations,
                "warnings": remaining_warnings,
            }
        ),
        ignored,
        accepted_adaptations,
    )


def build_compliance_result(
    assessment: _VisualComplianceAssessment,
    *,
    template_used: bool,
    template_compliance_scope: dict | None = None,
    selected_products: list[dict] | None = None,
    enforce_user_requested_relationships: bool = False,
    explicit_user_requirements_present: bool = False,
) -> RenderComplianceResult:
    """Classify architectural hard failures without another model call."""

    assessment = _apply_functional_presentation_policy(assessment)
    assessment, reported_safety = _apply_safety_evidence_gate(assessment)
    assessment, ignored_unresolved, accepted_adaptations = _apply_template_scope(
        assessment,
        template_compliance_scope=template_compliance_scope,
        selected_products=selected_products or [],
    )

    checks = [
        assessment.room_architecture_preserved,
        assessment.room_finish_materials_preserved,
        assessment.camera_view_preserved,
        assessment.selected_products_present,
        assessment.product_quantity_compliance,
        assessment.unselected_major_products_absent,
        assessment.product_identity_preserved,
        assessment.realistic_scale,
        assessment.door_accessible,
        assessment.windows_unblocked,
        assessment.circulation_usable,
        assessment.no_major_furniture_overlap,
        assessment.functionally_plausible,
    ]
    if template_used and enforce_user_requested_relationships:
        checks.append(
            assessment.template_composition_preserved is True
            or bool(accepted_adaptations)
        )
    if explicit_user_requirements_present:
        checks.append(assessment.explicit_user_requirements_satisfied is True)

    hard_failure_codes: list[str] = []
    hard_failure_reasons: list[str] = []
    for field_name, (code, reason) in HARD_COMPLIANCE_FAILURES.items():
        if getattr(assessment, field_name) is False:
            hard_failure_codes.append(code)
            hard_failure_reasons.append(reason)

    failure_codes = list(hard_failure_codes)
    camera_failure_reasons: list[str] = []
    if assessment.camera_view_preserved is False:
        failure_codes.append(CAMERA_VIEW_FAILURE_CODE)
        camera_failure_reasons.append(CAMERA_VIEW_FAILURE_REASON)
    finish_failure_reasons: list[str] = []
    if assessment.room_finish_materials_preserved is False:
        failure_codes.append(ROOM_FINISH_FAILURE_CODE)
        finish_failure_reasons.append(ROOM_FINISH_FAILURE_REASON)

    status = (
        "failed_compliance"
        if not all(checks)
        else "verified_with_warnings"
        if assessment.warnings
        else "verified"
    )
    failure_reasons = (
        _unique_messages(
            [
                *hard_failure_reasons,
                *camera_failure_reasons,
                *finish_failure_reasons,
                *assessment.violations,
            ]
        )
        if status == "failed_compliance"
        else []
    )
    if status == "failed_compliance" and not failure_reasons:
        failure_reasons = [
            "Post-render compliance failed one or more required visual checks."
        ]

    return RenderComplianceResult(
        status=status,
        room_architecture_preserved=assessment.room_architecture_preserved,
        room_finish_materials_preserved=(
            assessment.room_finish_materials_preserved
        ),
        camera_view_preserved=assessment.camera_view_preserved,
        template_composition_preserved=assessment.template_composition_preserved,
        explicit_user_requirements_satisfied=(
            assessment.explicit_user_requirements_satisfied
        ),
        selected_products_present=assessment.selected_products_present,
        product_quantity_compliance=assessment.product_quantity_compliance,
        unselected_major_products_absent=assessment.unselected_major_products_absent,
        product_identity_preserved=assessment.product_identity_preserved,
        realistic_scale=assessment.realistic_scale,
        door_accessible=reported_safety["door_accessible"],
        windows_unblocked=reported_safety["windows_unblocked"],
        circulation_usable=reported_safety["circulation_usable"],
        door_evidence=assessment.door_evidence,
        window_evidence=assessment.window_evidence,
        circulation_evidence=assessment.circulation_evidence,
        no_major_furniture_overlap=assessment.no_major_furniture_overlap,
        functionally_plausible=assessment.functionally_plausible,
        violations=assessment.violations,
        warnings=assessment.warnings,
        hard_compliance_failure=bool(hard_failure_codes),
        hard_failure_codes=hard_failure_codes,
        failure_codes=failure_codes,
        failure_reasons=failure_reasons,
        ignored_unresolved_template_violations=ignored_unresolved,
        accepted_template_adaptations=accepted_adaptations,
        automatic_paid_retry_enabled=AUTOMATIC_PAID_RENDER_RETRY_ENABLED,
        automatic_retry_attempted=False,
        model_name=RENDER_COMPLIANCE_MODEL,
    )


def build_render_compliance_prompt(manifest: dict) -> str:
    """Build the visual audit policy without calling OpenAI or a render endpoint."""

    compliance_contract = {
        "immutable_selected_inventory": manifest.get("selected_products") or [],
        "explicit_user_requirements": (
            manifest.get("explicit_user_requirements") or []
        ),
        "hard_constraints": manifest.get("hard_constraints") or [],
        "architectural_constraints": manifest.get("architectural_constraints") or {},
    }
    policy = """
Image 1 is the generated result. Image 2 is the authoritative original room.
No source-template image or template floor plan is part of this review. Check
only original-room architecture, permanent finishes, evidenced access and
circulation constraints, the original camera viewpoint/framing, explicit user
requirements, and the immutable selected inventory.

TEMPLATE AND FURNITURE-PRESENTATION POLICY

Do not evaluate template topology, sofa relationships, furniture orientation,
named-product placement, symmetry, zones, camera composition, or focal hierarchy
unless the same constraint appears under explicit_user_requirements. A layout
differing from a selected template image is never a violation by itself.

The sofa back facing the camera, the sofa side facing the camera, main seating
not being presented frontally, natural partial visual overlap or occlusion from
the camera viewpoint, and a composition that is not a catalog/showroom lineup
are valid. They are not violations. Do not use furniture orientation to judge
camera_view_preserved. Do not mark a selected product missing merely because it
is seen from its back or side or is naturally partially occluded.

Set no_major_furniture_overlap to false only for a directly evidenced impossible
physical collision, interpenetration, or two major products occupying the same
physical volume. Natural perspective overlap in the two-dimensional camera view
is allowed and must keep no_major_furniture_overlap true.

EVIDENCE-BASED ARCHITECTURE AND ACCESS POLICY

Classify each access finding as OBSERVED_CONFIRMED, OBSERVED_UNCERTAIN, INFERRED,
or NOT_VISIBLE. Only OBSERVED_CONFIRMED evidence normally supports a hard failure.
OBSERVED_UNCERTAIN supports one only when the obstruction is directly visible and
visually obvious despite uncertain measurement. INFERRED and NOT_VISIBLE findings
belong in warnings and must not create hard geometry. Treat door blockage, window
blockage, and primary circulation blockage as hard failures only under that
evidence rule.

Set door_accessible to false only when an object physically blocks or materially
narrows a visible door plane, opening width, threshold, genuinely required
landing, evidenced swing/operating area, or immediate entry path. When the
entrance is outside the camera view, mark door and route evidence NOT_VISIBLE;
do not guess its location, claim a route from it is blocked, or require a
continuous path from an unseen doorway.

Set windows_unblocked to false only when major furniture materially covers the
visible aperture, clearly prevents normal window operation, obstructs an operable
balcony/walk-through glazed opening, materially blocks daylight or necessary
physical access, or makes the window effectively unusable. A low sofa merely
below, beside, or near a window is allowed when it does not cover the aperture or
prevent operation. Do not require an 85 cm cleaning corridor along every window
wall, and do not treat cleaning convenience as primary circulation. Put uncertain
sill, opening-mechanism, or access findings in warnings.

Set circulation_usable to false only for a materially obstructed primary route
that is directly visible or authoritatively confirmed. An actual obstruction,
pinch point, or material narrowing of such a route may fail compliance. Require a
continuous body-width empty route only for that confirmed primary route. An
inferred camera-side entry or inferred window-cleaning route cannot support
primary_circulation_blocked. Do not fail a design for using open room footprint
outside the clearance genuinely required for evidenced access and movement.

ARCHITECTURE, CAMERA, AND IMMUTABLE INVENTORY

Set room_architecture_preserved from walls, openings, ceiling form, room envelope,
and fixed geometry. Set room_finish_materials_preserved to false only for a
material change to the original floor, wall, ceiling finish/form, or fixed trim;
minor lighting or color-rendering variation is not a finish change.

Set camera_view_preserved to false only for a substantially different camera wall
or corner, major rotation or reverse angle, significant zoom/crop that removes
important original architecture, materially changed perspective, or relocated
doors/windows caused by viewpoint change. A few pixels of framing difference,
small perspective drift, minor crop variation, or lighting/lens-like visual
differences are allowed. Furniture showing its back or side or having a different
orientation is not a camera-view change.

Check exact selected product identities and exact physical quantities, missing
selected major items, invented unselected major furniture, recognizable identity,
severe unrealistic scale, impossible physical collision, preserved architecture,
usable doors and windows, confirmed circulation, and functional plausibility.

Product construction is part of identity. Set product_identity_preserved to false
for a directly evidenced material change to a product's recognizable silhouette,
frame topology, module/section count, handedness, arms, backrests, cushion divisions,
legs/base, lamp stem/head/base, proportions, material, finish, color, or distinctive
details. Set product_quantity_compliance to false when repeated units were fused or
merged into one larger object. Do not demand pixel identity, invisible details, or
exact measurements from one camera view, but do not excuse material construction
changes as style or perspective variation. Put genuine uncertainty in warnings.

Hard failures must be limited to directly evidenced material problems.
""".strip()
    return policy + "\n\n" + json.dumps(
        compliance_contract,
        ensure_ascii=False,
        indent=2,
    )


def validate_render_compliance(
    *,
    generated_image_path: Path,
    room_image_path: Path,
    template_image_path: Path | None,
    manifest: dict,
) -> RenderComplianceResult:
    """Perform one uncertainty-aware post-render check.

    This does not claim exact geometry or perfect product recognition from one
    view. A missing API key or vision failure remains explicitly unverified.
    """

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return RenderComplianceResult(
            status="unverified",
            warnings=["OPENAI_API_KEY is unavailable for visual compliance validation."],
        )

    del template_image_path
    template_used = False
    prompt = build_render_compliance_prompt(manifest)
    content = [
        {"type": "input_text", "text": prompt},
        {
            "type": "input_image",
            "image_url": encode_image_as_data_url(generated_image_path),
        },
        {
            "type": "input_image",
            "image_url": encode_image_as_data_url(room_image_path),
        },
    ]
    try:
        response = OpenAI(api_key=api_key).responses.parse(
            model=RENDER_COMPLIANCE_MODEL,
            input=[{"role": "user", "content": content}],
            text_format=_VisualComplianceAssessment,
        )
        assessment = response.output_parsed
        if assessment is None:
            raise ValueError("Compliance model returned no parsed assessment.")
        return build_compliance_result(
            assessment,
            template_used=template_used,
            selected_products=list(manifest.get("selected_products") or []),
            explicit_user_requirements_present=bool(
                manifest.get("explicit_user_requirements") or []
            ),
        )
    except Exception as exc:
        return RenderComplianceResult(
            status="unverified",
            warnings=[f"Visual compliance validation failed: {exc}"],
            model_name=RENDER_COMPLIANCE_MODEL,
        )
