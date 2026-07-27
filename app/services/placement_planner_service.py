"""Deprecated compatibility-only semantic placement planner.

No Template, Custom, Auto, recommend, apply, prepare, or render route imports
or calls this service. It remains temporarily for old cached rows, explicit
legacy tooling, and regression coverage. GPT Image 2 is the active visual
designer for all new selections and renders.
"""

import hashlib
import json
import os

from pathlib import Path

from dotenv import load_dotenv
from fastapi import HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field

from app.database import get_connection
from app.services.room_analysis_service import (
    RoomAnalysis,
    encode_image_as_data_url
)
from app.services.design_director_service import DesignStrategy
from app.services.architectural_constraint_service import (
    ARCHITECTURAL_CONSTRAINT_VERSION,
    GeometryConfidence,
    SpatialRect,
    build_architectural_constraint_map,
)
from app.services.layout_quality_service import (
    LAYOUT_QUALITY_VERSION,
    LayoutQualityResult,
    LayoutQualityStatus,
    layout_requires_replan,
    validate_layout_quality,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(
    PROJECT_ROOT / ".env"
)


PLACEMENT_PLANNER_MODEL = "gpt-5.6-sol"
PLACEMENT_PLANNER_SCHEMA_VERSION = "4"
PLACEMENT_PLANNER_PROMPT_VERSION = "2026-07-15.5"
PLACEMENT_PLANNER_ACTIVE_IN_MAIN_FLOWS = False

# =========================================================
# Structured output schemas
# =========================================================

class ProductPlacement(BaseModel):
    product_id: int = Field(
        gt=0,
        description=(
            "Exact selected database product ID."
        )
    )

    unit_index: int = Field(
        ge=1,
        description=(
            "1-based index for this physical unit when quantity > 1."
        )
    )

    slot_key: str | None = Field(default=None, max_length=80)
    repeat_group_id: str | None = Field(default=None, max_length=80)

    category: str = Field(
        min_length=1
    )

    position: str = Field(
        min_length=1,
        description=(
            "Clear physical placement inside the visible room."
        )
    )

    orientation: str = Field(
        min_length=1,
        description=(
            "Direction the product should face or align toward."
        )
    )

    anchor: str = Field(
        min_length=1,
        description=(
            "Wall, corner, focal point, or furniture zone used as anchor."
        )
    )

    relationships: list[str] = Field(
        default_factory=list,
        description=(
            "Important spatial relationships to other selected products."
        )
    )

    clearance_notes: list[str] = Field(
        default_factory=list,
        description=(
            "Door, window, walkway, spacing, or circulation constraints."
        )
    )

    geometry_confidence: GeometryConfidence = Field(
        default=GeometryConfidence.UNCERTAIN,
        description=(
            "KNOWN only for supplied measured coordinates, ESTIMATED for a "
            "defensible room-relative estimate, otherwise UNCERTAIN."
        ),
    )

    footprint: SpatialRect | None = Field(
        default=None,
        description=(
            "Optional room-relative floor footprint in centimetres. Never "
            "invent it when the image/dimensions do not support an estimate."
        ),
    )

    related_product_ids: list[int] = Field(default_factory=list)
    faces_product_id: int | None = Field(default=None, gt=0)
    nearest_clearance_cm: float | None = Field(default=None, ge=0)


class PlacementPlanResponse(BaseModel):
    """Strict model-generated portion of a placement plan.

    Backend-computed diagnostics intentionally do not belong to the OpenAI
    response schema. Open-ended ``dict`` fields generate
    ``additionalProperties: true``, which Structured Outputs rejects.
    """

    layout_strategy: str = Field(
        min_length=1
    )

    focal_point: str = Field(
        min_length=1
    )

    circulation_strategy: str = Field(
        min_length=1
    )

    placements: list[ProductPlacement] = Field(
        min_length=1
    )

    global_instructions: list[str] = Field(
        default_factory=list
    )

    design_rationale: str = Field(
        min_length=1
    )


class PlacementPlan(PlacementPlanResponse):
    """Complete plan after deterministic backend validation/enrichment."""

    architectural_constraint_map: dict | None = None
    layout_quality: dict | None = None


# =========================================================
# OpenAI client
# =========================================================

def get_openai_client() -> OpenAI:
    api_key = os.getenv(
        "OPENAI_API_KEY"
    )

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail=(
                "OPENAI_API_KEY is not set. "
                "Add it to the root .env file."
            )
        )

    return OpenAI(
        api_key=api_key
    )


