"""Shared catalog gates used by recommendation and placement code."""

from __future__ import annotations


def is_design_eligible(product: dict) -> bool:
    """Return the persisted Shop-vs-design decision with legacy-safe defaults."""

    return (
        bool(product.get("availability", True))
        and str(product.get("catalog_status", "active")) == "active"
        and bool(product.get("design_eligible", True))
    )


def is_ceiling_mounted(product: dict) -> bool:
    return str(product.get("mounting_type") or "").strip().casefold() == "ceiling"
