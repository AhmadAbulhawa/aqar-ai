import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field, field_validator, model_validator

from app.database import get_connection
from app.services.room_analysis_service import (
    encode_image_as_data_url,
    is_external_ai_connection_error,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

TEMPLATE_BLUEPRINT_MODEL = "gpt-5.6-sol"
TEMPLATE_BLUEPRINT_SCHEMA_VERSION = "2"
TEMPLATE_BLUEPRINT_PROMPT_VERSION = "2026-07-14.2"

SUPPORTED_TEMPLATE_CATEGORIES = {
    "sofa",
    "armchair",
    "coffee_table",
    "side_table",
    "tv_stand",
    "television",
    "sideboard",
    "rug",
    "floor_lamp",
    "wall_art",
    "wall_shelf",
    "bookshelf",
    "storage_cabinet",
    "dining_chair",
    "dining_table",
    "office_chair",
    "desk",
    "bed",
    "wardrobe",
    "nightstand",
    "dresser",
    "mirror",
}


class TemplateProductSlot(BaseModel):
    slot_key: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    quantity: int = Field(ge=1, le=6)
    importance: Literal["essential", "supporting", "optional"]
    visual_role: str = Field(min_length=1, max_length=300)
    placement_zone: str = Field(min_length=1, max_length=300)
    relationships: list[str] = Field(default_factory=list, max_length=8)
    preferred_colors: list[str] = Field(default_factory=list, max_length=8)
    preferred_materials: list[str] = Field(default_factory=list, max_length=8)
    repeat_group_id: str | None = Field(default=None, max_length=80)
    same_product_required: bool = False
    layer_group_id: str | None = Field(default=None, max_length=80)

class TemplateBlueprint(BaseModel):
    layout_type: str = Field(min_length=1, max_length=100)
    focal_point: str = Field(min_length=1, max_length=100)
    density_target: Literal["minimal", "balanced", "layered"]
    composition_summary: str = Field(min_length=1, max_length=1200)
    visual_anchor_summary: str = Field(min_length=1, max_length=800)
    product_slots: list[TemplateProductSlot] = Field(
        default_factory=list,
        max_length=30,
    )
    key_visual_rules: list[str] = Field(default_factory=list, max_length=15)
    functional_additions_allowed: list[str] = Field(
        default_factory=list,
        max_length=12,
    )

    @field_validator("functional_additions_allowed")
    @classmethod
    def validate_functional_additions(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()

        for raw_value in values:
            value = str(raw_value).strip().lower()
            if value not in SUPPORTED_TEMPLATE_CATEGORIES:
                continue
            if value in seen:
                continue
            seen.add(value)
            normalized.append(value)

        return normalized

    @model_validator(mode="after")
    def validate_slot_contract(self):
        slot_keys = [slot.slot_key for slot in self.product_slots]
        if len(slot_keys) != len(set(slot_keys)):
            raise ValueError("Template blueprint slot_key values must be unique.")

        repeat_groups: dict[str, list[TemplateProductSlot]] = {}
        for slot in self.product_slots:
            if slot.repeat_group_id:
                repeat_groups.setdefault(slot.repeat_group_id, []).append(slot)

        for group_id, slots in repeat_groups.items():
            if any(slot.same_product_required for slot in slots) and len(slots) < 2:
                raise ValueError(
                    f"Repeat group '{group_id}' requires at least two slots."
                )
        return self


def normalize_template_blueprint(
    blueprint: TemplateBlueprint,
) -> TemplateBlueprint:
    """Upgrade deterministic legacy layout semantics into structured slots.

    Older cached facing-sofa blueprints predate repeat_group_id. The layout
    type and two explicit sofa slots are stable structured evidence; no prose
    matching is used.
    """

    slots = list(blueprint.product_slots)
    if blueprint.layout_type == "facing_sofas_conversation":
        sofa_indexes = [
            index
            for index, slot in enumerate(slots)
            if slot.category == "sofa"
        ]
        grouped = any(slots[index].repeat_group_id for index in sofa_indexes)
        if len(sofa_indexes) == 2 and not grouped:
            for index in sofa_indexes:
                slots[index] = slots[index].model_copy(
                    update={
                        "repeat_group_id": "main_facing_sofas",
                        "same_product_required": True,
                    }
                )

    return blueprint.model_copy(update={"product_slots": slots})


def ensure_template_blueprint_table() -> None:
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS design_template_blueprints (
                template_id INTEGER PRIMARY KEY,
                image_signature TEXT NOT NULL,
                blueprint_json TEXT NOT NULL,
                model_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (template_id)
                    REFERENCES design_templates(id)
                    ON DELETE CASCADE
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


def build_template_image_signature(image_path: Path) -> str:
    if not image_path.exists():
        raise FileNotFoundError(
            f"Template preview image not found: {image_path}"
        )

    digest = hashlib.sha256()

    with image_path.open("rb") as image_file:
        while True:
            chunk = image_file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)

    return digest.hexdigest()


def build_template_blueprint_signature(image_path: Path) -> str:
    payload = {
        "image_sha256": build_template_image_signature(image_path),
        "model": TEMPLATE_BLUEPRINT_MODEL,
        "prompt_version": TEMPLATE_BLUEPRINT_PROMPT_VERSION,
        "schema_version": TEMPLATE_BLUEPRINT_SCHEMA_VERSION,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def get_saved_template_blueprint(
    template_id: int,
    image_signature: str,
) -> TemplateBlueprint | None:
    ensure_template_blueprint_table()

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT blueprint_json
            FROM design_template_blueprints
            WHERE template_id = ?
              AND image_signature = ?
            """,
            (template_id, image_signature),
        )
        row = cursor.fetchone()
    finally:
        connection.close()

    if row is None:
        return None

    try:
        return normalize_template_blueprint(
            TemplateBlueprint.model_validate_json(row["blueprint_json"])
        )
    except Exception:
        return None


def save_template_blueprint(
    template_id: int,
    image_signature: str,
    blueprint: TemplateBlueprint,
) -> None:
    ensure_template_blueprint_table()

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO design_template_blueprints (
                template_id,
                image_signature,
                blueprint_json,
                model_name
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(template_id)
            DO UPDATE SET
                image_signature = excluded.image_signature,
                blueprint_json = excluded.blueprint_json,
                model_name = excluded.model_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                template_id,
                image_signature,
                blueprint.model_dump_json(),
                TEMPLATE_BLUEPRINT_MODEL,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail=(
                "OPENAI_API_KEY is not set. "
                "Add it to the root .env file."
            ),
        )

    return OpenAI(api_key=api_key)


def analyze_template_blueprint(
    image_path: Path,
    template: dict,
) -> TemplateBlueprint:
    if not image_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Selected template preview image was not found.",
        )

    client = get_openai_client()
    image_data_url = encode_image_as_data_url(image_path)

    prompt = f"""
You are the visual-blueprint analyzer for Aqar AI.

Analyze the supplied READY-MADE INTERIOR DESIGN IMAGE so another AI can recreate
THE SAME DESIGN COMPOSITION inside a different real user room.

Template metadata:
{json.dumps({
    "id": template.get("id"),
    "name": template.get("name"),
    "room_type": template.get("room_type"),
    "style": template.get("style"),
    "layout_type": template.get("layout_type"),
    "focal_point": template.get("focal_point"),
    "density_target": template.get("density_target"),
    "description": template.get("description"),
    "tags": template.get("tags", []),
}, ensure_ascii=False, indent=2)}

GOAL

Create a stable visual blueprint that preserves:

- the visible layout family
- major furniture relationships
- focal hierarchy
- seating pattern
- major zones
- density
- visual balance
- key negative-space pattern

MAJOR PRODUCT SLOT RULES

Create product_slots only for visible or compositionally necessary MAJOR room
products from these supported categories:

{json.dumps(sorted(SUPPORTED_TEMPLATE_CATEGORIES), ensure_ascii=False)}

Do NOT create slots for:

- books
- bowls
- trays
- tiny decor
- loose cushions
- throws
- plants
- candles
- glasses
- dishes
- tiny tabletop accessories

For each slot:

- use the exact visible category
- use the actual visible quantity
- mark importance as essential when removing it would materially change the
  template composition
- describe where it sits and its relationships to other slots
- do not guess exact IKEA SKU IDs
- do not invent hidden products
- when two or more physical slots must use the same product model, give every
  slot the same repeat_group_id and set same_product_required=true
- use layer_group_id only when multiple distinct visible layers are truly
  required (for example two intentionally layered rugs); never infer layers
  from the source product-list length

FUNCTIONAL ADDITIONS

functional_additions_allowed may contain a SMALL list of major categories that
could be added from the real Aqar AI database when useful for room function,
WITHOUT changing the selected template's composition. Example: television plus
tv_stand in a suitable living-room layout. Do not force additions.

CRITICAL

The selected ready-design image is the visual source of truth. The purpose is
not to create a generic room plan. The purpose is to reproduce the same design
logic in another room as closely as architecture and physical fit allow.
""".strip()

    try:
        response = client.responses.parse(
            model=TEMPLATE_BLUEPRINT_MODEL,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": prompt,
                        },
                        {
                            "type": "input_image",
                            "image_url": image_data_url,
                        },
                    ],
                }
            ],
            text_format=TemplateBlueprint,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Template blueprint analysis failed: "
                f"{exc}"
            ),
        ) from exc

    blueprint = response.output_parsed

    if blueprint is None:
        raise HTTPException(
            status_code=502,
            detail="Template blueprint model returned no parsed result.",
        )

    # Keep only categories that the current MVP can map to real DB products.
    # Unsupported decorative objects must not break blueprint parsing.
    sanitized_slots = []
    for slot in blueprint.product_slots:
        category = str(slot.category).strip().lower()
        if category not in SUPPORTED_TEMPLATE_CATEGORIES:
            continue
        sanitized_slots.append(
            slot.model_copy(update={"category": category})
        )

    if not sanitized_slots:
        raise HTTPException(
            status_code=502,
            detail=(
                "Template blueprint analysis returned no supported major "
                "product slots for the current MVP."
            ),
        )

    blueprint = normalize_template_blueprint(blueprint.model_copy(
        update={"product_slots": sanitized_slots}
    ))

    return blueprint


def build_catalog_template_blueprint(template: dict) -> TemplateBlueprint:
    """Build a conservative blueprint from real persisted template slots.

    This is a transport-failure fallback only. It never invents products and
    keeps every slot tied to design_template_products for the chosen template.
    """

    connection = get_connection()
    cursor = connection.cursor()
    try:
        rows = cursor.execute(
            """
            SELECT id, category, quantity, role, priority,
                   preferred_style, preferred_colors, exact_product_id
            FROM design_template_products
            WHERE template_id = ?
            ORDER BY id ASC
            """,
            (int(template["id"]),),
        ).fetchall()
    finally:
        connection.close()

    if not rows:
        raise HTTPException(
            status_code=502,
            detail=(
                "Template blueprint analysis is unreachable and the selected "
                "template has no persisted product slots for a safe fallback."
            ),
        )

    primary_categories = {"sofa", "bed", "dining_table", "desk"}
    product_slots = []
    for index, row in enumerate(rows, start=1):
        item = dict(row)
        category = str(item["category"]).strip().lower()
        if category not in SUPPORTED_TEMPLATE_CATEGORIES:
            continue
        try:
            colors = json.loads(item.get("preferred_colors") or "[]")
        except (json.JSONDecodeError, TypeError):
            colors = []
        product_slots.append(
            TemplateProductSlot(
                slot_key=f"catalog_template_{int(template['id'])}_{int(item['id'])}_{index}",
                category=category,
                quantity=max(1, min(int(item.get("quantity") or 1), 6)),
                importance=(
                    "essential" if category in primary_categories else "supporting"
                ),
                visual_role=f"Persisted {category.replace('_', ' ')} template product",
                placement_zone="Adapt the selected template's product relationship to the measured room.",
                preferred_colors=[str(value) for value in colors if str(value).strip()],
            )
        )

    if not product_slots:
        raise HTTPException(
            status_code=502,
            detail="The selected template has no supported persisted product slots.",
        )

    density = str(template.get("density_target") or "balanced")
    if density not in {"minimal", "balanced", "layered"}:
        density = "balanced"
    return TemplateBlueprint(
        layout_type=str(template.get("layout_type") or "template_adaptation"),
        focal_point=str(template.get("focal_point") or "template focal composition"),
        density_target=density,
        composition_summary=str(
            template.get("description")
            or "Preserve the selected template's persisted product composition."
        ),
        visual_anchor_summary=(
            f"Use the selected template '{template.get('name')}' as the visual reference."
        ),
        product_slots=product_slots,
        key_visual_rules=[
            "Preserve the selected template identity while adapting only to measured room constraints.",
            "Do not infer additional visual objects while external blueprint analysis is unavailable.",
        ],
        functional_additions_allowed=[],
    )


def get_or_create_template_blueprint(
    template: dict,
    image_path: Path,
) -> tuple[TemplateBlueprint, bool, str]:
    template_id = int(template["id"])
    raw_image_signature = build_template_image_signature(image_path)
    blueprint_signature = build_template_blueprint_signature(image_path)

    cached = get_saved_template_blueprint(
        template_id=template_id,
        image_signature=blueprint_signature,
    )

    if cached is not None:
        return cached, True, blueprint_signature

    # One-time, no-cost upgrade for pre-versioned cached blueprints.
    legacy_cached = get_saved_template_blueprint(
        template_id=template_id,
        image_signature=raw_image_signature,
    )
    if legacy_cached is not None:
        save_template_blueprint(
            template_id=template_id,
            image_signature=blueprint_signature,
            blueprint=legacy_cached,
        )
        return legacy_cached, True, blueprint_signature

    try:
        blueprint = analyze_template_blueprint(
            image_path=image_path,
            template=template,
        )
    except HTTPException as error:
        if not is_external_ai_connection_error(error):
            raise
        blueprint = build_catalog_template_blueprint(template)

    save_template_blueprint(
        template_id=template_id,
        image_signature=blueprint_signature,
        blueprint=blueprint,
    )

    return blueprint, False, blueprint_signature
