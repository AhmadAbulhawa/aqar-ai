import hashlib
import json
from pathlib import Path

from fastapi import HTTPException

from app.models import RoomDimensions
from app.services.catalog_compatibility_service import is_design_eligible
from app.services.design_director_service import (
    DecorTarget,
    DesignStrategy,
    LayoutDecision,
    SeatingTarget,
)
from app.services.design_template_service import (
    load_design_template,
    load_template_source_candidates,
)
from app.services.room_analysis_service import RoomAnalysis
from app.services.room_fit_service import evaluate_product_fit
from app.services.composition_fit_service import (
    CompositionFitResult,
    CompositionFitStatus,
)
from app.services.architectural_constraint_service import (
    build_architectural_constraint_map,
)
from app.services.room_completeness_service import (
    analyze_room_completeness,
    finalize_media_zone_assessment,
    reduce_composition_with_media_priority,
)
from app.services.template_blueprint_service import (
    TemplateBlueprint,
)
from app.services.template_slot_resolver_service import (
    resolve_template_product_inventory,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

TEMPLATE_UPLOAD_DIR = (
    PROJECT_ROOT
    / "uploads"
    / "design_templates"
)

CORNER_SOFA_TAGS = {
    "corner_sofa",
    "sectional",
    "l_shape",
    "chaise_longue",
    "complete_seating_set",
}

ALLOWED_LAYOUTS = {
    "l_shape_conversation",
    "facing_sofas_conversation",
    "u_shape_conversation",
    "media_wall_focused",
    "single_sofa_minimal",
    "floating_zone",
    "bed_wall_focused",
    "workstation_focused",
    "dining_centered",
    "storage_optimized",
    "functional_zoning",
}

ALLOWED_FOCAL_POINTS = {
    "tv_wall",
    "fireplace",
    "window_view",
    "art_wall",
    "bed_wall",
    "work_wall",
    "dining_table",
    "none",
}

ALLOWED_DENSITIES = {
    "minimal",
    "balanced",
    "layered",
}


def get_template_preview_path(template: dict) -> Path | None:
    filename = template.get("preview_image_filename")

    if not filename:
        return None

    path = TEMPLATE_UPLOAD_DIR / str(filename)

    if not path.exists():
        return None

    return path


def validate_template_for_room(
    template: dict,
    room_type: str,
) -> None:
    if not template.get("is_active", False):
        raise HTTPException(
            status_code=422,
            detail="The selected design template is inactive.",
        )

    if str(template.get("room_type")) != room_type:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "The selected design template does not match "
                    "the uploaded room type."
                ),
                "template_room_type": template.get("room_type"),
                "room_type": room_type,
            },
        )


def prepare_template_source_candidates(
    template_id: int,
    room_type: str,
    room_dimensions: RoomDimensions | None,
) -> tuple[list[dict], list[dict]]:
    """
    Load eligible source-linked products as candidates, not locked selections.

    Exact products that clearly cannot fit are not forced. They are reported
    and the functional-completion step can later add real DB essentials.
    """

    products = load_template_source_candidates(template_id=template_id)

    source_candidate_items: list[dict] = []
    rejected_exact_items: list[dict] = []

    for item in products:
        if not is_design_eligible(item):
            rejected_exact_items.append(
                {
                    "product_id": int(item["id"]),
                    "product_name": item.get("name"),
                    "reason": "unavailable_or_not_design_eligible",
                }
            )
            continue

        if room_type not in item.get("room_types", []):
            rejected_exact_items.append(
                {
                    "product_id": int(item["id"]),
                    "product_name": item.get("name"),
                    "reason": "room_type_mismatch",
                }
            )
            continue

        fit = evaluate_product_fit(
            product=item,
            room_dimensions=room_dimensions,
        )

        if fit.get("fits") is False:
            rejected_exact_items.append(
                {
                    "product_id": int(item["id"]),
                    "product_name": item.get("name"),
                    "reason": "clear_physical_non_fit",
                    "fit": fit,
                }
            )
            continue

        prepared = dict(item)
        prepared["selection_role"] = "template_source_candidate"
        prepared["selection_origin"] = "template"
        prepared["template_id"] = template_id
        prepared["is_template_source_candidate"] = True
        source_candidate_items.append(prepared)

    return source_candidate_items, rejected_exact_items


