from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import AsyncMock, patch

os.environ["BOT_TOKEN"] = "test-token"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ENABLE_GEOCODING"] = "false"
os.environ["AI_SCAM_ENABLED"] = "false"

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from config import settings
from database import models as m, repo
from database.session import Base, _make_engine
from scraper.base import ScrapedListing
from services import scheduler, search_jobs, site_sync


def listing(city="Paris", index=0):
    return ScrapedListing(
        source="job_test", external_id=f"{city}-{index}", url=f"https://example.test/{city}/{index}",
        title=f"Appartement {city} {index}", price=800 + index * 101,
        surface_m2=40 + index * 9, rooms=2 + index,
        location=f"{city} {'75011' if city == 'Paris' else '69001'}",
    )


class ImmediateSearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = _make_engine(f"sqlite:///{Path(self.directory.name) / 'jobs.db'}")
        Base.metadata.create_all(self.engine)
        self.patches = [
            patch.object(repo, "SessionLocal", sessionmaker(bind=self.engine, expire_on_commit=False)),
            patch.object(scheduler, "_source_health", {}),
            patch.object(settings, "expand_location_variants", False),
            patch.object(settings, "include_generic_location_pass", False),
            patch.object(settings, "full_scan_mode", False),
            patch.object(settings, "source_quota_per_cycle", {}),
            patch.object(settings, "default_source_quota", 1),
        ]
        for active_patch in self.patches:
            active_patch.start()
        with repo.session_scope() as session:
            user = m.User(username="web_only", is_premium=True)
            session.add(user)
            session.flush()
            self.user_id = user.id
            other = m.User(username="other", is_premium=True)
            session.add(other)
            session.flush()
            self.other_id = other.id
        self.flt = repo.create_or_update_filter(self.user_id, city="Paris", postal_code="75011", price_max=1000)
        self.bot = AsyncMock()
        self.releases: list[Event] = []

    async def asyncTearDown(self):
        for release in self.releases:
            release.set()
        await search_jobs.shutdown_search_jobs()
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.engine.dispose()
        self.directory.cleanup()

    async def wait_for(self, predicate, timeout=3):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.01)

    async def finished(self, identifier, user_id=None):
        user_id = user_id or self.user_id

        def find():
            return next((row for row in search_jobs.user_jobs(user_id) if row["id"] == identifier), None)

        await self.wait_for(lambda: find() and find()["status"] not in {"queued", "running"})
        return find()

    def command(self, kind, payload=None, identifier="save-1"):
        return {"id": identifier, "user_id": "web-session", "_profile_id": self.user_id,
                "kind": kind, "payload": payload or {}, "created_at": int(time.time() * 1000)}

    def blocking_scraper(self):
        started, release = Event(), Event()
        self.releases.append(release)

        class BlockingScraper:
            source = "job_test"
            calls = 0

            def fetch_city(inner, city, postal_code):
                inner.calls += 1
                started.set()
                release.wait(timeout=3)
                return [listing(city)]

        return BlockingScraper(), started, release

    async def test_save_starts_immediately_and_populates_local_account_without_telegram(self):
        scraper, started, release = self.blocking_scraper()
        with patch.object(scheduler, "SCRAPERS", [scraper]):
            before = time.monotonic()
            result = await site_sync.process_commands(self.bot, [self.command("save_filter", {"city": "Paris", "postal_code": "75011", "price_max": 1000})], [])
            self.assertLess(time.monotonic() - before, 1)
            response = result[0]["result"]
            self.assertEqual(result[0]["status"], "succeeded")
            self.assertEqual(response["filter_id"], self.flt.id)
            self.assertTrue(response["search_id"])
            self.assertFalse(any(key.startswith("_") for key in response))
            await self.wait_for(started.is_set)
            running = search_jobs.user_jobs(self.user_id)[0]
            self.assertEqual(running["status"], "running")
            self.assertEqual(running["sources_total"], 1)
            release.set()
            job = await self.finished(response["search_id"])
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual((job["sources_done"], job["listings_seen"], job["matches_count"]), (1, 1, 1))
        account = site_sync.account_snapshot(user_id=self.user_id)
        self.assertIsNone(account["telegram_id"])
        self.assertEqual(len(account["matches"][str(self.flt.id)]), 1)
        self.bot.send_message.assert_not_awaited()

    async def test_entire_valid_inventory_is_saved_without_telegram_message_limit(self):
        class ManyScraper:
            source = "job_test"

            def fetch_city(self, city, postal_code):
                return [listing(city, index) for index in range(8)]

        with patch.object(scheduler, "SCRAPERS", [ManyScraper()]):
            started = await search_jobs.start_search(self.bot, self.user_id, self.flt.id)
            job = await self.finished(started["id"])
        with repo.session_scope() as session:
            count = session.scalar(select(func.count()).select_from(m.Listing))
            runs = list(session.scalars(select(m.SourceRun)))
        self.assertEqual(count, 8)
        self.assertEqual(job["listings_seen"], 8)
        self.assertEqual(job["matches_count"], 2)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].new_count, 8)

    async def test_disabled_sources_are_not_called_and_no_source_is_not_success(self):
        scraper, started, release = self.blocking_scraper()
        repo.set_runtime_setting("source_enabled:job_test", "false")
        with patch.object(scheduler, "SCRAPERS", [scraper]):
            job = await search_jobs.start_search(self.bot, self.user_id, self.flt.id)
            job = await self.finished(job["id"])
        self.assertFalse(started.is_set())
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["sources_total"], 0)
        self.assertIn("désactivées", job["error"])

    async def test_source_backoff_and_quota_are_honored(self):
        from datetime import datetime, timedelta
        scraper, started, release = self.blocking_scraper()
        scheduler._ensure_source_health(scraper.source)["disabled_until"] = datetime.utcnow() + timedelta(minutes=5)
        with patch.object(scheduler, "SCRAPERS", [scraper]):
            job = await search_jobs.start_search(self.bot, self.user_id, self.flt.id)
            self.assertEqual((await self.finished(job["id"]))["status"], "failed")
            scheduler._ensure_source_health(scraper.source)["disabled_until"] = None
            with patch.object(settings, "default_source_quota", 0):
                job = await search_jobs.start_search(self.bot, self.user_id, self.flt.id)
                self.assertEqual((await self.finished(job["id"]))["sources_total"], 0)
        self.assertFalse(started.is_set())

    async def test_persisted_source_backoff_survives_process_restart(self):
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        repo.save_source_run(cycle_id="before-restart", source="job_test", status="failed",
                             started_at=now, finished_at=now, error_count=1,
                             disabled_until=now + timedelta(minutes=5))
        scraper, started, release = self.blocking_scraper()
        self.assertEqual(scheduler._source_health, {})
        with patch.object(scheduler, "SCRAPERS", [scraper]):
            job = await search_jobs.start_search(self.bot, self.user_id, self.flt.id)
            self.assertEqual((await self.finished(job["id"]))["sources_total"], 0)
        self.assertFalse(started.is_set())

    async def test_partial_source_failure_preserves_results_and_reports_failure(self):
        class Working:
            source = "job_test"

            def fetch_city(self, city, postal_code):
                return [listing(city)]

        class Failing:
            source = "unavailable_test"

            def fetch_city(self, city, postal_code):
                raise RuntimeError("private-provider-error-do-not-expose")

        with patch.object(scheduler, "SCRAPERS", [Working(), Failing()]):
            job = await search_jobs.start_search(self.bot, self.user_id, self.flt.id)
            job = await self.finished(job["id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["stage"], "Collecte partielle")
        self.assertEqual((job["sources_done"], job["sources_total"], job["matches_count"]), (2, 2, 1))
        self.assertNotIn("private-provider-error", json.dumps(job))
        self.assertIn("unavailable_test", job["error"])

    async def test_empty_extraction_does_not_claim_a_successful_check(self):
        class Empty:
            source = "job_test"

            def fetch_city(self, city, postal_code):
                return []

        with patch.object(scheduler, "SCRAPERS", [Empty()]):
            job = await search_jobs.start_search(self.bot, self.user_id, self.flt.id)
            job = await self.finished(job["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("disponibilité non confirmée", job["error"])

    async def test_identical_pending_search_is_coalesced(self):
        scraper, started, release = self.blocking_scraper()
        with patch.object(scheduler, "SCRAPERS", [scraper]):
            first = await search_jobs.start_search(self.bot, self.user_id, self.flt.id)
            await self.wait_for(started.is_set)
            second = await search_jobs.start_search(self.bot, self.user_id, self.flt.id)
            self.assertEqual(first["id"], second["id"])
            release.set()
            await self.finished(first["id"])
        self.assertEqual(scraper.calls, 1)

    async def test_modified_search_cancels_old_work_and_ignores_late_http_result(self):
        started, release = Event(), Event()
        self.releases.append(release)

        class LocationScraper:
            source = "job_test"

            def fetch_city(self, city, postal_code):
                if city == "Paris":
                    started.set()
                    release.wait(timeout=3)
                return [listing(city)]

        with patch.object(scheduler, "SCRAPERS", [LocationScraper()]):
            old = await search_jobs.start_search(self.bot, self.user_id, self.flt.id)
            await self.wait_for(started.is_set)
            repo.create_or_update_filter(self.user_id, city="Lyon", postal_code="69001")
            new = await search_jobs.start_search(self.bot, self.user_id, self.flt.id)
            self.assertNotEqual(old["id"], new["id"])
            self.assertEqual((await self.finished(old["id"]))["status"], "canceled")
            self.assertEqual((await self.finished(new["id"]))["status"], "succeeded")
            release.set()
            await asyncio.sleep(0.05)
        with repo.session_scope() as session:
            rows = list(session.scalars(select(m.Listing)))
        self.assertEqual(len(rows), 1)
        self.assertIn("Lyon", rows[0].location)

    async def test_delete_cancels_queued_or_running_search(self):
        scraper, started, release = self.blocking_scraper()
        with patch.object(scheduler, "SCRAPERS", [scraper]):
            job = await search_jobs.start_search(self.bot, self.user_id, self.flt.id)
            await self.wait_for(started.is_set)
            response = await site_sync.process_commands(self.bot, [self.command("delete_filter")], [])
            self.assertEqual(response[0]["status"], "succeeded")
            self.assertEqual((await self.finished(job["id"]))["status"], "canceled")
            release.set()
        self.assertEqual(repo.get_user_filters(self.user_id), [])

    async def test_job_visibility_and_start_are_scoped_to_account(self):
        scraper, started, release = self.blocking_scraper()
        with patch.object(scheduler, "SCRAPERS", [scraper]):
            job = await search_jobs.start_search(self.bot, self.user_id, self.flt.id)
            self.assertEqual(search_jobs.user_jobs(self.other_id), [])
            with self.assertRaises(ValueError):
                await search_jobs.start_search(self.bot, self.other_id, self.flt.id)
            release.set()
            job = await self.finished(job["id"])
        self.assertNotIn("_signature", job)
        self.assertNotIn("user_id", job)

    async def test_restart_marks_persisted_pending_work_interrupted(self):
        row = {"id": "interrupted", "filter_id": self.flt.id, "filter_name": "default",
               "status": "running", "stage": "Consultation des sources"}
        repo.set_runtime_setting(f"search_jobs:{self.user_id}", json.dumps([row]))
        visible = search_jobs.user_jobs(self.user_id)[0]
        self.assertEqual(visible["status"], "failed")
        self.assertIn("redémarrage", visible["error"])
        self.assertIsNotNone(visible["finished_at"])

    async def test_same_account_jobs_run_sequentially(self):
        scraper, started, release = self.blocking_scraper()
        second_filter = repo.create_or_update_filter(self.user_id, name="second", city="Lyon", postal_code="69001")
        with patch.object(scheduler, "SCRAPERS", [scraper]):
            first = await search_jobs.start_search(self.bot, self.user_id, self.flt.id)
            await self.wait_for(started.is_set)
            second = await search_jobs.start_search(self.bot, self.user_id, second_filter.id)
            await asyncio.sleep(0.03)
            rows = {job["id"]: job for job in search_jobs.user_jobs(self.user_id)}
            self.assertEqual(rows[first["id"]]["status"], "running")
            self.assertEqual(rows[second["id"]]["status"], "queued")
            self.assertEqual(scraper.calls, 1)
            release.set()
            await self.finished(first["id"])
            await self.finished(second["id"])
        self.assertEqual(scraper.calls, 2)

    async def test_global_concurrency_is_bounded_across_accounts(self):
        third = repo.get_or_create_user(412500)
        filters = [self.flt,
                   repo.create_or_update_filter(self.other_id, city="Paris"),
                   repo.create_or_update_filter(third.id, city="Lyon")]
        release = asyncio.Event()
        concurrent = 0
        maximum = 0

        async def collect(*args, **kwargs):
            nonlocal concurrent, maximum
            concurrent += 1
            maximum = max(maximum, concurrent)
            try:
                await release.wait()
                return scheduler.FilterInventoryResult(sources_total=1, sources_done=1)
            finally:
                concurrent -= 1

        with patch.object(scheduler, "collect_filter_inventory_once", side_effect=collect):
            jobs = [await search_jobs.start_search(self.bot, flt.user_id, flt.id) for flt in filters]
            await self.wait_for(lambda: concurrent == 2)
            await asyncio.sleep(0.03)
            self.assertEqual(maximum, 2)
            self.assertEqual(search_jobs.user_jobs(third.id)[0]["status"], "queued")
            release.set()
            for flt, job in zip(filters, jobs):
                await self.finished(job["id"], flt.user_id)
        self.assertEqual(maximum, 2)

    async def test_source_paused_during_fetch_is_not_used(self):
        scraper, started, release = self.blocking_scraper()
        with patch.object(scheduler, "SCRAPERS", [scraper]):
            job = await search_jobs.start_search(self.bot, self.user_id, self.flt.id)
            await self.wait_for(started.is_set)
            repo.set_runtime_setting("source_enabled:job_test", "false")
            release.set()
            job = await self.finished(job["id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["sources_done"], 1)
        self.assertEqual(job["listings_seen"], 0)
        with repo.session_scope() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(m.Listing)), 0)

    async def test_shutdown_leaves_a_terminal_state_for_work_not_started(self):
        scraper, started, release = self.blocking_scraper()
        with patch.object(scheduler, "SCRAPERS", [scraper]):
            job = await search_jobs.start_search(self.bot, self.user_id, self.flt.id)
            await search_jobs.shutdown_search_jobs()
            job = await self.finished(job["id"])
        self.assertEqual(job["status"], "canceled")
        self.assertIsNotNone(job["finished_at"])

    async def test_disconnected_request_does_not_leave_an_ownerless_queued_job(self):
        entered, release = Event(), Event()
        self.releases.append(release)
        original_prepare = search_jobs._prepare

        def prepare(*args):
            entered.set()
            release.wait(timeout=3)
            return original_prepare(*args)

        class Working:
            source = "job_test"

            def fetch_city(self, city, postal_code):
                return [listing(city)]

        with patch.object(search_jobs, "_prepare", side_effect=prepare), patch.object(scheduler, "SCRAPERS", [Working()]):
            request = asyncio.create_task(search_jobs.start_search(self.bot, self.user_id, self.flt.id))
            await self.wait_for(entered.is_set)
            request.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await request
            job = search_jobs.user_jobs(self.user_id)[0]
            self.assertEqual((await self.finished(job["id"]))["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
