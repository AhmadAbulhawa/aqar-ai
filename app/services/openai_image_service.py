"""GPT Image 2 integration where the renderer owns visual space planning."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from contextlib import ExitStack
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import HTTPException
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATED_DESIGNS_DIR = PROJECT_ROOT / "uploads" / "generated_designs"
GENERATED_DESIGNS_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger("uvicorn.error")

MODEL_NAME = "gpt-image-2"
RENDER_PROMPT_VERSION = "2026-07-21.1-repeated-corner-layout"
MAX_TOTAL_INPUT_IMAGES = 16
MAX_FURNITURE_REFERENCE_IMAGES = 15
TARGET_PROMPT_LENGTH = 18_000
HARD_PROMPT_LIMIT = 28_000
DEFAULT_IMAGE_TIMEOUT_SECONDS = 900.0
SUPPORTED_INPUT_IMAGE_MIME_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


def _image_timeout_seconds() -> float:
    raw_value = os.getenv("OPENAI_IMAGE_TIMEOUT_SECONDS", "")
    if not raw_value:
        return DEFAULT_IMAGE_TIMEOUT_SECONDS
    try:
        timeout = float(raw_value)
    except ValueError:
        return DEFAULT_IMAGE_TIMEOUT_SECONDS
    return timeout if timeout >= 30 else DEFAULT_IMAGE_TIMEOUT_SECONDS


def get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY is not set. Add it to the root .env file.",
        )
    # Image edits are billable. Disable the SDK's automatic retry behavior so
    # one explicit Studio click always results in at most one upstream request.
    return OpenAI(
        api_key=api_key,
        timeout=_image_timeout_seconds(),
        max_retries=0,
    )


def _upstream_error_message(exc: Exception) -> str | None:
    body = getattr(exc, "body", None)
    candidates: list[object] = []
    if isinstance(body, dict):
        nested_error = body.get("error")
        if isinstance(nested_error, dict):
            candidates.append(nested_error.get("message"))
        candidates.append(body.get("message"))
    candidates.append(getattr(exc, "message", None))
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        message = " ".join(candidate.split()).strip()
        if message:
            return message[:800]
    return None


def _upstream_error_code(exc: Exception) -> str | None:
    code = getattr(exc, "code", None)
    body = getattr(exc, "body", None)
    if not code and isinstance(body, dict):
        nested_error = body.get("error")
        if isinstance(nested_error, dict):
            code = nested_error.get("code")
        code = code or body.get("code")
    return str(code)[:120] if code else None


def _raise_image_service_error(
    exc: Exception,
    *,
    prompt_metadata: dict,
    input_image_count: int,
) -> NoReturn:
    upstream_message = _upstream_error_message(exc)
    upstream_code = _upstream_error_code(exc)
    upstream_status = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    cause = getattr(exc, "__cause__", None)

    status_code = 502
    retryable = False
    message = "The image service rejected the render request."
    if isinstance(exc, APITimeoutError):
        status_code = 504
        retryable = True
        message = (
            "The image service timed out before returning a result. "
            "The reviewed inventory remains locked; no automatic retry was made."
        )
    elif isinstance(exc, APIConnectionError):
        status_code = 503
        retryable = True
        message = (
            "The backend could not maintain a connection to the image service. "
            "The reviewed inventory remains locked; no automatic retry was made."
        )
    elif isinstance(exc, RateLimitError):
        status_code = 429
        retryable = True
        message = (
            "The image service rate limit was reached. Wait before trying Generate again; "
            "the reviewed inventory remains locked."
        )
    elif isinstance(exc, AuthenticationError):
        status_code = 502
        message = "The image service rejected the configured API credentials."
    elif isinstance(exc, PermissionDeniedError):
        status_code = 502
        message = "The configured API project is not permitted to run this image edit."
    elif isinstance(exc, BadRequestError):
        status_code = 422
        if upstream_code == "moderation_blocked":
            message = (
                "The image service safety check rejected the prompt or an input image. "
                "Review the room and product images before trying again."
            )
        elif upstream_message:
            message = f"The image service rejected the render request: {upstream_message}"
    elif isinstance(exc, InternalServerError):
        status_code = 502
        retryable = True
        message = (
            "The image service reported a temporary server error. "
            "The reviewed inventory remains locked; no automatic retry was made."
        )
    elif isinstance(exc, APIStatusError):
        if isinstance(upstream_status, int) and 400 <= upstream_status < 500:
            status_code = upstream_status
        retryable = isinstance(upstream_status, int) and upstream_status >= 500
        if upstream_message:
            message = f"The image service request failed: {upstream_message}"
    else:
        status_code = 500
        message = "The backend failed while preparing the image-service request."

    diagnostic = {
        "service": "openai_images",
        "model": MODEL_NAME,
        "error_type": type(exc).__name__,
        "error_code": upstream_code,
        "upstream_status": upstream_status,
        "request_id": str(request_id) if request_id else None,
        "transport_error_type": type(cause).__name__ if cause else None,
        "retryable": retryable,
        "automatic_retry_attempted": False,
        "input_image_count": input_image_count,
        **prompt_metadata,
    }
    logger.error("image_generation_failed %s", json.dumps(diagnostic, sort_keys=True))
    raise HTTPException(
        status_code=status_code,
        detail={
            "message": message,
            **diagnostic,
            "selection_preserved": True,
        },
    ) from exc


def choose_output_size(room_image_path: Path) -> str:
    """Choose the nearest supported output aspect ratio."""

    try:
        with Image.open(room_image_path) as image:
            width, height = image.size
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to read room image dimensions: {exc}",
        ) from exc
    if width <= 0 or height <= 0:
        return "1536x1024"
    aspect_ratio = width / height
    if aspect_ratio >= 1.15:
        return "1536x1024"
    if aspect_ratio <= 0.87:
        return "1024x1536"
    return "1024x1024"


def validated_input_image_mime_type(image_path: Path) -> str:
    """Inspect file content and return an explicit Images API MIME type."""

    try:
        with Image.open(image_path) as image:
            image_format = str(image.format or "").upper()
            image.verify()
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "A render input is not a valid image file.",
                "filename": image_path.name,
                "automatic_paid_request_sent": False,
            },
        ) from exc
    mime_type = SUPPORTED_INPUT_IMAGE_MIME_TYPES.get(image_format)
    if not mime_type:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    f"Unsupported render input format {image_format or 'unknown'} for "
                    f"{image_path.name}. Use JPEG, PNG, or WebP."
                ),
                "filename": image_path.name,
                "detected_format": image_format or None,
                "supported_formats": sorted(SUPPORTED_INPUT_IMAGE_MIME_TYPES),
                "automatic_paid_request_sent": False,
            },
        )
    return mime_type


def placement_plan_to_dict(placement_plan) -> dict | None:
    """Read-compatible serializer for legacy rows and callers."""

    if placement_plan is None:
        return None
    if hasattr(placement_plan, "model_dump"):
        return placement_plan.model_dump()
    if isinstance(placement_plan, dict):
        return placement_plan
    raise TypeError("placement_plan must be a Pydantic model, dict, or None.")


def build_placement_plan_text(placement_plan) -> str:
    """Return a compatibility notice without exposing legacy coordinates.

    Old immutable rows can contain placement data, but GPT Image 2 must not
    treat that symbolic plan as an authoritative visual-design instruction.
    """

    if placement_plan_to_dict(placement_plan) is None:
        return "Pre-render placement plan: not required."
    return (
        "Legacy placement metadata exists for backward compatibility but is "
        "non-authoritative and must not control the rendered arrangement."
    )


def _quantity_phrase(quantity: int) -> str:
    words = {
        1: "ONE",
        2: "TWO",
        3: "THREE",
        4: "FOUR",
        5: "FIVE",
        6: "SIX",
        7: "SEVEN",
        8: "EIGHT",
        9: "NINE",
        10: "TEN",
    }
    return words.get(quantity, str(quantity))


def _concise_text(value: object, maximum_length: int) -> str:
    """Normalize and bound one descriptive field, never the full prompt."""

    normalized = " ".join(str(value or "").split())
    if len(normalized) <= maximum_length:
        return normalized
    shortened = normalized[: maximum_length - 1].rsplit(" ", 1)[0]
    return (shortened or normalized[: maximum_length - 1]).rstrip(" ,;:") + "…"


def _measurement(value: object) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "unknown"
    return str(int(numeric)) if numeric.is_integer() else str(round(numeric, 1))


def _unique_descriptions(
    values: list[object],
    *,
    maximum_items: int,
    maximum_length: int,
) -> list[str]:
    descriptions: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            raw_description = value.get("description")
        else:
            raw_description = value
        description = _concise_text(raw_description, maximum_length)
        key = description.casefold()
        if not description or key in seen:
            continue
        seen.add(key)
        descriptions.append(description.rstrip(" .") + ".")
        if len(descriptions) >= maximum_items:
            break
    return descriptions


def build_exact_product_checklist(
    recommended_items: list[dict],
    start_index: int = 2,
    *,
    compact_optional: bool = False,
) -> str:
    lines: list[str] = []
    for index, item in enumerate(recommended_items, start=start_index):
        quantity = int(item.get("quantity", 1))
        dimensions = " x ".join(
            _measurement(item.get(field))
            for field in ("width_cm", "depth_cm", "height_cm")
        )
        product_id = int(item["id"])
        name = _concise_text(
            item.get("name") or f"Product {product_id}",
            90 if compact_optional else 150,
        )
        category = _concise_text(item.get("category") or "unknown", 60)
        lines.extend(
            [
                f"Product {product_id} (reference image {index}):",
                f"- Name: {name}",
                f"- Category: {category}",
                f"- quantity EXACTLY {quantity} physical unit{'s' if quantity != 1 else ''}",
                f"- Dimensions W x D x H: {dimensions} cm",
                f"- Render exactly {_quantity_phrase(quantity)} visible physical "
                f"unit{'s' if quantity != 1 else ''} of product {product_id}.",
                f"- Reference image {index} is this product's immutable construction "
                "blueprint, not style inspiration.",
                "- Preserve its recognizable silhouette, construction and module/section "
                "count, arm/back/seat/cushion geometry, legs or base, proportions, "
                "material, color, and distinctive details.",
            ]
        )
        if quantity > 1:
            lines.append(
                f"- Show {quantity} separate, complete, recognizable units. Never fuse, "
                "join, merge, or reinterpret them as one larger product or sectional."
            )
    return "\n".join(lines)


def _rule_mentions_unselected_inventory(
    rule: str,
    selected_categories: set[str],
) -> bool:
    normalized = rule.casefold()
    category_tokens = {
        "wall_art": ("artwork", "wall art", "painting", "canvas"),
        "floor_lamp": ("floor lamp",),
        "storage_cabinet": ("storage cabinet", "glass-front cabinet"),
        "side_table": ("side table", "pedestal table"),
        "rug": ("rug",),
        "armchair": ("armchair", "chair"),
        "television": ("television", " tv ", "tv-facing"),
        "tv_stand": ("tv stand", "tv bench", "media console"),
    }
    return any(
        category not in selected_categories
        and any(token in f" {normalized} " for token in tokens)
        for category, tokens in category_tokens.items()
    )


def _is_rigid_template_topology_rule(rule: str) -> bool:
    """Exclude template wording that makes adaptable topology absolute."""

    normalized = rule.casefold()
    rigid_phrases = (
        "do not convert",
        "must remain",
        "must be face",
        "never change",
        "preserve exactly",
    )
    topology_phrases = (
        "face-to-face",
        "face to face",
        "facing sofa",
        "l-shaped",
        "l shaped",
        "television-facing",
        "tv-facing",
    )
    return any(phrase in normalized for phrase in rigid_phrases) and any(
        phrase in normalized for phrase in topology_phrases
    )


def _is_permanent_finish_rule(rule: str) -> bool:
    """Reject template styling that would repaint or rematerialize Image 1."""

    normalized = rule.casefold()
    permanent_finish_phrases = (
        "accent wall",
        "wall color",
        "wall colour",
        "paint the wall",
        "painted wall",
        "wallpaper",
        "wall paneling",
        "wall panelling",
        "white walls",
        "dark wall",
        "charcoal wall",
        "wood floor",
        "wood flooring",
        "timber floor",
        "laminate floor",
        "tile floor",
        "tiled floor",
        "concrete floor",
        "floor material",
        "floor color",
        "floor colour",
        "paint the ceiling",
        "ceiling material",
        "ceiling color",
        "ceiling colour",
        "fixed trim",
    )
    return any(phrase in normalized for phrase in permanent_finish_phrases)


def _layout_adaptation_rules(
    layout_type: str,
    selected_items: list[dict],
) -> list[str]:
    """Translate every supported semantic layout into safe visual guidance."""

    normalized = layout_type.casefold()
    selected_categories = {str(item.get("category")) for item in selected_items}
    seating_quantity = sum(
        max(int(item.get("quantity", 1)), 1)
        for item in selected_items
        if item.get("category") in {"sofa", "armchair"}
    )
    rules: list[str] = []

    if "facing_sofas" in normalized and seating_quantity >= 2:
        rules.extend(
            [
                (
                    "Prefer the face-to-face sofa arrangement when safely feasible. "
                    "Otherwise adapt the sofa topology while preserving the same "
                    "selected sofas and overall visual character."
                ),
                (
                    "A perpendicular, angled, partially offset, asymmetric, or "
                    "L-shaped sofa arrangement is allowed when needed for door or "
                    "window access, primary circulation, comfortable spacing, or "
                    "the immediate entry path."
                ),
                (
                    "Use the usable room depth instead of lining both sofas along "
                    "the same rear wall. When the camera geometry supports it, place "
                    "one sofa nearer the camera with its back or three-quarter back "
                    "visible and the opposing sofa farther into the room."
                ),
            ]
        )
    elif "u_shape" in normalized and seating_quantity >= 2:
        rules.extend(
            [
                (
                    "Use the selected seating to suggest an open U-shaped conversation "
                    "zone around the central surface, with the open side preserving the "
                    "main approach and circulation."
                ),
                (
                    "Use both room width and depth; do not flatten all seating against "
                    "one wall merely to show every product front-on."
                ),
            ]
        )
    elif "l_shape" in normalized and seating_quantity >= 1:
        rules.extend(
            [
                (
                    "Create a readable L-shaped seating relationship from the selected "
                    "seating, using a safe corner or perpendicular orientation without "
                    "covering windows or narrowing the entry path."
                ),
                (
                    "Let one seating unit show its side or back to the camera when that "
                    "uses the room depth and produces the stronger real-world layout."
                ),
            ]
        )
    elif "media_wall" in normalized and "television" in selected_categories:
        rules.append(
            "Organize selected seating around a comfortable viewing relationship to "
            "the selected television while keeping the media wall and circulation clear."
        )
    elif "single_sofa" in normalized and "sofa" in selected_categories:
        rules.append(
            "Use the selected sofa as the primary anchor with deliberate negative "
            "space and only the selected supporting products around it."
        )
    elif "floating_zone" in normalized:
        rules.append(
            "Float the main selected furniture group within the usable floor area "
            "instead of pushing every product against the perimeter."
        )
    elif "bed_wall" in normalized and "bed" in selected_categories:
        rules.append(
            "Anchor the selected bed on the safest uninterrupted wall and preserve "
            "balanced bedside access, door operation, and wardrobe circulation."
        )
    elif "workstation" in normalized and "desk" in selected_categories:
        rules.append(
            "Build a functional workstation from the selected desk and task seating, "
            "with usable chair clearance and a screen orientation that avoids glare."
        )
    elif "dining_centered" in normalized and "dining_table" in selected_categories:
        rules.append(
            "Center the selected dining group in the usable zone with realistic chair "
            "pull-out clearance and an unobstructed route around the table."
        )
    elif "storage_optimized" in normalized:
        rules.append(
            "Consolidate selected storage on safe usable wall sections while keeping "
            "openings, service access, and the center of the room clear."
        )
    elif "functional_zoning" in normalized:
        rules.extend(
            [
                (
                    "Distribute the selected inventory into clear functional zones "
                    "appropriate to each product instead of clustering everything in "
                    "one corner or along one wall."
                ),
                (
                    "Use the full safe width and depth of the room while keeping a "
                    "continuous route between the zones."
                ),
            ]
        )
    else:
        rules.append(
            "Use the full safe width and depth of the room and avoid clustering all "
            "selected furniture against one wall."
        )

    return rules


def build_template_blueprint_text(
    template_blueprint: dict | None,
    recommended_items: list[dict] | None = None,
    *,
    compact_optional: bool = False,
) -> str:
    if not template_blueprint:
        return ""
    selected_items = recommended_items or []
    selected_categories = {
        str(item.get("category")) for item in selected_items
    }
    selected_slot_keys = {
        str(slot_key)
        for item in selected_items
        for slot_key in (item.get("slot_keys") or [])
    }
    slots = list(template_blueprint.get("product_slots") or [])
    unresolved_categories = {
        str(slot.get("category"))
        for slot in slots
        if str(slot.get("slot_key")) not in selected_slot_keys
    }
    layout_type = _concise_text(
        template_blueprint.get("layout_type") or "preserve selected template layout",
        100,
    )
    density_target = _concise_text(
        template_blueprint.get("density_target") or "balanced",
        40,
    )
    rules = _layout_adaptation_rules(layout_type, selected_items)
    if "coffee_table" in selected_categories:
        rules.append("Keep the selected coffee table centered naturally between the sofas.")
    if "rug" in selected_categories:
        rules.append("Ground the primary seating group with the selected rug.")
    rules.append(
        f"Preserve a {density_target} composition with comfortable negative space and no overcrowding."
    )
    if {"television", "tv_stand"} <= selected_categories:
        if "facing_sofas" in layout_type:
            rules.append(
                "Prefer a natural conversation relationship rather than orienting all seating only toward the television, when safely feasible."
            )
        rules.append(
            "Include the selected TV and TV stand together on the largest observed "
            "continuous, unobstructed wall when safe. Prefer a realistic wall-mounted "
            "TV above the selected stand; never place it over a window or opening."
        )

    maximum_rules = 2 if compact_optional else 5
    for raw_rule in template_blueprint.get("key_visual_rules") or []:
        rule = _concise_text(raw_rule, 180 if compact_optional else 260)
        if (
            not rule
            or _rule_mentions_unselected_inventory(rule, selected_categories)
            or _is_rigid_template_topology_rule(rule)
            or _is_permanent_finish_rule(rule)
            or any(rule.casefold() == existing.casefold() for existing in rules)
        ):
            continue
        rules.append(rule.rstrip(" .") + ".")
        if len(rules) >= 6 + maximum_rules:
            break

    notes = [
        (
            "Preserve the template composition when safely possible. However, "
            "architecture, door clearance, window access, and primary circulation "
            "have higher priority. You may change the exact sofa orientation, "
            "including using a perpendicular or L-shaped arrangement, when "
            "necessary to keep the room safe, accessible, comfortable, and "
            "visually balanced."
        ),
        "Use only products present in the exact inventory; unresolved template concepts are not physical inventory.",
        (
            "Never remove a selected product merely because the original template "
            "topology cannot fit safely; adapt positions and relationships instead."
        ),
    ]
    if "wall_art" in unresolved_categories and "wall_art" not in selected_categories:
        notes.append(
            "Do not invent wall art; adapt the focal hierarchy using only selected inventory."
        )
    return "\n".join(
        [
            f"Template layout: {layout_type}",
            "Essential composition and relationships:",
            *(f"- {rule}" for rule in rules),
            "Inventory boundary:",
            *(f"- {note}" for note in notes),
        ]
    )


def _manifest_dict(render_manifest: dict | object | None) -> dict:
    if render_manifest is None:
        return {}
    if isinstance(render_manifest, dict):
        return render_manifest
    if hasattr(render_manifest, "model_dump"):
        return render_manifest.model_dump(mode="json")
    raise TypeError("render_manifest must be a dict, Pydantic model, or None.")


def _room_and_safety_text(
    *,
    room_type: str,
    room_analysis: dict,
    architectural_constraints: dict,
    compact_optional: bool,
) -> str:
    maximum_items = 2 if compact_optional else 4
    maximum_description = 150 if compact_optional else 220
    dimensions = room_analysis.get("provided_dimensions_cm") or {}
    dimension_text = " x ".join(
        _measurement(dimensions.get(field))
        for field in ("length_cm", "width_cm", "height_cm")
    )
    door_values = list(architectural_constraints.get("door_zones") or [])
    door_swing_values = list(
        architectural_constraints.get("door_swing_zones") or []
    )
    window_values = list(architectural_constraints.get("window_zones") or [])
    structured_circulation_values = list(
        architectural_constraints.get("circulation_corridors") or []
    )
    circulation_values = structured_circulation_values or list(
        room_analysis.get("circulation_paths") or []
    )

    def evidence(value: object) -> str:
        if isinstance(value, dict):
            explicit = str(value.get("evidence") or "").strip().upper()
            description = str(value.get("description") or "").casefold()
        else:
            explicit = ""
            description = str(value).casefold()
        if explicit:
            return explicit
        if any(token in description for token in ("not visible", "unseen", "outside camera")):
            return "NOT_VISIBLE"
        return "OBSERVED_UNCERTAIN"

    visible_door_values = [
        value
        for value in door_values
        if evidence(value) in {"OBSERVED_CONFIRMED", "OBSERVED_UNCERTAIN"}
    ]
    visible_door_swing_values = [
        value
        for value in door_swing_values
        if evidence(value) in {"OBSERVED_CONFIRMED", "OBSERVED_UNCERTAIN"}
    ]
    unverified_door_values = [
        value for value in door_values if evidence(value) in {"INFERRED", "NOT_VISIBLE"}
    ]
    observed_circulation_values = [
        value
        for value in circulation_values
        if evidence(value) in {"OBSERVED_CONFIRMED", "OBSERVED_UNCERTAIN"}
    ]
    unverified_circulation_values = [
        value
        for value in circulation_values
        if evidence(value) in {"INFERRED", "NOT_VISIBLE"}
    ]
    doors = _unique_descriptions(
        [*visible_door_values, *visible_door_swing_values],
        maximum_items=maximum_items,
        maximum_length=maximum_description,
    )
    windows = _unique_descriptions(
        window_values,
        maximum_items=maximum_items,
        maximum_length=maximum_description,
    )
    circulation = _unique_descriptions(
        observed_circulation_values,
        maximum_items=maximum_items,
        maximum_length=maximum_description,
    )
    obstacles = _unique_descriptions(
        [
            obstacle
            for obstacle in (room_analysis.get("obstacles") or [])
            if not any(
                token in str(obstacle).casefold()
                for token in ("door", "window", "entry", "opening")
            )
        ],
        maximum_items=maximum_items,
        maximum_length=maximum_description,
    )

    facts = [
        f"- Room type: {room_type.replace('_', ' ').title()}",
        f"- Dimensions: {dimension_text} cm",
        f"- Shape: {_concise_text(room_analysis.get('layout_shape') or 'unknown', 60).title()}",
    ]
    if room_analysis.get("floor_material"):
        facts.append(
            "- Original permanent floor material: "
            + _concise_text(room_analysis.get("floor_material"), 120)
        )
    if room_analysis.get("wall_color_family"):
        facts.append(
            "- Original permanent wall appearance: "
            + _concise_text(room_analysis.get("wall_color_family"), 80)
        )
    facts.extend(
        f"- Confirmed visible door/opening fact: {description}"
        for description in doors
    )
    facts.extend(
        f"- Confirmed visible window fact: {description}"
        for description in windows
    )
    facts.extend(f"- Important obstacle: {description}" for description in obstacles)
    facts.extend(
        f"- Confirmed primary circulation/hallway fact: {description}"
        for description in circulation
    )
    facts.extend(
        "- Unverified opening context (not enforceable geometry): " + description
        for description in _unique_descriptions(
            unverified_door_values,
            maximum_items=maximum_items,
            maximum_length=maximum_description,
        )
    )
    facts.extend(
        "- Inferred or not-visible circulation context (do not invent a route): "
        + description
        for description in _unique_descriptions(
            unverified_circulation_values,
            maximum_items=maximum_items,
            maximum_length=maximum_description,
        )
    )
    return "\n".join(["Confirmed supporting facts:", *facts])


def _concise_design_intent_text(
    design_intent: dict,
    *,
    template_blueprint_present: bool,
) -> str:
    """Extract renderer-relevant fields and omit locked debug payloads."""

    strategy = design_intent.get("design_strategy") or {}
    if not isinstance(strategy, dict):
        return ""
    layout = strategy.get("layout") or {}
    lines: list[str] = []
    if not template_blueprint_present:
        final_layout = layout.get("final_layout_strategy")
        if final_layout:
            lines.append(
                f"- Preferred layout: {_concise_text(final_layout, 100)}."
            )
    for label, field in (("Density", "density_target"), ("Balance", "symmetry")):
        value = strategy.get(field)
        if value:
            lines.append(f"- {label}: {_concise_text(value, 60)}.")
    if not template_blueprint_present and strategy.get("focal_point"):
        lines.append(
            "- Establish the focal hierarchy with selected inventory only; "
            f"preferred focus: {_concise_text(strategy.get('focal_point'), 80)}."
        )
    return "\n".join(lines)


def _design_strategy_guidance_text(
    design_intent: dict,
    recommended_items: list[dict],
    *,
    template_blueprint_present: bool,
) -> str:
    """Expose high-level Auto strategy without leaking symbolic coordinates."""

    if template_blueprint_present:
        return ""
    strategy = design_intent.get("design_strategy") or {}
    if not isinstance(strategy, dict):
        return ""
    layout = strategy.get("layout") or {}
    layout_type = str(layout.get("final_layout_strategy") or "").strip()
    lines: list[str] = []
    if layout_type:
        lines.append(f"Preferred semantic layout: {_concise_text(layout_type, 100)}.")
        lines.extend(_layout_adaptation_rules(layout_type, recommended_items))
    for label, field in (("Density", "density_target"), ("Balance", "symmetry")):
        value = strategy.get(field)
        if value:
            lines.append(f"{label}: {_concise_text(value, 60)}.")
    focal = str(strategy.get("focal_point") or "").strip()
    selected_categories = {
        str(item.get("category")) for item in recommended_items
    }
    if focal and focal != "none":
        focal_requires_missing_product = (
            focal == "tv_wall" and "television" not in selected_categories
        ) or (
            focal == "art_wall" and "wall_art" not in selected_categories
        )
        if not focal_requires_missing_product:
            lines.append(
                "Preferred focal hierarchy: " + _concise_text(focal, 80) + "."
            )
    return "\n".join(f"- {line}" for line in lines)


_STANDARD_MEDIA_LIVING_PURPOSES = {
    "complete_living_room", "family_living", "general_living",
    "general_living_room", "living_room", "media_living_room", "media_room", "media_wall",
    "media_wall_focused", "standard_living", "standard_living_room", "television_room",
    "tv_room", "tv_viewing_living_room",
}
_UNSPECIFIED_PURPOSES = {"", "default", "none", "unknown", "unspecified"}
_PURPOSE_FIELDS = ("functional_intent", "room_purpose", "purpose", "room_type")
_TEMPLATE_CONSTRAINT_FIELDS = (
    "key_visual_rules", "constraints", "hard_constraints", "layout_constraints",
    "composition_summary",
)
_EXPLICIT_NON_MEDIA_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bnon[\s-]+media(?:\s+living)?\s+room\b",
        r"\bconversation[\s-]+only(?:\s+(?:living\s+)?room)?\b",
        r"\bdo\s+not\s+(?:orient|angle|face)\b[^.!?\n]{0,120}\b(?:tv|television)\b",
        r"\bdo\s+not\s+convert\b[^.!?\n]{0,160}\b(?:tv|television)[\s-]+facing\s+layout\b",
        r"\bavoid\b[^.!?\n]{0,100}\b(?:tv|television)[\s-]+facing\s+layout\b",
        r"\b(?:reading\s+room|home\s+library)\b",
    )
)


def _normalized_policy_value(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")


def _inventory_has_category(items: list[dict], *categories: str) -> bool:
    expected = {_normalized_policy_value(category) for category in categories}
    return any(_normalized_policy_value(item.get("category")) in expected for item in items)


def _structured_purpose_exempts_tv_default(
    *,
    room_type: str,
    design_intent: dict,
) -> bool:
    strategy = design_intent.get("design_strategy") or {}
    sources = (
        design_intent, strategy if isinstance(strategy, dict) else {},
    )
    purposes = [_normalized_policy_value(room_type)]
    for source in sources:
        if isinstance(source, dict):
            purposes.extend(
                _normalized_policy_value(source.get(field))
                for field in _PURPOSE_FIELDS if source.get(field) is not None
            )
    return any(
        purpose not in _UNSPECIFIED_PURPOSES
        and purpose not in _STANDARD_MEDIA_LIVING_PURPOSES
        for purpose in purposes
    )


def _matches_explicit_non_media_intent(texts: list[str]) -> bool:
    return any(pattern.search(text) for text in texts for pattern in _EXPLICIT_NON_MEDIA_PATTERNS)


def _template_explicitly_exempts_tv_default(
    template_context: dict | None,
    locked_template_blueprint: dict | None,
) -> bool:
    texts: list[str] = []
    for source in (template_context or {}, locked_template_blueprint or {}):
        for field in _TEMPLATE_CONSTRAINT_FIELDS:
            value = source.get(field)
            if isinstance(value, str):
                texts.append(value)
            elif isinstance(value, list):
                texts.extend(item for item in value if isinstance(item, str))
    return _matches_explicit_non_media_intent(texts)


def _optional_coffee_table_instruction(items: list[dict]) -> str | None:
    return (
        "- Keep the selected coffee table naturally accessible without blocking movement."
        if _inventory_has_category(items, "coffee_table") else None
    )


def _living_room_tv_principle_text(
    *,
    room_type: str,
    recommended_items: list[dict],
    explicit_user_requirements: list[str],
    design_intent: dict,
) -> str:
    if (
        _normalized_policy_value(room_type) != "living_room"
        or not _inventory_has_category(recommended_items, "television", "tv")
        or _structured_purpose_exempts_tv_default(
            room_type=room_type,
            design_intent=design_intent,
        )
        or _matches_explicit_non_media_intent(explicit_user_requirements)
    ):
        return ""

    instructions = [
        "- Treat the selected television as the primary functional focal point.",
        "- Create a clear, comfortable, unobstructed viewing relationship between the\n"
        "  main seating and the selected television.",
        "- Orient the primary sofa or sectional directly or predominantly toward the\n"
        "  television. It does not need to face the camera.",
        "- Secondary seating may angle toward both the television and the conversation area.",
        "- Place the television and its selected supporting furniture in a visually stable,\n"
        "  believable media zone.",
        "- Functional TV viewing and realistic room planning outrank presenting the front\n"
        "  face of each product to the camera.",
    ]
    coffee_table_instruction = _optional_coffee_table_instruction(recommended_items)
    if coffee_table_instruction:
        instructions.insert(4, coffee_table_instruction)
    return "\n".join(instructions)


def _professional_space_utilization_text(
    *,
    room_type: str,
    recommended_items: list[dict],
    living_room_tv_principle_text: str,
) -> str:
    """Give GPT Image 2 strong design direction without placement geometry."""

    if _normalized_policy_value(room_type) != "living_room":
        return ""

    major_order = (
        "television/media zone; primary sofa or sectional; secondary seating; "
        "rug and selected coffee table; storage and cabinets; side tables and "
        "lamps; then accessories"
        if living_room_tv_principle_text
        else (
            "primary sofa or sectional; secondary seating; rug and selected "
            "coffee table; storage and cabinets; side tables and lamps; then "
            "accessories"
        )
    )
    coffee_boundary = (
        "- Keep the selected coffee table naturally accessible from the main seating."
        if (
            _inventory_has_category(recommended_items, "coffee_table")
            and not living_room_tv_principle_text
        )
        else ""
    )
    repeated_corner_or_sectional = any(
        int(item.get("quantity", 1)) >= 2
        and any(
            marker in " ".join(
                [
                    str(item.get("name") or ""),
                    str(item.get("category") or ""),
                    " ".join(str(tag) for tag in item.get("tags") or []),
                ]
            ).casefold()
            for marker in ("corner sofa", "corner_sofa", "sectional", "l-shape", "l_shape")
        )
        for item in recommended_items
    )
    lines = [
        "- Design the room for real human use, not product-camera presentation.",
        "- Behave like a senior residential interior designer, not a product-catalog compositor.",
        "- Establish one clear primary furniture zone and build one coherent composition\n"
        "  around the room's main function; do not place products as isolated display items.",
        "- Use the complete available room footprint intelligently. Use the room's depth,\n"
        "  width, logical walls, corners, and spatial boundaries to create balanced visual weight.",
        "- Avoid an arbitrary floating main sofa, avoid compressing the composition into one\n"
        "  small area, and avoid large areas of meaningless or unjustified dead space.",
        "- Open space is valid only when it genuinely serves confirmed circulation, door\n"
        "  operation, entry landing, window access, or deliberate breathing room around a\n"
        "  complete furniture zone.",
        "- Connect the seating, rug, selected tables, media, lighting, storage, and side\n"
        "  furniture into a believable lived-in arrangement with clear focal hierarchy, proper\n"
        "  rug anchoring, usable reach, natural spacing, comfortable scale, and intentional zoning.",
        f"- Arrange major furniture first in this functional priority: {major_order}.",
        "- Minor furniture and accessories must adapt around the main layout; never weaken the\n"
        "  primary seating relationship merely to display a small item prominently.",
        "- The primary sofa may show its back or side to the camera. Natural perspective may\n"
        "  partially occlude one product with another when there is no physical collision.",
        "- Camera visibility must never control furniture orientation. Every selected major\n"
        "  product must be present with its exact identity and quantity, but it need not face\n"
        "  the camera or be fully unobstructed.",
        "- Preserve only the clearance genuinely needed for visible doors, door operation,\n"
        "  entries, windows, and confirmed primary circulation. Do not create oversized empty\n"
        "  safety zones or invent a prohibited route from an unseen entrance.",
        "- Exact placement remains the responsibility of GPT Image 2. Choose the best exact\n"
        "  arrangement for this specific room without coordinates, wall numbers, or fixed topology.",
    ]
    if coffee_boundary:
        lines.insert(9, coffee_boundary)
    if repeated_corner_or_sectional:
        lines.extend(
            [
                "- This inventory contains repeated complete corner/sectional sofas. Prefer one\n"
                "  complete unit safely aligned with an appropriate wall and orient the other\n"
                "  complete unit toward the selected television or primary focal point. A valid\n"
                "  circulation route may run behind the second unit when Image 1 supports it.",
                "- Rotate and position each repeated sofa only as one rigid complete product. Never\n"
                "  straighten it, remove or shorten its corner return/chaise, rearrange its modules,\n"
                "  or change its handedness to make the composition fit.",
            ]
        )
    if living_room_tv_principle_text:
        lines.extend(["", living_room_tv_principle_text])
    return "\n".join(lines)


def build_render_prompt_debug_metadata(prompt: str) -> dict:
    return {
        "render_prompt_length": len(prompt),
        "render_prompt_target_limit": TARGET_PROMPT_LENGTH,
        "render_prompt_hard_limit": HARD_PROMPT_LIMIT,
        "render_prompt_compacted": True,
    }


def _broad_template_atmosphere_text(template_context: dict | None) -> str:
    """Keep visual character without overriding permanent room finishes."""

    if not template_context:
        return ""
    if template_context.get("semantics") == "product_source_only":
        return ""
    labels = (
        ("Template style", "style"),
        ("Natural-light character", "natural_light_level"),
        ("Openness character", "room_openness"),
        ("Density character", "density_target"),
    )
    character = "\n".join(
        f"- {label}: {_concise_text(template_context.get(field), 80)}"
        for label, field in labels
        if template_context.get(field)
    )
    finish_boundary = (
        "- Template wall, floor, and permanent-finish metadata is non-authoritative; "
        "preserve the finishes visible in Image 1 exactly."
    )
    return "\n".join(value for value in (character, finish_boundary) if value)


def build_generation_prompt(
    recommended_items: list[dict],
    room_type: str,
    style: str | None,
    placement_plan=None,
    template_context: dict | None = None,
    template_blueprint: dict | None = None,
    template_reference_used: bool = False,
    room_analysis: dict | None = None,
    architectural_constraints: dict | None = None,
    hard_constraints: list[str] | None = None,
    design_intent: dict | None = None,
    render_manifest: dict | object | None = None,
) -> str:
    """Build the compact, placement-independent primary-designer prompt."""

    manifest = _manifest_dict(render_manifest)
    room_analysis = manifest.get("room_analysis", room_analysis or {})
    architectural_constraints = manifest.get(
        "architectural_constraints", architectural_constraints or {}
    )
    template_context = manifest.get("template") or template_context
    manifest_design_intent = manifest.get("design_intent") or design_intent or {}
    template_product_source = bool(template_context)
    locked_template_blueprint = (
        manifest_design_intent.get("locked_blueprint")
        if isinstance(manifest_design_intent, dict)
        else None
    ) or template_blueprint
    if template_product_source:
        # Templates select products only. No legacy blueprint or template-derived
        # strategy may influence the arrangement sent to GPT Image 2.
        locked_template_blueprint = None
    explicit_user_requirements = list(
        manifest.get("explicit_user_requirements") or []
    )
    del (
        placement_plan,
        template_reference_used,
        hard_constraints,
    )

    product_start_index = 2
    product_checklist = build_exact_product_checklist(
        recommended_items,
        start_index=product_start_index,
    )
    room_text = _room_and_safety_text(
        room_type=room_type,
        room_analysis=room_analysis,
        architectural_constraints=architectural_constraints,
        compact_optional=False,
    )
    atmosphere_text = "" if template_product_source else _broad_template_atmosphere_text(
        template_context
    )
    template_composition_text = ""
    strategy_guidance_text = (
        ""
        if template_product_source
        else _design_strategy_guidance_text(
            manifest_design_intent,
            recommended_items,
            template_blueprint_present=False,
        )
    )
    living_room_tv_principle_text = _living_room_tv_principle_text(
        room_type=room_type,
        recommended_items=recommended_items,
        explicit_user_requirements=explicit_user_requirements,
        design_intent=manifest_design_intent,
    )
    professional_space_utilization_text = _professional_space_utilization_text(
        room_type=room_type,
        recommended_items=recommended_items,
        living_room_tv_principle_text=living_room_tv_principle_text,
    )
    professional_space_utilization_section = (
        "PROFESSIONAL SPACE-UTILIZATION AND FURNITURE-ORIENTATION PRINCIPLE\n\n"
        + professional_space_utilization_text
        if professional_space_utilization_text
        else ""
    )
    template_boundary_text = (
        "- The template supplied product identities only. Its image, architecture, "
        "camera,\n  topology, sofa orientation, placement zones, and composition have "
        "no authority\n  in this render. Design a completely new arrangement for Image 1."
        if template_product_source
        else "- No template product source applies."
    )
    user_requirements_text = (
        "\n".join(f"- {requirement}" for requirement in explicit_user_requirements)
        if explicit_user_requirements
        else "- No explicit user-requested furniture topology or placement constraint."
    )

    prompt = f"""
