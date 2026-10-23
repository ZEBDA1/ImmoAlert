from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ["BOT_TOKEN"] = "test-token"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ENABLE_GEOCODING"] = "false"
os.environ["AI_SCAM_ENABLED"] = "false"

from services.ai_features import ai_parse_filter


class AIFeatureTests(unittest.TestCase):
    def test_invalid_ai_json_falls_back_to_local_parser(self):
        with patch("services.ai_features.call_ai", return_value={"city": "X", "confidence": 0.99}):
            parsed = ai_parse_filter("Paris 11, 2 pieces, 35 m2 min, 1200 euros max")

        self.assertEqual(parsed["city"], "Paris")
        self.assertEqual(parsed["postal_code"], "75011")
        self.assertEqual(parsed["rooms_min"], 2)
        self.assertEqual(parsed["surface_min"], 35.0)
        self.assertEqual(parsed["price_max"], 1200)

    def test_ai_numeric_fields_are_strictly_cleaned(self):
        with patch(
            "services.ai_features.call_ai",
            return_value={
                "city": "Lyon",
                "postal_code": "69001",
                "price_min": "1400",
                "price_max": "800",
                "surface_min": "30,5",
                "rooms_min": "2",
                "property_type": "flat",
                "budget_max_with_charges": "",
                "radius_km": "-2",
            },
        ):
            parsed = ai_parse_filter("Lyon 69001")

        self.assertEqual(parsed["city"], "Lyon")
        self.assertEqual(parsed["postal_code"], "69001")
        self.assertEqual(parsed["price_min"], 800)
        self.assertEqual(parsed["price_max"], 1400)
        self.assertEqual(parsed["surface_min"], 30.5)
        self.assertEqual(parsed["rooms_min"], 2)
        self.assertNotIn("property_type", parsed)
        self.assertNotIn("radius_km", parsed)


if __name__ == "__main__":
    unittest.main()