# =========================================================
# Selected product metadata
# =========================================================

def prepare_selected_products_for_planner(
    selected_items: list[dict]
) -> list[dict]:

    prepared_items = []

    for item in selected_items:
        prepared_items.append({
            "id": int(item["id"]),
            "name": item["name"],
            "category": item["category"],
            "style": item.get("style"),
            "color": item.get("color"),
            "material": item.get("material"),
            "width_cm": item.get("width_cm"),
            "depth_cm": item.get("depth_cm"),
            "height_cm": item.get("height_cm"),
            "quantity": int(
                item.get(
                    "quantity",
                    1
                )
            ),
            "slot_keys": list(item.get("slot_keys") or []),
            "slot_assignments": list(item.get("slot_assignments") or []),
            "repeat_group_id": item.get("repeat_group_id"),
        })

    return prepared_items


# =========================================================
# Stable cache signature
# =========================================================

def build_selection_signature(
    selected_items: list[dict],
    room_analysis: RoomAnalysis,
    room_type: str,
    style: str | None,
    design_strategy: DesignStrategy | None = None,
    room_image_path: Path | None = None,
    template_blueprint: dict | None = None,
    template_id: int | None = None,
    template_signature: str | None = None,
    composition_validation: dict | None = None,
    immutable_selection_signature: str | None = None,
) -> str:

    selected_products = sorted(
        [
            {
                "id": int(item["id"]),
                "quantity": int(
                    item.get(
                        "quantity",
                        1
                    )
                ),
                "category": item.get("category"),
                "style": item.get("style"),
                "color": item.get("color"),
                "material": item.get("material"),
                "width_cm": item.get("width_cm"),
                "depth_cm": item.get("depth_cm"),
                "height_cm": item.get("height_cm"),
                "image_filename": item.get("image_filename"),
                "slot_keys": list(item.get("slot_keys") or []),
                "repeat_group_id": item.get("repeat_group_id"),
            }
            for item in selected_items
        ],
        key=lambda item: item["id"]
    )

    room_image_signature = None
    if room_image_path is not None:
        digest = hashlib.sha256()
        with room_image_path.open("rb") as image_file:
            for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
                digest.update(chunk)
        room_image_signature = digest.hexdigest()

    payload = {
        "model": PLACEMENT_PLANNER_MODEL,
        "prompt_version": PLACEMENT_PLANNER_PROMPT_VERSION,
        "schema_version": PLACEMENT_PLANNER_SCHEMA_VERSION,
        "room_image_signature": room_image_signature,
        "selected_products": selected_products,
        "room_analysis": room_analysis.model_dump(),
        "room_type": room_type,
        "style": style,
        "design_strategy": (
            design_strategy.model_dump()
            if design_strategy is not None
            else None
        ),
        "template_blueprint": template_blueprint,
        "template_id": template_id,
        "template_signature": template_signature,
        "composition_validation": composition_validation,
        "immutable_selection_signature": immutable_selection_signature,
        "architectural_constraint_version": ARCHITECTURAL_CONSTRAINT_VERSION,
        "layout_quality_version": LAYOUT_QUALITY_VERSION,
    }

    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":")
    )

    return hashlib.sha256(
        canonical_json.encode("utf-8")
    ).hexdigest()


# =========================================================
# Placement-plan cache table
# =========================================================

