from datetime import datetime,timedelta
import json
import unittest
from unittest.mock import patch
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from config import settings
from database import repo,models as m
from database.session import Base,_make_engine
from services import listing_moderation as moderation
from services.scheduler import _persist_listing_candidate
from scraper.base import ScrapedListing
import test_web_api as base

class ModerationRepositoryTests(unittest.TestCase):
    def setUp(self):
        engine=_make_engine('sqlite://');Base.metadata.create_all(engine);self.addCleanup(engine.dispose)
        patcher=patch.object(repo,'SessionLocal',sessionmaker(bind=engine,expire_on_commit=False));patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(settings,'admin_telegram_ids',{42});patcher.start();self.addCleanup(patcher.stop)
        self.admin=repo.get_or_create_user(42,'admin');self.member=repo.get_or_create_user(99,'member');self.other=repo.get_or_create_user(100,'other')
        self.item=self.save().listing
    def save(self,**extra):
        data=dict(source='pap',external_id='one',url='https://www.pap.fr/annonces/appartement-one',title='Appartement T2',price=800,surface_m2=40,rooms=2,location='Rouen 76000',images=json.dumps(['https://images.test/property.jpg']),canonical_signature='same-property',last_seen_at=datetime.utcnow(),inventory_status='active')
        data.update(extra);return repo.save_listing(**data)
    def test_reports_are_per_member_idempotent_and_do_not_hide_the_property(self):
        report=moderation.report_listing(self.member.id,self.item.id,'Le portail indique annonce supprimée.')
        self.assertTrue(report['created']);self.assertFalse(moderation.report_listing(self.member.id,self.item.id,'Another message')['created'])
        moderation.report_listing(self.other.id,self.item.id)
        self.assertTrue(repo.is_current_listing(repo.get_listing_by_id(self.item.id)))
        self.assertEqual(len(moderation.own_reports(self.member.id)),1)
        data=moderation.admin_reports(self.admin.id);self.assertEqual(data['pending_count'],2);self.assertEqual(len(data['items']),1)
        self.assertEqual(data['items'][0]['reports'][1]['note'],'Le portail indique annonce supprimée.')
        with self.assertRaises(PermissionError):moderation.admin_reports(self.member.id)
        with self.assertRaises(ValueError):moderation.report_listing(self.member.id,self.item.id,'x'*501)
    def test_members_cannot_remove_and_dismissal_preserves_inventory(self):
        moderation.report_listing(self.member.id,self.item.id)
        with self.assertRaises(PermissionError):moderation.moderate(self.member.id,self.item.id,'remove')
        moderation.moderate(self.admin.id,self.item.id,'dismiss')
        self.assertTrue(repo.is_current_listing(repo.get_listing_by_id(self.item.id)))
        self.assertEqual(moderation.own_reports(self.member.id)[0]['status'],'dismissed')
        self.assertEqual(moderation.admin_reports(self.admin.id)['pending_count'],0)
    def test_removal_hides_favorites_cancels_pending_and_keeps_auditable_reports(self):
        moderation.report_listing(self.member.id,self.item.id)
        repo.add_favorite(self.member.id,self.item.id)
        repo.add_pending_alert(self.member.id,self.item.id,datetime.utcnow())
        moderation.moderate(self.admin.id,self.item.id,'remove')
        listing=repo.get_listing_by_id(self.item.id)
        self.assertIsNotNone(listing.removed_at);self.assertEqual(listing.removed_by,self.admin.id)
        self.assertFalse(repo.is_current_listing(listing));self.assertEqual(repo.get_user_favorite_listings(self.member.id),[])
        self.assertFalse(repo.add_favorite(self.other.id,self.item.id));self.assertEqual(repo.fetch_due_pending_alerts(datetime.utcnow()),[])
        self.assertFalse(repo.add_pending_alert(self.other.id,self.item.id,datetime.utcnow()))
        self.assertEqual(moderation.own_reports(self.member.id)[0]['status'],'removed')
        self.assertTrue(moderation.admin_reports(self.admin.id)['items'][0]['listing']['removed'])
        with self.assertRaises(ValueError):moderation.report_listing(self.other.id,self.item.id)
    def test_removal_survives_recollection_and_old_cross_portal_signatures(self):
        moderation.moderate(self.admin.id,self.item.id,'remove')
        with repo.session_scope() as session:
            row=session.get(m.Listing,self.item.id);row.first_seen_at=datetime.utcnow()-timedelta(days=90);row.last_seen_at=row.first_seen_at
        repeated=self.save();self.assertEqual(repeated.listing.id,self.item.id);self.assertIsNotNone(repeated.listing.removed_at)
        cross=self.save(source='seloger',external_id='new-publication',url='https://www.seloger.com/annonces/one')
        self.assertEqual(cross.listing.id,self.item.id);self.assertIsNotNone(cross.listing.removed_at)
        result=_persist_listing_candidate(ScrapedListing(source='pap',external_id='one',url=self.item.url,title='Appartement T2',price=800,surface_m2=40,rooms=2,location='Rouen 76000'))
        self.assertIsNone(result.listing);self.assertEqual(result.duplicate_reason,'admin_removed')
    def test_alias_reports_and_removal_resolve_to_the_canonical_property(self):
        other=repo.save_listing(source='unit',external_id='alias',url='https://example.test/alias').listing
        with repo.session_scope() as session:session.get(m.Listing,other.id).duplicate_of_id=self.item.id
        result=moderation.report_listing(self.member.id,other.id);self.assertEqual(result['listing_id'],self.item.id)
        moderation.moderate(self.admin.id,other.id,'remove');self.assertIsNotNone(repo.get_listing_by_id(other.id).removed_at)

