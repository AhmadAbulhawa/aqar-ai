from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from app.catalog_import.base import RetailerAdapter
from app.catalog_import.http import CachedHttpClient
from app.catalog_import.models import DiscoveredProduct, NormalizedProduct
from app.catalog_import.parsing import (
    canonicalize_url,
    clean_text,
    parse_decimal,
    parse_labeled_dimensions,
)
from app.catalog_import.policy import default_room_types


class ShahwanJordanAdapter(RetailerAdapter):
    adapter_id = "shahwan_jo"
    source_store = "Shahwan Furniture"
    source_store_slug = "shahwan_jo"
    base_url = "https://shahwan.net/"
    category_pages = {
        "master_bedroom_sets": "https://shahwan.net/shop/category/1",
        "youth_bedroom_sets": "https://shahwan.net/shop/category/13",
        "youth_bedroom_pieces": "https://shahwan.net/shop/category/9",
        "modern_sofas": "https://shahwan.net/shop/category/5",
        "neoclassical_sofas": "https://shahwan.net/shop/category/6",
        "classic_sofas": "https://shahwan.net/shop/category/7",
        "corner_sofas": "https://shahwan.net/shop/category/8",
        "recliners": "https://shahwan.net/shop/category/3",
        "sofa_beds": "https://shahwan.net/shop/category/4",
        "tv_stands": "https://shahwan.net/shop/category/tv-10",
        "buffets": "https://shahwan.net/shop/category/11",
        "dining_sets": "https://shahwan.net/shop/category/12",
        "dining_sets_8": "https://shahwan.net/shop/category/8-15",
        "dining_sets_6": "https://shahwan.net/shop/category/6-16",
        "dining_sets_4": "https://shahwan.net/shop/category/4-17",
        "service_tables": "https://shahwan.net/shop/category/18",
        "consoles": "https://shahwan.net/shop/category/20",
        "entry_seating": "https://shahwan.net/shop/category/21",
        "office_chairs": "https://shahwan.net/shop/category/23",
        "coffee_tables": "https://shahwan.net/shop/category/25",
        "children_bedrooms": "https://shahwan.net/shop/category/28",
        "mattresses": "https://shahwan.net/shop/category/2",
        "mattresses_2": "https://shahwan.net/shop/category/29",
    }
    category_mapping = {
        "master_bedroom_sets": "shop_only",
        "youth_bedroom_sets": "shop_only",
        "youth_bedroom_pieces": "mixed_bedroom",
        "modern_sofas": "sofa",
        "neoclassical_sofas": "sofa",
        "classic_sofas": "sofa",
        "corner_sofas": "sofa",
        "recliners": "armchair",
        "sofa_beds": "sofa",
        "tv_stands": "tv_stand",
        "buffets": "sideboard",
        "dining_sets": "shop_only",
        "dining_sets_8": "shop_only",
        "dining_sets_6": "shop_only",
        "dining_sets_4": "shop_only",
        "service_tables": "side_table",
        "consoles": "sideboard",
        "entry_seating": "shop_only",
        "office_chairs": "office_chair",
        "coffee_tables": "coffee_table",
        "children_bedrooms": "shop_only",
        "mattresses": "shop_only",
        "mattresses_2": "shop_only",
    }

    def discover(
        self,
        client: CachedHttpClient,
        category_filter: Sequence[str] | None = None,
    ) -> Iterable[DiscoveredProduct]:
        selected = self.selected_category_pages(category_filter)
        discovered: list[DiscoveredProduct] = []
        seen: set[str] = set()
        self.last_discovery_pages_scanned = 0
        for source_category, start_url in selected.items():
            page_url: str | None = start_url
            visited: set[str] = set()
            while page_url:
                page_url = canonicalize_url(page_url, base_url=start_url)
                if page_url in visited:
                    raise ValueError(f"Shahwan pagination loop at {page_url}")
                visited.add(page_url)
                html_text = client.get_text(page_url, max_bytes=5_000_000)
                parser = _ShahwanCategoryParser()
                parser.feed(html_text)
                self.last_discovery_pages_scanned += 1
                for raw_url, hint in parser.products.items():
                    canonical = canonicalize_url(raw_url, base_url=page_url)
                    if canonical in seen:
                        continue
                    source_id_match = re.search(r"-(\d+)$", canonical)
                    if not source_id_match:
                        continue
                    seen.add(canonical)
                    discovered.append(
                        DiscoveredProduct(
                            url=canonical,
                            source_product_id=source_id_match.group(1),
                            source_category=source_category,
                            normalized_category=self.category_mapping[source_category],
                            name_hint=clean_text(hint),
                        )
                    )
                page_url = (
                    urljoin(page_url, parser.next_url) if parser.next_url else None
                )
        return discovered

    @staticmethod
    def _bedroom_piece_category(name: str) -> str:
        value = name.casefold()
        if any(marker in value for marker in ("\u0633\u0631\u064a\u0631", "bed")):
            return "bed"
        if any(marker in value for marker in ("\u062e\u0632\u0627\u0646\u0629", "wardrobe")):
            return "wardrobe"
        if any(marker in value for marker in ("\u062a\u0633\u0631\u064a\u062d\u0629", "dresser")):
            return "dresser"
        if any(marker in value for marker in ("\u0643\u0645\u0648\u062f", "nightstand", "night stand")):
            return "nightstand"
        return "shop_only"

    def _semantics(
        self,
        source_category: str,
        name: str,
    ) -> tuple[str, str | None, list[str], bool]:
        category = self.category_mapping[source_category]
        style: str | None = None
        tags: list[str] = []
        is_set = source_category in {
            "master_bedroom_sets",
            "youth_bedroom_sets",
            "dining_sets",
            "dining_sets_8",
            "dining_sets_6",
            "dining_sets_4",
            "children_bedrooms",
        }
        if source_category in {"youth_bedroom_pieces", "children_bedrooms"}:
            category = self._bedroom_piece_category(name)
            is_set = category == "shop_only"
        if source_category == "modern_sofas":
            style = "modern"
        elif source_category == "classic_sofas":
            style = "classic"
        elif source_category == "neoclassical_sofas":
            style = "classic"
            tags.append("neoclassical")
        elif source_category == "corner_sofas":
            tags.extend(("corner_sofa", "sectional_sofa"))
        elif source_category == "recliners":
            tags.append("recliner")
        elif source_category == "sofa_beds":
            tags.append("sofa_bed")
        elif source_category == "consoles":
            tags.append("console_table")
        elif source_category == "entry_seating":
            tags.append("entry_seating")
        elif source_category.startswith("mattresses"):
            tags.append("mattress")
        elif source_category.startswith("dining_sets") or source_category == "dining_sets":
            tags.append("dining_set")
        elif "bedroom" in source_category:
            tags.append("bedroom_set" if is_set else "bedroom_piece")
        return category, style, tags, is_set

    @staticmethod
    def _included_pieces(description: str | None) -> list[str]:
        if not description:
            return []
        pieces: list[str] = []
        for segment in re.split(r"[\n\r\u2022\u2666]+", description):
            value = clean_text(segment)
            if value and re.search(r"\d+\s*\u0642\u0637\u0639|\b\d+\s*(?:pcs?|pieces?)\b", value, re.I):
                pieces.append(value)
        return pieces[:30]

    def parse_product(
        self,
        reference: DiscoveredProduct,
        client: CachedHttpClient,
    ) -> NormalizedProduct:
        html_text = client.get_text(reference.url, max_bytes=5_000_000)
        parser = _ShahwanProductParser()
        parser.feed(html_text)
        tracking = parser.tracking
        name = clean_text(parser.fields.get("name") or tracking.get("item_name"))
        if not name:
            raise ValueError("Shahwan product has no name")
        bracket_sku = re.match(r"^\[([^\]]+)\]\s*(.+)$", name)
        source_id = reference.source_product_id
        sku: str | None = None
        if bracket_sku:
            sku = clean_text(bracket_sku.group(1))
            name = clean_text(bracket_sku.group(2)) or name
        tracking_name = clean_text(tracking.get("item_name"))
        if tracking_name:
            tracking_sku = re.match(r"^\[([^\]]+)\]\s*(.+)$", tracking_name)
            if tracking_sku:
                sku = clean_text(tracking_sku.group(1)) or sku
                if not parser.fields.get("name"):
                    name = clean_text(tracking_sku.group(2)) or name
        source_id = sku or source_id
        if not source_id:
            raise ValueError("Shahwan product has no SKU or template ID")

        currency = clean_text(parser.fields.get("priceCurrency") or tracking.get("currency"))
        if currency != "JOD":
            raise ValueError(f"Unexpected Shahwan currency: {currency!r}")
        current_price = parse_decimal(parser.fields.get("price"))
        regular_price = parse_decimal(tracking.get("price"))
        if current_price is None:
            current_price = regular_price
        if current_price is None:
            raise ValueError("Shahwan product has no public price")
        original_price = (
            regular_price
            if regular_price is not None and regular_price > current_price
            else None
        )
        source_category = reference.source_category or ""
        category, style, tags, is_set = self._semantics(source_category, name)
        description = clean_text(parser.description)
        dimensions = (
            parse_labeled_dimensions(" ".join((name, description or "")))
            if not is_set
            else {
                "width_cm": None,
                "depth_cm": None,
                "height_cm": None,
                "diameter_cm": None,
            }
        )
        image = clean_text(parser.fields.get("image") or parser.og_image)
        canonical = canonicalize_url(parser.canonical_url or reference.url)
        template_match = re.search(r"-(\d+)$", canonical)
        template_id = template_match.group(1) if template_match else reference.source_product_id
        # Odoo publishes the page and cart action but exposes no trustworthy
        # physical-stock fact here. Hidden unavailable variants are ignored.
        availability: bool | None = None
        return NormalizedProduct(
            adapter_id=self.adapter_id,
            source_store=self.source_store,
            source_store_slug=self.source_store_slug,
            source_product_id=source_id,
            canonical_url=canonical,
            name=name,
            name_ar=name,
            category=category,
            subcategory=clean_text(tracking.get("item_category")) or source_category,
            current_price=current_price,
            original_price=original_price,
            currency="JOD",
            availability=availability,
            image_urls=[image] if image else [],
            width_cm=dimensions["width_cm"],
            depth_cm=dimensions["depth_cm"],
            height_cm=dimensions["height_cm"],
            diameter_cm=dimensions["diameter_cm"],
            style=style,
            room_types=default_room_types(category),
            description=description,
            model_number=sku,
            is_product_set=is_set,
            included_pieces=self._included_pieces(description) if is_set else [],
            tags=tags,
            source_metadata={
                "odoo_product_template_id": template_id,
                "official_source_category": source_category,
                "tracking": tracking,
                "availability_evidence": "unknown",
            },
        )


