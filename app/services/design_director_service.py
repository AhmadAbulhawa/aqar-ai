import hashlib
import json
import os

from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field

from app.database import get_connection
from app.services.room_analysis_service import (
    RoomAnalysis,
    encode_image_as_data_url
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(
    PROJECT_ROOT / ".env"
)


DESIGN_DIRECTOR_MODEL = "gpt-5.6-sol"
DESIGN_DIRECTOR_SCHEMA_VERSION = "2"
DESIGN_DIRECTOR_PROMPT_VERSION = "2026-07-14.2"


# =========================================================
# Structured strategy schemas
# =========================================================

LayoutStrategy = Literal[
    "l_shape_conversation",
    "facing_sofas_conversation",
    "u_shape_conversation",
    "media_wall_focused",
    "single_sofa_minimal",
    "floating_zone",
    "bed_wall_focused",
    "workstation_focused",
    "dining_centered",
    "storage_optimized",
    "functional_zoning"
]


FocalPoint = Literal[
    "tv_wall",
    "fireplace",
    "window_view",
    "art_wall",
    "bed_wall",
    "work_wall",
    "dining_table",
    "none"
]


class LayoutDecision(BaseModel):
    requested_layout: str | None = None

    requested_layout_supported: bool | None = None

    final_layout_strategy: LayoutStrategy

    reason: str = Field(
        min_length=1,
        description=(
            "Must cite visible room-analysis facts such as usable walls, "
            "obstacles, circulation, or layout shape. Do not invent exact "
            "measurements that were not supplied."
        )
    )


class SeatingTarget(BaseModel):
    category: Literal[
        "sofa",
        "armchair"
    ]

    solution_type: Literal[
        "standard_sofa",
        "corner_sofa",
        "sectional"
    ] | None = None

    quantity: int = Field(
        ge=1,
        le=4
    )

    role: Literal[
        "primary",
        "secondary",
        "accent"
    ]


class DecorTarget(BaseModel):
    category: str = Field(
        min_length=1
    )

    min_quantity: int = Field(
        ge=0,
        le=10
    )

    max_quantity: int = Field(
        ge=0,
        le=10
    )

    priority: Literal[
        "essential",
        "recommended",
        "optional"
    ]


class DesignStrategy(BaseModel):
    layout: LayoutDecision

    focal_point: FocalPoint

    seating: list[SeatingTarget] = Field(
        default_factory=list
    )

    decor: list[DecorTarget] = Field(
        default_factory=list
    )

    density_target: Literal[
        "minimal",
        "balanced",
        "layered"
    ]

    symmetry: Literal[
        "symmetric",
        "asymmetric"
    ]

    lighting_layers_needed: list[
        Literal[
            "ambient",
            "task",
            "accent"
        ]
    ] = Field(
        default_factory=list
    )

    design_rationale: str = Field(
        min_length=1,
        description=(
            "Short debug explanation only. Downstream code should not rely "
            "on this free-text field."
        )
    )


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
# Catalog capability summary
# =========================================================

CORNER_SOFA_TAGS = {
    "corner_sofa",
    "sectional",
    "l_shape",
    "chaise_longue",
    "complete_seating_set"
}


def normalize_tags(
    item: dict
) -> set[str]:
    return {
        str(tag).strip().lower()
        for tag in item.get(
            "tags",
            []
        )
    }


def is_corner_sofa(
    item: dict
) -> bool:
    return (
        item.get("category") == "sofa"
        and bool(
            normalize_tags(item)
            & CORNER_SOFA_TAGS
        )
    )


def build_catalog_capability_summary(
    candidate_items: list[dict]
) -> dict:
    category_counts: dict[str, int] = {}

    styles_by_category: dict[
        str,
        set[str]
    ] = {}

    corner_sofa_count = 0
    standard_sofa_count = 0
    armchair_count = 0

    for item in candidate_items:
        if not item.get(
            "availability",
            True
        ):
            continue

        category = str(
            item.get(
                "category",
                ""
            )
        ).strip()

        if not category:
            continue

        category_counts[category] = (
            category_counts.get(
                category,
                0
            )
            + 1
        )

        style = str(
            item.get(
                "style",
                ""
            )
        ).strip()

        if style:
            styles_by_category.setdefault(
                category,
                set()
            ).add(
                style
            )

        if category == "sofa":
            if is_corner_sofa(item):
                corner_sofa_count += 1
            else:
                standard_sofa_count += 1

        elif category == "armchair":
            armchair_count += 1

    return {
        "category_counts": dict(
            sorted(
                category_counts.items()
            )
        ),
        "styles_by_category": {
            category: sorted(styles)
            for category, styles
            in sorted(
                styles_by_category.items()
            )
        },
        "seating_capabilities": {
            "standard_sofa_products": (
                standard_sofa_count
            ),
            "corner_or_sectional_sofa_products": (
                corner_sofa_count
            ),
            "armchair_products": (
                armchair_count
            )
        }
    }


# =========================================================
# Stable strategy signature
# =========================================================

def normalize_user_prompt(
    value: str | None
) -> str | None:
    if value is None:
        return None

    normalized = " ".join(
        value.strip().split()
    )

    return normalized or None


def build_design_strategy_signature(
    room_analysis: RoomAnalysis,
    room_type: str,
    style: str | None,
    preferred_colors: list[str],
    user_prompt: str | None,
    layout_preference: str | None,
    constraints: dict,
    candidate_items: list[dict] | None = None,
    room_image_path: Path | None = None,
) -> str:
    image_signature = None
    if room_image_path is not None:
        image_digest = hashlib.sha256()
        with room_image_path.open("rb") as image_file:
            for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
                image_digest.update(chunk)
        image_signature = image_digest.hexdigest()

    candidate_signature_payload = sorted(
        [
            {
                "id": int(item["id"]),
                "category": item.get("category"),
                "style": item.get("style"),
                "color": item.get("color"),
                "width_cm": item.get("width_cm"),
                "depth_cm": item.get("depth_cm"),
                "height_cm": item.get("height_cm"),
                "availability": bool(item.get("availability", True)),
                "catalog_status": item.get("catalog_status", "active"),
            }
            for item in (candidate_items or [])
        ],
        key=lambda item: item["id"],
    )
    payload = {
        "model": DESIGN_DIRECTOR_MODEL,
        "prompt_version": DESIGN_DIRECTOR_PROMPT_VERSION,
        "schema_version": DESIGN_DIRECTOR_SCHEMA_VERSION,
        "room_image_signature": image_signature,
        "candidate_catalog": candidate_signature_payload,
        "room_analysis": (
            room_analysis.model_dump()
        ),
        "room_type": room_type,
        "style": style,
        "preferred_colors": sorted({
            str(color).strip().lower()
            for color in preferred_colors
            if str(color).strip()
        }),
        "user_prompt": (
            normalize_user_prompt(
                user_prompt
            )
        ),
        "layout_preference": (
            normalize_user_prompt(
                layout_preference
            )
        ),
        "required_quantities": dict(
            sorted(
                constraints.get(
                    "required_quantities",
                    {}
                ).items()
            )
        ),
        "required_product_quantities": dict(
            sorted(
                {
                    str(product_id): int(quantity)
                    for product_id, quantity
                    in constraints.get(
                        "required_product_quantities",
                        {}
                    ).items()
                }.items()
            )
        ),
        "excluded_categories": sorted(
            constraints.get(
                "excluded_categories",
                []
            )
        )
    }

    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":")
    )

    return hashlib.sha256(
        canonical_json.encode(
            "utf-8"
        )
    ).hexdigest()


