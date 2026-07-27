import base64
import hashlib
import json
import mimetypes
import os

from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from openai import APIConnectionError, APITimeoutError, OpenAI
from pydantic import BaseModel, Field

from app.database import get_connection
from app.models import RoomDimensions


PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(
    PROJECT_ROOT / ".env"
)


ROOM_ANALYSIS_MODEL = "gpt-5.6-sol"
ROOM_ANALYSIS_SCHEMA_VERSION = "2"
ROOM_ANALYSIS_PROMPT_VERSION = "2026-07-14.2"

class RoomAnalysis(BaseModel):
    analysis_source: Literal[
        "vision_model",
        "measured_fallback",
    ] = "vision_model"
    analysis_warning: str | None = None

    room_size: Literal[
        "small",
        "medium",
        "large"
    ]

    layout_shape: Literal[
        "square",
        "rectangular",
        "open_plan",
        "irregular",
        "unknown"
    ]

    provided_dimensions_cm: RoomDimensions | None = None

    wall_color_family: str | None = None
    floor_color_family: str | None = None
    floor_material: str | None = None
    natural_light_level: Literal[
        "low",
        "medium",
        "high",
        "unknown"
    ] | None = None
    room_openness: Literal[
        "closed",
        "semi_open",
        "open",
        "unknown"
    ] | None = None

    usable_walls: list[str] = Field(
        default_factory=list
    )

    obstacles: list[str] = Field(
        default_factory=list
    )

    circulation_paths: list[str] = Field(
        default_factory=list
    )

    # -----------------------------------------------------
    # New source-of-truth fields for new design logic.
    # These are visual estimates, not exact measurements.
    # -----------------------------------------------------

    estimated_max_standard_sofa_units: int | None = Field(
        default=None,
        ge=0,
        le=3
    )

    estimated_max_armchair_units: int | None = Field(
        default=None,
        ge=0,
        le=4
    )

    corner_zone_available: bool | None = Field(
        default=None
    )

    # -----------------------------------------------------
    # Deprecated compatibility fields.
    # Do not use them in new design logic.
    # They remain temporarily so old cached JSON can still load.
    # -----------------------------------------------------

    recommended_sofa_units: int | None = Field(
        default=None,
        ge=0,
        le=2
    )

    recommended_armchair_units: int | None = Field(
        default=None,
        ge=0,
        le=2
    )

    design_notes: list[str] = Field(
        default_factory=list
    )


def get_openai_client() -> OpenAI:
    api_key = os.getenv(
        "OPENAI_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY was not found. "
            "Make sure it exists in the project .env file."
        )

    return OpenAI(
        api_key=api_key
    )


def is_external_ai_connection_error(error: BaseException) -> bool:
    """Recognize only transport failures, including wrapped HTTP errors."""

    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, (APIConnectionError, APITimeoutError)):
            return True
        current = current.__cause__ or current.__context__
    return False


def build_measured_room_analysis(
    *,
    room_type: str,
    room_dimensions: RoomDimensions | None,
) -> RoomAnalysis:
    """Create an honest offline planning baseline from measured facts only.

    This deliberately leaves all image-derived architecture fields unknown.
    It is used only when the external vision service cannot be reached.
    """

    if room_dimensions is None:
        room_size = "medium"
        layout_shape = "unknown"
    else:
        area_m2 = (
            room_dimensions.length_cm * room_dimensions.width_cm / 10000
        )
        room_size = "small" if area_m2 < 12 else "large" if area_m2 > 28 else "medium"
        shortest = min(room_dimensions.length_cm, room_dimensions.width_cm)
        longest = max(room_dimensions.length_cm, room_dimensions.width_cm)
        layout_shape = "square" if longest / shortest <= 1.15 else "rectangular"

    return RoomAnalysis(
        analysis_source="measured_fallback",
        analysis_warning=(
            "External room-image analysis was unreachable. Recommendation is "
            "using measured dimensions only; doors, windows, openings, and "
            "circulation remain unknown and must be preserved conservatively."
        ),
        room_size=room_size,
        layout_shape=layout_shape,
        provided_dimensions_cm=room_dimensions,
        usable_walls=[],
        obstacles=[],
        circulation_paths=[],
        estimated_max_standard_sofa_units=None,
        estimated_max_armchair_units=None,
        corner_zone_available=None,
        design_notes=[
            "No visual architecture facts were inferred while the external analysis service was unreachable."
        ],
    )


def encode_image_as_data_url(
    image_path: Path
) -> str:
    if not image_path.exists():
        raise FileNotFoundError(
            f"Room image not found: {image_path}"
        )

    mime_type, _ = mimetypes.guess_type(
        image_path.name
    )

    if mime_type is None:
        mime_type = "image/jpeg"

    image_bytes = image_path.read_bytes()

    encoded_image = base64.b64encode(
        image_bytes
    ).decode(
        "utf-8"
    )

    return (
        f"data:{mime_type};base64,"
        f"{encoded_image}"
    )


