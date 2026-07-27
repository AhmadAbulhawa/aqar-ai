from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

from app.services.image_prep_service import prepare_furniture_reference_images


class ProductReferenceQualityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.output = self.root / "prepared"
        self.item = {
            "id": 35,
            "name": "HYLTARP corner sofa, 4-seat",
            "category": "sofa",
            "image_filename": "hyltarp.png",
        }

    def tearDown(self):
        self.directory.cleanup()

    def test_tiny_reference_is_blocked_before_any_paid_render(self):
        source = self.root / "tiny.png"
        Image.new("RGB", (160, 160), "white").save(source)
        with (
            patch(
                "app.services.image_prep_service.get_furniture_image_path",
                return_value=source,
            ),
            patch(
                "app.services.image_prep_service.RENDER_REFERENCE_DIR",
                self.output,
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                prepare_furniture_reference_images([self.item])

        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(
            raised.exception.detail["error_code"],
            "product_reference_too_small",
        )
        self.assertFalse(raised.exception.detail["automatic_paid_request_sent"])
        self.assertEqual(raised.exception.detail["products"][0]["width_px"], 160)

    def test_valid_reference_is_normalized_to_a_1024_square_board(self):
        source = self.root / "source.png"
        Image.new("RGBA", (1200, 700), (120, 160, 190, 255)).save(source)
        with (
            patch(
                "app.services.image_prep_service.get_furniture_image_path",
                return_value=source,
            ),
            patch(
                "app.services.image_prep_service.RENDER_REFERENCE_DIR",
                self.output,
            ),
        ):
            prepared = prepare_furniture_reference_images([self.item])

        prepared_path = Path(prepared[0]["local_image_path"])
        self.assertTrue(prepared_path.exists())
        with Image.open(prepared_path) as image:
            self.assertEqual(image.size, (1024, 1024))
        self.assertEqual(prepared[0]["source_image_width"], 1200)
        self.assertEqual(prepared[0]["prepared_image_width"], 1024)


if __name__ == "__main__":
    unittest.main()
