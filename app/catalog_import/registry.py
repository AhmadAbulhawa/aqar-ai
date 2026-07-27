from __future__ import annotations

from app.catalog_import.adapters import (
    IkeaJordanCeilingLightsAdapter,
    LeadersJordanAdapter,
    MidasJordanAdapter,
    ShahwanJordanAdapter,
)
from app.catalog_import.base import RetailerAdapter


def adapter_registry() -> dict[str, RetailerAdapter]:
    adapters: list[RetailerAdapter] = [
        LeadersJordanAdapter(),
        ShahwanJordanAdapter(),
        IkeaJordanCeilingLightsAdapter(),
        MidasJordanAdapter(),
    ]
    return {adapter.adapter_id: adapter for adapter in adapters}