def ensure_placement_plans_table() -> None:
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS placement_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_request_id INTEGER NOT NULL,
                selection_signature TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                model_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (
                    room_request_id,
                    selection_signature
                ),
                FOREIGN KEY (room_request_id)
                    REFERENCES room_requests(id)
                    ON DELETE CASCADE
            )
            """
        )

        connection.commit()

    finally:
        connection.close()


# =========================================================
# Validate exact physical units
# =========================================================

def validate_placement_plan(
    plan: PlacementPlan,
    selected_items: list[dict]
) -> PlacementPlan:

    selected_by_id = {
        int(item["id"]): item
        for item in selected_items
    }

    expected_units = set()

    for item in selected_items:
        product_id = int(
            item["id"]
        )

        quantity = int(
            item.get(
                "quantity",
                1
            )
        )

        for unit_index in range(
            1,
            quantity + 1
        ):
            expected_units.add((
                product_id,
                unit_index
            ))

    actual_units = []
    category_mismatches = []
    slot_mismatches = []
    repeat_group_mismatches = []

    for placement in plan.placements:
        product_id = int(
            placement.product_id
        )

        unit_index = int(
            placement.unit_index
        )

        actual_units.append((
            product_id,
            unit_index
        ))

        selected_item = selected_by_id.get(
            product_id
        )

        if selected_item is None:
            continue

        expected_category = str(
            selected_item["category"]
        )

        if (
            placement.category
            != expected_category
        ):
            category_mismatches.append({
                "product_id": product_id,
                "expected_category": expected_category,
                "returned_category": placement.category
            })

        expected_slot_keys = list(selected_item.get("slot_keys") or [])
        if expected_slot_keys:
            expected_slot_key = (
                expected_slot_keys[unit_index - 1]
                if unit_index <= len(expected_slot_keys)
                else None
            )
            if placement.slot_key != expected_slot_key:
                slot_mismatches.append({
                    "product_id": product_id,
                    "unit_index": unit_index,
                    "expected_slot_key": expected_slot_key,
                    "returned_slot_key": placement.slot_key,
                })
        expected_repeat_group = selected_item.get("repeat_group_id")
        if expected_repeat_group and placement.repeat_group_id != expected_repeat_group:
            repeat_group_mismatches.append({
                "product_id": product_id,
                "unit_index": unit_index,
                "expected_repeat_group_id": expected_repeat_group,
                "returned_repeat_group_id": placement.repeat_group_id,
            })

    duplicate_units = sorted({
        unit
        for unit in actual_units
        if actual_units.count(unit) > 1
    })

    actual_unit_set = set(
        actual_units
    )

    missing_units = sorted(
        expected_units
        - actual_unit_set
    )

    extra_units = sorted(
        actual_unit_set
        - expected_units
    )

    if (
        duplicate_units
        or missing_units
        or extra_units
        or category_mismatches
        or slot_mismatches
        or repeat_group_mismatches
    ):
        raise HTTPException(
            status_code=502,
            detail={
                "message": (
                    "Placement planner failed exact-product "
                    "or exact-quantity validation."
                ),
                "missing_units": [
                    {
                        "product_id": product_id,
                        "unit_index": unit_index
                    }
                    for product_id, unit_index
                    in missing_units
                ],
                "extra_units": [
                    {
                        "product_id": product_id,
                        "unit_index": unit_index
                    }
                    for product_id, unit_index
                    in extra_units
                ],
                "duplicate_units": [
                    {
                        "product_id": product_id,
                        "unit_index": unit_index
                    }
                    for product_id, unit_index
                    in duplicate_units
                ],
                "category_mismatches": (
                    category_mismatches
                ),
                "slot_mismatches": slot_mismatches,
                "repeat_group_mismatches": repeat_group_mismatches,
            }
        )

    return plan


# =========================================================
# AI placement planning
# =========================================================

def create_placement_plan(
    room_image_path: Path,
    room_type: str,
    style: str | None,
    room_analysis: RoomAnalysis,
    selected_items: list[dict],
    design_strategy: DesignStrategy | None = None,
    template_blueprint: dict | None = None,
    template_id: int | None = None,
    template_signature: str | None = None,
    composition_validation: dict | None = None,
    architectural_constraints: dict | None = None,
    revision_context: dict | None = None,
) -> PlacementPlan:

    if not selected_items:
        raise HTTPException(
            status_code=400,
            detail=(
                "No selected products were provided "
                "to the placement planner."
            )
        )

    client = get_openai_client()

    image_data_url = (
        encode_image_as_data_url(
            room_image_path
        )
    )

    selected_products = (
        prepare_selected_products_for_planner(
            selected_items
        )
    )

    design_strategy_text = ""

    if design_strategy is not None:
        design_strategy_text = f"""
