# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse


TRACKING_QUERY_KEYS = {
    "srsltid",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_",
    "source",
}

ARABIC_DECIMAL_SEPARATOR = "\u066b"
ARABIC_THOUSANDS_SEPARATOR = "\u066c"


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(html.unescape(str(value)).replace("\xa0", " ").split()).strip()
    return cleaned or None


def canonicalize_url(
    raw_url: str,
    *,
    base_url: str | None = None,
    keep_query_keys: set[str] | None = None,
) -> str:
    absolute = urljoin(base_url or raw_url, raw_url)
    parsed = urlparse(absolute.strip())
    keep = {key.lower() for key in (keep_query_keys or set())}
    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in TRACKING_QUERY_KEYS:
            continue
        if keep and lowered not in keep:
            continue
        if not keep:
            continue
        query.append((key, value))
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunparse(
        (
            parsed.scheme.lower() or "https",
            parsed.netloc.lower(),
            path,
            "",
            urlencode(query, doseq=True),
            "",
        )
    )


def latinize_digits(value: Any) -> str:
    """Normalize Unicode decimal digits and Arabic separators safely."""

    output: list[str] = []
    for character in str(value or ""):
        if character == ARABIC_DECIMAL_SEPARATOR:
            output.append(".")
            continue
        if character == ARABIC_THOUSANDS_SEPARATOR:
            output.append(",")
            continue
        try:
            output.append(str(unicodedata.digit(character)))
        except (TypeError, ValueError):
            output.append(character)
    return "".join(output)


def parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    normalized = latinize_digits(value)
    normalized = normalized.replace("\u200e", "").replace("\u200f", "")
    normalized = normalized.replace("\u202a", "").replace("\u202c", "")
    matches = re.findall(r"-?\d[\d,]*(?:\.\d+)?", normalized)
    if not matches:
        return None
    candidate = matches[-1].replace(",", "")
    try:
        return Decimal(candidate)
    except InvalidOperation:
        return None


def parse_price_pair(text: str | None) -> tuple[Decimal | None, Decimal | None]:
    """Return (current, original), supporting Arabic and Latin numerals."""

    if not text:
        return None, None
    normalized = latinize_digits(html.unescape(text))
    values = [
        parse_decimal(match)
        for match in re.findall(r"\d[\d,]*(?:\.\d+)?", normalized)
    ]
    values = [value for value in values if value is not None]
    if not values:
        return None, None
    lowered = normalized.lower()
    sale_markers = (
        "special price",
        "sale price",
        "السعر الخاص",
        "سعر الخصم",
    )
    regular_markers = (
        "regular price",
        "original price",
        "السعر العادي",
        "السعر السابق",
    )
    if (
        any(marker in lowered for marker in sale_markers + regular_markers)
        and len(values) >= 2
    ):
        current = min(values)
        original = max(values)
        return current, original if original > current else None
    return values[0], None


DIMENSION_LABELS = {
    "width_cm": ("width", "w", "العرض", "عرض"),
    "depth_cm": ("depth", "d", "العمق", "لعمق", "عمق"),
    "height_cm": ("height", "h", "الارتفاع", "الإرتفاع", "ارتفاع", "إرتفاع"),
    "diameter_cm": (
        "shade diameter",
        "cover diameter",
        "diameter",
        "dia",
        "قطر غطاء المصباح",
        "قطر الغطاء",
        "القطر",
        "قطر",
    ),
}


def _to_cm(number: Decimal, unit: str | None) -> Decimal:
    normalized = (unit or "cm").lower()
    if normalized in {"mm", "ملم", "مم"}:
        return number / Decimal(10)
    if normalized in {"m", "م"}:
        return number * Decimal(100)
    return number


def parse_labeled_dimensions(text: str | None) -> dict[str, Decimal | None]:
    """Parse only explicitly labelled dimensions; package inference is adapter-owned."""

    result: dict[str, Decimal | None] = {
        "width_cm": None,
        "depth_cm": None,
        "height_cm": None,
        "diameter_cm": None,
    }
    if not text:
        return result
    normalized = latinize_digits(html.unescape(text)).replace("×", "x")
    unit_pattern = r"(mm|cm|m|ملم|مم|سم|م)?"
    for field_name, labels in DIMENSION_LABELS.items():
        # A bare ``h``/``w``/``d`` must not match inside words such as
        # ``Width``.  It may, however, be immediately followed by a number
        # (``W228``), so a normal trailing word boundary is too restrictive.
        label_alternatives = []
        for label in labels:
            escaped = re.escape(label)
            if len(label) == 1 and label.isascii():
                label_alternatives.append(
                    rf"(?<![A-Za-z]){escaped}(?=\s*[:=]?\s*\d)"
                )
            else:
                # Unicode digits may immediately follow a full Arabic/English
                # label (``العرض40``). Block surrounding letters, not digits.
                label_alternatives.append(
                    rf"(?<![^\W\d_]){escaped}(?![^\W\d_])"
                )
        label_pattern = "|".join(label_alternatives)
        pattern = (
            rf"(?:{label_pattern})\s*[:=]?\s*(\d+(?:\.\d+)?)\s*{unit_pattern}"
        )
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            number = parse_decimal(match.group(1))
            if number is not None:
                result[field_name] = _to_cm(number, match.group(2))

    # Midas frequently uses one trailing unit for an explicitly-labelled W/D/H triplet.
    triplet = re.search(
        r"(?:width|w|العرض|عرض)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*"
        r"(?:x|\*)\s*(?:depth|d|العمق|عمق)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*"
        r"(?:x|\*)\s*(?:height|h|الارتفاع|الإرتفاع|ارتفاع|إرتفاع)\s*[:=]?\s*"
        r"(\d+(?:\.\d+)?)\s*(mm|cm|m|ملم|مم|سم|م)",
        normalized,
        flags=re.IGNORECASE,
    )
    if triplet:
        unit = triplet.group(4)
        for field_name, raw in zip(
            ("width_cm", "depth_cm", "height_cm"),
            triplet.groups()[:3],
            strict=True,
        ):
            number = parse_decimal(raw)
            if number is not None:
                result[field_name] = _to_cm(number, unit)
    return result


