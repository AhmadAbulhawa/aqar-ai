from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence

from app.catalog_import.http import CachedHttpClient
from app.catalog_import.models import DiscoveredProduct, NormalizedProduct


class SkipProduct(ValueError):
    """A real source product that is intentionally outside normalized scope."""


class RetailerAdapter(ABC):
    """Shared contract with intentionally site-specific implementations."""

    adapter_id: str
    source_store: str
    source_store_slug: str
    base_url: str
    category_pages: dict[str, str]
    category_mapping: dict[str, str]
    last_discovery_pages_scanned: int = 0

    def selected_category_pages(
        self,
        category_filter: Sequence[str] | None,
    ) -> dict[str, str]:
        if not category_filter:
            return dict(self.category_pages)
        requested = {value.strip().lower() for value in category_filter if value.strip()}
        selected = {
            key: url
            for key, url in self.category_pages.items()
            if key.lower() in requested
            or self.category_mapping.get(key, "").lower() in requested
        }
        unknown = requested - {
            value.lower()
            for key in selected
            for value in (key, self.category_mapping.get(key, ""))
        }
        if unknown:
            raise ValueError(
                f"Unknown {self.adapter_id} category filter(s): "
                + ", ".join(sorted(unknown))
            )
        return selected

    @abstractmethod
    def discover(
        self,
        client: CachedHttpClient,
        category_filter: Sequence[str] | None = None,
    ) -> Iterable[DiscoveredProduct]:
        """Discover all in-scope product references without database writes."""

    @abstractmethod
    def parse_product(
        self,
        reference: DiscoveredProduct,
        client: CachedHttpClient,
    ) -> NormalizedProduct:
        """Parse one public product detail into source facts."""