def analyze_room_image(
    image_path: Path,
    room_type: str,
    room_dimensions: RoomDimensions | None = None
) -> RoomAnalysis:
    client = get_openai_client()

    image_data_url = (
        encode_image_as_data_url(
            image_path
        )
    )

    room_dimensions_text = (
        json.dumps(
            room_dimensions.model_dump(),
            ensure_ascii=False,
            indent=2
        )
        if room_dimensions is not None
        else "No measured room dimensions were provided."
    )

    prompt = f"""
Analyze this uploaded room image as an interior-space planning expert.

Room type:
{room_type}

USER-PROVIDED MEASURED ROOM DIMENSIONS:
{room_dimensions_text}

Your job is to report PHYSICAL AND VISUAL FACTS about the room only.
Do not decide the final design concept and do not choose furniture.

Determine:

1. Apparent room size:
   - small
   - medium
   - large

2. Apparent layout shape.

3. Visible walls or wall sections that appear realistically usable for
   furniture placement.

4. Obstacles such as:
   - doors
   - windows
   - openings
   - radiators
   - fixed architectural elements
   - columns

5. Important circulation paths that should remain open.

6. Detect simple visual matching fields for template matching:
   - wall_color_family
   - floor_color_family
   - floor_material
   - natural_light_level
   - room_openness

7. For living rooms only, cautiously estimate:
   - the maximum number of standard sofa units that appear visually
     plausible,
   - the maximum number of armchairs that appear visually plausible,
   - whether a usable corner or connected two-run seating zone appears
     available.

IMPORTANT RULES:

- When measured dimensions are supplied, treat them as authoritative overall room dimensions.
- Do not replace measured dimensions with visual guesses.
- The image remains the source of truth for doors, windows, openings, obstacles, circulation paths, and usable wall conditions.
- Overall dimensions alone do not provide exact door/window offsets or exact usable-wall lengths.
- These are visual estimates from one image, not exact measurements.
- Never invent exact wall lengths or room dimensions.
- If the image is insufficient to estimate a capacity responsibly, return
  null for that field.
- Do not use a fixed fallback number when uncertain.
- corner_zone_available means only that the visible geometry appears to
  provide a usable corner or connected seating zone. It does NOT mean a
  corner sofa should actually be selected.
- Preserve doors, windows, openings, and circulation.
- Do not write aesthetic decisions such as "this room should use two sofas".
- design_notes should contain factual planning observations only.

For non-living rooms:

- estimated_max_standard_sofa_units = null
- estimated_max_armchair_units = null
- corner_zone_available = null

For all newly generated analyses:

- recommended_sofa_units = null
- recommended_armchair_units = null

Those two recommended_* fields are deprecated compatibility fields and must
not be used for new analysis decisions.
""".strip()

    response = client.responses.parse(
        model=ROOM_ANALYSIS_MODEL,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt
                    },
                    {
                        "type": "input_image",
                        "image_url": (
                            image_data_url
                        )
                    }
                ]
            }
        ],
        text_format=RoomAnalysis
    )

    analysis = response.output_parsed

    if analysis is None:
        raise ValueError(
            "Room analysis model returned no parsed result."
        )

    analysis = analysis.model_copy(
        update={
            "provided_dimensions_cm": room_dimensions
        }
    )

    return analysis


# =========================================================
# Cached room analysis persistence
# =========================================================

def ensure_room_analyses_table() -> None:
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS room_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_request_id INTEGER NOT NULL UNIQUE,
                analysis_json TEXT NOT NULL,
                cache_signature TEXT,
                model_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (room_request_id)
                    REFERENCES room_requests(id)
                    ON DELETE CASCADE
            )
            """
        )

        columns = {
            row["name"]
            for row in cursor.execute(
                "PRAGMA table_info(room_analyses)"
            ).fetchall()
        }
        if "cache_signature" not in columns:
            cursor.execute(
                "ALTER TABLE room_analyses ADD COLUMN cache_signature TEXT"
            )

        connection.commit()

    finally:
        connection.close()


def get_saved_room_analysis(
    room_request_id: int,
    cache_signature: str | None = None,
) -> RoomAnalysis | None:
    ensure_room_analyses_table()

    connection = get_connection()
    cursor = connection.cursor()

    try:
        if cache_signature is None:
            cursor.execute(
                "SELECT analysis_json FROM room_analyses WHERE room_request_id = ?",
                (room_request_id,),
            )
        else:
            cursor.execute(
                """
                SELECT analysis_json
                FROM room_analyses
                WHERE room_request_id = ? AND cache_signature = ?
                """,
                (room_request_id, cache_signature),
            )

        row = cursor.fetchone()

    finally:
        connection.close()

    if row is None:
        return None

    try:
        analysis_json = row[
            "analysis_json"
        ]
    except (
        TypeError,
        KeyError,
        IndexError
    ):
        analysis_json = row[0]

    analysis_data = json.loads(
        analysis_json
    )

    return RoomAnalysis.model_validate(
        analysis_data
    )


def save_room_analysis(
    room_request_id: int,
    analysis: RoomAnalysis,
    cache_signature: str,
) -> None:
    ensure_room_analyses_table()

    connection = get_connection()
    cursor = connection.cursor()

    analysis_json = json.dumps(
        analysis.model_dump(),
        ensure_ascii=False
    )

    try:
        cursor.execute(
            """
            INSERT INTO room_analyses (
                room_request_id,
                analysis_json,
                model_name,
                cache_signature
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(room_request_id)
            DO UPDATE SET
                analysis_json = excluded.analysis_json,
                model_name = excluded.model_name,
                cache_signature = excluded.cache_signature,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                room_request_id,
                analysis_json,
                ROOM_ANALYSIS_MODEL,
                cache_signature,
            )
        )

        connection.commit()

    finally:
        connection.close()


