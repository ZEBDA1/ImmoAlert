from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ["BOT_TOKEN"] = "test-token"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ENABLE_GEOCODING"] = "false"
os.environ["AI_SCAM_ENABLED"] = "false"

from scraper.base import ScrapedListing
from scraper.quality import normalize_listing
from services.matcher import match_and_score


def _filter(**overrides):
    data = {
        "price_min": None,
        "price_max": None,
        "surface_min": None,
        "rooms_min": None,
        "property_type": None,
        "budget_max_with_charges": None,
        "city": "Mantes-La-Jolie",
        "postal_code": "78200",
        "radius_km": 6.0,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class LocationMatchingTests(unittest.TestCase):
    def test_matches_mantes_variants(self):
        listing = ScrapedListing(
            source="paruvendu",
            external_id="1",
            url="https://example.test/1",
            title="Location Appartement 2 pieces 48 m2 Mantes la jolie (78200)",
            price=746,
            location="Mantes la jolie (78200)",
        )

        self.assertTrue(match_and_score(_filter(), listing).matched)
        self.assertIn("postal_code:78200", match_and_score(_filter(), listing).reasons or [])

    def test_rejects_lyon_for_mantes_filter(self):
        listing = ScrapedListing(
            source="entreparticuliers",
            external_id="2",
            url="https://example.test/location/lyon-69001/ref-2",
            title="Lyon (69001) Appartement a louer 2 pieces 44 m2 578 EUR",
            price=578,
            location="Lyon (69001)",
        )

        self.assertFalse(match_and_score(_filter(), listing).matched)
        self.assertEqual(match_and_score(_filter(), listing).reject_reason, "geocode_unavailable")

    def test_rejects_unknown_location_when_city_is_required(self):
        listing = ScrapedListing(
            source="leboncoin",
            external_id="3",
            url="https://example.test/annonce/3",
            title="Appartement lumineux",
            price=700,
            location=None,
        )

        self.assertFalse(match_and_score(_filter(postal_code=None, radius_km=None), listing).matched)
        self.assertEqual(
            match_and_score(_filter(postal_code=None, radius_km=None), listing).reject_reason,
            "city_mismatch:Mantes-La-Jolie",
        )

    def test_matches_paris_arrondissement_text(self):
        listing = ScrapedListing(
            source="example",
            external_id="paris-11",
            url="https://example.test/paris-11",
            title="T2 lumineux Paris 11e proche metro",
            price=1200,
            location="Paris 11e",
            rooms=2,
        )

        self.assertTrue(match_and_score(_filter(city="Paris", postal_code="75011", radius_km=None), listing).matched)

    def test_normalize_does_not_invent_location_from_search_context(self):
        listing = normalize_listing(
            ScrapedListing(
                source="example",
                external_id="4",
                url="https://example.test/annonce/4",
                title="Appartement",
                location=None,
            ),
            fallback_city="Mantes-La-Jolie",
            fallback_postal="78200",
        )

        self.assertIsNotNone(listing)
        self.assertIsNone(listing.location)


if __name__ == "__main__":
    unittest.main()
