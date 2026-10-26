import asyncio
from datetime import datetime,timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch
from sqlalchemy.orm import sessionmaker
from config import settings
from database import repo,models as m
from database.session import Base,_make_engine
from services import scheduler
from utils.fetch_diagnostics import capture_problems,note_event,safe_message
import test_web_api as base

class ListingReadTests(unittest.TestCase):
    def setUp(self):
        engine=_make_engine('sqlite://');Base.metadata.create_all(engine);self.addCleanup(engine.dispose)
        patcher=patch.object(repo,'SessionLocal',sessionmaker(bind=engine,expire_on_commit=False));patcher.start();self.addCleanup(patcher.stop)
        self.a=repo.get_or_create_user(9501);self.b=repo.get_or_create_user(9502)
        self.item=repo.save_listing(source='pap',external_id='read-one',url='https://example.test/one',title='Appartement',last_seen_at=datetime.utcnow(),inventory_status='active').listing

    def test_reads_are_idempotent_per_user_and_survive_inventory_refresh(self):
        moment=datetime.utcnow()-timedelta(days=2)
        with repo.session_scope() as session:session.get(m.Listing,self.item.id).first_seen_at=moment
        repo.mark_listing_read(self.a.id,self.item.id);repo.mark_listing_read(self.a.id,self.item.id)
        self.assertEqual(repo.read_listing_ids(self.a.id),[self.item.id]);self.assertEqual(repo.read_listing_ids(self.b.id),[])
        saved=repo.save_listing(source='pap',external_id='read-one',url='https://example.test/one',last_seen_at=datetime.utcnow(),inventory_status='active').listing
        self.assertEqual(saved.first_seen_at,moment);self.assertEqual(repo.read_listing_ids(self.a.id),[self.item.id])

    def test_read_alias_resolves_to_canonical_property(self):
        other=repo.save_listing(source='seloger',external_id='read-two',url='https://example.test/two').listing
        with repo.session_scope() as session:session.get(m.Listing,other.id).duplicate_of_id=self.item.id
        self.assertEqual(repo.mark_listing_read(self.a.id,other.id),self.item.id)
        self.assertEqual(repo.read_listing_ids(self.a.id),[self.item.id])
        with self.assertRaises(ValueError):repo.mark_listing_read(self.a.id,100000)

class ReadApiTests(unittest.IsolatedAsyncioTestCase):
    post=base.SelfHostedWebTests.post
    login=base.SelfHostedWebTests.login
    async def asyncSetUp(self):await base.SelfHostedWebTests.asyncSetUp(self)
    async def test_mark_read_requires_session_origin_and_valid_listing_then_invalidates_cache(self):
        self.assertEqual((await self.post('mark_read',{'listing_id':self.item.id})).status,401)
        await self.login(99)
        self.assertEqual((await self.post('mark_read',{'listing_id':self.item.id},origin='https://wrong.test')).status,403)
        self.assertEqual((await self.post('mark_read',{'listing_id':True})).status,400)
        self.assertEqual((await self.post('mark_read',{'listing_id':999999})).status,404)
        response=await self.post('mark_read',{'listing_id':self.item.id,'user_id':self.admin.id});self.assertEqual(response.status,200)
        account=(await (await self.client.get('/api/state')).json())['account']
        self.assertIn(self.item.id,account['read_listing_ids']);self.assertEqual(repo.read_listing_ids(self.admin.id),[])

class ScheduleAndDiagnosticTests(unittest.TestCase):
    def test_official_geography_is_direct_but_portals_keep_proxy_policy(self):
        from unittest.mock import Mock
        from utils import locations
        session=Mock();session.__enter__=Mock(return_value=session);session.__exit__=Mock(return_value=None)
        session.get.return_value=Mock(status_code=200,json=Mock(return_value=[{'nom':'Rouen','code':'76540'}]))
        with patch.object(locations.requests,'Session',return_value=session),patch.object(locations,'fetch_json_with_scrapling') as proxy:
            self.assertEqual(locations.public_json('https://geo.api.gouv.fr/communes',{'nom':'Rouen'})[0]['code'],'76540')
            self.assertFalse(session.trust_env);proxy.assert_not_called()

    def test_missing_geography_never_falls_back_to_national_portal_search(self):
        from scraper.paruvendu import ParuVenduScraper
        scraper=ParuVenduScraper()
        with patch.object(scraper,'_resolve_insee',return_value=None),patch('scraper.paruvendu.fetch_html') as fetch:
            self.assertEqual(list(scraper.fetch_city('Unknown','00000')),[])
            fetch.assert_not_called()

    def test_regular_cycle_uses_30_minute_start_to_start_interval_without_overlap(self):
        async def scenario():
            async def stop(delay):
                self.assertEqual(delay,1793)
                raise asyncio.CancelledError
            clock=SimpleNamespace(time=iter([10.0,17.0]).__next__)
            with patch.object(settings,'scheduler_interval_seconds',1800),patch.object(scheduler,'_run_scan_cycle',new=AsyncMock()) as scan,patch.object(scheduler.asyncio,'get_running_loop',return_value=clock),patch.object(scheduler.asyncio,'sleep',side_effect=stop):
                with self.assertRaises(asyncio.CancelledError):await scheduler.run_scheduler(None)
                scan.assert_awaited_once();self.assertFalse(scheduler.schedule_status()['running'])
        asyncio.run(scenario())

    def test_diagnostics_exclude_credentials_query_and_exception_locals(self):
        events=[]
        with capture_problems([],events):
            try:raise RuntimeError('Failed https://user:private-password@portal.test/search?api_key=private-key Cookie=private-cookie')
            except RuntimeError as error:note_event('transport_exception','https://u:p@portal.test/a?token=hidden',engine='chromium',exception=error)
        text=json.dumps(events)
        for secret in ['private-password','private-key','private-cookie','hidden','user:']:self.assertNotIn(secret,text)
        self.assertEqual(events[0]['url'],'https://portal.test/a');self.assertEqual(events[0]['exception_type'],'RuntimeError');self.assertIn('trace',events[0])

    def test_events_are_bounded_and_isolated_between_contexts(self):
        outer=[];inner=[]
        with capture_problems([],outer):
            note_event('first')
            with capture_problems([],inner):note_event('second')
            for i in range(100):note_event('outer')
        self.assertEqual(len(outer),80);self.assertEqual([event['code'] for event in inner],['second'])

class PersistentDiagnosticTests(unittest.TestCase):
    setUp=ListingReadTests.setUp
    def test_error_details_stay_in_history_after_a_successful_retry(self):
        events=[]
        with capture_problems([],events,source='pap'):
            note_event('http_blocked','https://portal.test/search?token=hidden',engine='scrapling_http',status=403)
        now=datetime.utcnow()
        failure=scheduler.SourceCollectResult(source='pap',started_at=now,finished_at=now,status='failed',error_count=1,last_error='HTTP 403',diagnostics=events)
        success=scheduler.SourceCollectResult(source='pap',started_at=now,finished_at=now,status='success',unique_count=2)
        with patch.object(scheduler,'_source_health',{}),patch.object(scheduler,'write_source_diagnostics') as write:
            scheduler._record_source_result('failed-cycle',failure,0)
            scheduler._record_source_result('fixed-cycle',success,0)
            write.assert_called_once()
        runs=repo.get_latest_source_runs(limit=2)
        self.assertEqual(runs[0].status,'success');self.assertEqual(runs[1].status,'failed')
        self.assertEqual(json.loads(runs[1].diagnostics_json)[0]['http_status'],403)
        self.assertNotIn('hidden',runs[1].diagnostics_json)
