from __future__ import annotations

import os
import unittest
from datetime import datetime
from uuid import uuid4

os.environ["BOT_TOKEN"] = "test-token"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ENABLE_GEOCODING"] = "false"
os.environ["AI_SCAM_ENABLED"] = "false"

from database.session import init_db
from database import repo
from scraper.base import ScrapedListing
from scraper.quality import (
    charges_included,
    extract_postal_code,
    listing_quality_flags,
    normalize_city_name,
    normalize_listing,
    quality_signature,
)


class QualityAndRepoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_normalize_extracts_quality_metadata(self):
        listing = normalize_listing(
            ScrapedListing(
                source="example",
                external_id="q1",
                url="https://example.test/q1",
                title="Appartement T2 42 m2 Paris 11e",
                price=1200,
                location="Paris 11e (75011)",
                description="Loyer charges comprises",
            )
        )

        self.assertIsNotNone(listing)
        assert listing is not None
        self.assertEqual(extract_postal_code(listing.location), "75011")
        self.assertEqual(normalize_city_name(listing.location), "Paris 11e")
        self.assertTrue(charges_included(f"{listing.title} {listing.description}"))
        self.assertNotIn("low_signal", listing_quality_flags(listing))
        self.assertEqual(quality_signature(listing), quality_signature(listing))

    def test_save_listing_dedupes_by_canonical_signature_window(self):
        sig = f"sig-{uuid4().hex}"
        first = repo.save_listing(
            source="source_a",
            external_id=f"a-{uuid4().hex}",
            url="https://example.test/a",
            title="Annonce A",
            canonical_signature=sig,
        )
        second = repo.save_listing(
            source="source_b",
            external_id=f"b-{uuid4().hex}",
            url="https://example.test/b",
            title="Annonce B",
            canonical_signature=sig,
        )

        self.assertTrue(first.is_new)
        self.assertFalse(second.is_new)
        self.assertEqual(second.duplicate_reason, "canonical_signature")
        self.assertEqual(second.duplicate_listing_id, first.listing.id)

    def test_source_runs_are_persisted(self):
        cycle = f"cycle-{uuid4().hex}"
        repo.save_source_run(
            cycle_id=cycle,
            source="unit",
            status="success",
            started_at=datetime.utcnow(),
            unique_count=2,
            new_count=1,
        )
        runs = repo.get_latest_source_runs(limit=5)

        self.assertTrue(any(run.cycle_id == cycle and run.source == "unit" for run in runs))


if __name__ == "__main__":
    unittest.main()
