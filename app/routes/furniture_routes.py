import json
import shutil
from typing import Literal

from io import BytesIO

from PIL import Image
import pillow_avif

from pathlib import Path
from uuid import uuid4

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Query,
    UploadFile,
    status
)

from app.database import get_connection

from app.enums import (
    FurnitureCategory,
    FurnitureStyle,
    RoomType
)

from app.models import (
    FurnitureCreate,
    FurnitureResponse,
    FurnitureUpdate
)


router = APIRouter(tags=["Furniture"])


# =========================================================
# Paths
# =========================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FURNITURE_UPLOAD_DIR = (
    PROJECT_ROOT / "uploads" / "furniture"
)

FURNITURE_UPLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# =========================================================
# Allowed image types
# =========================================================

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/avif": ".avif"
}


# =========================================================
# Helpers
# =========================================================

def encode_list(values: list) -> str:
    return json.dumps(
        values,
        ensure_ascii=False
    )


def decode_list(
    value: str | None
) -> list[str]:

    if not value:
        return []

    try:
        parsed_value = json.loads(value)

        if isinstance(parsed_value, list):
            return parsed_value

    except json.JSONDecodeError:
        pass

    # Fallback for old comma-separated values.
    return [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]


def row_to_furniture(row) -> dict:
    furniture = dict(row)

    furniture["room_types"] = decode_list(
        furniture.get("room_types")
    )

    furniture["tags"] = decode_list(
        furniture.get("tags")
    )

    furniture["additional_source_image_urls"] = decode_list(
        furniture.get("additional_source_image_urls")
    )

    furniture["included_pieces"] = decode_list(
        furniture.get("included_pieces")
    )

    source_metadata = furniture.get("source_metadata")
    if isinstance(source_metadata, str):
        try:
            decoded_metadata = json.loads(source_metadata)
            furniture["source_metadata"] = (
                decoded_metadata if isinstance(decoded_metadata, dict) else {}
            )
        except json.JSONDecodeError:
            furniture["source_metadata"] = {}

    furniture["availability"] = bool(
        furniture.get("availability")
    )
    furniture["design_eligible"] = bool(
        furniture.get("design_eligible", True)
    )
    furniture["is_product_set"] = bool(
        furniture.get("is_product_set", False)
    )

    return furniture


# =========================================================
# POST /furniture
# Create furniture without image.
#
# Image is uploaded separately through:
# POST /furniture/{item_id}/image
# =========================================================

@router.post(
    "/furniture",
    response_model=FurnitureResponse,
    status_code=status.HTTP_201_CREATED
)
def create_furniture(
    item: FurnitureCreate
):
    data = item.model_dump(
        mode="json"
    )

    room_types_json = encode_list(
        data["room_types"]
    )

    tags_json = encode_list(
        data["tags"]
    )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("""
            INSERT INTO furniture (
                name,
                category,
                style,
                color,
                material,
                width_cm,
                depth_cm,
                height_cm,
                price,
                image_filename,
                product_url,
                store_name,
                currency,
                room_types,
                tags,
                availability
            )
            VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?
            )
        """, (
            data["name"],
            data["category"],
            data["style"],
            data["color"],
            data["material"],

            data["width_cm"],
            data["depth_cm"],
            data["height_cm"],

            data["price"],

            None,

            data["product_url"],
            data["store_name"],
            data["currency"],

            room_types_json,
            tags_json,

            int(data["availability"])
        ))

        connection.commit()

        new_id = cursor.lastrowid

        cursor.execute("""
            SELECT *
            FROM furniture
            WHERE id = ?
        """, (
            new_id,
        ))

        row = cursor.fetchone()

    finally:
        connection.close()

    return row_to_furniture(row)


# =========================================================
# GET /furniture
# =========================================================