ROLE

You are the PRIMARY INTERIOR DESIGNER AND RENDERER.
Study the original room image carefully. Use all exact selected products in
their exact required quantities and create the most beautiful, comfortable,
realistic, functional, and visually balanced interior for this specific room.

INPUTS

Image 1: original room and authoritative architectural source of truth.
Images 2+: exact selected product references in the inventory order below.

{room_text}

ORIGINAL CAMERA VIEWPOINT AND FRAMING ARE AUTHORITATIVE

Preserve the original camera viewpoint and image composition. Keep the same
camera position, viewing direction, eye level, perspective, field of view,
horizon, framing, and approximate crop as the original uploaded room image.

Do not rotate the camera, move it to another wall or corner, zoom significantly
in or out, crop away visible architectural areas, create a reverse angle, or use
an alternative viewpoint. Doors, windows, walls, floor boundaries, and visible
room corners must remain in approximately the same positions within the image
frame. Design the furnished room from this exact original viewpoint.

Furniture orientation is independent from camera viewpoint. Furniture does not
need to face the camera. A sofa, chair, cabinet, or other item may show its back
or side to the camera when that creates a better real-world interior arrangement.
Treat this as a real interior design problem, not a furniture catalog showcase.
Do not sacrifice door access, circulation, comfort, realistic spacing, or design
quality merely to show the front of every product.

