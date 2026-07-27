import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError


# =========================================================
# Paths
# =========================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ROOM_UPLOAD_DIR = (
    PROJECT_ROOT / "uploads" / "rooms"
)

FURNITURE_UPLOAD_DIR = (
    PROJECT_ROOT / "uploads" / "furniture"
)

RENDER_REFERENCE_DIR = PROJECT_ROOT / "var" / "render_references"
REFERENCE_CANVAS_SIZE = 1024
REFERENCE_PADDING = 48
MIN_REFERENCE_PIXEL_AREA = 160_000


# =========================================================
# Get local room image path
# =========================================================

def get_room_image_path(
    image_filename: str
) -> Path:

    room_image_path = (
        ROOM_UPLOAD_DIR
        / image_filename
    )

    if not room_image_path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "Room image file not found"
            )
        )

    return room_image_path


# =========================================================
# Get local furniture image path
# =========================================================

def get_furniture_image_path(
    image_filename: str
) -> Path:

    furniture_image_path = (
        FURNITURE_UPLOAD_DIR
        / image_filename
    )

    if not furniture_image_path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"Furniture image file not found: "
                f"{image_filename}"
            )
        )

    return furniture_image_path


# =========================================================
# Prepare selected furniture reference images
# =========================================================

def prepare_furniture_reference_images(
    recommended_items: list[dict]
) -> list[dict]:

    source_images: list[tuple[dict, Path, tuple[int, int]]] = []
    low_resolution_products: list[dict] = []

    for item in recommended_items:

        furniture_id = item["id"]

        image_filename = item.get(
            "image_filename"
        )

        if not image_filename:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Furniture ID {furniture_id} "
                    f"does not have a local image. "
                    f"Upload one using "
                    f"POST /furniture/"
                    f"{furniture_id}/image"
                )
            )

        local_image_path = (
            get_furniture_image_path(
                image_filename=image_filename
            )
        )

        try:
            with Image.open(local_image_path) as source:
                source_size = source.size
        except (OSError, UnidentifiedImageError) as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "A selected product reference image is unreadable.",
                    "error_code": "product_reference_unreadable",
                    "product_id": furniture_id,
                    "image_filename": image_filename,
                    "automatic_paid_request_sent": False,
                },
            ) from exc

        source_width, source_height = source_size
        if source_width * source_height < MIN_REFERENCE_PIXEL_AREA:
            low_resolution_products.append({
                "product_id": furniture_id,
                "name": item["name"],
                "image_filename": image_filename,
                "width_px": source_width,
                "height_px": source_height,
            })
        source_images.append((item, local_image_path, source_size))

    if low_resolution_products:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Exact-product rendering was stopped before generation because "
                    "one or more product reference images are too small to preserve "
                    "their construction reliably. Replace those catalog images with "
                    "higher-resolution official product images."
                ),
                "error_code": "product_reference_too_small",
                "minimum_pixel_area": MIN_REFERENCE_PIXEL_AREA,
                "products": low_resolution_products,
                "automatic_paid_request_sent": False,
            },
        )

    RENDER_REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    prepared_images = []
    for item, local_image_path, source_size in source_images:
        furniture_id = int(item["id"])
        image_filename = str(item["image_filename"])
        source_signature = hashlib.sha256(local_image_path.read_bytes()).hexdigest()
        prepared_path = RENDER_REFERENCE_DIR / (
            f"product_{furniture_id}_{source_signature[:20]}_1024.png"
        )
        if not prepared_path.exists():
            with Image.open(local_image_path) as source:
                normalized = ImageOps.exif_transpose(source).convert("RGBA")
                normalized.thumbnail(
                    (
                        REFERENCE_CANVAS_SIZE - (REFERENCE_PADDING * 2),
                        REFERENCE_CANVAS_SIZE - (REFERENCE_PADDING * 2),
                    ),
                    Image.Resampling.LANCZOS,
                )
                canvas = Image.new(
                    "RGBA",
                    (REFERENCE_CANVAS_SIZE, REFERENCE_CANVAS_SIZE),
                    (255, 255, 255, 255),
                )
                position = (
                    (REFERENCE_CANVAS_SIZE - normalized.width) // 2,
                    (REFERENCE_CANVAS_SIZE - normalized.height) // 2,
                )
                canvas.alpha_composite(normalized, position)
                temporary_path = prepared_path.with_name(
                    f"{prepared_path.stem}.{uuid4().hex}.tmp"
                )
                canvas.convert("RGB").save(temporary_path, format="PNG")
                temporary_path.replace(prepared_path)

        prepared_images.append({
            "id": furniture_id,

            "name": item["name"],

            "category": item["category"],

            "image_filename":
                image_filename,

            "local_image_path": str(
                prepared_path
            ),

            "source_local_image_path": str(local_image_path),

            "source_image_width": source_size[0],

            "source_image_height": source_size[1],

            "prepared_image_width": REFERENCE_CANVAS_SIZE,

            "prepared_image_height": REFERENCE_CANVAS_SIZE,
        })

    return prepared_images
