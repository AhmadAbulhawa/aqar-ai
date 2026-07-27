from __future__ import annotations

import json
from uuid import uuid4

from app.database import GENERATED_DESIGNS_TABLE_SQL, get_connection


def load_latest_generated_design(
    *,
    room_request_id: int,
    selection_id: str,
) -> dict | None:
    """Return the latest persisted render audit without starting a render."""

    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id, room_request_id, selection_id, generated_image_filename,
                   manifest_json, compliance_status, compliance_json,
                   model_name, created_at
            FROM generated_designs
            WHERE room_request_id = ? AND selection_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (room_request_id, selection_id),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    result = dict(row)
    result["manifest"] = json.loads(result.pop("manifest_json"))
    result["compliance"] = json.loads(result.pop("compliance_json"))
    return result


def save_generated_design(
    *,
    room_request_id: int,
    selection_id: str | None,
    generated_image_filename: str,
    manifest: dict,
    compliance: dict,
    model_name: str | None,
) -> str:
    generated_design_id = str(uuid4())
    connection = get_connection()
    try:
        connection.execute(GENERATED_DESIGNS_TABLE_SQL)
        connection.execute(
            """
            INSERT INTO generated_designs (
                id, room_request_id, selection_id, generated_image_filename,
                manifest_json, compliance_status, compliance_json, model_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                generated_design_id,
                room_request_id,
                selection_id,
                generated_image_filename,
                json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                compliance["status"],
                json.dumps(compliance, ensure_ascii=False, sort_keys=True),
                model_name,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return generated_design_id
