from __future__ import annotations

from fastapi import HTTPException

from app.models import RoomDimensions
from app.services.catalog_compatibility_service import is_design_eligible
from app.services.composition_fit_service import (
    CompositionFitResult,
    CompositionFitStatus,
    validate_composition_fit,
)
from app.services.product_origin_service import public_product_origin
from app.services.room_fit_service import evaluate_product_fit


MAX_INVENTORY_PRODUCTS = 15
MAX_PRODUCT_QUANTITY = 10


def _assert_catalog_product_is_eligible(
    product: dict,
    *,
    room_type: str,
    room_dimensions: RoomDimensions | None,
) -> None:
    if not is_design_eligible(product):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "A requested product is no longer available or design-eligible.",
                "product_id": int(product["id"]),
            },
        )

    if room_type not in list(product.get("room_types") or []):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "A requested product is not cataloged for this room type.",
                "product_id": int(product["id"]),
                "room_type": room_type,
            },
        )

    fit = evaluate_product_fit(product, room_dimensions)
    if fit.get("fits") is False:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "A requested product does not fit the measured room.",
                "product_id": int(product["id"]),
                "fit": fit,
            },
        )


def _user_added_product(product: dict) -> dict:
    return {
        **product,
        "selection_role": "user_selected",
        "selection_origin": "user",
        "product_origin": "user_selected",
        "was_included": True,
        "added_for_completion": False,
        "template_id": None,
        "included_quantity": 1,
        "completion_quantity": 0,
        "inclusion_reason": (
            "Added by the user during Studio inventory review."
        ),
        "match_quality": "included",
        "slot_keys": [],
        "slot_assignments": [],
        "repeat_group_id": None,
        "functional_zone": None,
        "functional_zones": [],
        "functional_group_ids": [],
        "requirement_importance": None,
        "requirement_importances": [],
        "completeness_level": None,
    }


def _preserve_product_source(
    product: dict,
    previous: dict,
) -> dict:
    """Refresh catalog data while preserving persisted source provenance."""

    source_fields = (
        "selection_role",
        "selection_origin",
        "product_origin",
        "was_included",
        "added_for_completion",
        "template_id",
        "inclusion_reason",
        "match_quality",
        "slot_keys",
        "repeat_group_id",
        "functional_zone",
        "functional_zones",
        "functional_group_ids",
        "requirement_importance",
        "requirement_importances",
        "completeness_level",
    )
    revised = {**product}
    revised.update(
        {
            field: previous.get(field)
            for field in source_fields
            if field in previous
        }
    )
    # Quantity changes make old per-unit slot assignments misleading. Product
    # source remains persisted, but the renderer receives only the exact total.
    revised["slot_assignments"] = []
    revised["product_origin"] = public_product_origin(previous)
    return revised


def _apply_origin_quantities(item: dict, quantity: int) -> None:
    origin = public_product_origin(item)
    item["included_quantity"] = (
        quantity if origin in {"from_template", "user_selected"} else 0
    )
    item["completion_quantity"] = (
        quantity if origin == "added_to_complete_room" else 0
    )


def build_inventory_revision(
    *,
    base_items: list[dict],
    catalog_items: list[dict],
    requested_lines: list[dict],
    room_type: str,
    room_dimensions: RoomDimensions | None,
) -> tuple[list[dict], CompositionFitResult]:
    """Build a new immutable selection snapshot without mutating its source."""

    if not requested_lines:
        raise HTTPException(
            status_code=422,
            detail="Keep at least one product in the inventory.",
        )
    if len(requested_lines) > MAX_INVENTORY_PRODUCTS:
        raise HTTPException(
            status_code=422,
            detail=f"Inventory supports at most {MAX_INVENTORY_PRODUCTS} products.",
        )

    requested_ids = [int(line["product_id"]) for line in requested_lines]
    if len(requested_ids) != len(set(requested_ids)):
        raise HTTPException(
            status_code=422,
            detail="Each product may appear once; use quantity for multiple units.",
        )

    catalog_by_id = {int(item["id"]): item for item in catalog_items}
    base_by_id = {int(item["id"]): item for item in base_items}
    revised_items: list[dict] = []

    for line in requested_lines:
        product_id = int(line["product_id"])
        quantity = int(line["quantity"])
        if quantity < 1 or quantity > MAX_PRODUCT_QUANTITY:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": (
                        f"Product quantity must be between 1 and "
                        f"{MAX_PRODUCT_QUANTITY}."
                    ),
                    "product_id": product_id,
                },
            )
        product = catalog_by_id.get(product_id)
        if product is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "A requested product is not in the active catalog.",
                    "product_id": product_id,
                },
            )
        _assert_catalog_product_is_eligible(
            product,
            room_type=room_type,
            room_dimensions=room_dimensions,
        )

        previous = base_by_id.get(product_id)
        item = (
            _preserve_product_source(product, previous)
            if previous is not None
            else _user_added_product(product)
        )
        item["quantity"] = quantity
        item["line_total"] = round(float(item.get("price") or 0) * quantity, 2)
        item["quantity_edited_by_user"] = (
            previous is None
            or int(previous.get("quantity", 1)) != quantity
        )
        _apply_origin_quantities(item, quantity)
        revised_items.append(item)

    composition = validate_composition_fit(
        selected_items=revised_items,
        room_dimensions=room_dimensions,
        room_type=room_type,
    )
    if composition.status == CompositionFitStatus.INVALID:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Blocking error: the edited inventory is physically "
                    "impossible for the measured room."
                ),
                "composition_validation": composition.model_dump(mode="json"),
            },
        )
    return revised_items, composition
