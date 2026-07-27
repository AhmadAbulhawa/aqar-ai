import json
import re

from fastapi import HTTPException

from app.database import get_connection
from app.services.room_analysis_service import RoomAnalysis


# =========================================================
# JSON / row helpers
# =========================================================


def decode_json_list(value: str | None) -> list[str]:
    if not value:
        return []

    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [
                str(item).strip()
                for item in parsed
                if str(item).strip()
            ]

    except (json.JSONDecodeError, TypeError):
        pass

    return [
        item.strip()
        for item in str(value).split(",")
        if item.strip()
    ]


def row_to_design_template(row) -> dict:
    template = dict(row)

    template["tags"] = decode_json_list(
        template.get("tags")
    )

    template["is_active"] = bool(
        template.get("is_active")
    )

    for field in (
        "source_product_count",
        "matched_product_count",
        "skipped_product_count",
        "failed_product_count",
        "missing_product_count",
    ):
        template[field] = int(
            template.get(field) or 0
        )

    return template


# =========================================================
# Shared SQL projection for source-product import status
# =========================================================

_TEMPLATE_COUNTS_SQL = """
    (
        SELECT COUNT(*)
        FROM design_template_source_products dsp
        WHERE dsp.template_id = dt.id
    ) AS source_product_count,

    (
        SELECT COUNT(*)
        FROM design_template_source_products dsp
        WHERE dsp.template_id = dt.id
          AND dsp.matched_furniture_id IS NOT NULL
    ) AS matched_product_count,

    (
        SELECT COUNT(*)
        FROM design_template_source_products dsp
        WHERE dsp.template_id = dt.id
          AND dsp.match_status IN (
              'skipped_unsupported',
              'skipped_incomplete'
          )
    ) AS skipped_product_count,

    (
        SELECT COUNT(*)
        FROM design_template_source_products dsp
        WHERE dsp.template_id = dt.id
          AND dsp.match_status = 'failed'
    ) AS failed_product_count,

    (
        SELECT COUNT(*)
        FROM design_template_source_products dsp
        WHERE dsp.template_id = dt.id
          AND dsp.matched_furniture_id IS NULL
          AND dsp.match_status NOT IN (
              'skipped_unsupported',
              'skipped_incomplete',
              'failed'
          )
    ) AS missing_product_count
"""


# =========================================================
# Template loading
# =========================================================


def load_active_design_templates(
    room_type: str | None = None
) -> list[dict]:
    connection = get_connection()
    cursor = connection.cursor()

    try:
        if room_type is None:
            cursor.execute(
                f"""
                SELECT dt.*, {_TEMPLATE_COUNTS_SQL}
                FROM design_templates dt
                WHERE dt.is_active = 1
                ORDER BY dt.id ASC
                """
            )
        else:
            cursor.execute(
                f"""
                SELECT dt.*, {_TEMPLATE_COUNTS_SQL}
                FROM design_templates dt
                WHERE dt.room_type = ?
                  AND dt.is_active = 1
                ORDER BY dt.id ASC
                """,
                (room_type,)
            )

        rows = cursor.fetchall()

    finally:
        connection.close()

    return [
        row_to_design_template(row)
        for row in rows
    ]


def load_design_template(
    template_id: int
) -> dict:
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            f"""
            SELECT
                dt.*,
                {_TEMPLATE_COUNTS_SQL}
            FROM design_templates dt
            WHERE dt.id = ?
            """,
            (template_id,)
        )

        row = cursor.fetchone()

    finally:
        connection.close()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Design template not found"
        )

    return row_to_design_template(row)


def _template_catalog_product(row: dict, prefix: str = "") -> dict | None:
    product_id = row.get(f"{prefix}id")
    if product_id is None:
        return None
    return {
        "id": int(product_id),
        "name": row.get(f"{prefix}name"),
        "category": row.get(f"{prefix}category"),
        "style": row.get(f"{prefix}style"),
        "color": row.get(f"{prefix}color"),
        "material": row.get(f"{prefix}material"),
        "width_cm": row.get(f"{prefix}width_cm"),
        "depth_cm": row.get(f"{prefix}depth_cm"),
        "height_cm": row.get(f"{prefix}height_cm"),
        "diameter_cm": row.get(f"{prefix}diameter_cm"),
        "price": float(row.get(f"{prefix}price") or 0),
        "currency": row.get(f"{prefix}currency"),
        "image_filename": row.get(f"{prefix}image_filename"),
        "image_url": (
            f"/uploads/furniture/{row.get(f'{prefix}image_filename')}"
            if row.get(f"{prefix}image_filename")
            else None
        ),
        "product_url": row.get(f"{prefix}product_url"),
        "store_name": row.get(f"{prefix}store_name"),
        "availability": bool(row.get(f"{prefix}availability")),
        "catalog_status": row.get(f"{prefix}catalog_status"),
        "design_eligible": bool(row.get(f"{prefix}design_eligible", True)),
        "mounting_type": row.get(f"{prefix}mounting_type"),
        "is_product_set": bool(row.get(f"{prefix}is_product_set", False)),
    }


