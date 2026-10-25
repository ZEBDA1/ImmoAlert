from __future__ import annotations

import asyncio
import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["BOT_TOKEN"] = "test-token"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ENABLE_GEOCODING"] = "false"
os.environ["AI_SCAM_ENABLED"] = "false"

from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from bot import handlers
from database import models as m, repo
from database.session import Base, _make_engine
from scraper.base import ScrapedListing
from services import scheduler


class AlertDeliveryTests(unittest.TestCase):
    def setUp(self):
        engine = _make_engine("sqlite://")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        patcher = patch.object(repo, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = repo.get_or_create_user(123456, "alice")
        self.flt = repo.create_or_update_filter(self.user.id, city="Paris", price_max=1000)
        self.listing = ScrapedListing(
            source="unit", external_id="one", url="https://example.test/one",
            title="Appartement T2 Paris", price=850, surface_m2=40, location="Paris 75011",
        )
        self.stored = scheduler._persist_listing_candidate(self.listing).listing
        self.bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
        self.due = datetime.utcnow() - timedelta(seconds=1)

    def pending(self):
        with repo.session_scope() as session:
            return list(session.scalars(select(m.PendingAlert)))

    def queue(self, flt=None):
        flt = flt or self.flt
        return repo.add_pending_alert(
            self.user.id, self.stored.id, self.due, filter_id=flt.id, filter_name=flt.name,
        )

    def test_canceled_alert_can_be_requeued_without_duplicate_row(self):
        self.assertTrue(self.queue())
        pending_id = self.pending()[0].id
        repo.cancel_pending_alerts_for_user(self.user.id)
        self.assertTrue(self.queue())
        rows = self.pending()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, pending_id)
        self.assertEqual(rows[0].status, "pending")
        self.assertFalse(self.queue())

    def test_sent_alert_cannot_be_requeued(self):
        repo.mark_alert_sent(self.user.id, self.stored.id)
        self.assertFalse(self.queue())

    def test_filter_changes_preserve_other_filters_pending_alerts(self):
        other = repo.create_or_update_filter(self.user.id, name="autre", city="Paris")
        self.queue(other)
        repo.create_or_update_filter(self.user.id, name="default", city="Lyon")
        self.assertEqual(self.pending()[0].status, "pending")
        repo.delete_filter(self.user.id, "default")
        self.assertEqual(self.pending()[0].status, "pending")
        repo.delete_filter(self.user.id, "autre")
        self.assertEqual(self.pending()[0].status, "canceled")

    def test_transient_telegram_error_is_retried_and_sent_once(self):
        self.queue()
        self.bot.send_message.side_effect = OSError("temporary outage")
        asyncio.run(scheduler._dispatch_pending(self.bot))
        row = self.pending()[0]
        self.assertEqual(row.status, "pending")
        self.assertGreater(row.not_before, datetime.utcnow())
        self.assertFalse(repo.has_sent_alert(self.user.id, self.stored.id))
        self.bot.send_message.side_effect = None
        with repo.session_scope() as session:
            session.get(m.PendingAlert, row.id).not_before = self.due
        asyncio.run(scheduler._dispatch_pending(self.bot))
        asyncio.run(scheduler._dispatch_pending(self.bot))
        self.assertEqual(self.pending()[0].status, "sent")
        self.assertTrue(repo.has_sent_alert(self.user.id, self.stored.id))
        self.assertEqual(self.bot.send_message.await_count, 2)

    def test_blocked_bot_pauses_user_alerts(self):
        self.queue()
        self.bot.send_message.side_effect = TelegramForbiddenError(
            method=SendMessage(chat_id=self.user.telegram_id, text="test"),
            message="bot blocked by user",
        )
        asyncio.run(scheduler._dispatch_pending(self.bot))
        self.assertFalse(repo.get_user_by_id(self.user.id).active)
        self.assertEqual(self.pending()[0].status, "canceled")

    def test_existing_listing_is_delivered_to_unsent_user_only_once(self):
        repo.set_user_premium(self.user.telegram_id, True)
        self.assertFalse(asyncio.run(scheduler._process_new_listing(self.bot, self.listing)))
        self.assertFalse(asyncio.run(scheduler._process_new_listing(self.bot, self.listing)))
        self.bot.send_message.assert_awaited_once()
        self.assertTrue(repo.has_sent_alert(self.user.id, self.stored.id))

    def test_failed_premium_delivery_is_persisted_for_retry(self):
        repo.set_user_premium(self.user.telegram_id, True)
        self.bot.send_message.side_effect = OSError("temporary outage")
        asyncio.run(scheduler._process_new_listing(self.bot, self.listing))
        self.assertEqual(self.pending()[0].status, "pending")
        self.assertFalse(repo.has_sent_alert(self.user.id, self.stored.id))

    def test_edit_button_uses_human_account_and_keeps_alerts_active(self):
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=self.user.telegram_id, username="alice"),
            message=SimpleNamespace(
                from_user=SimpleNamespace(id=987654, username="the_bot"), answer=AsyncMock(),
            ),
            data="editflt:default", answer=AsyncMock(),
        )
        state = AsyncMock()
        asyncio.run(handlers.cb_edit_filter(callback, state))
        self.assertIsNone(repo.get_user_by_telegram_id(987654))
        self.assertTrue(repo.get_user_by_id(self.user.id).active)
        state.update_data.assert_awaited_once_with(filter_name="default")

    def test_start_clears_unfinished_filter_wizard(self):
        msg = SimpleNamespace(
            text="/start",
            from_user=SimpleNamespace(id=self.user.telegram_id, username="alice"), answer=AsyncMock(),
        )
        state = AsyncMock()
        with patch.object(handlers, "get_sources_health", return_value={}):
            asyncio.run(handlers.cmd_start(msg, state))
        state.clear.assert_awaited_once()
        msg.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
