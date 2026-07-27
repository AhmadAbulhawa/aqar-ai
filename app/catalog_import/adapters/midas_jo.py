from __future__ import annotations

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
    first_json_ld_product,
    parse_availability,
    parse_decimal,
    parse_labeled_dimensions,
)
from app.catalog_import.policy import default_room_types


class MidasJordanAdapter(RetailerAdapter):
    adapter_id = "midas_jo"
    source_store = "Midas Jordan"
    source_store_slug = "midas_jo"
    base_url = "https://midasfurniture.com/jo_ar/"
    category_pages = {
        "living_room": "https://midasfurniture.com/jo_ar/home-furniture/living-rooms.html",
        "bedroom": "https://midasfurniture.com/jo_ar/home-furniture/bedroom-sets.html",
        "dining": "https://midasfurniture.com/jo_ar/home-furniture/dining-rooms.html",
        "lighting": "https://midasfurniture.com/jo_ar/accessories/lighting.html",
        "rugs": "https://midasfurniture.com/jo_ar/accessories/soft-furnishings/rugs.html",
        "mirrors": "https://midasfurniture.com/jo_ar/accessories/wall-decor/mirrors.html",
        "wall_decor": "https://midasfurniture.com/jo_ar/accessories/wall-decor/wall-arts.html",
        "decorative_accessories": "https://midasfurniture.com/jo_ar/accessories/deco-figurines-and-objects.html",
        "vases_planters": "https://midasfurniture.com/jo_ar/accessories/decor-accessories/vases-and-pots.html",
        "outdoor": "https://midasfurniture.com/jo_ar/outdoor-furniture/furniture.html",
    }
    category_mapping = {
        "living_room": "mixed_living_room",
        "bedroom": "mixed_bedroom",
        "dining": "mixed_dining",
        "lighting": "mixed_lighting",
        "rugs": "rug",
        "mirrors": "mirror",
        "wall_decor": "wall_art",
        "decorative_accessories": "shop_only",
        "vases_planters": "shop_only",
        "outdoor": "shop_only",
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
            visited_pages: set[str] = set()
            while page_url:
                page_url = canonicalize_url(
                    page_url,
                    base_url=start_url,
                    keep_query_keys={"p"},
                )
                if page_url in visited_pages:
                    raise ValueError(f"Midas pagination loop at {page_url}")
                visited_pages.add(page_url)
                html_text = client.get_text(page_url, max_bytes=5_000_000)
                parser = _MagentoCategoryParser()
                parser.feed(html_text)
                self.last_discovery_pages_scanned += 1
                for raw_url in parser.product_urls:
                    canonical = canonicalize_url(raw_url, base_url=page_url)
                    if canonical in seen:
                        continue
                    seen.add(canonical)
                    discovered.append(
                        DiscoveredProduct(
                            url=canonical,
                            source_category=source_category,
                            normalized_category=self.category_mapping[source_category],
                        )
                    )
                page_url = (
                    urljoin(page_url, parser.next_url) if parser.next_url else None
                )
        return discovered

    @staticmethod
    def _category(name: str, source_category: str) -> tuple[str, list[str], bool]:
        value = name.casefold()
        tags: list[str] = []
        set_markers = (
            " set",
            "set ",
            " pcs",
            "piece set",
            "\u0637\u0642\u0645",
            " \u0642\u0637\u0639",
            "\u0645\u062c\u0645\u0648\u0639\u0629",
        )
        is_set = any(marker in value for marker in set_markers)

        if source_category == "outdoor":
            return "shop_only", ["outdoor_furniture"], is_set
        if source_category in {"decorative_accessories", "vases_planters"}:
            return "shop_only", [source_category], is_set
        if source_category == "rugs":
            return "rug", [], is_set
        if source_category == "mirrors":
            return "mirror", [], is_set
        if source_category == "wall_decor":
            return "wall_art", [], is_set

        if any(marker in value for marker in ("sofa bed", "\u0643\u0646\u0628 \u0633\u0631\u064a\u0631", "\u0635\u0648\u0641\u0627 \u0628\u064a\u062f")):
            return "sofa", ["sofa_bed"], is_set
        if any(marker in value for marker in ("sectional", "corner sofa", "\u0643\u0646\u0628\u0629 \u0632\u0627\u0648\u064a\u0629", "\u0643\u0646\u0628 \u0632\u0627\u0648\u064a\u0629")):
            tags.append("sectional_sofa")
            return ("shop_only" if is_set else "sofa"), tags, is_set
        if any(marker in value for marker in ("sofa", "loveseat", "\u0643\u0646\u0628", "\u0643\u0646\u0628\u0629")):
            return ("shop_only" if is_set else "sofa"), (["sofa_set"] if is_set else []), is_set
        if any(marker in value for marker in ("bench", "ottoman", "\u0628\u0646\u0634", "\u0623\u0648\u062a\u0648\u0645\u0627\u0646", "\u0627\u0648\u062a\u0648\u0645\u0627\u0646")):
            return "shop_only", ["bench_or_ottoman"], is_set
        if any(marker in value for marker in ("dining chair", "\u0643\u0631\u0633\u064a \u0637\u0639\u0627\u0645", "\u0643\u0631\u0633\u0649 \u0637\u0639\u0627\u0645")):
            return "dining_chair", [], is_set
        if any(marker in value for marker in ("chair", "recliner", "\u0643\u0631\u0633\u064a", "\u0643\u0631\u0633\u0649")):
            return "armchair", (["recliner"] if "reclin" in value or "\u0627\u0633\u062a\u0631\u062e\u0627\u0621" in value else []), is_set
        if any(marker in value for marker in ("tv unit", "tv stand", "\u0637\u0627\u0648\u0644\u0629 \u062a\u0644\u0641\u0632\u064a\u0648\u0646", "\u0645\u0643\u062a\u0628\u0629 \u062a\u0644\u0641\u0632\u064a\u0648\u0646")):
            return "tv_stand", [], is_set
        if any(marker in value for marker in ("console", "\u0643\u0648\u0646\u0633\u0648\u0644")):
            return "sideboard", ["console_table"], is_set
        if any(marker in value for marker in ("coffee table", "center table", "\u0637\u0627\u0648\u0644\u0629 \u0648\u0633\u0637", "\u0637\u0627\u0648\u0644\u0629 \u0642\u0647\u0648\u0629")):
            return ("shop_only" if is_set else "coffee_table"), (["table_set"] if is_set else []), is_set
        if any(marker in value for marker in ("side table", "end table", "\u0637\u0627\u0648\u0644\u0629 \u062c\u0627\u0646\u0628")):
            return "side_table", [], is_set
        if any(marker in value for marker in ("nesting table", "\u0637\u0627\u0648\u0644\u0627\u062a \u0645\u062a\u062f\u0627\u062e\u0644", "\u0637\u0627\u0648\u0644\u0629 \u0645\u062a\u062f\u0627\u062e\u0644")):
            return "shop_only", ["nesting_table_set"], True
        if any(marker in value for marker in ("wardrobe", "\u062e\u0632\u0627\u0646\u0629 \u0645\u0644\u0627\u0628\u0633")):
            return "wardrobe", [], is_set
        if any(marker in value for marker in ("cabinet", "\u062e\u0632\u0627\u0646\u0629", "\u062e\u0632\u0646\u0629")):
            return "storage_cabinet", [], is_set
        if any(marker in value for marker in ("shelf", "shelving", "bookcase", "\u0631\u0641\u0648\u0641", "\u0645\u0643\u062a\u0628\u0629")):
            return "bookshelf", [], is_set

        if any(marker in value for marker in ("bedroom set", "\u0637\u0642\u0645 \u063a\u0631\u0641\u0629 \u0646\u0648\u0645")):
            return "shop_only", ["bedroom_set"], True
        if any(marker in value for marker in ("nightstand", "night stand", "\u0643\u0645\u0648\u062f\u064a\u0646\u0648")):
            return "nightstand", [], is_set
        if any(marker in value for marker in ("dresser", "chest of drawers", "drawer unit", "\u062a\u0633\u0631\u064a\u062d\u0629", "\u0648\u062d\u062f\u0629 \u0623\u062f\u0631\u0627\u062c", "\u0648\u062d\u062f\u0629 \u0627\u062f\u0631\u0627\u062c")):
            return "dresser", [], is_set
        if re.search(r"\bbed\b", value) or "\u0633\u0631\u064a\u0631" in value:
            return "bed", [], is_set

        if any(marker in value for marker in ("dining set", "dining table set", "\u0637\u0642\u0645 \u0637\u0627\u0648\u0644\u0629 \u0637\u0639\u0627\u0645")):
            return "shop_only", ["dining_set"], True
        if any(marker in value for marker in ("dining table", "\u0637\u0627\u0648\u0644\u0629 \u0637\u0639\u0627\u0645")):
            return "dining_table", [], is_set
        if any(marker in value for marker in ("buffet", "sideboard", "\u0628\u0648\u0641\u064a\u0647", "\u0628\u0648\u0641\u064a\u0629")):
            return "sideboard", [], is_set

        if any(marker in value for marker in ("chandelier", "\u062b\u0631\u064a\u0627")):
            return "chandelier", [], is_set
        if any(marker in value for marker in ("floor lamp", "\u0627\u0628\u0627\u062c\u0648\u0631\u0629 \u0627\u0631\u0636", "\u0645\u0635\u0628\u0627\u062d \u0627\u0631\u0636")):
            return "floor_lamp", [], is_set
        if any(marker in value for marker in ("wall lamp", "\u0645\u0635\u0628\u0627\u062d \u062d\u0627\u0626\u0637", "\u0627\u0628\u0627\u062c\u0648\u0631\u0629 \u062d\u0627\u0626\u0637")):
            return "wall_lamp", [], is_set
        if any(marker in value for marker in ("table lamp", "\u0627\u0628\u0627\u062c\u0648\u0631\u0629 \u0637\u0627\u0648\u0644\u0629", "\u0645\u0635\u0628\u0627\u062d \u0637\u0627\u0648\u0644\u0629")):
            return "table_lamp", [], is_set
        if source_category == "lighting" and any(marker in value for marker in ("hanging", "pendant", "\u0645\u0639\u0644\u0642")):
            return "pendant_light", [], is_set

        return "shop_only", [f"unmapped_{source_category}"], is_set

    @staticmethod
    def _image_urls(structured: dict[str, Any]) -> list[str]:
        values = structured.get("image", [])
        if not isinstance(values, list):
            values = [values]
        urls: list[str] = []
        for value in values:
            url = value.get("url") or value.get("contentUrl") if isinstance(value, dict) else value
            if url and str(url) not in urls:
                urls.append(str(url))
        return urls

    @staticmethod
    def _included_pieces(description: str | None) -> list[str]:
        if not description:
            return []
        pieces: list[str] = []
        for segment in re.split(r"[\n\r\u2022\u2666]+", description):
            cleaned = clean_text(segment)
            if cleaned and re.search(r"\b\d+\s*(?:pcs?|pieces?)\b|\d+\s*\u0642\u0637\u0639", cleaned, re.I):
                pieces.append(cleaned)
        return pieces[:30]

    def parse_product(
        self,
        reference: DiscoveredProduct,
        client: CachedHttpClient,
    ) -> NormalizedProduct:
        html_text = client.get_text(reference.url, max_bytes=5_000_000)
        structured = first_json_ld_product(html_text)
        if structured is None:
            raise ValueError("Midas product page has no Product JSON-LD")
        offers = structured.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price = parse_decimal(offers.get("price"))
        if price is None:
            raise ValueError("Midas product has no public price")
        if clean_text(offers.get("priceCurrency")) != "JOD":
            raise ValueError("Midas product is not priced in JOD")
        name = clean_text(structured.get("name"))
        if not name:
            raise ValueError("Midas product has no name")
        source_category = reference.source_category or "unknown"
        category, category_tags, is_set = self._category(name, source_category)
        page = _MidasProductParser()
        page.feed(html_text)
        original_price = page.old_price
        if original_price is not None and original_price <= price:
            original_price = None
        availability_url = clean_text(offers.get("availability"))
        availability: bool | None = None
        if availability_url:
            if availability_url.endswith("/InStock"):
                availability = True
            elif availability_url.endswith("/OutOfStock"):
                availability = False
        if availability is None:
            availability = parse_availability(page.visible_product_text)

        description = clean_text(structured.get("description"))
        dimensions = parse_labeled_dimensions(" ".join((name, description or "")))
        source_id = clean_text(
            structured.get("sku") or structured.get("productID") or page.sku
        )
        if not source_id:
            raise ValueError("Midas product has no SKU")
        canonical = canonicalize_url(
            str(structured.get("url") or offers.get("url") or reference.url)
        )
        mounting = "ceiling" if category in {"chandelier", "pendant_light"} else None
        included_pieces = self._included_pieces(description) if is_set else []
        return NormalizedProduct(
            adapter_id=self.adapter_id,
            source_store=self.source_store,
            source_store_slug=self.source_store_slug,
            source_product_id=source_id,
            canonical_url=canonical,
            name=name,
            name_ar=name,
            category=category,
            subcategory=clean_text(structured.get("category")) or source_category,
            current_price=price,
            original_price=original_price,
            currency="JOD",
            availability=availability,
            image_urls=self._image_urls(structured),
            width_cm=dimensions["width_cm"],
            depth_cm=dimensions["depth_cm"],
            height_cm=dimensions["height_cm"],
            diameter_cm=dimensions["diameter_cm"],
            room_types=default_room_types(category),
            description=description,
            brand=clean_text(
                (structured.get("brand") or {}).get("name")
                if isinstance(structured.get("brand"), dict)
                else structured.get("brand")
            ),
            model_number=source_id,
            mounting_type=mounting,
            is_product_set=is_set,
            included_pieces=included_pieces,
            tags=category_tags,
            source_metadata={
                "official_source_category": source_category,
                "raw_category": clean_text(structured.get("category")),
                "set_description": description if is_set else None,
            },
        )


class _MagentoCategoryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.product_urls: list[str] = []
        self.next_url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value for key, value in attrs}
        href = values.get("href")
        classes = set(str(values.get("class") or "").split())
        rel = set(str(values.get("rel") or "").casefold().split())
        if href and tag.casefold() == "a" and "product-item-link" in classes:
            if href not in self.product_urls:
                self.product_urls.append(href)
        if href and ("next" in rel or {"action", "next"} <= classes):
            self.next_url = href


class _MidasProductParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.old_price: Decimal | None = None
        self.sku: str | None = None
        self._visible_depth = 0
        self._visible_parts: list[str] = []

    @property
    def visible_product_text(self) -> str:
        return " ".join(self._visible_parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value for key, value in attrs}
        price_type = clean_text(values.get("data-price-type"))
        if price_type and price_type.casefold() in {"oldprice", "regularprice"}:
            self.old_price = parse_decimal(values.get("data-price-amount"))
        if str(values.get("itemprop") or "").casefold() in {"sku", "productid"}:
            self.sku = clean_text(values.get("content")) or self.sku
        classes = set(str(values.get("class") or "").split())
        if self._visible_depth:
            self._visible_depth += 1
        elif classes & {"product-info-main", "product-info-price", "product-add-form"}:
            self._visible_depth = 1

    def handle_data(self, data: str) -> None:
        if self._visible_depth:
            value = clean_text(data)
            if value:
                self._visible_parts.append(value)

    def handle_endtag(self, tag: str) -> None:
        if self._visible_depth:
            self._visible_depth -= 1