def build_template_product_source_contract(
    template_id: int,
    template_products: list[dict],
) -> tuple[dict, str]:
    matched_products = sorted(
        [
            {
                "id": int(item["id"]),
                "quantity": int(item.get("quantity", 1)),
                "category": str(item.get("category") or ""),
            }
            for item in template_products
        ],
        key=lambda item: item["id"],
    )
    contract = {
        "semantics": "product_source_only",
        "template_id": int(template_id),
        "matched_products": matched_products,
        "layout_constraints_applied": False,
    }
    signature = hashlib.sha256(
        json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return contract, signature


def validate_final_template_products_fit(
    selected_items: list[dict],
    room_dimensions: RoomDimensions | None,
) -> list[dict]:
    fit_results: list[dict] = []
    violations: list[dict] = []

    for item in selected_items:
        result = evaluate_product_fit(
            product=item,
            room_dimensions=room_dimensions,
        )

        payload = {
            "product_id": int(item["id"]),
            "product_name": item["name"],
            "category": item["category"],
            "selection_role": item.get("selection_role"),
            "quantity": int(item.get("quantity", 1)),
            "product_dimensions_cm": {
                "width_cm": item.get("width_cm"),
                "depth_cm": item.get("depth_cm"),
                "height_cm": item.get("height_cm"),
            },
            "fit": result,
        }

        fit_results.append(payload)

        if result.get("fits") is False:
            violations.append(payload)

    if violations:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "One or more selected template/completion products "
                    "clearly cannot fit inside the measured room."
                ),
                "violations": violations,
            },
        )

    return fit_results


def _is_corner_sofa(item: dict) -> bool:
    tags = {
        str(tag).strip().lower()
        for tag in item.get("tags", [])
    }

    return (
        item.get("category") == "sofa"
        and bool(tags & CORNER_SOFA_TAGS)
    )


def _build_product_geometry_signature(
    selected_items: list[dict],
) -> str:
    """
    Include exact product geometry in the DesignStrategy text so design intent
    and downstream render contracts change whenever DB dimensions are
    corrected, even when product IDs and quantities remain unchanged.
    """

    payload = sorted(
        [
            {
                "id": int(item["id"]),
                "quantity": int(item.get("quantity", 1)),
                "width_cm": item.get("width_cm"),
                "depth_cm": item.get("depth_cm"),
                "height_cm": item.get("height_cm"),
            }
            for item in selected_items
        ],
        key=lambda item: item["id"],
    )

    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_template_design_strategy(
    template: dict,
    blueprint: TemplateBlueprint,
    selected_items: list[dict],
    room_analysis: RoomAnalysis,
) -> DesignStrategy:
    """
    Convert the selected template VISUAL BLUEPRINT plus the final DB-only
    product set into deterministic design intent supplied to GPT Image 2.

    The chosen ready design owns the layout direction. Product completion only
    fills missing major slots; it must not create a different design.
    """

    layout_type = str(
        blueprint.layout_type
        or template.get("layout_type")
        or "functional_zoning"
    )

    if layout_type not in ALLOWED_LAYOUTS:
        layout_type = "functional_zoning"

    focal_point = str(
        blueprint.focal_point
        or template.get("focal_point")
        or "none"
    )

    if focal_point not in ALLOWED_FOCAL_POINTS:
        normalized_focal = focal_point.casefold()
        if "fireplace" in normalized_focal:
            focal_point = "fireplace"
        elif "window" in normalized_focal or "view" in normalized_focal:
            focal_point = "window_view"
        elif any(
            token in normalized_focal
            for token in ("art", "artwork", "painting", "canvas")
        ):
            focal_point = "art_wall"
        elif "television" in normalized_focal or "tv" in normalized_focal:
            focal_point = "tv_wall"
        else:
            focal_point = "none"

    density_target = str(
        blueprint.density_target
        or template.get("density_target")
        or "balanced"
    )

    if density_target not in ALLOWED_DENSITIES:
        density_target = "balanced"

    seating: list[SeatingTarget] = []
    sofa_role_index = 0
    armchair_role_index = 0

    for item in selected_items:
        category = str(item.get("category", ""))
        quantity = min(
            max(int(item.get("quantity", 1)), 1),
            4,
        )

        if category == "sofa":
            seating.append(
                SeatingTarget(
                    category="sofa",
                    solution_type=(
                        "corner_sofa"
                        if _is_corner_sofa(item)
                        else "standard_sofa"
                    ),
                    quantity=quantity,
                    role=(
                        "primary"
                        if sofa_role_index == 0
                        else "secondary"
                    ),
                )
            )
            sofa_role_index += 1

        elif category == "armchair":
            seating.append(
                SeatingTarget(
                    category="armchair",
                    solution_type=None,
                    quantity=quantity,
                    role=(
                        "secondary"
                        if armchair_role_index == 0
                        else "accent"
                    ),
                )
            )
            armchair_role_index += 1

    decor_totals: dict[str, int] = {}

    for item in selected_items:
        category = str(item.get("category", ""))

        if category in {"sofa", "armchair"}:
            continue

        decor_totals[category] = (
            decor_totals.get(category, 0)
            + int(item.get("quantity", 1))
        )

    slot_importances_by_category: dict[str, set[str]] = {}
    for slot in blueprint.product_slots:
        if slot.category in {"sofa", "armchair"}:
            continue
        slot_importances_by_category.setdefault(slot.category, set()).add(
            slot.importance
        )

    decor = []
    for category, quantity in sorted(decor_totals.items()):
        importances = slot_importances_by_category.get(category, set())
        if "essential" in importances:
            priority = "essential"
            minimum = quantity
        elif "supporting" in importances:
            priority = "recommended"
            minimum = 0
        else:
            priority = "optional"
            minimum = 0
        decor.append(
            DecorTarget(
                category=category,
                min_quantity=min(minimum, 10),
                max_quantity=min(quantity, 10),
                priority=priority,
            )
        )

    symmetry = (
        "symmetric"
        if layout_type in {
            "facing_sofas_conversation",
            "u_shape_conversation",
        }
        else "asymmetric"
    )

    obstacle_summary = (
        "; ".join(room_analysis.obstacles[:3])
        if room_analysis.obstacles
        else "No major visible obstacles were reported."
    )

    visual_rules = " | ".join(
        blueprint.key_visual_rules[:8]
    )

    geometry_signature = _build_product_geometry_signature(
        selected_items=selected_items
    )

    return DesignStrategy(
        layout=LayoutDecision(
            requested_layout=(
                template.get("layout_type")
                or blueprint.layout_type
            ),
            requested_layout_supported=True,
            final_layout_strategy=layout_type,
            reason=(
                "Recreate the selected ready-design visual blueprint inside "
                f"the uploaded {room_analysis.layout_shape} room. Preserve "
                "the blueprint's major zones, furniture relationships, focal "
                "hierarchy, density, and negative-space logic while respecting "
                f"the real room obstacles and circulation. Visible constraints: "
                f"{obstacle_summary}. Blueprint composition: "
                f"{blueprint.composition_summary}."
            ),
        ),
        focal_point=focal_point,
        seating=seating,
        decor=decor,
        density_target=density_target,
        symmetry=symmetry,
        lighting_layers_needed=[
            "ambient",
            "accent",
        ],
        design_rationale=(
            "The user selected ready-design template "
            f"'{template.get('name')}'. Reproduce the same design composition "
            "as closely as the real room geometry allows. Source-linked products "
            "are ranked first for each blueprint slot; unresolved slots are "
            "completed only with active products from the Aqar AI database. "
            "Do not convert "
            "this into a generic recommendation. Key visual rules: "
            f"{visual_rules or 'Preserve the supplied template blueprint.'} "
            f"Product geometry signature: {geometry_signature}."
        ),
    )


