from __future__ import annotations

import argparse
import json
import re
import sys
import time

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from playwright.sync_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright
)


# =========================================================
# Make project imports work when script is run directly:
#
# python scripts/import_ikea_living_room_templates.py
# =========================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT)
    )


from app.database import (  # noqa: E402
    get_connection,
    init_db
)
from app.services.template_analysis_service import (  # noqa: E402
    TemplateVisualAnalysis,
    analyze_design_template_image
)
from app.services.ikea_product_import_service import (  # noqa: E402
    ProductImportResult,
    import_ikea_product_if_missing
)


# =========================================================
# Constants
# =========================================================


DEFAULT_SECTION_URL = (
    "https://www.ikea.com/jo/en/rooms/living-room/"
)

SOURCE_NAME = "IKEA Jordan"
ROOM_TYPE = "living_room"

TEMPLATE_UPLOAD_DIR = (
    PROJECT_ROOT
    / "uploads"
    / "design_templates"
)

REPORT_DIR = (
    PROJECT_ROOT
    / "imports"
    / "reports"
)

TEMPLATE_UPLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True
)

REPORT_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# =========================================================
# Dataclasses
# =========================================================


@dataclass
class ScrapedProduct:
    name: str | None
    url: str
    image_url: str | None = None


@dataclass
class ScrapedTemplate:
    title: str
    description: str | None
    url: str
    hero_image_url: str | None
    products: list[ScrapedProduct]


# =========================================================
# URL helpers
# =========================================================



def normalize_url(
    raw_url: str
) -> str:
    parsed = urlparse(
        raw_url.strip()
    )

    normalized_path = re.sub(
        r"/+",
        "/",
        parsed.path
    )

    if normalized_path != "/":
        normalized_path = (
            normalized_path.rstrip("/")
            + "/"
        )

    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=normalized_path,
        params="",
        query="",
        fragment=""
    )

    return urlunparse(
        normalized
    )



def canonical_ikea_product_key(
    raw_url: str
) -> str:
    """
    IKEA URLs usually end with an article/package identifier such as:

    69508992
    s69508992

    Matching on that identifier is more reliable than comparing the full
    localized URL string.
    """

    normalized = normalize_url(
        raw_url
    )

    path = urlparse(
        normalized
    ).path.rstrip("/")

    last_segment = (
        path.split("/")[-1]
        if path
        else ""
    )

    match = re.search(
        r"(?:-|^)([a-z]?\d{8})$",
        last_segment,
        flags=re.IGNORECASE
    )

    if match:
        return match.group(1).lower()

    return normalized.lower()



def safe_slug(
    value: str,
    max_length: int = 80
) -> str:
    value = value.strip().lower()
    value = re.sub(
        r"[^a-z0-9]+",
        "-",
        value
    )
    value = value.strip("-")

    return (
        value[:max_length]
        or "ikea-template"
    )


# =========================================================
# Browser helpers
# =========================================================



def dismiss_common_cookie_banners(
    page: Page
) -> None:
    possible_labels = [
        re.compile(r"accept all", re.IGNORECASE),
        re.compile(r"accept", re.IGNORECASE),
        re.compile(r"allow all", re.IGNORECASE)
    ]

    for label in possible_labels:
        try:
            button = page.get_by_role(
                "button",
                name=label
            ).first

            if (
                button.count() > 0
                and button.is_visible()
            ):
                button.click(
                    timeout=1500
                )
                page.wait_for_timeout(
                    500
                )
                return

        except Exception:
            continue



def click_all_load_more_buttons(
    page: Page,
    max_clicks: int
) -> int:
    """Click IKEA's Load more button only while new gallery cards appear.

    The button can remain visible even after no additional cards are loaded.
    Stop after two consecutive no-growth attempts so a 9-card page does not
    report 20 meaningless clicks.
    """

    clicks = 0
    no_growth_attempts = 0

    while clicks < max_clicks:
        button = page.locator(
            "button"
        ).filter(
            has_text=re.compile(
                r"load\s*(?:\d+\s*)?more",
                re.IGNORECASE
            )
        ).first

        try:
            if (
                button.count() == 0
                or not button.is_visible()
            ):
                break

            before_count = page.locator(
                'a[href*="/rooms/living-room/gallery/"]'
            ).count()

            button.scroll_into_view_if_needed()
            button.click(
                timeout=5000
            )

            clicks += 1
            page.wait_for_timeout(
                1500
            )

            after_count = page.locator(
                'a[href*="/rooms/living-room/gallery/"]'
            ).count()

            if after_count > before_count:
                no_growth_attempts = 0
            else:
                no_growth_attempts += 1

                if no_growth_attempts >= 2:
                    break

        except PlaywrightTimeoutError:
            break

        except Exception:
            break

    return clicks