class _ShahwanCategoryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.products: dict[str, str] = {}
        self.next_url: str | None = None
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value for key, value in attrs}
        href = values.get("href")
        classes = set(str(values.get("class") or "").split())
        if href and tag.casefold() == "a":
            if (
                re.search(r"/shop/[^/?]+-\d+(?:\?|$)", href)
                and "/shop/category/" not in href
            ):
                self._href = href
                self._parts = []
                self.products.setdefault(href, "")
            if "tp-load-more-btn" in classes:
                self.next_url = href

    def handle_data(self, data: str) -> None:
        if self._href:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._href:
            value = clean_text(" ".join(self._parts))
            if value and len(value) > len(self.products[self._href]):
                self.products[self._href] = value
            self._href = None
            self._parts = []


class _ShahwanProductParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tracking: dict[str, Any] = {}
        self.fields: dict[str, str] = {}
        self.canonical_url: str | None = None
        self.og_image: str | None = None
        self.description: str | None = None
        self._field: str | None = None
        self._field_depth = 0
        self._field_parts: list[str] = []
        self._description_depth = 0
        self._description_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value for key, value in attrs}
        raw_tracking = values.get("data-product-tracking-info")
        if raw_tracking:
            try:
                parsed = json.loads(raw_tracking)
                if isinstance(parsed, dict):
                    self.tracking = parsed
            except json.JSONDecodeError:
                pass
        if tag.casefold() == "link" and str(values.get("rel") or "").casefold() == "canonical":
            self.canonical_url = clean_text(values.get("href"))
        if tag.casefold() == "meta" and values.get("property") == "og:image":
            self.og_image = clean_text(values.get("content"))

        itemprop = clean_text(values.get("itemprop"))
        if itemprop in {"name", "url", "image", "price", "priceCurrency", "availability"}:
            direct = clean_text(values.get("content") or values.get("href"))
            if direct:
                self.fields[itemprop] = direct
            elif self._field is None:
                self._field = itemprop
                self._field_depth = 1
                self._field_parts = []
        elif self._field is not None:
            self._field_depth += 1

        classes = set(str(values.get("class") or "").split())
        if self._description_depth:
            self._description_depth += 1
        elif tag.casefold() == "p" and {"text-muted", "my-2"} <= classes:
            self._description_depth = 1
            self._description_parts = []

    def handle_data(self, data: str) -> None:
        if self._field is not None:
            self._field_parts.append(data)
        if self._description_depth:
            self._description_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._field is not None:
            self._field_depth -= 1
            if self._field_depth == 0:
                value = clean_text(" ".join(self._field_parts))
                if value:
                    self.fields[self._field] = value
                self._field = None
                self._field_parts = []
        if self._description_depth:
            self._description_depth -= 1
            if self._description_depth == 0:
                self.description = clean_text(" ".join(self._description_parts))
                self._description_parts = []
