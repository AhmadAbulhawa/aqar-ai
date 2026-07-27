import json
from pathlib import Path
from uuid import uuid4

from fastapi import (
    APIRouter,
    Body,
    HTTPException
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from app.database import get_connection

from app.models import (
    DesignRequest,
    ProductRequirement,
    ExactProductRequirement,
    RoomDimensions,
    TemplateRenderRequest,
)

from app.services.room_analysis_service import (
    RoomAnalysis,
    build_room_analysis_signature,
    get_or_create_room_analysis,
    is_external_ai_connection_error,
)

from app.room_templates import ROOM_TEMPLATES

from app.services.ai_selector_service import (
    resolve_design_constraints
)

from app.services.design_director_service import (
    DESIGN_DIRECTOR_MODEL,
    DesignStrategy,
    get_or_create_design_strategy,
    get_saved_design_strategy
)

from app.services.composition_fit_service import (
    CompositionFitResult,
    CompositionFitStatus,
    validate_composition_fit,
)
from app.services.architectural_constraint_service import (
    build_architectural_constraint_map,
)

from app.services.image_prep_service import (
    get_room_image_path,
    prepare_furniture_reference_images
)

from app.services.openai_image_service import (
    MODEL_NAME,
    RENDER_PROMPT_VERSION,
    build_generation_prompt,
    build_render_prompt_debug_metadata,
    generate_furnished_room_image,
)
from app.services.design_template_service import (
    load_active_design_templates,
    load_design_template,
    load_design_template_detail,
    match_design_templates,
)

from app.services.template_application_service import (
    get_template_preview_path,
    load_template_application_data,
    validate_final_template_products_fit,
)
from app.services.selection_persistence_service import (
    PersistedDesignSelection,
    build_design_selection_signature,
    create_or_reuse_design_selection,
    load_design_selection,
    load_latest_template_design_selection,
)
from app.services.render_manifest_service import (
    PLACEMENT_PLAN_STATUS,
    VISUAL_DESIGN_AUTHORITY,
    build_render_manifest,
)
from app.services.render_compliance_service import (
    RenderComplianceResult,
    validate_render_compliance,
)
from app.services.generated_design_service import (
    load_latest_generated_design,
    save_generated_design,
)
from app.services.product_origin_service import public_product_origin
from app.services.inventory_revision_service import build_inventory_revision
from app.services.unified_selection_service import build_catalog_signature
from app.services.observability_service import (
    log_pipeline_event,
    timed_pipeline_stage,
)


from app.services.recommendation_service import (
    generate_custom_recommendation,
    generate_recommendation,
    validate_selection_against_design_strategy
)

from app.services.room_fit_service import (
    validate_exact_required_products_fit
)


router = APIRouter(
    tags=["Design"]
)


# =========================================================
# Constants
# =========================================================

MAX_FURNITURE_REFERENCE_IMAGES = 15


def _core_composition_validation(payload: dict) -> dict:
    """Read the broad composition contract from legacy/new persisted payloads."""

    return {
        field: payload[field]
        for field in CompositionFitResult.model_fields
        if field in payload
    }


def _assert_current_catalog_valid(
    selected_items: list[dict],
    active_catalog_items: list[dict],
) -> None:
    """Fail explicitly if an immutable product is no longer render-eligible."""

    active_ids = {int(item["id"]) for item in active_catalog_items}
    invalid_ids = [
        int(item["id"])
        for item in selected_items
        if int(item["id"]) not in active_ids
    ]
    if invalid_ids:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "One or more immutable selected products are now unavailable "
                    "or quarantined. Run selection again; render will not substitute them."
                ),
                "invalid_product_ids": invalid_ids,
            },
        )


def _refresh_immutable_product_reference_images(
    selected_items: list[dict],
    active_catalog_items: list[dict],
) -> list[dict]:
    """Use the latest catalog image for the same locked product ID.

    Product identity, dimensions, price, and quantity remain sourced from the
    immutable snapshot. Official catalog images may be upgraded after a
    selection is locked, and keeping a stale low-resolution path would reduce
    fidelity without adding any immutability protection.
    """

    active_by_id = {
        int(item["id"]): item
        for item in active_catalog_items
    }
    refreshed_items: list[dict] = []
    reference_fields = (
        "image_filename",
        "source_image_url",
        "additional_source_image_urls",
    )
    for snapshot in selected_items:
        refreshed = dict(snapshot)
        current = active_by_id[int(snapshot["id"])]
        for field in reference_fields:
            if current.get(field):
                refreshed[field] = current[field]
        refreshed_items.append(refreshed)
    return refreshed_items


def _reconcile_slot_metadata(
    metadata: dict,
    selected_items: list[dict],
) -> None:
    """Keep frontend slot diagnostics truthful after draft reductions."""

    selected_ids = {int(item["id"]) for item in selected_items}
    assignments = [
        assignment
        for assignment in metadata.get("slot_assignments", [])
        if int(assignment.get("product_id", 0)) in selected_ids
    ]
    metadata["slot_assignments"] = assignments
    unresolved = list(metadata.get("unresolved_slots", []))
    unresolved_keys = {str(item.get("slot_key")) for item in unresolved}

    reconciled_satisfaction = []
    for satisfaction in metadata.get("slot_satisfaction", []):
        payload = dict(satisfaction)
        retained_fulfillment = [
            fulfilled
            for fulfilled in payload.get("fulfilled_by", [])
            if int(fulfilled.get("product_id", 0)) in selected_ids
        ]
        fulfilled_quantity = sum(
            int(item.get("quantity", 0)) for item in retained_fulfillment
        )
        required_quantity = int(payload.get("required_quantity", 0))
        payload["fulfilled_by"] = retained_fulfillment
        payload["fulfilled_quantity"] = min(
            fulfilled_quantity, required_quantity
        )
        payload["missing_quantity"] = max(
            required_quantity - fulfilled_quantity, 0
        )
        reconciled_satisfaction.append(payload)
        slot_key = str(payload.get("slot_key"))
        if payload["missing_quantity"] and slot_key not in unresolved_keys:
            unresolved.append(
                {
                    "slot_key": slot_key,
                    "category": payload.get("category"),
                    "missing_quantity": payload["missing_quantity"],
                    "quantity": payload["missing_quantity"],
                    "importance": payload.get("importance"),
                    "reason_code": "layout_reduction",
                    "reason": (
                        "A lower-priority system addition was removed before "
                        "final lock to protect layout quality."
                    ),
                    "repeat_group_id": payload.get("repeat_group_id"),
                }
            )
            unresolved_keys.add(slot_key)
    metadata["slot_satisfaction"] = reconciled_satisfaction
    metadata["unresolved_slots"] = unresolved


def _validate_draft_selection_for_render(
    *,
    room_analysis: RoomAnalysis,
    selected_items: list[dict],
    composition_validation: CompositionFitResult | dict,
) -> tuple[
    list[dict],
    CompositionFitResult,
    dict,
]:
    """Apply physical checks without changing product IDs or quantities."""

    composition = (
        composition_validation
        if isinstance(composition_validation, CompositionFitResult)
        else CompositionFitResult.model_validate(composition_validation)
    )
    reduction_metadata = {
        "attempted": False,
        "removed_product_ids": [],
        "hard_conflict": False,
        "reason": None,
    }

    if composition.status == CompositionFitStatus.INVALID:
        reduction_metadata = {
            "attempted": False,
            "removed_product_ids": [],
            "hard_conflict": True,
            "reason": "automatic_product_removal_disabled",
        }
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Blocking error: the exact inventory is physically "
                    "impossible for the measured room."
                ),
                "composition_validation": composition.model_dump(mode="json"),
                "reduction": reduction_metadata,
            },
        )
    return (
        selected_items,
        composition,
        reduction_metadata,
    )


# =========================================================
# Saved-room recommendation options
#
# Used by:
#
# POST /designs/recommend/{room_request_id}
#
# Empty body or no body:
# -> Auto mode
#
# product_requirements supplied:
# -> Guided mode
# =========================================================

class RoomDesignOptions(BaseModel):

    product_requirements: list[
        ProductRequirement
    ] = Field(
        default_factory=list
    )

    required_products: list[
        ExactProductRequirement
    ] = Field(
        default_factory=list
    )

    user_prompt: str | None = Field(
        default=None,
        max_length=1000
    )

    layout_preference: str | None = Field(
        default=None,
        max_length=100
    )


# =========================================================
# Render / generation request
#
# The frontend sends the exact products previously returned
# by the recommendation endpoint.
#
# No AI product selection happens here.
# =========================================================

class RenderDesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection_id: str = Field(
        ...,
        min_length=36,
        max_length=36,
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        ),
    )

    design_strategy_signature: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$"
    )



# =========================================================
# Ready-template application request
# =========================================================

class ApplyTemplateRequest(BaseModel):
    template_id: int = Field(
        ...,
        gt=0
    )


class InventoryRevisionLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(..., gt=0)
    quantity: int = Field(..., ge=1, le=10)


class InventoryRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[InventoryRevisionLine] = Field(
        ...,
        min_length=1,
        max_length=MAX_FURNITURE_REFERENCE_IMAGES,
    )


# =========================================================
# Helpers
# =========================================================

def decode_list(
    value: str | None
) -> list[str]:

    if not value:
        return []

    try:
        parsed_value = json.loads(
            value
        )

        if isinstance(
            parsed_value,
            list
        ):
            return [
                str(item).strip()
                for item in parsed_value
                if str(item).strip()
            ]

    except (
        json.JSONDecodeError,
        TypeError
    ):
        pass

    return [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]