# =========================================================
# Design-strategy validation
# =========================================================

def validate_design_strategy(
    strategy: DesignStrategy,
    room_analysis: RoomAnalysis,
    room_type: str,
    constraints: dict,
    candidate_items: list[dict],
    layout_preference: str | None = None
) -> DesignStrategy:
    errors: list[str] = []

    required_quantities = {
        str(category): int(quantity)
        for category, quantity
        in constraints.get(
            "required_quantities",
            {}
        ).items()
    }

    excluded_categories = {
        str(category)
        for category in constraints.get(
            "excluded_categories",
            []
        )
    }

    catalog_categories = {
        str(item.get("category"))
        for item in candidate_items
        if item.get(
            "availability",
            True
        )
    }

    # -----------------------------------------------------
    # Layout-preference consistency
    # -----------------------------------------------------

    if layout_preference:
        if (
            strategy.layout.requested_layout
            is None
        ):
            errors.append(
                "requested_layout must be populated when a layout preference was supplied."
            )

        if (
            strategy.layout.requested_layout_supported
            is None
        ):
            errors.append(
                "requested_layout_supported must be true or false when a layout preference was supplied."
            )

        if (
            strategy.layout.requested_layout_supported
            is False
            and not strategy.layout.reason.strip()
        ):
            errors.append(
                "An unsupported requested layout requires a non-empty geometric reason."
            )

    # -----------------------------------------------------
    # TV exclusion vs focal point
    # -----------------------------------------------------

    if (
        "television" in excluded_categories
        or "tv_stand" in excluded_categories
    ):
        if strategy.focal_point == "tv_wall":
            errors.append(
                "tv_wall cannot be the focal point when television or tv_stand is excluded."
            )

    # -----------------------------------------------------
    # Seating consistency
    # -----------------------------------------------------

    seating_totals: dict[str, int] = {
        "sofa": 0,
        "armchair": 0
    }

    standard_sofa_total = 0
    corner_sofa_total = 0

    for target in strategy.seating:
        seating_totals[
            target.category
        ] = (
            seating_totals.get(
                target.category,
                0
            )
            + int(target.quantity)
        )

        if target.category in excluded_categories:
            errors.append(
                f"Design strategy uses excluded seating category: {target.category}."
            )

        if target.category == "sofa":
            if target.solution_type is None:
                errors.append(
                    "Every sofa seating target must specify solution_type."
                )

            elif target.solution_type == "standard_sofa":
                standard_sofa_total += int(
                    target.quantity
                )

            elif target.solution_type in {
                "corner_sofa",
                "sectional"
            }:
                corner_sofa_total += int(
                    target.quantity
                )

                if int(target.quantity) != 1:
                    errors.append(
                        "Corner or sectional sofa target quantity must be exactly 1."
                    )

        elif (
            target.category == "armchair"
            and target.solution_type is not None
        ):
            errors.append(
                "Armchair targets must use solution_type = null."
            )

    if room_type != "living_room" and strategy.seating:
        errors.append(
            "Non-living rooms must not receive living-room seating targets."
        )

    if (
        room_type == "living_room"
        and "sofa" not in excluded_categories
        and seating_totals["sofa"] < 1
    ):
        errors.append(
            "Living-room design strategy must include at least one sofa target unless sofa is explicitly excluded."
        )

    if (
        strategy.focal_point == "tv_wall"
        and (
            "television" not in catalog_categories
            or "tv_stand" not in catalog_categories
        )
    ):
        errors.append(
            "tv_wall cannot be the focal point because the current catalog cannot supply both television and tv_stand."
        )

    # Guided seating quantities are hard requirements.
    for category in {
        "sofa",
        "armchair"
    }:
        if category in required_quantities:
            expected = int(
                required_quantities[
                    category
                ]
            )

            actual = int(
                seating_totals.get(
                    category,
                    0
                )
            )

            if actual != expected:
                errors.append(
                    f"Design strategy seating target for {category} must equal guided quantity {expected}; got {actual}."
                )

    # Auto-only capacity checks. These are estimates, not exact measurements.
    if constraints.get("selection_mode") == "auto":
        estimated_sofa_capacity = (
            room_analysis
            .estimated_max_standard_sofa_units
        )

        if (
            estimated_sofa_capacity is not None
            and standard_sofa_total
            > int(estimated_sofa_capacity) + 1
        ):
            errors.append(
                "Standard-sofa target materially exceeds the room analyzer's estimated capacity."
            )

        estimated_armchair_capacity = (
            room_analysis
            .estimated_max_armchair_units
        )

        if (
            estimated_armchair_capacity is not None
            and seating_totals["armchair"]
            > int(estimated_armchair_capacity) + 1
        ):
            errors.append(
                "Armchair target materially exceeds the room analyzer's estimated capacity."
            )

        if (
            corner_sofa_total > 0
            and room_analysis.corner_zone_available
            is False
        ):
            errors.append(
                "A corner/sectional sofa was selected as strategy despite corner_zone_available = false."
            )

    if corner_sofa_total > 0:
        has_corner_catalog_product = any(
            is_corner_sofa(item)
            for item in candidate_items
            if item.get(
                "availability",
                True
            )
        )

        if not has_corner_catalog_product:
            errors.append(
                "Design strategy requires a corner/sectional sofa but the catalog has none."
            )

    # -----------------------------------------------------
    # Decor consistency
    # -----------------------------------------------------

    for target in strategy.decor:
        if target.min_quantity > target.max_quantity:
            errors.append(
                f"Decor target {target.category} has min_quantity greater than max_quantity."
            )

        if target.category in excluded_categories:
            errors.append(
                f"Design strategy uses excluded decor category: {target.category}."
            )

        if target.category not in catalog_categories:
            errors.append(
                f"Design strategy uses catalog category that is unavailable: {target.category}."
            )

    if errors:
        raise ValueError(
            "Invalid Design Director strategy: "
            + " | ".join(errors)
        )

    return strategy


