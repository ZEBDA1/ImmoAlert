"""Regression tests for the Firecrawl free-tier backoff and degradation.

The CLI exits ``0`` and prints its quota errors on **stderr**, so a client that
only parses stdout silently retries the exhausted quota on every cycle. These
tests pin the detection logic and the graceful-degradation contract of the
Firecrawl-backed scrapers.
"""

from __future__ import annotations

import unittest
import os
import json
import subprocess
from unittest import mock

os.environ["BOT_TOKEN"] = "test-token"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["USE_SELENIUM_FALLBACK"] = "false"

from config import settings
from utils import firecrawl as fc
from scraper.leboncoin import LeboncoinScraper
from scraper.seloger import SeLogerScraper


CLI_RATE_LIMIT_STDERR = (
    "Error: You've hit Firecrawl's keyless free tier rate limit. "
    "To continue now, create a free API key at https://firecrawl.dev/k/xxx"
)


class RateLimitDetectionTests(unittest.TestCase):
    def setUp(self):
        # Every test starts with a clean slate: the flag is module-global.
        self._saved = fc._RATE_LIMITED_UNTIL
        fc._RATE_LIMITED_UNTIL = 0.0

    def tearDown(self):
        fc._RATE_LIMITED_UNTIL = self._saved

    def test_real_cli_wording_is_detected(self):
        fc._note_rate_limit(CLI_RATE_LIMIT_STDERR)
        self.assertTrue(fc.firecrawl_rate_limited())

    def test_429_and_quota_variants_are_detected(self):
        for message in ("HTTP 429", "Quota exceeded", "rate_limit", "Too Many Requests"):
            with self.subTest(message=message):
                fc._RATE_LIMITED_UNTIL = 0.0
                fc._note_rate_limit(message)
                self.assertTrue(fc.firecrawl_rate_limited())

    def test_unrelated_stderr_does_not_arm_the_backoff(self):
        fc._note_rate_limit("Warning: config file not found")
        self.assertFalse(fc.firecrawl_rate_limited())

    def test_empty_stderr_is_safe(self):
        fc._note_rate_limit("")
        fc._note_rate_limit(None)  # type: ignore[arg-type]
        self.assertFalse(fc.firecrawl_rate_limited())

    def test_search_arms_backoff_from_stderr(self):
        """``search`` used to ignore stderr entirely; it must now arm the flag."""
        completed = mock.Mock(stdout="", stderr=CLI_RATE_LIMIT_STDERR)

        with mock.patch.object(fc.subprocess, "run", return_value=completed), \
             mock.patch.object(fc, "firecrawl_cli_path", return_value="/usr/bin/firecrawl"):
            client = fc.FirecrawlClient(executable="/usr/bin/firecrawl")
            self.assertEqual(client.search("test query", use_cache=False), [])

        self.assertTrue(fc.firecrawl_rate_limited())

    def test_scrape_paths_detect_quota_even_with_zero_exit_code(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr=CLI_RATE_LIMIT_STDERR)
        with mock.patch.object(fc.subprocess, "run", return_value=completed) as run, \
             mock.patch.object(fc, "firecrawl_cli_path", return_value="/usr/bin/firecrawl"), \
             mock.patch.object(settings, "firecrawl_enabled", True):
            for method in ("fetch_markdown", "scrape_json"):
                with self.subTest(method=method):
                    fc._RATE_LIMITED_UNTIL = 0.0
                    client = fc.FirecrawlClient()
                    kwargs = {"schema": {"type": "object"}} if method == "scrape_json" else {}
                    self.assertIsNone(getattr(client, method)("https://example.test", **kwargs))
                    self.assertTrue(fc.firecrawl_rate_limited())
                    count = run.call_count
                    client.fetch_markdown("https://example.test/another")
                    self.assertEqual(run.call_count, count)

    def test_structured_scrape_reads_stdout_and_reuses_cached_result(self):
        data = {"listings": [{"title": "Appartement Paris", "price": 850}]}
        payload = {"success": True, "data": {"json": data, "metadata": {"statusCode": 200}}}
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")
        with mock.patch.object(fc.subprocess, "run", return_value=completed) as run, \
             mock.patch.object(fc, "firecrawl_cli_path", return_value="/usr/bin/firecrawl"), \
             mock.patch.object(settings, "firecrawl_enabled", True):
            client = fc.FirecrawlClient()
            for _ in range(2):
                self.assertEqual(client.scrape_json("https://example.test", schema={"type": "object"}), data)
            run.assert_called_once()

    def test_nested_http_error_never_becomes_a_listing(self):
        payload = {"data": {"json": {"listings": [{}]}, "metadata": {"statusCode": 429}}}
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")
        with mock.patch.object(fc.subprocess, "run", return_value=completed), \
             mock.patch.object(fc, "firecrawl_cli_path", return_value="/usr/bin/firecrawl"), \
             mock.patch.object(settings, "firecrawl_enabled", True):
            self.assertIsNone(fc.FirecrawlClient().scrape_json("https://example.test", schema={"type": "object"}))
        self.assertTrue(fc.firecrawl_rate_limited())

    def test_prompt_only_extraction_uses_json_schema_instead_of_query_format(self):
        def run_cli(command, **kwargs):
            self.assertNotIn("--query", command)
            schema_path = command[command.index("--schema-file") + 1]
            with open(schema_path, encoding="utf-8") as stream:
                schema = json.load(stream)
            self.assertEqual(schema["type"], "object")
            self.assertIn("Extract rental listings", schema["description"])
            payload = {"data": {"json": {"listings": []}}}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        with mock.patch.object(fc.subprocess, "run", side_effect=run_cli), \
             mock.patch.object(fc, "firecrawl_cli_path", return_value="/usr/bin/firecrawl"), \
             mock.patch.object(settings, "firecrawl_enabled", True):
            result = fc.FirecrawlClient().scrape_json(
                "https://example.test", prompt="Extract rental listings", use_cache=False,
            )
        self.assertEqual(result, {"listings": []})

    def test_schema_extraction_keeps_prompt_without_mutating_callers_schema(self):
        original = {"type": "object", "description": "Portal listings", "properties": {"listings": {"type": "array"}}}

        def run_cli(command, **kwargs):
            schema_path = command[command.index("--schema-file") + 1]
            with open(schema_path, encoding="utf-8") as stream:
                schema = json.load(stream)
            self.assertEqual(schema["description"], "Portal listings\nExtract rental listings")
            self.assertEqual(schema["properties"], original["properties"])
            payload = {"data": {"json": {"listings": []}}}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        with mock.patch.object(fc.subprocess, "run", side_effect=run_cli), \
             mock.patch.object(fc, "firecrawl_cli_path", return_value="/usr/bin/firecrawl"), \
             mock.patch.object(settings, "firecrawl_enabled", True):
            fc.FirecrawlClient().scrape_json(
                "https://example.test", schema=original, prompt="Extract rental listings", use_cache=False,
            )
        self.assertEqual(original["description"], "Portal listings")

    def test_status_reports_remaining_credits_and_max_concurrency(self):
        output = "\x1b[32m● Authenticated\x1b[0m\nConcurrency: 2/5 jobs\nCredits: 1,234 / 3,000 (41% left)"
        completed = subprocess.CompletedProcess([], 0, stdout=output, stderr="")
        with mock.patch.object(fc.subprocess, "run", return_value=completed), \
             mock.patch.object(fc, "firecrawl_cli_path", return_value="/usr/bin/firecrawl"):
            status = fc.firecrawl_status()
        self.assertTrue(status.authenticated)
        self.assertEqual(status.credits_remaining, 1234)
        self.assertEqual(status.concurrency, 5)

    def test_failed_status_does_not_claim_authenticated(self):
        completed = subprocess.CompletedProcess([], 1, stdout="", stderr="error")
        with mock.patch.object(fc.subprocess, "run", return_value=completed), \
             mock.patch.object(fc, "firecrawl_cli_path", return_value="/usr/bin/firecrawl"):
            self.assertFalse(fc.firecrawl_status().authenticated)

    def test_rate_limited_client_short_circuits(self):
        """Once throttled, no subprocess should be spawned at all."""
        fc._RATE_LIMITED_UNTIL = float("inf")
        with mock.patch.object(fc.subprocess, "run") as run:
            client = fc.FirecrawlClient(executable="/usr/bin/firecrawl")
            self.assertEqual(client.search("test", use_cache=False), [])
            self.assertIsNone(client.fetch_markdown("https://example.test", use_cache=False))
            self.assertIsNone(client.scrape_json("https://example.test", schema={"type": "object"}))
            run.assert_not_called()

    def test_firecrawl_available_is_false_while_throttled(self):
        fc._RATE_LIMITED_UNTIL = float("inf")
        self.assertFalse(fc.firecrawl_available())


