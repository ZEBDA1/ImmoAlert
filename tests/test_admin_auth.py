from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

os.environ["BOT_TOKEN"]="test-token"
os.environ["DATABASE_URL"]="sqlite://"
os.environ["ENABLE_GEOCODING"]="false"

from sqlalchemy import select, text, inspect
from config import settings
from database import repo, models as m, web_repo, session as db_session
from services import web_auth, site_sync, admin_monitor
from services.web_api import COOKIE, STATE
import test_web_api as base


class AdminAccessTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=base.SelfHostedWebTests.asyncSetUp
    post=base.SelfHostedWebTests.post
    login=base.SelfHostedWebTests.login

    async def create(self,login="camille",**options):
        response=await self.client.post("/api/admin/accounts",json={"login":login,**options},headers={"Origin":self.origin})
        self.assertEqual(response.status,201,await response.text())
        return await response.json()

    async def password_login(self,credentials):
        return await self.client.post("/api/login",json=credentials,headers={"Origin":self.origin})

    async def update(self,identifier,operation,value=None):
        return await self.client.post(f"/api/admin/accounts/{identifier}",json={"operation":operation,"value":value},headers={"Origin":self.origin})

    async def test_only_admin_can_provision_and_no_public_registration(self):
        response=await self.client.post("/api/admin/accounts",json={"login":"any"},headers={"Origin":self.origin})
        self.assertEqual(response.status,401)
        await self.login(99)
        self.assertEqual((await self.client.get("/api/admin/live")).status,403)
        self.assertEqual((await self.client.post("/api/admin/accounts",json={"login":"any"},headers={"Origin":self.origin})).status,403)
        self.assertIn((await self.client.post("/api/register",json={},headers={"Origin":self.origin})).status,{404,405})

    async def test_password_account_without_telegram_keeps_filters_favorites_and_premium(self):
        await self.login()
        result=await self.create(premium=True)
        credentials=result["credentials"]
        self.assertGreaterEqual(len(credentials["password"]),20)
        self.assertIsNone(result["account"]["telegram_id"])
        self.assertEqual((await self.password_login(credentials)).status,200)
        state=await (await self.client.get("/api/state")).json()
        self.assertTrue(state["account"]["is_premium"])
        self.assertFalse(state["account"]["is_admin"])
        self.assertIsNone(state["account"]["telegram_id"])
        self.assertEqual((await self.post("save_filter",{"name":"Rive gauche","city":"Rouen","postal_code":"76000"})).status,202)
        self.assertEqual((await self.post("favorite",{"listing_id":self.item.id,"enabled":True})).status,202)
        state=await (await self.client.get("/api/state")).json()
        self.assertEqual(state["account"]["favorites"][0]["id"],self.item.id)
        self.assertEqual(state["account"]["filters"][0]["name"],"Rive gauche")
        self.assertEqual((await self.client.get("/api/admin/live")).status,403)
        self.assertEqual((await self.post("premium",{"target_user_id":state["account"]["user_id"],"enabled":True})).status,403)

    async def test_passwords_are_salted_and_never_retrieved_or_logged(self):
        await self.login()
        result=await self.create()
        password=result["credentials"]["password"]
        with repo.session_scope() as session:
            row=session.get(m.WebAccount,result["account"]["id"])
            self.assertNotIn(password,row.password_hash)
            self.assertTrue(web_auth.verify_password(password,row.password_hash))
            self.assertNotEqual(web_auth.hash_password(password),row.password_hash)
        monitor=await (await self.client.get("/api/admin/live")).json()
        self.assertNotIn(password,json.dumps(monitor))
        self.assertNotIn("password_hash",json.dumps(monitor))
        commands=await (await self.client.get("/api/state")).json()
        self.assertNotIn(password,json.dumps(commands))

    async def test_reset_revokes_sessions_and_replaces_password(self):
        await self.login();result=await self.create()
        await self.password_login(result["credentials"])
        token=self.client.session.cookie_jar.filter_cookies(self.client.make_url("/"))[COOKIE].value
        reset=web_auth.update_account(self.admin.id,result["account"]["id"],"reset_password")
        self.assertIsNone(web_repo.find_session(token))
        self.assertEqual((await self.password_login(result["credentials"])).status,401)
        self.assertEqual((await self.password_login(reset["credentials"])).status,200)

    async def test_disabling_account_blocks_password_and_old_session(self):
        await self.login();result=await self.create();await self.password_login(result["credentials"])
        token=self.client.session.cookie_jar.filter_cookies(self.client.make_url("/"))[COOKIE].value
        web_auth.update_account(self.admin.id,result["account"]["id"],"enabled",False)
        self.assertIsNone(web_repo.find_session(token))
        self.assertEqual((await self.password_login(result["credentials"])).status,401)
        self.assertEqual((await self.post("favorite",{"listing_id":self.item.id,"enabled":True})).status,401)

    async def test_admin_role_is_rechecked_without_snapshot_cache(self):
        await self.login();result=await self.create(admin=True);await self.password_login(result["credentials"])
        self.assertEqual((await self.client.get("/api/admin/live")).status,200)
        with repo.session_scope() as session:
            session.get(m.WebAccount,result["account"]["id"]).is_admin=False
        self.assertEqual((await self.client.get("/api/admin/live")).status,403)
        self.assertEqual((await self.post("scan")).status,403)

    async def test_duplicate_login_and_invalid_options_are_rejected(self):
        await self.login();await self.create()
        for payload in [{"login":"CAMILLE"},{"login":"<script>"},{"login":"another","premium":"true"}]:
            response=await self.client.post("/api/admin/accounts",json=payload,headers={"Origin":self.origin})
            self.assertEqual(response.status,400)

    async def test_uninvited_telegram_user_cannot_self_register_on_website(self):
        repo.get_or_create_user(765,"uninvited")
        data,_,_=await self.login(765)
        self.assertIsNone(data["account"])
        self.assertEqual(data["commands"][0]["status"],"failed")

    async def test_verified_telegram_link_merges_web_and_bot_profiles(self):
        await self.login();result=await self.create(premium=True);await self.password_login(result["credentials"])
        local=result["account"]["user_id"]
        repo.add_favorite(local,self.item.id)
        repo.mark_listing_read(local,self.item.id)
        repo.create_or_update_filter(local,city="Rouen",postal_code="76000")
        telegram=repo.get_or_create_user(876,"linked_user")
        repo.create_or_update_filter(telegram.id,city="Paris",postal_code="75001")
        state,_,_=await self.login(876)
        self.assertEqual(state["account"]["telegram_id"],876)
        self.assertEqual(state["account"]["user_id"],telegram.id)
        self.assertTrue(state["account"]["is_premium"])
        self.assertEqual(len(state["account"]["filters"]),2)
        self.assertEqual(state["account"]["favorites"][0]["id"],self.item.id)
        self.assertIn(self.item.id,state["account"]["read_listing_ids"])
        self.assertEqual((await self.password_login(result["credentials"])).status,200)

    async def test_link_to_another_provisioned_account_is_refused(self):
        await self.login();result=await self.create();await self.password_login(result["credentials"])
        state,_,_=await self.login(99)
        self.assertEqual(state["account"]["user_id"],result["account"]["user_id"])
        self.assertIsNone(state["account"]["telegram_id"])
        self.assertEqual(next(c for c in state["commands"] if c["kind"]=="link")["status"],"failed")

    async def test_live_requests_show_inflight_and_completed_without_payload(self):
        await self.login()
        gate=asyncio.Event()
        async def slow(identifier):
            await gate.wait();return self.item
        with patch("services.listing_details.get_listing_details",side_effect=slow):
            request=asyncio.create_task(self.client.get(f"/api/listings/{self.item.id}"))
            for _ in range(50):
                if any(item["path"].startswith("/api/listings/") for item in self.app[STATE].inflight.values()):break
                await asyncio.sleep(.01)
            data=await (await self.client.get("/api/admin/live")).json()
            self.assertTrue(any(r["path"].startswith("/api/listings/") and r["status"] is None for r in data["inflight"]))
            gate.set();self.assertEqual((await request).status,200)
        data=await (await self.client.get("/api/admin/live")).json()
        self.assertTrue(any(r["path"].startswith("/api/listings/") and r["status"]==200 for r in data["requests"]))
        self.assertNotIn("/api/admin/live",[r["path"] for r in data["requests"]])

    async def test_source_monitor_keeps_last_success_after_a_failed_run_and_restart(self):
        await self.login()
        moment=datetime.utcnow()
        repo.save_source_run(cycle_id="old-success",source="pap",status="success",started_at=moment-timedelta(hours=1),finished_at=moment-timedelta(hours=1),unique_count=3)
        repo.save_source_run(cycle_id="new-failure",source="pap",status="failed",started_at=moment,finished_at=moment,error_count=1,last_error="Portal refused request")
        with patch("services.scheduler._source_health",{}):
            data=await (await self.client.get("/api/admin/live")).json()
        source=next(s for s in data["sources"] if s["name"]=="pap")
        self.assertEqual(source["status"],"failed")
        self.assertIsNotNone(source["last_success_at"])
        self.assertIsNotNone(source["last_failure_at"])

    async def test_proxy_requires_valid_fresh_non_replayed_signature(self):
        secret="unit-test-proxy-secret-"+"a"*40
        public="https://site.example.test"
        def headers(path,body=b"",method="GET"):
            stamp=str(int(time.time()));nonce=secrets.token_hex(16);client="203.0.113.7"
            signed="\n".join([method,path,stamp,nonce,hashlib.sha256(body).hexdigest(),public,client])
            return {"Origin":public,"X-Immo-Public-Origin":public,"X-Immo-Timestamp":stamp,"X-Immo-Nonce":nonce,"X-Immo-Client-IP":client,"X-Immo-Signature":hmac.new(secret.encode(),signed.encode(),hashlib.sha256).hexdigest()}
        with patch.object(settings,"web_proxy_origin",public),patch.object(settings,"web_proxy_secret",secret):
            valid=headers("/api/state")
            self.assertEqual((await self.client.get("/api/state",headers=valid)).status,200)
            self.assertEqual((await self.client.get("/api/state",headers=valid)).status,403)
            forged=headers("/api/state");forged["X-Immo-Signature"]="0"*64
            self.assertEqual((await self.client.get("/api/state",headers=forged)).status,403)
            body=json.dumps({"kind":"link"}).encode()
            signed=headers("/api/action",body,"POST")
            response=await self.client.post("/api/action",data=body,headers={**signed,"Content-Type":"application/json"})
            self.assertEqual(response.status,202)
            self.assertIn("Secure",response.headers["Set-Cookie"])


