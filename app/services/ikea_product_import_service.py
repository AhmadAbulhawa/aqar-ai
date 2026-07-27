from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

from PIL import Image

try:
    import pillow_avif  # noqa: F401
except ImportError:
    pillow_avif = None
from playwright.sync_api import BrowserContext, Page

from app.database import get_connection


# =========================================================
# Paths / source metadata
# =========================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FURNITURE_UPLOAD_DIR = (
    PROJECT_ROOT
    / "uploads"
    / "furniture"
)

FURNITURE_UPLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True
)

SOURCE_NAME = "IKEA Jordan"
DEFAULT_ROOM_TYPE = "living_room"


# =========================================================
# Supported MVP categories
#
# Only products that map confidently to one of these existing
# FurnitureCategory values are inserted into furniture.
# =========================================================

SUPPORTED_CATEGORY_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    (
        "tv_stand",
        (
            "tv bench",
            "tv unit",
            "tv storage combination",
            "media unit",
        ),
    ),
    (
        "coffee_table",
        (
            "coffee table",
            "nest of tables",
            "nesting table",
        ),
    ),
    (
        "side_table",
        (
            "side table",
            "tray table",
            "laptop stand",
        ),
    ),
    (
        "floor_lamp",
        (
            "floor lamp",
            "reading lamp",
        ),
    ),
    (
        "wall_art",
        (
            "wall decoration",
            "picture",
            "poster",
            "canvas",
            "wall art",
        ),
    ),
    (
        "wall_shelf",
        (
            "wall shelf",
            "wall-mounted shelf",
        ),
    ),
    (
        "bookshelf",
        (
            "bookcase",
            "shelving unit",
            "shelf unit",
        ),
    ),
    (
        "sideboard",
        (
            "sideboard",
            "console table",
        ),
    ),
    (
        "storage_cabinet",
        (
            "storage cabinet",
            "cabinet with doors",
            "cabinet with sliding doors",
            "cabinet",
            "storage combination",
        ),
    ),
    (
        "sofa",
        (
            "sofa-bed",
            "sofa bed",
            "corner sofa",
            "sectional sofa",
            "modular sofa",
            "3-seat sofa",
            "2-seat sofa",
            "4-seat sofa",
            "5-seat sofa",
            "6-seat sofa",
            "sofa",
        ),
    ),
    (
        "armchair",
        (
            "armchair",
            "easy chair",
            "chaise longue",
            "chaise lounge",
        ),
    ),
    (
        "rug",
        (
            "rug",
            "door mat",
            "doormat",
        ),
    ),
    (
        "mirror",
        (
            "mirror",
        ),
    ),
]


# Products intentionally excluded from the current MVP furniture catalog.
# These are checked against the exact product name BEFORE any breadcrumb or
# category inference so unrelated navigation text can never misclassify them.
UNSUPPORTED_PRODUCT_PATTERNS: tuple[str, ...] = (
    "tea towel",
    "dish towel",
    "kitchen towel",
    "duvet cover",
    "pillowcase",
    "cushion cover",
    "cushion",
    "pillow",
    "throw",
    "blanket",
    "bedspread",
    "pouffe",
    "ottoman",
    "footstool",
    "vase",
    "bowl",
    "tray",
    "candlestick",
    "tealight holder",
    "plant pot",
    "flowerpot",
    "table lamp",
    "pendant lamp",
    "ceiling lamp",
)


CATEGORY_DIMENSION_RANGES_CM: dict[
    str,
    dict[str, tuple[float, float]],
] = {
    "sofa": {
        "width_cm": (70, 500),
        "depth_cm": (45, 250),
        "height_cm": (25, 180),
    },
    "armchair": {
        "width_cm": (35, 180),
        "depth_cm": (40, 180),
        "height_cm": (35, 180),
    },
    "coffee_table": {
        "width_cm": (25, 300),
        "depth_cm": (20, 200),
        "height_cm": (15, 100),
    },
    "rug": {
        "width_cm": (30, 1000),
        "depth_cm": (30, 1500),
        "height_cm": (0.1, 15),
    },
}


# =========================================================
# Models
# =========================================================


@dataclass
class IkeaProductMetadata:
    source_url: str
    article_key: str
    name: str
    category: str | None
    style: str
    color: str
    material: str | None
    width_cm: float | None
    depth_cm: float | None
    height_cm: float | None
    price: float | None
    currency: str
    image_url: str | None
    description: str | None
    room_types: list[str]
    tags: list[str]


@dataclass
class ProductImportResult:
    source_url: str
    status: str
    reason: str | None = None
    furniture_id: int | None = None
    furniture_name: str | None = None
    category: str | None = None
    image_filename: str | None = None
    metadata: dict[str, Any] | None = None


# =========================================================
# URL helpers
# =========================================================


def normalize_url(raw_url: str) -> str:
    parsed = urlparse(raw_url.strip())

    normalized_path = re.sub(
        r"/+",
        "/",
        parsed.path,
    )

    if normalized_path != "/":
        normalized_path = normalized_path.rstrip("/") + "/"

    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=normalized_path,
        params="",
        query="",
        fragment="",
    )

    return urlunparse(normalized)



def canonical_ikea_product_key(raw_url: str) -> str:
    normalized = normalize_url(raw_url)
    path = urlparse(normalized).path.rstrip("/")

    last_segment = path.split("/")[-1] if path else ""

    match = re.search(
        r"(?:-|^)([a-z]?\d{8})$",
        last_segment,
        flags=re.IGNORECASE,
    )

    if match:
        return match.group(1).lower()

    return normalized.lower()


