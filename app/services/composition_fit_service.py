from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.models import RoomDimensions
from app.services.catalog_compatibility_service import (
    is_ceiling_mounted,
    is_design_eligible,
)


class CompositionFitStatus(StrEnum):
    VALID = "valid"
    VALID_WITH_WARNINGS = "valid_with_warnings"
    INVALID = "invalid"
    UNCERTAIN_GEOMETRY = "uncertain_geometry"


class DensityClassification(StrEnum):
    COMFORTABLE = "comfortable"
    MODERATELY_DENSE = "moderately_dense"
    HIGH_DENSITY = "high_density"
    PHYSICALLY_IMPLAUSIBLE = "physically_implausible"


class CompositionFitResult(BaseModel):
    # Defaults keep legacy persisted selections loadable while new validations
    # expose the complete overlap-aware capacity report.
    status: CompositionFitStatus
    density_classification: DensityClassification | None = None
    confirmation_allowed: bool = True
    room_type: str | None = None
    room_dimensions_cm: dict | None = None
    room_floor_area_m2: float | None = None
    usable_floor_area_m2: float | None = None
    circulation_reserve_area_m2: float | None = None
    raw_product_footprint_m2: float | None = None
    major_furniture_footprint_m2: float | None = None
    overlap_adjusted_effective_footprint_m2: float | None = None
    effective_occupancy_ratio: float | None = None
    # Legacy aliases retained for renderer/persisted-contract compatibility.
    estimated_required_area_m2: float | None = None
    estimated_density_ratio: float | None = None
    excluded_from_independent_floor_area: list[dict] = Field(default_factory=list)
    overlap_adjustments: list[dict] = Field(default_factory=list)
    largest_space_consuming_products: list[dict] = Field(default_factory=list)
    violations: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class CompositionReductionResult(BaseModel):
    selected_items: list[dict] = Field(default_factory=list)
    removed_items: list[dict] = Field(default_factory=list)
    validation: CompositionFitResult
    hard_conflict: bool = False


NON_FLOOR_CATEGORIES = {
    "wall_art",
    "wall_shelf",
    "mirror",
    "television",
    "tv",
    "wall_mounted_television",
    "wall_lamp",
    "wall_light",
    "ceiling_lamp",
    "ceiling_light",
    "pendant_lamp",
    "table_lamp",
    "desk_lamp",
    "accessory",
    "decor",
}

LAYERED_FLOOR_CATEGORIES = {
    "rug",
    "coffee_table",
    "side_table",
}

SEATING_CATEGORIES = {
    "sofa",
    "sectional_sofa",
    "corner_sofa",
    "loveseat",
    "armchair",
    "recliner",
    "dining_chair",
    "office_chair",
}

MAJOR_FLOOR_CATEGORIES = {
    *SEATING_CATEGORIES,
    "dining_table",
    "table",
    "console_table",
    "desk",
    "tv_stand",
    "media_console",
    "cabinet",
    "storage_cabinet",
    "sideboard",
    "bookshelf",
    "bookcase",
    "bed",
    "bunk_bed",
    "wardrobe",
    "dresser",
    "chest_of_drawers",
}

CIRCULATION_RESERVE_BY_ROOM_TYPE = {
    "living_room": 0.18,
    "bedroom": 0.20,
    "dining_room": 0.22,
    "office": 0.18,
    "kitchen": 0.30,
    "bathroom": 0.30,
}

UNKNOWN_GEOMETRY_MARKERS = {
    "not visible",
    "unseen",
    "unknown",
    "uncertain",
    "inferred",
    "possibly",
    "may be",
}


def _normalized_category(item: dict) -> str:
    return str(item.get("category") or "").strip().casefold().replace("-", "_")


def _has_confirmed_geometry(text: str, subject: str) -> bool:
    normalized = text.casefold()
    return (
        subject in normalized
        and not any(marker in normalized for marker in UNKNOWN_GEOMETRY_MARKERS)
    )


