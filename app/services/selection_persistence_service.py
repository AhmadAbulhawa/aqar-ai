from __future__ import annotations

import json
import hashlib
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.database import (
    DESIGN_SELECTION_ITEMS_TABLE_SQL,
    DESIGN_SELECTIONS_TABLE_SQL,
    ensure_immutable_selection_triggers,
    get_connection,
)
from app.services.product_origin_service import public_product_origin


class PersistedDesignSelection(BaseModel):
    selection_id: str
    room_request_id: int
    template_id: int | None = None
    mode: str
    status: str
    selection_signature: str
    blueprint_signature: str | None = None
    catalog_signature: str
    room_analysis_signature: str
    blueprint: dict | None = None
    composition_validation: dict
    selected_items: list[dict] = Field(default_factory=list)


def build_design_selection_signature(
    *,
    room_request_id: int,
    template_id: int | None,
    blueprint_signature: str | None,
    catalog_signature: str,
    room_analysis_signature: str,
    selected_items: list[dict],
) -> str:
    payload = {
        "contract_version": "2026-07-18.1-product-origin",
        "room_request_id": room_request_id,
        "template_id": template_id,
        "blueprint_signature": blueprint_signature,
        "catalog_signature": catalog_signature,
        "room_analysis_signature": room_analysis_signature,
        "items": sorted(
            [
                {
                    "id": int(item["id"]),
                    "quantity": int(item.get("quantity", 1)),
                    "slot_keys": list(item.get("slot_keys") or []),
                    "repeat_group_id": item.get("repeat_group_id"),
                    "selection_role": item.get("selection_role"),
                    "selection_origin": item.get("selection_origin"),
                    "product_origin": public_product_origin(item),
                    "source_template_id": item.get("template_id"),
                    "included_quantity": item.get("included_quantity"),
                    "completion_quantity": item.get("completion_quantity"),
                    "functional_zone": item.get("functional_zone"),
                    "functional_zones": list(item.get("functional_zones") or []),
                    "functional_group_ids": list(
                        item.get("functional_group_ids") or []
                    ),
                    "requirement_importance": item.get(
                        "requirement_importance"
                    ),
                    "completeness_level": item.get("completeness_level"),
                    "width_cm": item.get("width_cm"),
                    "depth_cm": item.get("depth_cm"),
                    "height_cm": item.get("height_cm"),
                    "image_filename": item.get("image_filename"),
                }
                for item in selected_items
            ],
            key=lambda item: item["id"],
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def ensure_design_selection_tables() -> None:
    connection = get_connection()
    try:
        connection.execute(DESIGN_SELECTIONS_TABLE_SQL)
        connection.execute(DESIGN_SELECTION_ITEMS_TABLE_SQL)
        ensure_immutable_selection_triggers(connection.cursor())
        connection.commit()
    finally:
        connection.close()


def _snapshot_product(item: dict) -> dict:
    snapshot_fields = (
        "id", "name", "category", "style", "color", "material",
        "width_cm", "depth_cm", "height_cm", "price", "currency",
        "image_filename", "product_url", "store_name", "room_types",
        "tags", "availability", "catalog_status", "selection_role",
        "selection_origin", "was_included", "added_for_completion",
        "product_origin", "template_id",
        "included_quantity", "completion_quantity", "inclusion_reason",
        "match_quality", "line_total", "score", "functional_zone",
        "functional_zones", "functional_group_ids",
        "requirement_importance", "requirement_importances",
        "completeness_level", "quantity_edited_by_user",
    )
    snapshot = {
        field: item.get(field)
        for field in snapshot_fields
        if field in item
    }
    snapshot["id"] = int(item["id"])
    snapshot["quantity"] = int(item.get("quantity", 1))
    snapshot["selection_role"] = str(
        item.get("selection_role") or "template_slot"
    )
    snapshot["product_origin"] = public_product_origin(item)
    snapshot["slot_keys"] = list(item.get("slot_keys") or [])
    snapshot["slot_assignments"] = list(
        item.get("slot_assignments") or []
    )
    snapshot["repeat_group_id"] = item.get("repeat_group_id")
    snapshot["line_total"] = round(
        float(snapshot.get("price") or 0) * snapshot["quantity"], 2
    )
    return snapshot


def _row_to_selection(connection, row) -> PersistedDesignSelection:
    item_rows = connection.execute(
        """
        SELECT product_snapshot_json
        FROM design_selection_items
        WHERE selection_id = ?
        ORDER BY id ASC
        """,
        (row["id"],),
    ).fetchall()
    return PersistedDesignSelection(
        selection_id=row["id"],
        room_request_id=int(row["room_request_id"]),
        template_id=(
            int(row["template_id"])
            if row["template_id"] is not None
            else None
        ),
        mode=row["mode"],
        status=row["status"],
        selection_signature=row["selection_signature"],
        blueprint_signature=row["blueprint_signature"],
        catalog_signature=row["catalog_signature"],
        room_analysis_signature=row["room_analysis_signature"],
        blueprint=(
            json.loads(row["blueprint_json"])
            if row["blueprint_json"]
            else None
        ),
        composition_validation=json.loads(
            row["composition_validation_json"]
        ),
        selected_items=[
            json.loads(item_row["product_snapshot_json"])
            for item_row in item_rows
        ],
    )


def create_or_reuse_design_selection(
    *,
    room_request_id: int,
    template_id: int | None,
    mode: str,
    selection_signature: str,
    blueprint_signature: str | None,
    catalog_signature: str,
    room_analysis_signature: str,
    selected_items: list[dict],
    blueprint: dict | None,
    composition_validation: dict,
) -> tuple[PersistedDesignSelection, bool]:
    """Atomically lock a snapshot; an existing signature is never mutated."""

    if str(composition_validation.get("status")) == "invalid":
        raise HTTPException(
            status_code=422,
            detail="Invalid whole-composition drafts cannot be locked.",
        )
    # Legacy selections may still carry coordinate-derived layout_quality.
    # It is intentionally ignored for new immutable locks: synthetic visual
    # alignment is not knowable before GPT Image creates the final arrangement.
    # Broad composition validity above remains an authoritative pre-render gate.

    ensure_design_selection_tables()
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT * FROM design_selections
            WHERE room_request_id = ? AND selection_signature = ?
            """,
            (room_request_id, selection_signature),
        ).fetchone()
        if existing is not None:
            selection = _row_to_selection(connection, existing)
            connection.commit()
            return selection, True

        selection_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO design_selections (
                id, room_request_id, template_id, mode, status,
                selection_signature, blueprint_signature, catalog_signature,
                room_analysis_signature, blueprint_json,
                composition_validation_json
            ) VALUES (?, ?, ?, ?, 'locked', ?, ?, ?, ?, ?, ?)
            """,
            (
                selection_id,
                room_request_id,
                template_id,
                mode,
                selection_signature,
                blueprint_signature,
                catalog_signature,
                room_analysis_signature,
                json.dumps(blueprint, ensure_ascii=False, sort_keys=True),
                json.dumps(
                    composition_validation,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        )

        for item in selected_items:
            snapshot = _snapshot_product(item)
            slot_keys = snapshot["slot_keys"]
            connection.execute(
                """
                INSERT INTO design_selection_items (
                    selection_id, product_id, quantity, selection_role,
                    slot_key, slot_keys_json, repeat_group_id,
                    assignment_json, product_snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    selection_id,
                    snapshot["id"],
                    snapshot["quantity"],
                    snapshot["selection_role"],
                    slot_keys[0] if slot_keys else None,
                    json.dumps(slot_keys, ensure_ascii=False),
                    snapshot.get("repeat_group_id"),
                    json.dumps(
                        snapshot["slot_assignments"],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                ),
            )

        row = connection.execute(
            "SELECT * FROM design_selections WHERE id = ?",
            (selection_id,),
        ).fetchone()
        selection = _row_to_selection(connection, row)
        connection.commit()
        return selection, False
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def load_design_selection(
    selection_id: str,
    *,
    room_request_id: int | None = None,
) -> PersistedDesignSelection:
    try:
        normalized_id = str(UUID(selection_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid selection_id.") from exc

    ensure_design_selection_tables()
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM design_selections WHERE id = ?",
            (normalized_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Design selection not found.")
        if room_request_id is not None and int(row["room_request_id"]) != room_request_id:
            raise HTTPException(
                status_code=422,
                detail="The selection does not belong to this room request.",
            )
        if row["status"] == "invalidated":
            raise HTTPException(status_code=409, detail="Design selection was invalidated.")
        return _row_to_selection(connection, row)
    finally:
        connection.close()


def load_latest_template_design_selection(
    *,
    room_request_id: int,
    template_id: int,
) -> PersistedDesignSelection:
    """Load render inventory without exposing selection IDs to the client."""

    ensure_design_selection_tables()
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT *
            FROM design_selections
            WHERE room_request_id = ?
              AND template_id = ?
              AND status IN ('locked', 'rendered')
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (room_request_id, template_id),
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "This template has no immutable selection for the "
                        "room. Apply the template before rendering."
                    ),
                    "room_request_id": room_request_id,
                    "template_id": template_id,
                },
            )
        return _row_to_selection(connection, row)
    finally:
        connection.close()
