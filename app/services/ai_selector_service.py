import json
import os

from fastapi import HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field

from app.enums import RequirementMode
from app.models import DesignRequest
from app.services.catalog_compatibility_service import is_design_eligible
from app.services.room_analysis_service import RoomAnalysis
from app.services.design_director_service import DesignStrategy


# =========================================================
# Constants
# =========================================================

AI_SELECTOR_MODEL = "gpt-5.6-terra"
# Technical limit for unique product reference images
# sent later to the image-generation pipeline.
MAX_FURNITURE_REFERENCE_IMAGES = 15

CORNER_SOFA_TAGS = {
    "corner_sofa",
    "sectional",
    "l_shape",
    "chaise_longue",
    "complete_seating_set"
}


# =========================================================
# Structured output schemas
# =========================================================

class AISelectedProduct(BaseModel):
    id: int = Field(
        description=(
            "Exact product ID from the candidate database products."
        )
    )

    quantity: int = Field(
        ge=1,
        le=10,
        description=(
            "Number of units of this exact product "
            "to use in the final room."
        )
    )


class AISelectionResult(BaseModel):
    selected_products: list[AISelectedProduct]

    design_summary: str = Field(
        description=(
            "A short explanation of the selected product composition "
            "and overall visual direction."
        )
    )


# =========================================================
# Product helpers
# =========================================================