HARD CONSTRAINTS: EVIDENCE-BASED ARCHITECTURAL SAFETY GATE

Before considering any furniture arrangement, inspect Image 1 and protect every
visible door opening, observed door-operating area, immediate entry landing,
usable window, hallway, and confirmed primary circulation route. Reserve only
the clearance genuinely required for operation and realistic human movement;
then use the remaining room footprint intelligently.

Inventory compliance never overrides architectural clearance. If a candidate
arrangement physically blocks or materially narrows one of those evidenced
areas, discard it and design another one. Do not invent a door, route, swing, or
oversized safety zone from an unseen or merely inferred area.

EXACT IMMUTABLE INVENTORY

{product_checklist}

PRODUCT-REFERENCE FIDELITY IS A HARD CONSTRAINT

Images 2+ are exact physical product blueprints, never mood-board or style
inspiration. Copy each referenced product's construction faithfully into Image 1.
Preserve the exact recognizable silhouette, frame topology, module/section count,
handedness, arms, backrests, seats, cushion divisions and seams, legs, pedestal or
base, lamp stem and lamp head, proportions, material, finish, color, and distinctive
details visible in its reference image.

Changing a sectional into a different sectional construction, changing the number
or arrangement of modules, changing a lamp's stem/head/base, recoloring or
reupholstering a product, or substituting a same-category lookalike is prohibited.
Perspective and natural partial occlusion may change only how a product is seen;
they may not change what the product physically is.