def discover_gallery_urls(
    page: Page,
    section_url: str,
    max_load_more_clicks: int
) -> list[str]:
    print(
        f"\n[DISCOVER] Opening section:\n{section_url}"
    )

    max_attempts = 3
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            print(
                f"[DISCOVER] Attempt {attempt}/{max_attempts}"
            )

            if page.is_closed():
                raise RuntimeError(
                    "The Playwright page is already closed."
                )

            page.goto(
                section_url,
                wait_until="domcontentloaded",
                timeout=60000
            )

            try:
                page.wait_for_load_state(
                    "networkidle",
                    timeout=15000
                )
            except PlaywrightTimeoutError:
                pass

            dismiss_common_cookie_banners(
                page
            )

            page.wait_for_timeout(
                2000
            )

            load_more_clicks = (
                click_all_load_more_buttons(
                    page=page,
                    max_clicks=max_load_more_clicks
                )
            )

            page.wait_for_timeout(
                1000
            )

            if page.is_closed():
                raise RuntimeError(
                    "The IKEA section page closed before URL extraction."
                )

            locator = page.locator(
                'a[href*="/rooms/living-room/gallery/"]'
            )

            link_count = locator.count()

            print(
                f"[DISCOVER] Gallery anchors found: {link_count}"
            )

            hrefs: list[str] = []

            for index in range(link_count):
                href = locator.nth(index).get_attribute("href")

                if not href:
                    continue

                if href.startswith("/"):
                    href = "https://www.ikea.com" + href

                hrefs.append(href)

            unique_urls: list[str] = []
            seen: set[str] = set()

            for href in hrefs:
                normalized = normalize_url(href)

                if normalized in seen:
                    continue

                seen.add(normalized)
                unique_urls.append(normalized)

            print(
                f"[DISCOVER] Load-more clicks: {load_more_clicks}"
            )
            print(
                f"[DISCOVER] Unique gallery URLs: {len(unique_urls)}"
            )

            if not unique_urls:
                raise RuntimeError(
                    "No IKEA living-room gallery URLs were discovered."
                )

            return unique_urls

        except Exception as exc:
            last_error = exc

            print(
                f"[DISCOVER] Attempt {attempt} failed: "
                f"{type(exc).__name__}: {exc}"
            )

            if attempt < max_attempts:
                time.sleep(2)

    raise RuntimeError(
        "Failed to discover IKEA gallery URLs "
        f"after {max_attempts} attempts. Last error: {last_error}"
    )


# =========================================================
# Gallery-page extraction
# =========================================================



def extract_first_meaningful_paragraph(
    page: Page
) -> str | None:
    paragraphs: list[str] = page.locator(
        "main p"
    ).all_inner_texts()

    for paragraph in paragraphs:
        cleaned = " ".join(
            paragraph.split()
        )

        if len(cleaned) >= 50:
            return cleaned

    return None



def extract_hero_image_url(
    page: Page
) -> str | None:
    meta = page.locator(
        'meta[property="og:image"]'
    ).first

    try:
        if meta.count() > 0:
            value = meta.get_attribute(
                "content"
            )

            if value:
                return value

    except Exception:
        pass

    try:
        image = page.locator(
            "main img"
        ).first

        if image.count() > 0:
            return (
                image.get_attribute("src")
                or image.get_attribute("data-src")
            )

    except Exception:
        pass

    return None