# =========================================================
# Prompt construction
# =========================================================

def build_design_director_prompt(
    room_analysis: RoomAnalysis,
    room_type: str,
    style: str | None,
    preferred_colors: list[str],
    user_prompt: str | None,
    layout_preference: str | None,
    constraints: dict,
    catalog_capability_summary: dict
) -> str:
    guided_constraints = {
        "required_quantities": (
            constraints.get(
                "required_quantities",
                {}
            )
        ),
        "required_exact_products": (
            constraints.get(
                "required_exact_products",
                []
            )
        ),
        "excluded_categories": (
            constraints.get(
                "excluded_categories",
                []
            )
        )
    }

    return f"""
You are the Design Director for Aqar AI.

You do NOT choose exact product IDs.
However, if exact user-selected products are listed in guided constraints,
you must design around them as locked products.
They may not be replaced for style, color, price, or taste.
Only backend physical-fit validation may reject them before this stage.
You do NOT place exact products.
You decide the room's high-level interior-design strategy before shopping.

Your output will be consumed programmatically by:

1. the AI Product Selector,
2. backend validation,
3. the Placement Planner.

Therefore every structured field must be useful and internally consistent.

============================================================
ROOM REQUEST
============================================================

Room type:
{room_type}

Requested style:
{style}

Preferred colors:
{json.dumps(
    preferred_colors,
    ensure_ascii=False
)}

User prompt:
{user_prompt}

Requested layout preference:
{layout_preference}

============================================================
ROOM ANALYSIS — PHYSICAL FACTS ONLY
============================================================

{json.dumps(
    room_analysis.model_dump(),
    ensure_ascii=False,
    indent=2
)}

IMPORTANT:

- estimated_max_standard_sofa_units and estimated_max_armchair_units are
  visual estimates, not exact measured dimensions.

- If either estimated capacity is null, do NOT use a fixed default.
  Reassess the actual uploaded room image directly and make a cautious
  visual judgment from visible geometry, usable walls, obstacles, and
  circulation.

- corner_zone_available is only a geometric observation about whether a
  visible corner or connected usable zone appears available. It is NOT a
  decision to use a corner sofa.

- Never invent exact wall lengths or exact room dimensions that were not
  supplied by the backend.

============================================================
GUIDED HARD CONSTRAINTS
============================================================

{json.dumps(
    guided_constraints,
    ensure_ascii=False,
    indent=2
)}

These hard constraints always win over your preferred strategy.

Examples:

- If television is excluded, do not use tv_wall as focal_point.
- If sofa quantity is explicitly required as 2, the seating strategy must
  target exactly 2 total sofa units.
- Never build a strategy around an excluded category.

============================================================
CATALOG CAPABILITIES
============================================================

{json.dumps(
    catalog_capability_summary,
    ensure_ascii=False,
    indent=2
)}

This is a capability summary, not the full catalog.

Do not commit to a product type the catalog cannot support.

For sofas, the database category remains exactly:

"sofa"

Use solution_type to distinguish:

- standard_sofa
- corner_sofa
- sectional

For a sofa + armchairs strategy, return separate seating targets.

Example:

[
  {{
    "category": "sofa",
    "solution_type": "standard_sofa",
    "quantity": 1,
    "role": "primary"
  }},
  {{
    "category": "armchair",
    "solution_type": null,
    "quantity": 2,
    "role": "secondary"
  }}
]

============================================================
DESIGN DECISIONS
============================================================

Decide only the major strategic choices that materially change downstream
selection and placement:

- final layout strategy,
- focal point,
- seating composition,
- decor targets,
- density target,
- symmetry,
- lighting layers.

Do not create verbose philosophy fields.
Do not duplicate style and preferred colors into new strategy fields.

For decor:

- Use only category names that exist in the catalog capability summary.
- essential means the Product Selector should strongly satisfy the minimum.
- recommended means it should be preferred when composition and image budget allow.
- optional means it may be dropped first.

For layout preference:

- If no preference was supplied, requested_layout and
  requested_layout_supported should both be null.

- If a preference was supplied, evaluate it against the actual room image
  and room-analysis facts.

- The reason must cite visible facts such as layout shape, usable walls,
  obstacles, or circulation.

- Do not invent measurements.

For living-room seating:

- Compare multiple standard sofas, one corner/sectional sofa, and a mixed
  sofa + armchair composition.

- A corner or sectional sofa is normally quantity 1.

- Do not force two standard sofas merely because a previous legacy field
  might have suggested two sofa units.

- Choose the composition that best fits actual geometry, circulation,
  focal point, style, and visual balance.

============================================================
FINAL REQUIREMENT
============================================================

Return one structured DesignStrategy only.
The strategy must be realistic, concise, and directly executable by the
Product Selector and Placement Planner.
""".strip()