class MigrationAndDeliveryTests(unittest.TestCase):
    def test_legacy_users_become_nullable_without_losing_relations_or_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=db_session._make_engine("sqlite:///"+str(Path(folder)/"legacy.db"))
            self.addCleanup(engine.dispose)
            with engine.begin() as conn:
                conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, telegram_id BIGINT NOT NULL, username VARCHAR(64), is_premium BOOLEAN NOT NULL, premium_until DATETIME, active BOOLEAN NOT NULL, created_at DATETIME NOT NULL)"))
                conn.execute(text("CREATE UNIQUE INDEX ix_users_telegram_id ON users (telegram_id)"))
                conn.execute(text("INSERT INTO users VALUES (41,123,'legacy',1,NULL,1,'2026-10-24 10:00:00')"))
            with patch.object(db_session,"engine",engine):
                db_session.init_db()
                with engine.begin() as conn:
                    conn.execute(text("INSERT INTO filters(user_id,name,active,created_at) VALUES (41,'default',1,'2026-10-24 10:00:00')"))
                    conn.execute(text("INSERT INTO users (id,telegram_id,username,is_premium,active,created_at) VALUES (42,NULL,'local',0,1,'2026-10-24 10:00:00')"))
                    self.assertEqual(conn.execute(text("SELECT user_id FROM filters")).scalar(),41)
                    self.assertEqual(conn.execute(text("SELECT telegram_id FROM users WHERE id=41")).scalar(),123)
                    self.assertFalse(conn.execute(text("PRAGMA foreign_key_check")).all())
                db_session.init_db()
                self.assertTrue(next(c for c in inspect(engine).get_columns("users") if c["name"]=="telegram_id")["nullable"])

    def test_monitor_redacts_credential_values(self):
        value=admin_monitor.safe_error("https://name:password@proxy.example api_key=very-secret token=another-secret")
        self.assertNotIn("very-secret",value)
        self.assertNotIn("another-secret",value)
        self.assertNotIn("name:password",value)