def normalize_tags(
    item: dict
) -> set[str]:
    return {
        str(tag).strip().lower()
        for tag in item.get("tags", [])
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


def build_seating_candidate_summary(
    candidate_items: list[dict]
) -> dict:
    corner_sofa_candidates = []
    standard_sofa_candidates = []
    armchair_candidates = []

    for item in candidate_items:
        if item.get("category") not in {
            "sofa",
            "armchair"
        }:
            continue

        summary = {
            "id": int(item["id"]),
            "name": item.get("name"),
            "category": item.get("category"),
            "style": item.get("style"),
            "color": item.get("color"),
            "width_cm": item.get("width_cm"),
            "depth_cm": item.get("depth_cm"),
            "height_cm": item.get("height_cm"),
            "price": item.get("price"),
            "tags": item.get("tags", [])
        }

        if is_corner_sofa(item):
            corner_sofa_candidates.append(summary)
        elif item.get("category") == "sofa":
            standard_sofa_candidates.append(summary)
        elif item.get("category") == "armchair":
            armchair_candidates.append(summary)

    return {
        "corner_sofas": corner_sofa_candidates,
        "standard_sofas": standard_sofa_candidates,
        "armchairs": armchair_candidates
    }


# =========================================================
# Prepare candidate metadata
# =========================================================

def prepare_candidate_products(
    candidate_items: list[dict]
) -> list[dict]:

    prepared_candidates = []

    for item in candidate_items:
        prepared_candidates.append({
            "id": int(item["id"]),
            "name": item["name"],
            "category": item["category"],
            "style": item["style"],
            "color": item["color"],
            "material": item.get("material"),

            "width_cm": item["width_cm"],
            "depth_cm": item["depth_cm"],
            "height_cm": item["height_cm"],

            "price": item["price"],

            "currency": item.get(
                "currency",
                "JOD"
            ),

            "room_types": item.get(
                "room_types",
                []
            ),

            "tags": item.get(
                "tags",
                []
            )
        })

    return prepared_candidates


# =========================================================
# Build room-specific rules
# =========================================================

def build_room_specific_rules(
    room_type: str
) -> str:

    if room_type == "living_room":
        return """
============================================================
LIVING ROOM SEATING RULES
============================================================

For a living room, seating must feel complete and functional.

You MUST select at least one sofa product unless the user explicitly
excluded sofas in Guided Mode.

A selected sofa with any of these tags must be treated as a complete
main seating solution when its dimensions and room geometry fit:

- corner_sofa
- sectional
- l_shape
- chaise_longue
- complete_seating_set

A corner / sectional / L-shaped sofa may replace multiple standard
sofas. Do not add another sofa merely to satisfy a two-sofa target
unless the room is clearly large and extra seating is genuinely useful.

Valid seating solutions include:

- 2 standard sofas.
- 1 standard sofa + 1 armchair.
- 1 standard sofa + 2 armchairs.
- 1 suitable corner / sectional sofa.
- 1 suitable corner / sectional sofa + 1 accent chair only when the
  room is large enough and the chair improves the composition.

Invalid seating solution:

- Only 1 small standard sofa with no other seating.

Do not use more seating than necessary.

============================================================
DESIGN-STRATEGY SEATING EXECUTION
============================================================

When a Design Director strategy is provided, treat its seating targets as
the composition plan to execute.

You must still compare actual candidate products by:

- dimensions
- room geometry
- circulation
- style
- preferred colors
- visual balance

But do not silently replace the chosen seating strategy with a different
one merely because another candidate is individually attractive.

For sofas:

- category remains exactly "sofa".
- solution_type = standard_sofa means select standard sofa products.
- solution_type = corner_sofa or sectional means select a sofa product
  tagged with one or more of:

  - corner_sofa
  - sectional
  - l_shape
  - chaise_longue
  - complete_seating_set

A corner or sectional sofa is normally quantity 1.
Never return quantity greater than 1 for the same corner or sectional sofa
product in Auto mode.

When no Design Director strategy is provided, compare these options:

A. Multiple standard sofas.
B. One corner / sectional / L-shaped sofa.
C. One standard sofa combined with armchairs.

Do not default to two identical standard sofas without a real geometric
and design reason.

============================================================
LIVING ROOM QUANTITY GUIDANCE
============================================================

For living rooms:

- standard sofa: maximum 2 units total
- corner / sectional sofa: maximum 1 unit total
- armchair: maximum 2 units total
- coffee_table: maximum 1 unit
- tv_stand: maximum 1 unit
- television: maximum 1 unit
- sideboard: maximum 1 unit
- side_table: maximum 2 units total
- rug: maximum 1 unit
- floor_lamp: maximum 2 units total
- wall_art: maximum 2 units total

Choose between:

- multiple standard sofas,
- one corner / sectional sofa,
- or a mixed sofa and armchair composition,

based on room geometry, circulation, focal point, style, dimensions,
and visual quality.

============================================================
TELEVISION COMPATIBILITY
============================================================

For a living room:

- If a television is selected, a tv_stand must also be selected.
- If a tv_stand is selected, a television must also be selected.
- Treat television and tv_stand as a compatible pair.
- Do not use a sideboard as a replacement for the required tv_stand
  when a television is selected.
- Choose a television size that works visually with the dimensions of
  the selected tv_stand.

============================================================
LIVING ROOM TV PREFERENCE
============================================================

For living rooms in AUTO mode:

- Prefer selecting a television together with a compatible tv_stand
  when suitable products are available.

- Treat television + tv_stand as the preferred default entertainment
  setup for a typical modern living room.

- Choose a sideboard instead only when it clearly creates a more
  coherent and functional composition for the requested style, colors,
  and products.

- Do not select both tv_stand and sideboard unless the room composition
  clearly benefits from both.

============================================================
WALL ART RULES
============================================================

Wall art should improve the room richness without becoming random
visual noise.

Prefer wall art when:

- the room would otherwise feel too empty or sparse,
- a solid wall section is available,
- it matches the style and color palette,
- it adds texture, warmth, rhythm, or a finished designer look.

Do not select wall art when:

- there is no clear wall location,
- it would compete with the TV focal point,
- it would block or crowd a window, door, or opening,
- it feels unrelated to the selected furniture palette.

Wall art should not automatically be placed above or around the
television.

Prefer:

1. above a sofa,
2. on a secondary solid wall,
3. or on another visually empty wall section,

unless the media wall has enough clear space and the wall art clearly
improves the composition without competing with the TV focal point.

For wall-art sets containing multiple pieces, treat the set as one
coordinated composition.
""".strip()

    return """
============================================================
ROOM-SPECIFIC RULES
============================================================

No additional hard-coded room-specific composition rules are currently
defined for this room type.

Follow the room template, required categories, one-of groups, optional
categories, product dimensions, product quantities, and overall design
coherence.
""".strip()


def resolve_design_constraints(
    request: DesignRequest,
    room_template: dict,
    candidate_items: list[dict] | None = None
) -> dict:
    """
    Resolve the final design constraints by combining:

    - Room template rules.
    - User required categories.
    - User excluded categories.
    - User explicit auto categories.

    User choices override the default room template.
    """

    required_quantities: dict[str, int] = {}
    excluded_categories: set[str] = set()
    explicit_auto_categories: set[str] = set()
    explicit_categories: set[str] = set()

    required_product_quantities: dict[int, int] = {}
    required_exact_products: list[dict] = []
    exact_category_quantities: dict[str, int] = {}

    # =====================================================
    # Read user requirements
    # =====================================================

    for requirement in request.product_requirements:

        category = (
            requirement.category.value
        )

        mode = (
            requirement.mode.value
        )

        explicit_categories.add(
            category
        )

        if mode == RequirementMode.REQUIRED.value:

            required_quantities[
                category
            ] = int(
                requirement.quantity
            )

        elif mode == RequirementMode.EXCLUDED.value:

            excluded_categories.add(
                category
            )

        elif mode == RequirementMode.AUTO.value:

            explicit_auto_categories.add(
                category
            )

    # =====================================================
    # Read exact user-selected products
    # =====================================================

    candidate_by_id = {
        int(item["id"]): item
        for item in (candidate_items or [])
    }

    for requirement in request.required_products:
        product_id = int(requirement.product_id)
        quantity = int(requirement.quantity)

        if product_id in required_product_quantities:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Each exact product ID may appear only once in "
                    "required_products. Use quantity for multiple units."
                )
            )

        required_product_quantities[product_id] = quantity

        if candidate_items is None:
            continue

        product = candidate_by_id.get(product_id)

        if product is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": (
                        "Exact selected product does not exist in the "
                        "available room catalog."
                    ),
                    "product_id": product_id
                }
            )

        if not is_design_eligible(product):
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Exact selected product is unavailable or not design-eligible.",
                    "product_id": product_id
                }
            )

        category = str(product["category"])

        if category in excluded_categories:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": (
                        "Exact selected product belongs to an excluded category."
                    ),
                    "product_id": product_id,
                    "category": category
                }
            )

        exact_category_quantities[category] = (
            exact_category_quantities.get(category, 0) + quantity
        )

        required_exact_products.append({
            "id": product_id,
            "name": product.get("name"),
            "category": category,
            "quantity": quantity,
            "style": product.get("style"),
            "color": product.get("color"),
            "width_cm": product.get("width_cm"),
            "depth_cm": product.get("depth_cm"),
            "height_cm": product.get("height_cm"),
            "tags": product.get("tags", [])
        })

    for category, exact_quantity in exact_category_quantities.items():
        required_quantities[category] = max(
            required_quantities.get(category, 0),
            exact_quantity
        )

    # =====================================================
    # Living-room TV relationship rules
    # =====================================================

    if request.room_type.value == "living_room":

        television = "television"
        tv_stand = "tv_stand"

        # -------------------------------------------------
        # Conflict:
        # TV required but TV stand excluded
        # -------------------------------------------------

        if (
            television in required_quantities
            and tv_stand in excluded_categories
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Invalid guided-design requirements: "
                    "television is required but tv_stand "
                    "is excluded."
                )
            )

        # -------------------------------------------------
        # Conflict:
        # TV stand required but TV excluded
        # -------------------------------------------------

        if (
            tv_stand in required_quantities
            and television in excluded_categories
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Invalid guided-design requirements: "
                    "tv_stand is required but television "
                    "is excluded."
                )
            )

        # -------------------------------------------------
        # If either side is excluded,
        # exclude the whole pair.
        # -------------------------------------------------

        if (
            television in excluded_categories
            or tv_stand in excluded_categories
        ):
            excluded_categories.update({
                television,
                tv_stand
            })

        # -------------------------------------------------
        # If either side is required,
        # require the whole pair.
        # -------------------------------------------------

        if (
            television in required_quantities
            or tv_stand in required_quantities
        ):

            television_quantity = (
                required_quantities.get(
                    television,
                    1
                )
            )

            tv_stand_quantity = (
                required_quantities.get(
                    tv_stand,
                    1
                )
            )

            if television_quantity != 1:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Living-room television quantity "
                        "must currently be exactly 1."
                    )
                )

            if tv_stand_quantity != 1:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Living-room tv_stand quantity "
                        "must currently be exactly 1."
                    )
                )

            required_quantities[
                television
            ] = 1

            required_quantities[
                tv_stand
            ] = 1

    # =====================================================
    # Effective required categories
    # =====================================================

    effective_required_categories = [
        category
        for category in room_template.get(
            "required",
            []
        )
        if category not in excluded_categories
    ]

    # Add user-required categories.
    for category in required_quantities:

        if (
            category
            not in effective_required_categories
        ):
            effective_required_categories.append(
                category
            )

    # =====================================================
    # Effective one-of groups
    # =====================================================

    effective_one_of_groups = []

    for group in room_template.get(
        "one_of",
        []
    ):

        available_group_categories = [
            category
            for category in group
            if category not in excluded_categories
        ]

        # If the user excluded every category
        # inside this group, skip the group.
        if available_group_categories:

            effective_one_of_groups.append(
                available_group_categories
            )

    # =====================================================
    # Selection mode
    # =====================================================

    selection_mode = (
        "guided"
        if (
            request.product_requirements
            or request.required_products
        )
        else "auto"
    )

    return {
        "selection_mode":
            selection_mode,

        "required_quantities":
            required_quantities,

        "required_product_quantities":
            required_product_quantities,

        "required_exact_products":
            required_exact_products,

        "excluded_categories":
            sorted(
                excluded_categories
            ),

        "explicit_auto_categories":
            sorted(
                explicit_auto_categories
            ),

        "explicit_categories":
            sorted(
                explicit_categories
            ),

        "effective_required_categories":
            effective_required_categories,

        "effective_one_of_groups":
            effective_one_of_groups
    }