Quantity is a physical identity constraint. Quantity 2 means two separate complete
units of that same referenced SKU, not one enlarged object, one fused sectional, or
one object that merely suggests two. Never merge repeated units. If the exact
products do not fit a candidate layout without structural alteration, discard that
layout and simplify spacing or optional styling instead; never redesign a product.

BROAD STYLE / ATMOSPHERE

- Requested style: {style or "designer's best judgment"}
{atmosphere_text}

EXPLICIT USER REQUIREMENTS

{user_requirements_text}

TEMPLATE MODE PRODUCT-SOURCE BOUNDARY

{template_boundary_text}

DESIGN STRATEGY

{strategy_guidance_text or "- No separate semantic design strategy applies."}

COMPLETE VISUAL-DESIGN AUTHORITY

Choose the complete furniture arrangement yourself. You decide every furniture
position, orientation, relationship, spacing decision, visual balance, negative
space, focal hierarchy, symmetry or asymmetry, and final composition.

Do not follow any predefined sofa topology, furniture relationship, named-product
placement, wall assignment, template image, template topology, or template room
zone. Only EXPLICIT USER REQUIREMENTS and the functional design principles in
this prompt may guide the intended layout, and they never override Image 1
architecture, permanent finishes, visible door or window access, confirmed
primary circulation, realistic spacing, or the exact inventory.