def row_to_furniture(
    row
) -> dict:

    furniture = dict(
        row
    )

    furniture["room_types"] = (
        decode_list(
            furniture.get(
                "room_types"
            )
        )
    )

    furniture["tags"] = (
        decode_list(
            furniture.get(
                "tags"
            )
        )
    )

    furniture["additional_source_image_urls"] = decode_list(
        furniture.get("additional_source_image_urls")
    )

    furniture["included_pieces"] = decode_list(
        furniture.get("included_pieces")
    )

    furniture["availability"] = bool(
        furniture.get(
            "availability"
        )
    )
    furniture["design_eligible"] = bool(
        furniture.get("design_eligible", True)
    )
    furniture["is_product_set"] = bool(
        furniture.get("is_product_set", False)
    )

    return furniture


def parse_preferred_colors(
    colors_value: str | None
) -> list[str]:

    if not colors_value:
        return []

    try:
        parsed_value = json.loads(
            colors_value
        )

        if isinstance(
            parsed_value,
            list
        ):
            return [
                str(color)
                .strip()
                .lower()

                for color
                in parsed_value

                if str(color).strip()
            ]

    except (
        json.JSONDecodeError,
        TypeError
    ):
        pass

    return [
        color
        .strip()
        .lower()

        for color
        in colors_value.split(",")

        if color.strip()
    ]


# =========================================================
# DB loading
# =========================================================

def load_room_and_furniture(
    room_request_id: int
) -> tuple[
    dict,
    list[dict]
]:

    connection = (
        get_connection()
    )

    cursor = (
        connection.cursor()
    )

    try:
        # -------------------------------------------------
        # Room request
        # -------------------------------------------------

        cursor.execute(
            """
            SELECT *
            FROM room_requests
            WHERE id = ?
            """,
            (
                room_request_id,
            )
        )

        room_row = (
            cursor.fetchone()
        )

        if room_row is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Room request not found"
                )
            )

        room_request = dict(
            room_row
        )

        # -------------------------------------------------
        # Available furniture / products
        # -------------------------------------------------

        cursor.execute(
            """
            SELECT *
            FROM furniture
            WHERE availability = 1
              AND COALESCE(catalog_status, 'active') = 'active'
              AND COALESCE(design_eligible, 1) = 1
            """
        )

        furniture_rows = (
            cursor.fetchall()
        )

    finally:
        connection.close()

    furniture_items = [
        row_to_furniture(
            row
        )
        for row
        in furniture_rows
    ]

    return (
        room_request,
        furniture_items
    )


# =========================================================
# Build DesignRequest from saved room
# =========================================================

def build_design_request(
    room_request: dict,
    options: RoomDesignOptions | None = None
) -> DesignRequest:

    preferred_colors = (
        parse_preferred_colors(
            room_request.get(
                "preferred_colors"
            )
        )
    )

    product_requirements = (
        options.product_requirements
        if options
        else []
    )

    required_products = (
        options.required_products
        if options
        else []
    )

    room_dimensions = None

    room_length_cm = room_request.get(
        "room_length_cm"
    )

    room_width_cm = room_request.get(
        "room_width_cm"
    )

    room_height_cm = room_request.get(
        "room_height_cm"
    )

    if (
        room_length_cm is not None
        and room_width_cm is not None
    ):
        room_dimensions = RoomDimensions(
            length_cm=float(room_length_cm),
            width_cm=float(room_width_cm),
            height_cm=(
                float(room_height_cm)
                if room_height_cm is not None
                else None
            )
        )

    user_prompt = (
        options.user_prompt
        if options
        else None
    )

    return DesignRequest(
        room_type=
            room_request[
                "room_type"
            ],

        budget=
            room_request.get(
                "budget"
            ),

        style=
            room_request.get(
                "style"
            ),

        preferred_colors=
            preferred_colors,

        user_prompt=
            user_prompt,

        product_requirements=
            product_requirements,

        required_products=
            required_products,

        room_dimensions=
            room_dimensions
    )


# =========================================================
# Recommendation for saved room request
#
# This function may run/cached-load Design Director requirements, then uses
# deterministic shared product satisfaction/completion. It does not call the
# legacy product-selector model.
#
# Use only for recommendation endpoints.
# Never use this from render.
# =========================================================

def get_room_recommendation(
    room_request_id: int,
    options: RoomDesignOptions | None = None
) -> tuple[
    dict,
    DesignRequest,
    RoomAnalysis,
    dict
]:

    (
        room_request,
        all_items
    ) = load_room_and_furniture(
        room_request_id
    )

    design_request = (
        build_design_request(
            room_request=
                room_request,

            options=
                options
        )
    )

    room_type = (
        design_request
        .room_type
        .value
    )

    room_items = [
        item
        for item in all_items
        if (
            room_type
            in item["room_types"]
        )
    ]

    if not room_items:
        raise HTTPException(
            status_code=404,
            detail=(
                "No available furniture "
                "items found for this room type"
            )
        )

    room_image_path = (
        get_room_image_path(
            room_request[
                "image_filename"
            ]
        )
    )

    try:
        (
            room_analysis,
            analysis_cached
        ) = get_or_create_room_analysis(
            room_request_id=
                room_request_id,

            image_path=
                room_image_path,

            room_type=
                room_type,

            room_dimensions=
                design_request.room_dimensions
        )

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to load or analyze room before recommendation: "
                f"{str(error)}"
            )
        ) from error

    room_template = ROOM_TEMPLATES.get(
        room_type
    )

    if room_template is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No room template exists for: {room_type}"
            )
        )

    constraints = resolve_design_constraints(
        request=design_request,
        room_template=room_template,
        candidate_items=room_items
    )

    validate_exact_required_products_fit(
        required_products=design_request.required_products,
        candidate_items=room_items,
        room_dimensions=design_request.room_dimensions
    )

    style = (
        design_request.style.value
        if design_request.style
        else None
    )

    layout_preference = (
        options.layout_preference
        if options
        else None
    )

    is_custom = bool(design_request.required_products) and not bool(
        design_request.product_requirements
    )
    design_strategy: DesignStrategy | None = None
    design_strategy_cached = False
    design_strategy_signature: str | None = None
    design_strategy_fallback = False
    if not is_custom:
        try:
            (
                design_strategy,
                design_strategy_cached,
                design_strategy_signature
            ) = get_or_create_design_strategy(
                room_request_id=room_request_id,
                room_image_path=room_image_path,
                room_analysis=room_analysis,
                room_type=room_type,
                style=style,
                preferred_colors=(
                    design_request.preferred_colors
                ),
                user_prompt=design_request.user_prompt,
                layout_preference=layout_preference,
                constraints=constraints,
                candidate_items=room_items
            )

        except HTTPException as error:
            if not is_external_ai_connection_error(error):
                raise
            # The deterministic selection engine can safely use the existing
            # room-template requirements when Design Director transport is
            # unavailable. Authentication, schema, and validation failures
            # are intentionally not swallowed here.
            design_strategy = None
            design_strategy_cached = False
            design_strategy_signature = None
            design_strategy_fallback = True

        except Exception as error:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Failed to create Design Director strategy: "
                    f"{str(error)}"
                )
            ) from error

    try:
        if is_custom:
            recommendation = generate_custom_recommendation(
                room_items=room_items,
                request=design_request,
                room_analysis=room_analysis,
            )
        else:
            recommendation = generate_recommendation(
                room_items=room_items,
                request=design_request,
                room_analysis=room_analysis,
                design_strategy=design_strategy,
            )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        ) from exc

    recommendation[
        "room_analysis_cached"
    ] = analysis_cached

    recommendation[
        "design_director_model"
    ] = None if is_custom else DESIGN_DIRECTOR_MODEL

    recommendation[
        "design_strategy_cached"
    ] = design_strategy_cached

    recommendation[
        "design_strategy_fallback"
    ] = design_strategy_fallback

    recommendation[
        "design_strategy_signature"
    ] = design_strategy_signature

    recommendation[
        "design_strategy"
    ] = design_strategy.model_dump() if design_strategy else None

    recommendation[
        "layout_preference"
    ] = layout_preference

    authoritative_room_dimensions = (
        design_request.room_dimensions
        or room_analysis.provided_dimensions_cm
    )
    room_analysis_signature = build_room_analysis_signature(
        image_path=room_image_path,
        room_type=room_type,
        room_dimensions=authoritative_room_dimensions,
    )
    selected_items = recommendation["recommended_items"]
    (
        selected_items,
        final_composition_validation,
        composition_reduction,
    ) = _validate_draft_selection_for_render(
        room_analysis=room_analysis,
        selected_items=selected_items,
        composition_validation=recommendation["composition_validation"],
    )
    recommendation["recommended_items"] = selected_items
    _reconcile_slot_metadata(recommendation, selected_items)
    recommendation["selected_furniture_ids"] = [
        int(item["id"]) for item in selected_items
    ]
    recommendation["selected_products"] = [
        {"id": int(item["id"]), "quantity": int(item["quantity"])}
        for item in selected_items
    ]
    recommendation["total_price"] = round(
        sum(float(item.get("line_total") or 0) for item in selected_items), 2
    )
    currencies = {
        item.get("currency") for item in selected_items if item.get("currency")
    }
    recommendation["currency"] = (
        next(iter(currencies)) if len(currencies) == 1 else None
    )
    recommendation["selection_breakdown"].update(
        {
            "user_required_selected_ids": [
                int(item["id"])
                for item in selected_items
                if item.get("selection_origin") == "user"
            ],
            "required_selected_ids": [int(item["id"]) for item in selected_items],
            "system_completion_ids": [
                int(item["id"])
                for item in selected_items
                if item.get("selection_origin") == "system"
            ],
        }
    )
    recommendation["composition_validation"] = (
        final_composition_validation.model_dump(mode="json")
    )
    recommendation.update(
        {
            "visual_design_authority": VISUAL_DESIGN_AUTHORITY,
            "placement_planner_called": False,
            "pre_render_placement_required": False,
            "placement_plan": None,
            "placement_plan_status": PLACEMENT_PLAN_STATUS,
            "placement_plan_cached": False,
            "placement_selection_signature": None,
            "layout_quality": None,
            "composition_reduction": composition_reduction,
            "render_reference_budget": {
                "room_images": 1,
                "template_images": 0,
                "maximum_unique_product_images": 15,
                "selected_unique_product_images": len(selected_items),
                "total_input_image_limit": 16,
            },
        }
    )
    selection_signature = build_design_selection_signature(
        room_request_id=room_request_id,
        template_id=None,
        blueprint_signature=recommendation["requirements_signature"],
        catalog_signature=recommendation["catalog_signature"],
        room_analysis_signature=room_analysis_signature,
        selected_items=selected_items,
    )
    if design_request.required_products:
        persisted_mode = "custom"
    elif design_request.product_requirements:
        persisted_mode = "guided"
    else:
        persisted_mode = "auto"
    persisted_selection, selection_reused = create_or_reuse_design_selection(
        room_request_id=room_request_id,
        template_id=None,
        mode=persisted_mode,
        selection_signature=selection_signature,
        blueprint_signature=recommendation["requirements_signature"],
        catalog_signature=recommendation["catalog_signature"],
        room_analysis_signature=room_analysis_signature,
        selected_items=selected_items,
        blueprint={
            "requirements": recommendation["requirements"],
            "design_strategy_signature": design_strategy_signature,
            "room_completeness": recommendation.get("room_completeness"),
            "architectural_constraint_map": recommendation.get(
                "architectural_constraint_map"
            ),
        },
        composition_validation={
            **recommendation["composition_validation"],
            "pre_render_validation_version": "2026-07-19.1-overlap-aware",
            "coordinate_layout_validation_required": False,
            "composition_reduction": composition_reduction,
        },
    )
    recommendation["selection_id"] = persisted_selection.selection_id
    recommendation["selection_reused"] = selection_reused
    recommendation["selection_status"] = persisted_selection.status

    return (
        room_request,
        design_request,
        room_analysis,
        recommendation
    )



