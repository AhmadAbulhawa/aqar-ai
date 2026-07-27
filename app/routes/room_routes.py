import json
import shutil

from pathlib import Path
from uuid import uuid4

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile
)

from app.database import get_connection
from app.enums import (
    FurnitureStyle,
    RoomType
)


router = APIRouter(
    prefix="/rooms",
    tags=["Rooms"]
)


# =========================================================
# Paths
# =========================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_DIR = PROJECT_ROOT / "uploads" / "rooms"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================
# Allowed image types
# =========================================================

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp"
}


# =========================================================
# Helpers
# =========================================================

def parse_preferred_colors(value: str | None) -> list[str]:
    if not value:
        return []

    try:
        parsed_value = json.loads(value)

        if isinstance(parsed_value, list):
            return [
                str(color).strip().lower()
                for color in parsed_value
                if str(color).strip()
            ]

    except (json.JSONDecodeError, TypeError):
        pass

    return [
        color.strip().lower()
        for color in value.split(",")
        if color.strip()
    ]


def normalize_colors_for_database(value: str | None) -> str | None:
    colors = parse_preferred_colors(value)

    if not colors:
        return None

    return json.dumps(colors, ensure_ascii=False)


def row_to_room_request(row) -> dict:
    room_request = dict(row)

    room_request["preferred_colors"] = parse_preferred_colors(
        room_request.get("preferred_colors")
    )

    return room_request


def validate_dimension_input(
    room_length_cm: float | None,
    room_width_cm: float | None,
    room_height_cm: float | None
) -> None:
    any_dimension_supplied = any(
        value is not None
        for value in [
            room_length_cm,
            room_width_cm,
            room_height_cm
        ]
    )

    if not any_dimension_supplied:
        return

    if room_length_cm is None or room_width_cm is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "If room dimensions are provided, "
                "room_length_cm and room_width_cm are both required. "
                "room_height_cm is optional."
            )
        )


# =========================================================
# POST /rooms/upload
# =========================================================

@router.post("/upload")
def upload_room_image(
    image: UploadFile = File(...),
    room_type: RoomType = Form(...),
    budget: float | None = Form(default=None, gt=0),
    style: FurnitureStyle | None = Form(default=None),
    preferred_colors: str | None = Form(default=None),

    # Real measured dimensions from frontend.
    room_length_cm: float | None = Form(default=None, gt=0),
    room_width_cm: float | None = Form(default=None, gt=0),
    room_height_cm: float | None = Form(default=None, gt=0)
):
    if image.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                "Unsupported room image type. "
                "Allowed types: JPEG, PNG, WEBP."
            )
        )

    validate_dimension_input(
        room_length_cm=room_length_cm,
        room_width_cm=room_width_cm,
        room_height_cm=room_height_cm
    )

    extension = ALLOWED_IMAGE_TYPES[image.content_type]
    unique_filename = f"{uuid4()}{extension}"
    destination_path = UPLOAD_DIR / unique_filename

    try:
        with destination_path.open("wb") as buffer:
            shutil.copyfileobj(image.file, buffer)

    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="Failed to save room image"
        ) from exc

    original_filename = image.filename or "uploaded-room"
    style_value = style.value if style else None
    colors_value = normalize_colors_for_database(preferred_colors)

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO room_requests (
                image_filename,
                original_filename,
                content_type,
                room_type,
                budget,
                style,
                preferred_colors,
                room_length_cm,
                room_width_cm,
                room_height_cm
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                unique_filename,
                original_filename,
                image.content_type,
                room_type.value,
                budget,
                style_value,
                colors_value,
                room_length_cm,
                room_width_cm,
                room_height_cm
            )
        )

        connection.commit()
        room_request_id = cursor.lastrowid

    except Exception as exc:
        connection.rollback()

        if destination_path.exists():
            destination_path.unlink()

        raise HTTPException(
            status_code=500,
            detail="Failed to save room request"
        ) from exc

    finally:
        connection.close()

    return {
        "message": "Room image uploaded successfully",
        "room_request_id": room_request_id,
        "filename": unique_filename,
        "original_filename": original_filename,
        "content_type": image.content_type,
        "room_type": room_type.value,
        "budget": budget,
        "style": style_value,
        "preferred_colors": parse_preferred_colors(colors_value),
        "room_dimensions": (
            {
                "length_cm": room_length_cm,
                "width_cm": room_width_cm,
                "height_cm": room_height_cm
            }
            if room_length_cm is not None and room_width_cm is not None
            else None
        )
    }


# =========================================================
# GET /rooms
# =========================================================

@router.get("")
def get_room_requests():
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT *
            FROM room_requests
            ORDER BY id DESC
            """
        )

        rows = cursor.fetchall()

    finally:
        connection.close()

    return [row_to_room_request(row) for row in rows]


# =========================================================
# GET /rooms/{room_request_id}
# =========================================================

@router.get("/{room_request_id}")
def get_room_request(room_request_id: int):
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT *
            FROM room_requests
            WHERE id = ?
            """,
            (room_request_id,)
        )

        row = cursor.fetchone()

    finally:
        connection.close()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Room request not found"
        )

    return row_to_room_request(row)
