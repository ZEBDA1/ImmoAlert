"""Offline checks of the documented FlashProxy customer API contract."""
import json
import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, Mock

from config import settings, env_secret_list
from utils import flashproxy as fp, firecrawl as fc, http
from utils.cache import TTLCache

PROXY = "http://proxy-user:p%40ss%3Aword@gateway.example:8080"


def response(data=None, status=200, headers=None):
    return Mock(status_code=status, headers=headers or {}, json=Mock(return_value={"success": True, "data": data}))


class FlashProxyTests(unittest.TestCase):
    def setUp(self):
        for name, value in {"flashproxy_enabled": True, "flashproxy_api_key": "test-flash-secret",
                            "flashproxy_plan_id": "plan-1", "flashproxy_country": "FR",
                            "flashproxy_sticky_minutes": 10}.items():
            p = patch.object(settings, name, value); p.start(); self.addCleanup(p.stop)
        self.client = fp.FlashProxyClient()

    def test_generation_contract_and_cached_session_under_concurrency(self):
        with patch.object(fp.requests, "request", return_value=response({"proxies": [PROXY]})) as request:
            with ThreadPoolExecutor(4) as executor:
                routes = list(executor.map(lambda _: self.client.route(), range(8)))
        request.assert_called_once()
        self.assertTrue(all(r.url == PROXY for r in routes))
        self.assertEqual(request.call_args.args, ("POST", "https://www.flashproxy.com/api/v1/plans/plan-1/proxies"))
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer test-flash-secret")
        self.assertEqual(request.call_args.kwargs["json"], {"count": 1, "country": "FR", "session": "sticky",
            "sticky_minutes": 10, "protocol": "http", "format": "url"})
        self.assertEqual(routes[0].browser, {"server": "http://gateway.example:8080", "username": "proxy-user", "password": "p@ss:word"})
        self.assertNotIn("proxy-user", repr(routes[0]))
        self.assertNotIn("test-flash-secret", json.dumps(self.client.status()))

    def test_unique_active_plan_can_be_selected_without_plan_id(self):
        with patch.object(settings, "flashproxy_plan_id", None), patch.object(fp.requests, "request", side_effect=[
            response({"plans": [{"plan_id": "actual-id"}], "total": 1}), response({"proxies": [PROXY]})]) as request:
            self.client.route()
        self.assertEqual(request.call_args_list[0].kwargs["params"], {"status": "active", "limit": 100})
        self.assertIn("/actual-id/proxies", request.call_args.args[1])

    def test_ambiguous_plan_never_generates_or_purchases(self):
        with patch.object(settings, "flashproxy_plan_id", None), patch.object(fp.requests, "request", return_value=response({
                "plans": [{"plan_id": "one"}, {"plan_id": "two"}], "total": 2})) as request:
            with self.assertRaisesRegex(fp.FlashProxyUnavailable, "FLASHPROXY_PLAN_ID"):
                self.client.route()
            request.assert_called_once()

    def test_missing_api_key_does_not_issue_a_request(self):
        with patch.object(settings, "flashproxy_api_key", None), patch.object(fp.requests, "request") as request:
            with self.assertRaises(fp.FlashProxyUnavailable):
                self.client.route()
            request.assert_not_called()

    def test_errors_are_cached_and_provider_secrets_never_logged(self):
        for status in (401, 403, 404, 422, 503):
            with self.subTest(status=status):
                client = fp.FlashProxyClient()
                with patch.object(fp.requests, "request", return_value=response({"secret": PROXY}, status)), self.assertLogs(fp.log, level="WARNING") as logs:
                    for _ in range(2):
                        with self.assertRaises(fp.FlashProxyUnavailable):
                            client.route()
                self.assertNotIn("proxy-user", str(logs.output))
                self.assertNotIn("test-flash-secret", str(logs.output))

    def test_429_respects_retry_after_without_new_api_call(self):
        with patch.object(fp.requests, "request", return_value=response({}, 429, {"Retry-After": "180"})) as request, \
             patch.object(fp.time, "monotonic", return_value=100):
            for _ in range(2):
                with self.assertRaises(fp.FlashProxyUnavailable):
                    self.client.route()
            self.assertGreaterEqual(self.client._retry_at, 280)
            request.assert_called_once()

    def test_malformed_proxy_is_rejected_without_credentials_in_error(self):
        for value in ("http://user:secret@bad:NaN", "socks5://user:secret@host:8", "http://host:8080", "bad", None):
            with self.subTest(value=value), patch.object(fp.requests, "request", return_value=response({"proxies": [value]})):
                client = fp.FlashProxyClient()
                with self.assertRaises(fp.FlashProxyUnavailable) as exc:
                    client.route()
                self.assertNotIn("secret", str(exc.exception))

    def test_enabling_flashproxy_invalidates_direct_html_cache(self):
        with patch.object(http, "scrapling_enabled", return_value=True), \
             patch.object(http, "fetch_with_scrapling", return_value="<html>Appartement</html>") as primary, \
             patch.object(http, "_HTML_CACHE", TTLCache(90)), patch.object(settings, "flashproxy_enabled", False):
            http.fetch_html("https://example.test/a")
            with patch.object(settings, "flashproxy_enabled", True):
                http.fetch_html("https://example.test/a")
            self.assertEqual(primary.call_count, 2)

    def test_proxy_failure_never_calls_scrapling_directly(self):
        from utils import scrapling
        with patch.object(scrapling, "scrapling_enabled", return_value=True), \
             patch.object(scrapling, "get_proxy_route", side_effect=fp.FlashProxyUnavailable("pending")), \
             patch("scrapling.fetchers.Fetcher.get") as fetch:
            self.assertIsNone(scrapling.fetch_with_scrapling("https://example.test"))
            fetch.assert_not_called()

    def test_expired_session_is_regenerated_and_changed_key_clears_cooldown(self):
        with patch.object(fp.requests, "request", return_value=response({"proxies": [PROXY]})) as request:
            self.client.route()
            self.client._expires = 0
            self.client.route()
            self.assertEqual(request.call_count, 2)
            self.client._retry_at = float("inf")
            with patch.object(settings, "flashproxy_api_key", "replacement-key"):
                self.client.route()
            self.assertEqual(request.call_count, 3)