============================================================
DESIGN DIRECTOR STRATEGY — MUST EXECUTE
============================================================

{json.dumps(
    design_strategy.model_dump(),
    ensure_ascii=False,
    indent=2
)}

IMPORTANT:

- Do not re-decide the high-level design concept.
- Execute the supplied layout strategy, focal point, density, symmetry,
  seating composition, and lighting intent using the exact selected products.
- You may adapt exact positions only when required by visible geometry,
  obstacles, product dimensions, or circulation safety.
- If a spatial adaptation is necessary, preserve the strategy's intent and
  explain the adaptation in design_rationale.
""".strip()

    template_contract_text = ""
    if template_blueprint is not None:
        template_contract_text = f"""
============================================================
AUTHORITATIVE TEMPLATE BLUEPRINT AND SLOT CONTRACT
============================================================
Template ID: {template_id}
Template image signature: {template_signature}
{json.dumps(template_blueprint, ensure_ascii=False, indent=2)}

WHOLE-COMPOSITION FIT RESULT
{json.dumps(composition_validation, ensure_ascii=False, indent=2)}

Every selected product unit includes its assigned slot_key. Return that exact
slot_key on its ProductPlacement. A repeat_group_id means those physical slots
belong to one repeated-model composition; do not split or substitute it.
""".strip()

    dimensions = room_analysis.provided_dimensions_cm
    coordinate_contract_text = ""
    if dimensions is not None:
        coordinate_contract_text = f"""
============================================================
MEASURED FLOOR COORDINATE CONTRACT — HARD LIMITS
============================================================

- Origin (0, 0): near-left room corner.
- x axis: left-to-right across room width_cm={dimensions.width_cm}.
- y axis: near-to-far across room length_cm={dimensions.length_cm}.
- Every footprint must satisfy 0 <= x_cm and
  x_cm + footprint.width_cm <= {dimensions.width_cm}.
- Every footprint must satisfy 0 <= y_cm and
  y_cm + footprint.depth_cm <= {dimensions.length_cm}.
- Product footprint width/depth may be swapped only when the product is
  intentionally rotated 90 degrees and the orientation says so.
""".strip()

    revision_contract_text = ""
    if revision_context is not None:
        revision_contract_text = f"""
============================================================
CORRECTIVE PASS — REPLACE THE INVALID DRAFT
============================================================

The previous complete draft and deterministic validator findings are below:
{json.dumps(revision_context, ensure_ascii=False, indent=2)}

Return a complete replacement plan for every exact unit. Reposition products
to resolve every listed violation; do not remove, add, substitute, or merge
products. Preserve committed/template inventory and slot assignments. Before
returning, recheck the measured floor limits, physical overlap, coffee-table
edge clearance, storage access, openings, and circulation.
""".strip()

    prompt = f"""
You are the interior-space placement planner for Aqar AI.

Your job is NOT to select products.
Your job is NOT to add products.
Your job is NOT to remove products.

Your only job is to create a professional, practical placement plan for
EVERY exact physical product unit already selected by the backend.

============================================================
ROOM
============================================================

Room type:
{room_type}

Requested style:
{style}

Room analysis:
{json.dumps(
    room_analysis.model_dump(),
    ensure_ascii=False,
    indent=2
)}

