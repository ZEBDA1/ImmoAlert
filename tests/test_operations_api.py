"""Admin boundaries, live diagnostics and fresh search results; entirely offline."""
import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from config import settings
from database import repo
from services import admin_monitor, provider_credentials as credentials, site_sync, web_api
from services.scheduler import SourceCollectResult
import test_web_api as base


class SourceHealthTests(unittest.TestCase):
    def classify(self, status="success", *, age=0, enabled=True, running=False, **values):
        now=datetime.utcnow()
        state={"last_status":status,"last_unique_count":10,"last_raw_count":10,**values}
        run=SimpleNamespace(finished_at=now-timedelta(seconds=age),started_at=now-timedelta(seconds=age+4),unique_count=10)
        return admin_monitor.source_health(enabled=enabled,state=state,latest=run,running=[{}] if running else [],now=now)["state"]

    def test_normal_requires_recent_valid_results(self):
        self.assertEqual(self.classify(),"healthy")
        self.assertEqual(self.classify(age=2101),"stale")
        self.assertEqual(self.classify(last_unique_count=0),"no_results")
        self.assertEqual(self.classify("partial"),"degraded")
        self.assertEqual(self.classify(last_dropped_count=8),"degraded")
        self.assertEqual(self.classify("never_run"),"unverified")

    def test_failure_cause_disabled_and_cooldown_have_explicit_states(self):
        self.assertEqual(self.classify("failed",last_error="quota 402"),"quota_exhausted")
        self.assertEqual(self.classify("failed",last_error="captcha 403"),"blocked")
        self.assertEqual(self.classify("failed",last_error="connection error"),"unavailable")
        self.assertEqual(self.classify("failed",disabled_until=datetime.utcnow()+timedelta(minutes=5)),"cooldown")
        self.assertEqual(self.classify(enabled=False,running=True),"disabled")
        self.assertEqual(self.classify(running=True),"collecting")