class FirecrawlKeyPoolTests(unittest.TestCase):
    def setUp(self):
        for obj, name, value in [(settings, "firecrawl_api_keys", ("fc-key-a", "fc-key-b")),
                                 (settings, "firecrawl_api_key", "fc-key-a"),
                                 (settings, "firecrawl_enabled", True),
                                 (fc, "_KEY_CURSOR", 0), (fc, "_KEY_PAUSES", {}),
                                 (fc, "_RATE_LIMITED_UNTIL", 0),
                                 (fc, "firecrawl_cli_path", Mock(return_value="/usr/bin/firecrawl"))]:
            p = patch.object(obj, name, value); p.start(); self.addCleanup(p.stop)
        self.client = fc.FirecrawlClient()

    def result(self, data):
        return subprocess.CompletedProcess([], 0, stdout=json.dumps(data), stderr="")

    def test_402_pauses_only_affected_key_and_uses_next_key(self):
        with patch.object(fc.subprocess, "run", side_effect=[self.result({"success": False, "error": "402 Insufficient credits"}),
            self.result({"data": {"rawHtml": "<html>Appartement</html>"}})]) as run:
            self.assertEqual(self.client.fetch_html("https://example.test"), "<html>Appartement</html>")
        self.assertEqual([c.kwargs["env"]["FIRECRAWL_API_KEY"] for c in run.call_args_list], ["fc-key-a", "fc-key-b"])
        for call in run.call_args_list:
            self.assertNotIn("--api-key", call.args[0])
            self.assertNotIn("fc-key-a", str(call.args))
            self.assertNotIn("FIRECRAWL_API_KEYS", call.kwargs["env"])
        self.assertEqual(fc.key_pool_status()["keys_paused"], 1)
        self.assertTrue(fc.firecrawl_available())

    def test_401_tries_other_key_for_structured_extraction(self):
        with patch.object(fc.subprocess, "run", side_effect=[self.result({"success": False, "error": "401 Unauthorized"}),
                self.result({"data": {"json": {"listings": []}}})]) as run:
            self.assertEqual(self.client.scrape_json("https://example.test", schema={"type": "object"}), {"listings": []})
        self.assertEqual(run.call_count, 2)

    def test_retry_output_file_is_truncated_between_credentials(self):
        def cli(cmd, **kwargs):
            if kwargs["env"]["FIRECRAWL_API_KEY"] == "fc-key-a":
                with open(cmd[cmd.index("-o") + 1], "w") as stream:
                    stream.write('{"success":false,"error":"402 Insufficient credits"}')
                return self.result({})
            return self.result({"json": {"listings": [{"id": "second-key"}]}})
        with patch.object(fc.subprocess, "run", side_effect=cli):
            self.assertEqual(self.client.scrape_json("https://example.test", schema={"type": "object"}),
                             {"listings": [{"id": "second-key"}]})

    def test_429_pauses_all_keys_without_rotating(self):
        with patch.object(fc.subprocess, "run", return_value=self.result({"success": False, "error": "429 Too many requests"})) as run:
            self.assertIsNone(self.client.fetch_html("https://example.test"))
            self.assertEqual(self.client.search("query"), [])
            run.assert_called_once()
        self.assertTrue(fc.firecrawl_rate_limited())

    def test_all_exhausted_keys_short_circuit_future_calls(self):
        with patch.object(fc.subprocess, "run", return_value=self.result({"success": False, "error": "402 Insufficient credits"})) as run:
            self.assertIsNone(self.client.fetch_html("https://example.test"))
            self.assertIsNone(self.client.fetch_markdown("https://example.test/2"))
            self.assertEqual(run.call_count, 2)
        self.assertEqual(fc.key_pool_status()["keys_paused"], 2)
        self.assertFalse(fc.firecrawl_available())

    def test_healthy_credentials_rotate_without_duplicates(self):
        self.assertEqual(fc.configured_keys(), ("fc-key-a", "fc-key-b"))
        with patch.object(fc.subprocess, "run", return_value=self.result({"data": {"web": []}})) as run:
            self.client.search("first", use_cache=False)
            self.client.search("second", use_cache=False)
        self.assertEqual([c.kwargs["env"]["FIRECRAWL_API_KEY"] for c in run.call_args_list], ["fc-key-a", "fc-key-b"])

    def test_target_page_text_cannot_exhaust_the_pool(self):
        with patch.object(fc.subprocess, "run", return_value=self.result({"data": {"rawHtml": "<html>402 rate limit 429</html>"}})):
            self.assertIn("402", self.client.fetch_html("https://example.test"))
        self.assertEqual(fc.key_pool_status()["keys_paused"], 0)
        self.assertFalse(fc.firecrawl_rate_limited())

    def test_secrets_list_formats_and_invalid_errors(self):
        for value in ('["fc-a", "fc-b", "fc-a"]', "fc-a,fc-b", "fc-a\nfc-b"):
            with patch.dict("os.environ", {"FIRECRAWL_API_KEYS": value}):
                self.assertEqual(env_secret_list("FIRECRAWL_API_KEYS"), ("fc-a", "fc-b"))
        with patch.dict("os.environ", {"FIRECRAWL_API_KEYS": '["secret",123]'}):
            with self.assertRaises(RuntimeError) as exc:
                env_secret_list("FIRECRAWL_API_KEYS")
            self.assertNotIn("secret", str(exc.exception))
