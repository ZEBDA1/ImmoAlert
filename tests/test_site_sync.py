from __future__ import annotations

import asyncio
import json
import os
import time
import unittest
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace
from datetime import datetime

os.environ["BOT_TOKEN"] = "test-token"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ENABLE_GEOCODING"] = "false"
os.environ["AI_SCAM_ENABLED"] = "false"

from sqlalchemy.orm import sessionmaker
from config import settings
from database import models as m, repo
from database.session import Base, _make_engine
from services import site_sync as site
from services.scheduler import _source_enabled
from bot import handlers


class SiteSyncTests(unittest.TestCase):
    def setUp(self):
        engine = _make_engine("sqlite://")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        patcher = patch.object(repo, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(settings, "admin_telegram_ids", {42})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.admin = repo.get_or_create_user(42, "alice")
        self.member = repo.get_or_create_user(99, "bob")
        self.linked = {"owner":42,"member":99}
        self.listing = repo.save_listing(source="unit",external_id="one",url="https://example.test/one",title="Appartement T2 Paris",price=800,surface_m2=40,location="Paris 75011",rooms=2,last_seen_at=datetime.utcnow(),inventory_status="active").listing

    def command(self, kind, payload=None, user_id="owner", command_id="command-1"):
        return {"id":command_id,"user_id":user_id,"kind":kind,"payload":payload or {},"created_at":int(time.time()*1000)}

    def test_admin_authorization_is_checked_again_on_the_bot(self):
        for kind in ["scrapling","source","scan","premium"]:
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError,"administrateur"):
                site.apply_command(self.command(kind,{"enabled":True},"member"),self.linked)

    def test_unlinked_identity_cannot_modify_a_telegram_account(self):
        with self.assertRaisesRegex(ValueError,"Connectez"):
            site.apply_command(self.command("alerts",{"enabled":False},"stranger"),self.linked)
        self.assertTrue(repo.get_user_by_telegram_id(42).active)

    def test_pairing_requires_explicit_bot_confirmation(self):
        command=self.command("link",{"token":"a"*32})
        self.assertIsNone(site.apply_command(command,{}))
        self.assertTrue(site.approve_pairing("a"*32,42))
        result=site.apply_command(command,{})
        self.assertEqual(result["telegram_id"],42)
        self.assertTrue(result["account"]["is_admin"])

    def test_invalid_and_expired_pairing_are_refused(self):
        self.assertFalse(site.approve_pairing("bad",42))
        command=self.command("link",{"token":"b"*32})
        command["created_at"]-=601000
        with self.assertRaisesRegex(ValueError,"expiré"):
            site.apply_command(command,{})

    def test_pairing_uses_the_person_clicking_not_the_bot_message_author(self):
        callback=SimpleNamespace(data="sitepair:"+"c"*32, from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(chat=SimpleNamespace(type="private"),from_user=SimpleNamespace(id=999),edit_text=AsyncMock()),answer=AsyncMock())
        asyncio.run(handlers.cb_site_pair(callback))
        approval=json.loads(repo.get_runtime_setting("site_pair:"+"c"*32))
        self.assertEqual(approval["telegram_id"],42)

    def test_pairing_in_a_group_is_refused(self):
        callback=SimpleNamespace(data="sitepair:"+"d"*32,from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(chat=SimpleNamespace(type="group")),answer=AsyncMock())
        asyncio.run(handlers.cb_site_pair(callback))
        self.assertIsNone(repo.get_runtime_setting("site_pair:"+"d"*32))

    def test_free_search_uses_existing_bot_primary_filter(self):
        command=self.command("save_filter",{"name":"new","city":"Paris","postal_code":"75011","price_max":900},"member")
        site.apply_command(command,self.linked)
        filters=repo.get_user_filters(self.member.id)
        self.assertEqual(len(filters),1)
        self.assertEqual(filters[0].name,"default")
        self.assertEqual(filters[0].price_max,900)

    def test_premium_can_save_named_searches(self):
        repo.set_user_premium(42,True)
        for name in ["Paris","Lyon"]:
            site.apply_command(self.command("save_filter",{"name":name,"city":name}),self.linked)
        self.assertEqual({f.name for f in repo.get_user_filters(self.admin.id)},{"Paris","Lyon"})

    def test_invalid_budget_and_location_are_rejected(self):
        for payload in [{"city":"Paris","price_min":1000,"price_max":500},{"city":"Paris","price_max":"NaN"},{"city":"Paris","postal_code":"abc"},{}]:
            with self.subTest(payload=payload),self.assertRaises(ValueError):
                site.apply_command(self.command("save_filter",payload),self.linked)

    def test_favorites_share_the_telegram_repository(self):
        payload={"listing_id":self.listing.id,"enabled":True}
        site.apply_command(self.command("favorite",payload),self.linked)
        self.assertTrue(repo.is_favorite(self.admin.id,self.listing.id))
        payload["enabled"]=False
        site.apply_command(self.command("favorite",payload),self.linked)
        self.assertFalse(repo.is_favorite(self.admin.id,self.listing.id))

    def test_deleting_search_does_not_affect_another_user(self):
        repo.create_or_update_filter(self.admin.id,city="Paris")
        repo.create_or_update_filter(self.member.id,city="Lyon")
        site.apply_command(self.command("delete_filter",{"name":"default"}),self.linked)
        self.assertEqual(repo.get_user_filters(self.admin.id),[])
        self.assertEqual(len(repo.get_user_filters(self.member.id)),1)

    def test_pausing_alerts_uses_the_same_user_state(self):
        site.apply_command(self.command("alerts",{"enabled":False}),self.linked)
        self.assertFalse(repo.get_user_by_telegram_id(42).active)
        self.assertTrue(repo.get_user_by_telegram_id(99).active)

    def test_source_switch_is_honored_by_the_scheduler(self):
        site.apply_command(self.command("source",{"source":"pap","enabled":False}),self.linked)
        self.assertFalse(_source_enabled("pap"))
        site.apply_command(self.command("source",{"source":"pap","enabled":True}),self.linked)
        self.assertTrue(_source_enabled("pap"))

    def test_unknown_source_and_non_boolean_values_are_rejected(self):
        with self.assertRaises(ValueError):
            site.apply_command(self.command("source",{"source":"unknown","enabled":True}),self.linked)
        with self.assertRaises(ValueError):
            site.apply_command(self.command("alerts",{"enabled":"false"}),self.linked)

    def test_admin_can_manage_existing_premium_accounts(self):
        site.apply_command(self.command("premium",{"telegram_id":99,"enabled":True}),self.linked)
        self.assertTrue(repo.get_user_by_telegram_id(99).is_premium)

    def test_snapshot_matches_use_existing_matching_engine(self):
        flt=repo.create_or_update_filter(self.admin.id,city="Paris",price_max=900)
        snapshot=site.account_snapshot(42)
        self.assertEqual(snapshot["matches"][str(flt.id)],[self.listing.id])
        self.assertEqual(snapshot["filters"][0]["price_max"],900)

    def test_acknowledged_commands_are_not_executed_twice(self):
        command=self.command("alerts",{"enabled":False})
        accounts=[{"user_id":"owner","telegram_id":42}]
        first=asyncio.run(site.process_commands(None,[command],accounts))
        site.apply_command(self.command("alerts",{"enabled":True}),self.linked)
        second=asyncio.run(site.process_commands(None,[command],accounts))
        self.assertEqual(first,second)
        self.assertTrue(repo.get_user_by_telegram_id(42).active)

    def test_failed_command_is_acknowledged_without_changing_state(self):
        command=self.command("scrapling",{"enabled":True},"member")
        result=asyncio.run(site.process_commands(None,[command],[{"user_id":"member","telegram_id":99}]))
        self.assertEqual(result[0]["status"],"failed")
        self.assertIn("administrateur",result[0]["result"]["message"])


if __name__=="__main__":
    unittest.main()
