from fastapi import HTTPException

from app.models import RoomDimensions
from app.services.catalog_compatibility_service import is_ceiling_mounted


NON_FLOOR_FOOTPRINT_CATEGORIES = {
    "wall_art",
    "wall_shelf",
    "mirror",
    "television",
    "medicine_cabinet",
    "wall_cabinet",
    "range_hood"
}


def evaluate_product_fit(
    product: dict,
    room_dimensions: RoomDimensions | None
) -> dict:
    """
    MVP physical-fit check.

    This is intentionally conservative:
    - Reject only when a product clearly cannot fit inside the measured room.
    - Do not reject for style, color, taste, or template mismatch.
    - Overall L x W x H cannot prove detailed wall/door clearance.
    """

    if room_dimensions is None:
        return {
            "status": "unknown",
            "fits": None,
            "reason": "No measured room dimensions were provided."
        }

    product_width = product.get("width_cm")
    product_depth = product.get("depth_cm")
    product_height = product.get("height_cm")

    if (
        room_dimensions.height_cm is not None
        and product_height is not None
        and float(product_height) > room_dimensions.height_cm
    ):
        return {
            "status": "does_not_fit",
            "fits": False,
            "reason": "Product height exceeds the measured room height."
        }

    category = str(product.get("category", ""))

    if is_ceiling_mounted(product):
        return {
            "status": "unknown",
            "fits": None,
            "reason": (
                "Overall room dimensions are insufficient for a strict "
                "ceiling-mounted product fit check; it has no floor footprint."
            ),
        }

    if category in NON_FLOOR_FOOTPRINT_CATEGORIES:
        return {
            "status": "unknown",
            "fits": None,
            "reason": (
                "Overall room dimensions are insufficient for a strict "
                "wall-mounted product fit check."
            )
        }

    if product_width is None or product_depth is None:
        return {
            "status": "unknown",
            "fits": None,
            "reason": "Product footprint dimensions are incomplete."
        }

    product_width = float(product_width)
    product_depth = float(product_depth)
    room_length = float(room_dimensions.length_cm)
    room_width = float(room_dimensions.width_cm)

    fits_normal_orientation = (
        product_width <= room_length
        and product_depth <= room_width
    )

    fits_rotated_orientation = (
        product_width <= room_width
        and product_depth <= room_length
    )

    if not (fits_normal_orientation or fits_rotated_orientation):
        return {
            "status": "does_not_fit",
            "fits": False,
            "reason": (
                "The product footprint exceeds the measured room footprint "
                "in both possible orientations."
            )
        }

    return {
        "status": "fits_overall_room",
        "fits": True,
        "reason": (
            "The product fits inside the measured overall room footprint "
            "in at least one orientation."
        )
    }


def validate_exact_required_products_fit(
    required_products: list,
    candidate_items: list[dict],
    room_dimensions: RoomDimensions | None
) -> None:
    """
    Custom Design rule:
    exact user-selected products are locked unless they clearly do not fit.
    """

    if not required_products or room_dimensions is None:
        return

    candidate_by_id = {
        int(item["id"]): item
        for item in candidate_items
    }

    for requirement in required_products:
        product_id = int(requirement.product_id)
        product = candidate_by_id.get(product_id)

        if product is None:
            continue

        fit_result = evaluate_product_fit(
            product=product,
            room_dimensions=room_dimensions
        )

        if fit_result["fits"] is False:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": (
                        "The exact selected product does not physically "
                        "fit inside the measured room footprint."
                    ),
                    "product_id": product_id,
                    "product_name": product.get("name"),
                    "product_dimensions_cm": {
                        "width_cm": product.get("width_cm"),
                        "depth_cm": product.get("depth_cm"),
                        "height_cm": product.get("height_cm")
                    },
                    "room_dimensions_cm": room_dimensions.model_dump(),
                    "fit_reason": fit_result["reason"]
                }
            )