VISIBLE DOOR, ENTRY, HALLWAY, AND CIRCULATION SAFETY

This is the highest-priority, non-negotiable validity rule:

- Never block a visible door, doorway, entrance, or hallway.
- Keep the actual visible doorway, full opening width, threshold, genuinely needed
  operating area, and immediate entry landing usable and unobstructed.
- No furniture may physically occupy, block, or materially narrow those evidenced
  areas. Natural camera-view overlap is not physical intrusion.
- Preserve a continuous body-width route only for a visible or authoritatively
  confirmed primary route. Do not create a pinch point beside a visible doorway.
- If hinge side or exact swing geometry is uncertain, preserve reasonable
  conservative operating clearance without creating an oversized empty zone.
- Never use a doorway, entrance landing, hallway, or circulation route as a place
  to fit leftover furniture. Recompose the design within the remaining safe area.

OTHER HARD CONSTRAINTS

- Preserve the original room architecture from Image 1. Do not remove, move,
  resize, cover, or invent walls, doors, windows, or other openings.
- Preserve every existing permanent finish from Image 1, including floor
  material, wall material, ceiling form, and fixed architectural trim. Do not
  redesign these permanent finishes unless the user explicitly requests it.
- Never block a visible window or reasonable window access.
- Use every exact selected product in its exact required quantity. Do not omit,
  merge, substitute, or replace selected major products.
