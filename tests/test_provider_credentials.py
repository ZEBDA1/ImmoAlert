"""Offline tests of credential lifecycle, encryption and provider usage contracts."""
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import requests
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from config import settings
from database import models as m, repo
from database.session import Base, _make_engine
from services import provider_credentials as credentials
from utils import firecrawl, flashproxy


def response(data=None, *, status=200, success=True):
    return Mock(status_code=status, json=Mock(return_value={"success": success, "data": data,
        "error": "a provider might echo an API key here: fc-secret-never-expose"}))


class ProviderCredentialsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.master = Path(self.directory.name) / "provider-credentials.key"
        self.engine = _make_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        values = [(repo, "SessionLocal", self.factory), (credentials, "_MASTER_PATH", self.master),
            (settings, "firecrawl_api_keys", ()), (settings, "firecrawl_api_key", None),
            (settings, "flashproxy_api_key", None), (settings, "flashproxy_plan_id", "plan-test"), (settings,"flashproxy_plan_ids",()),
            (settings, "firecrawl_enabled", True), (settings, "flashproxy_enabled", True)]
        for obj, name, value in values:
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def add(self, provider="firecrawl", key="fc-fixture-primary", **kwargs):
        return credentials.save_credential(provider, api_key=key, label="Production", **kwargs)

    def test_environment_import_is_encrypted_deduplicated_and_durable_after_deletion(self):
        secret = "fc-fixture-environment"
        with patch.object(settings, "firecrawl_api_key", secret), patch.object(settings, "firecrawl_api_keys", (secret,)):
            self.assertEqual(credentials.runtime_keys("firecrawl"), (secret,))
            data = credentials.list_providers()
            keys = data["providers"][0]["credentials"]
            self.assertEqual(len(keys), 1)
            self.assertEqual(keys[0]["origin"], "environment")
            self.assertNotIn(secret, json.dumps(data))
            with self.factory() as session:
                row = session.get(m.ProviderCredential, keys[0]["id"])
                self.assertNotIn(secret, row.encrypted_key)
                self.assertEqual(session.execute(select(m.RuntimeSetting)).scalars().all(), [])
            self.assertEqual(stat.S_IMODE(self.master.stat().st_mode), 0o600)
            credentials.delete_credential("firecrawl", keys[0]["id"])
            self.assertEqual(credentials.runtime_keys("firecrawl"), ())
            self.assertEqual(credentials.list_providers()["providers"][0]["credentials"], [])
            self.assertTrue(credentials.managed_empty("firecrawl"))

    def test_runtime_replacement_disable_and_reenable_apply_without_restart(self):
        key = self.add()
        self.assertEqual(firecrawl.configured_keys(), ("fc-fixture-primary",))
        replacement = credentials.save_credential("firecrawl", key_id=key["id"], api_key="fc-fixture-replacement")
        self.assertNotEqual(key["fingerprint"], replacement["fingerprint"])
        self.assertEqual(firecrawl.configured_keys(), ("fc-fixture-replacement",))

        credentials.save_credential("firecrawl", key_id=key["id"], enabled=False)
        self.assertEqual(firecrawl.configured_keys(), ())
        with patch.object(firecrawl, "firecrawl_cli_path", return_value="/fixture/firecrawl"), patch.object(firecrawl.subprocess, "run") as run:
            self.assertFalse(firecrawl.firecrawl_available())
            self.assertIsNone(firecrawl.FirecrawlClient().fetch_html("https://fixture.example/results"))
            run.assert_not_called()
        credentials.save_credential("firecrawl", key_id=key["id"], enabled=True)
        self.assertEqual(firecrawl.configured_keys(), ("fc-fixture-replacement",))

    def test_opening_admin_preserves_existing_keyless_cli_until_pool_is_managed(self):
        credentials.list_providers()
        self.assertFalse(credentials.managed_empty("firecrawl"))
        self.assertEqual(firecrawl._key_candidates(), [None])
        key = self.add()
        credentials.delete_credential("firecrawl", key["id"])
        self.assertTrue(credentials.managed_empty("firecrawl"))
        self.assertEqual(firecrawl._key_candidates(), [])

    def test_foreign_provider_identifiers_and_ciphertext_swaps_are_rejected(self):
        first = self.add()
        second = self.add("flashproxy", "fp-fixture-provider")
        for operation in (lambda: credentials.delete_credential("flashproxy", first["id"]),
                          lambda: credentials.save_credential("flashproxy", key_id=first["id"], enabled=False),
                          lambda: credentials.refresh_quota("flashproxy", first["id"])):
            with self.assertRaisesRegex(ValueError, "introuvable"):
                operation()
        with self.factory.begin() as session:
            a, b = session.get(m.ProviderCredential, first["id"]), session.get(m.ProviderCredential, second["id"])
            a.encrypted_key = b.encrypted_key
        with self.assertRaisesRegex(RuntimeError, "chiffrée illisible"):
            credentials.runtime_keys("firecrawl")

    def test_lost_master_is_not_silently_replaced(self):
        first = self.add()
        self.master.unlink()
        with self.assertRaisesRegex(RuntimeError, "maîtresse"):
            credentials.runtime_keys("firecrawl")
        with self.assertRaisesRegex(RuntimeError, "maîtresse"):
            self.add(key="fc-another-fixture-key")
        self.assertFalse(self.master.exists())
        self.assertEqual(credentials.list_providers()["providers"][0]["credentials"][0]["id"], first["id"])

    def test_firecrawl_official_team_quota_and_unknown_fields(self):
        key = self.add()
        with patch.object(credentials.requests, "get", return_value=response({
                "remainingCredits": 20, "planCredits": 100, "billingPeriodEnd": "2026-11-01T00:00:00Z"})) as request:
            result = credentials.refresh_quota("firecrawl", key["id"])
        self.assertEqual(request.call_args.args, ("https://api.firecrawl.dev/v2/team/credit-usage",))
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer fc-fixture-primary")
        self.assertFalse(request.call_args.kwargs["allow_redirects"])
        self.assertEqual(request.call_args.kwargs["timeout"], (3, 7))
        self.assertEqual(result["quota"], {"remaining": 20, "used": 80, "total": 100, "unit": "credits", "reset_at": "2026-11-01T00:00:00Z"})
        self.assertIsNotNone(result["checked_at"])
        self.assertEqual(result["status"], "ok")
        with patch.object(credentials.requests, "get", return_value=response({"planCredits": 100})):
            unknown = credentials.refresh_quota("firecrawl", key["id"])
        self.assertIsNone(unknown["quota"]["remaining"])
        self.assertIsNone(unknown["quota"]["used"])
        with patch.object(credentials.requests, "get", return_value=response({"remainingCredits": 0, "planCredits": 100})):
            self.assertEqual(credentials.refresh_quota("firecrawl", key["id"])["status"], "exhausted")

    def test_provider_errors_discard_stale_quota_and_never_expose_provider_text(self):
        key = self.add()
        for code, expected in ((401, "invalid"), (403, "invalid"), (402, "exhausted"), (429, "rate_limited"), (503, "error"), (302, "error")):
            with self.subTest(code=code), patch.object(credentials.requests, "get", return_value=response(status=code)):
                result = credentials.refresh_quota("firecrawl", key["id"])
                self.assertEqual(result["status"], expected)
                self.assertIsNone(result["quota"]["remaining"])
                self.assertNotIn("fc-secret-never-expose", json.dumps(result))
        with patch.object(credentials.requests, "get", side_effect=requests.Timeout("fc-secret-never-expose")):
            self.assertEqual(credentials.refresh_quota("firecrawl", key["id"])["status"], "error")

    def test_flashproxy_decimal_gb_uncapped_usage_and_plan_selection(self):
        key = self.add("flashproxy", "fp-fixture-primary")
        plan = {"plan_id": "plan-test", "expires_at": "2026-11-01T00:00:00Z", "usage": {
            "bytes_used": 2_000_000_000, "max_bytes": 10_000_000_000, "remaining_bytes": 8_000_000_000}}
        with patch.object(credentials.requests, "get", return_value=response({"plans": [plan]})) as request:
            result = credentials.refresh_quota("flashproxy", key["id"])
        self.assertEqual(request.call_args.args, ("https://www.flashproxy.com/api/v1/usage",))
        self.assertEqual(result["quota"], {"remaining": 8, "used": 2, "total": 10, "unit": "GB", "reset_at": None})
        plan["usage"]["max_bytes"] = plan["usage"]["remaining_bytes"] = None
        with patch.object(credentials.requests, "get", return_value=response({"plans": [plan]})):
            result = credentials.refresh_quota("flashproxy", key["id"])
        self.assertIsNone(result["quota"]["remaining"])
        self.assertIn("sans plafond", result["detail"])
        with patch.object(settings, "flashproxy_plan_id", None), patch.object(credentials.requests, "get", return_value=response({"plans": [plan, plan]})):
            self.assertEqual(credentials.refresh_quota("flashproxy", key["id"])["status"], "error")

    def test_flashproxy_changed_key_invalidates_live_proxy_route(self):
        key = self.add("flashproxy", "fp-fixture-primary")
        client = flashproxy.FlashProxyClient()
        payload = response({"proxies": ["http://user:password@fixture.example:8080"]})
        with patch.object(flashproxy.requests, "request", return_value=payload) as request:
            client.route()
            first_identity = flashproxy.configuration_id()
            credentials.save_credential("flashproxy", key_id=key["id"], api_key="fp-fixture-replacement")
            client.route()
        self.assertNotEqual(first_identity, flashproxy.configuration_id())
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer fp-fixture-replacement")
        self.add("flashproxy", "fp-fixture-new-active")
        self.assertEqual(credentials.runtime_keys("flashproxy"), ("fp-fixture-new-active",))

    def test_provider_toggle_overrides_environment_immediately_and_persists(self):
        with patch.object(settings, "flashproxy_enabled", False):
            self.add("flashproxy", "fp-fixture-primary")
            self.assertFalse(credentials.provider_enabled("flashproxy"))
            result = credentials.set_provider_enabled("flashproxy", True)
            self.assertTrue(result["enabled"])
            self.assertTrue(credentials.provider_enabled("flashproxy"))
            with patch.object(flashproxy, "_CLIENT") as client:
                flashproxy.get_proxy_route()
                client.route.assert_called_once()
            credentials.set_provider_enabled("flashproxy", False)
            with patch.object(flashproxy, "_CLIENT") as client:
                flashproxy.get_proxy_route()
                client.route.assert_not_called()
        with self.factory() as session:
            self.assertFalse(session.get(m.ProviderConfiguration, "flashproxy").enabled)
        credentials.set_provider_enabled("firecrawl", False)
        self.assertFalse(firecrawl.firecrawl_available())

    def test_real_cli_wrapper_uses_admin_key_and_records_only_safe_telemetry(self):
        key = self.add()
        payload = subprocess.CompletedProcess([], 0, stdout='{"data":{"rawHtml":"<html>fixture</html>"}}', stderr="")
        with patch.object(firecrawl, "firecrawl_cli_path", return_value="/fixture/firecrawl"), \
             patch.object(firecrawl, "_KEY_PAUSES", {}), patch.object(firecrawl, "_RATE_LIMITED_UNTIL", 0), \
             patch.object(firecrawl.subprocess, "run", return_value=payload) as run:
            self.assertIn("fixture", firecrawl.FirecrawlClient().fetch_html("https://fixture.example", use_cache=False))
        self.assertEqual(run.call_args.kwargs["env"]["FIRECRAWL_API_KEY"], "fc-fixture-primary")
        data = credentials.list_providers()["providers"][0]["credentials"][0]
        self.assertEqual(data["id"], key["id"])
        self.assertIsNotNone(data["last_used_at"])
        self.assertIsNone(data["checked_at"])
        self.assertIsNone(data["quota"]["remaining"])

    def test_concurrent_replacement_cannot_receive_previous_key_quota(self):
        key = self.add()
        def replace_during_request(provider, api_key):
            credentials.save_credential(provider, key_id=key["id"], api_key="fc-fixture-replacement")
            return "ok", "obsolete", {"remaining": 999}
        with patch.object(credentials, "_fetch_quota", side_effect=replace_during_request):
            with self.assertRaisesRegex(ValueError, "changé"):
                credentials.refresh_quota("firecrawl", key["id"])
        data = credentials.list_providers()["providers"][0]["credentials"][0]
        self.assertIsNone(data["quota"]["remaining"])
        self.assertIsNone(data["checked_at"])

    def test_validation_never_echoes_invalid_keys(self):
        for value in ("short", "fc-a key with spaces", "fc-fixture\nsecret", 123):
            with self.subTest(value=value), self.assertRaises(ValueError) as error:
                self.add(key=value)
            self.assertNotIn(str(value), str(error.exception))
        self.add()
        with self.assertRaisesRegex(ValueError, "déjà"):
            self.add()


if __name__ == "__main__":
    unittest.main()