def extract_recreate_room_products(
    page: Page
) -> list[ScrapedProduct]:
    """Extract exact IKEA product links referenced by a gallery page.

    Preferred path:
    - When an explicit "Recreate the room" section exists, collect every
      /p/ link until the next heading of the SAME OR HIGHER level.
      Product-card h3/h4 headings must not terminate an h2 section.

    Fallback path:
    - For gallery pages without that section, collect /p/ links from the main
      editorial content and stop before Footer / recommendation sections.
    """

    raw_products: list[dict[str, Any]] = page.evaluate(
        r"""
        () => {
            const main = document.querySelector('main') || document.body;
            if (!main) return [];

            const sectionHeadings = Array.from(
                main.querySelectorAll('h1, h2, h3, h4, h5, h6')
            );

            const anchors = Array.from(
                main.querySelectorAll('a[href*="/p/"]')
            );

            function headingLevel(node) {
                const tag = (node && node.tagName || '').toUpperCase();
                const match = tag.match(/^H([1-6])$/);
                return match ? Number(match[1]) : 7;
            }

            function isAfter(node, reference) {
                if (!node || !reference) return false;

                return Boolean(
                    reference.compareDocumentPosition(node)
                    & Node.DOCUMENT_POSITION_FOLLOWING
                );
            }

            function isBefore(node, reference) {
                if (!reference) return true;

                return Boolean(
                    node.compareDocumentPosition(reference)
                    & Node.DOCUMENT_POSITION_FOLLOWING
                );
            }

            function normalizedText(node) {
                return (node && node.textContent || '')
                    .replace(/\s+/g, ' ')
                    .trim();
            }

            function buildProduct(anchor) {
                const container = anchor.closest(
                    'article, li, [data-testid*="product" i], '
                    + '[class*="product" i], [class*="card" i], div'
                );

                const nameNode = container
                    ? container.querySelector(
                        'h2, h3, h4, h5, [class*="title" i], [class*="name" i]'
                    )
                    : null;

                const imageNode = container
                    ? container.querySelector('img')
                    : null;

                const name = (
                    (nameNode && nameNode.textContent)
                    || anchor.textContent
                    || anchor.getAttribute('aria-label')
                    || ''
                ).replace(/\s+/g, ' ').trim();

                const imageUrl = imageNode
                    ? (
                        imageNode.currentSrc
                        || imageNode.src
                        || imageNode.getAttribute('data-src')
                    )
                    : null;

                return {
                    name,
                    url: anchor.href,
                    image_url: imageUrl
                };
            }

            const recreateHeading = sectionHeadings.find((heading) => {
                return /recreate\s+the\s+room/i.test(
                    normalizedText(heading)
                );
            });

            let selectedAnchors = [];

            if (recreateHeading) {
                const targetLevel = headingLevel(recreateHeading);

                const nextSectionHeading = sectionHeadings.find((heading) => {
                    if (heading === recreateHeading) return false;
                    if (!isAfter(heading, recreateHeading)) return false;

                    return headingLevel(heading) <= targetLevel;
                });

                selectedAnchors = anchors.filter((anchor) => {
                    if (!isAfter(anchor, recreateHeading)) return false;

                    if (
                        nextSectionHeading
                        && !isBefore(anchor, nextSectionHeading)
                    ) {
                        return false;
                    }

                    return true;
                });
            }

            if (selectedAnchors.length === 0) {
                const stopPatterns = [
                    /^Footer$/i,
                    /see\s+lots\s+more\s+inspirational\s+living\s+rooms/i,
                    /^Related products$/i,
                    /^Recommended for you$/i,
                    /^More from the/i
                ];

                const terminalHeading = sectionHeadings.find((heading) => {
                    const text = normalizedText(heading);
                    return stopPatterns.some((pattern) => pattern.test(text));
                });

                selectedAnchors = anchors.filter((anchor) => {
                    if (terminalHeading && !isBefore(anchor, terminalHeading)) {
                        return false;
                    }

                    return true;
                });
            }

            return selectedAnchors.map(buildProduct);
        }
        """
    )

    products: list[ScrapedProduct] = []
    seen_urls: set[str] = set()

    for raw_product in raw_products:
        raw_url = str(
            raw_product.get("url")
            or ""
        ).strip()

        if not raw_url:
            continue

        normalized_url = normalize_url(
            raw_url
        )

        if normalized_url in seen_urls:
            continue

        seen_urls.add(
            normalized_url
        )

        name = " ".join(
            str(
                raw_product.get("name")
                or ""
            ).split()
        ) or None

        products.append(
            ScrapedProduct(
                name=name,
                url=normalized_url,
                image_url=raw_product.get(
                    "image_url"
                )
            )
        )

    return products


def scrape_template_page(
    page: Page,
    gallery_url: str
) -> ScrapedTemplate:
    page.goto(
        gallery_url,
        wait_until="domcontentloaded",
        timeout=60000
    )

    dismiss_common_cookie_banners(
        page
    )

    page.wait_for_timeout(
        700
    )

    title = " ".join(
        page.locator(
            "h1"
        ).first.inner_text().split()
    )

    description = (
        extract_first_meaningful_paragraph(
            page
        )
    )

    hero_image_url = (
        extract_hero_image_url(
            page
        )
    )

    products = (
        extract_recreate_room_products(
            page
        )
    )

    return ScrapedTemplate(
        title=title,
        description=description,
        url=normalize_url(gallery_url),
        hero_image_url=hero_image_url,
        products=products
    )


# =========================================================
# Image download
# =========================================================