class OperationsApiTests(unittest.IsolatedAsyncioTestCase):
    post=base.SelfHostedWebTests.post
    login=base.SelfHostedWebTests.login

    async def asyncSetUp(self):
        await base.SelfHostedWebTests.asyncSetUp(self)
        for obj,name,value in ((credentials,"_MASTER_PATH",Path(self.directory.name)/"master.key"),
            (settings,"firecrawl_api_keys",()),(settings,"firecrawl_api_key",None),(settings,"flashproxy_api_key",None)):
            p=patch.object(obj,name,value);p.start();self.addCleanup(p.stop)
        self.addAsyncCleanup(self.finish_tasks)

    async def finish_tasks(self):
        tasks=list(site_sync._scan_tasks.values())
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)

    async def provider(self,**payload):
        return await self.client.post("/api/admin/providers",json={"provider":"firecrawl",**payload},headers={"Origin":self.origin})

    async def test_provider_endpoints_are_admin_only_and_origin_protected(self):
        self.assertEqual((await self.client.get("/api/admin/providers")).status,401)
        await self.login(99)
        self.assertEqual((await self.client.get("/api/admin/providers")).status,403)
        self.assertEqual((await self.provider(operation="save",api_key="fc-private-fixture")).status,403)
        self.assertEqual((await self.client.post("/api/admin/sources/pap/check",json={},headers={"Origin":self.origin})).status,403)
        await self.login()
        self.assertEqual((await self.client.post("/api/admin/providers",json={"provider":"firecrawl"},headers={"Origin":"https://evil.example"})).status,403)

    async def test_key_crud_never_exposes_secret_in_api_state_or_traffic(self):
        await self.login()
        secret="fc-private-fixture-DO-NOT-EXPOSE"
        response=await self.provider(operation="save",label="Collecte",api_key=secret)
        self.assertEqual(response.status,200,await response.text())
        key=(await response.json())["credential"]
        self.assertNotIn(secret,json.dumps(key))
        data=await (await self.client.get("/api/admin/providers")).json()
        self.assertEqual(data["providers"][0]["credentials"][0]["masked_key"],"•••• POSE")
        response=await self.provider(operation="save",key_id=key["id"],enabled=False)
        self.assertFalse((await response.json())["credential"]["enabled"])
        state=await (await self.client.get("/api/state")).json()
        monitor=await (await self.client.get("/api/admin/live")).json()
        self.assertNotIn(secret,json.dumps([state,monitor,data]))
        self.assertNotIn("encrypted_key",json.dumps([state,monitor,data]))
        self.assertEqual((await self.provider(operation="delete",key_id=key["id"])).status,200)
        self.assertEqual(credentials.runtime_keys("firecrawl"),())
        self.assertEqual((await self.provider(operation="toggle_provider",enabled=False)).status,200)
        state=await (await self.client.get("/api/state")).json()
        self.assertFalse(state["snapshot"]["firecrawl"]["enabled"])
        for payload in ({"enabled":"false"},{"api_key":"x\nsecret"},{"provider":"unknown"}):
            self.assertEqual((await self.provider(operation="save",**payload)).status,400)

    async def test_quota_refresh_is_non_billable_and_throttled(self):
        await self.login()
        key=credentials.save_credential("firecrawl",api_key="fc-fixture-credits",label="Crédits")
        with patch.object(credentials.requests,"get",return_value=Mock(status_code=200,json=Mock(return_value={"success":True,"data":{"remainingCredits":25,"planCredits":100}}))) as request:
            for _ in range(2):
                response=await self.provider(operation="refresh",key_id=key["id"])
                self.assertEqual(response.status,200,await response.text())
                self.assertEqual((await response.json())["credential"]["quota"]["remaining"],25)
            self.assertEqual(request.call_count,1)
            self.assertTrue(request.call_args.args[0].endswith("/credit-usage"))

    async def test_manual_diagnostic_runs_once_and_records_actual_source_result(self):
        await self.login()
        ready=asyncio.Event();release=asyncio.Event()
        async def collect(*args):
            ready.set();await release.wait()
            return SourceCollectResult(source="pap",listings=[],locations_count=1,status="empty",started_at=datetime.utcnow(),finished_at=datetime.utcnow())
        with patch("services.scheduler._collect_source_listings",side_effect=collect),patch("services.scheduler._record_source_result") as record:
            response=await self.client.post("/api/admin/sources/pap/check",json={},headers={"Origin":self.origin})
            self.assertEqual(response.status,202,await response.text());await ready.wait()
            self.assertEqual((await self.client.post("/api/admin/sources/pap/check",json={},headers={"Origin":self.origin})).status,409)
            data=await (await self.client.get("/api/admin/live")).json();self.assertIn("pap",data["diagnostics"])
            release.set();await asyncio.gather(*list(site_sync._scan_tasks.values()))
            record.assert_called_once();self.assertEqual(record.call_args.args[1].status,"empty")
            self.assertNotIn("pap",self.app[web_api.STATE].diagnostic_sources)
        repo.set_runtime_setting("source_enabled:pap","false")
        self.assertEqual((await self.client.post("/api/admin/sources/pap/check",json={},headers={"Origin":self.origin})).status,400)

    async def test_save_filter_returns_job_and_state_never_leaks_other_accounts_jobs(self):
        await self.login(99)
        response=await self.post("save_filter",{"name":"Mon logement","city":"Rouen","postal_code":"76000"})
        self.assertEqual(response.status,202)
        data=await response.json();self.assertEqual(data["search_id"],"test-search-job")
        self.search_start.assert_awaited_once()
        flt=repo.get_user_filters(self.member.id)[0]
        self.assertEqual(self.search_start.call_args.args[1:],(self.member.id,flt.id))
        repo.set_runtime_setting(f"search_jobs:{self.admin.id}",json.dumps([{"id":"admin-private","status":"succeeded"}]))
        state=await (await self.client.get("/api/state")).json()
        self.assertEqual(state["search_jobs"],[])

    async def test_finished_search_invalidates_inventory_cache_immediately(self):
        await self.login(99)
        # Warm caches, then change inventory inside their normal TTL.
        await self.client.get("/api/state")
        repo.save_listing(source="unit",external_id="fresh",url="https://example.test/fresh",title="Résultat neuf",price=750,surface_m2=35,location="Rouen 76000",rooms=2,last_seen_at=datetime.utcnow(),inventory_status="active")
        repo.set_runtime_setting(f"search_jobs:{self.member.id}",json.dumps([{"id":"job-fresh","status":"succeeded","finished_at":datetime.utcnow().isoformat()+"Z","listings_seen":1,"matches_count":1}]))
        state=await (await self.client.get("/api/state")).json()
        self.assertEqual(state["snapshot"]["total_listings"],2)
        self.assertEqual(state["search_jobs"][0]["id"],"job-fresh")