def load_template_application_data(
    template_id: int,
    room_type: str,
    room_analysis: RoomAnalysis,
    room_dimensions: RoomDimensions | None,
    all_items: list[dict],
    requested_style: str | None,
    preferred_colors: list[str],
) -> tuple[
    dict,
    dict,
    bool,
    str,
    list[dict],
    list[dict],
    list[dict],
    dict,
    list[dict],
    CompositionFitResult,
    None,
    Path | None,
]:
    template = load_design_template(template_id=template_id)

    validate_template_for_room(
        template=template,
        room_type=room_type,
    )

    preview_path = get_template_preview_path(template=template)

    (
        source_candidate_items,
        rejected_exact_items,
    ) = prepare_template_source_candidates(
        template_id=template_id,
        room_type=room_type,
        room_dimensions=room_dimensions,
    )
    (
        product_source_contract,
        product_source_signature,
    ) = build_template_product_source_contract(
        template_id=template_id,
        template_products=source_candidate_items,
    )

    if not source_candidate_items:
        raise HTTPException(
            status_code=422,
            detail=(
                "The selected template has no active matched database products "
                "that are compatible with this room."
            ),
        )

    architectural_constraints = build_architectural_constraint_map(
        room_analysis=room_analysis,
        room_dimensions=room_dimensions,
    )
    completeness = analyze_room_completeness(
        room_type=room_type,
        room_analysis=room_analysis,
        current_selected_items=source_candidate_items,
        architectural_constraints=architectural_constraints,
        catalog_items=all_items,
        room_dimensions=room_dimensions,
        blueprint=None,
        slot_satisfaction=None,
        density_target="balanced",
    )
    slot_resolution = resolve_template_product_inventory(
        template_products=source_candidate_items,
        database_candidates=all_items,
        completion_requirements=completeness.additional_requirements,
        room_dimensions=room_dimensions,
        room_type=room_type,
        requested_style=requested_style,
        preferred_colors=preferred_colors,
    )
    selected_items = slot_resolution.selected_items

    completion_metadata = {
        "ai_selector_called": False,
        "resolver_version": slot_resolution.resolver_version,
        "catalog_signature": slot_resolution.catalog_signature,
        "requirements_signature": slot_resolution.requirements_signature,
        "slot_satisfaction": [
            satisfaction.model_dump()
            for satisfaction in slot_resolution.slot_satisfaction
        ],
        "slot_assignments": [
            assignment.model_dump()
            for assignment in slot_resolution.assignments
        ],
        "unresolved_slots": [
            slot.model_dump()
            for slot in slot_resolution.unresolved_slots
        ],
        "unassigned_source_candidate_ids": (
            slot_resolution.unassigned_source_candidate_ids
        ),
        "warnings": slot_resolution.warnings,
        "source_candidates_are_mandatory": True,
        "completion_scope": "matched_template_products_plus_functional_room_completion",
        "template_semantics": "product_source_only",
        "template_layout_constraints_applied": False,
        "template_product_source_signature": product_source_signature,
        "room_completeness": completeness.model_dump(mode="json"),
        "architectural_constraint_map": architectural_constraints.model_dump(
            mode="json"
        ),
        "render_reference_budget": {
            "room_images": 1,
            "template_images": 0,
            "maximum_unique_product_images": 15,
            "selected_unique_product_images": len(selected_items),
            "total_input_image_limit": 16,
        },
    }
    exact_template_items = [
        item
        for item in selected_items
        if item.get("selection_role") == "template_selected"
    ]

    if not selected_items:
        raise HTTPException(
            status_code=422,
            detail=(
                "No real database products could be selected for the "
                "chosen ready-design template."
            ),
        )

    fit_results = validate_final_template_products_fit(
        selected_items=selected_items,
        room_dimensions=room_dimensions,
    )

    composition_reduction = reduce_composition_with_media_priority(
        selected_items=selected_items,
        completeness=completeness,
        room_dimensions=room_dimensions,
        obstacles=room_analysis.obstacles,
        circulation_paths=room_analysis.circulation_paths,
        room_type=room_type,
    )
    selected_items = composition_reduction.selected_items
    composition_validation = composition_reduction.validation
    removed_items = [
        *composition_reduction.removed_lower_priority_items,
        *composition_reduction.removed_media_items,
    ]
    completeness = analyze_room_completeness(
        room_type=room_type,
        room_analysis=room_analysis,
        current_selected_items=selected_items,
        architectural_constraints=architectural_constraints,
        catalog_items=all_items,
        room_dimensions=room_dimensions,
        blueprint=None,
        slot_satisfaction=None,
        density_target="balanced",
        allow_additions=False,
    )
    completeness = finalize_media_zone_assessment(
        completeness=completeness,
        selected_items=selected_items,
        reduction=composition_reduction,
    )
    completion_metadata["room_completeness"] = completeness.model_dump(mode="json")
    if removed_items:
        fit_results = validate_final_template_products_fit(
            selected_items=selected_items,
            room_dimensions=room_dimensions,
        )
        completion_metadata["overcrowding_reduction"] = {
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
            "policy": (
                "optional, recommended, then supporting system completions; "
                "user selections, essential template products, required seating, "
                "and the required atomic media pair are protected"
            ),
        }
        exact_template_items = [
            item
            for item in selected_items
            if item.get("selection_role") == "template_selected"
        ]
        completion_metadata["render_reference_budget"][
            "selected_unique_product_images"
        ] = len(selected_items)
    elif composition_reduction.lower_priority_reduction_attempted:
        completion_metadata["overcrowding_reduction"] = {
            "removed_product_ids": [],
            "removed_lower_priority_product_ids": [],
            "removed_media_product_ids": [],
            "lower_priority_reduction_attempted": True,
            "hard_conflict": composition_reduction.hard_conflict,
            "policy": (
                "Required media was tested after attempting lower-priority "
                "system-completion reduction."
            ),
        }
    if composition_validation.status == CompositionFitStatus.INVALID:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Blocking error: the selected template inventory is "
                    "physically impossible for the measured room."
                ),
                "composition_validation": composition_validation.model_dump(),
            },
        )

    return (
        template,
        product_source_contract,
        False,
        product_source_signature,
        exact_template_items,
        rejected_exact_items,
        selected_items,
        completion_metadata,
        fit_results,
        composition_validation,
        None,
        preview_path,
    )