def extension_from_content_type(
    content_type: str | None
) -> str:
    normalized = (
        content_type
        or ""
    ).split(";")[0].strip().lower()

    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/avif": ".avif"
    }

    return mapping.get(
        normalized,
        ".jpg"
    )



def download_template_image(
    context: BrowserContext,
    image_url: str,
    title: str,
    source_url: str
) -> Path:
    response = context.request.get(
        image_url,
        timeout=60000
    )

    if not response.ok:
        raise RuntimeError(
            f"Image download failed with HTTP {response.status}: "
            f"{image_url}"
        )

    content_type = response.headers.get(
        "content-type"
    )

    extension = extension_from_content_type(
        content_type
    )

    source_key = canonical_ikea_product_key(
        source_url
    )

    # Gallery URLs do not always have an article ID, so the slug remains
    # the stable part of the local filename.
    filename = (
        "ikea_"
        + safe_slug(title)
        + "_"
        + safe_slug(source_key, max_length=30)
        + extension
    )

    destination = (
        TEMPLATE_UPLOAD_DIR
        / filename
    )

    destination.write_bytes(
        response.body()
    )

    return destination


# =========================================================
# Furniture matching
# =========================================================



def load_furniture_index() -> tuple[
    dict[str, dict],
    dict[str, dict]
]:
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

    by_normalized_url: dict[str, dict] = {}
    by_product_key: dict[str, dict] = {}

    for row in rows:
        item = dict(row)
        product_url = str(
            item.get("product_url")
            or ""
        ).strip()

        if not product_url:
            continue

        normalized_url = normalize_url(
            product_url
        )

        by_normalized_url[
            normalized_url
        ] = item

        product_key = canonical_ikea_product_key(
            product_url
        )

        by_product_key[
            product_key
        ] = item

    return (
        by_normalized_url,
        by_product_key
    )



def match_source_product(
    source_product: ScrapedProduct,
    by_normalized_url: dict[str, dict],
    by_product_key: dict[str, dict]
) -> tuple[dict | None, str | None]:
    normalized_url = normalize_url(
        source_product.url
    )

    exact = by_normalized_url.get(
        normalized_url
    )

    if exact is not None:
        return exact, "exact_url"

    product_key = canonical_ikea_product_key(
        source_product.url
    )

    matched = by_product_key.get(
        product_key
    )

    if matched is not None:
        return matched, "ikea_product_key"

    return None, None



def format_product_dimensions_from_result(
    result: ProductImportResult,
) -> str | None:
    metadata = result.metadata or {}

    width = metadata.get("width_cm")
    depth = metadata.get("depth_cm")
    height = metadata.get("height_cm")

    if width is None and depth is None and height is None:
        return None

    return (
        f"{width} × {depth} × {height} cm"
    )


# =========================================================
# Auto-import missing IKEA products into furniture
# =========================================================


def import_missing_source_products(
    product_page: Page,
    context: BrowserContext,
    source_products: list[ScrapedProduct],
    enabled: bool,
    product_delay_ms: int
) -> list[ProductImportResult]:
    """
    For every exact IKEA product URL referenced by a room template:

    - reuse an existing furniture row when already present,
    - otherwise open the IKEA product page,
    - scrape supported product metadata,
    - download the product image locally,
    - insert the new product into furniture,
    - skip unsupported/incomplete products without inventing data.
    """

    results: list[ProductImportResult] = []

    if not enabled:
        return results

    for index, source_product in enumerate(
        source_products,
        start=1
    ):
        display_name = source_product.name or "Unnamed IKEA product"

        print(
            f"[PRODUCT {index}/{len(source_products)}] "
            f"{display_name}"
        )

        print(
            f"[PRODUCT URL] {source_product.url}"
        )

        result = import_ikea_product_if_missing(
            page=product_page,
            context=context,
            product_url=source_product.url,
            room_type=ROOM_TYPE
        )

        results.append(result)

        dimensions_debug = format_product_dimensions_from_result(
            result
        )

        if dimensions_debug:
            print(
                f"[PRODUCT DIMENSIONS] {dimensions_debug}"
            )

        if result.status == "matched_existing":
            print(
                f"[PRODUCT] Existing furniture ID "
                f"{result.furniture_id}: {result.furniture_name}"
            )

        elif result.status == "imported_new":
            print(
                f"[PRODUCT] Imported NEW furniture ID "
                f"{result.furniture_id}: {result.furniture_name}"
            )

        elif result.status in {
            "skipped_unsupported",
            "skipped_incomplete"
        }:
            print(
                f"[PRODUCT] Skipped: {result.reason}"
            )

        else:
            print(
                f"[PRODUCT] Failed: {result.reason}"
            )

        if product_delay_ms > 0:
            time.sleep(
                product_delay_ms / 1000
            )

    return results


