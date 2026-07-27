"""Conservative architectural constraints derived from room-analysis facts.

The room image and measured dimensions remain authoritative.  This module
never turns qualitative prose into invented coordinates: bounds exist only
when an upstream source supplied them explicitly.  Qualitative observations
are still useful, but are marked ESTIMATED or UNCERTAIN for downstream gates.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models import RoomDimensions
from app.services.room_analysis_service import RoomAnalysis


ARCHITECTURAL_CONSTRAINT_VERSION = "2026-07-18.1-usable-wall-media-classification"


class GeometryConfidence(StrEnum):
    KNOWN = "KNOWN"
    ESTIMATED = "ESTIMATED"
    UNCERTAIN = "UNCERTAIN"


class ArchitecturalEvidence(StrEnum):
    OBSERVED_CONFIRMED = "OBSERVED_CONFIRMED"
    OBSERVED_UNCERTAIN = "OBSERVED_UNCERTAIN"
    INFERRED = "INFERRED"
    NOT_VISIBLE = "NOT_VISIBLE"


class SpatialRect(BaseModel):
    """Axis-aligned floor rectangle in a documented room coordinate system.

    ``x`` runs left-to-right across ``RoomDimensions.width_cm``. ``y`` runs
    near-to-far across ``RoomDimensions.length_cm``.
    """

    x_cm: float = Field(
        ge=0,
        description="Left edge measured from the room's left wall.",
    )
    y_cm: float = Field(
        ge=0,
        description="Near edge measured from the room's near/front boundary.",
    )
    width_cm: float = Field(
        gt=0,
        description="Left-to-right extent along the room-width/x axis.",
    )
    depth_cm: float = Field(
        gt=0,
        description="Near-to-far extent along the room-length/y axis.",
    )

    @property
    def right_cm(self) -> float:
        return self.x_cm + self.width_cm

    @property
    def far_cm(self) -> float:
        return self.y_cm + self.depth_cm


ZoneKind = Literal[
    "door",
    "door_swing",
    "window",
    "no_block",
    "circulation",
    "usable_wall",
    "tv_wall",
    "storage_wall",
    "seating",
    "corner",
    "focal_wall",
]


class ArchitecturalZone(BaseModel):
    zone_id: str = Field(min_length=1, max_length=120)
    zone_type: ZoneKind
    confidence: GeometryConfidence
    evidence: ArchitecturalEvidence = ArchitecturalEvidence.OBSERVED_UNCERTAIN
    description: str = Field(min_length=1, max_length=1000)
    bounds: SpatialRect | None = None
    axis: Literal["horizontal", "vertical"] | None = None
    is_primary: bool = False
    target_clearance_cm: float | None = Field(default=None, gt=0)
    minimum_clearance_cm: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def known_zones_need_bounds(self):
        if self.confidence == GeometryConfidence.KNOWN and self.bounds is None:
            raise ValueError("KNOWN architectural zones require explicit bounds.")
        return self


class ArchitecturalConstraintMap(BaseModel):
    version: str = ARCHITECTURAL_CONSTRAINT_VERSION
    room_dimensions_cm: RoomDimensions | None = None
    door_zones: list[ArchitecturalZone] = Field(default_factory=list)
    door_swing_zones: list[ArchitecturalZone] = Field(default_factory=list)
    window_zones: list[ArchitecturalZone] = Field(default_factory=list)
    no_block_zones: list[ArchitecturalZone] = Field(default_factory=list)
    circulation_corridors: list[ArchitecturalZone] = Field(default_factory=list)
    usable_walls: list[ArchitecturalZone] = Field(default_factory=list)
    tv_suitable_walls: list[ArchitecturalZone] = Field(default_factory=list)
    storage_suitable_walls: list[ArchitecturalZone] = Field(default_factory=list)
    seating_zones: list[ArchitecturalZone] = Field(default_factory=list)
    corner_zones: list[ArchitecturalZone] = Field(default_factory=list)
    focal_wall_candidates: list[ArchitecturalZone] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)

    @property
    def has_tv_suitable_wall(self) -> bool:
        return bool(self.tv_suitable_walls)

    def all_protected_zones(self) -> list[ArchitecturalZone]:
        return [
            *self.door_zones,
            *self.door_swing_zones,
            *self.window_zones,
            *self.no_block_zones,
            *self.circulation_corridors,
        ]


def _contains_any(value: str, tokens: tuple[str, ...]) -> bool:
    normalized = value.casefold()
    return any(token in normalized for token in tokens)


def _qualitative_zone(
    *,
    zone_id: str,
    zone_type: ZoneKind,
    description: str,
    confidence: GeometryConfidence = GeometryConfidence.UNCERTAIN,
    evidence: ArchitecturalEvidence = ArchitecturalEvidence.OBSERVED_UNCERTAIN,
    is_primary: bool = False,
    target_clearance_cm: float | None = None,
    minimum_clearance_cm: float | None = None,
) -> ArchitecturalZone:
    return ArchitecturalZone(
        zone_id=zone_id,
        zone_type=zone_type,
        confidence=confidence,
        evidence=evidence,
        description=description,
        is_primary=is_primary,
        target_clearance_cm=target_clearance_cm,
        minimum_clearance_cm=minimum_clearance_cm,
    )


def build_architectural_constraint_map(
    *,
    room_analysis: RoomAnalysis,
    room_dimensions: RoomDimensions | None = None,
    explicit_zones: list[ArchitecturalZone] | None = None,
) -> ArchitecturalConstraintMap:
    """Build structured zones without fabricating geometry.

    ``explicit_zones`` is intended for measured/frontend geometry.  The
    legacy RoomAnalysis strings remain useful descriptions, but receive no
    rectangle and therefore force uncertainty-aware validation.
    """

    dimensions = room_dimensions or room_analysis.provided_dimensions_cm
    result = ArchitecturalConstraintMap(room_dimensions_cm=dimensions)

    target_lists = {
        "door": result.door_zones,
        "door_swing": result.door_swing_zones,
        "window": result.window_zones,
        "no_block": result.no_block_zones,
        "circulation": result.circulation_corridors,
        "usable_wall": result.usable_walls,
        "tv_wall": result.tv_suitable_walls,
        "storage_wall": result.storage_suitable_walls,
        "seating": result.seating_zones,
        "corner": result.corner_zones,
        "focal_wall": result.focal_wall_candidates,
    }
    for zone in explicit_zones or []:
        if zone.confidence == GeometryConfidence.KNOWN and zone.bounds is not None:
            zone = zone.model_copy(
                update={"evidence": ArchitecturalEvidence.OBSERVED_CONFIRMED}
            )
        target_lists[zone.zone_type].append(zone)

    for index, obstacle in enumerate(room_analysis.obstacles, start=1):
        description = str(obstacle).strip()
        if not description:
            continue
        is_window = _contains_any(description, ("window", "glazing", "balcony"))
        is_door = _contains_any(description, ("door", "entry", "entrance")) or (
            "opening" in description.casefold() and not is_window
        )
        if is_door:
            door_not_visible = _contains_any(
                description,
                ("not visible", "outside the camera", "outside camera", "unseen"),
            )
            result.door_zones.append(
                _qualitative_zone(
                    zone_id=f"observed_door_{index}",
                    zone_type="door",
                    description=description,
                    evidence=(
                        ArchitecturalEvidence.NOT_VISIBLE
                        if door_not_visible
                        else ArchitecturalEvidence.OBSERVED_UNCERTAIN
                    ),
                )
            )
            result.door_swing_zones.append(
                _qualitative_zone(
                    zone_id=f"conservative_door_swing_{index}",
                    zone_type="door_swing",
                    description=(
                        "Conservative clearance around an observed entry/opening; "
                        "the exact hinge and swing direction are unknown."
                    ),
                    evidence=(
                        ArchitecturalEvidence.NOT_VISIBLE
                        if door_not_visible
                        else ArchitecturalEvidence.INFERRED
                    ),
                )
            )
            result.uncertainty_notes.append(
                f"Door/opening '{description}' has no exact offset or swing direction."
            )
        if is_window:
            window_not_visible = _contains_any(
                description,
                ("not visible", "outside the camera", "outside camera", "unseen"),
            )
            result.window_zones.append(
                _qualitative_zone(
                    zone_id=f"observed_window_{index}",
                    zone_type="window",
                    description=description,
                    evidence=(
                        ArchitecturalEvidence.NOT_VISIBLE
                        if window_not_visible
                        else ArchitecturalEvidence.OBSERVED_UNCERTAIN
                    ),
                )
            )
            result.uncertainty_notes.append(
                f"Window '{description}' has no exact sill height or floor-plane offset."
            )

    for index, path in enumerate(room_analysis.circulation_paths, start=1):
        description = str(path).strip()
        if not description:
            continue
        route_not_visible = _contains_any(
            description,
            ("not visible", "outside the camera", "outside camera", "unseen"),
        )
        route_inferred = _contains_any(
            description,
            ("cleaning", "likely", "assumed", "approach", "should remain"),
        )
        evidence = (
            ArchitecturalEvidence.NOT_VISIBLE
            if route_not_visible
            else ArchitecturalEvidence.INFERRED
            if route_inferred
            else ArchitecturalEvidence.OBSERVED_UNCERTAIN
        )
        result.circulation_corridors.append(
            _qualitative_zone(
                zone_id=f"observed_circulation_{index}",
                zone_type="circulation",
                description=description,
                evidence=evidence,
                is_primary=(
                    evidence
                    in {
                        ArchitecturalEvidence.OBSERVED_CONFIRMED,
                        ArchitecturalEvidence.OBSERVED_UNCERTAIN,
                    }
                    and _contains_any(
                        description, ("main", "primary", "entry", "door")
                    )
                ),
                target_clearance_cm=85,
                minimum_clearance_cm=60,
            )
        )

    for index, wall in enumerate(room_analysis.usable_walls, start=1):
        description = str(wall).strip()
        if not description:
            continue
        wall_zone = _qualitative_zone(
            zone_id=f"observed_usable_wall_{index}",
            zone_type="usable_wall",
            description=description,
            confidence=GeometryConfidence.ESTIMATED,
        )
        result.usable_walls.append(wall_zone)
        result.focal_wall_candidates.append(
            wall_zone.model_copy(
                update={
                    "zone_id": f"focal_candidate_{index}",
                    "zone_type": "focal_wall",
                }
            )
        )

        unsuitable = _contains_any(
            description,
            ("window", "door", "opening", "radiator", "glare", "blocked"),
        )
        explicitly_solid = _contains_any(
            description,
            (
                "tv",
                "media",
                "solid",
                "blank",
                "continuous",
                "uninterrupted",
                "unobstructed",
                "window-free",
            ),
        )
        if explicitly_solid and not unsuitable:
            result.tv_suitable_walls.append(
                wall_zone.model_copy(
                    update={
                        "zone_id": f"tv_suitable_wall_{index}",
                        "zone_type": "tv_wall",
                    }
                )
            )
        if _contains_any(description, ("storage", "cabinet", "solid", "uninterrupted")) and not unsuitable:
            result.storage_suitable_walls.append(
                wall_zone.model_copy(
                    update={
                        "zone_id": f"storage_suitable_wall_{index}",
                        "zone_type": "storage_wall",
                    }
                )
            )

    if room_analysis.corner_zone_available is True:
        result.corner_zones.append(
            _qualitative_zone(
                zone_id="observed_corner_zone",
                zone_type="corner",
                description="Room analysis reports a visually usable corner zone.",
                confidence=GeometryConfidence.ESTIMATED,
            )
        )

    if result.door_zones and not result.circulation_corridors:
        result.circulation_corridors.append(
            _qualitative_zone(
                zone_id="conservative_entry_route",
                zone_type="circulation",
                description=(
                    "Conservative entry route retained because a door/opening was "
                    "observed but its route geometry is not measured."
                ),
                evidence=ArchitecturalEvidence.INFERRED,
                is_primary=True,
                target_clearance_cm=85,
                minimum_clearance_cm=60,
            )
        )

    if not dimensions:
        result.uncertainty_notes.append(
            "Measured room length and width are unavailable."
        )
    if result.door_zones and not any(zone.bounds for zone in result.door_zones):
        result.uncertainty_notes.append(
            "Door safety must use conservative qualitative clearance until measured bounds are supplied."
        )

    return result
