from __future__ import annotations

import asyncio
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

os.environ["BOT_TOKEN"] = "test-token"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ENABLE_GEOCODING"] = "false"
os.environ["AI_SCAM_ENABLED"] = "false"

from scraper.base import ScrapedListing
from database import repo
from database.session import init_db
from services.scheduler import _collect_source_listings, run_filter_backfill_once


class FakeScraper:
    source = "fake"

    def fetch_city(self, city, postal_code):
        if city == "bad":
            raise RuntimeError("boom")
        return [
            ScrapedListing(
                source=self.source,
                external_id=f"{city or 'none'}-{postal_code or 'none'}",
                url=f"https://example.test/{city or 'none'}",
                title=f"Appartement {city}",
                price=800,
                surface_m2=40,
                location=f"{city} ({postal_code})" if postal_code else city,
            )
        ]


class SchedulerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_collect_source_is_partial_when_one_location_fails(self):
        result = asyncio.run(
            _collect_source_listings(
                FakeScraper(),
                [("ok", "75001"), ("bad", "75002")],
            )
        )

        self.assertEqual(result.source, "fake")
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.error_count, 1)
        self.assertEqual(result.raw_count, 1)
        self.assertEqual(result.normalized_count, 1)
        self.assertEqual(result.unique_count, 1)
        self.assertIn("boom", result.last_error or "")

    def test_filter_backfill_sends_existing_source_listing(self):
        class FakeBot:
            def __init__(self):
                self.messages = []

            async def send_message(self, **kwargs):
                self.messages.append(kwargs)

        telegram_id = 910001
        repo.get_or_create_user(telegram_id, "premium_backfill")
        repo.set_user_premium(telegram_id, True)
        user = repo.get_user_by_telegram_id(telegram_id)
        flt = repo.create_or_update_filter(
            user_id=user.id,
            name="default",
            city="ok",
            postal_code="75001",
            price_max=900,
        )

        import services.scheduler as scheduler

        old_scrapers = scheduler.SCRAPERS
        scheduler.SCRAPERS = [FakeScraper()]
        try:
            bot = FakeBot()
            result = asyncio.run(run_filter_backfill_once(bot, user, flt, limit=1))
        finally:
            scheduler.SCRAPERS = old_scrapers

        self.assertEqual(result.sent_count, 1)
        self.assertEqual(result.pending_count, 0)
        self.assertEqual(len(bot.messages), 1)

    def test_slow_scraping_does_not_block_telegram_worker_pool(self):
        import services.scheduler as scheduler

        started = Event()
        release = Event()

        class SlowScraper:
            source = "slow"

            def fetch_city(self, city, postal_code):
                started.set()
                release.wait(timeout=3)
                return []

        async def scenario():
            loop = asyncio.get_running_loop()
            loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
            fetch = asyncio.create_task(scheduler._fetch_listings_non_blocking(SlowScraper(), None, None))
            try:
                async def wait_for_start():
                    while not started.is_set():
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(wait_for_start(), timeout=1)
                response = await asyncio.wait_for(asyncio.to_thread(lambda: "telegram-ready"), timeout=0.5)
                self.assertEqual(response, "telegram-ready")
            finally:
                release.set()
                await fetch

        asyncio.run(scenario())

    def test_timed_out_fetch_keeps_telegram_worker_pool_available(self):
        import services.scheduler as scheduler

        started = Event()
        release = Event()

        class SlowScraper:
            source = "slow"

            def fetch_city(self, city, postal_code):
                started.set()
                release.wait(timeout=3)
                return []

        async def scenario():
            loop = asyncio.get_running_loop()
            loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
            try:
                with patch.object(scheduler, "_source_timeout_seconds", return_value=0.1):
                    with self.assertRaises(asyncio.TimeoutError):
                        await scheduler._fetch_listings_non_blocking(SlowScraper(), None, None)
                self.assertTrue(started.is_set())
                response = await asyncio.wait_for(asyncio.to_thread(lambda: "telegram-ready"), timeout=0.5)
                self.assertEqual(response, "telegram-ready")
            finally:
                release.set()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