class ModerationApiTests(unittest.IsolatedAsyncioTestCase):
    post=base.SelfHostedWebTests.post
    login=base.SelfHostedWebTests.login
    async def asyncSetUp(self):await base.SelfHostedWebTests.asyncSetUp(self)
    async def moderate(self,operation='remove',confirmed=True,**extra):
        return await self.client.post('/api/admin/reports',json={'listing_id':self.item.id,'operation':operation,'confirmed':confirmed,**extra},headers={'Origin':self.origin})
    async def test_reporting_requires_login_valid_payload_and_origin(self):
        self.assertEqual((await self.post('report_listing',{'listing_id':self.item.id})).status,401)
        await self.login(99)
        self.assertEqual((await self.post('report_listing',{'listing_id':self.item.id},origin='https://wrong.test')).status,403)
        for value in [True,'1',0,-1]:self.assertEqual((await self.post('report_listing',{'listing_id':value})).status,400)
        self.assertEqual((await self.post('report_listing',{'listing_id':999999})).status,404)
        self.assertEqual((await self.post('report_listing',{'listing_id':self.item.id,'note':'x'*501})).status,400)
        self.assertEqual((await self.post('report_listing',{'listing_id':self.item.id,'note':'<script>text only</script>'})).status,201)
        self.assertEqual((await self.post('report_listing',{'listing_id':self.item.id})).status,200)
        account=(await (await self.client.get('/api/state')).json())['account'];self.assertEqual(account['listing_reports'],[{'listing_id':self.item.id,'status':'open'}])
    async def test_reports_are_admin_only_and_deletion_updates_cached_inventory(self):
        self.assertEqual((await self.client.get('/api/admin/reports')).status,401)
        await self.login(99)
        self.assertEqual((await self.client.get('/api/admin/reports')).status,403);self.assertEqual((await self.moderate()).status,403)
        await self.post('report_listing',{'listing_id':self.item.id,'note':'Annonce indisponible.'})
        await self.login()
        data=await (await self.client.get('/api/admin/reports')).json();self.assertEqual(data['pending_count'],1)
        self.assertEqual((await self.moderate(confirmed=False)).status,400)
        self.assertEqual((await self.moderate()).status,200)
        self.assertEqual((await self.client.get(f'/api/listings/{self.item.id}')).status,404)
        data=await (await self.client.get('/api/state')).json();self.assertNotIn(self.item.id,[row['id'] for row in data['snapshot']['listings']])
        self.assertEqual((await self.moderate()).status,200)
        data=await (await self.client.get('/api/admin/reports')).json();self.assertEqual(data['pending_count'],0);self.assertEqual(data['items'][0]['reports'][0]['status'],'removed')
    async def test_admin_can_remove_without_any_report_and_retain_existing_reports(self):
        await self.login()
        self.assertEqual((await self.moderate()).status,200)
        with repo.session_scope() as session:
            row=session.get(m.Listing,self.item.id);self.assertEqual(row.removed_by,self.admin.id)

    async def test_admin_can_dismiss_without_removing_and_reviewed_status_persists(self):
        await self.login(99)
        await self.post('report_listing',{'listing_id':self.item.id})
        await self.login()
        self.assertEqual((await self.moderate(operation='dismiss')).status,200)
        self.assertIsNone(repo.get_listing_by_id(self.item.id).removed_at)
        data=await (await self.client.get('/api/admin/reports')).json()
        self.assertEqual(data['items'][0]['reports'][0]['status'],'dismissed')
        self.assertIsNotNone(data['items'][0]['reports'][0]['reviewed_at'])
