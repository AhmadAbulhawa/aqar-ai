import unittest

from app.enums import RoomType
from app.models import DesignRequest, ExactProductRequirement, RoomDimensions
from app.services.unified_recommendation_service import (
    generate_exact_custom_recommendation,
)


def catalog_item(product_id: int, category: str) -> dict:
    return {
        "id": product_id,
        "name": f"Product {product_id}",
        "category": category,
        "style": "modern",
        "color": "beige",
        "material": "wood",
        "width_cm": 80,
        "depth_cm": 70,
        "height_cm": 75,
        "price": 100,
        "image_filename": f"{product_id}.jpg",
        "product_url": f"https://example.com/{product_id}",
        "store_name": "IKEA Jordan",
        "currency": "JOD",
        "room_types": ["living_room"],
        "tags": [],
        "availability": True,
        "catalog_status": "active",
        "quarantine_reason": None,
    }


class ExactCustomRecommendationTests(unittest.TestCase):
    def test_preserves_only_exact_ids_and_quantities(self):
        request = DesignRequest(
            room_type=RoomType.LIVING_ROOM,
            required_products=[ExactProductRequirement(product_id=30, quantity=2)],
            room_dimensions=RoomDimensions(length_cm=600, width_cm=500),
        )

        result = generate_exact_custom_recommendation(
            room_items=[catalog_item(30, "armchair"), catalog_item(31, "sofa")],
            request=request,
            room_analysis=None,
        )

        self.assertEqual(result["selected_products"], [{"id": 30, "quantity": 2}])
        self.assertEqual(result["selected_furniture_ids"], [30])
        self.assertEqual(result["selection_breakdown"]["system_completion_ids"], [])
        self.assertEqual(result["recommended_items"][0]["selection_origin"], "user")
        self.assertIsNone(result["design_strategy"])


if __name__ == "__main__":
    unittest.main()
