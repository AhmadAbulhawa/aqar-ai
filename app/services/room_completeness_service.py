"""Deterministic room-function policy layered above design slots.

Completeness is intentionally expressed as functional zones and conditional
rules, not as a universal category checklist.  The result adds only missing
requirements that remain compatible with architectural and density evidence.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from app.models import RoomDimensions
from app.room_templates import ROOM_TEMPLATES
from app.services.architectural_constraint_service import (
    ArchitecturalConstraintMap,
)
from app.services.catalog_compatibility_service import is_design_eligible
from app.services.composition_fit_service import (
    CompositionFitResult,
    CompositionFitStatus,
    validate_composition_fit,
)
from app.services.design_director_service import DesignStrategy
from app.services.room_analysis_service import RoomAnalysis
from app.services.unified_selection_service import DesignRequirementSlot


ROOM_COMPLETENESS_VERSION = "2026-07-18.2-all-room-functional-policy"

CompletenessLevel = Literal[
    "required",
    "conditionally_required",
    "recommended",
    "optional",
    "incompatible",
]
ZoneStatus = Literal["fulfilled", "missing", "added", "skipped", "incompatible"]


class FunctionalZoneAssessment(BaseModel):
    functional_zone: str
    policy_level: CompletenessLevel
    status: ZoneStatus
    fulfilled_categories: list[str] = Field(default_factory=list)
    missing_categories: list[str] = Field(default_factory=list)
    addition_slot_keys: list[str] = Field(default_factory=list)
    addition_attempted: bool = False
    lower_priority_reduction_attempted: bool = False
    lower_priority_products_removed: bool = False
    removed_product_ids: list[int] = Field(default_factory=list)
    reason: str


class RoomCompletenessResult(BaseModel):
    version: str = ROOM_COMPLETENESS_VERSION
    room_type: str
    functional_intent: str
    status: Literal["complete", "complete_with_omissions", "incomplete"]
    zones: list[FunctionalZoneAssessment] = Field(default_factory=list)
    additional_requirements: list[DesignRequirementSlot] = Field(
        default_factory=list
    )
    missing_essential_zones: list[str] = Field(default_factory=list)
    intentional_omissions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MediaPriorityReductionResult(BaseModel):
    selected_items: list[dict] = Field(default_factory=list)
    removed_lower_priority_items: list[dict] = Field(default_factory=list)
    removed_media_items: list[dict] = Field(default_factory=list)
    validation: CompositionFitResult
    lower_priority_reduction_attempted: bool = False
    hard_conflict: bool = False
    media_omission_reason: str | None = None


def _blueprint_value(blueprint: object | None, field: str, default=None):
    if blueprint is None:
        return default
    if isinstance(blueprint, dict):
        return blueprint.get(field, default)
    return getattr(blueprint, field, default)


def _infer_living_room_intent(
    *,
    design_strategy: DesignStrategy | None,
    blueprint: object | None,
    explicit_intent: str | None,
    user_prompt: str | None,
) -> str:
    aliases = {
        "media": "media_room",
        "family_media": "family_living",
        "conversation_only": "conversation_room",
        "formal": "formal_living",
        "lounge_no_media": "conversation_room",
    }
    if explicit_intent:
        normalized = explicit_intent.strip().casefold().replace(" ", "_")
        return aliases.get(normalized, normalized)

    prompt = (user_prompt or "").casefold()
    if any(
        value in prompt
        for value in (
            "no tv",
            "without tv",
            "exclude tv",
            "no television",
            "without television",
            "exclude television",
            "no media",
            "without media",
            "conversation only",
        )
    ):
        return "conversation_room"
    if "formal" in prompt:
        return "formal_living"
    if any(value in prompt for value in ("media room", "television", " tv", "with tv")):
        return "media_room"
    if "family" in prompt:
        return "family_living"

    if design_strategy is not None:
        if (
            design_strategy.focal_point == "tv_wall"
            or design_strategy.layout.final_layout_strategy == "media_wall_focused"
        ):
            return "media_room"
        if design_strategy.focal_point in {"fireplace", "window_view", "art_wall"}:
            return "non_media_focal"

    focal_point = str(_blueprint_value(blueprint, "focal_point", "none") or "none")
    layout_type = str(_blueprint_value(blueprint, "layout_type", "") or "")
    summary = str(_blueprint_value(blueprint, "composition_summary", "") or "").casefold()
    normalized_focal = focal_point.casefold()
    if (
        focal_point == "tv_wall"
        or layout_type == "media_wall_focused"
        or "television" in normalized_focal
        or "tv" in normalized_focal
    ):
        return "media_room"
    if "conversation-only" in summary or "conversation only" in summary:
        return "conversation_room"
    if (
        focal_point in {"fireplace", "window_view", "art_wall"}
        or any(
            token in normalized_focal
            for token in ("fireplace", "window", "view", "art", "artwork", "canvas")
        )
    ):
        return "non_media_focal"
    return "general_living"


def _active_catalog_categories(catalog_items: list[dict]) -> set[str]:
    return {
        str(item.get("category"))
        for item in catalog_items
        if is_design_eligible(item)
    }


def _choose_compatible_media_pair(
    catalog_items: list[dict],
) -> tuple[dict, dict] | None:
    televisions = [
        item
        for item in catalog_items
        if item.get("category") == "television"
        and is_design_eligible(item)
        and (
            not (item.get("room_types") or [])
            or "living_room" in (item.get("room_types") or [])
        )
    ]
    stands = [
        item
        for item in catalog_items
        if item.get("category") == "tv_stand"
        and is_design_eligible(item)
        and (
            not (item.get("room_types") or [])
            or "living_room" in (item.get("room_types") or [])
        )
    ]
    ranked: list[tuple[float, int, int, dict, dict]] = []
    for television in televisions:
        try:
            television_width = float(television.get("width_cm") or 0)
        except (TypeError, ValueError):
            television_width = 0
        for stand in stands:
            try:
                stand_width = float(stand.get("width_cm") or 0)
            except (TypeError, ValueError):
                stand_width = 0
            if television_width and stand_width and television_width > stand_width * 1.2:
                continue
            proportional_gap = (
                abs(stand_width - television_width) if television_width and stand_width else 999
            )
            same_style_penalty = (
                0
                if str(television.get("style") or "")
                == str(stand.get("style") or "")
                else 100
            )
            ranked.append(
                (
                    proportional_gap + same_style_penalty,
                    int(television["id"]),
                    int(stand["id"]),
                    television,
                    stand,
                )
            )
    if not ranked:
        return None
    _, _, _, television, stand = min(ranked, key=lambda entry: entry[:3])
    return television, stand


def _zone(
    functional_zone: str,
    level: CompletenessLevel,
    status: ZoneStatus,
    reason: str,
    *,
    fulfilled: list[str] | None = None,
    missing: list[str] | None = None,
    additions: list[str] | None = None,
    addition_attempted: bool = False,
    lower_priority_reduction_attempted: bool = False,
    lower_priority_products_removed: bool = False,
    removed_product_ids: list[int] | None = None,
) -> FunctionalZoneAssessment:
    return FunctionalZoneAssessment(
        functional_zone=functional_zone,
        policy_level=level,
        status=status,
        fulfilled_categories=fulfilled or [],
        missing_categories=missing or [],
        addition_slot_keys=additions or [],
        addition_attempted=addition_attempted,
        lower_priority_reduction_attempted=lower_priority_reduction_attempted,
        lower_priority_products_removed=lower_priority_products_removed,
        removed_product_ids=removed_product_ids or [],
        reason=reason,
    )


def _analyze_generic_room_completeness(
    *,
    room_type: str,
    current_selected_items: list[dict],
    catalog_items: list[dict],
    excluded_categories: set[str],
    allow_additions: bool,
) -> RoomCompletenessResult:
    """Apply the declared room template to every non-living room type."""

    room_policy = ROOM_TEMPLATES.get(room_type)
    if room_policy is None:
        return RoomCompletenessResult(
            room_type=room_type,
            functional_intent="unsupported_room_type",
            status="incomplete",
            missing_essential_zones=["room_policy"],
            warnings=[f"No deterministic room policy exists for {room_type}."],
        )

    category_quantities = Counter()
    for item in current_selected_items:
        category_quantities[str(item.get("category"))] += max(
            int(item.get("quantity", 1)), 1
        )
    catalog_categories = _active_catalog_categories(catalog_items)
    zones: list[FunctionalZoneAssessment] = []
    additions: list[DesignRequirementSlot] = []

    for category in room_policy.get("required", []):
        zone_name = f"required_{category}"
        if category_quantities[category] > 0:
            zones.append(
                _zone(
                    zone_name,
                    "required",
                    "fulfilled",
                    f"The required {category} function is present.",
                    fulfilled=[category],
                )
            )
            continue
        if category in excluded_categories:
            zones.append(
                _zone(
                    zone_name,
                    "incompatible",
                    "incompatible",
                    f"The required {category} category is explicitly excluded.",
                    missing=[category],
                )
            )
            continue
        if allow_additions and category in catalog_categories:
            requirement = DesignRequirementSlot(
                slot_key=f"completeness_{room_type}_{category}",
                category=category,
                required_quantity=1,
                importance="essential",
                visual_role=f"required {room_type.replace('_', ' ')} function",
                placement_zone="safe primary functional zone",
                functional_zone=zone_name,
                completeness_level="required",
            )
            additions.append(requirement)
            zones.append(
                _zone(
                    zone_name,
                    "required",
                    "added",
                    f"A complete {room_type.replace('_', ' ')} requires {category}.",
                    missing=[category],
                    additions=[requirement.slot_key],
                    addition_attempted=True,
                )
            )
        else:
            zones.append(
                _zone(
                    zone_name,
                    "required",
                    "missing",
                    f"No eligible active {category} product is available.",
                    missing=[category],
                    addition_attempted=allow_additions,
                )
            )

    for index, group in enumerate(room_policy.get("one_of", []), start=1):
        normalized_group = [str(category) for category in group]
        fulfilled = [
            category
            for category in normalized_group
            if category_quantities[category] > 0
        ]
        zone_name = f"required_choice_{index}"
        if fulfilled:
            zones.append(
                _zone(
                    zone_name,
                    "required",
                    "fulfilled",
                    "The required functional choice is present.",
                    fulfilled=fulfilled,
                )
            )
            continue
        eligible = [
            category
            for category in normalized_group
            if category not in excluded_categories and category in catalog_categories
        ]
        if allow_additions and eligible:
            category = eligible[0]
            requirement = DesignRequirementSlot(
                slot_key=f"completeness_{room_type}_choice_{index}_{category}",
                category=category,
                required_quantity=1,
                importance="essential",
                visual_role=f"required {room_type.replace('_', ' ')} functional choice",
                placement_zone="safe primary functional zone",
                functional_zone=zone_name,
                completeness_level="required",
            )
            additions.append(requirement)
            zones.append(
                _zone(
                    zone_name,
                    "required",
                    "added",
                    "One eligible product was selected for the required functional choice.",
                    missing=normalized_group,
                    additions=[requirement.slot_key],
                    addition_attempted=True,
                )
            )
        else:
            zones.append(
                _zone(
                    zone_name,
                    "required",
                    "missing",
                    "No eligible active product can satisfy the required functional choice.",
                    missing=normalized_group,
                    addition_attempted=allow_additions,
                )
            )

    missing_essential = [
        zone.functional_zone
        for zone in zones
        if zone.status in {"missing", "incompatible"}
    ]
    return RoomCompletenessResult(
        room_type=room_type,
        functional_intent=f"complete_{room_type}",
        status="incomplete" if missing_essential else "complete",
        zones=zones,
        additional_requirements=additions,
        missing_essential_zones=missing_essential,
    )


def analyze_room_completeness(
    *,
    room_type: str,
    room_analysis: RoomAnalysis,
    current_selected_items: list[dict],
    architectural_constraints: ArchitecturalConstraintMap,
    catalog_items: list[dict],
    room_dimensions: RoomDimensions | None = None,
    design_strategy: DesignStrategy | None = None,
    blueprint: object | None = None,
    slot_satisfaction: list[object] | None = None,
    excluded_categories: set[str] | None = None,
    explicit_intent: str | None = None,
    user_prompt: str | None = None,
    density_target: str = "balanced",
    allow_additions: bool = True,
) -> RoomCompletenessResult:
    """Return missing functional zones and safe deterministic additions."""

    del slot_satisfaction  # Current product quantities are authoritative here.
    exclusions = {str(value) for value in (excluded_categories or set())}
    if room_type != "living_room":
        return _analyze_generic_room_completeness(
            room_type=room_type,
            current_selected_items=current_selected_items,
            catalog_items=catalog_items,
            excluded_categories=exclusions,
            allow_additions=allow_additions,
        )

    category_quantities = Counter()
    for item in current_selected_items:
        category_quantities[str(item.get("category"))] += max(
            int(item.get("quantity", 1)), 1
        )

    catalog_categories = _active_catalog_categories(catalog_items)
    compatible_media_pair = _choose_compatible_media_pair(catalog_items)
    intent = _infer_living_room_intent(
        design_strategy=design_strategy,
        blueprint=blueprint,
        explicit_intent=explicit_intent,
        user_prompt=user_prompt,
    )
    raw_blueprint_focal = str(
        _blueprint_value(blueprint, "focal_point", "") or ""
    ).casefold()
    if (
        intent == "non_media_focal"
        and any(
            token in raw_blueprint_focal
            for token in ("art", "artwork", "painting", "canvas")
        )
        and not category_quantities["wall_art"]
    ):
        # An unresolved artwork slot is not yet a functioning focal zone.
        intent = "general_living"
    zones: list[FunctionalZoneAssessment] = []
    additions: list[DesignRequirementSlot] = []
    intentional_omissions: list[str] = []
    warnings: list[str] = []

    seating_categories = [
        category for category in ("sofa", "armchair") if category_quantities[category]
    ]
    if seating_categories:
        zones.append(
            _zone(
                "seating",
                "required",
                "fulfilled",
                "The room has a coherent seating inventory to anchor the layout.",
                fulfilled=seating_categories,
            )
        )
    elif not allow_additions:
        zones.append(
            _zone(
                "seating",
                "required",
                "missing",
                "The final immutable inventory has no primary seating product.",
                missing=["sofa"],
            )
        )
    elif "sofa" in exclusions or "sofa" not in catalog_categories:
        zones.append(
            _zone(
                "seating",
                "incompatible",
                "incompatible",
                "A living-room seating zone cannot be completed from allowed active products.",
                missing=["sofa"],
            )
        )
    else:
        requirement = DesignRequirementSlot(
            slot_key="completeness_primary_seating",
            category="sofa",
            required_quantity=1,
            importance="essential",
            visual_role="primary functional seating anchor",
            placement_zone="safe main seating zone",
            functional_zone="seating",
            completeness_level="required",
        )
        additions.append(requirement)
        zones.append(
            _zone(
                "seating",
                "required",
                "added",
                "A living room requires at least one usable seating anchor.",
                missing=["sofa"],
                additions=[requirement.slot_key],
            )
        )

    central_fulfilled = [
        category
        for category in ("coffee_table", "rug")
        if category_quantities[category]
    ]
    central_missing: list[str] = []
    if seating_categories and not category_quantities["coffee_table"]:
        central_missing.append("coffee_table")
    if seating_categories and not category_quantities["rug"]:
        central_missing.append("rug")

    central_additions: list[str] = []
    if allow_additions and "coffee_table" in central_missing and "coffee_table" not in exclusions and "coffee_table" in catalog_categories:
        requirement = DesignRequirementSlot(
            slot_key="completeness_central_coffee_table",
            category="coffee_table",
            required_quantity=1,
            importance="supporting",
            visual_role="reachable central surface for the seating group",
            placement_zone="center of the main seating group",
            relationships=["Maintain practical sofa-to-table clearance."],
            functional_zone="central",
            completeness_level="conditionally_required",
        )
        additions.append(requirement)
        central_additions.append(requirement.slot_key)
    if (
        allow_additions
        and "rug" in central_missing
        and "rug" not in exclusions
        and "rug" in catalog_categories
        and density_target != "minimal"
    ):
        requirement = DesignRequirementSlot(
            slot_key="completeness_central_rug",
            category="rug",
            required_quantity=1,
            importance="supporting",
            visual_role="visual anchor for the main seating group",
            placement_zone="beneath and connecting the seating composition",
            functional_zone="central",
            completeness_level="recommended",
        )
        additions.append(requirement)
        central_additions.append(requirement.slot_key)
    zones.append(
        _zone(
            "central",
            "conditionally_required",
            "fulfilled" if not central_missing else "added" if central_additions else "skipped",
            (
                "The seating group has its central functional layer."
                if not central_missing
                else "Only central layers compatible with catalog availability and density were considered."
            ),
            fulfilled=central_fulfilled,
            missing=central_missing,
            additions=central_additions,
        )
    )

    composition = validate_composition_fit(
        selected_items=current_selected_items,
        room_dimensions=room_dimensions or room_analysis.provided_dimensions_cm,
        obstacles=room_analysis.obstacles,
        circulation_paths=room_analysis.circulation_paths,
        room_type=room_type,
    )
    density_ratio = composition.estimated_density_ratio
    dense = density_ratio is not None and density_ratio >= 0.52

    normalized_prompt = (user_prompt or "").casefold()
    prompt_excludes_media = any(
        value in normalized_prompt
        for value in (
            "no tv",
            "without tv",
            "exclude tv",
            "no television",
            "without television",
            "exclude television",
            "no media",
            "without media",
        )
    )
    media_explicitly_excluded = bool(
        {"television", "tv_stand"} & exclusions
    ) or prompt_excludes_media
    media_explicitly_requested = (
        not prompt_excludes_media
        and any(
            value in normalized_prompt
            for value in ("with tv", "add tv", "television", "media room")
        )
    )
    required_media_intent = intent in {
        "media_room",
        "general_living",
        "family_living",
        "non_media_focal",
    }
    optional_media_intent = intent in {
        "conversation_room",
        "formal_living",
    }
    if media_explicitly_requested:
        media_policy_level: CompletenessLevel = "required"
    elif required_media_intent:
        media_policy_level = "required"
    else:
        media_policy_level = "optional"

    has_tv = category_quantities["television"] > 0
    has_stand = any(
        item.get("category") == "tv_stand"
        and item.get("functional_zone") != "storage"
        for item in current_selected_items
    )
    if has_tv and has_stand:
        zones.append(
            _zone(
                "media",
                media_policy_level,
                "fulfilled",
                "A complete television/media-console pair is already included.",
                fulfilled=["television", "tv_stand"],
            )
        )
    elif has_tv != has_stand:
        missing = "tv_stand" if has_tv else "television"
        committed_item = next(
            (
                item
                for item in current_selected_items
                if item.get("category") == ("television" if has_tv else "tv_stand")
            ),
            None,
        )
        allowed_product_ids: list[int] = []
        if committed_item is not None:
            committed_width = float(committed_item.get("width_cm") or 0)
            compatible_partners = []
            for item in catalog_items:
                if (
                    item.get("category") != missing
                    or not is_design_eligible(item)
                ):
                    continue
                candidate_width = float(item.get("width_cm") or 0)
                if missing == "tv_stand":
                    compatible = not committed_width or not candidate_width or committed_width <= candidate_width * 1.2
                else:
                    compatible = not committed_width or not candidate_width or candidate_width <= committed_width * 1.2
                if compatible:
                    compatible_partners.append(int(item["id"]))
            allowed_product_ids = sorted(compatible_partners)
        if (
            allow_additions
            and missing not in exclusions
            and missing in catalog_categories
            and allowed_product_ids
        ):
            requirement = DesignRequirementSlot(
                slot_key=f"completeness_media_{missing}",
                category=missing,
                required_quantity=1,
                importance="essential",
                visual_role="compatible missing half of the committed media pair",
                placement_zone="safe TV-suitable media wall",
                relationships=["Pair television and media console as one functional zone."],
                functional_zone="media",
                functional_group_id="living_room_media_pair",
                completeness_level="required",
                allowed_product_ids=allowed_product_ids,
            )
            additions.append(requirement)
            zones.append(
                _zone(
                    "media",
                    "required",
                    "added",
                    "A committed media product must not remain as an unusable half-pair.",
                    fulfilled=["television" if has_tv else "tv_stand"],
                    missing=[missing],
                    additions=[requirement.slot_key],
                    addition_attempted=True,
                )
            )
        else:
            zones.append(
                _zone(
                    "media",
                    "incompatible",
                    "incompatible",
                    "The existing half-pair cannot be completed from allowed active products.",
                    fulfilled=["television" if has_tv else "tv_stand"],
                    missing=[missing],
                )
            )
    else:
        seating_available = bool(seating_categories) or any(
            requirement.functional_zone == "seating" for requirement in additions
        )
        safe_wall = architectural_constraints.has_tv_suitable_wall
        compatible_pair = compatible_media_pair is not None
        blueprint_allowed = {
            str(value)
            for value in (_blueprint_value(blueprint, "functional_additions_allowed", []) or [])
        }
        media_allowed_by_template = (
            blueprint is None
            or not blueprint_allowed
            or bool({"television", "tv_stand"} & blueprint_allowed)
            or intent == "media_room"
            or media_explicitly_requested
        )

        reasons: list[str] = []
        if media_explicitly_excluded:
            reasons.append("television/media-console is excluded by user preference")
        if optional_media_intent and not media_explicitly_requested:
            reasons.append(f"functional intent {intent} makes media optional")
        if not safe_wall:
            reasons.append("no conservatively TV-suitable wall is known")
        if not seating_available:
            reasons.append("no viewing seating relationship is available")
        if not compatible_pair:
            reasons.append("the active catalog has no dimensionally plausible TV/media-console pair")
        if not media_allowed_by_template:
            reasons.append("the template does not allow a compatible media addition")
        if not allow_additions:
            reasons.append("the final immutable inventory contains no complete media pair")

        can_add_media = (
            allow_additions
            and media_policy_level == "required"
            and not reasons
        )
        if can_add_media:
            chosen_television, chosen_stand = compatible_media_pair
            media_requirements = [
                DesignRequirementSlot(
                    slot_key="completeness_media_television",
                    category="television",
                    required_quantity=1,
                    importance="essential",
                    visual_role="secondary or primary functional media focal point",
                    placement_zone="TV-suitable wall preserving the seating composition",
                    relationships=[
                        "Maintain a reasonable viewing relationship to primary seating.",
                        "Do not block doors, windows, or primary circulation.",
                    ],
                    functional_zone="media",
                    functional_group_id="living_room_media_pair",
                    completeness_level="required",
                    allowed_product_ids=[int(chosen_television["id"])],
                ),
                DesignRequirementSlot(
                    slot_key="completeness_media_console",
                    category="tv_stand",
                    required_quantity=1,
                    importance="essential",
                    visual_role="compatible support console for the television",
                    placement_zone="same safe media wall as the television",
                    relationships=[
                        "Pair with the selected television.",
                        "Preserve console access and circulation.",
                    ],
                    functional_zone="media",
                    functional_group_id="living_room_media_pair",
                    completeness_level="required",
                    allowed_product_ids=[int(chosen_stand["id"])],
                ),
            ]
            additions.extend(media_requirements)
            zones.append(
                _zone(
                    "media",
                    "required",
                    "added",
                    (
                        f"The {intent} policy requires a television and TV stand by default. "
                        "The atomic pair will be selected before capacity validation; "
                        "lower-priority system completions will be removed first if needed."
                    ),
                    missing=["television", "tv_stand"],
                    additions=[requirement.slot_key for requirement in media_requirements],
                    addition_attempted=True,
                )
            )
        else:
            if media_policy_level == "optional" and not reasons:
                reasons.append(f"functional intent {intent} makes media optional")
            reason = "Media was omitted because " + "; ".join(reasons) + "."
            intentional_omissions.append(reason)
            zones.append(
                _zone(
                    "media",
                    media_policy_level,
                    (
                        "missing"
                        if media_policy_level == "required"
                        and not media_explicitly_excluded
                        else "skipped"
                    ),
                    reason,
                    missing=["television", "tv_stand"],
                    addition_attempted=False,
                )
            )

    storage_fulfilled = [
        category
        for category in ("sideboard", "storage_cabinet", "bookshelf", "wall_shelf")
        if category_quantities[category]
    ]
    if any(
        item.get("category") == "tv_stand"
        and item.get("functional_zone") == "storage"
        for item in current_selected_items
    ):
        storage_fulfilled.append("tv_stand_as_storage")
    zones.append(
        _zone(
            "storage",
            "recommended",
            "fulfilled" if storage_fulfilled else "skipped",
            (
                "A storage function is present."
                if storage_fulfilled
                else "Storage remains recommended but is not forced without a strategy slot and safe storage wall."
            ),
            fulfilled=storage_fulfilled,
        )
    )

    lighting_fulfilled = [
        category for category in ("floor_lamp",) if category_quantities[category]
    ]
    lighting_additions: list[str] = []
    if (
        allow_additions
        and not lighting_fulfilled
        and density_target != "minimal"
        and architectural_constraints.corner_zones
        and "floor_lamp" not in exclusions
        and "floor_lamp" in catalog_categories
    ):
        requirement = DesignRequirementSlot(
            slot_key="completeness_accent_floor_lamp",
            category="floor_lamp",
            required_quantity=1,
            importance="supporting",
            visual_role="useful seating-adjacent accent light",
            placement_zone="safe available corner adjacent to seating",
            relationships=["Remain reachable without narrowing circulation."],
            functional_zone="lighting",
            completeness_level="recommended",
        )
        additions.append(requirement)
        lighting_additions.append(requirement.slot_key)
    zones.append(
        _zone(
            "lighting",
            "recommended",
            "fulfilled" if lighting_fulfilled else "added" if lighting_additions else "skipped",
            "Accent lighting is added only when a safe corner and density headroom are known.",
            fulfilled=lighting_fulfilled,
            missing=[] if lighting_fulfilled else ["floor_lamp"],
            additions=lighting_additions,
        )
    )

    side_surface_fulfilled = ["side_table"] if category_quantities["side_table"] else []
    zones.append(
        _zone(
            "supporting_surfaces",
            "optional",
            "fulfilled" if side_surface_fulfilled else "skipped",
            "Side tables are optional and are never added merely to increase product count.",
            fulfilled=side_surface_fulfilled,
        )
    )

    focal_categories = [
        category
        for category in ("television", "wall_art")
        if category_quantities[category]
    ]
    strategy_focal = design_strategy.focal_point if design_strategy else None
    blueprint_focal = _blueprint_value(blueprint, "focal_point", None)
    architectural_focal = strategy_focal or blueprint_focal
    if architectural_focal and architectural_focal != "none":
        focal_categories.append(str(architectural_focal))
    zones.append(
        _zone(
            "visual_focal",
            "conditionally_required",
            "fulfilled" if focal_categories else "skipped",
            (
                "A focal hierarchy is defined."
                if focal_categories
                else "The placement stage must establish a quiet focal hierarchy without forcing decor."
            ),
            fulfilled=focal_categories,
        )
    )

    missing_essential = [
        zone.functional_zone
        for zone in zones
        if zone.policy_level == "required" and zone.status in {"missing", "incompatible"}
    ]
    if missing_essential:
        status = "incomplete"
    elif intentional_omissions or any(zone.status == "skipped" for zone in zones):
        status = "complete_with_omissions"
    else:
        status = "complete"

    if dense:
        media_zone = next(
            (zone for zone in zones if zone.functional_zone == "media"),
            None,
        )
        if media_zone is not None and media_zone.addition_attempted:
            warnings.append(
                "Moderately high density did not veto required media; the exact inventory will remain editable and no product will be removed automatically."
            )
        else:
            warnings.append(
                "Non-required completeness additions were constrained because the draft already has high effective occupancy."
            )

    return RoomCompletenessResult(
        room_type=room_type,
        functional_intent=intent,
        status=status,
        zones=zones,
        additional_requirements=additions,
        missing_essential_zones=missing_essential,
        intentional_omissions=intentional_omissions,
        warnings=warnings,
    )


def reduce_composition_with_media_priority(
    *,
    selected_items: list[dict],
    completeness: RoomCompletenessResult,
    room_dimensions: RoomDimensions | None,
    obstacles: list[str] | None = None,
    circulation_paths: list[str] | None = None,
    room_type: str | None = None,
) -> MediaPriorityReductionResult:
    """Validate the exact draft and preserve every selected product.

    Media completeness remains diagnostic. Capacity warnings or an incomplete
    system pair are returned for explicit review and never trigger deletion.
    """

    media_zone = next(
        (zone for zone in completeness.zones if zone.functional_zone == "media"),
        None,
    )
    initial_validation = validate_composition_fit(
        selected_items=selected_items,
        room_dimensions=room_dimensions,
        obstacles=obstacles,
        circulation_paths=circulation_paths,
        room_type=room_type,
    )
    media_addition_attempted = bool(
        media_zone is not None and media_zone.addition_attempted
    )
    selected_media_items = [
        item
        for item in selected_items
        if item.get("category") in {"television", "tv_stand"}
    ]
    selected_media_categories = {
        str(item.get("category")) for item in selected_media_items
    }
    unresolved_atomic_pair_reason = None
    if media_addition_attempted and selected_media_categories != {
        "television",
        "tv_stand",
    }:
        unresolved_atomic_pair_reason = (
            "Media addition was attempted, but the resolver could not commit "
            "a valid television and TV-stand pair atomically. The selected "
            "inventory was preserved unchanged for explicit user review."
        )
    return MediaPriorityReductionResult(
        selected_items=[dict(item) for item in selected_items],
        validation=initial_validation,
        hard_conflict=(
            initial_validation.status == CompositionFitStatus.INVALID
            or bool(unresolved_atomic_pair_reason)
        ),
        media_omission_reason=unresolved_atomic_pair_reason,
    )


def finalize_media_zone_assessment(
    *,
    completeness: RoomCompletenessResult,
    selected_items: list[dict],
    reduction: MediaPriorityReductionResult,
) -> RoomCompletenessResult:
    """Make response metadata describe the preserved final inventory."""

    result = completeness.model_copy(deep=True)
    media_zone = next(
        (zone for zone in result.zones if zone.functional_zone == "media"),
        None,
    )
    if media_zone is None:
        return result

    final_media_categories = {
        str(item.get("category"))
        for item in selected_items
        if item.get("category") in {"television", "tv_stand"}
    }
    removed_ids = sorted(
        int(item["id"])
        for item in reduction.removed_lower_priority_items
    )
    media_zone.lower_priority_reduction_attempted = (
        reduction.lower_priority_reduction_attempted
    )
    media_zone.lower_priority_products_removed = bool(removed_ids)
    media_zone.removed_product_ids = removed_ids

    if final_media_categories == {"television", "tv_stand"}:
        media_zone.status = "added" if media_zone.addition_attempted else "fulfilled"
        media_zone.fulfilled_categories = ["television", "tv_stand"]
        media_zone.missing_categories = []
        if media_zone.addition_attempted and removed_ids:
            media_zone.reason = (
                "Added television and TV stand after removing lower-priority "
                "system-completion products: "
                + ", ".join(str(product_id) for product_id in removed_ids)
                + "."
            )
        elif media_zone.addition_attempted:
            media_zone.reason = (
                f"Added television and TV stand as an atomic pair under the "
                f"{result.functional_intent} media policy; no product removal was required."
            )
    elif reduction.removed_media_items:
        media_zone.status = "skipped"
        media_zone.fulfilled_categories = []
        media_zone.missing_categories = ["television", "tv_stand"]
        media_zone.reason = reduction.media_omission_reason or (
            "The system-added television and TV stand were omitted atomically."
        )
        if media_zone.reason not in result.intentional_omissions:
            result.intentional_omissions.append(media_zone.reason)
    elif len(final_media_categories) == 1:
        media_zone.status = "incompatible"
        media_zone.reason = reduction.media_omission_reason or (
            "Atomic media-pair integrity failed: television and TV stand must "
            "both be present or both be absent."
        )
    elif reduction.media_omission_reason:
        media_zone.status = "skipped"
        media_zone.fulfilled_categories = []
        media_zone.missing_categories = ["television", "tv_stand"]
        media_zone.reason = reduction.media_omission_reason
        if media_zone.reason not in result.intentional_omissions:
            result.intentional_omissions.append(media_zone.reason)

    result.missing_essential_zones = [
        zone.functional_zone
        for zone in result.zones
        if zone.policy_level == "required"
        and zone.status in {"missing", "incompatible"}
    ]
    if result.missing_essential_zones:
        result.status = "incomplete"
    elif result.intentional_omissions or any(
        zone.status == "skipped" for zone in result.zones
    ):
        result.status = "complete_with_omissions"
    else:
        result.status = "complete"
    return result