@router.get(
    "/furniture",
    response_model=list[FurnitureResponse]
)
def get_all_furniture(
    category: FurnitureCategory | None = Query(
        default=None
    ),

    room_type: RoomType | None = Query(
        default=None
    ),

    style: FurnitureStyle | None = Query(
        default=None
    ),

    color: str | None = Query(
        default=None
    ),

    min_price: float | None = Query(
        default=None,
        ge=0
    ),

    max_price: float | None = Query(
        default=None,
        ge=0
    ),

    availability: bool | None = Query(
        default=None
    ),

    store_name: str | None = Query(
        default=None,
        min_length=1,
        max_length=100
    ),

    design_eligible: bool | None = Query(
        default=None
    ),

    catalog_status: Literal["active", "shop_only", "quarantined"] | None = Query(
        default=None
    )
):
    if (
        min_price is not None
        and max_price is not None
        and min_price > max_price
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "min_price cannot be greater "
                "than max_price"
            )
        )

    query = """
        SELECT *
        FROM furniture
        WHERE 1 = 1
    """

    params = []

    if category is not None:
        query += " AND category = ?"
        params.append(
            category.value
        )

    if style is not None:
        query += " AND style = ?"
        params.append(
            style.value
        )

    if color is not None:
        query += """
            AND LOWER(color) = LOWER(?)
        """

        params.append(color)

    if min_price is not None:
        query += " AND price >= ?"
        params.append(min_price)

    if max_price is not None:
        query += " AND price <= ?"
        params.append(max_price)

    if availability is not None:
        query += " AND availability = ?"
        params.append(
            int(availability)
        )

    if store_name is not None:
        query += " AND store_name = ?"
        params.append(store_name)

    if design_eligible is not None:
        query += " AND design_eligible = ?"
        params.append(int(design_eligible))

    if catalog_status is not None:
        query += " AND catalog_status = ?"
        params.append(catalog_status)

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            query,
            params
        )

        rows = cursor.fetchall()

    finally:
        connection.close()

    items = [
        row_to_furniture(row)
        for row in rows
    ]

    # room_types is stored as JSON list,
    # so filtering is done after decoding.
    if room_type is not None:
        items = [
            item
            for item in items
            if room_type.value
            in item["room_types"]
        ]

    return items


# =========================================================
# GET /furniture/{item_id}
# =========================================================

@router.get(
    "/furniture/{item_id}",
    response_model=FurnitureResponse
)
def get_furniture_by_id(
    item_id: int
):
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("""
            SELECT *
            FROM furniture
            WHERE id = ?
        """, (
            item_id,
        ))

        row = cursor.fetchone()

    finally:
        connection.close()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Furniture item not found"
        )

    return row_to_furniture(row)


# =========================================================
# PUT /furniture/{item_id}
# Full replacement.
#
# Existing image_filename is preserved.
# =========================================================

@router.put(
    "/furniture/{item_id}",
    response_model=FurnitureResponse
)
def replace_furniture(
    item_id: int,
    updated_item: FurnitureCreate
):
    data = updated_item.model_dump(
        mode="json"
    )

    room_types_json = encode_list(
        data["room_types"]
    )

    tags_json = encode_list(
        data["tags"]
    )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("""
            UPDATE furniture
            SET
                name = ?,
                category = ?,
                style = ?,
                color = ?,
                material = ?,
                width_cm = ?,
                depth_cm = ?,
                height_cm = ?,
                price = ?,
                product_url = ?,
                store_name = ?,
                currency = ?,
                room_types = ?,
                tags = ?,
                availability = ?
            WHERE id = ?
        """, (
            data["name"],
            data["category"],
            data["style"],
            data["color"],
            data["material"],

            data["width_cm"],
            data["depth_cm"],
            data["height_cm"],

            data["price"],

            data["product_url"],
            data["store_name"],
            data["currency"],

            room_types_json,
            tags_json,

            int(data["availability"]),

            item_id
        ))

        connection.commit()

        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Furniture item not found"
            )

        cursor.execute("""
            SELECT *
            FROM furniture
            WHERE id = ?
        """, (
            item_id,
        ))

        row = cursor.fetchone()

    finally:
        connection.close()

    return row_to_furniture(row)


# =========================================================
# PATCH /furniture/{item_id}
# Partial update.
# =========================================================

@router.patch(
    "/furniture/{item_id}",
    response_model=FurnitureResponse
)
def update_furniture(
    item_id: int,
    updated_item: FurnitureUpdate
):
    data = updated_item.model_dump(
        mode="json",
        exclude_unset=True
    )

    if not data:
        raise HTTPException(
            status_code=400,
            detail=(
                "No fields were provided "
                "for update"
            )
        )

    if "room_types" in data:
        data["room_types"] = encode_list(
            data["room_types"]
        )

    if "tags" in data:
        data["tags"] = encode_list(
            data["tags"]
        )

    if "availability" in data:
        data["availability"] = int(
            data["availability"]
        )

    allowed_fields = {
        "name",
        "category",
        "style",
        "color",
        "material",

        "width_cm",
        "depth_cm",
        "height_cm",

        "price",

        "product_url",
        "store_name",
        "currency",

        "room_types",
        "tags",
        "availability"
    }

    update_fields = {
        key: value
        for key, value in data.items()
        if key in allowed_fields
    }

    set_clause = ", ".join(
        f"{field} = ?"
        for field in update_fields
    )

    values = list(
        update_fields.values()
    )

    values.append(item_id)

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            f"""
            UPDATE furniture
            SET {set_clause}
            WHERE id = ?
            """,
            values
        )

        connection.commit()

        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Furniture item not found"
            )

        cursor.execute("""
            SELECT *
            FROM furniture
            WHERE id = ?
        """, (
            item_id,
        ))

        row = cursor.fetchone()

    finally:
        connection.close()

    return row_to_furniture(row)