# =========================================================
# Database upsert helpers
# =========================================================



def get_existing_template_by_source_url(
    source_url: str
) -> dict | None:
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT *
            FROM design_templates
            WHERE source_url = ?
            """,
            (
                normalize_url(source_url),
            )
        )

        row = cursor.fetchone()

    finally:
        connection.close()

    return (
        dict(row)
        if row is not None
        else None
    )



def upsert_template(
    scraped: ScrapedTemplate,
    preview_image_filename: str | None,
    analysis: TemplateVisualAnalysis | None,
    template_type: str
) -> int:
    source_url = normalize_url(
        scraped.url
    )

    existing = (
        get_existing_template_by_source_url(
            source_url
        )
    )

    now_utc = datetime.now(
        timezone.utc
    ).isoformat()

    analysis_data = (
        analysis.model_dump()
        if analysis is not None
        else {}
    )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        if existing is None:
            cursor.execute(
                """
                INSERT INTO design_templates (
                    name,
                    room_type,
                    style,
                    size_category,
                    layout_type,
                    wall_color_family,
                    floor_color_family,
                    floor_material,
                    natural_light_level,
                    room_openness,
                    density_target,
                    focal_point,
                    description,
                    preview_image_filename,
                    source_name,
                    source_url,
                    source_image_url,
                    template_type,
                    tags,
                    is_active,
                    imported_at,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    scraped.title,
                    ROOM_TYPE,
                    analysis_data.get("primary_style"),
                    analysis_data.get("size_category"),
                    analysis_data.get("layout_type"),
                    analysis_data.get("wall_color_family"),
                    analysis_data.get("floor_color_family"),
                    analysis_data.get("floor_material"),
                    analysis_data.get("natural_light_level"),
                    analysis_data.get("room_openness"),
                    analysis_data.get("density_target"),
                    analysis_data.get("focal_point"),
                    scraped.description,
                    preview_image_filename,
                    SOURCE_NAME,
                    source_url,
                    scraped.hero_image_url,
                    template_type,
                    json.dumps(
                        analysis_data.get("tags", []),
                        ensure_ascii=False
                    ),
                    1,
                    now_utc,
                    now_utc
                )
            )

            template_id = int(
                cursor.lastrowid
            )

        else:
            template_id = int(
                existing["id"]
            )

            if analysis is None:
                cursor.execute(
                    """
                    UPDATE design_templates
                    SET
                        name = ?,
                        description = ?,
                        preview_image_filename = COALESCE(?, preview_image_filename),
                        source_name = ?,
                        source_url = ?,
                        source_image_url = ?,
                        template_type = ?,
                        imported_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        scraped.title,
                        scraped.description,
                        preview_image_filename,
                        SOURCE_NAME,
                        source_url,
                        scraped.hero_image_url,
                        template_type,
                        now_utc,
                        now_utc,
                        template_id
                    )
                )

            else:
                cursor.execute(
                    """
                    UPDATE design_templates
                    SET
                        name = ?,
                        room_type = ?,
                        style = ?,
                        size_category = ?,
                        layout_type = ?,
                        wall_color_family = ?,
                        floor_color_family = ?,
                        floor_material = ?,
                        natural_light_level = ?,
                        room_openness = ?,
                        density_target = ?,
                        focal_point = ?,
                        description = ?,
                        preview_image_filename = COALESCE(?, preview_image_filename),
                        source_name = ?,
                        source_url = ?,
                        source_image_url = ?,
                        template_type = ?,
                        tags = ?,
                        imported_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        scraped.title,
                        ROOM_TYPE,
                        analysis.primary_style,
                        analysis.size_category,
                        analysis.layout_type,
                        analysis.wall_color_family,
                        analysis.floor_color_family,
                        analysis.floor_material,
                        analysis.natural_light_level,
                        analysis.room_openness,
                        analysis.density_target,
                        analysis.focal_point,
                        scraped.description,
                        preview_image_filename,
                        SOURCE_NAME,
                        source_url,
                        scraped.hero_image_url,
                        template_type,
                        json.dumps(
                            analysis.tags,
                            ensure_ascii=False
                        ),
                        now_utc,
                        now_utc,
                        template_id
                    )
                )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    return template_id