# =========================================================
# Build exact render context
#
# No recommendation AI is called here.
# =========================================================

def get_exact_render_context(
    room_request_id: int,
    render_request: RenderDesignRequest
) -> tuple[
    dict,
    DesignRequest,
    list[dict],
    PersistedDesignSelection,
]:

    (
        room_request,
        all_items
    ) = load_room_and_furniture(
        room_request_id
    )

    design_request = (
        build_design_request(
            room_request=
                room_request
        )
    )

    room_type = (
        design_request
        .room_type
        .value
    )

    if render_request.selection_id:
        selection = load_design_selection(
            render_request.selection_id,
            room_request_id=room_request_id,
        )
        if selection.template_id is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Template selections must use the template render endpoint "
                    "so template-selection audit context remains attached."
                ),
            )
        room_image_path = get_room_image_path(room_request["image_filename"])
        room_analysis, _ = get_or_create_room_analysis(
            room_request_id=room_request_id,
            image_path=room_image_path,
            room_type=room_type,
            room_dimensions=design_request.room_dimensions,
        )
        authoritative_dimensions = (
            design_request.room_dimensions
            or room_analysis.provided_dimensions_cm
        )
        current_signature = build_room_analysis_signature(
            image_path=room_image_path,
            room_type=room_type,
            room_dimensions=authoritative_dimensions,
        )
        if current_signature != selection.room_analysis_signature:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Room inputs changed after selection finalization. "
                    "Run recommendation again before rendering."
                ),
            )
        selected_items = selection.selected_items
    else:  # Pydantic validation makes this unreachable.
        raise HTTPException(
            status_code=422,
            detail="selection_id is required for render inventory.",
        )

    _assert_current_catalog_valid(selected_items, all_items)
    selected_items = _refresh_immutable_product_reference_images(
        selected_items,
        all_items,
    )

    return (
        room_request,
        design_request,
        selected_items,
        selection,
    )


# =========================================================
# Calculate totals
# =========================================================

def calculate_selected_items_totals(
    selected_items: list[dict]
) -> tuple[
    float,
    str | None
]:

    total_price = round(
        sum(
            float(
                item[
                    "line_total"
                ]
            )
            for item
            in selected_items
        ),
        2
    )

    currencies = {
        item.get(
            "currency"
        )
        for item
        in selected_items
        if item.get(
            "currency"
        )
    }

    currency = (
        next(
            iter(
                currencies
            )
        )
        if len(currencies) == 1
        else None
    )

    return (
        total_price,
        currency
    )


# =========================================================
# Frontend product response
# =========================================================

def build_selected_furniture_response(
    recommended_items: list[dict]
) -> list[dict]:

    selected_furniture = []

    for item in recommended_items:

        image_filename = (
            item.get(
                "image_filename"
            )
        )

        image_url = (
            (
                f"/uploads/furniture/"
                f"{image_filename}"
            )
            if image_filename
            else None
        )

        selected_furniture.append({
            "id":
                int(
                    item["id"]
                ),

            "name":
                item["name"],

            "category":
                item["category"],

            "style":
                item["style"],

            "color":
                item["color"],

            "material": item.get("material"),

            "width_cm": item.get("width_cm"),

            "depth_cm": item.get("depth_cm"),

            "height_cm": item.get("height_cm"),

            "diameter_cm": item.get("diameter_cm"),

            "price":
                float(
                    item["price"]
                ),

            "currency":
                item["currency"],

            "image_filename":
                image_filename,

            "image_url":
                image_url,

            "product_url":
                item["product_url"],

            "store_name":
                item.get(
                    "store_name"
                ),

            "source_store_slug": item.get("source_store_slug"),
            "source_product_id": item.get("source_product_id"),
            "canonical_product_url": item.get("canonical_product_url"),
            "design_eligible": bool(item.get("design_eligible", True)),
            "mounting_type": item.get("mounting_type"),
            "is_product_set": bool(item.get("is_product_set", False)),

            "selection_role":
                item.get(
                    "selection_role"
                ),

            "selection_origin": item.get("selection_origin"),
            "product_origin": public_product_origin(item),
            "slot_keys": list(item.get("slot_keys") or []),
            "repeat_group_id": item.get("repeat_group_id"),
            "inclusion_reason": item.get("inclusion_reason"),
            "was_included": bool(item.get("was_included", False)),
            "added_for_completion": bool(
                item.get("added_for_completion", False)
            ),
            "included_quantity": int(item.get("included_quantity", 0)),
            "completion_quantity": int(item.get("completion_quantity", 0)),
            "match_quality": item.get("match_quality"),
            "functional_zone": item.get("functional_zone"),
            "functional_zones": list(item.get("functional_zones") or []),
            "functional_group_ids": list(
                item.get("functional_group_ids") or []
            ),
            "requirement_importance": item.get("requirement_importance"),
            "completeness_level": item.get("completeness_level"),
            "quantity_edited_by_user": bool(
                item.get("quantity_edited_by_user", False)
            ),

            "quantity":
                int(
                    item.get(
                        "quantity",
                        1
                    )
                ),

            "line_total":
                float(
                    item.get(
                        "line_total",
                        item["price"]
                    )
                )
        })

    return selected_furniture


# =========================================================
# Build design-options response
# =========================================================

def build_design_options_response(
    design_request: DesignRequest,
    recommendation: dict
) -> dict:

    return {
        "selection_mode":
            recommendation.get(
                "selection_mode",
                "auto"
            ),

        "user_prompt":
            design_request.user_prompt,

        "layout_preference":
            recommendation.get(
                "layout_preference"
            ),

        "product_requirements": [
            requirement.model_dump(
                mode="json"
            )
            for requirement
            in design_request.product_requirements
        ],

        "applied_requirements":
            recommendation.get(
                "applied_requirements",
                {
                    "required_quantities": {},
                    "excluded_categories": [],
                    "explicit_auto_categories": []
                }
            )
    }


# =========================================================
# POST /designs/recommend
#
# Manual JSON test using full DesignRequest.
# =========================================================

@router.post(
    "/designs/recommend"
)
def recommend_design(
    request: DesignRequest
):

    connection = (
        get_connection()
    )

    cursor = (
        connection.cursor()
    )

    try:
        cursor.execute(
            """
            SELECT *
            FROM furniture
            WHERE availability = 1
              AND COALESCE(catalog_status, 'active') = 'active'
              AND COALESCE(design_eligible, 1) = 1
            """
        )

        rows = (
            cursor.fetchall()
        )

    finally:
        connection.close()

    all_items = [
        row_to_furniture(
            row
        )
        for row
        in rows
    ]

    room_type = (
        request
        .room_type
        .value
    )

    room_items = [
        item
        for item in all_items
        if (
            room_type
            in item["room_types"]
        )
    ]

    if not room_items:
        raise HTTPException(
            status_code=404,
            detail=(
                "No available furniture "
                "items found for this room type"
            )
        )

    try:
        recommendation = (
            generate_recommendation(
                room_items=
                    room_items,

                request=
                    request
            )
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        ) from exc

    return recommendation


