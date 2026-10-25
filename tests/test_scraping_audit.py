from __future__ import annotations

import asyncio
import json
import os
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ["BOT_TOKEN"] = "test-token"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ENABLE_GEOCODING"] = "false"
os.environ["AI_SCAM_ENABLED"] = "false"

from sqlalchemy.orm import sessionmaker
from database import repo
from database.session import Base, _make_engine
from scraper.base import ScrapedListing
from scraper.entreparticuliers import EntreParticuliersScraper
from scraper.paruvendu import ParuVenduScraper
from scraper.pap import PAPScraper
from scraper.leboncoin import LeboncoinScraper
from bs4 import BeautifulSoup
from scraper.ouestfranceimmo import OuestFranceImmoScraper
from scraper.html_helpers import jsonld_rentals
from scraper.quality import normalize_listing, quality_signature
from services.scheduler import _collect_source_listings, _persist_listing_candidate
from services.site_sync import global_snapshot, account_snapshot
from utils.fetch_diagnostics import note_problem
from utils.http import is_probably_blocked
from utils.images import decode_images, encode_images
from utils import firecrawl

FIXTURES = Path(__file__).parent / "fixtures" / "source_cards"


class CapturedPortalTests(unittest.TestCase):
    def test_leboncoin_native_data_keeps_price_array_geography_area_and_photos(self):
        soup = BeautifulSoup((FIXTURES / "leboncoin.html").read_text(), "html.parser")
        rows = LeboncoinScraper()._listings_from_next_data(soup)
        self.assertEqual(len(rows), 2)
        row = normalize_listing(rows[0])
        self.assertIsNotNone(row)
        self.assertEqual((row.price, row.surface_m2, row.rooms, row.location), (950, 57, 3, "Rouen 76000"))
        self.assertTrue(row.images)
        self.assertNotIn("[", str(row.price))

    def test_real_cards_keep_each_property_price_area_and_location(self):
        # Small DOM extracts from the live Rouen audit of 2026-10-24.
        cases = [
            (EntreParticuliersScraper(), "entreparticuliers", 690, 31, "Rouen (76000)"),
            (ParuVenduScraper(), "paruvendu", 1250, 63, "Rouen (76)"),
            (PAPScraper(), "pap", 460, 20, "Rouen (76000)"),
            (OuestFranceImmoScraper(), "ouestfranceimmo", 605, 46, "Rouen 76000"),
        ]
        for parser, name, price, area, location in cases:
            with self.subTest(source=name):
                rows = parser._parse_cards((FIXTURES / f"{name}.html").read_text())
                self.assertGreaterEqual(len(rows), 1)
                row = normalize_listing(rows[0])
                self.assertIsNotNone(row)
                self.assertEqual((row.price, row.surface_m2, row.location), (price, area, location))
                self.assertTrue(row.images)
                self.assertNotEqual(row.title, "En attente de photo")

    def test_rentola_schema_excludes_faq_prices_and_does_not_invent_rooms(self):
        html = (FIXTURES / "rentola.html").read_text()
        rows = jsonld_rentals(html, "rentola", "https://rentola.fr")
        self.assertEqual((rows[0].price, rows[0].surface_m2, rows[0].rooms), (556, 60, 4))
        self.assertTrue(rows[0].images)
        data = {"@type":"RealEstateListing", "name":"Appartement", "url":"/listings/test",
                "offers":{"price":800, "itemOffered":{"numberOfBedrooms":2, "floorSize":{"value":40}, "address":{"addressLocality":"Rouen"}}}, "image":[{}]}
        fake_faq = {"@type":"FAQPage", "mainEntity":[{"@type":"Question", "name":"Loyer moyen", "acceptedAnswer":{"text":"1200 € 60 m²"}}]}
        text = ''.join('<script type="application/ld+json">'+json.dumps(item)+'</script>' for item in [data, fake_faq])
        rows = jsonld_rentals(text, "rentola", "https://rentola.fr")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].rooms)

    def test_pap_nearby_card_keeps_property_city_without_the_search_city(self):
        html = (FIXTURES / "pap.html").read_text().replace("Rouen (76000)", "Limay (78520) à 1km de Mantes-la-Jolie")
        rows = PAPScraper()._parse_cards(html)
        self.assertEqual(rows[0].location, "Limay (78520)")

    def test_photo_urls_with_cdn_commas_survive_both_storage_formats(self):
        urls = ["https://cdn.test/image/upload/c_fill,w_500,h_300/a.jpg", "https://cdn.test/b.jpg"]
        self.assertEqual(decode_images(','.join(urls)), urls)
        self.assertEqual(decode_images(encode_images(urls)), urls)

    def test_cloudflare_script_on_normal_page_is_not_a_block(self):
        self.assertFalse(is_probably_blocked('<title>Location Rouen</title><h1>21 appartements</h1><script src="/cloudflare/captcha.js"></script>'))
        self.assertTrue(is_probably_blocked('<title>Just a moment...</title><p>Enable JavaScript and cookies to continue</p>'))
        self.assertTrue(is_probably_blocked('<h1>Accès restreint</h1><p>Trafic inhabituel, veuillez résoudre le captcha.</p>'))

    def test_exhausted_paid_credits_trigger_backoff(self):
        with patch.object(firecrawl, "_RATE_LIMITED_UNTIL", 0):
            firecrawl._note_rate_limit("Error: Insufficient credits (402)")
            self.assertTrue(firecrawl.firecrawl_rate_limited())


