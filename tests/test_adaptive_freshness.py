from __future__ import annotations
from datetime import timedelta
import unittest
from unittest.mock import patch
import test_member_features as base
from database import models as m,repo
from services import favorite_freshness as freshness
from config import settings

class AdaptiveFreshnessTests(unittest.TestCase):
    setUp=base.MemberFeatureTests.setUp

    def populate(self,count,sources=('pap','seloger','leboncoin','paruvendu')):
        with repo.session_scope() as session:
            for index in range(count):
                row=m.Listing(source=sources[index%len(sources)],external_id=str(index),url='https://www.pap.fr/'+str(index),title='Fictif',price=800,location='Rouen',first_seen_at=self.now,inventory_status='active')
                session.add(row);session.flush()
                session.add(m.Favorite(user_id=self.user.id,listing_id=row.id))
                session.add(m.ListingPublication(listing_id=row.id,source=row.source,external_id=row.external_id,url=row.url))

    def test_batch_adapts_to_volume_and_limits_each_portal(self):
        self.populate(900)
        with patch.object(settings,'favorite_freshness_target_hours',6),patch.object(settings,'favorite_freshness_max_batch',12):
            plan=freshness.freshness_plan(self.now);rows=freshness.due_publications(self.now)
        self.assertEqual(plan['eligible_publications'],900);self.assertEqual(plan['batch_size'],3);self.assertEqual(len(rows),3)
        self.assertTrue(all(sum(row.source==source for row in rows)<=2 for source in ('pap','seloger','leboncoin','paruvendu')))

    def test_budget_exposes_insufficient_capacity_instead_of_promising_six_hours(self):
        self.populate(800,sources=('pap',))
        plan=freshness.freshness_plan(self.now)
        self.assertTrue(plan['capacity_limited']);self.assertGreater(plan['estimated_coverage_hours'],6)
        self.assertEqual(len(freshness.due_publications(self.now)),2)

    def test_disabled_paused_and_recent_publications_are_not_checked(self):
        self.populate(4)
        with repo.session_scope() as session:
            session.add(m.RuntimeSetting(key='source_enabled:pap',value='false'))
            session.add(m.SourceRun(cycle_id='test',source='seloger',status='failed',started_at=self.now,finished_at=self.now,disabled_until=self.now+timedelta(hours=1)))
            session.query(m.ListingPublication).filter_by(source='leboncoin').update({'availability_checked_at':self.now})
        rows=freshness.due_publications(self.now);self.assertEqual([row.source for row in rows],['paruvendu'])
        plan=freshness.freshness_plan(self.now);self.assertEqual(plan['paused_publications'],1)