# =========================================================
# POST /designs/recommend/{room_request_id}
#
# No body / empty body:
# -> Auto Design
#
# With product_requirements:
# -> Guided Design
#
# This endpoint chooses products.
# =========================================================
@router.post("/analyze-room/{room_request_id}")
def analyze_room(
    room_request_id: int
):
    room_request, _ = load_room_and_furniture(
        room_request_id
    )
    design_request = build_design_request(room_request=room_request, options=None)

    room_image_path = get_room_image_path(
        room_request["image_filename"]
    )

    try:
        (
            analysis,
            analysis_cached
        ) = get_or_create_room_analysis(
            room_request_id=room_request_id,
            image_path=room_image_path,
            room_type=room_request["room_type"],
            room_dimensions=design_request.room_dimensions,
        )

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to analyze room image: "
                f"{str(error)}"
            )
        ) from error

    return {
        "message": "Room analyzed successfully",
        "room_request_id": room_request_id,
        "room_type": room_request["room_type"],
        "analysis_cached": analysis_cached,
        "room_image": {
            "filename": room_request[
                "image_filename"
            ],
            "local_path": str(room_image_path)
        },
        "analysis": analysis.model_dump()
    }

@router.post(
    "/designs/recommend/{room_request_id}"
)
def recommend_design_for_room(
    room_request_id: int,

    options: RoomDesignOptions | None = Body(
        default=None
    )
):

    (
        room_request,
        design_request,
        room_analysis,
        recommendation
    ) = get_room_recommendation(
        room_request_id=
            room_request_id,

        options=
            options
    )

    recommended_items = (
        recommendation[
            "recommended_items"
        ]
    )

    return {
        "room_request": {
            "id":
                room_request[
                    "id"
                ],

            "image_filename":
                room_request[
                    "image_filename"
                ],

            "original_filename":
                room_request[
                    "original_filename"
                ],

            "content_type":
                room_request[
                    "content_type"
                ],

            "room_type":
                design_request
                .room_type
                .value,

            "budget":
                design_request
                .budget,

            "style": (
                design_request
                .style
                .value

                if design_request.style

                else None
            ),

            "preferred_colors":
                design_request
                .preferred_colors
        },

        "room_analysis":
            room_analysis.model_dump(),

        "design_director_model":
            recommendation.get(
                "design_director_model"
            ),

        "design_strategy_cached":
            recommendation.get(
                "design_strategy_cached"
            ),

        "design_strategy_fallback":
            recommendation.get(
                "design_strategy_fallback",
                False
            ),

        "design_strategy_signature":
            recommendation.get(
                "design_strategy_signature"
            ),

        "design_strategy":
            recommendation.get(
                "design_strategy"
            ),

        "design_options":
            build_design_options_response(
                design_request=
                    design_request,

                recommendation=
                    recommendation
            ),

        "selected_furniture_ids":
            recommendation[
                "selected_furniture_ids"
            ],

        "selection_id": recommendation.get("selection_id"),
        "selection_reused": recommendation.get("selection_reused", False),
        "selection_status": recommendation.get("selection_status"),
        "room_completeness": recommendation.get("room_completeness"),
        "architectural_constraint_map": recommendation.get(
            "architectural_constraint_map"
        ),
        "composition_validation": recommendation.get("composition_validation"),
        "render_reference_budget": recommendation.get("render_reference_budget"),
        "visual_design_authority": VISUAL_DESIGN_AUTHORITY,
        "placement_planner_called": False,
        "pre_render_placement_required": False,
        "placement_plan": None,
        "placement_plan_status": PLACEMENT_PLAN_STATUS,

        "selected_products":
            recommendation.get(
                "selected_products",
                []
            ),

        "selected_furniture":
            build_selected_furniture_response(
                recommended_items
            ),

        "total_price":
            recommendation[
                "total_price"
            ],

        "currency":
            recommendation[
                "currency"
            ],

        "recommendation":
            recommendation
    }


# =========================================================
# POST /designs/selections/{selection_id}/revise
#
# Studio edits never mutate a locked selection. They create/reuse a new
# immutable snapshot which is then passed explicitly to the renderer.
# No room analysis, AI selection, or image generation happens here.
# =========================================================

@router.post("/designs/selections/{selection_id}/revise")
def revise_design_selection(
    selection_id: str,
    request: InventoryRevisionRequest,
):
    base_selection = load_design_selection(selection_id)
    room_request, active_catalog_items = load_room_and_furniture(
        base_selection.room_request_id
    )
    design_request = build_design_request(room_request=room_request, options=None)
    revised_items, composition = build_inventory_revision(
        base_items=base_selection.selected_items,
        catalog_items=active_catalog_items,
        requested_lines=[item.model_dump() for item in request.items],
        room_type=design_request.room_type.value,
        room_dimensions=design_request.room_dimensions,
    )

    catalog_signature = build_catalog_signature(active_catalog_items)
    revision_blueprint = dict(base_selection.blueprint or {})
    revision_blueprint.update(
        {
            "inventory_user_edited": True,
            "base_selection_id": base_selection.selection_id,
            "inventory_revision_contract": "2026-07-18.1",
        }
    )
    revision_signature = build_design_selection_signature(
        room_request_id=base_selection.room_request_id,
        template_id=base_selection.template_id,
        blueprint_signature=base_selection.blueprint_signature,
        catalog_signature=catalog_signature,
        room_analysis_signature=base_selection.room_analysis_signature,
        selected_items=revised_items,
    )
    revised_selection, reused = create_or_reuse_design_selection(
        room_request_id=base_selection.room_request_id,
        template_id=base_selection.template_id,
        mode=base_selection.mode,
        selection_signature=revision_signature,
        blueprint_signature=base_selection.blueprint_signature,
        catalog_signature=catalog_signature,
        room_analysis_signature=base_selection.room_analysis_signature,
        selected_items=revised_items,
        blueprint=revision_blueprint,
        composition_validation=composition.model_dump(mode="json"),
    )
    total_price, currency = calculate_selected_items_totals(
        revised_selection.selected_items
    )
    strategy_signature = (
        revision_blueprint.get("design_strategy_signature")
        if revision_blueprint
        else None
    )
    return {
        "message": "Edited inventory saved as a new immutable selection.",
        "room_request_id": base_selection.room_request_id,
        "template_id": base_selection.template_id,
        "mode": base_selection.mode,
        "base_selection_id": base_selection.selection_id,
        "selection_id": revised_selection.selection_id,
        "selection_reused": reused,
        "selection_status": revised_selection.status,
        "design_strategy_signature": strategy_signature,
        "selected_furniture_ids": [
            int(item["id"]) for item in revised_selection.selected_items
        ],
        "selected_products": [
            {
                "id": int(item["id"]),
                "quantity": int(item.get("quantity", 1)),
                "product_origin": public_product_origin(item),
                "selection_role": item.get("selection_role"),
            }
            for item in revised_selection.selected_items
        ],
        "selected_furniture": build_selected_furniture_response(
            revised_selection.selected_items
        ),
        "composition_validation": composition.model_dump(mode="json"),
        "total_price": total_price,
        "currency": currency,
        "ai_selector_called": False,
        "room_analysis_called": False,
        "image_generation_called": False,
    }


# =========================================================
# Exact immutable-render context helper
#
# IMPORTANT:
#
# - No AI Selector call happens here.
# - Exact selected products come from the immutable selection ID.
# - Room analysis is cached by room_request_id.
# - No placement plan or product reselection occurs.
# =========================================================

def get_exact_render_preparation_context(
    room_request_id: int,
    render_request: RenderDesignRequest
) -> tuple:

    (
        room_request,
        design_request,
        recommended_items,
        persisted_selection,
    ) = get_exact_render_context(
        room_request_id=
            room_request_id,

        render_request=
            render_request
    )

    room_image_path = (
        get_room_image_path(
            room_request[
                "image_filename"
            ]
        )
    )

    room_type = (
        design_request
        .room_type
        .value
    )

    style = (
        design_request
        .style
        .value

        if design_request.style

        else None
    )

    try:
        (
            room_analysis,
            room_analysis_cached
        ) = get_or_create_room_analysis(
            room_request_id=
                room_request_id,

            image_path=
                room_image_path,

            room_type=
                room_type,

            room_dimensions=
                design_request.room_dimensions,
        )

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to load or analyze room before render preparation: "
                f"{str(error)}"
            )
        ) from error

    design_strategy = None

    design_strategy_signature = (
        render_request.design_strategy_signature
        or (
            persisted_selection.blueprint.get("design_strategy_signature")
            if persisted_selection.blueprint
            else None
        )
    )

    if design_strategy_signature is not None:
        design_strategy = get_saved_design_strategy(
            room_request_id=room_request_id,
            strategy_signature=(
                design_strategy_signature
            )
        )

        if design_strategy is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": (
                        "The supplied design_strategy_signature was not found "
                        "for this room request."
                    ),
                    "design_strategy_signature": (
                        design_strategy_signature
                    )
                }
            )

        if not bool(
            (persisted_selection.blueprint or {}).get("inventory_user_edited")
        ):
            validate_selection_against_design_strategy(
                selected_items=recommended_items,
                design_strategy=design_strategy,
                constraints={}
            )

    composition_validation = validate_composition_fit(
        selected_items=recommended_items,
        room_dimensions=(
            design_request.room_dimensions
            or room_analysis.provided_dimensions_cm
        ),
        obstacles=room_analysis.obstacles,
        circulation_paths=room_analysis.circulation_paths,
        room_type=design_request.room_type.value,
    )
    if composition_validation.status == CompositionFitStatus.INVALID:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Rendering stopped before generation because the locked "
                    "inventory has a coordinate-independent physical conflict: at "
                    "least one product cannot fit in either orientation, or the major "
                    "independently placed furniture exceeds the room floor itself. "
                    "Edit the inventory or correct the room dimensions, then save it "
                    "again. Products will never be resized or structurally changed "
                    "to force a fit."
                ),
                "composition_validation": composition_validation.model_dump(mode="json"),
                "automatic_paid_request_sent": False,
            },
        )
    architectural_constraints = build_architectural_constraint_map(
        room_analysis=room_analysis,
        room_dimensions=(
            design_request.room_dimensions
            or room_analysis.provided_dimensions_cm
        ),
    )

    return (
        room_request,
        design_request,
        recommended_items,
        room_image_path,
        room_analysis,
        room_analysis_cached,
        design_strategy,
        design_strategy_signature,
        persisted_selection,
        architectural_constraints,
        composition_validation,
    )