def load_design_template_detail(template_id: int) -> dict:
    """Return read-only template, persisted slots, and real catalog matches."""

    template = load_design_template(template_id)
    connection = get_connection()
    cursor = connection.cursor()
    product_fields = """
        f.id AS product_id,
        f.name AS product_name,
        f.category AS product_category,
        f.style AS product_style,
        f.color AS product_color,
        f.material AS product_material,
        f.width_cm AS product_width_cm,
        f.depth_cm AS product_depth_cm,
        f.height_cm AS product_height_cm,
        f.diameter_cm AS product_diameter_cm,
        f.price AS product_price,
        f.currency AS product_currency,
        f.image_filename AS product_image_filename,
        f.product_url AS product_product_url,
        f.store_name AS product_store_name,
        f.availability AS product_availability,
        f.catalog_status AS product_catalog_status,
        f.design_eligible AS product_design_eligible,
        f.mounting_type AS product_mounting_type,
        f.is_product_set AS product_is_product_set
    """
    try:
        slot_rows = cursor.execute(
            f"""
            SELECT dtp.*, {product_fields}
            FROM design_template_products dtp
            LEFT JOIN furniture f ON f.id = dtp.exact_product_id
            WHERE dtp.template_id = ?
            ORDER BY dtp.id ASC
            """,
            (template_id,),
        ).fetchall()
        source_rows = cursor.execute(
            """
            SELECT id, source_product_name, source_product_url,
                   matched_furniture_id, match_status, matched_by
            FROM design_template_source_products
            WHERE template_id = ?
            ORDER BY id ASC
            """,
            (template_id,),
        ).fetchall()
    finally:
        connection.close()

    represented_products = []
    available_products: dict[int, dict] = {}
    unresolved_slots = []
    total = 0.0
    currencies: set[str] = set()
    for row in slot_rows:
        item = dict(row)
        product = _template_catalog_product(item, "product_")
        quantity = int(item.get("quantity") or 1)
        represented_products.append(
            {
                "slot_id": int(item["id"]),
                "category": item.get("category"),
                "quantity": quantity,
                "role": item.get("role"),
                "priority": item.get("priority"),
                "preferred_style": item.get("preferred_style"),
                "preferred_colors": decode_json_list(item.get("preferred_colors")),
                "matched_product": product,
            }
        )
        if (
            product is None
            or not product["availability"]
            or product["catalog_status"] != "active"
            or not product["design_eligible"]
        ):
            unresolved_slots.append(
                {
                    "type": "template_slot",
                    "slot_id": int(item["id"]),
                    "category": item.get("category"),
                    "quantity": quantity,
                    "reason": "No active exact catalog product is linked to this template slot.",
                }
            )
            continue
        available_products[product["id"]] = product
        total += product["price"] * quantity
        if product.get("currency"):
            currencies.add(str(product["currency"]))

    for row in source_rows:
        item = dict(row)
        if item.get("matched_furniture_id") is None:
            unresolved_slots.append(
                {
                    "type": "source_product",
                    "source_product_id": int(item["id"]),
                    "name": item.get("source_product_name"),
                    "source_url": item.get("source_product_url"),
                    "match_status": item.get("match_status"),
                    "reason": "No catalog product is currently matched to this source item.",
                }
            )

    preview_filename = template.get("preview_image_filename")
    return {
        **template,
        "preview_image_url": (
            f"/uploads/design_templates/{preview_filename}"
            if preview_filename
            else None
        ),
        "represented_products": represented_products,
        "available_matched_catalog_products": list(available_products.values()),
        "unresolved_slots": unresolved_slots,
        "estimated_product_total": round(total, 2),
        "currency": next(iter(currencies)) if len(currencies) == 1 else None,
    }


