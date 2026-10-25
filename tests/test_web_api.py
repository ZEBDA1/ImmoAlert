from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit, parse_qs

os.environ["BOT_TOKEN"] = "test-token"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ENABLE_GEOCODING"] = "false"

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from config import settings
from database import repo, models as m, web_repo
from database.session import Base, _make_engine
from services import site_sync
from services.web_api import create_app, STATE, COOKIE


class SelfHostedWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        engine = _make_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        patcher = patch.object(repo, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
        patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(settings, "admin_telegram_ids", {42})
        patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(site_sync, "_scan_tasks", {})
        patcher.start(); self.addCleanup(patcher.stop)
        self.admin = repo.get_or_create_user(42,"admin")
        self.member = repo.get_or_create_user(99,"member")
        from services import web_auth
        web_auth.create_account(self.admin.id,"member",target_user_id=self.member.id)
        self.item = repo.save_listing(source="unit",external_id="1",url="https://example.test/appartement",title="Appartement T2",price=800,surface_m2=40,location="Rouen 76000",rooms=2,last_seen_at=datetime.utcnow(),inventory_status="active").listing
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        Path(self.directory.name,"index.html").write_text("<!doctype html><title>IMMO//ALERT</title>")
        Path(self.directory.name,".env").write_text("secret")
        self.app = create_app(AsyncMock(),directory=Path(self.directory.name),origin="http://127.0.0.1",bot_username="immoalert_test_bot")
        self.client = TestClient(TestServer(self.app),cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server(); self.addAsyncCleanup(self.client.close)
        self.origin = str(self.client.make_url("/")).rstrip("/")
        self.app[STATE].origin = self.origin

    async def post(self,kind,payload=None,origin=None):
        return await self.client.post("/api/action",json={"kind":kind,"payload":payload or {}},headers={"Origin":origin or self.origin})

    async def login(self,identifier=42):
        response = await self.post("link")
        self.assertEqual(response.status,202)
        result = await response.json()
        pair = parse_qs(urlsplit(result["url"]).query)["start"][0][5:]
        old_cookie = self.client.session.cookie_jar.filter_cookies(self.client.make_url("/"))[COOKIE].value
        self.assertTrue(site_sync.approve_pairing(pair,identifier))
        response = await self.client.get("/api/state")
        data = await response.json()
        self.assertEqual(response.status,200)
        return data,old_cookie,pair

    async def test_anonymous_inventory_and_mutations_are_private(self):
        response = await self.client.get("/api/state")
        data = await response.json()
        self.assertIsNone(data["snapshot"])
        self.assertIsNone(data["account"])
        self.assertEqual((await self.post("source",{"source":"pap","enabled":False})).status,401)
        self.assertEqual((await self.client.get(f"/api/listings/{self.item.id}")).status,401)

    async def test_full_detail_is_authenticated_and_not_truncated(self):
        await self.login(99)
        with repo.session_scope() as session:
            row=session.get(m.Listing,self.item.id)
            row.description="Description complète. "*300
            row.images=json.dumps([f"https://cdn.test/{i}.jpg" for i in range(15)])
            row.details=json.dumps({"DPE":"C","Étage":"2"})
            row.latitude,row.longitude,row.geo_precision=49.44,1.1,"city"
        row=repo.get_listing_by_id(self.item.id)
        with patch("services.listing_details.get_listing_details",new=AsyncMock(return_value=row)):
            response=await self.client.get(f"/api/listings/{self.item.id}")
        data=await response.json()
        self.assertEqual(response.status,200)
        self.assertEqual(data["listing"]["description"],row.description)
        self.assertEqual(len(data["listing"]["images"]),15)
        self.assertEqual(data["listing"]["features"]["DPE"],"C")
        self.assertEqual(data["listing"]["geo_precision"],"city")
        self.assertEqual((await self.client.get("/api/listings/999999")).status,404)
        self.assertEqual((await self.client.get("/api/listings/nope")).status,404)

    async def test_login_needs_explicit_telegram_approval_and_rotates_cookie(self):
        response = await self.post("link")
        self.assertEqual(response.status,202)
        self.assertIsNone((await (await self.client.get("/api/state")).json())["account"])
        data,old_cookie,pair = await self.login()
        fresh_cookie = self.client.session.cookie_jar.filter_cookies(self.client.make_url("/"))[COOKIE].value
        self.assertNotEqual(old_cookie,fresh_cookie)
        self.assertTrue(data["account"]["is_admin"])
        self.assertEqual(data["snapshot"]["total_listings"],1)
        self.assertIsNone(repo.get_runtime_setting("site_pair:"+pair))
        self.assertIsNone(web_repo.find_session(old_cookie))
        with repo.session_scope() as session:
            keys = list(session.scalars(select(m.WebSession.id)))
        self.assertNotIn(fresh_cookie, keys)

    async def test_expired_pairing_cannot_log_in(self):
        response=await self.post("link"); data=await response.json()
        pair=parse_qs(urlsplit(data["url"]).query)["start"][0][5:]
        with repo.session_scope() as session:
            row=session.scalar(select(m.WebSession).where(m.WebSession.pair_token==pair));row.pair_expires_at=datetime.utcnow()-timedelta(seconds=1)
        site_sync.approve_pairing(pair,42)
        data=await (await self.client.get("/api/state")).json()
        self.assertIsNone(data["account"])
        self.assertEqual(data["commands"][0]["status"],"failed")

    async def test_member_cannot_read_member_list_or_change_admin_settings(self):
        data,_,_=await self.login(99)
        self.assertNotIn("users",data["snapshot"])
        for kind,payload in [("source",{"source":"pap","enabled":False}),("scrapling",{"enabled":True}),("scan",{}),("premium",{"telegram_id":42,"enabled":True})]:
            self.assertEqual((await self.post(kind,payload)).status,403)

    async def test_retried_login_gets_a_complete_new_approval_window(self):
        response=await self.post("link"); data=await response.json()
        original=parse_qs(urlsplit(data["url"]).query)["start"][0][5:]
        with repo.session_scope() as session:
            row=session.scalar(select(m.WebSession).where(m.WebSession.pair_token==original))
            row.expires_at=datetime.utcnow()+timedelta(minutes=1)
            row.pair_expires_at=datetime.utcnow()-timedelta(seconds=1)
        await self.client.get("/api/state")
        response=await self.post("link");data=await response.json()
        fresh=parse_qs(urlsplit(data["url"]).query)["start"][0][5:]
        self.assertNotEqual(original,fresh)
        with repo.session_scope() as session:
            row=session.scalar(select(m.WebSession).where(m.WebSession.pair_token==fresh))
            self.assertGreater(row.expires_at, row.pair_expires_at)
        self.assertTrue(site_sync.approve_pairing(fresh,42))
        data=await (await self.client.get("/api/state")).json()
        self.assertTrue(data["account"]["is_admin"])

    async def test_admin_source_switch_updates_same_bot_setting(self):
        await self.login()
        result=await self.post("source",{"source":"pap","enabled":False})
        self.assertEqual(result.status,202)
        self.assertEqual(repo.get_runtime_setting("source_enabled:pap"),"false")
        data=await (await self.client.get("/api/state")).json()
        command=next(c for c in data["commands"] if c["kind"]=="source")
        self.assertEqual(command["status"],"succeeded")
        self.assertFalse(next(s for s in data["snapshot"]["sources"] if s["name"]=="pap")["enabled"])

    async def test_filters_and_favorites_use_existing_database(self):
        await self.login(99)
        self.assertEqual((await self.post("save_filter",{"city":"Rouen","postal_code":"76000","price_max":900})).status,202)
        self.assertEqual(repo.get_user_filters(self.member.id)[0].price_max,900)
        self.assertEqual((await self.post("favorite",{"listing_id":self.item.id,"enabled":True})).status,202)
        data=await (await self.client.get("/api/state")).json()
        self.assertEqual(data["account"]["favorites"][0]["id"],self.item.id)
        self.assertEqual(data["account"]["matches"][str(data["account"]["filters"][0]["id"])],[self.item.id])

    async def test_invalid_admin_payload_fails_without_mutation(self):
        await self.login()
        result=await self.post("source",{"source":"pap","enabled":"false"})
        self.assertEqual(result.status,400)
        self.assertIsNone(repo.get_runtime_setting("source_enabled:pap"))
        data=await (await self.client.get("/api/state")).json()
        self.assertEqual(next(c for c in data["commands"] if c["kind"]=="source")["status"],"failed")

    async def test_csrf_logout_and_replayed_cookie(self):
        await self.login()
        self.assertEqual((await self.post("alerts",{"enabled":False},origin="https://evil.test")).status,403)
        self.assertTrue(repo.get_user_by_telegram_id(42).active)
        old=self.client.session.cookie_jar.filter_cookies(self.client.make_url("/"))[COOKIE].value
        self.assertEqual((await self.client.post("/api/logout",headers={"Origin":self.origin})).status,200)
        self.assertIsNone(web_repo.find_session(old))
        data=await (await self.client.get("/api/state")).json()
        self.assertIsNone(data["account"])

    async def test_static_server_health_and_hidden_files(self):
        response=await self.client.get("/")
        self.assertEqual(response.status,200)
        self.assertIn("IMMO//ALERT",await response.text())
        self.assertEqual(response.headers["X-Frame-Options"],"DENY")
        self.assertEqual((await self.client.get("/.env")).status,404)
        self.assertEqual((await self.client.get("/api/unknown")).status,404)
        self.assertEqual((await self.client.get("/api/health")).status,200)

    async def test_scan_runs_asynchronously_and_is_not_duplicated(self):
        await self.login()
        gate=asyncio.Event()
        async def scan(bot):
            await gate.wait();return {"pap":2}
        with patch("services.scheduler.run_full_scan_once",side_effect=scan) as run:
            response=await self.post("scan");self.assertEqual(response.status,202)
            self.assertEqual((await self.post("scan")).status,409)
            data=await (await self.client.get("/api/state")).json()
            self.assertEqual(next(c for c in data["commands"] if c["kind"]=="scan")["status"],"running")
            gate.set()
            await asyncio.gather(*site_sync._scan_tasks.values())
            data=await (await self.client.get("/api/state")).json()
            self.assertEqual(next(c for c in data["commands"] if c["kind"]=="scan")["status"],"succeeded")
            self.assertEqual(run.call_count,1)