# =========================================================
# Build AI selector prompt
# =========================================================

def build_ai_selector_prompt(
    request: DesignRequest,
    room_template: dict,
    candidate_items: list[dict],
    constraints: dict | None = None,
    room_analysis: RoomAnalysis | None = None,
    design_strategy: DesignStrategy | None = None
) -> str:

    room_type = (
        request.room_type.value
    )

    style = (
        request.style.value
        if request.style
        else None
    )

    preferred_colors = (
        request.preferred_colors
    )

    user_prompt = (
        request.user_prompt
        if request.user_prompt
        else None
    )

    if constraints is None:
        constraints = (
            resolve_design_constraints(
                request=request,
                room_template=room_template
            )
        )

    required_quantities = (
        constraints[
            "required_quantities"
        ]
    )

    excluded_categories = (
        constraints[
            "excluded_categories"
        ]
    )

    explicit_auto_categories = (
        constraints[
            "explicit_auto_categories"
        ]
    )

    effective_required_categories = (
        constraints[
            "effective_required_categories"
        ]
    )

    effective_one_of_groups = (
        constraints[
            "effective_one_of_groups"
        ]
    )

    selection_mode = (
        constraints[
            "selection_mode"
        ]
    )

    optional_categories = (
        room_template.get(
            "optional",
            []
        )
    )

    max_unique_products = min(
        int(
            room_template.get(
                "max_unique_products",
                MAX_FURNITURE_REFERENCE_IMAGES
            )
        ),
        MAX_FURNITURE_REFERENCE_IMAGES
    )

    candidate_products = (
        prepare_candidate_products(
            candidate_items
        )
    )

    seating_candidates = (
        build_seating_candidate_summary(
            candidate_items
        )
    )

    room_specific_rules = (
        build_room_specific_rules(
            room_type
        )
    )

    room_analysis_text = ""

    if room_analysis is not None:
        room_analysis_text = f"""
============================================================
ROOM IMAGE ANALYSIS — PHYSICAL FACTS
============================================================

Apparent room size:
{room_analysis.room_size}

Layout shape:
{room_analysis.layout_shape}

Usable walls:
{json.dumps(
    room_analysis.usable_walls,
    ensure_ascii=False
)}

Obstacles:
{json.dumps(
    room_analysis.obstacles,
    ensure_ascii=False
)}

Circulation paths:
{json.dumps(
    room_analysis.circulation_paths,
    ensure_ascii=False
)}

Estimated maximum standard sofa units:
{room_analysis.estimated_max_standard_sofa_units}

Estimated maximum armchair units:
{room_analysis.estimated_max_armchair_units}

Corner zone available:
{room_analysis.corner_zone_available}

Factual planning notes:
{json.dumps(
    room_analysis.design_notes,
    ensure_ascii=False
)}

IMPORTANT:

- These capacities are visual estimates, not exact measurements.
- If an estimated capacity is null, do not use a fixed default.
- Use the uploaded-room facts and the Design Director strategy instead.
- The deprecated recommended_sofa_units and recommended_armchair_units
  fields are compatibility-only and must not drive new selection logic.
- Keep doors, windows, openings, and circulation paths clear.
- User hard requirements always have priority in Guided mode.
""".strip()

    design_strategy_text = ""

    if design_strategy is not None:
        design_strategy_text = f"""
============================================================
DESIGN DIRECTOR STRATEGY
============================================================

{json.dumps(
    design_strategy.model_dump(),
    ensure_ascii=False,
    indent=2
)}

IMPORTANT:

- This strategy was decided before product selection.
- Use it as the high-level composition plan.
- Do not invent a different focal point, seating composition, density,
  symmetry, or layout strategy unless a hard user requirement forces it.
- Guided hard constraints always win over strategy preferences.
- Select exact real product IDs that best execute this strategy.
""".strip()

    prompt = f"""
You are the AI product selector for Aqar AI, a real interior
design application.

Your task is to select a complete, visually coherent set of REAL
products from the supplied database candidates.

You must select only product IDs that exist in the candidate list.

============================================================
SELECTION MODE
============================================================

Current mode:
{selection_mode}

AUTO MODE means:
The AI decides the complete room composition using the room template,
design preferences, product metadata, and composition rules.

GUIDED MODE means:
The user has supplied hard product requirements.

Required categories and quantities MUST be satisfied exactly.

Excluded categories MUST NOT be selected.

Everything else may be selected automatically when it improves the room.

============================================================
DESIGN REQUEST
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

Budget:
IGNORE TOTAL BUDGET COMPLETELY FOR THIS VERSION.

{room_analysis_text}

{design_strategy_text}

============================================================
USER HARD REQUIREMENTS
============================================================

Required exact quantities:
{json.dumps(
    required_quantities,
    ensure_ascii=False
)}

Excluded categories:
{json.dumps(
    excluded_categories,
    ensure_ascii=False
)}

Explicitly marked Auto categories:
{json.dumps(
    explicit_auto_categories,
    ensure_ascii=False
)}

IMPORTANT:

Every category in Required exact quantities MUST appear with exactly
the specified total quantity.

Every category in Excluded categories MUST NOT appear at all.

Categories not explicitly mentioned by the user may still be selected
automatically when they improve the room design.

============================================================
EFFECTIVE ROOM TEMPLATE
============================================================

Required categories after applying user overrides:
{json.dumps(
    effective_required_categories,
    ensure_ascii=False
)}

Effective one-of groups after applying user overrides:
{json.dumps(
    effective_one_of_groups,
    ensure_ascii=False
)}

Optional categories:
{json.dumps(
    optional_categories,
    ensure_ascii=False
)}

Maximum unique product IDs:
{max_unique_products}

IMPORTANT:

This is only a limit on UNIQUE PRODUCT IDs.

It is NOT a fixed limit on total physical product units.

Example:

{{
  "id": 1,
  "quantity": 2
}}

means:

- 1 unique product ID
- 2 physical product units

============================================================
SEATING CANDIDATES SUMMARY
============================================================

Use this section to compare seating typologies before selecting sofas.

Corner / sectional / L-shaped sofa candidates:
{json.dumps(
    seating_candidates["corner_sofas"],
    ensure_ascii=False,
    indent=2
)}

Standard sofa candidates:
{json.dumps(
    seating_candidates["standard_sofas"],
    ensure_ascii=False,
    indent=2
)}

Armchair candidates:
{json.dumps(
    seating_candidates["armchairs"],
    ensure_ascii=False,
    indent=2
)}

============================================================
GENERAL SELECTION RULES
============================================================

1. Every user-required category must be satisfied exactly.

2. Never select any user-excluded category.

3. Select at least one suitable product for every effective required
   category unless the user explicitly excluded that category.

4. For every effective one-of group, select at least one suitable product
   from that group.

5. Optional products should be selected when they improve:

   - completeness
   - function
   - visual balance
   - realism
   - design coherence

6. Do not automatically select every optional category.

7. Avoid both extremes:

   - Do not overcrowd the room.
   - Do not leave the room visually sparse or under-furnished.

8. Select products according to:

   - requested style
   - preferred colors
   - materials
   - dimensions
   - proportions
   - quantities
   - room function
   - overall design direction

9. Never invent a product ID.

10. Never return an ID outside the supplied candidate list.

11. Each product ID must appear only once in selected_products.

12. Use quantity when multiple units of the same exact product are needed.

13. Never select more than {max_unique_products} unique product IDs.

14. Do not use a fixed total number of product units.

15. Do not stop merely because the minimum required categories are complete.

16. Continue adding useful optional products when they make the design
    feel more complete and visually finished.

17. Do not add random products merely to increase item count.

18. Respect actual product dimensions and realistic proportions.

19. Do not assume exact room dimensions when they have not been supplied.

20. User hard requirements have priority over default template preferences.

21. The design_summary must mention only products that are actually
    selected in selected_products. Do not mention armchairs, decor, lamps,
    wall art, or any other item unless its exact product ID is selected.

{room_specific_rules}

============================================================
CANDIDATE PRODUCTS
============================================================

{json.dumps(
    candidate_products,
    ensure_ascii=False,
    indent=2
)}

============================================================
FINAL TASK
============================================================

Return:

- selected_products
- design_summary

Each selected_products entry must contain:

{{
  "id": exact_database_product_id,
  "quantity": number_of_units
}}

Example:

{{
  "selected_products": [
    {{
      "id": 1,
      "quantity": 2
    }},
    {{
      "id": 10,
      "quantity": 1
    }}
  ],
  "design_summary": "..."
}}

The result must satisfy every user hard requirement exactly.
""".strip()

    return prompt