# =========================================================
# AI generation
# =========================================================

def create_design_strategy(
    room_image_path: Path,
    room_analysis: RoomAnalysis,
    room_type: str,
    style: str | None,
    preferred_colors: list[str],
    user_prompt: str | None,
    layout_preference: str | None,
    constraints: dict,
    candidate_items: list[dict]
) -> DesignStrategy:
    if not candidate_items:
        raise HTTPException(
            status_code=404,
            detail=(
                "No candidate products were provided "
                "to the Design Director."
            )
        )

    client = get_openai_client()

    image_data_url = (
        encode_image_as_data_url(
            room_image_path
        )
    )

    catalog_capability_summary = (
        build_catalog_capability_summary(
            candidate_items
        )
    )

    prompt = build_design_director_prompt(
        room_analysis=room_analysis,
        room_type=room_type,
        style=style,
        preferred_colors=preferred_colors,
        user_prompt=user_prompt,
        layout_preference=layout_preference,
        constraints=constraints,
        catalog_capability_summary=(
            catalog_capability_summary
        )
    )

    try:
        response = client.responses.parse(
            model=DESIGN_DIRECTOR_MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict interior-design strategy engine. "
                        "Decide the design concept before product selection. "
                        "Respect room geometry, user hard constraints, and "
                        "catalog capabilities."
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
            text_format=DesignStrategy
        )

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Design Director failed: "
                f"{exc}"
            )
        ) from exc

    strategy = response.output_parsed

    if strategy is None:
        raise HTTPException(
            status_code=502,
            detail=(
                "Design Director did not return "
                "a valid structured response."
            )
        )

    try:
        return validate_design_strategy(
            strategy=strategy,
            room_analysis=room_analysis,
            room_type=room_type,
            constraints=constraints,
            candidate_items=candidate_items,
            layout_preference=layout_preference
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc)
        ) from exc


