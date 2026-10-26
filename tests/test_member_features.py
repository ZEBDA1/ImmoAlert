from __future__ import annotations

from datetime import datetime,timedelta
import os
import unittest
from unittest.mock import patch

os.environ['BOT_TOKEN']='test-token'
os.environ['DATABASE_URL']='sqlite://'
os.environ['ENABLE_GEOCODING']='false'

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from database import models as m,repo
from database.session import Base,_make_engine
from services import property_tracking as tracking,performance


class MemberFeatureTests(unittest.TestCase):
    def setUp(self):
        self.engine=_make_engine('sqlite://');Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        patcher=patch.object(repo,'SessionLocal',sessionmaker(bind=self.engine,expire_on_commit=False))
        patcher.start();self.addCleanup(patcher.stop)
        self.user=repo.get_or_create_user(42,'member')
        self.other=repo.get_or_create_user(99,'other')
        self.now=datetime.utcnow()

    def save(self,**values):
        defaults=dict(source='pap',external_id='one',url='https://www.pap.fr/annonces/one',title='Appartement',price=800,surface_m2=40,rooms=2,location='Rouen 76000',images='["https://cdn.test/common.jpg"]',normalized_postal_code='76000',last_seen_at=self.now,inventory_status='active')
        defaults.update(values)
        return repo.save_listing(**defaults).listing

    def test_prices_are_per_publication_and_identical_observations_are_not_repeated(self):
        first=self.save();self.save()
        second=self.save(source='seloger',external_id='two',url='https://www.seloger.com/two',price=790)
        self.assertEqual(first.id,second.id)
        self.save(price=750,last_seen_at=self.now+timedelta(minutes=1))
        records=tracking.price_history(first.id)['items']
        self.assertEqual(len(records),3)
        self.assertEqual((records[0]['source'],records[0]['price'],records[0]['previous_price']),('pap',750,800))
        self.assertIsNone(next(item for item in records if item['source']=='seloger')['previous_price'])

    def test_stale_observation_does_not_rewrite_price_or_history(self):
        first=self.save()
        self.save(price=900,last_seen_at=self.now-timedelta(hours=1))
        self.assertEqual(repo.get_listing_by_id(first.id).price,800)
        self.assertEqual(len(tracking.price_history(first.id)['items']),1)

    def test_price_history_is_paginated_without_losing_older_changes(self):
        row=self.save()
        for index in range(6):self.save(price=799-index,last_seen_at=self.now+timedelta(minutes=index+1))
        page=tracking.price_history(row.id,limit=3)
        older=tracking.price_history(row.id,limit=3,before=page['next_cursor'])
        last=tracking.price_history(row.id,limit=3,before=older['next_cursor'])
        self.assertEqual(len(page['items']+older['items']+last['items']),7)
        self.assertEqual(len({item['id'] for item in page['items']+older['items']+last['items']}),7)

    def test_notes_are_private_and_allow_paused_telegram_alerts(self):
        row=self.save();repo.add_favorite(self.user.id,row.id)
        with repo.session_scope() as session:session.get(m.User,self.user.id).active=False
        result=tracking.save_note(self.user.id,row.id,{'note':'Contacter lundi','stage':'contacted'})
        self.assertEqual(result['note'],'Contacter lundi')
        with self.assertRaises(PermissionError):tracking.note_for(self.other.id,row.id)
        repo.add_favorite(self.other.id,row.id)
        self.assertIsNone(tracking.note_for(self.other.id,row.id))

    def test_visit_and_reminder_need_aware_future_dates(self):
        row=self.save();repo.add_favorite(self.user.id,row.id)
        visit=self.now+timedelta(days=1);reminder=visit-timedelta(hours=1)
        payload={'stage':'visit','note':'Interphone B','visit_at':tracking.iso(visit),'reminder_at':tracking.iso(reminder)}
        result=tracking.save_note(self.user.id,row.id,payload,now=self.now)
        self.assertEqual(result['visit_at'],tracking.iso(visit))
        for update in [{'visit_at':visit.isoformat()},{'reminder_at':tracking.iso(visit+timedelta(hours=1))},{'stage':'bad'},{'note':'x'*4001}]:
            with self.subTest(update=update),self.assertRaises(ValueError):tracking.save_note(self.user.id,row.id,{**payload,**update},now=self.now)

    def test_unavailability_needs_spaced_confirmations_and_positive_recovers(self):
        row=self.save()
        with repo.session_scope() as session:publication=session.scalar(select(m.ListingPublication).where(m.ListingPublication.listing_id==row.id)).id
        tracking.record_availability(publication,'absent',now=self.now)
        self.assertEqual(tracking.publication_freshness(row.id)['status'],'unconfirmed')
        tracking.record_availability(publication,'absent',now=self.now+timedelta(minutes=1))
        self.assertEqual(tracking.publication_freshness(row.id)['status'],'unconfirmed')
        tracking.record_availability(publication,'check_failed',now=self.now+timedelta(hours=1))
        self.assertEqual(tracking.publication_freshness(row.id)['status'],'check_failed')
        tracking.record_availability(publication,'absent',now=self.now+timedelta(hours=7))
        self.assertEqual(tracking.publication_freshness(row.id)['status'],'unavailable')
        tracking.record_availability(publication,'available',now=self.now+timedelta(hours=8))
        self.assertEqual(tracking.publication_freshness(row.id)['status'],'available')

    def test_performance_summary_has_per_portal_median_p95_and_no_content(self):
        for value in range(1,21):performance.record('portal_collect',value,'pap')
        result=performance.summary()
        self.assertEqual(result['metrics'][0],{'name':'portal_collect','source':'pap','count':20,'median_ms':10.5,'p95_ms':19})
        with self.assertRaises(ValueError):performance.record('note','Private text')

    def test_targeted_reconciliation_preserves_tracking_and_leaves_other_cities_alone(self):
        first=self.save()
        second=self.save(source='seloger',external_id='two',images=None)
        self.assertNotEqual(first.id,second.id)
        paris_a=self.save(external_id='paris-a',location='Paris 75011',normalized_postal_code='75011')
        paris_b=self.save(source='seloger',external_id='paris-b',images=None,location='Paris 75011',normalized_postal_code='75011')
        for identifier in (first.id,second.id):repo.add_favorite(self.user.id,identifier)
        tracking.save_note(self.user.id,first.id,{'note':'Premier contact','stage':'to_contact'})
        tracking.save_note(self.user.id,second.id,{'note':'Téléphone à rappeler','stage':'contacted'})
        with repo.session_scope() as session:
            session.get(m.Listing,second.id).images=first.images
            session.get(m.Listing,paris_b.id).images=paris_a.images
        self.assertEqual(repo.reconcile_listing_duplicates(second.id),1)
        self.assertEqual(repo.get_listing_by_id(second.id).id,first.id)
        self.assertNotEqual(repo.get_listing_by_id(paris_a.id).id,repo.get_listing_by_id(paris_b.id).id)
        note=tracking.note_for(self.user.id,first.id)
        self.assertIn('Premier contact',note['note']);self.assertIn('Téléphone à rappeler',note['note'])
        self.assertEqual(note['stage'],'contacted')
        self.assertEqual(len(tracking.price_history(first.id)['items']),2)
        self.assertEqual(len(repo.get_user_favorite_listings(self.user.id)),1)

    def test_old_alias_chains_resolve_to_the_current_property(self):
        first=self.save();second=self.save(external_id='alias-two');third=self.save(external_id='alias-three')
        with repo.session_scope() as session:
            session.get(m.Listing,third.id).duplicate_of_id=second.id
            session.get(m.Listing,second.id).duplicate_of_id=first.id
        self.assertEqual(repo.get_listing_by_id(third.id).id,first.id)

    def test_note_revision_prevents_overwriting_another_device(self):
        row=self.save();repo.add_favorite(self.user.id,row.id)
        initial=tracking.save_note(self.user.id,row.id,{'note':'Initiale'})
        tracking.save_note(self.user.id,row.id,{'note':'Autre appareil','updated_at':initial['updated_at']})
        with self.assertRaises(tracking.TrackingConflict):tracking.save_note(self.user.id,row.id,{'note':'Brouillon ancien','updated_at':initial['updated_at']})
        self.assertEqual(tracking.note_for(self.user.id,row.id)['note'],'Autre appareil')

    def test_recording_performance_does_not_write_to_the_database_on_the_request_path(self):
        with patch.object(repo,'SessionLocal') as factory:
            performance.record('detail_visible',42)
            factory.assert_not_called()