def get_exact_layout_plan_context(
    room_request_id: int,
    render_request: RenderDesignRequest,
) -> tuple:
    """Deprecated compatibility alias; no placement plan is created."""

    return get_exact_render_preparation_context(room_request_id, render_request)


def _explicit_user_render_requirements(
    *,
    persisted_selection: PersistedDesignSelection,
    design_strategy: DesignStrategy | None,
) -> list[str]:
    """Expose only genuinely user-requested spatial preferences to rendering."""

    if persisted_selection.template_id is not None or design_strategy is None:
        return []
    layout = design_strategy.layout
    if layout.requested_layout and layout.requested_layout_supported is True:
        return [
            "The user explicitly requested this layout preference: "
            f"{layout.requested_layout}."
        ]
    return []


def _build_renderer_contract(
    *,
    persisted_selection: PersistedDesignSelection,
    room_image_path: Path,
    room_analysis: RoomAnalysis,
    architectural_constraints,
    selected_items: list[dict],
    furniture_reference_images: list[dict],
    room_type: str,
    style: str | None,
    design_strategy: DesignStrategy | None = None,
    template: dict | None = None,
) -> tuple:
    template_product_source = persisted_selection.template_id is not None
    renderer_template = (
        {
            "id": template.get("id"),
            "name": template.get("name"),
            "semantics": "product_source_only",
        }
        if template_product_source and template is not None
        else template
    )
    design_intent = {
        "design_strategy": (
            design_strategy.model_dump(mode="json")
            if design_strategy is not None and not template_product_source
            else None
        ),
        "locked_blueprint": (
            None if template_product_source else persisted_selection.blueprint
        ),
        "template_semantics": (
            "product_source_only" if template_product_source else None
        ),
        "template_layout_constraints_applied": False,
        "composition_validation": _core_composition_validation(
            persisted_selection.composition_validation
        ),
    }
    manifest = build_render_manifest(
        selection_id=persisted_selection.selection_id,
        selection_signature=persisted_selection.selection_signature,
        room_request_id=persisted_selection.room_request_id,
        template_id=persisted_selection.template_id,
        design_mode=persisted_selection.mode,
        room_image_path=room_image_path,
        selected_items=selected_items,
        furniture_reference_images=furniture_reference_images,
        room_analysis=room_analysis,
        room_analysis_signature=persisted_selection.room_analysis_signature,
        architectural_constraints=architectural_constraints,
        template=renderer_template,
        template_blueprint_signature=(
            None
            if template_product_source
            else persisted_selection.blueprint_signature
        ),
        template_product_source_signature=(
            persisted_selection.blueprint_signature
            if template_product_source
            else None
        ),
        explicit_user_requirements=_explicit_user_render_requirements(
            persisted_selection=persisted_selection,
            design_strategy=design_strategy,
        ),
        design_intent=design_intent,
        render_prompt_version=RENDER_PROMPT_VERSION,
        renderer_model=MODEL_NAME,
    )
    prompt = build_generation_prompt(
        recommended_items=selected_items,
        room_type=room_type,
        style=style,
        template_context=renderer_template,
        render_manifest=manifest,
    )
    return manifest, prompt


def _raise_if_render_compliance_failed(
    *,
    compliance: RenderComplianceResult,
    generated_design_id: str,
    generated_result: dict,
    generated_image_url: str,
) -> None:
    """Reject failed renders after preserving the original image and audit row."""

    if compliance.status != "failed_compliance":
        return
    raise HTTPException(
        status_code=422,
        detail={
            "message": (
                "Generated image failed post-render compliance and was not "
                "returned as a successful design. The original generated image "
                "was preserved for audit."
            ),
            "render_status": "failed_compliance",
            "compliance_status": compliance.status,
            "hard_compliance_failure": compliance.hard_compliance_failure,
            "hard_failure_codes": compliance.hard_failure_codes,
            "failure_codes": compliance.failure_codes,
            "camera_view_preserved": compliance.camera_view_preserved,
            "room_finish_materials_preserved": (
                compliance.room_finish_materials_preserved
            ),
            "door_evidence": compliance.door_evidence,
            "window_evidence": compliance.window_evidence,
            "circulation_evidence": compliance.circulation_evidence,
            "failure_reasons": compliance.failure_reasons,
            "violations": compliance.violations,
            "warnings": compliance.warnings,
            "generated_design_id": generated_design_id,
            "generated_image": {
                "filename": generated_result["generated_image_filename"],
                "local_path": generated_result["generated_image_path"],
                "image_url": generated_image_url,
                "preserved": True,
            },
            "automatic_paid_retry_enabled": (
                compliance.automatic_paid_retry_enabled
            ),
            "automatic_retry_attempted": compliance.automatic_retry_attempted,
            "retry_policy": "disabled_requires_explicit_enablement",
        },
    )


# =========================================================
# POST /designs/plan-layout/{room_request_id}
#
# Deprecated compatibility endpoint. It now exposes the semantic render
# preparation contract and never invokes Placement Planner AI.
#
# NO AI Selector call.
# NO image-generation call.
# =========================================================

@router.post(
    "/designs/plan-layout/{room_request_id}"
)
def plan_layout_for_room(
    room_request_id: int,
    render_request: RenderDesignRequest
):
    (
        room_request,
        design_request,
        recommended_items,
        room_image_path,
        room_analysis,
        room_analysis_cached,
        design_strategy,
        design_strategy_signature,
        persisted_selection,
        architectural_constraints,
        composition_validation,
    ) = get_exact_render_preparation_context(
        room_request_id=room_request_id,
        render_request=render_request,
    )
    return {
        "message": "Placement planning is not required; semantic render constraints are ready.",
        "selection_source": "immutable_selection",
        "selection_id": persisted_selection.selection_id,
        "selection_status": persisted_selection.status,
        "ai_selector_called": False,
        "visual_design_authority": VISUAL_DESIGN_AUTHORITY,
        "placement_planner_called": False,
        "pre_render_placement_required": False,
        "placement_plan": None,
        "placement_plan_status": PLACEMENT_PLAN_STATUS,
        "placement_plan_cached": False,
        "placement_selection_signature": None,
        "room_request_id": room_request_id,
        "room_type": design_request.room_type.value,
        "style": (
            design_request.style.value if design_request.style else None
        ),
        "room_image": {
            "filename": room_request["image_filename"],
            "local_path": str(room_image_path),
            "image_url": f"/uploads/rooms/{room_request['image_filename']}",
        },
        "room_analysis_cached": room_analysis_cached,
        "room_analysis": room_analysis.model_dump(),
        "architectural_constraint_map": architectural_constraints.model_dump(mode="json"),
        "composition_validation": composition_validation.model_dump(mode="json"),
        "design_strategy_signature": design_strategy_signature,
        "design_strategy": (
            design_strategy.model_dump()
            if design_strategy is not None
            else None
        ),
        "selected_products": [
            {
                "id": int(item["id"]),
                "quantity": int(item.get("quantity", 1)),
            }
            for item in recommended_items
        ],
        "selected_furniture": build_selected_furniture_response(recommended_items),
    }


# =========================================================
# POST /designs/generate/{room_request_id}
#
# Backend-only dry run for immutable products + renderer contract.
#
# NO AI Selector call.
# NO image-generation call.
#
# Placement Planner is never called.
# =========================================================

@router.post(
    "/designs/generate/{room_request_id}"
)
def prepare_design_generation(
    room_request_id: int,
    render_request: RenderDesignRequest
):

    (
        room_request,
        design_request,
        recommended_items,
        room_image_path,
        room_analysis,
        room_analysis_cached,
        design_strategy,
        design_strategy_signature,
        persisted_selection,
        architectural_constraints,
        composition_validation,
    ) = get_exact_render_preparation_context(
        room_request_id=room_request_id,
        render_request=render_request,
    )
    furniture_reference_images = prepare_furniture_reference_images(
        recommended_items=recommended_items
    )
    style = design_request.style.value if design_request.style else None
    render_manifest, render_prompt = _build_renderer_contract(
        persisted_selection=persisted_selection,
        room_image_path=room_image_path,
        room_analysis=room_analysis,
        architectural_constraints=architectural_constraints,
        selected_items=recommended_items,
        furniture_reference_images=furniture_reference_images,
        room_type=design_request.room_type.value,
        style=style,
        design_strategy=design_strategy,
    )
    total_price, currency = calculate_selected_items_totals(recommended_items)
    return {
        "message": "Immutable selection and GPT Image designer contract prepared; no render was performed.",
        "selection_source": "immutable_selection",
        "selection_id": persisted_selection.selection_id,
        "selection_status": persisted_selection.status,
        "ai_selector_called": False,
        "visual_design_authority": VISUAL_DESIGN_AUTHORITY,
        "placement_planner_called": False,
        "pre_render_placement_required": False,
        "placement_plan": None,
        "placement_plan_status": PLACEMENT_PLAN_STATUS,
        "placement_plan_cached": False,
        "placement_selection_signature": None,
        "room_request_id": room_request_id,
        "room_type": design_request.room_type.value,
        "style": style,
        "room_analysis_cached": room_analysis_cached,
        "room_analysis": room_analysis.model_dump(),
        "architectural_constraint_map": architectural_constraints.model_dump(mode="json"),
        "composition_validation": composition_validation.model_dump(mode="json"),
        "design_strategy_signature": design_strategy_signature,
        "design_strategy": (
            design_strategy.model_dump()
            if design_strategy is not None
            else None
        ),
        "room_image": {
            "filename": room_request["image_filename"],
            "local_path": str(room_image_path),
            "image_url": f"/uploads/rooms/{room_request['image_filename']}",
        },
        "selected_furniture_ids": [
            int(item["id"]) for item in recommended_items
        ],
        "selected_products": [
            {"id": int(item["id"]), "quantity": int(item["quantity"])}
            for item in recommended_items
        ],
        "selected_furniture": build_selected_furniture_response(recommended_items),
        "furniture_reference_images": furniture_reference_images,
        "render_reference_budget": render_manifest.render_reference_budget,
        "render_manifest": render_manifest.model_dump(mode="json"),
        "render_prompt": render_prompt,
        **build_render_prompt_debug_metadata(render_prompt),
        "total_price": total_price,
        "currency": currency,
    }


