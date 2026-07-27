from __future__ import annotations

import unittest

from app.routes.design_routes import _refresh_immutable_product_reference_images


class CatalogReferenceRefreshTests(unittest.TestCase):
    def test_only_reference_fields_refresh_for_the_same_locked_product_id(self):
        snapshot = {
            "id": 35,
            "name": "HYLTARP corner sofa, 4-seat",
            "quantity": 2,
            "width_cm": 264,
            "depth_cm": 264,
            "image_filename": "old-160.png",
            "source_image_url": "https://old.invalid/hyltarp.png",
        }
        current = {
            "id": 35,
            "name": "Catalog display name may change",
            "quantity": 1,
            "width_cm": 999,
            "depth_cm": 999,
            "image_filename": "official-900.jpg",
            "source_image_url": "https://official.invalid/hyltarp.jpg",
            "additional_source_image_urls": ["https://official.invalid/side.jpg"],
        }

        refreshed = _refresh_immutable_product_reference_images(
            [snapshot],
            [current],
        )[0]

        self.assertEqual(refreshed["id"], 35)
        self.assertEqual(refreshed["name"], snapshot["name"])
        self.assertEqual(refreshed["quantity"], 2)
        self.assertEqual(refreshed["width_cm"], 264)
        self.assertEqual(refreshed["depth_cm"], 264)
        self.assertEqual(refreshed["image_filename"], "official-900.jpg")
        self.assertEqual(
            refreshed["source_image_url"],
            "https://official.invalid/hyltarp.jpg",
        )

        # The persisted snapshot itself remains immutable.
        self.assertEqual(snapshot["image_filename"], "old-160.png")


if __name__ == "__main__":
    unittest.main()
