import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from fastapi import HTTPException
from openai import APIConnectionError, BadRequestError
from PIL import Image

from app.services.openai_image_service import (
    generate_furnished_room_image,
    get_openai_client,
)


class ImageServiceErrorHandlingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.room_path = self.root / "room.jpg"
        self.product_path = self.root / "product.webp"
        Image.new("RGB", (64, 48), "white").save(self.room_path, "JPEG")
        Image.new("RGB", (32, 32), "blue").save(self.product_path, "WEBP")
        self.item = {
            "id": 33,
            "name": "Locked sofa",
            "category": "sofa",
            "quantity": 2,
            "width_cm": 200,
            "depth_cm": 90,
            "height_cm": 80,
        }

    def tearDown(self):
        self.directory.cleanup()

    def render_with_error(self, error: Exception) -> HTTPException:
        client = MagicMock()
        client.images.edit.side_effect = error
        with (
            patch(
                "app.services.openai_image_service.get_openai_client",
                return_value=client,
            ),
            patch(
                "app.services.openai_image_service.choose_output_size",
                return_value="1536x1024",
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                generate_furnished_room_image(
                    room_request_id=30,
                    room_image_path=self.room_path,
                    furniture_reference_images=[
                        {"id": 33, "local_image_path": str(self.product_path)}
                    ],
                    recommended_items=[self.item],
                    room_type="living_room",
                    style="modern",
                )
        client.images.edit.assert_called_once()
        self.edit_kwargs = client.images.edit.call_args.kwargs
        self.sent_images = self.edit_kwargs["image"]
        return raised.exception

    def test_connection_failure_is_precise_and_never_auto_retried(self):
        error = APIConnectionError(
            request=httpx.Request("POST", "https://api.openai.com/v1/images/edits")
        )
        raised = self.render_with_error(error)

        self.assertEqual(raised.status_code, 503)
        self.assertEqual(raised.detail["service"], "openai_images")
        self.assertEqual(raised.detail["error_type"], "APIConnectionError")
        self.assertTrue(raised.detail["retryable"])
        self.assertFalse(raised.detail["automatic_retry_attempted"])
        self.assertTrue(raised.detail["selection_preserved"])
        self.assertEqual(raised.detail["input_image_count"], 2)
        self.assertEqual(
            [image_file[2] for image_file in self.sent_images],
            ["image/jpeg", "image/webp"],
        )
        self.assertNotIn("input_fidelity", self.edit_kwargs)

    def test_bad_request_exposes_safe_upstream_reason(self):
        request = httpx.Request("POST", "https://api.openai.com/v1/images/edits")
        response = httpx.Response(
            400,
            request=request,
            headers={"x-request-id": "req_image_test"},
        )
        error = BadRequestError(
            "Invalid image input",
            response=response,
            body={
                "error": {
                    "message": "Unsupported image format.",
                    "code": "invalid_image",
                }
            },
        )
        raised = self.render_with_error(error)

        self.assertEqual(raised.status_code, 422)
        self.assertEqual(raised.detail["error_code"], "invalid_image")
        self.assertEqual(raised.detail["request_id"], "req_image_test")
        self.assertIn("Unsupported image format", raised.detail["message"])
        self.assertFalse(raised.detail["retryable"])

    def test_openai_client_disables_sdk_retries_for_billable_edits(self):
        with (
            patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "test-key",
                    "OPENAI_IMAGE_TIMEOUT_SECONDS": "321",
                },
            ),
            patch("app.services.openai_image_service.OpenAI") as client_class,
        ):
            get_openai_client()

        client_class.assert_called_once_with(
            api_key="test-key",
            timeout=321.0,
            max_retries=0,
        )

    def test_unsupported_input_is_blocked_before_paid_client_creation(self):
        unsupported_path = self.root / "product.gif"
        Image.new("RGB", (32, 32), "red").save(unsupported_path, "GIF")
        with patch("app.services.openai_image_service.get_openai_client") as client:
            with self.assertRaises(HTTPException) as raised:
                generate_furnished_room_image(
                    room_request_id=30,
                    room_image_path=self.room_path,
                    furniture_reference_images=[
                        {"id": 33, "local_image_path": str(unsupported_path)}
                    ],
                    recommended_items=[self.item],
                    room_type="living_room",
                    style="modern",
                )

        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(raised.exception.detail["detected_format"], "GIF")
        self.assertFalse(
            raised.exception.detail["automatic_paid_request_sent"]
        )
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