def _effective_factor(
    category: str,
    *,
    has_seating: bool,
    has_rug: bool,
    has_tv_stand: bool,
) -> tuple[float, str | None]:
    """Return the independent floor-area share for one product category."""

    if category == "rug":
        return 0.0, "Rug overlaps the seating zone."
    if category == "coffee_table":
        if has_rug or has_seating:
            return 0.25, "Coffee table shares the rug/seating composition."
        return 1.0, None
    if category == "side_table":
        if has_seating:
            return 0.30, "Side table uses an existing seating-edge gap."
        return 1.0, None
    if category in {"television", "tv", "wall_mounted_television"}:
        return (
            0.0,
            "Television is wall-mounted above the TV stand."
            if has_tv_stand
            else "Wall-mounted television does not occupy floor area.",
        )
    if category == "floor_lamp":
        return 0.15, "Floor lamp uses negligible corner or edge clearance."
    if category in NON_FLOOR_CATEGORIES or "wall_" in category:
        return 0.0, "Wall-mounted or accessory product does not occupy floor area."
    return 1.0, None


def validate_composition_fit(
    selected_items: list[dict],
    room_dimensions: RoomDimensions | None,
    obstacles: list[str] | None = None,
    circulation_paths: list[str] | None = None,
    room_type: str | None = None,
) -> CompositionFitResult:
    """Estimate effective occupancy without inventing placement coordinates.

    Density and circulation reserves are advisory when product coordinates are
    unknown. A route may legitimately run behind wall-aligned furniture, so a
    room-wide reserve must never fabricate a hard collision. Generation is
    blocked only by coordinate-free physical impossibilities: an individual
    product cannot fit in either orientation, or independently placed major
    furniture has more floor area than the room itself.
    """

    if room_dimensions is None:
        return CompositionFitResult(
            status=CompositionFitStatus.UNCERTAIN_GEOMETRY,
            confirmation_allowed=True,
            room_type=room_type,
            warnings=[
                "Measured room length and width are required for whole-layout fit validation."
            ],
        )

    room_area_cm2 = room_dimensions.length_cm * room_dimensions.width_cm
    raw_area_cm2 = 0.0
    major_area_cm2 = 0.0
    seating_area_cm2 = 0.0
    effective_area_cm2 = 0.0
    violations: list[dict] = []
    warnings: list[str] = []
    missing_footprints: list[int | None] = []
    excluded_items: list[dict] = []
    overlap_adjustments: list[dict] = []
    product_contributions: list[dict] = []

    categories = {_normalized_category(item) for item in selected_items}
    has_seating = bool(categories & SEATING_CATEGORIES)
    has_rug = "rug" in categories
    has_tv_stand = bool(categories & {"tv_stand", "media_console"})

    for item in selected_items:
        category = _normalized_category(item)
        product_id = item.get("id")
        try:
            quantity = int(item.get("quantity", 1))
        except (TypeError, ValueError):
            quantity = 0
        if quantity < 1:
            violations.append(
                {
                    "code": "invalid_product_quantity",
                    "product_id": product_id,
                    "quantity": item.get("quantity"),
                    "reason": "Product quantity must be at least one.",
                }
            )
            continue
        if not is_design_eligible(item):
            violations.append(
                {
                    "code": "selected_product_unavailable_or_quarantined",
                    "product_id": product_id,
                    "reason": "Selected product is not currently design-eligible.",
                }
            )
            continue

        if is_ceiling_mounted(item):
            factor, adjustment_reason = (
                0.0,
                "Ceiling-mounted product does not occupy floor area.",
            )
        else:
            factor, adjustment_reason = _effective_factor(
                category,
                has_seating=has_seating,
                has_rug=has_rug,
                has_tv_stand=has_tv_stand,
            )
        try:
            width = float(item["width_cm"])
            depth = float(item["depth_cm"])
        except (KeyError, TypeError, ValueError):
            if factor > 0:
                missing_footprints.append(product_id)
            continue

        unit_area_cm2 = width * depth
        line_raw_area_cm2 = unit_area_cm2 * quantity
        line_effective_area_cm2 = line_raw_area_cm2 * factor
        raw_area_cm2 += line_raw_area_cm2
        effective_area_cm2 += line_effective_area_cm2

        is_independent_floor_product = (
            factor == 1.0 and category not in NON_FLOOR_CATEGORIES
        )
        if category in MAJOR_FLOOR_CATEGORIES:
            area_classification = "major_floor"
        elif category in LAYERED_FLOOR_CATEGORIES:
            area_classification = "layered_or_overlapping"
        elif factor == 0:
            area_classification = "non_floor_or_negligible"
        elif factor < 1:
            area_classification = "negligible_floor"
        else:
            area_classification = "independent_floor"
        if is_independent_floor_product:
            major_area_cm2 += line_raw_area_cm2
            if category in SEATING_CATEGORIES:
                seating_area_cm2 += line_raw_area_cm2

        contribution = {
            "product_id": product_id,
            "name": item.get("name"),
            "category": category,
            "floor_area_classification": area_classification,
            "quantity": quantity,
            "raw_footprint_m2": round(line_raw_area_cm2 / 10000, 2),
            "effective_footprint_m2": round(
                line_effective_area_cm2 / 10000, 2
            ),
        }
        product_contributions.append(contribution)
        if adjustment_reason is not None:
            adjustment = {
                **contribution,
                "reason": adjustment_reason,
            }
            excluded_items.append(adjustment)
            overlap_adjustments.append(adjustment)

        # A layered floor product must still fit in at least one orientation;
        # only genuinely non-floor products are exempt from this direct check.
        if factor > 0 or category in LAYERED_FLOOR_CATEGORIES:
            fits_unrotated = (
                width <= room_dimensions.width_cm
                and depth <= room_dimensions.length_cm
            )
            fits_rotated = (
                depth <= room_dimensions.width_cm
                and width <= room_dimensions.length_cm
            )
        else:
            fits_unrotated = True
            fits_rotated = True
        if not (fits_unrotated or fits_rotated):
            violations.append(
                {
                    "code": "product_exceeds_room_in_every_orientation",
                    "product_id": product_id,
                    "product_width_cm": width,
                    "product_depth_cm": depth,
                    "room_width_cm": room_dimensions.width_cm,
                    "room_length_cm": room_dimensions.length_cm,
                    "reason": (
                        "A selected floor product is larger than the measured "
                        "room in every orientation."
                    ),
                }
            )

    # These are area lower bounds, not density heuristics. If either is true,
    # no coordinate-free rearrangement can make the inventory physically fit.
    if seating_area_cm2 > room_area_cm2:
        violations.append(
            {
                "code": "major_seating_footprint_exceeds_room_area",
                "major_seating_footprint_m2": round(seating_area_cm2 / 10000, 2),
                "room_floor_area_m2": round(room_area_cm2 / 10000, 2),
                "reason": (
                    "The major seating footprint alone is larger than the room "
                    "floor, before any circulation is considered."
                ),
            }
        )
    if major_area_cm2 > room_area_cm2:
        violations.append(
            {
                "code": "required_major_products_exceed_room_area",
                "major_furniture_footprint_m2": round(major_area_cm2 / 10000, 2),
                "room_floor_area_m2": round(room_area_cm2 / 10000, 2),
                "reason": (
                    "Required independently placed major products have more "
                    "physical floor area than the room itself."
                ),
            }
        )

    reserve_fraction = CIRCULATION_RESERVE_BY_ROOM_TYPE.get(room_type or "", 0.20)
    base_circulation_reserve_cm2 = room_area_cm2 * reserve_fraction
    confirmed_doors = sum(
        1
        for obstacle in obstacles or []
        if _has_confirmed_geometry(str(obstacle), "door")
        or _has_confirmed_geometry(str(obstacle), "entrance")
    )
    confirmed_paths = sum(
        1
        for path in circulation_paths or []
        if _has_confirmed_geometry(str(path), "circulation")
        or _has_confirmed_geometry(str(path), "path")
        or _has_confirmed_geometry(str(path), "route")
    )
    # Without coordinates these reserves influence density only; they never
    # fabricate a hard door/circulation collision.
    confirmed_reserve_cm2 = min(
        confirmed_doors * 8000 + confirmed_paths * 6000,
        room_area_cm2 * 0.12,
    )
    circulation_reserve_cm2 = min(
        base_circulation_reserve_cm2 + confirmed_reserve_cm2,
        room_area_cm2 * 0.45,
    )
    usable_area_cm2 = max(room_area_cm2 - circulation_reserve_cm2, 1.0)
    density_ratio = effective_area_cm2 / usable_area_cm2

    if violations:
        density_classification = DensityClassification.PHYSICALLY_IMPLAUSIBLE
        status = CompositionFitStatus.INVALID
        confirmation_allowed = False
    elif density_ratio <= 0.45:
        density_classification = DensityClassification.COMFORTABLE
        status = CompositionFitStatus.VALID
        confirmation_allowed = True
    elif density_ratio <= 0.70:
        density_classification = DensityClassification.MODERATELY_DENSE
        status = CompositionFitStatus.VALID_WITH_WARNINGS
        confirmation_allowed = True
        warnings.append(
            "Warning: moderately dense selection; confirmation and Generate remain allowed."
        )
    else:
        density_classification = DensityClassification.HIGH_DENSITY
        status = CompositionFitStatus.VALID_WITH_WARNINGS
        confirmation_allowed = True
        if effective_area_cm2 > usable_area_cm2:
            warnings.append(
                "Warning: the selection exceeds the advisory room-wide circulation reserve, "
                "but Generate is allowed because circulation may run behind or around "
                "wall-aligned furniture. The renderer must preserve every product's exact "
                "construction and the compliance check will reject structural changes or "
                "an obviously blocked primary route."
            )
        else:
            warnings.append(
                "Warning: dense selection, but Generate is allowed. Review the largest products or let GPT Image 2 attempt the best realistic arrangement."
            )

    if missing_footprints and status != CompositionFitStatus.INVALID:
        status = CompositionFitStatus.UNCERTAIN_GEOMETRY
        warnings.append(
            "Usable footprint dimensions are missing for product IDs: "
            + ", ".join(str(product_id) for product_id in missing_footprints)
            + "."
        )

    if obstacles and status != CompositionFitStatus.INVALID:
        if confirmed_doors or confirmed_paths:
            warnings.append(
                "Confirmed openings contribute to the circulation reserve; unknown coordinates remain renderer safety constraints rather than a fabricated hard failure."
            )
        else:
            warnings.append(
                "Unconfirmed opening coordinates remain renderer safety constraints and do not fabricate a hard capacity failure."
            )

    largest_products = sorted(
        product_contributions,
        key=lambda product: (
            float(product["effective_footprint_m2"]),
            float(product["raw_footprint_m2"]),
        ),
        reverse=True,
    )[:5]

    return CompositionFitResult(
        status=status,
        density_classification=density_classification,
        confirmation_allowed=confirmation_allowed,
        room_type=room_type,
        room_dimensions_cm={
            "length_cm": room_dimensions.length_cm,
            "width_cm": room_dimensions.width_cm,
            "height_cm": room_dimensions.height_cm,
        },
        room_floor_area_m2=round(room_area_cm2 / 10000, 2),
        usable_floor_area_m2=round(usable_area_cm2 / 10000, 2),
        circulation_reserve_area_m2=round(circulation_reserve_cm2 / 10000, 2),
        raw_product_footprint_m2=round(raw_area_cm2 / 10000, 2),
        major_furniture_footprint_m2=round(major_area_cm2 / 10000, 2),
        overlap_adjusted_effective_footprint_m2=round(
            effective_area_cm2 / 10000, 2
        ),
        effective_occupancy_ratio=round(density_ratio, 3),
        estimated_required_area_m2=round(effective_area_cm2 / 10000, 2),
        estimated_density_ratio=round(density_ratio, 3),
        excluded_from_independent_floor_area=excluded_items,
        overlap_adjustments=overlap_adjustments,
        largest_space_consuming_products=largest_products,
        violations=violations,
        warnings=warnings,
        assumptions=[
            "Rugs overlap the seating zone and add no independent occupied floor area.",
            "Coffee and side tables share functional space with the seating composition.",
            "Wall-mounted televisions, wall products, and small accessories add no independent floor area.",
            "Room-type circulation reserve is used only to classify density when exact product coordinates are unknown.",
            "A high density ratio is advisory; it does not prove a collision because circulation may run behind or around wall-aligned furniture.",
        ],
    )


def reduce_overcrowded_composition(
    *,
    selected_items: list[dict],
    room_dimensions: RoomDimensions | None,
    obstacles: list[str] | None = None,
) -> CompositionReductionResult:
    """Compatibility helper that never mutates a selected inventory.

    Capacity validation reports warnings or a hard conflict. Product removal
    must be an explicit user edit, so exact IDs and quantities are preserved.
    """

    selected = [dict(item) for item in selected_items]
    validation = validate_composition_fit(
        selected_items=selected,
        room_dimensions=room_dimensions,
        obstacles=obstacles,
    )
    return CompositionReductionResult(
        selected_items=selected,
        removed_items=[],
        validation=validation,
        hard_conflict=validation.status == CompositionFitStatus.INVALID,
    )