- Do not invent unselected major furniture.
- Maintain realistic physical scale and avoid impossible major-furniture overlap.

{professional_space_utilization_section}

MANDATORY PRE-OUTPUT CLEARANCE SCAN

Before producing the image, inspect every furniture unit against every visible
door, threshold, evidenced swing/operating area, entrance landing, hallway, and
confirmed primary route. If any item physically blocks or materially narrows one
of those areas, the design is invalid: rearrange it. Preserve necessary safety
without turning uncertain or unseen routes into large empty prohibited zones.
""".strip()
    if len(prompt) > TARGET_PROMPT_LENGTH:
        compact_room_text = _room_and_safety_text(
            room_type=room_type,
            room_analysis=room_analysis,
            architectural_constraints=architectural_constraints,
            compact_optional=True,
        )
        compact_product_checklist = build_exact_product_checklist(
            recommended_items,
            start_index=product_start_index,
            compact_optional=True,
        )
        prompt = prompt.replace(room_text, compact_room_text)
        prompt = prompt.replace(product_checklist, compact_product_checklist)
        if template_composition_text:
            prompt = prompt.replace(
                template_composition_text,
                build_template_blueprint_text(
                    locked_template_blueprint,
                    recommended_items,
                    compact_optional=True,
                ),
            )
    if len(prompt) > HARD_PROMPT_LIMIT:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Structurally compact render prompt still exceeds the local "
                    "hard limit; no image-generation request was sent."
                ),
                **build_render_prompt_debug_metadata(prompt),
            },
        )
    return prompt


def generate_furnished_room_image(
    room_request_id: int,
    room_image_path: Path,
    furniture_reference_images: list[dict],
    recommended_items: list[dict],
    room_type: str,
    style: str | None,
    placement_plan=None,
    template_reference_image_path: Path | None = None,
    template_context: dict | None = None,
    template_blueprint: dict | None = None,
    room_analysis: dict | None = None,
    architectural_constraints: dict | None = None,
    hard_constraints: list[str] | None = None,
    design_intent: dict | None = None,
    render_manifest: dict | object | None = None,
) -> dict:
    if not room_image_path.exists():
        raise HTTPException(status_code=404, detail="Room image file not found.")
    if not recommended_items:
        raise HTTPException(
            status_code=400,
            detail="No selected products were provided for image generation.",
        )

    manifest = _manifest_dict(render_manifest)
    manifest_references = manifest.get("references", [])
    if manifest_references:
        if manifest_references[0].get("kind") != "room":
            raise HTTPException(status_code=422, detail="Render manifest must start with room image.")
        if any(reference.get("kind") == "template" for reference in manifest_references):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Final GPT Image input cannot contain a template image; "
                    "use the room image followed by exact product references."
                ),
            )
        if any(
            reference.get("kind") != "product"
            for reference in manifest_references[1:]
        ):
            raise HTTPException(
                status_code=422,
                detail="Every render reference after Image 1 must be a selected product.",
            )
        all_input_paths = [Path(reference["local_path"]) for reference in manifest_references]
    else:
        if template_reference_image_path is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Final GPT Image input does not accept template reference images."
                ),
            )
        all_input_paths = [room_image_path]
        all_input_paths.extend(
            Path(reference["local_image_path"])
            for reference in furniture_reference_images
        )

    template_reference_used = False
    maximum_product_references = MAX_FURNITURE_REFERENCE_IMAGES
    if len(furniture_reference_images) > maximum_product_references:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Too many furniture reference images for this render.",
                "maximum_furniture_reference_images": maximum_product_references,
                "template_reference_used": template_reference_used,
            },
        )
    if len(all_input_paths) > MAX_TOTAL_INPUT_IMAGES:
        raise HTTPException(
            status_code=422,
            detail=f"Too many total input images. Maximum allowed is {MAX_TOTAL_INPUT_IMAGES}.",
        )
    missing_paths = [str(path) for path in all_input_paths if not path.exists()]
    if missing_paths:
        raise HTTPException(
            status_code=404,
            detail={"message": "Render reference image not found.", "paths": missing_paths},
        )
    input_mime_types = [
        validated_input_image_mime_type(image_path)
        for image_path in all_input_paths
    ]

    prompt = build_generation_prompt(
        recommended_items=recommended_items,
        room_type=room_type,
        style=style,
        placement_plan=placement_plan,
        template_context=template_context,
        template_blueprint=template_blueprint,
        template_reference_used=template_reference_used,
        room_analysis=room_analysis,
        architectural_constraints=architectural_constraints,
        hard_constraints=hard_constraints,
        design_intent=design_intent,
        render_manifest=manifest,
    )
    prompt_metadata = build_render_prompt_debug_metadata(prompt)
    if prompt_metadata["render_prompt_length"] > HARD_PROMPT_LIMIT:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Render prompt exceeds the local hard limit; no request was sent.",
                **prompt_metadata,
            },
        )
    output_size = choose_output_size(room_image_path)
    client = get_openai_client()

    try:
        with ExitStack() as stack:
            image_files = [
                (
                    image_path.name,
                    stack.enter_context(open(image_path, "rb")),
                    mime_type,
                )
                for image_path, mime_type in zip(
                    all_input_paths,
                    input_mime_types,
                    strict=True,
                )
            ]
            response = client.images.edit(
                model=MODEL_NAME,
                image=image_files,
                prompt=prompt,
                quality="medium",
                size=output_size,
                output_format="png",
            )
    except HTTPException:
        raise
    except Exception as exc:
        _raise_image_service_error(
            exc,
            prompt_metadata=prompt_metadata,
            input_image_count=len(all_input_paths),
        )

    if not response.data or not response.data[0].b64_json:
        raise HTTPException(
            status_code=502,
            detail="OpenAI image generation returned no image data.",
        )
    try:
        image_bytes = base64.b64decode(response.data[0].b64_json)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to decode generated image: {exc}",
        ) from exc

    generated_filename = f"design_{room_request_id}_{uuid4().hex}.png"
    generated_image_path = GENERATED_DESIGNS_DIR / generated_filename
    generated_image_path.write_bytes(image_bytes)
    return {
        "generated_image_filename": generated_filename,
        "generated_image_path": str(generated_image_path),
        "model": MODEL_NAME,
        "input_image_count": len(all_input_paths),
        "output_size": output_size,
        "template_reference_used": template_reference_used,
        "render_signature": manifest.get("render_signature"),
        "render_prompt_version": RENDER_PROMPT_VERSION,
        **prompt_metadata,
    }