# =========================================================
# POST /designs/render/{room_request_id}
#
# Real GPT Image render using:
#
# - exact room image
# - exact selected products
# - exact quantities
# - cached room analysis
# - semantic architecture/safety constraints
# - visual design authority delegated to GPT Image 2
#
# IMPORTANT:
# NO AI Selector call.
# =========================================================

@router.get("/designs/generated/{room_request_id}/latest")
def get_latest_generated_design_audit(
    room_request_id: int,
    selection_id: str,
):
    """Recover a completed render after a browser/proxy disconnect."""

    audit = load_latest_generated_design(
        room_request_id=room_request_id,
        selection_id=selection_id,
    )
    if audit is None:
        raise HTTPException(
            status_code=404,
            detail="No completed render exists for this locked selection.",
        )
    compliance = audit["compliance"]
    generated_filename = audit["generated_image_filename"]
    if compliance.get("status") == "failed_compliance":
        return {
            "message": (
                "Generated image failed post-render compliance and was not "
                "returned as a successful design. The original generated image "
                "was preserved for audit."
            ),
            "render_status": "failed_compliance",
            "compliance_status": compliance.get("status"),
            "hard_compliance_failure": compliance.get(
                "hard_compliance_failure", False
            ),
            "hard_failure_codes": compliance.get("hard_failure_codes", []),
            "failure_codes": compliance.get("failure_codes", []),
            "camera_view_preserved": compliance.get("camera_view_preserved"),
            "room_finish_materials_preserved": compliance.get(
                "room_finish_materials_preserved"
            ),
            "door_evidence": compliance.get("door_evidence"),
            "window_evidence": compliance.get("window_evidence"),
            "circulation_evidence": compliance.get("circulation_evidence"),
            "failure_reasons": compliance.get("failure_reasons", []),
            "violations": compliance.get("violations", []),
            "warnings": compliance.get("warnings", []),
            "generated_design_id": audit["id"],
            "generated_image": {
                "filename": generated_filename,
                "local_path": (
                    f"uploads/generated_designs/{generated_filename}"
                ),
                "image_url": (
                    f"/uploads/generated_designs/{generated_filename}"
                ),
                "preserved": True,
            },
            "automatic_paid_retry_enabled": compliance.get(
                "automatic_paid_retry_enabled", False
            ),
            "automatic_retry_attempted": compliance.get(
                "automatic_retry_attempted", False
            ),
            "retry_policy": "disabled_requires_explicit_enablement",
            "selection_id": audit["selection_id"],
            "created_at": audit["created_at"],
            "recovered_from_audit": True,
        }
    return {
        "message": "A completed render audit exists for this locked selection.",
        "render_status": "completed",
        "generated_design_id": audit["id"],
        "generated_image": {
            "filename": generated_filename,
            "image_url": f"/uploads/generated_designs/{generated_filename}",
            "preserved": True,
        },
        "selection_id": audit["selection_id"],
        "created_at": audit["created_at"],
        "recovered_from_audit": True,
    }

@router.post(
    "/designs/render/{room_request_id}"
)
def render_design_for_room(
    room_request_id: int,
    render_request: RenderDesignRequest
):

    (
        room_request,
        design_request,
        recommended_items,
        room_image_path,
        room_analysis,
        room_analysis_cached,
        design_strategy,
        design_strategy_signature,
        persisted_selection,
        architectural_constraints,
        composition_validation,
    ) = get_exact_render_preparation_context(
        room_request_id=room_request_id,
        render_request=render_request,
    )
    if not recommended_items:
        raise HTTPException(
            status_code=400,
            detail="No products were selected for rendering.",
        )
    furniture_reference_images = prepare_furniture_reference_images(
        recommended_items=recommended_items
    )
    style = design_request.style.value if design_request.style else None
    render_manifest, _ = _build_renderer_contract(
        persisted_selection=persisted_selection,
        room_image_path=room_image_path,
        room_analysis=room_analysis,
        architectural_constraints=architectural_constraints,
        selected_items=recommended_items,
        furniture_reference_images=furniture_reference_images,
        room_type=design_request.room_type.value,
        style=style,
        design_strategy=design_strategy,
    )
    total_price, currency = calculate_selected_items_totals(recommended_items)
    generated_result = generate_furnished_room_image(
        room_request_id=room_request_id,
        room_image_path=room_image_path,
        furniture_reference_images=furniture_reference_images,
        recommended_items=recommended_items,
        room_type=design_request.room_type.value,
        style=style,
        render_manifest=render_manifest,
    )
    generated_filename = generated_result["generated_image_filename"]
    generated_image_url = f"/uploads/generated_designs/{generated_filename}"
    compliance_result = validate_render_compliance(
        generated_image_path=Path(generated_result["generated_image_path"]),
        room_image_path=room_image_path,
        template_image_path=None,
        manifest=render_manifest.model_dump(mode="json"),
    )
    generated_design_id = save_generated_design(
        room_request_id=room_request_id,
        selection_id=persisted_selection.selection_id,
        generated_image_filename=generated_filename,
        manifest=render_manifest.model_dump(mode="json"),
        compliance=compliance_result.model_dump(mode="json"),
        model_name=generated_result.get("model"),
    )
    _raise_if_render_compliance_failed(
        compliance=compliance_result,
        generated_design_id=generated_design_id,
        generated_result=generated_result,
        generated_image_url=generated_image_url,
    )
    return {
        "message": "Design rendered; inspect compliance status before treating it as verified.",
        "selection_source": "immutable_selection",
        "selection_id": persisted_selection.selection_id,
        "selection_status": persisted_selection.status,
        "generated_design_id": generated_design_id,
        "ai_selector_called": False,
        "visual_design_authority": VISUAL_DESIGN_AUTHORITY,
        "placement_planner_called": False,
        "pre_render_placement_required": False,
        "placement_plan": None,
        "placement_plan_status": PLACEMENT_PLAN_STATUS,
        "placement_plan_cached": False,
        "placement_selection_signature": None,
        "render_prompt_length": generated_result["render_prompt_length"],
        "render_prompt_target_limit": generated_result[
            "render_prompt_target_limit"
        ],
        "render_prompt_hard_limit": generated_result[
            "render_prompt_hard_limit"
        ],
        "render_prompt_compacted": generated_result[
            "render_prompt_compacted"
        ],
        "room_request_id": room_request_id,
        "room_type": design_request.room_type.value,
        "style": style,
        "room_analysis_cached": room_analysis_cached,
        "room_analysis": room_analysis.model_dump(),
        "architectural_constraint_map": architectural_constraints.model_dump(mode="json"),
        "composition_validation": composition_validation.model_dump(mode="json"),
        "design_strategy_signature": design_strategy_signature,
        "design_strategy": (
            design_strategy.model_dump()
            if design_strategy is not None
            else None
        ),
        "render_manifest": render_manifest.model_dump(mode="json"),
        "compliance": compliance_result.model_dump(mode="json"),
        "generated_image": {
            "filename": generated_filename,
            "local_path": generated_result["generated_image_path"],
            "image_url": generated_image_url,
            "model": generated_result["model"],
            "input_image_count": generated_result["input_image_count"],
            "output_size": generated_result["output_size"],
        },
        "room_image": {
            "filename": room_request["image_filename"],
            "local_path": str(room_image_path),
            "image_url": f"/uploads/rooms/{room_request['image_filename']}",
        },
        "selected_furniture_ids": [
            int(item["id"]) for item in recommended_items
        ],
        "selected_products": [
            {"id": int(item["id"]), "quantity": int(item["quantity"])}
            for item in recommended_items
        ],
        "selected_furniture": build_selected_furniture_response(recommended_items),
        "total_price": total_price,
        "currency": currency,
    }


# =========================================================
# Read-only template browsing
# =========================================================

@router.get("/designs/templates")
def list_ready_design_templates(room_type: str | None = None):
    templates = load_active_design_templates(room_type=room_type)
    return [
        {
            **template,
            "preview_image_url": (
                f"/uploads/design_templates/{template['preview_image_filename']}"
                if template.get("preview_image_filename")
                else None
            ),
        }
        for template in templates
    ]


@router.get("/designs/templates/{template_id}")
def get_ready_design_template(template_id: int):
    return load_design_template_detail(template_id)


# =========================================================
# POST /designs/templates/match/{room_request_id}
#
# Auto Design MVP:
# Analyze room and return the closest ready design templates.
# =========================================================

