from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator
)

from app.enums import (
    FurnitureCategory,
    FurnitureStyle,
    RequirementMode,
    RoomType
)


# =========================================================
# Furniture
# =========================================================

class FurnitureBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    category: FurnitureCategory
    style: FurnitureStyle
    color: str = Field(..., min_length=2, max_length=50)
    material: Optional[str] = Field(default=None, max_length=100)

    width_cm: float = Field(..., gt=0)
    depth_cm: float = Field(..., gt=0)
    height_cm: float = Field(..., gt=0)

    price: float = Field(..., ge=0)
    product_url: HttpUrl
    store_name: Optional[str] = Field(default=None, max_length=100)
    currency: str = Field(default="JOD", min_length=3, max_length=3)

    room_types: list[RoomType] = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    availability: bool = True


class FurnitureCreate(FurnitureBase):
    pass


class FurnitureUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    category: Optional[FurnitureCategory] = None
    style: Optional[FurnitureStyle] = None
    color: Optional[str] = Field(default=None, min_length=2, max_length=50)
    material: Optional[str] = Field(default=None, max_length=100)

    width_cm: Optional[float] = Field(default=None, gt=0)
    depth_cm: Optional[float] = Field(default=None, gt=0)
    height_cm: Optional[float] = Field(default=None, gt=0)

    price: Optional[float] = Field(default=None, ge=0)
    product_url: Optional[HttpUrl] = None
    store_name: Optional[str] = Field(default=None, max_length=100)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)

    room_types: Optional[list[RoomType]] = Field(default=None, min_length=1)
    tags: Optional[list[str]] = None
    availability: Optional[bool] = None


class FurnitureResponse(FurnitureBase):
    model_config = ConfigDict(from_attributes=True)

    # Catalog imports may legitimately omit renderer-oriented presentation
    # metadata. Create/update requests retain FurnitureBase's strict positive
    # validation, while read responses accurately expose source nulls.
    style: FurnitureStyle | None = None
    color: str | None = None
    width_cm: float | None = Field(default=None, gt=0)
    depth_cm: float | None = Field(default=None, gt=0)
    height_cm: float | None = Field(default=None, gt=0)

    # Imported catalog rows may contain the retailer's full material notes.
    # Keep the 100-character input constraint on create/update, but do not make
    # valid existing rows impossible to return through the catalog API.
    material: Optional[str] = None
    room_types: list[RoomType] = Field(default_factory=list)

    id: int = Field(..., gt=0)
    image_filename: Optional[str] = None
    catalog_status: str = "active"
    quarantine_reason: Optional[str] = None
    source_store_slug: str | None = None
    source_product_id: str | None = None
    canonical_product_url: str | None = None
    name_ar: str | None = None
    name_en: str | None = None
    subcategory: str | None = None
    original_price: float | None = Field(default=None, ge=0)
    source_image_url: str | None = None
    additional_source_image_urls: list[str] = Field(default_factory=list)
    diameter_cm: float | None = Field(default=None, gt=0)
    description: str | None = None
    brand: str | None = None
    model_number: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    imported_at: datetime | None = None
    source_updated_at: datetime | None = None
    design_eligible: bool = True
    mounting_type: str | None = None
    is_product_set: bool = False
    included_pieces: list[str | dict[str, Any]] = Field(default_factory=list)
    normalized_identity: str | None = None
    image_sha256: str | None = None
    last_seen_import_run: str | None = None


# =========================================================
# Room dimensions
# =========================================================

class RoomDimensions(BaseModel):
    length_cm: float = Field(..., gt=0, le=10000)
    width_cm: float = Field(..., gt=0, le=10000)
    height_cm: float | None = Field(default=None, gt=0, le=10000)


# =========================================================
# Guided / Custom design inputs
# =========================================================

class ProductRequirement(BaseModel):
    """
    Category-level requirement.

    Example:
    - required sofa quantity 1 = AI may choose any sofa.
    - excluded armchair = AI may not use armchairs.
    """

    category: FurnitureCategory
    mode: RequirementMode = RequirementMode.AUTO
    quantity: int | None = Field(default=None, ge=1, le=10)

    @model_validator(mode="after")
    def validate_requirement(self):
        if self.mode == RequirementMode.REQUIRED and self.quantity is None:
            raise ValueError("quantity is required when mode is 'required'")

        if (
            self.mode in {RequirementMode.AUTO, RequirementMode.EXCLUDED}
            and self.quantity is not None
        ):
            raise ValueError("quantity must be null unless mode is 'required'")

        return self


class ExactProductRequirement(BaseModel):
    """
    Exact product locked by the user in Custom Design.

    If this product physically fits, the backend must include it exactly.
    It must not be rejected for style, color, price, or design taste.
    """

    product_id: int = Field(..., gt=0)
    quantity: int = Field(default=1, ge=1, le=10)


# =========================================================
# Design request
# =========================================================