def load_template_source_candidates(
    template_id: int
) -> list[dict]:
    """
    Return active source-linked candidate products for blueprint slot ranking.

    Output shape intentionally matches the rest of the Aqar AI product
    pipeline: id, name, category, dimensions, price, image_filename,
    quantity, and line_total are ready for Placement Planner and renderer.
    """

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                f.id AS id,
                f.name AS name,
                f.category AS category,
                f.style AS style,
                f.color AS color,
                f.material AS material,
                f.width_cm AS width_cm,
                f.depth_cm AS depth_cm,
                f.height_cm AS height_cm,
                f.diameter_cm AS diameter_cm,
                f.price AS price,
                f.image_filename AS image_filename,
                f.product_url AS product_url,
                f.store_name AS store_name,
                f.currency AS currency,
                f.room_types AS room_types,
                f.tags AS tags,
                f.availability AS availability,
                f.catalog_status AS catalog_status,
                f.quarantine_reason AS quarantine_reason,
                f.design_eligible AS design_eligible,
                f.mounting_type AS mounting_type,
                f.is_product_set AS is_product_set,
                dtp.quantity AS quantity,
                dtp.role AS template_role,
                dtp.priority AS template_priority,
                dtp.preferred_style AS template_preferred_style,
                dtp.preferred_colors AS template_preferred_colors
            FROM design_template_products dtp
            JOIN furniture f
              ON f.id = dtp.exact_product_id
            WHERE dtp.template_id = ?
              AND dtp.exact_product_id IS NOT NULL
              AND f.availability = 1
              AND COALESCE(f.catalog_status, 'active') = 'active'
              AND COALESCE(f.design_eligible, 1) = 1
            ORDER BY dtp.id ASC
            """,
            (template_id,)
        )

        rows = cursor.fetchall()

    finally:
        connection.close()

    products: list[dict] = []

    for row in rows:
        item = dict(row)

        item["room_types"] = decode_json_list(
            item.get("room_types")
        )

        item["tags"] = decode_json_list(
            item.get("tags")
        )

        item["template_preferred_colors"] = (
            decode_json_list(
                item.get("template_preferred_colors")
            )
        )

        item["availability"] = bool(
            item.get("availability")
        )
        item["design_eligible"] = bool(
            item.get("design_eligible", True)
        )
        item["is_product_set"] = bool(
            item.get("is_product_set", False)
        )

        item["quantity"] = int(
            item.get("quantity") or 1
        )

        item["selection_role"] = "template_source_candidate"

        item["line_total"] = round(
            float(item["price"])
            * item["quantity"],
            2
        )

        products.append(item)

    return products


def load_template_exact_products(template_id: int) -> list[dict]:
    """Deprecated compatibility alias; source mappings are not mandatory."""

    return load_template_source_candidates(template_id)


def load_template_source_products(
    template_id: int
) -> list[dict]:
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT *
            FROM design_template_source_products
            WHERE template_id = ?
            ORDER BY id ASC
            """,
            (template_id,)
        )

        rows = cursor.fetchall()

    finally:
        connection.close()

    return [
        dict(row)
        for row in rows
    ]


# =========================================================
# Normalization helpers for room/template matching
# =========================================================


_COLOR_FAMILIES = {
    "white": {
        "white",
        "off white",
        "off_white",
        "ivory",
        "cream"
    },
    "beige": {
        "beige",
        "sand",
        "tan"
    },
    "gray": {
        "gray",
        "grey",
        "light gray",
        "light grey",
        "dark gray",
        "dark grey"
    },
    "greige": {
        "greige",
        "grey beige",
        "gray beige"
    },
    "brown": {
        "brown",
        "warm brown",
        "medium brown",
        "warm medium brown"
    },
    "black": {"black", "charcoal", "anthracite"},
    "blue": {"blue", "navy", "turquoise"},
    "green": {"green", "olive", "sage"},
    "natural": {
        "natural",
        "natural wood",
        "wood",
        "oak",
        "walnut",
        "bamboo",
        "rattan"
    }
}


_FLOOR_MATERIAL_FAMILIES = {
    "wood": {
        "wood",
        "wood plank",
        "wood plank flooring",
        "hardwood",
        "parquet",
        "oak flooring"
    },
    "laminate": {"laminate"},
    "tile": {"tile", "ceramic", "porcelain"},
    "stone": {"stone", "marble", "granite"},
    "carpet": {"carpet", "wall to wall carpet"},
    "concrete": {"concrete", "cement"}
}


def normalize_text(value: str | None) -> str:
    if not value:
        return ""

    normalized = str(value).strip().lower()
    normalized = normalized.replace("_", " ")
    normalized = normalized.replace("grey", "gray")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def normalize_color_family(value: str | None) -> str:
    normalized = normalize_text(value)

    if not normalized:
        return ""

    for family, aliases in _COLOR_FAMILIES.items():
        normalized_aliases = {
            normalize_text(alias)
            for alias in aliases
        }

        if normalized in normalized_aliases:
            return family

        if any(
            alias in normalized
            for alias in normalized_aliases
            if len(alias) >= 4
        ):
            return family

    return normalized