def room_analysis_needs_upgrade(
    analysis: RoomAnalysis
) -> bool:
    """
    Detect a legacy cached living-room analysis that still contains old
    recommended_* design decisions but does not contain the new physical
    estimate fields. Such rows are re-analyzed once and overwritten.
    """

    new_fields_missing = (
        analysis.estimated_max_standard_sofa_units is None
        and analysis.estimated_max_armchair_units is None
        and analysis.corner_zone_available is None
        and not analysis.circulation_paths
    )

    legacy_decisions_present = (
        analysis.recommended_sofa_units is not None
        or analysis.recommended_armchair_units is not None
    )

    return (
        new_fields_missing
        and legacy_decisions_present
    )


def get_or_create_room_analysis(
    room_request_id: int,
    image_path: Path,
    room_type: str,
    room_dimensions: RoomDimensions | None = None
) -> tuple[
    RoomAnalysis,
    bool
]:
    """
    Returns:
        (analysis, analysis_cached)

    Existing old cache rows remain readable because the new RoomAnalysis
    fields are nullable and the deprecated recommended_* fields remain
    temporarily for compatibility.
    """

    previous_analysis = get_saved_room_analysis(room_request_id=room_request_id)
    effective_dimensions = room_dimensions
    if (
        effective_dimensions is None
        and previous_analysis is not None
        and previous_analysis.provided_dimensions_cm is not None
    ):
        effective_dimensions = previous_analysis.provided_dimensions_cm

    cache_signature = build_room_analysis_signature(
        image_path=image_path,
        room_type=room_type,
        room_dimensions=effective_dimensions,
    )
    saved_analysis = get_saved_room_analysis(
        room_request_id=room_request_id,
        cache_signature=cache_signature,
    )

    dimensions_changed = False

    if saved_analysis is not None:
        saved_dimensions = (
            saved_analysis.provided_dimensions_cm.model_dump()
            if saved_analysis.provided_dimensions_cm is not None
            else None
        )

        current_dimensions = (
            effective_dimensions.model_dump()
            if effective_dimensions is not None
            else None
        )

        dimensions_changed = saved_dimensions != current_dimensions

    if (
        saved_analysis is not None
        and not dimensions_changed
        and not room_analysis_needs_upgrade(
            saved_analysis
        )
    ):
        return (
            saved_analysis,
            True
        )

    # Legacy room-analysis rows are intentionally regenerated once so new
    # logic never depends on deprecated recommended_* design decisions.
    try:
        analysis = analyze_room_image(
            image_path=image_path,
            room_type=room_type,
            room_dimensions=effective_dimensions
        )
    except Exception as error:
        if not is_external_ai_connection_error(error):
            raise
        analysis = build_measured_room_analysis(
            room_type=room_type,
            room_dimensions=effective_dimensions,
        )

    save_room_analysis(
        room_request_id=room_request_id,
        analysis=analysis,
        cache_signature=cache_signature,
    )

    return (
        analysis,
        False,
    )


def build_room_analysis_signature(
    image_path: Path,
    room_type: str,
    room_dimensions: RoomDimensions | None,
) -> str:
    digest = hashlib.sha256()
    with image_path.open("rb") as image_file:
        for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
            digest.update(chunk)
    payload = {
        "image_sha256": digest.hexdigest(),
        "room_type": room_type,
        "room_dimensions": (
            room_dimensions.model_dump() if room_dimensions else None
        ),
        "model": ROOM_ANALYSIS_MODEL,
        "prompt_version": ROOM_ANALYSIS_PROMPT_VERSION,
        "schema_version": ROOM_ANALYSIS_SCHEMA_VERSION,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