class DesignRequest(BaseModel):
    room_type: RoomType

    # Kept for future use.
    # Current recommendation logic ignores total budget.
    budget: float | None = Field(default=None, gt=0)

    style: FurnitureStyle | None = None
    preferred_colors: list[str] = Field(default_factory=list)
    user_prompt: str | None = Field(default=None, max_length=1000)

    # Auto / guided category-level controls.
    product_requirements: list[ProductRequirement] = Field(default_factory=list)

    # Custom Design exact locked products.
    required_products: list[ExactProductRequirement] = Field(default_factory=list)

    # Measured room dimensions from the user.
    room_dimensions: RoomDimensions | None = None

    @model_validator(mode="after")
    def validate_design_request(self):
        categories = [
            requirement.category
            for requirement in self.product_requirements
        ]

        if len(categories) != len(set(categories)):
            raise ValueError(
                "Each product category may appear only once "
                "in product_requirements"
            )

        product_ids = [
            requirement.product_id
            for requirement in self.required_products
        ]

        if len(product_ids) != len(set(product_ids)):
            raise ValueError(
                "Each exact product ID may appear only once. "
                "Use quantity for multiple units of the same product."
            )

        return self


# =========================================================
# Design template MVP models
# =========================================================

class DesignTemplateCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    room_type: RoomType
    style: FurnitureStyle | None = None

    size_category: str | None = Field(default=None, max_length=30)
    layout_type: str | None = Field(default=None, max_length=80)
    wall_color_family: str | None = Field(default=None, max_length=50)
    floor_color_family: str | None = Field(default=None, max_length=50)
    floor_material: str | None = Field(default=None, max_length=50)
    natural_light_level: str | None = Field(default=None, max_length=30)
    room_openness: str | None = Field(default=None, max_length=30)
    density_target: str | None = Field(default=None, max_length=30)
    focal_point: str | None = Field(default=None, max_length=60)

    description: str | None = Field(default=None, max_length=2000)
    preview_image_filename: str | None = Field(default=None, max_length=255)

    # Source metadata. Used by the bulk IKEA importer and frontend attribution.
    source_name: str | None = Field(default=None, max_length=100)
    source_url: HttpUrl | None = None
    source_image_url: HttpUrl | None = None

    # "exact_products" when the source page lists products used in the room,
    # otherwise "inspiration".
    template_type: str = Field(default="inspiration", max_length=40)

    tags: list[str] = Field(default_factory=list)
    is_active: bool = True


class DesignTemplateResponse(DesignTemplateCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., gt=0)

    # Derived counts returned by the matching service when available.
    source_product_count: int = Field(default=0, ge=0)
    matched_product_count: int = Field(default=0, ge=0)
    missing_product_count: int = Field(default=0, ge=0)


class MatchedDesignTemplate(BaseModel):
    template: DesignTemplateResponse
    score: int = Field(..., ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)


# =========================================================
# Selected furniture for frontend
# =========================================================

class SelectedFurniture(BaseModel):
    id: int = Field(..., gt=0)
    name: str
    category: FurnitureCategory
    style: FurnitureStyle | None = None
    color: str | None = None
    material: str | None = None
    width_cm: float | None = Field(default=None, gt=0)
    depth_cm: float | None = Field(default=None, gt=0)
    height_cm: float | None = Field(default=None, gt=0)
    diameter_cm: float | None = Field(default=None, gt=0)
    price: float = Field(..., ge=0)
    currency: str = "JOD"
    image_filename: Optional[str] = None
    image_url: Optional[str] = None
    product_url: HttpUrl
    store_name: Optional[str] = None
    source_store_slug: str | None = None
    source_product_id: str | None = None
    canonical_product_url: str | None = None
    design_eligible: bool = True
    mounting_type: str | None = None
    is_product_set: bool = False
    selection_role: str
    selection_origin: str | None = None
    product_origin: Literal[
        "from_template",
        "added_to_complete_room",
        "user_selected",
        "unknown",
    ] = "unknown"
    slot_keys: list[str] = Field(default_factory=list)
    repeat_group_id: str | None = None
    inclusion_reason: str | None = None
    was_included: bool = False
    added_for_completion: bool = False
    included_quantity: int = Field(default=0, ge=0)
    completion_quantity: int = Field(default=0, ge=0)
    match_quality: str | None = None
    functional_zone: str | None = None
    functional_zones: list[str] = Field(default_factory=list)
    functional_group_ids: list[str] = Field(default_factory=list)
    requirement_importance: str | None = None
    completeness_level: str | None = None
    quantity: int = Field(default=1, ge=1)
    line_total: float = Field(..., ge=0)
    score: int = 0


# =========================================================
# Final generated design response
# =========================================================

class GeneratedDesignResponse(BaseModel):
    generated_image_url: str
    room_type: RoomType
    style: Optional[FurnitureStyle] = None
    selected_furniture_ids: list[int] = Field(default_factory=list)
    selected_furniture: list[SelectedFurniture] = Field(default_factory=list)
    total_price: float = Field(default=0, ge=0)
    currency: Optional[str] = "JOD"


# =========================================================
# Immutable ready-template render contract
# =========================================================

class TemplateRenderRequest(BaseModel):
    """Render the latest locked selection for a ready-design template."""

    model_config = ConfigDict(extra="forbid")

    template_id: int = Field(
        ...,
        gt=0,
        description="Numeric ID of the ready-design template to render.",
    )