ARCHITECTURAL CONSTRAINT MAP
{json.dumps(architectural_constraints, ensure_ascii=False, indent=2)}

The constraint map is deterministic and confidence-labelled. Bounds marked
KNOWN are hard geometry. ESTIMATED/UNCERTAIN descriptions require conservative
clearance and must never be described as exact.

{design_strategy_text}

{template_contract_text}

{coordinate_contract_text}

{revision_contract_text}

============================================================
EXACT SELECTED PRODUCTS
============================================================

{json.dumps(
    selected_products,
    ensure_ascii=False,
    indent=2
)}

============================================================
HARD RULES
============================================================

1. Use ONLY the exact selected product IDs above.

2. Do NOT invent, add, remove, replace, or merge products.

3. Create exactly one placement entry for every physical unit.

4. When quantity is greater than 1, create separate placement entries
   using unit_index values 1, 2, 3, and so on.

Example:

If product ID 1 has quantity 2, return exactly:

- product_id 1, unit_index 1
- product_id 1, unit_index 2

5. Do not block:

   - doors
   - windows
   - openings
   - circulation paths
   - entry routes

6. Use the uploaded room image as the visual source of truth for geometry,
   perspective, walls, openings, obstacles, and available floor space.

7. Do not assume exact room measurements that are not visible or supplied.

8. Respect the real product dimensions supplied by the backend.

9. The layout must look like a professional interior designer planned it,
   not like furniture was randomly placed.

10. Avoid both overcrowding and under-furnishing.

11. Keep natural walking clearances between major furniture pieces.

12. Create a clear focal point and a coherent conversation or functional zone.

13. When a Design Director strategy is supplied, the plan's layout_strategy
    and focal_point must implement that strategy rather than inventing a new
    concept.

14. Apply professional visual-weight, rhythm, grouping, depth, and balance
    heuristics while preserving exact selected products and quantities.

15. When a slot_key is supplied, copy it exactly into that unit's placement
    and execute the slot's placement zone, visual role, and relationships.

16. When measured room length and width plus visible geometry support a
    defensible room-relative estimate, return a footprint using the coordinate
    contract above and set geometry_confidence=ESTIMATED. Use KNOWN only for
    coordinates explicitly supplied by the room contract. Otherwise leave
    footprint=null and set geometry_confidence=UNCERTAIN. Never invent precision.

17. Populate related_product_ids and faces_product_id for functional
    relationships. Seating that views a television must explicitly identify
    the television product ID. Side tables must identify nearby seating.

18. Treat door location or swing uncertainty conservatively: retain a larger
    clear entry envelope and state that uncertainty in clearance_notes.

19. Keep a coffee table approximately 35-50 cm from its nearest seating edge;
    never return a measured edge gap below 30 cm. Reposition the seating group
    when a committed table cannot otherwise meet this constraint.

20. Give storage doors/drawers at least 45 cm of usable front approach space.
    Do not report nearest_clearance_cm below 45; reposition the storage and
    surrounding products until the reported value is geometrically credible.

21. Before returning, verify every footprint against the measured x/y limits
    and verify every numeric clearance against the returned footprints.

22. Treat the seating, coffee table, and primary rug as one relational layer:
    center the table within the rug; place the rug under the front zone of the
    main seating; keep the rug larger than the table on both floor axes; and
    preserve a credible rug scale relative to the primary sofa. Exact selected
    dimensions are authoritative—never enlarge or shrink a product footprint.

23. Major table/seating, rug/table, rug/seating, or symmetry alignment drift is
    replan-required. Resolve it before returning rather than merely describing
    the drift in design_rationale.

============================================================
LIVING ROOM RULES
============================================================

When room_type is living_room:

- Sofas and armchairs should form a coherent conversation zone.
- Two sofas should not automatically be placed side-by-side.
- Prefer an L-shaped, facing, or perpendicular arrangement when that better
  fits the visible geometry and keeps circulation open.