@router.post(
    "/designs/templates/match/{room_request_id}"
)
def match_ready_design_templates(
    room_request_id: int,
    limit: int = 5
):
    (
        room_request,
        all_items
    ) = load_room_and_furniture(
        room_request_id
    )

    design_request = build_design_request(
        room_request=room_request,
        options=None
    )

    room_type = design_request.room_type.value

    room_image_path = get_room_image_path(
        room_request["image_filename"]
    )

    try:
        (
            room_analysis,
            analysis_cached
        ) = get_or_create_room_analysis(
            room_request_id=room_request_id,
            image_path=room_image_path,
            room_type=room_type,
            room_dimensions=design_request.room_dimensions
        )

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to analyze room before template matching: "
                f"{str(error)}"
            )
        ) from error

    style = (
        design_request.style.value
        if design_request.style
        else None
    )

    matched_templates = match_design_templates(
        room_analysis=room_analysis,
        room_type=room_type,
        style=style,
        preferred_colors=design_request.preferred_colors,
        limit=limit
    )

    room_request_response = dict(room_request)
    room_request_response["preferred_colors"] = (
        parse_preferred_colors(
            room_request.get("preferred_colors")
        )
    )

    return {
        "room_request": room_request_response,
        "room_analysis": room_analysis.model_dump(),
        "room_analysis_cached": analysis_cached,
        "matched_templates": matched_templates
    }

# =========================================================
# Ready-template application context
# =========================================================

def get_ready_template_application_context(
    room_request_id: int,
    template_id: int,
    correlation_id: str,
):
    (
        room_request,
        all_items,
    ) = load_room_and_furniture(
        room_request_id
    )

    design_request = build_design_request(
        room_request=room_request,
        options=None,
    )

    room_type = design_request.room_type.value

    room_image_path = get_room_image_path(
        room_request["image_filename"]
    )

    try:
        with timed_pipeline_stage(correlation_id, "room_analysis"):
            (
                room_analysis,
                room_analysis_cached,
            ) = get_or_create_room_analysis(
                room_request_id=room_request_id,
                image_path=room_image_path,
                room_type=room_type,
                room_dimensions=design_request.room_dimensions,
            )

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to analyze room before applying the selected "
                f"ready design: {str(error)}"
            ),
        ) from error

    authoritative_room_dimensions = (
        design_request.room_dimensions
        or room_analysis.provided_dimensions_cm
    )

    style = (
        design_request.style.value
        if design_request.style
        else None
    )

    with timed_pipeline_stage(correlation_id, "template_slot_resolution"):
        (
        template,
        template_product_source,
        template_product_source_cached,
        template_product_source_signature,
        exact_template_items,
        rejected_exact_items,
        selected_items,
        completion_metadata,
        fit_results,
        composition_validation,
        template_design_strategy,
        template_preview_path,
        ) = load_template_application_data(
            template_id=template_id,
            room_type=room_type,
            room_analysis=room_analysis,
            room_dimensions=authoritative_room_dimensions,
            all_items=all_items,
            requested_style=style,
            preferred_colors=design_request.preferred_colors,
        )

    room_analysis_signature = build_room_analysis_signature(
        image_path=room_image_path,
        room_type=room_type,
        room_dimensions=authoritative_room_dimensions,
    )
    try:
        with timed_pipeline_stage(correlation_id, "broad_composition_validation"):
            (
                selected_items,
                composition_validation,
                composition_reduction,
            ) = _validate_draft_selection_for_render(
                room_analysis=room_analysis,
                selected_items=selected_items,
                composition_validation=composition_validation,
            )

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to adapt the selected ready design to the "
                f"uploaded room: {str(error)}"
            ),
        ) from error

    # Broad fit may remove only lower-priority system additions. The immutable
    # signature reflects the exact final inventory; the template contributes
    # products only and never becomes renderer layout guidance.
    template_design_strategy = None
    exact_template_items = [
        item
        for item in selected_items
        if item.get("selection_role") == "template_selected"
    ]
    fit_results = validate_final_template_products_fit(
        selected_items=selected_items,
        room_dimensions=authoritative_room_dimensions,
    )
    _reconcile_slot_metadata(completion_metadata, selected_items)
    completion_metadata.update(
        {
            "visual_design_authority": VISUAL_DESIGN_AUTHORITY,
            "placement_planner_called": False,
            "pre_render_placement_required": False,
            "placement_plan": None,
            "placement_plan_status": PLACEMENT_PLAN_STATUS,
            "composition_reduction": composition_reduction,
        }
    )
    completion_metadata["draft_to_final_flow"] = (
        "draft_selection -> room_completeness -> composition_fit -> "
        "immutable_final_selection -> gpt_image_visual_design"
    )
    completion_metadata["render_reference_budget"][
        "selected_unique_product_images"
    ] = len(selected_items)
    immutable_selection_signature = build_design_selection_signature(
        room_request_id=room_request_id,
        template_id=template_id,
        blueprint_signature=template_product_source_signature,
        catalog_signature=completion_metadata["catalog_signature"],
        room_analysis_signature=room_analysis_signature,
        selected_items=selected_items,
    )
    with timed_pipeline_stage(correlation_id, "selection_persistence"):
        persisted_selection, selection_reused = create_or_reuse_design_selection(
            room_request_id=room_request_id,
            template_id=template_id,
            mode="template_product_source",
            selection_signature=immutable_selection_signature,
            blueprint_signature=template_product_source_signature,
            catalog_signature=completion_metadata["catalog_signature"],
            room_analysis_signature=room_analysis_signature,
            selected_items=selected_items,
            blueprint=template_product_source,
            composition_validation={
                **composition_validation.model_dump(mode="json"),
                "pre_render_validation_version": "2026-07-19.1-overlap-aware",
                "coordinate_layout_validation_required": False,
                "composition_reduction": composition_reduction,
            },
        )
    # Render consumes only this immutable validated snapshot.
    selected_items = persisted_selection.selected_items

    return (
        room_request,
        design_request,
        room_image_path,
        room_analysis,
        room_analysis_cached,
        template,
        template_product_source,
        template_product_source_cached,
        template_product_source_signature,
        exact_template_items,
        rejected_exact_items,
        selected_items,
        completion_metadata,
        fit_results,
        composition_validation,
        template_design_strategy,
        template_preview_path,
        persisted_selection,
        selection_reused,
    )


# =========================================================
# POST /designs/templates/apply/{room_request_id}
#
# Product-source template:
# User chooses a curated product collection. Backend matches its linked
# products, completes essential functional gaps from the live catalog, and
# locks the exact inventory. No template layout is sent to the renderer.
#
# NO AI Selector call.
# NO image generation call.
# =========================================================

@router.post(
    "/designs/templates/apply/{room_request_id}"
)
def apply_ready_design_template(
    room_request_id: int,
    request: ApplyTemplateRequest,
):
    correlation_id = str(uuid4())
    (
        room_request,
        design_request,
        room_image_path,
        room_analysis,
        room_analysis_cached,
        template,
        template_product_source,
        template_product_source_cached,
        template_product_source_signature,
        exact_template_items,
        rejected_exact_items,
        selected_items,
        completion_metadata,
        fit_results,
        composition_validation,
        template_design_strategy,
        template_preview_path,
        persisted_selection,
        selection_reused,
    ) = get_ready_template_application_context(
        room_request_id=room_request_id,
        template_id=request.template_id,
        correlation_id=correlation_id,
    )
    log_pipeline_event(
        correlation_id,
        "cache_summary",
        "observed",
        room_analysis_cached=room_analysis_cached,
        product_source_cached=template_product_source_cached,
        selection_reused=selection_reused,
    )

    total_price, currency = calculate_selected_items_totals(
        selected_items
    )

    preview_filename = template.get(
        "preview_image_filename"
    )

    return {
        "message": (
            "Template products matched and the final inventory is ready for render"
        ),
        "correlation_id": correlation_id,
        "design_mode": "template_product_source",
        "template_semantics": "product_source_only",
        "template_layout_constraints_applied": False,
        "selection_id": persisted_selection.selection_id,
        "selection_reused": selection_reused,
        "selection_status": persisted_selection.status,
        "selection_source": (
            "selected_ready_template_plus_db_completion"
        ),
        "ai_selector_called": completion_metadata[
            "ai_selector_called"
        ],
        "design_director_called": False,
        "visual_design_authority": VISUAL_DESIGN_AUTHORITY,
        "placement_planner_called": False,
        "pre_render_placement_required": False,
        "placement_plan": None,
        "placement_plan_status": PLACEMENT_PLAN_STATUS,
        "placement_plan_cached": False,
        "placement_selection_signature": None,
        "room_request_id": room_request_id,
        "room_type": design_request.room_type.value,
        "style": (
            design_request.style.value
            if design_request.style
            else None
        ),
        "room_analysis_cached": room_analysis_cached,
        "room_analysis": room_analysis.model_dump(),
        "selected_template": {
            **template,
            "preview_image_url": (
                f"/uploads/design_templates/{preview_filename}"
                if preview_filename
                else None
            ),
            "preview_image_available": (
                template_preview_path is not None
            ),
        },
        "template_product_source_cached": template_product_source_cached,
        "template_product_source_signature": template_product_source_signature,
        "template_product_source": template_product_source,
        "template_completion": completion_metadata,
        "room_completeness": completion_metadata.get("room_completeness"),
        "architectural_constraint_map": completion_metadata.get(
            "architectural_constraint_map"
        ),
        "render_reference_budget": completion_metadata.get(
            "render_reference_budget"
        ),
        "exact_template_products_used": [
            {
                "id": int(item["id"]),
                "name": item["name"],
                "category": item["category"],
                "quantity": int(item.get("quantity", 1)),
                "product_origin": public_product_origin(item),
            }
            for item in exact_template_items
        ],
        "rejected_exact_template_products": (
            rejected_exact_items
        ),
        "fit_results": fit_results,
        "composition_validation": composition_validation.model_dump(mode="json"),
        "selected_furniture_ids": [
            int(item["id"])
            for item in selected_items
        ],
        "selected_products": [
            {
                "id": int(item["id"]),
                "quantity": int(item["quantity"]),
                "product_origin": public_product_origin(item),
                "selection_role": item.get("selection_role"),
            }
            for item in selected_items
        ],
        "selected_furniture": (
            build_selected_furniture_response(
                selected_items
            )
        ),
        "total_price": total_price,
        "currency": currency,
    }


