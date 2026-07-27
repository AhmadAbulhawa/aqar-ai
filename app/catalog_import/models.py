from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal


@dataclass(frozen=True)
class DiscoveredProduct:
    """A lightweight, stable reference returned by retailer discovery."""

    url: str
    source_product_id: str | None = None
    source_category: str | None = None
    normalized_category: str | None = None
    name_hint: str | None = None
    availability_hint: bool | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedProduct:
    """Retailer-neutral source facts. Missing facts remain ``None``."""

    adapter_id: str
    source_store: str
    source_store_slug: str
    source_product_id: str | None
    canonical_url: str
    name: str
    category: str
    current_price: Decimal
    currency: str = "JOD"
    name_ar: str | None = None
    name_en: str | None = None
    subcategory: str | None = None
    original_price: Decimal | None = None
    availability: bool | None = None
    image_urls: list[str] = field(default_factory=list)
    width_cm: Decimal | None = None
    depth_cm: Decimal | None = None
    height_cm: Decimal | None = None
    diameter_cm: Decimal | None = None
    color: str | None = None
    material: str | None = None
    style: str | None = None
    room_types: list[str] = field(default_factory=list)
    description: str | None = None
    brand: str | None = None
    model_number: str | None = None
    source_updated_at: datetime | None = None
    mounting_type: str | None = None
    is_product_set: bool = False
    included_pieces: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def source_image_url(self) -> str | None:
        return self.image_urls[0] if self.image_urls else None

    def to_report_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "current_price",
            "original_price",
            "width_cm",
            "depth_cm",
            "height_cm",
            "diameter_cm",
        ):
            value = payload.get(key)
            payload[key] = str(value) if value is not None else None
        if self.source_updated_at is not None:
            payload["source_updated_at"] = self.source_updated_at.isoformat()
        return payload


ImportAction = Literal[
    "inserted",
    "updated",
    "unchanged",
    "skipped",
    "failed",
]


@dataclass
class ProductImportOutcome:
    action: ImportAction
    canonical_url: str
    source_product_id: str | None = None
    furniture_id: int | None = None
    reason: str | None = None
    design_eligible: bool = False
    image_downloaded: bool = False
    image_reused: bool = False
    image_failed: bool = False
    category: str | None = None
    availability: bool | None = None


@dataclass
class ImportReport:
    adapter_id: str
    source_store: str
    source_store_slug: str
    started_at: str
    command: str
    dry_run: bool
    full_sync: bool
    category_filter: list[str] = field(default_factory=list)
    official_category_pages: list[str] = field(default_factory=list)
    discovered_products: int = 0
    pages_scanned: int = 0
    products_scanned: int = 0
    inserted_products: int = 0
    updated_products: int = 0
    unchanged_products: int = 0
    unavailable_products: int = 0
    deactivated_products: int = 0
    skipped_products: int = 0
    parsing_failures: int = 0
    missing_dimension_skips: int = 0
    design_eligible_count: int = 0
    shop_only_count: int = 0
    downloaded_image_count: int = 0
    reused_image_count: int = 0
    failed_image_count: int = 0
    duplicate_count: int = 0
    categories_imported: dict[str, int] = field(default_factory=dict)
    category_mapping: dict[str, str] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    finished_at: str | None = None

    def record(self, outcome: ProductImportOutcome) -> None:
        self.products_scanned += 1
        self.outcomes.append(asdict(outcome))
        if outcome.action == "inserted":
            self.inserted_products += 1
        elif outcome.action == "updated":
            self.updated_products += 1
        elif outcome.action == "unchanged":
            self.unchanged_products += 1
        elif outcome.action == "skipped":
            self.skipped_products += 1
        elif outcome.action == "failed":
            self.parsing_failures += 1
        if outcome.reason and "missing_dimension" in outcome.reason:
            self.missing_dimension_skips += 1
        if outcome.availability is False:
            self.unavailable_products += 1
        if outcome.design_eligible:
            self.design_eligible_count += 1
        else:
            self.shop_only_count += 1
        if outcome.image_downloaded:
            self.downloaded_image_count += 1
        if outcome.image_reused:
            self.reused_image_count += 1
        if outcome.image_failed:
            self.failed_image_count += 1
        if outcome.category:
            self.categories_imported[outcome.category] = (
                self.categories_imported.get(outcome.category, 0) + 1
            )
