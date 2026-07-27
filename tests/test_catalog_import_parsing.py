# -*- coding: utf-8 -*-
from decimal import Decimal
import unittest

from app.catalog_import.parsing import (
    canonicalize_url,
    clean_text,
    latinize_digits,
    parse_availability,
    parse_decimal,
    parse_dimension_triplet,
    parse_labeled_dimensions,
    parse_price_pair,
)


class CatalogImportUnicodeParsingTests(unittest.TestCase):
    def test_arabic_and_eastern_arabic_digits_are_normalized(self):
        self.assertEqual(latinize_digits("\u0661\u0662\u0669\u066b\u0669\u0669"), "129.99")
        self.assertEqual(latinize_digits("\u06f1\u06f2\u06f9\u066b\u06f9\u06f9"), "129.99")

    def test_jod_prices_in_arabic_and_english(self):
        self.assertEqual(parse_decimal("\u0661\u0662\u0669\u066b\u0669\u0669 \u062f.\u0623"), Decimal("129.99"))
        self.assertEqual(parse_decimal("129.99 JOD"), Decimal("129.99"))
        self.assertEqual(
            parse_price_pair("sale price 99.99 JOD original price 129.99 JOD"),
            (Decimal("99.99"), Decimal("129.99")),
        )

    def test_arabic_and_latin_dimension_triplets(self):
        self.assertEqual(
            parse_dimension_triplet("\u0661\u0662\u0660 \u00d7 \u0666\u0660 \u00d7 \u0664\u0665 \u0633\u0645"),
            (Decimal("120"), Decimal("60"), Decimal("45")),
        )
        self.assertEqual(
            parse_dimension_triplet("120 x 60 x 45 cm"),
            (Decimal("120"), Decimal("60"), Decimal("45")),
        )

    def test_mixed_arabic_english_labeled_dimensions(self):
        dimensions = parse_labeled_dimensions(
            "W \u0661\u0662\u0660 cm x \u0627\u0644\u0639\u0645\u0642 \u0666\u0660 cm x H 45 cm"
        )
        self.assertEqual(dimensions["width_cm"], Decimal("120"))
        self.assertEqual(dimensions["depth_cm"], Decimal("60"))
        self.assertEqual(dimensions["height_cm"], Decimal("45"))

    def test_ikea_arabic_shade_diameter_label(self):
        dimensions = parse_labeled_dimensions(
            "الإرتفاع: 24 سم قطر الغطاء: 46 سم"
        )
        self.assertEqual(dimensions["height_cm"], Decimal("24"))
        self.assertEqual(dimensions["diameter_cm"], Decimal("46"))

    def test_adjacent_arabic_dimension_labels_from_midas(self):
        dimensions = parse_labeled_dimensions(
            "العرض40 * لعمق30 * الارتفاع40 سم"
        )
        self.assertEqual(dimensions["width_cm"], Decimal("40"))
        self.assertEqual(dimensions["depth_cm"], Decimal("30"))
        self.assertEqual(dimensions["height_cm"], Decimal("40"))

    def test_arabic_availability_requires_explicit_evidence(self):
        self.assertFalse(parse_availability("\u063a\u064a\u0631 \u0645\u062a\u0648\u0641\u0631"))
        self.assertTrue(parse_availability("\u0645\u062a\u0648\u0641\u0631"))
        self.assertTrue(parse_availability("\u0623\u0636\u0641 \u0625\u0644\u0649 \u0627\u0644\u0633\u0644\u0629"))
        self.assertIsNone(parse_availability("\u0623\u062b\u0627\u062b \u0639\u0635\u0631\u064a"))

    def test_arabic_names_and_categories_remain_utf8(self):
        value = "\u0643\u0646\u0628 \u0645\u0648\u062f\u0631\u0646 - \u063a\u0631\u0641\u0629 \u0627\u0644\u0645\u0639\u064a\u0634\u0629"
        self.assertEqual(clean_text(value), value)

    def test_canonical_url_removes_tracking(self):
        self.assertEqual(
            canonicalize_url(
                "https://leaders.jo/en/product/example/?srsltid=tracking&utm_source=test"
            ),
            "https://leaders.jo/en/product/example",
        )


if __name__ == "__main__":
    unittest.main()