- The coffee table should belong to the main seating zone.
- The rug should visually anchor the seating group.
- A television and tv_stand must function as one media pair.
- The TV should face the main seating position whenever physically possible.
- The tv_stand should sit on a suitable solid wall and must not block openings.
- Side tables should be reachable from nearby seating.
- Floor lamps should occupy useful corners or seating-adjacent positions
  without blocking circulation.
- When a TV and TV stand occupy a media wall, do not place the return or
  chaise section of a corner sofa along that same wall if it would overlap,
  crowd, or compete with the media zone.
- Keep the media wall physically and visually clear. Prefer the corner sofa's
  long side along a suitable side wall, with the return extending toward the
  center or foreground when that preserves circulation.
- Wall art should not automatically be placed above or around the TV. Prefer
  above seating or on another clear wall section when that produces a cleaner
  composition.

============================================================
OUTPUT QUALITY
============================================================

For every placement, be specific enough that an image-generation model can
follow the plan.

Good position example:
"Against the uninterrupted right wall, centered in the rear half of the room."

Bad position example:
"Somewhere on the right."

Good orientation example:
"Facing inward toward the coffee table and the opposite sofa."

Bad orientation example:
"Facing forward."

The final plan must be physically plausible, balanced, visually coherent,
and faithful to the exact selected products and exact quantities.
""".strip()

    try:
        response = client.responses.parse(
            model=PLACEMENT_PLANNER_MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict professional interior layout planner. "
                        "You may place only the exact products already selected "
                        "by the backend, with exact quantities."
                    )
                },
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
            text_format=PlacementPlanResponse
        )

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "AI placement planning failed: "
                f"{exc}"
            )
        ) from exc

    parsed_plan = response.output_parsed

    if parsed_plan is None:
        raise HTTPException(
            status_code=502,
            detail=(
                "Placement planner did not return "
                "a valid structured response."
            )
        )

    plan = PlacementPlan.model_validate(
        parsed_plan.model_dump(mode="python")
    )

    return validate_placement_plan(
        plan=plan,
        selected_items=selected_items
    )


# =========================================================
# Cache read / write
# =========================================================

def get_saved_placement_plan(
    room_request_id: int,
    selection_signature: str,
    selected_items: list[dict]
) -> PlacementPlan | None:

    ensure_placement_plans_table()

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT plan_json
            FROM placement_plans
            WHERE room_request_id = ?
              AND selection_signature = ?
            """,
            (
                room_request_id,
                selection_signature
            )
        )

        row = cursor.fetchone()

    finally:
        connection.close()

    if row is None:
        return None

    try:
        plan_json = row["plan_json"]
    except (
        TypeError,
        KeyError,
        IndexError
    ):
        plan_json = row[0]

    try:
        plan_data = json.loads(
            plan_json
        )

        plan = PlacementPlan.model_validate(
            plan_data
        )

        return validate_placement_plan(
            plan=plan,
            selected_items=selected_items
        )

    except Exception:
        return None