def replace_template_products(
    template_id: int,
    source_products: list[ScrapedProduct],
    by_normalized_url: dict[str, dict],
    by_product_key: dict[str, dict],
    product_import_results: list[ProductImportResult] | None = None
) -> dict:
    connection = get_connection()
    cursor = connection.cursor()

    matched_products: list[dict] = []
    missing_products: list[dict] = []
    skipped_products: list[dict] = []
    failed_products: list[dict] = []

    import_result_by_url = {
        normalize_url(result.source_url): result
        for result in (product_import_results or [])
    }

    try:
        cursor.execute(
            """
            DELETE FROM design_template_products
            WHERE template_id = ?
            """,
            (template_id,)
        )

        cursor.execute(
            """
            DELETE FROM design_template_source_products
            WHERE template_id = ?
            """,
            (template_id,)
        )

        inserted_furniture_ids = set()

        for source_product in source_products:
            normalized_source_url = normalize_url(
                source_product.url
            )

            import_result = import_result_by_url.get(
                normalized_source_url
            )

            matched_item, matched_by = (
                match_source_product(
                    source_product=source_product,
                    by_normalized_url=by_normalized_url,
                    by_product_key=by_product_key
                )
            )

            matched_furniture_id = (
                int(matched_item["id"])
                if matched_item is not None
                else None
            )

            if matched_item is not None:
                match_status = (
                    "imported"
                    if (
                        import_result is not None
                        and import_result.status == "imported_new"
                    )
                    else "matched"
                )

                if (
                    import_result is not None
                    and import_result.status == "imported_new"
                ):
                    matched_by = "auto_imported_product_page"

            elif import_result is not None:
                match_status = import_result.status
                matched_by = "auto_product_import"

            else:
                match_status = "missing"

            cursor.execute(
                """
                INSERT INTO design_template_source_products (
                    template_id,
                    source_product_name,
                    source_product_url,
                    source_product_image_url,
                    matched_furniture_id,
                    match_status,
                    matched_by,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    template_id,
                    source_product.name,
                    normalized_source_url,
                    source_product.image_url,
                    matched_furniture_id,
                    match_status,
                    matched_by
                )
            )

            if matched_item is None:
                payload = {
                    "name": source_product.name,
                    "url": normalized_source_url,
                    "status": match_status,
                    "reason": (
                        import_result.reason
                        if import_result is not None
                        else None
                    )
                }

                if match_status in {
                    "skipped_unsupported",
                    "skipped_incomplete"
                }:
                    skipped_products.append(payload)

                elif match_status == "failed":
                    failed_products.append(payload)

                else:
                    missing_products.append(payload)

                continue

            matched_products.append({
                "source_name": source_product.name,
                "source_url": normalized_source_url,
                "furniture_id": matched_furniture_id,
                "furniture_name": matched_item.get("name"),
                "matched_by": matched_by,
                "match_status": match_status
            })

            # This table records source-linked candidate models, not a final
            # mandatory selection. Blueprint slots own final quantities and
            # the slot resolver decides which candidate best fills each slot.
            if matched_furniture_id in inserted_furniture_ids:
                continue

            inserted_furniture_ids.add(
                matched_furniture_id
            )

            preferred_colors = []

            if matched_item.get("color"):
                preferred_colors.append(
                    str(matched_item["color"])
                )

            cursor.execute(
                """
                INSERT INTO design_template_products (
                    template_id,
                    category,
                    quantity,
                    role,
                    priority,
                    preferred_style,
                    preferred_colors,
                    exact_product_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    template_id,
                    matched_item["category"],
                    1,
                    "source_candidate",
                    "candidate",
                    matched_item.get("style"),
                    json.dumps(
                        preferred_colors,
                        ensure_ascii=False
                    ),
                    matched_furniture_id
                )
            )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    return {
        "matched_products": matched_products,
        "missing_products": missing_products,
        "skipped_products": skipped_products,
        "failed_products": failed_products
    }


# =========================================================
# Report helpers
# =========================================================



def write_import_report(
    report: dict
) -> Path:
    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%d_%H%M%S"
    )

    report_path = (
        REPORT_DIR
        / f"ikea_living_room_import_{timestamp}.json"
    )

    report_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    return report_path


# =========================================================
# Import one template
# =========================================================



