from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any

from app.catalog_import.base import RetailerAdapter
from app.catalog_import.http import CachedHttpClient
from app.catalog_import.models import DiscoveredProduct, NormalizedProduct
from app.catalog_import.parsing import (
    canonicalize_url,
    clean_text,
    first_json_ld_product,
    parse_decimal,
    parse_labeled_dimensions,
)
from app.catalog_import.policy import default_room_types


class IkeaJordanCeilingLightsAdapter(RetailerAdapter):
    adapter_id = "ikea_jo_ceiling_lights"
    source_store = "IKEA Jordan"
    source_store_slug = "ikea_jo"
    base_url = "https://www.ikea.com/jo/ar/"
    search_url = "https://sik.search.blue.cdtapps.com/jo/ar/search?c=listaf&v=20250507"
    category_pages = {
        "pendant_lights": "https://www.ikea.com/jo/ar/cat/pendant-lamps-18751/",
        "ceiling_lamps": "https://www.ikea.com/jo/ar/cat/ceiling-lamps-18752/",
        "ceiling_spotlights": "https://www.ikea.com/jo/ar/cat/ceiling-spotlights-18753/",
        "chandeliers": "https://www.ikea.com/jo/ar/cat/chandeliers-59307/",
    }
    category_mapping = {
        "pendant_lights": "pendant_light",
        "ceiling_lamps": "ceiling_light",
        "ceiling_spotlights": "ceiling_spotlight",
        "chandeliers": "chandelier",
    }
    category_ids = {
        "pendant_lights": "18751",
        "ceiling_lamps": "18752",
        "ceiling_spotlights": "18753",
        "chandeliers": "59307",
    }
    root_category_id = "18750"
    window_size = 24

    def _request_payload(self, category_id: str, offset: int) -> dict[str, Any]:
        return {
            "searchParameters": {"input": category_id, "type": "CATEGORY"},
            "isUserLoggedIn": False,
            "isB2B": False,
            "components": [
                {
                    "component": "PRIMARY_AREA",
                    "columns": 4,
                    "types": {"main": "PRODUCT", "breakouts": []},
                    "filterConfig": {"max-num-filters": 5},
                    "window": {"size": self.window_size, "offset": offset},
                    "allVariants": False,
                    "forceFilterCalculation": offset == 0,
                }
            ],
        }

    def _component(self, response: dict[str, Any]) -> dict[str, Any]:
        for result in response.get("results", []):
            if result.get("component") == "PRIMARY_AREA":
                return result
        raise ValueError("IKEA response has no PRIMARY_AREA component")

    @staticmethod
    def _availability(product: dict[str, Any]) -> bool | None:
        statuses = {
            str(value.get("status", "")).strip().upper()
            for value in product.get("availability", [])
            if isinstance(value, dict)
        }
        if statuses & {"OUT_OF_STOCK", "NOT_AVAILABLE", "UNAVAILABLE", "NO_STOCK"}:
            return False
        if statuses & {
            "HIGH_IN_STOCK",
            "LOW_IN_STOCK",
            "IN_STOCK",
            "AVAILABLE",
        }:
            return True
        if product.get("onlineSellable") is False:
            return False
        if product.get("onlineSellable") is True:
            return True
        return None

    def _normalized_category(self, product: dict[str, Any]) -> str:
        keys = {
            str(value.get("key"))
            for value in product.get("categoryPath", [])
            if isinstance(value, dict)
        }
        for source_name, category_id in self.category_ids.items():
            if category_id in keys:
                return self.category_mapping[source_name]
        text = " ".join(
            str(value or "")
            for value in (
                product.get("typeName"),
                product.get("filterClass"),
                (product.get("businessStructure") or {}).get("productAreaName"),
            )
        ).casefold()
        if "chandelier" in text or "\u062b\u0631\u064a\u0627" in text:
            return "chandelier"
        if "spotlight" in text or "spot light" in text or "\u0645\u0648\u0636\u0639\u064a" in text:
            return "ceiling_spotlight"
        if "pendant" in text or "\u0645\u0639\u0644" in text:
            return "pendant_light"
        return "ceiling_light"

    @staticmethod
    def _expanded_products(product: dict[str, Any]) -> list[dict[str, Any]]:
        expanded = [dict(product)]
        for variant in (product.get("gprDescription") or {}).get("variants", []):
            if not isinstance(variant, dict):
                continue
            combined = dict(product)
            combined.update(variant)
            combined["categoryPath"] = product.get("categoryPath", [])
            combined["businessStructure"] = product.get("businessStructure", {})
            combined["colors"] = variant.get("colors", product.get("colors", []))
            if "allProductImage" not in combined and variant.get("imageUrl"):
                combined["allProductImage"] = [
                    {"type": "MAIN_PRODUCT_IMAGE", "url": variant["imageUrl"]}
                ]
            expanded.append(combined)
        return expanded

    def discover(
        self,
        client: CachedHttpClient,
        category_filter: Sequence[str] | None = None,
    ) -> Iterable[DiscoveredProduct]:
        selected = self.selected_category_pages(category_filter)
        category_ids = (
            [self.root_category_id]
            if not category_filter
            else [self.category_ids[name] for name in selected]
        )
        seen: set[str] = set()
        discovered: list[DiscoveredProduct] = []
        self.last_discovery_pages_scanned = 0
        headers = {
            "Origin": "https://www.ikea.com",
            "Referer": "https://www.ikea.com/jo/ar/cat/ceiling-lights-18750/",
        }
        for category_id in category_ids:
            offset = 0
            maximum: int | None = None
            while maximum is None or offset < maximum:
                response = client.post_json(
                    self.search_url,
                    self._request_payload(category_id, offset),
                    headers=headers,
                    max_bytes=12_000_000,
                )
                component = self._component(response)
                self.last_discovery_pages_scanned += 1
                items = component.get("items", [])
                metadata = component.get("metadata", {})
                maximum = int(metadata.get("max") or len(items))
                if not items:
                    break
                for wrapper in items:
                    source = wrapper.get("product") if isinstance(wrapper, dict) else None
                    if not isinstance(source, dict):
                        continue
                    for product in self._expanded_products(source):
                        source_id = clean_text(
                            product.get("itemNo")
                            or product.get("itemNoGlobal")
                            or product.get("id")
                        )
                        url = clean_text(product.get("pipUrl"))
                        if not source_id or not url or source_id in seen:
                            continue
                        seen.add(source_id)
                        normalized_category = self._normalized_category(product)
                        discovered.append(
                            DiscoveredProduct(
                                url=canonicalize_url(url),
                                source_product_id=source_id,
                                source_category=normalized_category,
                                normalized_category=normalized_category,
                                name_hint=clean_text(product.get("name")),
                                availability_hint=self._availability(product),
                                payload=product,
                            )
                        )
                offset += len(items)
        return discovered

    @staticmethod
    def _image_urls(product_json: dict[str, Any], payload: dict[str, Any]) -> list[str]:
        urls: list[str] = []
        images = product_json.get("image", [])
        if not isinstance(images, list):
            images = [images]
        for image in images:
            url = image.get("contentUrl") if isinstance(image, dict) else image
            if url and str(url) not in urls:
                urls.append(str(url))
        for image in payload.get("allProductImage", []):
            url = image.get("url") if isinstance(image, dict) else None
            if url and str(url) not in urls:
                urls.append(str(url))
        fallback = payload.get("mainImageUrl") or payload.get("imageUrl")
        if fallback and str(fallback) not in urls:
            urls.insert(0, str(fallback))
        return urls

    @staticmethod
    def _original_price(payload: dict[str, Any]) -> Decimal | None:
        sales_price = payload.get("salesPrice") or {}
        candidates = [
            sales_price.get("previousPrice"),
            sales_price.get("regularPrice"),
            sales_price.get("originalPrice"),
            (sales_price.get("previous") or {}).get("numeral")
            if isinstance(sales_price.get("previous"), dict)
            else sales_price.get("previous"),
        ]
        current = parse_decimal(sales_price.get("numeral"))
        for candidate in candidates:
            if isinstance(candidate, dict):
                candidate = candidate.get("numeral") or candidate.get("value")
            parsed = parse_decimal(candidate)
            if parsed is not None and current is not None and parsed > current:
                return parsed
        return None

    def parse_product(
        self,
        reference: DiscoveredProduct,
        client: CachedHttpClient,
    ) -> NormalizedProduct:
        html_text = client.get_text(reference.url, max_bytes=3_000_000)
        structured = first_json_ld_product(html_text)
        if structured is None:
            raise ValueError("IKEA product page has no Product JSON-LD")
        payload = reference.payload
        offers = structured.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        current_price = parse_decimal(
            (payload.get("salesPrice") or {}).get("numeral")
            or offers.get("price")
        )
        if current_price is None:
            raise ValueError("IKEA product has no public JOD price")
        currency = clean_text(
            (payload.get("salesPrice") or {}).get("currencyCode")
            or offers.get("priceCurrency")
        )
        if currency != "JOD":
            raise ValueError(f"Unexpected IKEA currency: {currency!r}")

        measurements = parse_labeled_dimensions(
            " ".join(_ProductMeasurementParser.extract(html_text))
        )
        source_id = clean_text(structured.get("sku") or structured.get("mpn"))
        if not source_id:
            source_id = reference.source_product_id
        name = clean_text(structured.get("name") or payload.get("name"))
        if not name:
            raise ValueError("IKEA product has no name")
        category = reference.normalized_category or self._normalized_category(payload)
        availability = self._availability(payload)
        structured_availability = clean_text(offers.get("availability"))
        if structured_availability:
            if structured_availability.endswith("/OutOfStock"):
                availability = False
            elif structured_availability.endswith("/InStock"):
                availability = True

        images = self._image_urls(structured, payload)
        colors = payload.get("colors") or []
        payload_color = clean_text(
            "/".join(
                str(item.get("name"))
                for item in colors
                if isinstance(item, dict) and item.get("name")
            )
        )
        structured_color = structured.get("color")
        if isinstance(structured_color, list):
            structured_color = "/".join(str(value) for value in structured_color)
        category_path = [
            clean_text(item.get("name"))
            for item in payload.get("categoryPath", [])
            if isinstance(item, dict) and clean_text(item.get("name"))
        ]
        return NormalizedProduct(
            adapter_id=self.adapter_id,
            source_store=self.source_store,
            source_store_slug=self.source_store_slug,
            source_product_id=source_id,
            canonical_url=canonicalize_url(
                str(structured.get("url") or offers.get("url") or reference.url)
            ),
            name=name,
            name_ar=name,
            name_en=clean_text(payload.get("name")),
            category=category,
            subcategory=clean_text(structured.get("category"))
            or (category_path[-1] if category_path else None),
            current_price=current_price,
            original_price=self._original_price(payload),
            currency="JOD",
            availability=availability,
            image_urls=images,
            width_cm=measurements["width_cm"],
            depth_cm=measurements["depth_cm"],
            height_cm=measurements["height_cm"],
            diameter_cm=measurements["diameter_cm"],
            color=clean_text(structured_color) or payload_color,
            material=clean_text(structured.get("material")),
            room_types=default_room_types(category),
            description=clean_text(structured.get("description")),
            brand="IKEA",
            model_number=clean_text(structured.get("mpn")) or source_id,
            mounting_type="ceiling",
            tags=[value for value in (clean_text(payload.get("typeName")),) if value],
            source_metadata={
                "category_path": category_path,
                "item_measure_reference_text": clean_text(
                    payload.get("itemMeasureReferenceText")
                ),
                "online_sellable": payload.get("onlineSellable"),
                "home_delivery": payload.get("homeDelivery"),
                "product_area": clean_text(
                    (payload.get("businessStructure") or {}).get("productAreaName")
                ),
            },
        )


class _ProductMeasurementParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._parts: list[str] = []
        self.measurements: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = str(dict(attrs).get("class") or "").split()
        if self._depth:
            self._depth += 1
        elif tag.casefold() == "li" and "pipf-measurements-modal__product-measurement-wrapper" in classes:
            self._depth = 1
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._depth:
            return
        self._depth -= 1
        if self._depth == 0:
            value = clean_text(" ".join(self._parts))
            if value:
                self.measurements.append(value)
            self._parts = []

    @classmethod
    def extract(cls, html_text: str) -> list[str]:
        parser = cls()
        parser.feed(html_text)
        return parser.measurements