# =========================================================
# Cache table
# =========================================================

def ensure_design_strategies_table() -> None:
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS design_strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_request_id INTEGER NOT NULL,
                strategy_signature TEXT NOT NULL,
                strategy_json TEXT NOT NULL,
                model_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (
                    room_request_id,
                    strategy_signature
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
# Cache read / write
# =========================================================

def get_saved_design_strategy(
    room_request_id: int,
    strategy_signature: str
) -> DesignStrategy | None:
    ensure_design_strategies_table()

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT strategy_json
            FROM design_strategies
            WHERE room_request_id = ?
              AND strategy_signature = ?
            """,
            (
                room_request_id,
                strategy_signature
            )
        )

        row = cursor.fetchone()

    finally:
        connection.close()

    if row is None:
        return None

    try:
        strategy_json = row[
            "strategy_json"
        ]
    except (
        TypeError,
        KeyError,
        IndexError
    ):
        strategy_json = row[0]

    try:
        strategy_data = json.loads(
            strategy_json
        )

        return DesignStrategy.model_validate(
            strategy_data
        )

    except (
        ValueError,
        TypeError,
        json.JSONDecodeError
    ):
        return None


def save_design_strategy(
    room_request_id: int,
    strategy_signature: str,
    strategy: DesignStrategy
) -> None:
    ensure_design_strategies_table()

    connection = get_connection()
    cursor = connection.cursor()

    strategy_json = json.dumps(
        strategy.model_dump(),
        ensure_ascii=False
    )

    try:
        cursor.execute(
            """
            INSERT INTO design_strategies (
                room_request_id,
                strategy_signature,
                strategy_json,
                model_name
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(
                room_request_id,
                strategy_signature
            )
            DO UPDATE SET
                strategy_json = excluded.strategy_json,
                model_name = excluded.model_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                room_request_id,
                strategy_signature,
                strategy_json,
                DESIGN_DIRECTOR_MODEL
            )
        )

        connection.commit()

    finally:
        connection.close()