def import_one_template(
    page: Page,
    product_page: Page,
    context: BrowserContext,
    gallery_url: str,
    by_normalized_url: dict[str, dict],
    by_product_key: dict[str, dict],
    skip_ai: bool,
    reanalyze: bool,
    auto_import_products: bool,
    product_delay_ms: int
) -> dict:
    scraped = scrape_template_page(
        page=page,
        gallery_url=gallery_url
    )

    print(
        f"\n[TEMPLATE] {scraped.title}"
    )
    print(
        f"[TEMPLATE] Source products found: {len(scraped.products)}"
    )

    existing = (
        get_existing_template_by_source_url(
            scraped.url
        )
    )

    preview_path: Path | None = None

    if scraped.hero_image_url:
        try:
            preview_path = download_template_image(
                context=context,
                image_url=scraped.hero_image_url,
                title=scraped.title,
                source_url=scraped.url
            )

        except Exception as exc:
            print(
                f"[WARNING] Could not download hero image: {exc}"
            )

    should_analyze = (
        not skip_ai
        and preview_path is not None
        and (
            reanalyze
            or existing is None
            or not existing.get("style")
            or not existing.get("size_category")
        )
    )

    analysis: TemplateVisualAnalysis | None = None
    analysis_error: str | None = None

    if should_analyze:
        try:
            print(
                "[AI] Analyzing template image..."
            )

            analysis = analyze_design_template_image(
                image_path=preview_path,
                title=scraped.title,
                description=scraped.description
            )

            print(
                "[AI] Analysis complete."
            )

        except Exception as exc:
            analysis_error = str(exc)
            print(
                f"[WARNING] AI template analysis failed: {exc}"
            )

    # -----------------------------------------------------
    # Import missing exact source products into furniture.
    # Existing products are reused, unsupported categories are skipped,
    # and incomplete data is never inserted with fake dimensions.
    # -----------------------------------------------------

    product_import_results = import_missing_source_products(
        product_page=product_page,
        context=context,
        source_products=scraped.products,
        enabled=auto_import_products,
        product_delay_ms=product_delay_ms
    )

    # Reload indexes after product importing so every newly inserted row
    # can be linked to the template immediately.
    fresh_by_normalized_url, fresh_by_product_key = (
        load_furniture_index()
    )

    template_type = (
        "exact_products"
        if scraped.products
        else "inspiration"
    )

    template_id = upsert_template(
        scraped=scraped,
        preview_image_filename=(
            preview_path.name
            if preview_path is not None
            else None
        ),
        analysis=analysis,
        template_type=template_type
    )

    product_result = replace_template_products(
        template_id=template_id,
        source_products=scraped.products,
        by_normalized_url=fresh_by_normalized_url,
        by_product_key=fresh_by_product_key,
        product_import_results=product_import_results
    )

    print(
        f"[DB] Template ID: {template_id}"
    )
    print(
        f"[MATCH] Matched/imported products: "
        f"{len(product_result['matched_products'])}"
    )
    print(
        f"[MATCH] Missing products: "
        f"{len(product_result['missing_products'])}"
    )
    print(
        f"[MATCH] Skipped products: "
        f"{len(product_result['skipped_products'])}"
    )
    print(
        f"[MATCH] Failed products: "
        f"{len(product_result['failed_products'])}"
    )

    return {
        "template_id": template_id,
        "title": scraped.title,
        "source_url": scraped.url,
        "preview_image_filename": (
            preview_path.name
            if preview_path is not None
            else None
        ),
        "source_product_count": len(scraped.products),
        "matched_product_count": len(
            product_result["matched_products"]
        ),
        "missing_product_count": len(
            product_result["missing_products"]
        ),
        "skipped_product_count": len(
            product_result["skipped_products"]
        ),
        "failed_product_count": len(
            product_result["failed_products"]
        ),
        "matched_products": product_result[
            "matched_products"
        ],
        "missing_products": product_result[
            "missing_products"
        ],
        "skipped_products": product_result[
            "skipped_products"
        ],
        "failed_products": product_result[
            "failed_products"
        ],
        "product_import_results": [
            asdict(result)
            for result in product_import_results
        ],
        "analysis": (
            analysis.model_dump()
            if analysis is not None
            else None
        ),
        "analysis_error": analysis_error
    }


# =========================================================
# CLI
# =========================================================



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Bulk-import IKEA Jordan living-room gallery templates "
            "into Aqar AI."
        )
    )

    parser.add_argument(
        "--section-url",
        default=DEFAULT_SECTION_URL,
        help="IKEA living-room section URL."
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Import only the first N discovered templates. "
            "Useful for testing."
        )
    )

    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the Chromium browser window."
    )

    parser.add_argument(
        "--skip-ai",
        action="store_true",
        help=(
            "Skip GPT template-image analysis. "
            "Scraping and DB import still run."
        )
    )

    parser.add_argument(
        "--reanalyze",
        action="store_true",
        help=(
            "Force GPT analysis again even when an existing imported "
            "template already has metadata."
        )
    )

    parser.add_argument(
        "--delay-ms",
        type=int,
        default=500,
        help="Delay between gallery pages."
    )

    parser.add_argument(
        "--max-load-more-clicks",
        type=int,
        default=20,
        help="Safety limit for dynamic Load more clicks."
    )

    parser.add_argument(
        "--skip-product-import",
        action="store_true",
        help=(
            "Do not auto-import missing IKEA source products into the "
            "furniture table. Existing product matching still runs."
        )
    )

    parser.add_argument(
        "--product-delay-ms",
        type=int,
        default=250,
        help=(
            "Delay between IKEA product pages while auto-importing "
            "missing products."
        )
    )

    return parser.parse_args()



