"""Backward-compatible entry point for deterministic template slot resolution.

The former implementation mixed source-product mappings, prose heuristics, and
an additional selector call. Blueprint slots now own quantity and repetition;
this module remains only so older internal imports fail safely into the single
authoritative resolver instead of reviving the obsolete pipeline.
"""

from app.models import RoomDimensions
from app.services.room_analysis_service import RoomAnalysis
from app.services.template_blueprint_service import TemplateBlueprint
from app.services.template_slot_resolver_service import resolve_template_slots


MAX_TEMPLATE_MODE_UNIQUE_PRODUCTS = 14


def complete_template_products_from_database(
    template: dict,
    blueprint: TemplateBlueprint,
    exact_template_items: list[dict],
    all_items: list[dict],
    room_analysis: RoomAnalysis,
    room_dimensions: RoomDimensions | None,
    room_type: str,
    requested_style: str | None,
    preferred_colors: list[str],
) -> tuple[list[dict], dict, list[dict]]:
    del template, room_analysis  # Kept in the compatibility signature.

    source_ids = {int(item["id"]) for item in exact_template_items}
    result = resolve_template_slots(
        blueprint=blueprint,
        source_candidates=exact_template_items,
        database_candidates=[
            item for item in all_items if int(item["id"]) not in source_ids
        ],
        room_dimensions=room_dimensions,
        room_type=room_type,
        requested_style=requested_style,
        preferred_colors=preferred_colors,
        max_unique_products=MAX_TEMPLATE_MODE_UNIQUE_PRODUCTS,
    )

    source_items = [
        item
        for item in result.selected_items
        if item.get("selection_role") == "template_selected"
    ]
    metadata = {
        "ai_selector_called": False,
        "resolver_version": result.resolver_version,
        "catalog_signature": result.catalog_signature,
        "requirements_signature": result.requirements_signature,
        "slot_satisfaction": [
            satisfaction.model_dump()
            for satisfaction in result.slot_satisfaction
        ],
        "slot_assignments": [
            assignment.model_dump() for assignment in result.assignments
        ],
        "unresolved_slots": [
            slot.model_dump() for slot in result.unresolved_slots
        ],
        "unassigned_source_candidate_ids": result.unassigned_source_candidate_ids,
        "warnings": result.warnings,
        "source_candidates_are_mandatory": False,
        "completion_scope": "unresolved_blueprint_slots_only",
    }
    return result.selected_items, metadata, source_items
