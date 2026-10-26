from __future__ import annotations
from datetime import timedelta
import unittest
from unittest.mock import patch
import test_member_features as base
from database import models as m,repo
from services import property_tracking as tracking,web_auth,alert_preferences
from config import settings
from sqlalchemy import select

class ComparisonCriteriaTests(unittest.TestCase):
    setUp=base.MemberFeatureTests.setUp
    save=base.MemberFeatureTests.save

    def test_personal_criteria_are_private_and_legacy_saves_preserve_them(self):
        row=self.save();repo.add_favorite(self.user.id,row.id)
        payload={'note':'Appeler','stage':'contacted','commute_minutes':25,'commute_destination':'Gare principale','commute_mode':'bike','parking':'yes','accessibility':'no'}
        saved=tracking.save_note(self.user.id,row.id,payload)
        self.assertEqual(saved['commute_minutes'],25)
        with self.assertRaises(PermissionError):tracking.note_for(self.other.id,row.id)
        repo.add_favorite(self.other.id,row.id);self.assertIsNone(tracking.note_for(self.other.id,row.id))
        tracking.save_note(self.user.id,row.id,{'note':'Ancien client','stage':'contacted'})
        self.assertEqual(tracking.note_for(self.user.id,row.id)['parking'],'yes')
        cleared=tracking.save_note(self.user.id,row.id,{'commute_minutes':None,'parking':None})
        self.assertIsNone(cleared['parking']);self.assertIsNone(cleared['commute_minutes'])

    def test_personal_input_rejects_wrong_types_and_out_of_range_values(self):
        row=self.save();repo.add_favorite(self.user.id,row.id)
        for payload in [{'commute_minutes':True},{'commute_minutes':-1},{'commute_minutes':601},{'commute_minutes':2.5},{'commute_destination':'x'*161},{'commute_mode':{}},{'parking':[]},{'accessibility':'maybe'}]:
            with self.subTest(payload=payload),self.assertRaises(ValueError):tracking.save_note(self.user.id,row.id,payload)

    def test_note_merge_keeps_newer_criteria_and_complementary_known_values(self):
        first=m.MemberListingNote(note='Première note',stage='contacted',updated_at=self.now,parking='yes',commute_minutes=30)
        second=m.MemberListingNote(note='Deuxième note',stage='to_contact',updated_at=self.now+timedelta(minutes=1),accessibility='no',commute_minutes=20)
        tracking.merge_note_rows(first,second)
        self.assertEqual(first.commute_minutes,20);self.assertEqual(first.parking,'yes');self.assertEqual(first.accessibility,'no');self.assertIn('Première',first.note);self.assertIn('Deuxième',first.note)

    def test_linking_telegram_preserves_private_tracking_and_preferences(self):
        with patch.object(settings,'admin_telegram_ids',{self.user.telegram_id}):
            account=web_auth.create_account(self.user.id,'local_criteria')
            local_id=account['account']['user_id'];row=self.save();repo.add_favorite(local_id,row.id)
            tracking.save_note(local_id,row.id,{'note':'Note privée à conserver','commute_minutes':18,'commute_mode':'walk','parking':'no'})
            alert_preferences.save_preferences(local_id,{'timezone':'Europe/Brussels','daily_summary':True,'summary_time':'09:15','price_alerts':False})
            with repo.session_scope() as session:
                web_auth.link_profile(session,session.get(m.WebAccount,account['account']['id']),session.get(m.User,self.other.id))
            note=tracking.note_for(self.other.id,row.id)
            self.assertEqual(note['commute_minutes'],18);self.assertEqual(note['parking'],'no')
            self.assertEqual(alert_preferences.public_preferences(self.other.id)['summary_time'],'09:15')
            self.assertFalse(alert_preferences.public_preferences(self.other.id)['price_alerts'])