def parse_dimension_triplet(text: str | None) -> tuple[Decimal, Decimal, Decimal] | None:
    """Parse a source field explicitly documented as width x depth x height."""

    if not text:
        return None
    normalized = latinize_digits(html.unescape(text)).replace("\u00d7", "x")
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*[x*]\s*(\d+(?:\.\d+)?)\s*[x*]\s*"
        r"(\d+(?:\.\d+)?)\s*(mm|cm|m|\u0645\u0644\u0645|\u0645\u0645|\u0633\u0645|\u0645)\b",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    first = parse_decimal(match.group(1))
    second = parse_decimal(match.group(2))
    third = parse_decimal(match.group(3))
    if first is None or second is None or third is None:
        return None
    unit = match.group(4)
    return (_to_cm(first, unit), _to_cm(second, unit), _to_cm(third, unit))


def parse_availability(text: str | None) -> bool | None:
    """Return explicit stock evidence; absence of evidence remains unknown."""

    normalized = clean_text(latinize_digits(text))
    if not normalized:
        return None
    value = normalized.casefold()
    unavailable_markers = (
        "out of stock",
        "not available",
        "unavailable",
        "sold out",
        "\u063a\u064a\u0631 \u0645\u062a\u0648\u0641\u0631",
        "\u0646\u0641\u062f\u062a \u0627\u0644\u0643\u0645\u064a\u0629",
        "\u0646\u0627\u0641\u062f",
    )
    if any(marker in value for marker in unavailable_markers):
        return False
    available_markers = (
        "in stock",
        "available",
        "add to cart",
        "\u0645\u062a\u0648\u0641\u0631",
        "\u0623\u0636\u0641 \u0625\u0644\u0649 \u0627\u0644\u0633\u0644\u0629",
        "\u0627\u0636\u0641 \u0627\u0644\u0649 \u0627\u0644\u0633\u0644\u0629",
    )
    if any(marker in value for marker in available_markers):
        return True
    return None


def normalized_identity(
    *,
    name: str,
    width_cm: Decimal | None,
    depth_cm: Decimal | None,
    height_cm: Decimal | None,
    variant_values: list[str] | None = None,
) -> str:
    text = unicodedata.normalize("NFKC", name).casefold()
    text = re.sub(r"[^\w\u0600-\u06ff]+", " ", text, flags=re.UNICODE)
    text = " ".join(text.split())
    dimensions = "x".join(
        str(value.normalize()) if value is not None else ""
        for value in (width_cm, depth_cm, height_cm)
    )
    variants = "|".join(
        sorted(clean_text(value).casefold() for value in (variant_values or []) if clean_text(value))
    )
    return f"{text}|{dimensions}|{variants}".strip("|")


def first_json_ld_product(html_text: str) -> dict[str, Any] | None:
    for node in extract_json_ld(html_text):
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if any(str(value).casefold() == "product" for value in types):
            return node
    return None


def extract_json_ld(html_text: str) -> list[dict[str, Any]]:
    parser = _JsonLdParser()
    parser.feed(html_text)
    nodes: list[dict[str, Any]] = []
    for raw in parser.scripts:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        nodes.extend(_flatten_json_nodes(parsed))
    return nodes


def _flatten_json_nodes(value: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    if isinstance(value, dict):
        nodes.append(value)
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                nodes.extend(_flatten_json_nodes(item))
    elif isinstance(value, list):
        for item in value:
            nodes.extend(_flatten_json_nodes(item))
    return nodes


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._collecting = False
        self._parts: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value for key, value in attrs}
        if tag.casefold() == "script" and str(values.get("type", "")).casefold() == "application/ld+json":
            self._collecting = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._collecting:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._collecting:
            self.scripts.append("".join(self._parts).strip())
            self._collecting = False
            self._parts = []