class GracefulDegradationTests(unittest.TestCase):
    """Both scrapers must return ``[]`` instead of raising, whatever happens.

    Network access is stubbed out on purpose: these tests assert the *contract*
    (never raise, never return junk), so they must stay green offline and must
    not make real requests during the suite.
    """

    def setUp(self):
        self._saved_enabled = settings.firecrawl_enabled
        settings.firecrawl_enabled = False

    def tearDown(self):
        settings.firecrawl_enabled = self._saved_enabled
        fc._RATE_LIMITED_UNTIL = 0.0

    def _stub_network(self):
        """Make every outbound fetch fail, without touching the network.

        ``leboncoin`` binds ``fetch_html``/``http_client`` at import time, so it
        must be patched on that module. ``seloger`` imports ``fetch_html``
        lazily inside ``_legacy_html`` and uses ``requests`` directly in
        ``_api_with_cookie``, so those are patched at their definition sites.
        """
        patches = [
            mock.patch("scraper.leboncoin.fetch_html", return_value=None),
            mock.patch(
                "scraper.leboncoin.http_client.get", side_effect=RuntimeError("offline")
            ),
            mock.patch("utils.http.fetch_html", return_value=None),
            mock.patch("utils.http.fetch_json_with_playwright", return_value=None),
            mock.patch("requests.get", side_effect=RuntimeError("offline")),
            mock.patch("requests.post", side_effect=RuntimeError("offline")),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_leboncoin_returns_empty_without_raising(self):
        self._stub_network()
        self.assertEqual(list(LeboncoinScraper().fetch_city("Paris", "75011")), [])

    def test_seloger_returns_empty_without_raising(self):
        self._stub_network()
        self.assertEqual(list(SeLogerScraper().fetch_city("Paris", "75011")), [])

    def test_seloger_never_crashes_on_garbage_html(self):
        self._stub_network()
        with mock.patch("utils.http.fetch_html", return_value="<html><body>ban</body></html>"):
            self.assertEqual(list(SeLogerScraper().fetch_city("Paris", "75011")), [])

    def test_scrapers_declare_their_scheduler_timeouts(self):
        self.assertTrue(LeboncoinScraper().uses_browser)
        self.assertTrue(SeLogerScraper().uses_browser)
        self.assertGreater(LeboncoinScraper().timeout_seconds, 60)
        self.assertGreater(SeLogerScraper().timeout_seconds, 60)


if __name__ == "__main__":
    unittest.main()