# =========================================================
# AI selection
# =========================================================

def select_furniture_with_ai(
    request: DesignRequest,
    room_template: dict,
    candidate_items: list[dict],
    constraints: dict | None = None,
    room_analysis: RoomAnalysis | None = None,
    design_strategy: DesignStrategy | None = None
) -> AISelectionResult:

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

    if not candidate_items:
        raise HTTPException(
            status_code=404,
            detail=(
                "No candidate products were provided "
                "to the AI selector."
            )
        )

    prompt = build_ai_selector_prompt(
        request=request,
        room_template=room_template,
        candidate_items=candidate_items,
        constraints=constraints,
        room_analysis=room_analysis,
        design_strategy=design_strategy
    )

    client = OpenAI(
        api_key=api_key
    )

    try:
        response = client.responses.parse(
            model=AI_SELECTOR_MODEL,

            input=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict interior product selection "
                        "engine. Select only real database product IDs "
                        "supplied by the backend. Respect every room "
                        "composition, relationship, category, and "
                        "quantity rule."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],

            text_format=AISelectionResult
        )

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "AI furniture selection failed: "
                f"{exc}"
            )
        ) from exc

    result = response.output_parsed

    if result is None:
        raise HTTPException(
            status_code=502,
            detail=(
                "AI selector did not return "
                "a valid structured response."
            )
        )

    return result