# =========================================================
# POST /furniture/{item_id}/image
#
# Upload or replace local furniture product image.
# =========================================================

@router.post(
    "/furniture/{item_id}/image"
)
def upload_furniture_image(
    item_id: int,
    image: UploadFile = File(...)
):
    if image.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                "Unsupported image type. "
                "Allowed types: JPEG, PNG, WEBP, AVIF"
            )
        )

    connection = get_connection()
    cursor = connection.cursor()

    new_file_path = None

    try:
        # ================================================
        # 1. Check furniture exists
        # ================================================

        cursor.execute(
            """
            SELECT id, image_filename
            FROM furniture
            WHERE id = ?
            """,
            (item_id,)
        )

        row = cursor.fetchone()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Furniture item not found"
            )

        old_image_filename = row["image_filename"]

        # ================================================
        # 2. Read uploaded image
        # ================================================

        image_bytes = image.file.read()

        # ================================================
        # 3. Convert AVIF to PNG
        # ================================================

        if image.content_type == "image/avif":

            try:
                pil_image = Image.open(
                    BytesIO(image_bytes)
                )

                # Preserve transparency if present
                if pil_image.mode not in ("RGB", "RGBA"):
                    pil_image = pil_image.convert("RGBA")

                unique_filename = (
                    f"furniture_{item_id}_{uuid4()}.png"
                )

                new_file_path = (
                    FURNITURE_UPLOAD_DIR
                    / unique_filename
                )

                pil_image.save(
                    new_file_path,
                    format="PNG"
                )

            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail="Failed to convert AVIF image to PNG"
                ) from exc

        # ================================================
        # 4. Save JPEG / PNG / WEBP normally
        # ================================================

        else:
            extension = ALLOWED_IMAGE_TYPES[
                image.content_type
            ]

            unique_filename = (
                f"furniture_{item_id}_"
                f"{uuid4()}"
                f"{extension}"
            )

            new_file_path = (
                FURNITURE_UPLOAD_DIR
                / unique_filename
            )

            with new_file_path.open("wb") as buffer:
                buffer.write(image_bytes)

        # ================================================
        # 5. Save filename in database
        # ================================================

        cursor.execute(
            """
            UPDATE furniture
            SET image_filename = ?
            WHERE id = ?
            """,
            (
                unique_filename,
                item_id
            )
        )

        connection.commit()

        # ================================================
        # 6. Delete previous local image
        # ================================================

        if old_image_filename:
            old_file_path = (
                FURNITURE_UPLOAD_DIR
                / old_image_filename
            )

            if old_file_path.exists():
                old_file_path.unlink()

    except HTTPException:
        raise

    except OSError as exc:
        connection.rollback()

        if (
            new_file_path is not None
            and new_file_path.exists()
        ):
            new_file_path.unlink()

        raise HTTPException(
            status_code=500,
            detail="Failed to save furniture image"
        ) from exc

    except Exception as exc:
        connection.rollback()

        if (
            new_file_path is not None
            and new_file_path.exists()
        ):
            new_file_path.unlink()

        raise HTTPException(
            status_code=500,
            detail="Failed to update furniture image"
        ) from exc

    finally:
        connection.close()

    return {
        "message": "Furniture image uploaded successfully",
        "furniture_id": item_id,
        "original_content_type": image.content_type,
        "image_filename": unique_filename
    }


# =========================================================
# DELETE /furniture/{item_id}
#
# Deletes DB record and local image, if any.
# =========================================================

@router.delete(
    "/furniture/{item_id}"
)
def delete_furniture(
    item_id: int
):
    connection = get_connection()
    cursor = connection.cursor()

    image_filename = None

    try:
        cursor.execute("""
            SELECT image_filename
            FROM furniture
            WHERE id = ?
        """, (
            item_id,
        ))

        row = cursor.fetchone()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Furniture item not found"
            )

        image_filename = row[
            "image_filename"
        ]

        cursor.execute("""
            DELETE FROM furniture
            WHERE id = ?
        """, (
            item_id,
        ))

        connection.commit()

    finally:
        connection.close()

    if image_filename:
        file_path = (
            FURNITURE_UPLOAD_DIR
            / image_filename
        )

        if file_path.exists():
            file_path.unlink()

    return {
        "message": (
            "Furniture item deleted successfully"
        )
    }
