"""Versioned, placement-independent contract for GPT Image rendering."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.services.product_origin_service import public_product_origin


RENDER_MANIFEST_VERSION = "2026-07-18.2-template-product-source"
NORMAL_MAX_PRODUCT_REFERENCES = 15
MAX_TOTAL_INPUT_IMAGES = 16
VISUAL_DESIGN_AUTHORITY = "gpt-image-2"
PLACEMENT_PLAN_STATUS = "not_required_gpt_image_2_is_visual_designer"


class RenderReference(BaseModel):
    index: int = Field(ge=1)
    kind: Literal["room", "template", "product"]
    local_path: str
    content_signature: str
    product_id: int | None = None
    quantity: int | None = None
    slot_keys: list[str] = Field(default_factory=list)


class RenderManifest(BaseModel):
    contract_version: str = RENDER_MANIFEST_VERSION
    selection_id: str
    room_request_id: int
    template_id: int | None = None
    design_mode: str
    selection_signature: str
    room_analysis_signature: str
    architectural_constraint_signature: str
    template_blueprint_signature: str | None = None
    template_product_source_signature: str | None = None
    render_signature: str
    render_prompt_version: str
    renderer_model: str
    visual_design_authority: Literal["gpt-image-2"] = VISUAL_DESIGN_AUTHORITY
    visual_freedom: bool = True
    placement_planner_called: bool = False
    pre_render_placement_required: bool = False
    placement_plan: None = None
    placement_plan_status: str = PLACEMENT_PLAN_STATUS
    room_analysis: dict
    architectural_constraints: dict
    selected_products: list[dict]
    template: dict | None = None
    hard_constraints: list[str] = Field(default_factory=list)
    explicit_user_requirements: list[str] = Field(default_factory=list)
    design_intent: dict = Field(default_factory=dict)
    references: list[RenderReference]
    render_reference_budget: dict
    product_reference_count: int
    total_input_image_count: int
    # Read-compatible only. New manifests never depend on this value.
    placement_signature: str | None = None


def _as_dict(value: object | None) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError("Expected a dict, Pydantic model, or None.")


def _file_signature(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_architectural_constraint_signature(
    architectural_constraints: dict | object,
) -> str:
    payload = _as_dict(architectural_constraints)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_hard_constraints(architectural_constraints: dict | object) -> list[str]:
    """Translate structured/qualitative room facts into renderer instructions."""

    constraints = _as_dict(architectural_constraints)
    instructions = [
        (
            "The original room image is authoritative for walls, doors, windows, "
            "ceiling, floor, perspective, camera position, and the room envelope; "
            "do not move, remove, or invent architectural elements."
        ),
        "Use only the exact selected products and exact physical quantities.",
        "Do not invent, substitute, or omit major furniture products.",
        "Preserve realistic product scale and prevent visible major-furniture overlap.",
    ]

    def evidence(zone: dict) -> str:
        explicit = str(zone.get("evidence") or "").strip().upper()
        if explicit:
            return explicit
        description = str(zone.get("description") or "").casefold()
        if any(value in description for value in ("not visible", "unseen", "outside camera")):
            return "NOT_VISIBLE"
        return "OBSERVED_UNCERTAIN"

    visible_door_zones = [
        zone
        for zone in constraints.get("door_zones", [])
        if evidence(zone) in {"OBSERVED_CONFIRMED", "OBSERVED_UNCERTAIN"}
    ]
    for zone in visible_door_zones:
        description = str(zone.get("description") or "visible door/entry").rstrip(" .")
        instructions.append(
            f"Keep the door/entry fully visible and usable: {description}."
        )
    if visible_door_zones:
        instructions.append(
            "Keep the entire visible door opening, door swing area, and immediate "
            "entry path completely free of sofas, chairs, tables, cabinets, or "
            "any other furniture. No furniture may overlap or sit directly in "
            "front of the door plane."
        )
    for zone in constraints.get("door_swing_zones", []):
        if evidence(zone) not in {"OBSERVED_CONFIRMED", "OBSERVED_UNCERTAIN"}:
            continue
        description = str(
            zone.get("description") or "likely door operating area"
        ).rstrip(" .")
        instructions.append(
            "Do not place furniture in the likely door-operating or swing area: "
            f"{description}. Exact geometry is uncertain, so preserve conservative clearance."
        )
    for zone in constraints.get("window_zones", []):
        if evidence(zone) == "NOT_VISIBLE":
            continue
        description = str(zone.get("description") or "visible window").rstrip(" .")
        instructions.append(
            f"Preserve access, light, and visibility at the window: {description}; "
            "do not place tall furniture in front of it."
        )
    for zone in constraints.get("circulation_corridors", []):
        description = str(
            zone.get("description") or "observed circulation route"
        ).rstrip(" .")
        zone_evidence = evidence(zone)
        if zone_evidence in {"INFERRED", "NOT_VISIBLE"}:
            instructions.append(
                "Unverified circulation context only; do not invent enforceable "
                f"route geometry from it: {description}."
            )
        else:
            priority = "primary " if zone.get("is_primary") else ""
            instructions.append(
                f"Keep the {priority}circulation route clear and usable: {description}."
            )

    if not visible_door_zones:
        instructions.append(
            "No door or entrance is authoritatively visible in the supplied view; "
            "do not invent its location, swing, or approach route. Keep any actually "
            "visible opening usable."
        )
    if not constraints.get("window_zones"):
        instructions.append(
            "Preserve every visible window and do not cover it with tall furniture."
        )
    if not any(
        evidence(zone) in {"OBSERVED_CONFIRMED", "OBSERVED_UNCERTAIN"}
        for zone in constraints.get("circulation_corridors", [])
    ):
        instructions.append(
            "No primary circulation route is directly confirmed in the image; "
            "preserve reasonable general movement without inventing an unseen entrance path."
        )

    return instructions


def _selected_product_payload(item: dict) -> dict:
    quantity = int(item.get("quantity", 1))
    if quantity < 1:
        raise HTTPException(
            status_code=422,
            detail={"message": "Selected product quantity must be positive.", "product_id": item.get("id")},
        )
    return {
        "id": int(item["id"]),
        "name": item.get("name"),
        "category": item.get("category"),
        "quantity": quantity,
        "dimensions_cm": {
            "width_cm": item.get("width_cm"),
            "depth_cm": item.get("depth_cm"),
            "height_cm": item.get("height_cm"),
        },
        "selection_origin": item.get("selection_origin"),
        "selection_role": item.get("selection_role"),
        "product_origin": public_product_origin(item),
        "functional_zone": item.get("functional_zone"),
        "functional_zones": list(item.get("functional_zones") or []),
        "slot_keys": list(item.get("slot_keys") or []),
        "repeat_group_id": item.get("repeat_group_id"),
    }


def build_render_manifest(
    *,
    selection_id: str,
    selection_signature: str,
    room_request_id: int,
    room_image_path: Path,
    selected_items: list[dict],
    furniture_reference_images: list[dict],
    design_mode: str = "auto",
    room_analysis: dict | object | None = None,
    room_analysis_signature: str = "legacy_unknown",
    architectural_constraints: dict | object | None = None,
    template_id: int | None = None,
    template_image_path: Path | None = None,
    template: dict | None = None,
    template_blueprint_signature: str | None = None,
    template_product_source_signature: str | None = None,
    hard_constraints: list[str] | None = None,
    explicit_user_requirements: list[str] | None = None,
    design_intent: dict | None = None,
    render_prompt_version: str = "legacy_unknown",
    renderer_model: str = VISUAL_DESIGN_AUTHORITY,
    placement_signature: str | None = None,
) -> RenderManifest:
    """Build and validate the complete non-billable render contract.

    ``placement_signature`` is accepted solely so older internal callers do not
    crash during rolling upgrades. It is deliberately excluded from the render
    signature and is always returned as ``None`` for new manifests.
    """

    del placement_signature, template_image_path
    maximum_product_references = NORMAL_MAX_PRODUCT_REFERENCES
    if len(furniture_reference_images) > maximum_product_references:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Render product-reference limit exceeded.",
                "maximum_product_references": maximum_product_references,
                "actual_product_references": len(furniture_reference_images),
                "total_input_image_limit": MAX_TOTAL_INPUT_IMAGES,
            },
        )

    selected_products = [_selected_product_payload(item) for item in selected_items]
    selected_by_id = {int(item["id"]): item for item in selected_items}
    if len(selected_by_id) != len(selected_items):
        raise HTTPException(status_code=422, detail="Duplicate products in locked selection.")

    reference_ids = [int(reference["id"]) for reference in furniture_reference_images]
    if len(reference_ids) != len(set(reference_ids)):
        raise HTTPException(status_code=422, detail="Duplicate product reference image.")
    if set(reference_ids) != set(selected_by_id):
        raise HTTPException(
            status_code=422,
            detail="Render references do not exactly match the locked selection.",
        )

    raw_references: list[tuple[str, Path, int | None]] = [
        ("room", Path(room_image_path), None)
    ]
    reference_by_id = {
        int(reference["id"]): reference
        for reference in furniture_reference_images
    }
    raw_references.extend(
        (
            "product",
            Path(reference_by_id[int(item["id"])]["local_image_path"]),
            int(item["id"]),
        )
        for item in selected_items
    )

    if len(raw_references) > MAX_TOTAL_INPUT_IMAGES:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Render input image limit exceeded.",
                "maximum_total_input_images": MAX_TOTAL_INPUT_IMAGES,
                "actual_total_input_images": len(raw_references),
            },
        )
    missing_paths = [str(path) for _, path, _ in raw_references if not path.exists()]
    if missing_paths:
        raise HTTPException(
            status_code=404,
            detail={"message": "Render reference file missing.", "paths": missing_paths},
        )

    references: list[RenderReference] = []
    for index, (kind, path, product_id) in enumerate(raw_references, start=1):
        item = selected_by_id.get(product_id) if product_id is not None else None
        references.append(
            RenderReference(
                index=index,
                kind=kind,
                local_path=str(path),
                content_signature=_file_signature(path),
                product_id=product_id,
                quantity=int(item.get("quantity", 1)) if item else None,
                slot_keys=list(item.get("slot_keys") or []) if item else [],
            )
        )

    room_analysis_payload = _as_dict(room_analysis)
    architectural_payload = _as_dict(architectural_constraints)
    architectural_signature = build_architectural_constraint_signature(
        architectural_payload
    )
    effective_hard_constraints = hard_constraints or build_hard_constraints(
        architectural_payload
    )
    effective_template = (
        dict(template or {})
        if template_id is not None or template is not None
        else None
    )
    if effective_template is not None:
        effective_template.setdefault("id", template_id)
        effective_template["blueprint_signature"] = template_blueprint_signature
        effective_template["product_source_signature"] = (
            template_product_source_signature
        )

    signature_payload = {
        "contract_version": RENDER_MANIFEST_VERSION,
        "selection_signature": selection_signature,
        "room_analysis_signature": room_analysis_signature,
        "architectural_constraint_signature": architectural_signature,
        "template_blueprint_signature": template_blueprint_signature,
        "template_product_source_signature": template_product_source_signature,
        "render_prompt_version": render_prompt_version,
        "renderer_model": renderer_model,
        "explicit_user_requirements": list(explicit_user_requirements or []),
        "reference_signatures": [reference.content_signature for reference in references],
    }
    render_signature = hashlib.sha256(
        json.dumps(signature_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    return RenderManifest(
        selection_id=selection_id,
        room_request_id=room_request_id,
        template_id=template_id,
        design_mode=design_mode,
        selection_signature=selection_signature,
        room_analysis_signature=room_analysis_signature,
        architectural_constraint_signature=architectural_signature,
        template_blueprint_signature=template_blueprint_signature,
        template_product_source_signature=template_product_source_signature,
        render_signature=render_signature,
        render_prompt_version=render_prompt_version,
        renderer_model=renderer_model,
        room_analysis=room_analysis_payload,
        architectural_constraints=architectural_payload,
        selected_products=selected_products,
        template=effective_template,
        hard_constraints=effective_hard_constraints,
        explicit_user_requirements=list(explicit_user_requirements or []),
        design_intent=dict(design_intent or {}),
        references=references,
        render_reference_budget={
            "room_images": 1,
            "template_images": 0,
            "maximum_unique_product_images": maximum_product_references,
            "selected_unique_product_images": len(furniture_reference_images),
            "total_input_image_limit": MAX_TOTAL_INPUT_IMAGES,
        },
        product_reference_count=len(furniture_reference_images),
        total_input_image_count=len(references),
        placement_signature=None,
    )
