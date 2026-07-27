"""Legacy deterministic coordinate-plan validation and compatibility models.

The active pre-render pipeline uses composition_fit_service for broad physical
plausibility. Coordinate-derived aesthetic/alignment findings in this module
do not gate new immutable selections or GPT Image 2 renders.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.models import RoomDimensions
from app.services.architectural_constraint_service import (
    ArchitecturalConstraintMap,
    ArchitecturalZone,
    GeometryConfidence,
    SpatialRect,
)

if TYPE_CHECKING:
    from app.services.placement_planner_service import PlacementPlan, ProductPlacement


LAYOUT_QUALITY_VERSION = "2026-07-15.3"


REPLAN_REQUIRED_WARNING_CODES = frozenset({
    "unintended_symmetry_drift",
    "central_zone_alignment_drift",
    "rug_not_anchoring_seating",
    "rug_not_anchoring_table",
    "rug_table_alignment_drift",
})


# A serious warning must have a measurable effect on at least one score. The
# mapping also makes each penalty explainable instead of relying on warning
# count alone.
SERIOUS_WARNING_SCORE_PENALTIES: dict[str, dict[str, int]] = {
    "window_access_constrained": {"functional": 6},
    "circulation_below_target": {"circulation": 8},
    "coffee_table_spacing_tight": {"comfort": 8},
    "coffee_table_too_far": {"functional": 5, "comfort": 8},
    "coffee_table_seating_scale_outside_target": {
        "functional": 6,
        "visual": 8,
        "comfort": 8,
    },
    "isolated_committed_side_table": {"functional": 8, "comfort": 5},
    "visual_weight_imbalance": {"visual": 12},
    "dominant_product_scale": {"visual": 10, "density": 6},
    "isolated_seating_relationship": {
        "functional": 10,
        "visual": 8,
        "comfort": 10,
    },
    "unintended_symmetry_drift": {"functional": 8, "visual": 18},
    "central_zone_alignment_drift": {"functional": 10, "visual": 18},
    "rug_not_anchoring_seating": {"functional": 12, "visual": 16},
    "rug_not_anchoring_table": {"functional": 12, "visual": 16},
    "rug_table_alignment_drift": {"functional": 10, "visual": 14},
    "rug_seating_scale_outside_target": {"functional": 8, "visual": 12},
    "rug_table_scale_tight": {"functional": 8, "visual": 10},
    "oppressive_density_risk": {"comfort": 6, "density": 12},
}


class LayoutQualityStatus(StrEnum):
    VALID = "valid"
    VALID_WITH_WARNINGS = "valid_with_warnings"
    INVALID = "invalid"
    UNCERTAIN_GEOMETRY = "uncertain_geometry"


class LayoutQualityResult(BaseModel):
    version: str = LAYOUT_QUALITY_VERSION
    status: LayoutQualityStatus
    functional_score: int = Field(ge=0, le=100)
    circulation_score: int = Field(ge=0, le=100)
    visual_balance_score: int = Field(ge=0, le=100)
    comfort_score: int = Field(ge=0, le=100)
    density_score: int = Field(ge=0, le=100)
    violations: list[dict] = Field(default_factory=list)
    warnings: list[dict] = Field(default_factory=list)
    recommended_changes: list[dict] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)


class LayoutSelectionReduction(BaseModel):
    selected_items: list[dict] = Field(default_factory=list)
    removed_items: list[dict] = Field(default_factory=list)
    hard_conflict: bool = False
    reason: str


NON_FLOOR_CATEGORIES = {
    "rug",
    "wall_art",
    "wall_shelf",
    "mirror",
    "television",
}

TALL_WINDOW_BLOCKERS = {
    "storage_cabinet",
    "bookshelf",
    "wardrobe",
    "wall_cabinet",
    "refrigerator",
}

VISUAL_WEIGHTS = {
    "sofa": 5.0,
    "wardrobe": 5.0,
    "storage_cabinet": 4.0,
    "bookshelf": 4.0,
    "sideboard": 3.5,
    "tv_stand": 3.0,
    "armchair": 3.0,
    "coffee_table": 2.0,
    "dining_table": 4.0,
    "desk": 3.0,
    "side_table": 1.0,
    "floor_lamp": 0.8,
}


def _intersection(first: SpatialRect, second: SpatialRect) -> tuple[float, float]:
    overlap_x = max(
        0.0,
        min(first.right_cm, second.right_cm) - max(first.x_cm, second.x_cm),
    )
    overlap_y = max(
        0.0,
        min(first.far_cm, second.far_cm) - max(first.y_cm, second.y_cm),
    )
    return overlap_x, overlap_y


def _intersects(first: SpatialRect, second: SpatialRect) -> bool:
    overlap_x, overlap_y = _intersection(first, second)
    return overlap_x > 0 and overlap_y > 0


def _rect_gap(first: SpatialRect, second: SpatialRect) -> float:
    gap_x = max(
        second.x_cm - first.right_cm,
        first.x_cm - second.right_cm,
        0.0,
    )
    gap_y = max(
        second.y_cm - first.far_cm,
        first.y_cm - second.far_cm,
        0.0,
    )
    return math.hypot(gap_x, gap_y)


def _placement_rect(placement: "ProductPlacement") -> SpatialRect | None:
    footprint = getattr(placement, "footprint", None)
    if footprint is None:
        return None
    if isinstance(footprint, SpatialRect):
        return footprint
    return SpatialRect.model_validate(footprint)


def _unit_payload(placement: "ProductPlacement") -> dict:
    return {
        "product_id": int(placement.product_id),
        "unit_index": int(placement.unit_index),
    }


def _rect_center(rect: SpatialRect) -> tuple[float, float]:
    return (
        rect.x_cm + rect.width_cm / 2,
        rect.y_cm + rect.depth_cm / 2,
    )


def _removable_product_ids(
    placements: list["ProductPlacement"],
    selected_by_id: dict[int, dict],
) -> list[int]:
    return sorted({
        int(placement.product_id)
        for placement in placements
        if (
            selected_by_id.get(int(placement.product_id), {}).get(
                "selection_origin"
            ) == "system"
            and str(
                selected_by_id.get(int(placement.product_id), {}).get(
                    "requirement_importance", ""
                )
            ) != "essential"
        )
    })


def _add_violation(
    violations: list[dict],
    recommended_changes: list[dict],
    *,
    code: str,
    message: str,
    placements: list["ProductPlacement"] | None = None,
    zone: ArchitecturalZone | None = None,
    recommendation: str,
    removable_product_ids: list[int] | None = None,
    **details,
) -> None:
    product_units = [_unit_payload(item) for item in placements or []]
    payload = {
        "code": code,
        "message": message,
        "product_units": product_units,
        "product_ids": sorted({item["product_id"] for item in product_units}),
        **details,
    }
    if zone is not None:
        payload["zone_id"] = zone.zone_id
        payload["zone_type"] = zone.zone_type
        payload["zone_confidence"] = zone.confidence.value
    violations.append(payload)
    recommended_changes.append(
        {
            "code": code,
            "action": recommendation,
            "product_ids": payload["product_ids"],
            "removable_product_ids": sorted(set(removable_product_ids or [])),
        }
    )


def _add_warning(
    warnings: list[dict],
    recommended_changes: list[dict],
    *,
    code: str,
    message: str,
    placements: list["ProductPlacement"] | None = None,
    recommendation: str | None = None,
    severity: str = "advisory",
    **details,
) -> None:
    product_units = [_unit_payload(item) for item in placements or []]
    warnings.append(
        {
            "code": code,
            "message": message,
            "product_units": product_units,
            "product_ids": sorted({item["product_id"] for item in product_units}),
            "severity": severity,
            **details,
        }
    )
    if recommendation:
        recommended_changes.append(
            {
                "code": code,
                "action": recommendation,
                "product_ids": sorted(
                    {item["product_id"] for item in product_units}
                ),
                "removable_product_ids": [],
            }
        )


def layout_requires_replan(
    layout_quality: LayoutQualityResult | dict,
) -> bool:
    """Return True when major relational/alignment drift cannot be locked."""

    quality = (
        layout_quality
        if isinstance(layout_quality, LayoutQualityResult)
        else LayoutQualityResult.model_validate(layout_quality)
    )
    return any(
        warning.get("code") in REPLAN_REQUIRED_WARNING_CODES
        for warning in quality.warnings
    )


def validate_layout_quality(
    *,
    plan: "PlacementPlan",
    selected_items: list[dict],
    architectural_constraints: ArchitecturalConstraintMap,
    room_dimensions: RoomDimensions | None = None,
    composition_validation: dict | BaseModel | None = None,
) -> LayoutQualityResult:
    """Run deterministic checks before a draft selection may be locked."""

    selected_by_id = {int(item["id"]): item for item in selected_items}
    dimensions = room_dimensions or architectural_constraints.room_dimensions_cm
    violations: list[dict] = []
    warnings: list[dict] = []
    recommended_changes: list[dict] = []
    assumptions = [
        "Main circulation targets 75-90 cm where geometry allows; below 60 cm is treated as unsafe.",
        "Sofa-to-coffee-table comfort targets 35-50 cm; below 30 cm is unsafe.",
        "Qualitative architectural observations without bounds remain uncertain and are never treated as exact geometry.",
    ]

    placements = list(plan.placements)
    rect_by_unit: dict[tuple[int, int], SpatialRect] = {}
    unknown_geometry: list[dict] = []
    for placement in placements:
        rect = _placement_rect(placement)
        confidence = getattr(
            placement, "geometry_confidence", GeometryConfidence.UNCERTAIN
        )
        if rect is None or confidence == GeometryConfidence.UNCERTAIN:
            unknown_geometry.append(_unit_payload(placement))
        if rect is not None:
            rect_by_unit[(int(placement.product_id), int(placement.unit_index))] = rect
            if dimensions is not None and (
                rect.right_cm > dimensions.width_cm
                or rect.far_cm > dimensions.length_cm
            ):
                _add_violation(
                    violations,
                    recommended_changes,
                    code="product_outside_room_bounds",
                    message="A product footprint extends beyond the measured room envelope.",
                    placements=[placement],
                    recommendation="Move this unit fully inside the measured room or revise the composition.",
                )

    qualitative_protected = [
        zone.zone_id
        for zone in architectural_constraints.all_protected_zones()
        if zone.bounds is None
    ]
    if qualitative_protected:
        _add_warning(
            warnings,
            recommended_changes,
            code="unmeasured_protected_zones",
            message=(
                "Some door, window, or circulation zones are qualitative; "
                "conservative clearance is required."
            ),
            recommendation="Confirm measured opening and circulation bounds before claiming exact clearance.",
            zone_ids=qualitative_protected,
        )

    qualitative_doors = [
        zone
        for zone in [
            *architectural_constraints.door_zones,
            *architectural_constraints.door_swing_zones,
        ]
        if zone.bounds is None
    ]
    if qualitative_doors:
        door_safety_text = " ".join(
            [
                str(plan.circulation_strategy),
                *[str(value) for value in plan.global_instructions],
                *[
                    str(note)
                    for placement in placements
                    for note in placement.clearance_notes
                ],
            ]
        ).casefold()
        if not any(
            token in door_safety_text
            for token in ("door", "entry", "entrance", "opening")
        ):
            _add_violation(
                violations,
                recommended_changes,
                code="door_uncertainty_not_acknowledged",
                message=(
                    "Door/swing geometry is uncertain and the placement plan "
                    "does not explicitly retain a conservative entry envelope."
                ),
                recommendation=(
                    "Revise placement with an explicit conservative door/swing "
                    "clearance and preserved entry route."
                ),
                zone_ids=[zone.zone_id for zone in qualitative_doors],
            )

    # Architectural collision checks.
    for placement in placements:
        rect = rect_by_unit.get((int(placement.product_id), int(placement.unit_index)))
        if rect is None:
            continue
        item = selected_by_id.get(int(placement.product_id), {})
        category = str(item.get("category") or placement.category)
        removable = (
            [int(placement.product_id)]
            if item.get("selection_origin") == "system"
            and str(item.get("requirement_importance", "")) != "essential"
            else []
        )

        for zone in [
            *architectural_constraints.door_zones,
            *architectural_constraints.door_swing_zones,
            *architectural_constraints.no_block_zones,
        ]:
            if zone.bounds is None or not _intersects(rect, zone.bounds):
                continue
            code = {
                "door": "door_opening_blocked",
                "door_swing": "door_swing_blocked",
                "no_block": "architectural_no_block_zone_occupied",
            }[zone.zone_type]
            _add_violation(
                violations,
                recommended_changes,
                code=code,
                message="Furniture occupies a protected architectural clearance zone.",
                placements=[placement],
                zone=zone,
                recommendation="Reposition this unit outside the protected zone; remove only a lower-priority system addition if no safe placement exists.",
                removable_product_ids=removable,
            )

        for zone in architectural_constraints.window_zones:
            if zone.bounds is None or not _intersects(rect, zone.bounds):
                continue
            try:
                height = float(item.get("height_cm") or 0)
            except (TypeError, ValueError):
                height = 0
            if category == "television" or category in TALL_WINDOW_BLOCKERS or height > 100:
                _add_violation(
                    violations,
                    recommended_changes,
                    code="window_blocked",
                    message="A tall or glare-sensitive product blocks a window/access zone.",
                    placements=[placement],
                    zone=zone,
                    recommendation="Move the product to a solid wall and preserve natural light and window access.",
                    removable_product_ids=removable,
                )
            else:
                _add_warning(
                    warnings,
                    recommended_changes,
                    code="window_access_constrained",
                    message="Low furniture overlaps a window zone; sill height and access must be confirmed.",
                    placements=[placement],
                    recommendation="Keep the unit below the sill and preserve practical access to the window.",
                    severity="serious",
                )

        for zone in architectural_constraints.circulation_corridors:
            if zone.bounds is None or not _intersects(rect, zone.bounds):
                continue
            overlap_x, overlap_y = _intersection(rect, zone.bounds)
            axis = zone.axis or (
                "vertical"
                if zone.bounds.depth_cm >= zone.bounds.width_cm
                else "horizontal"
            )
            if axis == "vertical":
                remaining = max(zone.bounds.width_cm - overlap_x, 0)
            else:
                remaining = max(zone.bounds.depth_cm - overlap_y, 0)
            minimum = float(zone.minimum_clearance_cm or (60 if zone.is_primary else 45))
            target = float(zone.target_clearance_cm or (85 if zone.is_primary else 60))
            if remaining < minimum:
                _add_violation(
                    violations,
                    recommended_changes,
                    code="main_circulation_blocked" if zone.is_primary else "secondary_circulation_blocked",
                    message="Furniture narrows a circulation corridor below its safe practical minimum.",
                    placements=[placement],
                    zone=zone,
                    recommendation="Reposition the product to restore the route; drop a lower-priority system addition if necessary.",
                    removable_product_ids=removable,
                    remaining_clearance_cm=round(remaining, 1),
                    minimum_clearance_cm=minimum,
                )
            elif remaining < target:
                _add_warning(
                    warnings,
                    recommended_changes,
                    code="circulation_below_target",
                    message="A circulation route remains passable but is below its comfort target.",
                    placements=[placement],
                    recommendation="Increase the corridor toward its category-appropriate target where possible.",
                    severity="serious",
                    remaining_clearance_cm=round(remaining, 1),
                    target_clearance_cm=target,
                )

    # Product/product collision checks.
    floor_placements = [
        placement
        for placement in placements
        if str(selected_by_id.get(int(placement.product_id), {}).get("category") or placement.category)
        not in NON_FLOOR_CATEGORIES
        and _placement_rect(placement) is not None
    ]
    for first_index, first in enumerate(floor_placements):
        first_rect = _placement_rect(first)
        if first_rect is None:
            continue
        for second in floor_placements[first_index + 1 :]:
            second_rect = _placement_rect(second)
            if second_rect is None or not _intersects(first_rect, second_rect):
                continue
            first_item = selected_by_id.get(int(first.product_id), {})
            second_item = selected_by_id.get(int(second.product_id), {})
            removable_ids = [
                int(placement.product_id)
                for placement, item in ((first, first_item), (second, second_item))
                if item.get("selection_origin") == "system"
                and str(item.get("requirement_importance", "")) != "essential"
            ]
            _add_violation(
                violations,
                recommended_changes,
                code="major_product_overlap",
                message="Two floor-standing product footprints overlap physically.",
                placements=[first, second],
                recommendation="Separate the units; remove only a lower-priority system addition if the composition cannot fit.",
                removable_product_ids=removable_ids,
            )

    seating = [
        placement
        for placement in placements
        if str(selected_by_id.get(int(placement.product_id), {}).get("category") or placement.category)
        in {"sofa", "armchair"}
    ]
    coffee_tables = [
        placement
        for placement in placements
        if str(selected_by_id.get(int(placement.product_id), {}).get("category") or placement.category)
        == "coffee_table"
    ]
    rugs = [
        placement
        for placement in placements
        if str(
            selected_by_id.get(int(placement.product_id), {}).get("category")
            or placement.category
        ) == "rug"
    ]
    coffee_gaps: list[float] = []
    relational_metrics: dict[str, float | int | None] = {
        "coffee_table_seating_long_span_ratio": None,
        "rug_seating_long_span_ratio": None,
        "rug_table_width_margin_cm": None,
        "rug_table_depth_margin_cm": None,
        "rug_table_center_offset_cm": None,
        "rug_seating_intersection_count": 0,
    }
    for table in coffee_tables:
        table_rect = _placement_rect(table)
        candidate_gaps = [
            _rect_gap(table_rect, seat_rect)
            for seat in seating
            if table_rect is not None and (seat_rect := _placement_rect(seat)) is not None
        ]
        if not candidate_gaps:
            continue
        nearest = min(candidate_gaps)
        coffee_gaps.append(nearest)
        table_item = selected_by_id.get(int(table.product_id), {})
        removable = [int(table.product_id)] if table_item.get("selection_origin") == "system" and str(table_item.get("requirement_importance", "")) != "essential" else []
        if nearest < 30:
            _add_violation(
                violations,
                recommended_changes,
                code="coffee_table_too_close",
                message="The coffee table is too close to seating for safe leg movement.",
                placements=[table],
                recommendation="Revise placement to approximately 35-50 cm from the nearest seating edge.",
                removable_product_ids=removable,
                measured_gap_cm=round(nearest, 1),
            )
        elif nearest < 35:
            _add_warning(
                warnings,
                recommended_changes,
                code="coffee_table_spacing_tight",
                message="Coffee-table spacing is usable but tighter than the comfort target.",
                placements=[table],
                recommendation="Increase the nearest seating gap toward 35-50 cm.",
                severity="serious",
                measured_gap_cm=round(nearest, 1),
            )
        elif nearest > 65:
            _add_warning(
                warnings,
                recommended_changes,
                code="coffee_table_too_far",
                message="The coffee table is too far from seating to be comfortably reachable.",
                placements=[table],
                recommendation="Move the table into a 35-50 cm relationship with the main seating group.",
                severity="serious",
                measured_gap_cm=round(nearest, 1),
            )

    # Relational scale and anchoring for the central seating composition.
    # The largest rug/table represent the primary layer when intentional
    # layering exists; exact selected product dimensions remain authoritative.
    seating_with_rects = [
        (placement, rect)
        for placement in seating
        if (rect := _placement_rect(placement)) is not None
    ]
    tables_with_rects = [
        (placement, rect)
        for placement in coffee_tables
        if (rect := _placement_rect(placement)) is not None
    ]
    rugs_with_rects = [
        (placement, rect)
        for placement in rugs
        if (rect := _placement_rect(placement)) is not None
    ]
    primary_seat = max(
        seating_with_rects,
        key=lambda value: value[1].width_cm * value[1].depth_cm,
        default=None,
    )
    primary_table = max(
        tables_with_rects,
        key=lambda value: value[1].width_cm * value[1].depth_cm,
        default=None,
    )
    primary_rug = max(
        rugs_with_rects,
        key=lambda value: value[1].width_cm * value[1].depth_cm,
        default=None,
    )

    if primary_seat is not None and primary_table is not None:
        seat_placement, seat_rect = primary_seat
        table_placement, table_rect = primary_table
        seat_span = max(seat_rect.width_cm, seat_rect.depth_cm)
        table_span = max(table_rect.width_cm, table_rect.depth_cm)
        table_seating_ratio = table_span / max(seat_span, 1)
        relational_metrics["coffee_table_seating_long_span_ratio"] = round(
            table_seating_ratio, 3
        )
        relation_placements = [table_placement, seat_placement]
        if table_seating_ratio < 0.25 or table_seating_ratio > 0.90:
            _add_violation(
                violations,
                recommended_changes,
                code="coffee_table_seating_scale_invalid",
                message=(
                    "The coffee table is severely under- or over-scaled "
                    "relative to the primary seating piece."
                ),
                placements=relation_placements,
                recommendation=(
                    "Use a coffee table whose long span is approximately "
                    "40-75% of the primary seating span."
                ),
                removable_product_ids=_removable_product_ids(
                    [table_placement], selected_by_id
                ),
                long_span_ratio=round(table_seating_ratio, 3),
            )
        elif table_seating_ratio < 0.40 or table_seating_ratio > 0.75:
            _add_warning(
                warnings,
                recommended_changes,
                code="coffee_table_seating_scale_outside_target",
                message=(
                    "The coffee table is usable but outside the preferred "
                    "scale range for the primary seating piece."
                ),
                placements=relation_placements,
                recommendation=(
                    "Prefer a coffee-table long span around 40-75% of the "
                    "primary seating span."
                ),
                severity="serious",
                long_span_ratio=round(table_seating_ratio, 3),
            )

    if primary_seat is not None and primary_rug is not None:
        seat_placement, seat_rect = primary_seat
        rug_placement, rug_rect = primary_rug
        seat_span = max(seat_rect.width_cm, seat_rect.depth_cm)
        rug_span = max(rug_rect.width_cm, rug_rect.depth_cm)
        rug_seating_ratio = rug_span / max(seat_span, 1)
        relational_metrics["rug_seating_long_span_ratio"] = round(
            rug_seating_ratio, 3
        )
        relation_placements = [rug_placement, seat_placement]
        if rug_seating_ratio < 0.65:
            _add_violation(
                violations,
                recommended_changes,
                code="rug_too_small_for_seating",
                message=(
                    "The primary rug is severely underscaled for the main "
                    "seating anchor."
                ),
                placements=relation_placements,
                recommendation=(
                    "Use a larger rug whose long span is at least 90% of the "
                    "primary seating span, or remove the incompatible system rug."
                ),
                removable_product_ids=_removable_product_ids(
                    [rug_placement], selected_by_id
                ),
                long_span_ratio=round(rug_seating_ratio, 3),
            )
        elif rug_seating_ratio < 0.90:
            _add_warning(
                warnings,
                recommended_changes,
                code="rug_seating_scale_outside_target",
                message=(
                    "The rug is smaller than the preferred anchoring scale "
                    "for the main seating piece."
                ),
                placements=relation_placements,
                recommendation=(
                    "Prefer a rug whose long span is at least 90% of the "
                    "primary seating span."
                ),
                severity="serious",
                long_span_ratio=round(rug_seating_ratio, 3),
            )

        intersecting_seats = [
            placement
            for placement, rect in seating_with_rects
            if _intersects(rug_rect, rect)
        ]
        relational_metrics["rug_seating_intersection_count"] = len(
            intersecting_seats
        )
        if not intersecting_seats:
            _add_warning(
                warnings,
                recommended_changes,
                code="rug_not_anchoring_seating",
                message=(
                    "The rug does not intersect the front zone of any seating "
                    "piece and therefore does not anchor the seating group."
                ),
                placements=[rug_placement, *[item for item, _ in seating_with_rects]],
                recommendation=(
                    "Reposition the rug under the front zone of the main "
                    "seating while preserving circulation."
                ),
                severity="serious",
            )

    if primary_rug is not None and primary_table is not None:
        rug_placement, rug_rect = primary_rug
        table_placement, table_rect = primary_table
        width_margin = (rug_rect.width_cm - table_rect.width_cm) / 2
        depth_margin = (rug_rect.depth_cm - table_rect.depth_cm) / 2
        relational_metrics["rug_table_width_margin_cm"] = round(width_margin, 1)
        relational_metrics["rug_table_depth_margin_cm"] = round(depth_margin, 1)
        rug_area = rug_rect.width_cm * rug_rect.depth_cm
        table_area = table_rect.width_cm * table_rect.depth_cm
        relation_placements = [rug_placement, table_placement]
        if width_margin < 0 or depth_margin < 0 or rug_area < table_area * 1.25:
            _add_violation(
                violations,
                recommended_changes,
                code="rug_too_small_for_table",
                message=(
                    "The rug cannot provide a credible border around the "
                    "coffee table at their exact selected scales."
                ),
                placements=relation_placements,
                recommendation=(
                    "Use a larger rug or a smaller compatible system table; "
                    "the rug should exceed the table on both floor axes."
                ),
                removable_product_ids=_removable_product_ids(
                    relation_placements, selected_by_id
                ),
                width_margin_cm=round(width_margin, 1),
                depth_margin_cm=round(depth_margin, 1),
                rug_to_table_area_ratio=round(rug_area / max(table_area, 1), 3),
            )
        elif width_margin < 15 or depth_margin < 15:
            _add_warning(
                warnings,
                recommended_changes,
                code="rug_table_scale_tight",
                message="The rug leaves only a narrow visual border around the table.",
                placements=relation_placements,
                recommendation=(
                    "Prefer at least 15 cm of rug border beyond the table on "
                    "each side of both floor axes."
                ),
                severity="serious",
                width_margin_cm=round(width_margin, 1),
                depth_margin_cm=round(depth_margin, 1),
            )

        if not _intersects(rug_rect, table_rect):
            _add_warning(
                warnings,
                recommended_changes,
                code="rug_not_anchoring_table",
                message="The coffee table is spatially disconnected from its rug.",
                placements=relation_placements,
                recommendation="Center the coffee table within the primary rug layer.",
                severity="serious",
            )
        else:
            rug_center = _rect_center(rug_rect)
            table_center = _rect_center(table_rect)
            center_offset = math.hypot(
                rug_center[0] - table_center[0],
                rug_center[1] - table_center[1],
            )
            relational_metrics["rug_table_center_offset_cm"] = round(
                center_offset, 1
            )
            allowed_offset = max(
                20.0,
                min(rug_rect.width_cm, rug_rect.depth_cm) * 0.20,
            )
            if center_offset > allowed_offset:
                _add_warning(
                    warnings,
                    recommended_changes,
                    code="rug_table_alignment_drift",
                    message=(
                        "The coffee table is materially off-center within the "
                        "primary rug layer."
                    ),
                    placements=relation_placements,
                    recommendation="Realign the table and rug around one central axis.",
                    severity="serious",
                    center_offset_cm=round(center_offset, 1),
                    allowed_offset_cm=round(allowed_offset, 1),
                )

    # Side tables must belong to seating.
    for side_table in [
        placement
        for placement in placements
        if str(selected_by_id.get(int(placement.product_id), {}).get("category") or placement.category)
        == "side_table"
    ]:
        side_rect = _placement_rect(side_table)
        gaps = [
            _rect_gap(side_rect, seat_rect)
            for seat in seating
            if side_rect is not None and (seat_rect := _placement_rect(seat)) is not None
        ]
        nearest = min(gaps) if gaps else None
        if nearest is None or nearest > 75:
            item = selected_by_id.get(int(side_table.product_id), {})
            if item.get("selection_origin") == "system":
                _add_violation(
                    violations,
                    recommended_changes,
                    code="isolated_side_table",
                    message="A system-added side table has no useful relationship to seating.",
                    placements=[side_table],
                    recommendation="Move it within reach of seating or remove this system addition.",
                    removable_product_ids=[int(side_table.product_id)],
                    measured_gap_cm=round(nearest, 1) if nearest is not None else None,
                )
            else:
                _add_warning(
                    warnings,
                    recommended_changes,
                    code="isolated_committed_side_table",
                    message="A committed side table lacks a practical seating relationship.",
                    placements=[side_table],
                    recommendation="Reposition it adjacent to its intended seat.",
                    severity="serious",
                    measured_gap_cm=round(nearest, 1) if nearest is not None else None,
                )

    # Storage doors/drawers need usable approach clearance when reported by
    # the structured placement plan.
    for storage in [
        placement
        for placement in placements
        if str(selected_by_id.get(int(placement.product_id), {}).get("category") or placement.category)
        in {"storage_cabinet", "sideboard", "wardrobe", "bookshelf", "tv_stand"}
    ]:
        clearance = getattr(storage, "nearest_clearance_cm", None)
        if clearance is not None and float(clearance) < 45:
            item = selected_by_id.get(int(storage.product_id), {})
            _add_violation(
                violations,
                recommended_changes,
                code="storage_access_clearance_blocked",
                message="A storage product lacks practical door/drawer approach clearance.",
                placements=[storage],
                recommendation="Restore usable front access or remove a lower-priority system storage addition.",
                removable_product_ids=(
                    [int(storage.product_id)]
                    if item.get("selection_origin") == "system"
                    and str(item.get("requirement_importance", "")) != "essential"
                    else []
                ),
                measured_clearance_cm=round(float(clearance), 1),
            )

    # Media pairing, console scale, and viewing relationship.
    televisions = [item for item in selected_items if item.get("category") == "television"]
    stands = [item for item in selected_items if item.get("category") == "tv_stand"]
    media_stands = [
        item for item in stands if item.get("functional_zone") != "storage"
    ]
    if bool(televisions) != bool(media_stands):
        media_items = televisions or media_stands
        _add_violation(
            violations,
            recommended_changes,
            code="incomplete_media_pair",
            message="Television and media console must form a complete functional pair.",
            placements=[
                placement
                for placement in placements
                if int(placement.product_id) in {int(item["id"]) for item in media_items}
            ],
            recommendation="Add the compatible missing half or remove a system-added half-pair.",
            removable_product_ids=[
                int(item["id"])
                for item in media_items
                if item.get("selection_origin") == "system"
            ],
        )
    if televisions and media_stands:
        try:
            television_width = max(float(item.get("width_cm") or 0) for item in televisions)
            stand_width = max(float(item.get("width_cm") or 0) for item in media_stands)
        except (TypeError, ValueError):
            television_width = stand_width = 0
        if television_width and stand_width and television_width > stand_width * 1.2:
            _add_violation(
                violations,
                recommended_changes,
                code="media_console_too_narrow",
                message="The selected media console is not proportionally compatible with the television.",
                placements=[
                    placement
                    for placement in placements
                    if int(placement.product_id) in {
                        *[int(item["id"]) for item in televisions],
                        *[int(item["id"]) for item in media_stands],
                    }
                ],
                recommendation="Choose a wider compatible media console or a smaller television.",
                removable_product_ids=[
                    int(item["id"])
                    for item in [*televisions, *media_stands]
                    if item.get("selection_origin") == "system"
                ],
                television_width_cm=television_width,
                stand_width_cm=stand_width,
            )

        television_ids = {int(item["id"]) for item in televisions}
        viewing_seats = []
        for placement in seating:
            relationships = " ".join(placement.relationships).casefold()
            related_ids = set(getattr(placement, "related_product_ids", []) or [])
            faces_product_id = getattr(placement, "faces_product_id", None)
            if (
                faces_product_id in television_ids
                or related_ids & television_ids
                or "television" in relationships
                or " tv" in f" {relationships}"
            ):
                viewing_seats.append(placement)
        if not viewing_seats:
            _add_violation(
                violations,
                recommended_changes,
                code="no_tv_viewing_relationship",
                message="The television has no explicit reasonable viewing relationship to seating.",
                placements=[
                    placement
                    for placement in placements
                    if int(placement.product_id) in television_ids
                ],
                recommendation="Revise the media placement while preserving the intended seating composition, or omit the media addition.",
                removable_product_ids=[
                    int(item["id"])
                    for item in [*televisions, *media_stands]
                    if item.get("selection_origin") == "system"
                ],
            )

    # Visual-weight balance on the room's left/right axis.
    weighted_centers: list[tuple[float, float]] = []
    for placement in floor_placements:
        rect = _placement_rect(placement)
        item = selected_by_id.get(int(placement.product_id), {})
        category = str(item.get("category") or placement.category)
        if rect is None:
            continue
        weight = VISUAL_WEIGHTS.get(category, 1.5)
        weighted_centers.append((rect.x_cm + rect.width_cm / 2, weight))
    imbalance_ratio = None
    if dimensions is not None and len(weighted_centers) >= 2:
        weighted_center = sum(x * weight for x, weight in weighted_centers) / sum(
            weight for _, weight in weighted_centers
        )
        room_center = dimensions.width_cm / 2
        imbalance_ratio = abs(weighted_center - room_center) / max(room_center, 1)
        if imbalance_ratio > 0.55:
            _add_violation(
                violations,
                recommended_changes,
                code="severe_visual_imbalance",
                message="The layout concentrates nearly all visual weight on one side of the room.",
                recommendation="Redistribute major pieces while preserving circulation and focal intent.",
                imbalance_ratio=round(imbalance_ratio, 3),
            )
        elif imbalance_ratio > 0.30:
            _add_warning(
                warnings,
                recommended_changes,
                code="visual_weight_imbalance",
                message="Major visual weight is noticeably concentrated on one side.",
                recommendation="Balance the composition with position changes or a lighter opposing anchor.",
                severity="serious",
                imbalance_ratio=round(imbalance_ratio, 3),
            )

    # Additional deterministic visual-comfort heuristics. These do not
    # pretend to replace image reasoning; they catch clear scale, grouping,
    # repetition, alignment, and palette problems first.
    if dimensions is not None:
        room_area = dimensions.length_cm * dimensions.width_cm
        for placement_item in floor_placements:
            rect = _placement_rect(placement_item)
            if rect is None:
                continue
            footprint_ratio = (rect.width_cm * rect.depth_cm) / max(room_area, 1)
            if footprint_ratio > 0.32:
                _add_warning(
                    warnings,
                    recommended_changes,
                    code="dominant_product_scale",
                    message="One product carries disproportionate visual and physical weight.",
                    placements=[placement_item],
                    recommendation="Confirm the dominant scale is intentional and preserve compensating negative space.",
                    severity="serious",
                    footprint_ratio=round(footprint_ratio, 3),
                )

        if len(seating) >= 2:
            seating_rects = [
                (seat, _placement_rect(seat))
                for seat in seating
                if _placement_rect(seat) is not None
            ]
            isolated = []
            for seat, seat_rect in seating_rects:
                nearest = min(
                    (
                        _rect_gap(seat_rect, other_rect)
                        for other, other_rect in seating_rects
                        if other is not seat
                    ),
                    default=0,
                )
                if nearest > 260:
                    isolated.append(seat)
            if isolated:
                _add_warning(
                    warnings,
                    recommended_changes,
                    code="isolated_seating_relationship",
                    message="One or more seats are too isolated for a coherent conversation group.",
                    placements=isolated,
                    recommendation="Regroup seating while retaining primary circulation and focal intent.",
                    severity="serious",
                )

            strategy_text = str(plan.layout_strategy).casefold()
            if any(token in strategy_text for token in ("facing", "symmetric", "symmetry")):
                seating_centers = [
                    rect.x_cm + rect.width_cm / 2
                    for _, rect in seating_rects
                ]
                if seating_centers:
                    group_center = sum(seating_centers) / len(seating_centers)
                    symmetry_offset = abs(
                        group_center - dimensions.width_cm / 2
                    ) / max(dimensions.width_cm / 2, 1)
                    if symmetry_offset > 0.20:
                        _add_warning(
                            warnings,
                            recommended_changes,
                            code="unintended_symmetry_drift",
                            message="A symmetry-led layout is materially off its room-relative balance axis.",
                            placements=[seat for seat, _ in seating_rects],
                            recommendation="Realign the repeated seating group around its intended axis.",
                            severity="serious",
                            symmetry_offset=round(symmetry_offset, 3),
                        )

        if coffee_tables and seating:
            table_centers = [
                rect.x_cm + rect.width_cm / 2
                for table in coffee_tables
                if (rect := _placement_rect(table)) is not None
            ]
            seating_centers = [
                rect.x_cm + rect.width_cm / 2
                for seat in seating
                if (rect := _placement_rect(seat)) is not None
            ]
            if table_centers and seating_centers:
                alignment_offset = abs(
                    sum(table_centers) / len(table_centers)
                    - sum(seating_centers) / len(seating_centers)
                )
                if alignment_offset > 90:
                    _add_warning(
                        warnings,
                        recommended_changes,
                        code="central_zone_alignment_drift",
                        message="The coffee-table layer is not aligned with the visual center of seating.",
                        placements=coffee_tables,
                        recommendation="Realign the central surface with the intended seating-group center.",
                        severity="serious",
                        alignment_offset_cm=round(alignment_offset, 1),
                    )

    palette = {
        str(item.get("color") or "").strip().casefold()
        for item in selected_items
        if str(item.get("color") or "").strip()
    }
    if len(palette) > 4:
        _add_warning(
            warnings,
            recommended_changes,
            code="competing_color_families",
            message="The selected inventory contains many competing color descriptions.",
            recommendation="Confirm an intentional dominant/secondary/accent palette before render.",
            distinct_color_count=len(palette),
        )

    density_ratio = None
    density_classification = None
    confirmation_allowed = True
    if composition_validation is not None:
        if isinstance(composition_validation, BaseModel):
            composition_payload = composition_validation.model_dump()
        else:
            composition_payload = composition_validation
        density_ratio = composition_payload.get("estimated_density_ratio")
        density_classification = composition_payload.get("density_classification")
        confirmation_allowed = composition_payload.get(
            "confirmation_allowed",
            composition_payload.get("status") != "invalid",
        )
    if density_ratio is not None:
        density_ratio = float(density_ratio)
        if not confirmation_allowed:
            _add_violation(
                violations,
                recommended_changes,
                code="physically_impossible_inventory",
                message="Direct dimension evidence shows that the confirmed inventory cannot fit.",
                recommendation="Return the unchanged inventory for explicit quantity or product edits.",
                estimated_density_ratio=round(density_ratio, 3),
            )
        elif density_classification in {"moderately_dense", "high_density"}:
            _add_warning(
                warnings,
                recommended_changes,
                code="high_density_selection",
                message="The overlap-adjusted composition is dense but may proceed to generation.",
                recommendation="Keep the exact inventory unless the user explicitly edits it; preserve circulation in the generated arrangement.",
                severity=(
                    "serious" if density_classification == "high_density" else "advisory"
                ),
                estimated_density_ratio=round(density_ratio, 3),
            )

    functional_penalties = sum(
        22
        for violation in violations
        if violation["code"]
        in {
            "window_blocked",
            "incomplete_media_pair",
            "media_console_too_narrow",
            "no_tv_viewing_relationship",
            "isolated_side_table",
            "coffee_table_seating_scale_invalid",
            "rug_too_small_for_seating",
            "rug_too_small_for_table",
        }
    )
    circulation_penalties = sum(
        28
        for violation in violations
        if violation["code"]
        in {
            "door_opening_blocked",
            "door_swing_blocked",
            "door_uncertainty_not_acknowledged",
            "architectural_no_block_zone_occupied",
            "main_circulation_blocked",
            "secondary_circulation_blocked",
            "major_product_overlap",
            "product_outside_room_bounds",
            "storage_access_clearance_blocked",
        }
    )
    comfort_penalties = sum(
        25
        for violation in violations
        if violation["code"]
        in {
            "coffee_table_too_close",
            "major_product_overlap",
            "coffee_table_seating_scale_invalid",
        }
    )
    serious_warnings = [
        warning
        for warning in warnings
        if warning.get("severity") == "serious"
    ]
    warning_score_penalties = {
        dimension: sum(
            SERIOUS_WARNING_SCORE_PENALTIES.get(
                warning["code"], {"functional": 5}
            ).get(dimension, 0)
            for warning in serious_warnings
        )
        for dimension in (
            "functional",
            "circulation",
            "visual",
            "comfort",
            "density",
        )
    }
    functional_penalties += warning_score_penalties["functional"]
    circulation_penalties += warning_score_penalties["circulation"]
    comfort_penalties += warning_score_penalties["comfort"]

    visual_violation_penalties = sum(
        18
        for violation in violations
        if violation["code"]
        in {
            "coffee_table_seating_scale_invalid",
            "rug_too_small_for_seating",
            "rug_too_small_for_table",
        }
    )
    visual_score = (
        max(0, round(100 - (imbalance_ratio or 0) * 100))
        if dimensions is not None
        else 70
    )
    visual_score = max(
        0,
        visual_score
        - visual_violation_penalties
        - warning_score_penalties["visual"]
        - 5 * sum(
            1
            for warning in warnings
            if warning["code"] == "competing_color_families"
        ),
    )
    density_score = (
        max(0, min(100, round(100 - max(float(density_ratio or 0) - 0.25, 0) * 180)))
        if density_ratio is not None
        else 70
    )
    density_score = max(
        0,
        density_score - warning_score_penalties["density"],
    )

    if violations:
        status = LayoutQualityStatus.INVALID
    elif unknown_geometry or qualitative_protected or dimensions is None:
        status = LayoutQualityStatus.UNCERTAIN_GEOMETRY
    elif warnings:
        status = LayoutQualityStatus.VALID_WITH_WARNINGS
    else:
        status = LayoutQualityStatus.VALID

    return LayoutQualityResult(
        status=status,
        functional_score=max(0, 100 - functional_penalties),
        circulation_score=max(0, 100 - circulation_penalties),
        visual_balance_score=visual_score,
        comfort_score=max(0, 100 - comfort_penalties),
        density_score=density_score,
        violations=violations,
        warnings=warnings,
        recommended_changes=recommended_changes,
        metrics={
            "placements_with_uncertain_geometry": unknown_geometry,
            "qualitative_protected_zone_ids": qualitative_protected,
            "coffee_table_nearest_gaps_cm": [round(value, 1) for value in coffee_gaps],
            "visual_imbalance_ratio": (
                round(imbalance_ratio, 3) if imbalance_ratio is not None else None
            ),
            "estimated_density_ratio": density_ratio,
            "serious_warning_codes": [
                warning["code"] for warning in serious_warnings
            ],
            "replan_required_warning_codes": [
                warning["code"]
                for warning in warnings
                if warning["code"] in REPLAN_REQUIRED_WARNING_CODES
            ],
            "serious_warning_score_penalties": warning_score_penalties,
            **relational_metrics,
        },
        assumptions=assumptions,
    )


def reduce_selection_for_layout(
    *,
    selected_items: list[dict],
    layout_quality: LayoutQualityResult | dict,
) -> LayoutSelectionReduction:
    """Remove only validator-identified lower-priority system additions.

    The placement must be regenerated and validated after this operation.
    Committed user/template products and essential requirements are immutable.
    Functional groups are removed atomically so a TV/console half-pair cannot
    survive a reduction.
    """

    quality = (
        layout_quality
        if isinstance(layout_quality, LayoutQualityResult)
        else LayoutQualityResult.model_validate(layout_quality)
    )
    removable_ids = {
        int(product_id)
        for change in quality.recommended_changes
        for product_id in change.get("removable_product_ids", [])
    }
    eligible_ids = {
        int(item["id"])
        for item in selected_items
        if int(item["id"]) in removable_ids
        and item.get("selection_origin") == "system"
        and str(item.get("requirement_importance") or "") != "essential"
    }
    if not eligible_ids:
        return LayoutSelectionReduction(
            selected_items=[dict(item) for item in selected_items],
            hard_conflict=quality.status == LayoutQualityStatus.INVALID,
            reason=(
                "No validator-identified lower-priority system addition can be "
                "removed without changing committed or essential inventory."
            ),
        )

    group_ids = {
        group_id
        for item in selected_items
        if int(item["id"]) in eligible_ids
        for group_id in (item.get("functional_group_ids") or [])
    }
    if group_ids:
        eligible_ids.update(
            int(item["id"])
            for item in selected_items
            if group_ids & set(item.get("functional_group_ids") or [])
            and item.get("selection_origin") == "system"
            and str(item.get("requirement_importance") or "") != "essential"
        )

    removed = [
        dict(item) for item in selected_items if int(item["id"]) in eligible_ids
    ]
    retained = [
        dict(item) for item in selected_items if int(item["id"]) not in eligible_ids
    ]
    return LayoutSelectionReduction(
        selected_items=retained,
        removed_items=removed,
        hard_conflict=False,
        reason=(
            "Removed validator-identified optional/recommended/supporting system "
            "additions; placement must be regenerated before final lock."
        ),
    )