def main() -> None:
    args = parse_args()

    if (
        args.limit is not None
        and args.limit < 1
    ):
        raise SystemExit(
            "--limit must be at least 1"
        )

    print(
        "\n============================================================"
    )
    print(
        "Aqar AI — IKEA Living Room Template Importer"
    )
    print(
        "============================================================"
    )

    init_db()

    (
        by_normalized_url,
        by_product_key
    ) = load_furniture_index()

    print(
        f"[DB] Furniture URL index size: {len(by_normalized_url)}"
    )

    report: dict[str, Any] = {
        "source": SOURCE_NAME,
        "section_url": normalize_url(
            args.section_url
        ),
        "started_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "templates": [],
        "errors": []
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=not args.headed
        )

        context = browser.new_context(
            viewport={
                "width": 1440,
                "height": 1100
            },
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            )
        )

        page = context.new_page()
        product_page = context.new_page()

        try:
            gallery_urls = discover_gallery_urls(
                page=page,
                section_url=args.section_url,
                max_load_more_clicks=(
                    args.max_load_more_clicks
                )
            )

            if args.limit is not None:
                gallery_urls = gallery_urls[
                    :args.limit
                ]

            report[
                "discovered_template_count"
            ] = len(gallery_urls)

            for index, gallery_url in enumerate(
                gallery_urls,
                start=1
            ):
                print(
                    "\n------------------------------------------------------------"
                )
                print(
                    f"[{index}/{len(gallery_urls)}] {gallery_url}"
                )

                try:
                    result = import_one_template(
                        page=page,
                        product_page=product_page,
                        context=context,
                        gallery_url=gallery_url,
                        by_normalized_url=by_normalized_url,
                        by_product_key=by_product_key,
                        skip_ai=args.skip_ai,
                        reanalyze=args.reanalyze,
                        auto_import_products=(
                            not args.skip_product_import
                        ),
                        product_delay_ms=args.product_delay_ms
                    )

                    report["templates"].append(
                        result
                    )

                except Exception as exc:
                    error_payload = {
                        "source_url": gallery_url,
                        "error": str(exc)
                    }

                    report["errors"].append(
                        error_payload
                    )

                    print(
                        f"[ERROR] {exc}"
                    )

                if args.delay_ms > 0:
                    time.sleep(
                        args.delay_ms / 1000
                    )

        finally:
            context.close()
            browser.close()

    report[
        "finished_at"
    ] = datetime.now(
        timezone.utc
    ).isoformat()

    report[
        "successful_template_count"
    ] = len(
        report["templates"]
    )

    report[
        "error_count"
    ] = len(
        report["errors"]
    )

    report[
        "total_missing_product_count"
    ] = sum(
        int(template.get(
            "missing_product_count",
            0
        ))
        for template in report["templates"]
    )

    report[
        "total_skipped_product_count"
    ] = sum(
        int(template.get(
            "skipped_product_count",
            0
        ))
        for template in report["templates"]
    )

    report[
        "total_failed_product_count"
    ] = sum(
        int(template.get(
            "failed_product_count",
            0
        ))
        for template in report["templates"]
    )

    report[
        "total_newly_imported_product_count"
    ] = sum(
        sum(
            1
            for result in template.get(
                "product_import_results",
                []
            )
            if result.get("status") == "imported_new"
        )
        for template in report["templates"]
    )

    report_path = write_import_report(
        report
    )

    print(
        "\n============================================================"
    )
    print(
        "IMPORT COMPLETE"
    )
    print(
        "============================================================"
    )
    print(
        f"Successful templates: {report['successful_template_count']}"
    )
    print(
        f"Errors: {report['error_count']}"
    )
    print(
        "New furniture products imported: "
        f"{report['total_newly_imported_product_count']}"
    )
    print(
        "Missing source products across imported templates: "
        f"{report['total_missing_product_count']}"
    )
    print(
        "Skipped unsupported/incomplete products: "
        f"{report['total_skipped_product_count']}"
    )
    print(
        "Failed product imports: "
        f"{report['total_failed_product_count']}"
    )
    print(
        f"Report: {report_path}"
    )


if __name__ == "__main__":
    main()