def save_placement_plan(
    room_request_id: int,
    selection_signature: str,
    plan: PlacementPlan
) -> None:

    ensure_placement_plans_table()

    connection = get_connection()
    cursor = connection.cursor()

    plan_json = json.dumps(
        plan.model_dump(),
        ensure_ascii=False
    )

    try:
        cursor.execute(
            """
            INSERT INTO placement_plans (
                room_request_id,
                selection_signature,
                plan_json,
                model_name
            )
            VALUES (?, ?, ?, ?)

            ON CONFLICT(
                room_request_id,
                selection_signature
            )
            DO UPDATE SET
                plan_json = excluded.plan_json,
                model_name = excluded.model_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                room_request_id,
                selection_signature,
                plan_json,
                PLACEMENT_PLANNER_MODEL
            )
        )

        connection.commit()

    finally:
        connection.close()


# =========================================================
# Public cached planner entry point
# =========================================================

def get_or_create_placement_plan(
    room_request_id: int,
    room_image_path: Path,
    room_type: str,
    style: str | None,
    room_analysis: RoomAnalysis,
    selected_items: list[dict],
    design_strategy: DesignStrategy | None = None,
    template_blueprint: dict | None = None,
    template_id: int | None = None,
    template_signature: str | None = None,
    composition_validation: dict | None = None,
    immutable_selection_signature: str | None = None,
) -> tuple[
    PlacementPlan,
    bool,
    str
]:
    """
    Return:

    - placement_plan
    - placement_plan_cached
    - selection_signature
    """

    selection_signature = (
        build_selection_signature(
            selected_items=
                selected_items,

            room_analysis=
                room_analysis,

            room_type=
                room_type,

            style=
                style,

            design_strategy=
                design_strategy,

            room_image_path=room_image_path,
            template_blueprint=template_blueprint,
            template_id=template_id,
            template_signature=template_signature,
            composition_validation=composition_validation,
            immutable_selection_signature=immutable_selection_signature,
        )
    )

    saved_plan = (
        get_saved_placement_plan(
            room_request_id=
                room_request_id,

            selection_signature=
                selection_signature,

            selected_items=
                selected_items
        )
    )

    if saved_plan is not None and saved_plan.layout_quality is not None:
        cached_quality = LayoutQualityResult.model_validate(
            saved_plan.layout_quality
        )
        if (
            cached_quality.status != LayoutQualityStatus.INVALID
            and not layout_requires_replan(cached_quality)
        ):
            return (
                saved_plan,
                True,
                selection_signature
            )

    architectural_constraints = build_architectural_constraint_map(
        room_analysis=room_analysis,
        room_dimensions=room_analysis.provided_dimensions_cm,
    )

    plan = create_placement_plan(
        room_image_path=
            room_image_path,

        room_type=
            room_type,

        style=
            style,

        room_analysis=
            room_analysis,

        selected_items=
            selected_items,

        design_strategy=
            design_strategy,

        template_blueprint=template_blueprint,
        template_id=template_id,
        template_signature=template_signature,
        composition_validation=composition_validation,
        architectural_constraints=architectural_constraints.model_dump(
            mode="json"
        ),
    )
    layout_quality = validate_layout_quality(
        plan=plan,
        selected_items=selected_items,
        architectural_constraints=architectural_constraints,
        room_dimensions=room_analysis.provided_dimensions_cm,
        composition_validation=composition_validation,
    )

    if (
        layout_quality.status == LayoutQualityStatus.INVALID
        or layout_requires_replan(layout_quality)
    ):
        plan = create_placement_plan(
            room_image_path=room_image_path,
            room_type=room_type,
            style=style,
            room_analysis=room_analysis,
            selected_items=selected_items,
            design_strategy=design_strategy,
            template_blueprint=template_blueprint,
            template_id=template_id,
            template_signature=template_signature,
            composition_validation=composition_validation,
            architectural_constraints=architectural_constraints.model_dump(
                mode="json"
            ),
            revision_context={
                "previous_plan": plan.model_dump(mode="json"),
                "layout_quality": layout_quality.model_dump(mode="json"),
            },
        )
        layout_quality = validate_layout_quality(
            plan=plan,
            selected_items=selected_items,
            architectural_constraints=architectural_constraints,
            room_dimensions=room_analysis.provided_dimensions_cm,
            composition_validation=composition_validation,
        )

    plan = plan.model_copy(
        update={
            "architectural_constraint_map": architectural_constraints.model_dump(
                mode="json"
            ),
            "layout_quality": layout_quality.model_dump(mode="json"),
        }
    )

    if (
        layout_quality.status != LayoutQualityStatus.INVALID
        and not layout_requires_replan(layout_quality)
    ):
        save_placement_plan(
            room_request_id=room_request_id,
            selection_signature=selection_signature,
            plan=plan,
        )

    return (
        plan,
        False,
        selection_signature
    )