class InventoryTests(unittest.TestCase):
    def setUp(self):
        engine = _make_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        patcher = patch.object(repo, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.item = ScrapedListing(source="pap", external_id="one", url="https://www.pap.fr/annonces/appartement-rouen-r123",
                                   title="Appartement 2 pièces 40 m²", price=800, surface_m2=40, rooms=2, location="Rouen (76000)")

    def test_invalid_rentals_are_rejected_without_fabricating_location(self):
        cases = [replace(self.item, title="Parking à louer"), replace(self.item, price=410000),
                 replace(self.item, url="https://www.pap.fr/vente/appartement/123"),
                 replace(self.item, title="En attente de photo"), replace(self.item, location=None),
                 replace(self.item, price=float("nan")), replace(self.item, price="800"),
                 replace(self.item, url="https://other.test/appartement")]
        for item in cases:
            with self.subTest(item=item):
                self.assertIsNone(normalize_listing(item, fallback_city="Rouen", fallback_postal="76000"))

    def test_similar_generic_ads_do_not_collapse_distinct_homes(self):
        other = replace(self.item, external_id="two", url=self.item.url+"4")
        self.assertNotEqual(quality_signature(self.item), quality_signature(other))

    def test_observed_listing_refresh_keeps_id_favorite_and_first_seen(self):
        first = _persist_listing_candidate(self.item).listing
        self.assertEqual(self.item.db_id, first.id)
        user = repo.get_or_create_user(42, "test")
        repo.add_favorite(user.id, first.id)
        updated = replace(self.item, price=750, images=["https://images.test/a.jpg"])
        second = _persist_listing_candidate(updated)
        self.assertFalse(second.is_new)
        self.assertEqual(second.listing.id, first.id)
        self.assertEqual(second.listing.first_seen_at, first.first_seen_at)
        self.assertEqual(second.listing.price, 750)
        self.assertEqual(decode_images(second.listing.images), updated.images)
        self.assertEqual(repo.get_user_favorite_listings(user.id)[0].id, first.id)

    def test_site_only_counts_recent_observations_and_keeps_history_separate(self):
        current = _persist_listing_candidate(self.item).listing
        for identifier, status, observed in [("old", "active", datetime.utcnow()-timedelta(days=31)), ("legacy", None, None), ("bad", "invalid", datetime.utcnow())]:
            repo.save_listing(source="pap", external_id=identifier, url=self.item.url+identifier, title=self.item.title,
                              price=800, surface_m2=40, location=self.item.location, inventory_status=status, last_seen_at=observed)
        snapshot = global_snapshot("test_bot")
        self.assertEqual(snapshot["stored_listings"], 4)
        self.assertEqual(snapshot["total_listings"], 1)
        self.assertEqual([x["id"] for x in snapshot["listings"]], [current.id])
        user = repo.get_or_create_user(42, "test")
        with repo.session_scope() as session:
            from sqlalchemy import select
            from database.models import Listing
            old = session.scalar(select(Listing).where(Listing.external_id == "old"))
        repo.add_favorite(user.id, old.id)
        self.assertEqual(account_snapshot(42)["favorites"][0]["inventory_status"], "stale")

    def test_empty_collection_does_not_claim_success(self):
        class Empty:
            source = "empty"
            def fetch_city(self, city, postal): return []
        result = asyncio.run(_collect_source_listings(Empty(), [("Rouen", "76000")]))
        self.assertEqual(result.status, "empty")
        self.assertIn("could not be confirmed", result.last_error)

    def test_blocked_collection_reports_failure_but_working_backup_succeeds(self):
        item = self.item
        class Blocked:
            source = "pap"
            def fetch_city(self, city, postal):
                note_problem("blocked_or_unavailable", "https://www.pap.fr/")
                return []
        class Recovered(Blocked):
            def fetch_city(self, city, postal):
                super().fetch_city(city, postal)
                return [item]
        self.assertEqual(asyncio.run(_collect_source_listings(Blocked(), [("Rouen", "76000")])).status, "failed")
        self.assertEqual(asyncio.run(_collect_source_listings(Recovered(), [("Rouen", "76000")])).status, "success")
