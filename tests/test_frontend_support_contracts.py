import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import APIConnectionError

from app.main import app
from app.models import RoomDimensions
from app.services.room_analysis_service import get_or_create_room_analysis


class FrontendTemplateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_read_only_template_list_uses_real_database_rows(self):
        response = self.client.get("/designs/templates")
        self.assertEqual(response.status_code, 200)
        templates = response.json()
        self.assertGreater(len(templates), 0)
        self.assertIn("matched_product_count", templates[0])
        self.assertTrue(templates[0]["preview_image_url"].startswith(
            "/uploads/design_templates/"
        ))

    def test_template_detail_exposes_products_and_unresolved_sources(self):
        template_id = self.client.get("/designs/templates").json()[0]["id"]
        response = self.client.get(f"/designs/templates/{template_id}")
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail["id"], template_id)
        self.assertIn("represented_products", detail)
        self.assertIn("available_matched_catalog_products", detail)
        self.assertIn("unresolved_slots", detail)
        self.assertIn("estimated_product_total", detail)

    def test_unknown_template_returns_404(self):
        response = self.client.get("/designs/templates/999999")
        self.assertEqual(response.status_code, 404)


class RoomAnalysisConnectionFallbackTests(unittest.TestCase):
    def test_transport_failure_uses_measured_facts_without_visual_invention(self):
        connection_error = APIConnectionError(
            request=httpx.Request("POST", "https://api.openai.com/v1/responses")
        )
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "room.jpg"
            image_path.write_bytes(b"room-image")
            dimensions = RoomDimensions(length_cm=500, width_cm=400)
            with (
                patch(
                    "app.services.room_analysis_service.get_saved_room_analysis",
                    return_value=None,
                ),
                patch(
                    "app.services.room_analysis_service.analyze_room_image",
                    side_effect=connection_error,
                ),
                patch("app.services.room_analysis_service.save_room_analysis") as save,
            ):
                analysis, cached = get_or_create_room_analysis(
                    room_request_id=99,
                    image_path=image_path,
                    room_type="living_room",
                    room_dimensions=dimensions,
                )

        self.assertFalse(cached)
        self.assertEqual(analysis.analysis_source, "measured_fallback")
        self.assertEqual(analysis.layout_shape, "rectangular")
        self.assertEqual(analysis.obstacles, [])
        self.assertEqual(analysis.usable_walls, [])
        self.assertIsNotNone(analysis.analysis_warning)
        save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
