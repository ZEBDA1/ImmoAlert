from __future__ import annotations
import asyncio
from datetime import datetime,timedelta
import json
import unittest
import os
os.environ['BOT_TOKEN']='test-token'
os.environ['DATABASE_URL']='sqlite://'
os.environ['ENABLE_GEOCODING']='false'
from unittest.mock import AsyncMock,patch
from sqlalchemy import select
from database import models as m,repo
from services import alert_preferences as prefs,member_notifications as notices,favorite_freshness as freshness
from services.property_tracking import iso,save_note,publication_freshness
from test_member_features import MemberFeatureTests


class PreferenceTests(MemberFeatureTests):
    def test_quiet_hours_cross_midnight_and_end_at_the_configured_minute(self):
        data=prefs.save_preferences(self.user.id,{'quiet_start':'22:00','quiet_end':'07:00'})
        now=datetime(2026,10,26,22,30)
        self.assertTrue(prefs.is_quiet(data,now))
        self.assertEqual(prefs.next_allowed(data,now),datetime(2026,10,27,6))
        self.assertFalse(prefs.is_quiet(data,datetime(2026,10,27,6)))

    def test_quiet_end_handles_repeated_and_missing_dst_hours(self):
        data={**prefs.DEFAULTS,'quiet_start':'01:30','quiet_end':'03:30'}
        self.assertEqual(prefs.next_allowed(data,datetime(2026,3,29,0,45)),datetime(2026,3,29,1,30))
        data['quiet_end']='03:00'
        self.assertEqual(prefs.next_allowed(data,datetime(2026,10,25,0,45)),datetime(2026,10,25,2))

    def test_invalid_preferences_are_rejected_and_summary_is_once_per_local_day(self):
        for values in [{'timezone':'Missing/Place'},{'daily_summary':'yes'},{'quiet_start':'25:00','quiet_end':'08:00'},{'quiet_start':'22:00'},{'quiet_start':'08:00','quiet_end':'08:00'}]:
            with self.subTest(values=values),self.assertRaises(ValueError):prefs.save_preferences(self.user.id,values)
        prefs.save_preferences(self.user.id,{'daily_summary':True,'summary_time':'08:00'})
        data=prefs.get_preferences(self.user.id)
        self.assertFalse(prefs.summary_due(data,datetime(2026,10,26,6,59)))
        self.assertTrue(prefs.summary_due(data,datetime(2026,10,26,7)))
        data['last_summary_at']=datetime(2026,10,26,7)
        self.assertFalse(prefs.summary_due(data,datetime(2026,10,26,22)))
        self.assertTrue(prefs.summary_due(data,datetime(2026,10,27,7)))

    def test_price_drop_queue_is_deduplicated_and_respects_free_delay_and_opt_out(self):
        row=self.save();repo.add_favorite(self.user.id,row.id);repo.add_favorite(self.other.id,row.id)
        prefs.save_preferences(self.other.id,{'price_alerts':False})
        self.save(price=750,last_seen_at=self.now+timedelta(minutes=1))
        notices.queue_events(self.now);notices.queue_events(self.now)
        with repo.session_scope() as session:
            rows=list(session.scalars(select(m.MemberNotification)))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].user_id,self.user.id)
        from config import settings
        self.assertEqual(rows[0].not_before,self.now+timedelta(seconds=settings.premium_free_delay_seconds))
        self.assertEqual(json.loads(rows[0].payload)['previous_price'],800)

    def test_reminder_queue_and_notification_reads_are_private(self):
        row=self.save();repo.add_favorite(self.user.id,row.id)
        save_note(self.user.id,row.id,{'stage':'visit','visit_at':iso(self.now+timedelta(hours=2)),'reminder_at':iso(self.now+timedelta(hours=1))},now=self.now)
        notices.queue_events(self.now+timedelta(hours=1));notices.queue_events(self.now+timedelta(hours=1))
        inbox=notices.inbox(self.user.id)
        self.assertEqual(len(inbox['items']),1)
        self.assertEqual(inbox['items'][0]['kind'],'visit_reminder')
        with self.assertRaises(ValueError):notices.mark_read(self.other.id,inbox['items'][0]['id'])
        notices.mark_read(self.user.id,inbox['items'][0]['id'])
        self.assertEqual(notices.inbox(self.user.id)['unread'],0)

    def test_portal_errors_never_confirm_disappearance(self):
        row=self.save();repo.add_favorite(self.user.id,row.id)
        with repo.session_scope() as session:pub=session.scalar(select(m.ListingPublication).where(m.ListingPublication.listing_id==row.id))
        from utils.fetch_diagnostics import note_event
        def blocked(*args,**kwargs):note_event('http_blocked',pub.url,engine='scrapling_http',status=403);return None
        with patch.object(freshness,'fetch_html',side_effect=blocked):
            self.assertEqual(freshness.check_publication(pub,now=self.now+timedelta(hours=7)),'check_failed')
        self.assertEqual(publication_freshness(row.id)['status'],'check_failed')
        def missing(*args,**kwargs):note_event('http_error',pub.url,engine='scrapling_http',status=404);return None
        with patch.object(freshness,'fetch_html',side_effect=missing):
            freshness.check_publication(pub,now=self.now+timedelta(hours=8))
            self.assertEqual(publication_freshness(row.id)['status'],'unconfirmed')
            freshness.check_publication(pub,now=self.now+timedelta(hours=15))
        self.assertEqual(publication_freshness(row.id)['status'],'unavailable')

    def test_expired_page_can_retain_old_structured_data_without_becoming_available(self):
        row=self.save()
        with repo.session_scope() as session:pub=session.scalar(select(m.ListingPublication).where(m.ListingPublication.listing_id==row.id))
        data='<script type="application/ld+json">'+json.dumps({'@type':'RealEstateListing','description':'Ancienne description complète du logement. '*12,'image':['https://cdn.test/old.jpg']})+'</script>'
        with patch.object(freshness,'fetch_html',return_value="<h1>Cette annonce n’est plus disponible</h1>"+data):
            self.assertEqual(freshness.check_publication(pub,now=self.now),'absent')
        self.assertEqual(publication_freshness(row.id)['status'],'unconfirmed')
        with patch.object(freshness,'fetch_html',return_value="<h1>Appartement au calme</h1><p>Si cette annonce n’est plus disponible, contactez-nous.</p>"+data):
            self.assertEqual(freshness.check_publication(pub,now=self.now+timedelta(hours=1)),'available')
        self.assertEqual(publication_freshness(row.id)['status'],'available')

    def test_only_due_favorites_on_enabled_sources_are_checked(self):
        first=self.save();second=self.save(source='seloger',external_id='second',images=None)
        third=self.save(external_id='not-favorite')
        for identifier in (first.id,second.id):repo.add_favorite(self.user.id,identifier)
        with repo.session_scope() as session:
            for pub in session.scalars(select(m.ListingPublication)):pub.availability_checked_at=self.now-timedelta(hours=7)
        repo.set_runtime_setting('source_enabled:seloger','false')
        due=freshness.due_publications(self.now)
        self.assertEqual([pub.listing_id for pub in due],[first.id])


class NotificationDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        MemberFeatureTests.setUp(self)
        patcher=patch.object(notices,'_dispatch_lock',asyncio.Lock());patcher.start();self.addCleanup(patcher.stop)
        self.bot=AsyncMock()
        self.now=datetime(2026,10,26,9)
    save=MemberFeatureTests.save

    def price_drop(self):
        row=self.save();repo.add_favorite(self.user.id,row.id)
        with repo.session_scope() as session:session.get(m.User,self.user.id).is_premium=True
        self.save(price=750,last_seen_at=self.now+timedelta(minutes=1))
        return row

    async def test_price_drop_delivers_once_and_retries_after_transient_failure(self):
        self.price_drop()
        self.bot.send_message.side_effect=RuntimeError('Temporary error')
        await notices.dispatch_member_notifications(self.bot,now=self.now)
        with repo.session_scope() as session:
            item=session.scalar(select(m.MemberNotification));self.assertEqual(item.telegram_status,'pending')
        self.bot.send_message.side_effect=None
        await notices.dispatch_member_notifications(self.bot,now=self.now+timedelta(seconds=61))
        await notices.dispatch_member_notifications(self.bot,now=self.now+timedelta(seconds=62))
        self.assertEqual(self.bot.send_message.await_count,2)
        with repo.session_scope() as session:self.assertEqual(session.scalar(select(m.MemberNotification)).telegram_status,'sent')

    async def test_daily_summary_groups_new_listings_and_price_changes_once(self):
        row=self.price_drop()
        prefs.save_preferences(self.user.id,{'daily_summary':True,'summary_time':'08:00'})
        search=repo.create_or_update_filter(self.user.id,name='default',city='Rouen',price_max=1000)
        other=self.save(external_id='different',price=950,images=None,surface_m2=65)
        repo.add_pending_alert(self.user.id,other.id,self.now,filter_id=search.id)
        await notices.dispatch_member_notifications(self.bot,now=self.now)
        self.assertEqual(self.bot.send_message.await_count,1)
        self.assertIn('résumé immobilier',self.bot.send_message.await_args.args[1])
        self.assertIn('800 → 750',self.bot.send_message.await_args.args[1])
        self.assertTrue(repo.has_sent_alert(self.user.id,other.id))
        await notices.dispatch_member_notifications(self.bot,now=self.now+timedelta(minutes=1))
        self.assertEqual(self.bot.send_message.await_count,1)

    async def test_quiet_hours_defer_price_alerts_without_losing_them(self):
        self.price_drop()
        prefs.save_preferences(self.user.id,{'quiet_start':'09:00','quiet_end':'12:00'})
        await notices.dispatch_member_notifications(self.bot,now=self.now)
        self.bot.send_message.assert_not_awaited()
        await notices.dispatch_member_notifications(self.bot,now=datetime(2026,10,26,11))
        self.bot.send_message.assert_awaited_once()

    async def test_telegram_rate_limit_is_not_retried_before_the_server_delay(self):
        from aiogram.exceptions import TelegramRetryAfter
        from aiogram.methods import SendMessage
        self.price_drop()
        self.bot.send_message.side_effect=TelegramRetryAfter(method=SendMessage(chat_id=42,text='test'),message='Slow down',retry_after=120)
        await notices.dispatch_member_notifications(self.bot,now=self.now)
        self.bot.send_message.side_effect=None
        await notices.dispatch_member_notifications(self.bot,now=self.now+timedelta(seconds=60))
        self.assertEqual(self.bot.send_message.await_count,1)
        await notices.dispatch_member_notifications(self.bot,now=self.now+timedelta(seconds=121))
        self.assertEqual(self.bot.send_message.await_count,2)

    async def test_permanent_telegram_error_is_not_retried_forever(self):
        from aiogram.exceptions import TelegramBadRequest
        from aiogram.methods import SendMessage
        self.price_drop()
        self.bot.send_message.side_effect=TelegramBadRequest(method=SendMessage(chat_id=42,text='test'),message='Invalid request')
        await notices.dispatch_member_notifications(self.bot,now=self.now)
        await notices.dispatch_member_notifications(self.bot,now=self.now+timedelta(minutes=5))
        self.assertEqual(self.bot.send_message.await_count,1)
        with repo.session_scope() as session:self.assertEqual(session.scalar(select(m.MemberNotification)).telegram_status,'canceled')

    async def test_visit_reminder_sends_at_its_due_time_and_is_not_repeated(self):
        row=self.save();repo.add_favorite(self.user.id,row.id)
        save_note(self.user.id,row.id,{'stage':'visit','visit_at':iso(self.now+timedelta(hours=2)),'reminder_at':iso(self.now+timedelta(hours=1))},now=self.now)
        await notices.dispatch_member_notifications(self.bot,now=self.now+timedelta(minutes=59));self.bot.send_message.assert_not_awaited()
        await notices.dispatch_member_notifications(self.bot,now=self.now+timedelta(hours=1))
        await notices.dispatch_member_notifications(self.bot,now=self.now+timedelta(hours=1,minutes=1))
        self.bot.send_message.assert_awaited_once();self.assertIn('Rappel de visite',self.bot.send_message.await_args.args[1])