# =========================================================
# POST /designs/templates/render/{room_request_id}
#
# Render from the immutable template-sourced product inventory.
# Uses:
# - original uploaded room
# - every exact final product reference
# - room constraints and renderer-owned visual design
#
# NO AI Selector call.
# =========================================================

@router.post(
    "/designs/templates/render/{room_request_id}"
)
def render_ready_design_template(
    room_request_id: int,
    request: TemplateRenderRequest,
):
    correlation_id = str(uuid4())
    persisted_selection = load_latest_template_design_selection(
        room_request_id=room_request_id,
        template_id=request.template_id,
    )

    room_request, active_catalog_items = load_room_and_furniture(room_request_id)
    design_request = build_design_request(room_request=room_request, options=None)
    room_image_path = get_room_image_path(room_request["image_filename"])
    room_type = design_request.room_type.value
    style = design_request.style.value if design_request.style else None
    room_analysis, room_analysis_cached = get_or_create_room_analysis(
        room_request_id=room_request_id,
        image_path=room_image_path,
        room_type=room_type,
        room_dimensions=design_request.room_dimensions,
    )
    authoritative_room_dimensions = (
        design_request.room_dimensions
        or room_analysis.provided_dimensions_cm
    )
    current_analysis_signature = build_room_analysis_signature(
        image_path=room_image_path,
        room_type=room_type,
        room_dimensions=authoritative_room_dimensions,
    )
    if current_analysis_signature != persisted_selection.room_analysis_signature:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "The room image, dimensions, analysis model, or prompt contract "
                    "changed after apply. Apply the template again before rendering."
                ),
                "selection_id": persisted_selection.selection_id,
            },
        )
    template = load_design_template(request.template_id)
    template_product_source = persisted_selection.blueprint or {
        "semantics": "product_source_only",
        "template_id": request.template_id,
        "layout_constraints_applied": False,
    }
    template_product_source_signature = persisted_selection.blueprint_signature
    selected_items = persisted_selection.selected_items
    _assert_current_catalog_valid(selected_items, active_catalog_items)
    exact_template_items = [
        item
        for item in selected_items
        if item.get("selection_role") == "template_selected"
    ]
    rejected_exact_items = []
    fit_results = []
    composition_validation = validate_composition_fit(
        selected_items=selected_items,
        room_dimensions=authoritative_room_dimensions,
        obstacles=room_analysis.obstacles,
        circulation_paths=room_analysis.circulation_paths,
        room_type=room_type,
    )
    if composition_validation.status == CompositionFitStatus.INVALID:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "The locked template inventory is broadly physically impossible.",
                "composition_validation": composition_validation.model_dump(mode="json"),
            },
        )
    completion_metadata = {
        "ai_selector_called": False,
        "selection_recomputed": False,
        "completion_scope": "locked_apply_snapshot",
        "template_semantics": "product_source_only",
        "template_layout_constraints_applied": False,
        "catalog_signature": persisted_selection.catalog_signature,
        "visual_design_authority": VISUAL_DESIGN_AUTHORITY,
        "placement_planner_called": False,
        "pre_render_placement_required": False,
    }
    template_design_strategy = None
    architectural_constraints = build_architectural_constraint_map(
        room_analysis=room_analysis,
        room_dimensions=authoritative_room_dimensions,
    )

    furniture_reference_images = (
        prepare_furniture_reference_images(
            recommended_items=selected_items
        )
    )

    # The template has completed its product-selection role. Final rendering
    # receives only the authoritative room plus exact product references.
    render_manifest, _ = _build_renderer_contract(
        persisted_selection=persisted_selection,
        room_image_path=room_image_path,
        room_analysis=room_analysis,
        architectural_constraints=architectural_constraints,
        selected_items=selected_items,
        furniture_reference_images=furniture_reference_images,
        room_type=room_type,
        style=style,
        design_strategy=template_design_strategy,
        template=template,
    )

    total_price, currency = calculate_selected_items_totals(
        selected_items
    )

    with timed_pipeline_stage(correlation_id, "image_generation"):
        generated_result = generate_furnished_room_image(
            room_request_id=room_request_id,
            room_image_path=room_image_path,
            furniture_reference_images=(
                furniture_reference_images
            ),
            recommended_items=selected_items,
            room_type=design_request.room_type.value,
            style=(
                design_request.style.value
                if design_request.style
                else None
            ),
            template_context={
                "id": template.get("id"),
                "name": template.get("name"),
                "semantics": "product_source_only",
            },
            render_manifest=render_manifest,
        )

    generated_filename = generated_result[
        "generated_image_filename"
    ]

    generated_image_url = (
        f"/uploads/generated_designs/"
        f"{generated_filename}"
    )

    with timed_pipeline_stage(correlation_id, "render_compliance"):
        compliance_result = validate_render_compliance(
            generated_image_path=Path(generated_result["generated_image_path"]),
            room_image_path=room_image_path,
            template_image_path=None,
            manifest=render_manifest.model_dump(mode="json"),
        )
    with timed_pipeline_stage(correlation_id, "generated_design_persistence"):
        generated_design_id = save_generated_design(
            room_request_id=room_request_id,
            selection_id=persisted_selection.selection_id,
            generated_image_filename=generated_filename,
            manifest=render_manifest.model_dump(mode="json"),
            compliance=compliance_result.model_dump(mode="json"),
            model_name=generated_result.get("model"),
        )
    _raise_if_render_compliance_failed(
        compliance=compliance_result,
        generated_design_id=generated_design_id,
        generated_result=generated_result,
        generated_image_url=generated_image_url,
    )

    preview_filename = template.get(
        "preview_image_filename"
    )

    return {
        "message": (
            "Ready design rendered; inspect compliance_status before treating it as verified."
        ),
        "correlation_id": correlation_id,
        "design_mode": "template_product_source",
        "template_semantics": "product_source_only",
        "template_layout_constraints_applied": False,
        "selection_id": persisted_selection.selection_id,
        "generated_design_id": generated_design_id,
        "render_prompt_length": generated_result["render_prompt_length"],
        "render_prompt_target_limit": generated_result[
            "render_prompt_target_limit"
        ],
        "render_prompt_hard_limit": generated_result[
            "render_prompt_hard_limit"
        ],
        "render_prompt_compacted": generated_result[
            "render_prompt_compacted"
        ],
        "selection_source": (
            "selected_ready_template_plus_db_completion"
        ),
        "ai_selector_called": completion_metadata[
            "ai_selector_called"
        ],
        "design_director_called": False,
        "visual_design_authority": VISUAL_DESIGN_AUTHORITY,
        "placement_planner_called": False,
        "pre_render_placement_required": False,
        "placement_plan": None,
        "placement_plan_status": PLACEMENT_PLAN_STATUS,
        "placement_plan_cached": False,
        "placement_selection_signature": None,
        "room_request_id": room_request_id,
        "room_type": design_request.room_type.value,
        "style": (
            design_request.style.value
            if design_request.style
            else None
        ),
        "room_analysis_cached": room_analysis_cached,
        "room_analysis": room_analysis.model_dump(),
        "architectural_constraint_map": architectural_constraints.model_dump(mode="json"),
        "selected_template": {
            **template,
            "preview_image_url": (
                f"/uploads/design_templates/{preview_filename}"
                if preview_filename
                else None
            ),
        },
        "template_reference_used": False,
        "template_product_source_signature": template_product_source_signature,
        "template_product_source": template_product_source,
        "template_completion": completion_metadata,
        "exact_template_products_used": [
            {
                "id": int(item["id"]),
                "name": item["name"],
                "category": item["category"],
                "quantity": int(item.get("quantity", 1)),
                "product_origin": public_product_origin(item),
            }
            for item in exact_template_items
        ],
        "rejected_exact_template_products": (
            rejected_exact_items
        ),
        "fit_results": fit_results,
        "composition_validation": composition_validation.model_dump(mode="json"),
        "render_manifest": render_manifest.model_dump(mode="json"),
        "compliance": compliance_result.model_dump(mode="json"),
        "generated_image": {
            "filename": generated_filename,
            "local_path": generated_result[
                "generated_image_path"
            ],
            "image_url": generated_image_url,
            "model": generated_result["model"],
            "input_image_count": generated_result[
                "input_image_count"
            ],
            "output_size": generated_result[
                "output_size"
            ],
        },
        "room_image": {
            "filename": room_request[
                "image_filename"
            ],
            "local_path": str(room_image_path),
            "image_url": (
                f"/uploads/rooms/"
                f"{room_request['image_filename']}"
            ),
        },
        "selected_furniture_ids": [
            int(item["id"])
            for item in selected_items
        ],
        "selected_products": [
            {
                "id": int(item["id"]),
                "quantity": int(item["quantity"]),
                "product_origin": public_product_origin(item),
                "selection_role": item.get("selection_role"),
            }
            for item in selected_items
        ],
        "selected_furniture": (
            build_selected_furniture_response(
                selected_items
            )
        ),
        "total_price": total_price,
        "currency": currency,
    }