# =========================================================
# Generic helpers
# =========================================================


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = " ".join(str(value).split()).strip()
    return cleaned or None



def normalize_keyword_text(value: str | None) -> str:
    return (clean_text(value) or "").lower()



def parse_number(value: Any) -> float | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    cleaned = str(value).strip().replace(",", "")

    match = re.search(
        r"-?\d+(?:\.\d+)?",
        cleaned,
    )

    if not match:
        return None

    try:
        return float(match.group(0))
    except ValueError:
        return None



def unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    for value in values:
        cleaned = clean_text(value)

        if not cleaned:
            continue

        lowered = cleaned.lower()

        if lowered in seen:
            continue

        seen.add(lowered)
        result.append(cleaned)

    return result


# =========================================================
# Category / style / color helpers
# =========================================================


def infer_supported_category(
    product_name: str,
    breadcrumb_text: str | None = None,
    structured_category_text: str | None = None,
) -> str | None:
    """
    Map an IKEA product to one existing MVP FurnitureCategory.

    Safety against false positives:
    - exact product-name exclusions win first,
    - product name is checked before broader category metadata,
    - only trusted breadcrumb/structured category text is used,
    - generic page navigation links are never used for classification.
    """

    normalized_name = normalize_keyword_text(product_name)

    if any(
        pattern in normalized_name
        for pattern in UNSUPPORTED_PRODUCT_PATTERNS
    ):
        return None

    # Highest-confidence source: exact product name.
    for category, patterns in SUPPORTED_CATEGORY_PATTERNS:
        for pattern in patterns:
            if pattern in normalized_name:
                return category

    # Lower-confidence, but still trusted, category metadata.
    trusted_context = " ".join(
        part
        for part in [
            normalize_keyword_text(structured_category_text),
            normalize_keyword_text(breadcrumb_text),
        ]
        if part
    )

    for category, patterns in SUPPORTED_CATEGORY_PATTERNS:
        for pattern in patterns:
            if pattern in trusted_context:
                return category

    return None



def infer_style(
    name: str,
    description: str | None,
    breadcrumb_text: str | None,
) -> str:
    searchable = " ".join(
        part
        for part in [
            normalize_keyword_text(name),
            normalize_keyword_text(description),
            normalize_keyword_text(breadcrumb_text),
        ]
        if part
    )

    if any(
        keyword in searchable
        for keyword in (
            "classic",
            "traditional",
            "timeless",
        )
    ):
        return "classic"

    if any(
        keyword in searchable
        for keyword in (
            "luxury",
            "luxurious",
            "glam",
            "premium",
            "elegant",
        )
    ):
        return "luxury"

    if any(
        keyword in searchable
        for keyword in (
            "minimal",
            "minimalist",
            "scandinavian",
            "scandi",
            "japandi",
            "clean-lined",
            "clean lines",
        )
    ):
        return "minimal"

    return "modern"



def infer_color(
    product_name: str,
    json_ld_color: str | None,
) -> str:
    explicit = clean_text(json_ld_color)

    if explicit:
        return explicit.lower()

    # IKEA product names normally use comma-separated variant text.
    # Example:
    # BÅRSLÖV 3-seat sofa-bed with chaise longue, Tibbleby beige/grey
    parts = [
        clean_text(part)
        for part in product_name.split(",")
    ]

    parts = [
        part
        for part in parts
        if part
    ]

    if len(parts) >= 2:
        candidate = parts[-1]

        # Remove common trailing measurements.
        candidate = re.sub(
            r"\b\d+(?:[.,]\d+)?\s*x\s*\d+(?:[.,]\d+)?(?:\s*x\s*\d+(?:[.,]\d+)?)?\s*cm\b",
            "",
            candidate,
            flags=re.IGNORECASE,
        )

        candidate = clean_text(candidate)

        if candidate:
            words = candidate.split()

            # Keep the final few words because IKEA may prefix the fabric
            # series name before the actual color.
            if len(words) > 4:
                candidate = " ".join(words[-4:])

            return candidate.lower()

    return "unknown"


# =========================================================
# JSON-LD extraction
# =========================================================


