from __future__ import annotations

from dataclasses import dataclass

from app.catalog_import.models import NormalizedProduct
from app.enums import FurnitureCategory


CEILING_CATEGORIES = {
    "ceiling_light",
    "chandelier",
    "pendant_light",
    "ceiling_spotlight",
}

SHOP_ONLY_CATEGORIES = {
    "shop_only",
    "refrigerator",
    "washing_machine",
    "dryer",
    "dishwasher",
    "oven",
    "cooktop",
    "freestanding_cooker",
    "range_hood",
    "microwave",
}

WIDTH_DEPTH_HEIGHT_CATEGORIES = {
    "sofa",
    "armchair",
    "coffee_table",
    "side_table",
    "tv_stand",
    "sideboard",
    "floor_lamp",
    "table_lamp",
    "dining_chair",
    "dining_table",
    "office_chair",
    "desk",
    "bookshelf",
    "bed",
    "wardrobe",
    "nightstand",
    "dresser",
    "storage_cabinet",
}

WIDTH_HEIGHT_CATEGORIES = {
    "television",
    "mirror",
    "wall_art",
    "wall_lamp",
    "wall_shelf",
}

WIDTH_DEPTH_CATEGORIES = {"rug"}

ROOMS_BY_CATEGORY = {
    "sofa": ["living_room"],
    "armchair": ["living_room", "bedroom", "office"],
    "coffee_table": ["living_room"],
    "side_table": ["living_room", "bedroom"],
    "tv_stand": ["living_room"],
    "television": ["living_room", "bedroom"],
    "sideboard": ["living_room", "dining_room"],
    "rug": ["living_room", "bedroom", "dining_room", "office"],
    "floor_lamp": ["living_room", "bedroom", "office"],
    "table_lamp": ["living_room", "bedroom", "office"],
    "wall_lamp": ["living_room", "bedroom", "dining_room", "office"],
    "ceiling_light": ["living_room", "bedroom", "dining_room", "office", "kitchen"],
    "chandelier": ["living_room", "bedroom", "dining_room"],
    "pendant_light": ["living_room", "bedroom", "dining_room", "kitchen"],
    "ceiling_spotlight": ["living_room", "bedroom", "dining_room", "office", "kitchen"],
    "dining_chair": ["dining_room"],
    "dining_table": ["dining_room"],
    "office_chair": ["office"],
    "desk": ["office", "bedroom"],
    "bookshelf": ["living_room", "office", "bedroom"],
    "bed": ["bedroom"],
    "wardrobe": ["bedroom"],
    "nightstand": ["bedroom"],
    "dresser": ["bedroom"],
    "mirror": ["living_room", "bedroom", "bathroom"],
    "wall_shelf": ["living_room", "bedroom", "office", "kitchen"],
    "storage_cabinet": ["living_room", "bedroom", "office", "kitchen"],
    "wall_art": ["living_room", "bedroom", "dining_room", "office"],
}


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    reasons: tuple[str, ...]


def default_room_types(category: str) -> list[str]:
    return list(ROOMS_BY_CATEGORY.get(category, []))


def evaluate_design_eligibility(
    product: NormalizedProduct,
    *,
    has_valid_local_image: bool,
) -> EligibilityDecision:
    reasons: list[str] = []
    recognized = {category.value for category in FurnitureCategory}

    if product.category not in recognized:
        reasons.append("unrecognized_category")
    if product.category in SHOP_ONLY_CATEGORIES:
        reasons.append("category_is_shop_only")
    if product.availability is not True:
        reasons.append(
            "availability_unknown" if product.availability is None else "unavailable"
        )
    if not has_valid_local_image:
        reasons.append("missing_valid_local_image")
    if product.is_product_set:
        reasons.append("ambiguous_product_set")
    if not (product.room_types or default_room_types(product.category)):
        reasons.append("unsupported_room_mapping")

    if product.category in CEILING_CATEGORIES:
        if product.mounting_type != "ceiling":
            reasons.append("unsupported_mounting_type")
        if product.diameter_cm is None and (
            product.width_cm is None or product.depth_cm is None
        ):
            reasons.append("missing_dimensions")
    elif product.category in WIDTH_DEPTH_HEIGHT_CATEGORIES:
        if any(
            value is None
            for value in (product.width_cm, product.depth_cm, product.height_cm)
        ):
            reasons.append("missing_dimensions")
    elif product.category in WIDTH_HEIGHT_CATEGORIES:
        if product.width_cm is None or product.height_cm is None:
            reasons.append("missing_dimensions")
    elif product.category in WIDTH_DEPTH_CATEGORIES:
        if product.width_cm is None or product.depth_cm is None:
            reasons.append("missing_dimensions")
    elif product.category not in SHOP_ONLY_CATEGORIES:
        reasons.append("unsupported_placement_semantics")

    return EligibilityDecision(not reasons, tuple(dict.fromkeys(reasons)))


def has_floor_footprint(product: NormalizedProduct) -> bool:
    return product.mounting_type != "ceiling"