def normalize_floor_material(value: str | None) -> str:
    normalized = normalize_text(value)

    if not normalized:
        return ""

    for family, aliases in _FLOOR_MATERIAL_FAMILIES.items():
        normalized_aliases = {
            normalize_text(alias)
            for alias in aliases
        }

        if normalized in normalized_aliases:
            return family

        if any(
            alias in normalized
            for alias in normalized_aliases
            if len(alias) >= 4
        ):
            return family

    return normalized


# =========================================================
# Scoring
# =========================================================


def score_design_template(
    template: dict,
    room_analysis: RoomAnalysis,
    style: str | None,
    preferred_colors: list[str]
) -> dict:
    """Score one template out of 100."""

    score = 0
    reasons: list[str] = []

    if template.get("room_type"):
        score += 30
        reasons.append("room type match")

    template_size = normalize_text(
        template.get("size_category")
    )
    analysis_size = normalize_text(
        room_analysis.room_size
    )

    if template_size and template_size == analysis_size:
        score += 20
        reasons.append("room size match")

    template_style = normalize_text(
        template.get("style")
    )
    requested_style = normalize_text(style)

    if (
        requested_style
        and template_style
        and template_style == requested_style
    ):
        score += 15
        reasons.append("style match")

    template_wall_color = normalize_color_family(
        template.get("wall_color_family")
    )
    analysis_wall_color = normalize_color_family(
        room_analysis.wall_color_family
    )

    if (
        template_wall_color
        and analysis_wall_color
        and template_wall_color == analysis_wall_color
    ):
        score += 10
        reasons.append("wall color family match")

    template_floor_color = normalize_color_family(
        template.get("floor_color_family")
    )
    analysis_floor_color = normalize_color_family(
        room_analysis.floor_color_family
    )

    if (
        template_floor_color
        and analysis_floor_color
        and template_floor_color == analysis_floor_color
    ):
        score += 5
        reasons.append("floor color family match")

    template_floor_material = normalize_floor_material(
        template.get("floor_material")
    )
    analysis_floor_material = normalize_floor_material(
        room_analysis.floor_material
    )

    if (
        template_floor_material
        and analysis_floor_material
        and template_floor_material == analysis_floor_material
    ):
        score += 5
        reasons.append("floor material match")

    template_light = normalize_text(
        template.get("natural_light_level")
    )
    analysis_light = normalize_text(
        room_analysis.natural_light_level
    )

    if (
        template_light
        and analysis_light
        and template_light == analysis_light
    ):
        score += 5
        reasons.append("natural light match")

    template_openness = normalize_text(
        template.get("room_openness")
    )
    analysis_openness = normalize_text(
        room_analysis.room_openness
    )

    if (
        template_openness
        and analysis_openness
        and template_openness == analysis_openness
    ):
        score += 5
        reasons.append("room openness match")

    template_tags = {
        normalize_text(tag)
        for tag in template.get("tags", [])
        if normalize_text(tag)
    }

    preferred_color_set = {
        normalize_color_family(color)
        for color in preferred_colors
        if normalize_color_family(color)
    }

    normalized_template_tag_colors = {
        normalize_color_family(tag)
        for tag in template_tags
        if normalize_color_family(tag)
    }

    tag_overlap = len(
        normalized_template_tag_colors
        & preferred_color_set
    )

    if tag_overlap:
        bonus = min(tag_overlap * 2, 5)
        score += bonus
        reasons.append("preferred color/tag overlap")

    return {
        "template": template,
        "score": min(score, 100),
        "reasons": reasons
    }


def match_design_templates(
    room_analysis: RoomAnalysis,
    room_type: str,
    style: str | None,
    preferred_colors: list[str],
    limit: int = 5
) -> list[dict]:
    if limit < 1:
        raise HTTPException(
            status_code=422,
            detail="limit must be at least 1"
        )

    templates = load_active_design_templates(
        room_type=room_type
    )

    if not templates:
        raise HTTPException(
            status_code=404,
            detail=(
                "No active design templates found for this room type. "
                "Import or create templates first."
            )
        )

    scored_templates = [
        score_design_template(
            template=template,
            room_analysis=room_analysis,
            style=style,
            preferred_colors=preferred_colors
        )
        for template in templates
    ]

    scored_templates.sort(
        key=lambda item: (
            -item["score"],
            int(item["template"]["id"])
        )
    )

    return scored_templates[:limit]