def flatten_json_ld_nodes(value: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []

    if isinstance(value, dict):
        nodes.append(value)

        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                nodes.extend(
                    flatten_json_ld_nodes(item)
                )

    elif isinstance(value, list):
        for item in value:
            nodes.extend(
                flatten_json_ld_nodes(item)
            )

    return nodes



def extract_product_json_ld(
    page: Page,
) -> dict[str, Any] | None:
    scripts = page.locator(
        'script[type="application/ld+json"]'
    )

    for index in range(scripts.count()):
        raw = scripts.nth(index).text_content()

        if not raw:
            continue

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue

        for node in flatten_json_ld_nodes(parsed):
            node_type = node.get("@type")

            if isinstance(node_type, list):
                type_values = {
                    str(item).lower()
                    for item in node_type
                }
            else:
                type_values = {
                    str(node_type).lower()
                }

            if "product" in type_values:
                return node

    return None



def extract_offer_data(
    product_json_ld: dict[str, Any] | None,
) -> tuple[float | None, str]:
    if not product_json_ld:
        return None, "JOD"

    offers = product_json_ld.get("offers")

    offer: dict[str, Any] | None = None

    if isinstance(offers, dict):
        offer = offers
    elif isinstance(offers, list):
        offer = next(
            (
                item
                for item in offers
                if isinstance(item, dict)
            ),
            None,
        )

    if not offer:
        return None, "JOD"

    price = parse_number(
        offer.get("price")
        or offer.get("lowPrice")
    )

    currency = clean_text(
        offer.get("priceCurrency")
    ) or "JOD"

    return price, currency.upper()



def extract_json_ld_image_url(
    product_json_ld: dict[str, Any] | None,
) -> str | None:
    if not product_json_ld:
        return None

    image_value = product_json_ld.get("image")

    if isinstance(image_value, str):
        return image_value

    if isinstance(image_value, list):
        for item in image_value:
            if isinstance(item, str) and item.strip():
                return item

            if isinstance(item, dict):
                candidate = (
                    item.get("url")
                    or item.get("contentUrl")
                )

                if candidate:
                    return str(candidate)

    if isinstance(image_value, dict):
        candidate = (
            image_value.get("url")
            or image_value.get("contentUrl")
        )

        if candidate:
            return str(candidate)

    return None


# =========================================================
# DOM text extraction
# =========================================================


def extract_breadcrumb_text(
    page: Page,
) -> str | None:
    """Return only real breadcrumb UI text.

    Do NOT fall back to arbitrary links from <main>; doing that can inject
    unrelated words such as sofa/rug into category inference and cause false
    imports of unsupported products.
    """

    candidates = [
        'nav[aria-label*="breadcrumb" i]',
        '[data-testid*="breadcrumb" i]',
        'ol[class*="breadcrumb" i]',
    ]

    for selector in candidates:
        locator = page.locator(selector).first

        try:
            if locator.count() > 0:
                value = clean_text(locator.text_content())
                if value:
                    return value
        except Exception:
            continue

    return None


def extract_structured_category_text(
    product_json_ld: dict[str, Any] | None,
) -> str | None:
    if not product_json_ld:
        return None

    candidates: list[str] = []

    for key in (
        "category",
        "additionalType",
        "productGroupID",
    ):
        raw = product_json_ld.get(key)

        if isinstance(raw, str):
            candidates.append(raw)
        elif isinstance(raw, list):
            candidates.extend(
                str(item)
                for item in raw
                if str(item).strip()
            )

    return clean_text(" ".join(candidates))



def _contains_dimension_pair(
    text: str | None,
) -> bool:
    if not text:
        return False

    return bool(
        re.search(
            r"\b(?:Width|Depth|Height|Length|Diameter|Thickness|Pile thickness|"
            r"Max\.?\s+depth|Max\.?\s+width|Max\.?\s+height|Total depth)\b"
            r"\s*:?\s*[\d.,]+\s*(?:cm|mm)\b",
            text.replace("\u00a0", " "),
            flags=re.IGNORECASE,
        )
    )


def _measurement_block_from_text(
    raw_text: str | None,
) -> str | None:
    """Return only the product Measurements block from a larger text blob."""

    if not raw_text:
        return None

    normalized = (
        str(raw_text)
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u00a0", " ")
    )

    stop_pattern = (
        r"(?:Packaging|Assembly(?: and documents)?|Safety(?: and compliance)?|"
        r"Related products|Accessories for|Explore more options|Recommended for you|Footer)"
    )

    # Prefer line-aware slicing so package dimensions never leak into product dimensions.
    match = re.search(
        rf"(?:^|\n)\s*Measurements\s*(?P<body>.*?)(?=(?:\n)\s*{stop_pattern}\b|\Z)",
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if match:
        body = clean_text(match.group("body"))

        if body and _contains_dimension_pair(body):
            return body

    # Some IKEA page variants flatten disclosure text into one line.
    flattened = clean_text(normalized) or ""

    match = re.search(
        rf"\bMeasurements\b(?P<body>.*?)(?=\b{stop_pattern}\b|\Z)",
        flattened,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if match:
        body = clean_text(match.group("body"))

        if body and _contains_dimension_pair(body):
            return body

    return None


def _measurements_are_loaded(
    page: Page,
) -> bool:
    """Check whether the product Measurements disclosure has actually loaded."""

    selectors = (
        '[role="dialog"]',
        '[role="tabpanel"]',
        '[data-testid*="measurement" i]',
        '[id*="measurement" i]',
        '[class*="measurement" i]',
        'main',
        'body',
    )

    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 20)

            for index in range(count):
                node = locator.nth(index)

                try:
                    raw = node.inner_text(timeout=1000)
                except Exception:
                    try:
                        raw = node.text_content(timeout=1000)
                    except Exception:
                        raw = None

                if _measurement_block_from_text(raw):
                    return True
        except Exception:
            continue

    return False


def expand_measurements_section(
    page: Page,
) -> bool:
    """Open IKEA's Measurements disclosure/tab and wait for dimension data.

    IKEA Jordan may render Measurements as a tab, accordion button, disclosure,
    or lazily-loaded dialog. A simple body.inner_text() before activating the
    control only returns the heading "Measurements" and no values.

    Returns True when a real product-dimension block becomes available.
    """

    if _measurements_are_loaded(page):
        return True

    # -----------------------------------------------------
    # 1. Semantic role-based controls.
    # -----------------------------------------------------

    controls = []

    try:
        controls.append(
            page.get_by_role(
                "button",
                name=re.compile(r"^Measurements$", re.IGNORECASE),
            )
        )
    except Exception:
        pass

    for selector in (
        'button:has-text("Measurements")',
        '[role="button"]:has-text("Measurements")',
        '[aria-label*="Measurements" i]',
        '[aria-controls*="measurement" i]',
    ):
        try:
            controls.append(page.locator(selector))
        except Exception:
            continue

    for locator in controls:
        try:
            count = min(locator.count(), 10)
        except Exception:
            continue

        for index in range(count):
            control = locator.nth(index)

            try:
                if not control.is_visible():
                    continue

                control.scroll_into_view_if_needed(timeout=2500)

                # IKEA cookie layers or sticky UI can intercept a normal click.
                # Force click is safe here because we target an exact Measurements control.
                control.click(timeout=3500, force=True)
                page.wait_for_timeout(1200)

                if _measurements_are_loaded(page):
                    return True
            except Exception:
                continue

    # -----------------------------------------------------
    # 2. Exact visible text -> nearest ancestor button.
    # -----------------------------------------------------

    try:
        labels = page.get_by_text(
            re.compile(r"^Measurements$", re.IGNORECASE),
            exact=True,
        )

        count = min(labels.count(), 10)

        for index in range(count):
            label = labels.nth(index)

            try:
                if not label.is_visible():
                    continue

                button = label.locator(
                    "xpath=ancestor-or-self::button[1]"
                )

                if button.count() == 0:
                    continue

                button.scroll_into_view_if_needed(timeout=2500)
                button.click(timeout=3500, force=True)
                page.wait_for_timeout(1200)

                if _measurements_are_loaded(page):
                    return True
            except Exception:
                continue
    except Exception:
        pass

    # -----------------------------------------------------
    # 3. Browser-JS fallback.
    #
    # This handles IKEA variants where Playwright selectors see the label,
    # but the actionable element is wrapped in custom markup.
    # -----------------------------------------------------

    try:
        clicked = page.evaluate(
            r"""
            () => {
                const candidates = Array.from(
                    document.querySelectorAll(
                        'button, [role="button"], summary, a'
                    )
                );

                const control = candidates.find((node) => {
                    const text = (node.innerText || node.textContent || '')
                        .replace(/\s+/g, ' ')
                        .trim();

                    return /^Measurements$/i.test(text);
                });

                if (!control) return false;

                control.scrollIntoView({block: 'center'});
                control.click();
                return true;
            }
            """
        )

        if clicked:
            page.wait_for_timeout(1500)

            if _measurements_are_loaded(page):
                return True
    except Exception:
        pass

    return False


def extract_measurements_section_text(
    page: Page,
) -> str | None:
    """Extract exact product dimensions while excluding package dimensions."""

    expand_measurements_section(page)

    text_candidates: list[str] = []

    # Read the most specific containers first. IKEA may inject measurements
    # into a dialog/tab panel only after the Measurements control is activated.
    selectors = (
        '[role="dialog"]',
        '[role="tabpanel"]',
        '[data-testid*="measurement" i]',
        '[id*="measurement" i]',
        '[class*="measurement" i]',
        'main',
        'body',
    )

    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 25)

            for index in range(count):
                node = locator.nth(index)

                for getter_name in ("inner_text", "text_content"):
                    try:
                        raw = getattr(node, getter_name)(timeout=1500)
                    except TypeError:
                        try:
                            raw = getattr(node, getter_name)()
                        except Exception:
                            raw = None
                    except Exception:
                        raw = None

                    if raw:
                        text_candidates.append(str(raw))
        except Exception:
            continue

    # DOM fallback: inspect the section around an exact Measurements heading.
    try:
        dom_slice = page.evaluate(
            r"""
            () => {
                const root = document.querySelector('main') || document.body;
                if (!root) return null;

                const nodes = Array.from(
                    root.querySelectorAll(
                        'h1, h2, h3, h4, button, [role="button"], section, div'
                    )
                );

                const start = nodes.find((node) => {
                    const text = (node.innerText || node.textContent || '')
                        .replace(/\s+/g, ' ')
                        .trim();

                    return /^Measurements$/i.test(text);
                });

                if (!start) return null;

                const section = start.closest(
                    'section, article, [role="dialog"], [role="tabpanel"], [data-testid], div'
                );

                if (section) {
                    const sectionText = section.innerText || section.textContent || '';

                    if (/\b(?:Width|Depth|Height|Length|Diameter|Thickness)\b\s*:?\s*[\d.,]+\s*(?:cm|mm)\b/i.test(sectionText)) {
                        return sectionText;
                    }
                }

                const rootText = root.innerText || root.textContent || '';
                const match = rootText.match(
                    /\bMeasurements\b([\s\S]*?)(?=\bPackaging\b|\bAccessories for\b|\bRelated products\b|$)/i
                );

                return match ? match[1] : null;
            }
            """
        )

        if dom_slice:
            text_candidates.append(str(dom_slice))
    except Exception:
        pass

    for raw_text in text_candidates:
        block = _measurement_block_from_text(raw_text)

        if block:
            return block

    return None

def extract_material_section_text(
    page: Page,
) -> str | None:
    try:
        main_text = page.locator("main").inner_text()
    except Exception:
        return None

    normalized = main_text.replace("\r\n", "\n")

    match = re.search(
        r"(?:^|\n)Material\s*\n(?P<body>.*?)(?=\n(?:Care|Safety and compliance|Assembly and documents|Measurements)\b|\Z)",
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if not match:
        return None

    body = clean_text(match.group("body"))

    if not body:
        return None

    # Keep a compact material summary for the furniture DB.
    return body[:500]



def extract_page_description(
    page: Page,
    product_json_ld: dict[str, Any] | None,
) -> str | None:
    if product_json_ld:
        description = clean_text(
            product_json_ld.get("description")
        )

        if description:
            return description

    try:
        paragraphs = page.locator("main p").all_inner_texts()
    except Exception:
        return None

    for paragraph in paragraphs:
        cleaned = clean_text(paragraph)

        if cleaned and len(cleaned) >= 40:
            return cleaned

    return None


# =========================================================
# Measurement parsing
# =========================================================



def normalize_measurement_text_for_parsing(
    text: str,
) -> str:
    """Normalize IKEA measurement text before regex parsing.

    IKEA sometimes returns accordion text flattened like:
    "Width:236 cmDepth:153 cmHeight:90 cm".

    This function makes label/value pairs readable without changing the
    actual values. It also strips package-related text before parsing.
    """

    normalized = (
        text
        .replace("\u00a0", " ")
        .replace("\r", "\n")
        .replace("：", ":")
    )

    # Add spacing when a unit is directly followed by a new label.
    normalized = re.sub(
        r"(?i)(cm|mm)(?=(?:Width|Depth|Height|Length|Diameter|Thickness|Pile|Max|Min|Total|Frame))",
        r"\1 ",
        normalized,
    )

    # Add spacing when labels are glued after numbers.
    normalized = re.sub(
        r"(?i)(\d)(?=(?:Width|Depth|Height|Length|Diameter|Thickness|Pile|Max|Min|Total|Frame))",
        r"\1 ",
        normalized,
    )

    # Keep product measurements only. Package dimensions must never leak into
    # furniture dimensions.
    normalized = re.split(
        r"(?i)\b(?:Packaging|Package\s+measurement|Package\s+measurements|Assembly(?: and documents)?|Safety(?: and compliance)?)\b",
        normalized,
        maxsplit=1,
    )[0]

    return normalized


def parse_cm_measurement(
    text: str,
    labels: tuple[str, ...],
) -> float | None:
    """Read the first centimetre/millimetre value for one of the labels.

    The parser intentionally accepts text between label and value because IKEA
    often writes labels such as "Height including back cushions" or
    "Depth including chaise longue" instead of plain "Height" / "Depth".
    """

    normalized = normalize_measurement_text_for_parsing(
        text
    )

    label_patterns: list[str] = []

    for label in labels:
        escaped = re.escape(label).replace(r"\.\ ", r"\.?\s+")
        label_patterns.append(escaped)

    # Some pages use descriptive variants that should still map to the same
    # physical field. These are intentionally product-measurement terms, not
    # package terms.
    expanded_labels: list[str] = list(label_patterns)

    if any("Depth" in label or "depth" in label for label in labels):
        expanded_labels.extend([
            r"Depth(?:\s+including\s+chaise\s+longue)?",
            r"Depth(?:\s+including\s+cushions)?",
            r"Depth(?:\s+of\s+chaise\s+longue)?",
            r"Total\s+depth",
            r"Overall\s+depth",
            r"Max\.?\s+depth",
            r"Min\.?\s+depth",
            r"Frame,?\s+depth",
        ])

    if any("Height" in label or "height" in label for label in labels):
        expanded_labels.extend([
            r"Height(?:\s+including\s+back\s+cushions)?",
            r"Height(?:\s+including\s+cushions)?",
            r"Back\s+height",
            r"Total\s+height",
            r"Overall\s+height",
            r"Max\.?\s+height",
            r"Min\.?\s+height",
        ])

    if any("Width" in label or "width" in label for label in labels):
        expanded_labels.extend([
            r"Total\s+width",
            r"Overall\s+width",
            r"Max\.?\s+width",
            r"Min\.?\s+width",
        ])

    if any("Length" in label or "length" in label for label in labels):
        expanded_labels.extend([
            r"Total\s+length",
            r"Overall\s+length",
            r"Max\.?\s+length",
            r"Min\.?\s+length",
        ])

    if any("Diameter" in label or "diameter" in label for label in labels):
        expanded_labels.extend([
            r"Diameter",
            r"Max\.?\s+diameter",
        ])

    # Prefer exact/local label-value pairs.
    label_union = "|".join(f"(?:{pattern})" for pattern in expanded_labels)

    pattern = re.compile(
        rf"(?<![A-Za-z])(?:{label_union})(?![A-Za-z])"
        rf"(?:\s|:|\-|–|—|/|,|\.)*"
        rf"(?:[A-Za-z][A-Za-z\s,./\-–—]{{0,80}}?)?"
        rf"\s*:?\s*"
        rf"([\d.,]+)\s*(cm|mm)\b",
        flags=re.IGNORECASE,
    )

    for match in pattern.finditer(normalized):
        # Avoid seat measurements for global product footprint.
        local_start = max(0, match.start() - 24)
        local_prefix = normalized[local_start:match.start()].lower()
        matched_label = match.group(0).lower()

        if "seat" in local_prefix or matched_label.startswith("seat"):
            continue

        value = parse_number(match.group(1))

        if value is None:
            continue

        unit = match.group(2).lower()

        if unit == "mm":
            value /= 10.0

        return value

    return None


def parse_name_dimensions(
    product_name: str,
) -> list[float]:
    """Extract a trailing IKEA-style 2D/3D dimension expression from h1."""

    match = re.search(
        r"\b([\d.,]+)\s*[x×]\s*([\d.,]+)"
        r"(?:\s*[x×]\s*([\d.,]+))?\s*cm\b",
        product_name,
        flags=re.IGNORECASE,
    )

    if not match:
        return []

    values: list[float] = []

    for raw in match.groups():
        if raw is None:
            continue

        value = parse_number(raw)

        if value is not None:
            values.append(value)

    return values



def parse_dimensions(
    category: str,
    measurements_text: str | None,
    product_name: str,
) -> tuple[float | None, float | None, float | None]:
    """Map IKEA measurements into the project's width/depth/height schema.

    Important category semantics:
    - sofa/cabinet/bookcase: Width × Depth × Height
    - rectangular tables: Length × Width × Height -> width × depth × height
    - round tables: Diameter × Diameter × Height
    - rugs: Width × Length × thickness (or 1 cm technical fallback)
    - mirrors/wall art: Width × thickness/depth × Height

    No fake 1×1×1 fallback is used for real furniture.
    """

    text = measurements_text or ""

    measured_width = parse_cm_measurement(
        text,
        (
            "Width",
            "Max. width",
            "Max width",
            "Maximum width",
        ),
    )

    measured_depth = parse_cm_measurement(
        text,
        (
            "Depth",
            "Max. depth",
            "Max depth",
            "Maximum depth",
            "Total depth",
        ),
    )

    measured_height = parse_cm_measurement(
        text,
        (
            "Height",
            "Max. height",
            "Max height",
            "Maximum height",
        ),
    )

    length = parse_cm_measurement(
        text,
        (
            "Length",
        ),
    )

    diameter = parse_cm_measurement(
        text,
        (
            "Diameter",
        ),
    )

    thickness = parse_cm_measurement(
        text,
        (
            "Thickness",
            "Pile thickness",
            "Frame, depth",
        ),
    )

    name_dimensions = parse_name_dimensions(
        product_name
    )

    width: float | None = measured_width
    depth: float | None = measured_depth
    height: float | None = measured_height

    if category == "rug":
        width = width or (
            name_dimensions[0]
            if len(name_dimensions) >= 1
            else None
        )

        depth = length or depth or (
            name_dimensions[1]
            if len(name_dimensions) >= 2
            else None
        )

        height = thickness or height or 1.0

    elif category in {
        "wall_art",
        "mirror",
    }:
        width = width or (
            name_dimensions[0]
            if len(name_dimensions) >= 1
            else None
        )

        height = height or length or (
            name_dimensions[1]
            if len(name_dimensions) >= 2
            else None
        )

        depth = depth or thickness or 2.0

    elif category in {
        "side_table",
        "coffee_table",
    } and diameter is not None:
        width = diameter
        depth = diameter

    elif category in {
        "side_table",
        "coffee_table",
    }:
        if length is not None:
            width = length
            depth = measured_width or measured_depth

        if width is None and len(name_dimensions) >= 1:
            width = name_dimensions[0]

        if depth is None and len(name_dimensions) >= 2:
            depth = name_dimensions[1]

        if height is None and len(name_dimensions) >= 3:
            height = name_dimensions[2]

    elif category == "floor_lamp":
        width = width or diameter
        depth = depth or diameter or width

    elif category in {
        "bookshelf",
        "storage_cabinet",
        "sideboard",
        "tv_stand",
        "wall_shelf",
    }:
        if width is None and len(name_dimensions) >= 1:
            width = name_dimensions[0]

        if height is None and len(name_dimensions) >= 2:
            height = name_dimensions[1]

        if depth is None and len(name_dimensions) >= 3:
            depth = name_dimensions[1]
            height = name_dimensions[2]

    else:
        if width is None and length is not None:
            width = length

        if len(name_dimensions) >= 3:
            width = width or name_dimensions[0]
            depth = depth or name_dimensions[1]
            height = height or name_dimensions[2]

    return width, depth, height


# =========================================================
# Product metadata scraping
# =========================================================


def scrape_ikea_product_metadata(
    page: Page,
    product_url: str,
    room_type: str = DEFAULT_ROOM_TYPE,
) -> IkeaProductMetadata:
    normalized_url = normalize_url(product_url)

    page.goto(
        normalized_url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    page.wait_for_timeout(1200)

    try:
        page.wait_for_load_state(
            "networkidle",
            timeout=8000,
        )
    except Exception:
        # IKEA may keep analytics/background requests alive.
        pass

    h1 = page.locator("h1").first

    if h1.count() == 0:
        raise RuntimeError(
            "IKEA product page does not contain an h1 product name."
        )

    name = clean_text(h1.inner_text())

    if not name:
        raise RuntimeError(
            "Could not read IKEA product name."
        )

    product_json_ld = extract_product_json_ld(page)
    breadcrumb_text = extract_breadcrumb_text(page)
    structured_category_text = extract_structured_category_text(
        product_json_ld
    )

    category = infer_supported_category(
        product_name=name,
        breadcrumb_text=breadcrumb_text,
        structured_category_text=structured_category_text,
    )

    description = extract_page_description(
        page=page,
        product_json_ld=product_json_ld,
    )

    style = infer_style(
        name=name,
        description=description,
        breadcrumb_text=breadcrumb_text,
    )

    json_ld_color = None

    if product_json_ld:
        raw_color = product_json_ld.get("color")

        if isinstance(raw_color, list):
            json_ld_color = "/".join(
                str(item)
                for item in raw_color
                if str(item).strip()
            )
        elif raw_color is not None:
            json_ld_color = str(raw_color)

    color = infer_color(
        product_name=name,
        json_ld_color=json_ld_color,
    )

    material = extract_material_section_text(page)
    measurements_text = extract_measurements_section_text(page)

    width_cm, depth_cm, height_cm = parse_dimensions(
        category=category or "",
        measurements_text=measurements_text,
        product_name=name,
    )

    price, currency = extract_offer_data(
        product_json_ld
    )

    # Fallback to visible page text if JSON-LD did not provide the price.
    if price is None:
        try:
            main_text = page.locator("main").inner_text()
        except Exception:
            main_text = ""

        visible_price = re.search(
            r"\bJD\s*([\d.,]+)",
            main_text,
            flags=re.IGNORECASE,
        )

        if visible_price:
            price = parse_number(
                visible_price.group(1)
            )
            currency = "JOD"

    image_url = extract_json_ld_image_url(
        product_json_ld
    )

    if not image_url:
        try:
            meta = page.locator(
                'meta[property="og:image"]'
            ).first

            if meta.count() > 0:
                image_url = meta.get_attribute(
                    "content"
                )
        except Exception:
            image_url = None

    tags = build_product_tags(
        name=name,
        category=category,
        style=style,
        color=color,
        description=description,
    )

    return IkeaProductMetadata(
        source_url=normalized_url,
        article_key=canonical_ikea_product_key(
            normalized_url
        ),
        name=name,
        category=category,
        style=style,
        color=color,
        material=material,
        width_cm=width_cm,
        depth_cm=depth_cm,
        height_cm=height_cm,
        price=price,
        currency=currency,
        image_url=image_url,
        description=description,
        room_types=[room_type],
        tags=tags,
    )



def build_product_tags(
    name: str,
    category: str | None,
    style: str,
    color: str,
    description: str | None,
) -> list[str]:
    tags = [
        category or "",
        style,
        color,
        SOURCE_NAME.lower().replace(" ", "_"),
    ]

    searchable = " ".join(
        part
        for part in [
            normalize_keyword_text(name),
            normalize_keyword_text(description),
        ]
        if part
    )

    keyword_tags = [
        "corner_sofa",
        "sectional",
        "l_shape",
        "chaise_longue",
        "sofa_bed",
        "storage",
        "natural",
        "wood",
        "oak",
        "bamboo",
        "beige",
        "white",
        "grey",
        "black",
        "blue",
        "green",
        "warm",
        "neutral",
    ]

    for tag in keyword_tags:
        phrase = tag.replace("_", " ")

        if phrase in searchable:
            tags.append(tag)

    # A sofa with chaise longue is useful to the existing corner-sofa logic.
    if category == "sofa" and (
        "chaise longue" in searchable
        or "corner sofa" in searchable
        or "sectional" in searchable
    ):
        tags.extend([
            "corner_sofa",
            "sectional",
            "l_shape",
            "complete_seating_set",
        ])

    return [
        value.lower().replace(" ", "_")
        for value in unique_strings(tags)
        if value
    ]


# =========================================================
# Furniture DB helpers
# =========================================================


def find_existing_furniture(
    product_url: str,
) -> dict | None:
    normalized_url = normalize_url(product_url)
    product_key = canonical_ikea_product_key(product_url)

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT *
            FROM furniture
            """
        )

        rows = cursor.fetchall()

    finally:
        connection.close()

    for row in rows:
        item = dict(row)
        existing_url = str(
            item.get("product_url")
            or ""
        ).strip()

        if not existing_url:
            continue

        if normalize_url(existing_url) == normalized_url:
            return item

        if (
            canonical_ikea_product_key(existing_url)
            == product_key
        ):
            return item

    return None



def validate_metadata_for_insert(
    metadata: IkeaProductMetadata,
) -> str | None:
    if metadata.category is None:
        return (
            "unsupported_category: product does not map confidently "
            "to an existing MVP FurnitureCategory"
        )

    if metadata.price is None:
        return "missing_price"

    normalized_name = normalize_keyword_text(metadata.name)
    if metadata.category == "rug" and any(
        pattern in normalized_name
        for pattern in ("tea towel", "dish towel", "kitchen towel")
    ):
        return (
            "semantic_category_mismatch: household textile cannot be "
            "stored as a rug"
        )

    missing_dimensions = [
        field_name
        for field_name, value in (
            ("width_cm", metadata.width_cm),
            ("depth_cm", metadata.depth_cm),
            ("height_cm", metadata.height_cm),
        )
        if value is None or value <= 0
    ]

    if missing_dimensions:
        return (
            "missing_dimensions: "
            + ", ".join(missing_dimensions)
        )

    category_ranges = CATEGORY_DIMENSION_RANGES_CM.get(metadata.category)
    if category_ranges:
        implausible = []
        for field_name, (minimum, maximum) in category_ranges.items():
            value = float(getattr(metadata, field_name))
            if value < minimum or value > maximum:
                implausible.append(
                    f"{field_name}={value:g} outside {minimum:g}-{maximum:g}"
                )
        if implausible:
            return "implausible_dimensions: " + ", ".join(implausible)

    return None



def insert_furniture_row(
    metadata: IkeaProductMetadata,
) -> int:
    validation_error = validate_metadata_for_insert(
        metadata
    )

    if validation_error:
        raise ValueError(validation_error)

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metadata.name,
                metadata.category,
                metadata.style,
                metadata.color,
                metadata.material,
                metadata.width_cm,
                metadata.depth_cm,
                metadata.height_cm,
                metadata.price,
                None,
                metadata.source_url,
                SOURCE_NAME,
                metadata.currency,
                json.dumps(
                    metadata.room_types,
                    ensure_ascii=False,
                ),
                json.dumps(
                    metadata.tags,
                    ensure_ascii=False,
                ),
                1,
            ),
        )

        furniture_id = int(
            cursor.lastrowid
        )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    return furniture_id



def update_furniture_image_filename(
    furniture_id: int,
    image_filename: str,
) -> None:
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            UPDATE furniture
            SET image_filename = ?
            WHERE id = ?
            """,
            (
                image_filename,
                furniture_id,
            ),
        )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()



def delete_furniture_row(
    furniture_id: int,
) -> None:
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            DELETE FROM furniture
            WHERE id = ?
            """,
            (furniture_id,),
        )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


# =========================================================
# Image download
# =========================================================


def download_product_image_as_png(
    context: BrowserContext,
    image_url: str,
    furniture_id: int,
) -> Path:
    response = context.request.get(
        image_url,
        timeout=60000,
    )

    if not response.ok:
        raise RuntimeError(
            f"Image download failed with HTTP {response.status}: "
            f"{image_url}"
        )

    image_bytes = response.body()

    filename = (
        f"furniture_{furniture_id}_{uuid4()}.png"
    )

    destination = (
        FURNITURE_UPLOAD_DIR
        / filename
    )

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            # JPEG-compatible RGB output is not required because we save PNG.
            # Preserve transparency when present.
            if image.mode not in {
                "RGB",
                "RGBA",
            }:
                image = image.convert("RGBA")

            image.save(
                destination,
                format="PNG",
                optimize=True,
            )

    except Exception as exc:
        raise RuntimeError(
            f"Failed to decode/store IKEA product image: {exc}"
        ) from exc

    return destination


# =========================================================
# Main import operation
# =========================================================


def import_ikea_product_if_missing(
    page: Page,
    context: BrowserContext,
    product_url: str,
    room_type: str = DEFAULT_ROOM_TYPE,
) -> ProductImportResult:
    normalized_url = normalize_url(
        product_url
    )

    existing = find_existing_furniture(
        normalized_url
    )

    if existing is not None:
        existing_id = int(existing["id"])
        existing_image_filename = existing.get("image_filename")

        # Existing product rows are reused. When an older row has no local
        # image yet, try to fill only that missing image without replacing
        # the user's existing metadata.
        if not existing_image_filename:
            try:
                existing_metadata = scrape_ikea_product_metadata(
                    page=page,
                    product_url=normalized_url,
                    room_type=room_type,
                )

                if existing_metadata.image_url:
                    image_path = download_product_image_as_png(
                        context=context,
                        image_url=existing_metadata.image_url,
                        furniture_id=existing_id,
                    )

                    update_furniture_image_filename(
                        furniture_id=existing_id,
                        image_filename=image_path.name,
                    )

                    existing_image_filename = image_path.name

            except Exception:
                # Reuse the existing DB row even when the optional image
                # repair fails; do not duplicate the product.
                pass

        return ProductImportResult(
            source_url=normalized_url,
            status="matched_existing",
            furniture_id=existing_id,
            furniture_name=existing.get("name"),
            category=existing.get("category"),
            image_filename=existing_image_filename,
        )

    try:
        metadata = scrape_ikea_product_metadata(
            page=page,
            product_url=normalized_url,
            room_type=room_type,
        )

    except Exception as exc:
        return ProductImportResult(
            source_url=normalized_url,
            status="failed",
            reason=f"scrape_failed: {exc}",
        )

    validation_error = validate_metadata_for_insert(
        metadata
    )

    if validation_error:
        status = (
            "skipped_unsupported"
            if validation_error.startswith(
                "unsupported_category"
            )
            else "skipped_incomplete"
        )

        return ProductImportResult(
            source_url=normalized_url,
            status=status,
            reason=validation_error,
            category=metadata.category,
            metadata=asdict(metadata),
        )

    furniture_id: int | None = None
    image_path: Path | None = None

    try:
        furniture_id = insert_furniture_row(
            metadata
        )

        if metadata.image_url:
            image_path = download_product_image_as_png(
                context=context,
                image_url=metadata.image_url,
                furniture_id=furniture_id,
            )

            update_furniture_image_filename(
                furniture_id=furniture_id,
                image_filename=image_path.name,
            )

        return ProductImportResult(
            source_url=normalized_url,
            status="imported_new",
            furniture_id=furniture_id,
            furniture_name=metadata.name,
            category=metadata.category,
            image_filename=(
                image_path.name
                if image_path is not None
                else None
            ),
            metadata=asdict(metadata),
        )

    except Exception as exc:
        if image_path is not None and image_path.exists():
            image_path.unlink()

        if furniture_id is not None:
            try:
                delete_furniture_row(
                    furniture_id
                )
            except Exception:
                pass

        return ProductImportResult(
            source_url=normalized_url,
            status="failed",
            reason=f"insert_or_image_failed: {exc}",
            category=metadata.category,
            metadata=asdict(metadata),
        )
