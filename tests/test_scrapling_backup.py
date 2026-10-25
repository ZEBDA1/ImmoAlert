from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ["BOT_TOKEN"] = "test-token"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ENABLE_GEOCODING"] = "false"
os.environ["AI_SCAM_ENABLED"] = "false"

import requests
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import Message
from sqlalchemy.orm import sessionmaker

from bot import handlers
from config import settings
from database import repo
from database.session import Base, _make_engine
from scraper.leboncoin import LeboncoinScraper
from scraper.seloger import SeLogerScraper
from services.scheduler import _source_timeout_seconds
from utils import http
from utils import scrapling as backup

HTML = "<html><body>Appartement à Paris, 42 m², 900 €</body></html>"


class ScraplingBackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_url = f"sqlite:///{self.directory.name}/test.db"
        self.engine = _make_engine(self.db_url)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.patch(repo, "SessionLocal", sessionmaker(bind=self.engine, expire_on_commit=False))
        self.patch(settings, "scrapling_enabled", False)
        self.patch(settings, "flashproxy_enabled", False)
        self.patch(http, "_HTML_CACHE", http.TTLCache(90))
        self.firecrawl = self.patch(__import__("utils.firecrawl", fromlist=["get_client"]), "get_client", Mock())
        self.firecrawl.return_value.fetch_html.return_value = None
        self.patch(__import__("utils.firecrawl", fromlist=["firecrawl_available"]), "firecrawl_available", lambda: True)
        self.patch(settings, "admin_telegram_ids", {42})
        self.patch(http, "_HTML_CACHE", http.TTLCache(90))
        self.playwright = self.patch(http, "get_with_playwright", Mock(return_value=None))
        self.patch(http, "_selenium_fallback_enabled", lambda: False)
        self.fetcher = Mock()
        self.fetcher.fetch.return_value = SimpleNamespace(status=200, html_content=HTML)
        patcher = patch.dict(sys.modules, {"scrapling.fetchers": SimpleNamespace(StealthyFetcher=self.fetcher)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.patch(backup, "_browser_path", lambda: "/usr/bin/chromium")

    def patch(self, obj, name, value):
        patcher = patch.object(obj, name, value)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def response(self, status=403, text="blocked"):
        response = requests.Response()
        response.status_code = status
        response._content = text.encode("utf-8")
        response.encoding = "utf-8"
        return response

    def request(self, response=None, error=None):
        self.patch(http.http_client, "get", Mock(return_value=response, side_effect=error))
        return http.fetch_html("https://example.test/search", referer="https://example.test/")

    def callback(self, user_id=42, action="on"):
        return SimpleNamespace(
            from_user=SimpleNamespace(id=user_id),
            data=f"admin:scrapling:{action}", message=None, answer=AsyncMock(),
        )

    def test_default_is_disabled(self):
        self.assertFalse(backup.scrapling_enabled())
        self.assertIsNone(self.request(self.response()))
        self.fetcher.fetch.assert_not_called()

    def test_environment_default_applies_until_admin_override(self):
        with patch.object(settings, "scrapling_enabled", True):
            self.assertTrue(backup.scrapling_enabled())
            backup.set_scrapling_enabled(False)
            self.assertFalse(backup.scrapling_enabled())

    def test_switch_persists_with_a_fresh_database_connection(self):
        backup.set_scrapling_enabled(True)
        restarted_engine = _make_engine(self.db_url)
        try:
            with patch.object(repo, "SessionLocal", sessionmaker(bind=restarted_engine)):
                self.assertTrue(backup.scrapling_enabled())
                backup.set_scrapling_enabled(False)
            self.assertFalse(backup.scrapling_enabled())
        finally:
            restarted_engine.dispose()

    def test_scrapling_runs_before_ordinary_http(self):
        backup.set_scrapling_enabled(True)
        self.assertEqual(self.request(self.response(200, HTML)), HTML)
        self.fetcher.fetch.assert_called_once()
        http.http_client.get.assert_not_called()
        self.firecrawl.assert_not_called()

    def test_blocked_http_uses_scrapling_before_legacy_browser(self):
        backup.set_scrapling_enabled(True)
        self.assertEqual(self.request(self.response()), HTML)
        self.playwright.assert_not_called()
        kwargs = self.fetcher.fetch.call_args.kwargs
        self.assertEqual(kwargs["timeout"], settings.scrapling_timeout_seconds * 1000)
        self.assertEqual(kwargs["extra_headers"]["Referer"], "https://example.test/")

    def test_http_200_challenge_uses_scrapling(self):
        backup.set_scrapling_enabled(True)
        self.assertEqual(self.request(self.response(200, "<html>Verify you are human</html>")), HTML)
        self.fetcher.fetch.assert_called_once()

    def test_connection_error_uses_scrapling(self):
        backup.set_scrapling_enabled(True)
        self.assertEqual(self.request(error=requests.ConnectionError("offline")), HTML)

    def test_missing_page_in_disabled_legacy_mode(self):
        backup.set_scrapling_enabled(False)
        self.assertIsNone(self.request(self.response(404)))
        self.fetcher.fetch.assert_not_called()
        self.playwright.assert_not_called()

    def test_failed_scrapling_calls_firecrawl_without_direct_bypass(self):
        backup.set_scrapling_enabled(True)
        self.fetcher.fetch.side_effect = TimeoutError("slow")
        self.firecrawl.return_value.fetch_html.return_value = HTML
        self.assertEqual(self.request(self.response()), HTML)
        self.firecrawl.return_value.fetch_html.assert_called_once()
        http.http_client.get.assert_not_called()
        self.playwright.assert_not_called()

    def test_bad_browser_responses_are_rejected(self):
        backup.set_scrapling_enabled(True)
        for status, html in [(403, HTML), (200, "captcha"), (200, "")]:
            with self.subTest(status=status, html=html):
                self.fetcher.fetch.return_value = SimpleNamespace(status=status, html_content=html)
                self.assertIsNone(backup.fetch_with_scrapling("https://example.test/"))

    def test_disabled_switch_bypasses_browser_and_its_cached_html(self):
        backup.set_scrapling_enabled(True)
        self.assertEqual(self.request(self.response()), HTML)
        backup.set_scrapling_enabled(False)
        self.fetcher.fetch.reset_mock()
        self.assertIsNone(self.request(self.response()))
        self.fetcher.fetch.assert_not_called()

    def test_missing_dependency_preserves_firecrawl_backup(self):
        backup.set_scrapling_enabled(True)
        with patch.dict(sys.modules, {"scrapling.fetchers": None}):
            self.assertFalse(backup.scrapling_status().ready)
            self.assertIsNone(self.request(self.response()))
        self.firecrawl.return_value.fetch_html.assert_called_once()
        self.playwright.assert_not_called()

    def test_missing_browser_reports_unavailable(self):
        with patch.object(backup, "_browser_path", return_value=None):
            self.assertFalse(backup.scrapling_status().ready)

    def test_admin_can_enable_and_disable_without_restart(self):
        for action, expected in [("on", True), ("off", False)]:
            callback = self.callback(action=action)
            asyncio.run(handlers.cb_admin_scrapling(callback))
            self.assertEqual(backup.scrapling_enabled(), expected)
            callback.answer.assert_awaited_once()

    def test_non_admin_cannot_change_setting_from_callback(self):
        callback = self.callback(user_id=99)
        asyncio.run(handlers.cb_admin_scrapling(callback))
        self.assertFalse(backup.scrapling_enabled())
        callback.answer.assert_awaited_once_with("⛔ Accès refusé.", show_alert=True)

    def test_unavailable_dependency_prevents_enabling_but_allows_disabling(self):
        with patch.object(backup, "_browser_path", return_value=None):
            asyncio.run(handlers.cb_admin_scrapling(self.callback()))
            self.assertFalse(backup.scrapling_enabled())
            backup.set_scrapling_enabled(True)
            asyncio.run(handlers.cb_admin_scrapling(self.callback(action="off")))
            self.assertFalse(backup.scrapling_enabled())

    def test_stale_enable_callback_is_idempotent(self):
        callback = self.callback()
        asyncio.run(handlers.cb_admin_scrapling(callback))
        asyncio.run(handlers.cb_admin_scrapling(callback))
        self.assertTrue(backup.scrapling_enabled())

    def test_unknown_callback_does_not_change_setting(self):
        callback = self.callback(action="invalid")
        asyncio.run(handlers.cb_admin_scrapling(callback))
        self.assertFalse(backup.scrapling_enabled())

    def test_callback_updates_the_existing_admin_panel(self):
        callback = self.callback()
        callback.message = Message(message_id=1, date=0, chat={"id": 42, "type": "private"})
        with patch.object(Message, "edit_text", new_callable=AsyncMock) as edit:
            asyncio.run(handlers.cb_admin_scrapling(callback))
        edit.assert_awaited_once()
        self.assertEqual(
            edit.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data,
            "admin:scrapling:off",
        )

    def test_identical_panel_from_stale_callback_does_not_raise(self):
        callback = self.callback()
        callback.message = Message(message_id=1, date=0, chat={"id": 42, "type": "private"})
        error = TelegramBadRequest(
            method=EditMessageText(chat_id=42, message_id=1, text="panel"),
            message="Bad Request: message is not modified",
        )
        with patch.object(Message, "edit_text", new_callable=AsyncMock, side_effect=error):
            asyncio.run(handlers.cb_admin_scrapling(callback))
        self.assertTrue(backup.scrapling_enabled())

    def test_admin_panel_reflects_persisted_switch(self):
        for enabled, target in [(False, "on"), (True, "off")]:
            backup.set_scrapling_enabled(enabled)
            text, keyboard = asyncio.run(handlers._admin_panel())
            self.assertIn("Moteur principal Scrapling", text)
            self.assertEqual(keyboard.inline_keyboard[0][0].callback_data, f"admin:scrapling:{target}")

    def test_scheduler_allows_time_for_enabled_backup(self):
        class Scraper:
            uses_browser = False
        backup.set_scrapling_enabled(True)
        self.assertGreater(_source_timeout_seconds(Scraper()), settings.scrapling_timeout_seconds)

    def test_firecrawl_runs_after_empty_primary(self):
        for scraper, loader in [(LeboncoinScraper(), "_legacy_fetch"), (SeLogerScraper(), "_legacy_html")]:
            rows = [SimpleNamespace(external_id=str(i)) for i in range(5)]
            order = []
            with patch.object(scraper, loader, side_effect=lambda *a: order.append("primary") or []), \
                 patch.object(scraper, "_fetch_with_firecrawl", side_effect=lambda *a: order.append("backup") or rows):
                self.assertEqual(list(scraper.fetch_city("Paris", "75011")), rows)
                self.assertEqual(order, ["primary", "backup"])

    def test_valid_primary_listing_avoids_firecrawl_extraction(self):
        from scraper.base import ScrapedListing
        for scraper, loader in [(LeboncoinScraper(), "_legacy_fetch"), (SeLogerScraper(), "_legacy_html")]:
            row = ScrapedListing(source=scraper.source, external_id="123", title="Appartement T2 à Rouen",
                url=f"https://www.{scraper.source}.{'fr' if scraper.source == 'leboncoin' else 'com'}/annonces/location/123.htm",
                price=700, surface_m2=40, location="Rouen 76000", rooms=2)
            with patch.object(scraper, loader, return_value=[row]), \
                 patch.object(scraper, "_fetch_with_firecrawl") as fallback:
                self.assertEqual(len(list(scraper.fetch_city("Rouen", "76000"))), 1)
                fallback.assert_not_called()

    def test_seloger_reuses_discovered_url_for_html_backup(self):
        scraper = SeLogerScraper()
        canonical = "https://www.seloger.com/recherche/location/appartement/ad75fr56"
        html = '<a href="/annonces/location/appartement/paris/123.htm">Appartement T2</a>900 €<ul>42 m²</ul>'
        with patch("scraper.seloger._PLACE_CACHE.get", return_value=canonical), \
             patch.object(http, "fetch_html", return_value=html) as fetch:
            rows = scraper._legacy_html("Paris", "75011")
        self.assertEqual(fetch.call_args.args[0], canonical)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].price, 900)


if __name__ == "__main__":
    unittest.main()