# =========================================================
# Main cached entry point
# =========================================================

def get_or_create_design_strategy(
    room_request_id: int,
    room_image_path: Path,
    room_analysis: RoomAnalysis,
    room_type: str,
    style: str | None,
    preferred_colors: list[str],
    user_prompt: str | None,
    layout_preference: str | None,
    constraints: dict,
    candidate_items: list[dict]
) -> tuple[
    DesignStrategy,
    bool,
    str
]:
    strategy_signature = (
        build_design_strategy_signature(
            room_analysis=room_analysis,
            room_type=room_type,
            style=style,
            preferred_colors=preferred_colors,
            user_prompt=user_prompt,
            layout_preference=layout_preference,
            constraints=constraints,
            candidate_items=candidate_items,
            room_image_path=room_image_path,
        )
    )

    saved_strategy = get_saved_design_strategy(
        room_request_id=room_request_id,
        strategy_signature=strategy_signature
    )

    if saved_strategy is not None:
        try:
            validate_design_strategy(
                strategy=saved_strategy,
                room_analysis=room_analysis,
                room_type=room_type,
                constraints=constraints,
                candidate_items=candidate_items,
                layout_preference=layout_preference
            )

            return (
                saved_strategy,
                True,
                strategy_signature
            )

        except ValueError:
            # Cached strategy no longer matches current catalog
            # or constraints. Regenerate below.
            pass

    strategy = create_design_strategy(
        room_image_path=room_image_path,
        room_analysis=room_analysis,
        room_type=room_type,
        style=style,
        preferred_colors=preferred_colors,
        user_prompt=user_prompt,
        layout_preference=layout_preference,
        constraints=constraints,
        candidate_items=candidate_items
    )

    save_design_strategy(
        room_request_id=room_request_id,
        strategy_signature=strategy_signature,
        strategy=strategy
    )

    return (
        strategy,
        False,
        strategy_signature
    )
