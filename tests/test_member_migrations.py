from __future__ import annotations
from datetime import datetime
import tempfile
from pathlib import Path
import unittest
import os
os.environ['BOT_TOKEN']='test-token'
os.environ['DATABASE_URL']='sqlite://'
os.environ['ENABLE_GEOCODING']='false'
from unittest.mock import patch
from sqlalchemy import text,select,inspect
from sqlalchemy.orm import sessionmaker
from database import models as m,repo,session as database
from services import property_tracking,alert_preferences


class MemberMigrationTests(unittest.TestCase):
    def test_additive_migration_preserves_legacy_data_and_bootstraps_price_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine=database._make_engine('sqlite:///'+str(Path(temporary)/'legacy.db'))
            try:
                database.Base.metadata.create_all(engine)
                moment=datetime.utcnow()
                with engine.begin() as conn:
                    conn.execute(m.User.__table__.insert().values(id=1,telegram_id=42,username='fixture'))
                    conn.execute(m.Listing.__table__.insert().values(id=1,source='pap',external_id='legacy',url='https://www.pap.fr/annonces/legacy',title='Appartement rénové',price=800,location='Rouen 76000',surface_m2=40,last_seen_at=moment,inventory_status='active'))
                    conn.execute(m.ListingPublication.__table__.insert().values(listing_id=1,source='pap',external_id='legacy',url='https://www.pap.fr/annonces/legacy',last_seen_at=moment))
                    conn.execute(m.Favorite.__table__.insert().values(user_id=1,listing_id=1))
                    for name in ('member_notifications','member_listing_notes','notification_preferences','listing_price_history','performance_samples'):conn.execute(text('DROP TABLE '+name))
                    conn.execute(text('ALTER TABLE listings DROP COLUMN search_text'))
                    for column in ('price','availability_status','availability_checked_at','availability_detail','absence_count','absence_first_at'):conn.execute(text('ALTER TABLE listing_publications DROP COLUMN '+column))
                with patch.object(database,'engine',engine),patch.object(repo,'SessionLocal',sessionmaker(bind=engine,expire_on_commit=False)):
                    database.init_db();database.init_db()
                    row=repo.get_listing_by_id(1)
                    self.assertEqual(row.search_text,'appartement renove rouen 76000')
                    self.assertTrue(repo.is_favorite(1,1))
                    self.assertEqual(alert_preferences.public_preferences(1)['timezone'],'Europe/Paris')
                    repo.save_listing(source='pap',external_id='legacy',url=row.url,price=750,last_seen_at=moment,inventory_status='active')
                    history=property_tracking.price_history(1)['items']
                    self.assertEqual(len(history),2)
                    self.assertEqual(history[0]['previous_price'],800)
                    self.assertTrue({'performance_samples','member_listing_notes','notification_preferences'}.issubset(inspect(engine).get_table_names()))
                    with repo.session_scope() as session:self.assertEqual(session.scalar(select(m.ListingPublication)).absence_count,0)
            finally:engine.dispose()


    def test_personal_criteria_are_added_to_existing_notes_without_erasing_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine=database._make_engine('sqlite:///'+str(Path(temporary)/'notes.db'))
            self.addCleanup(engine.dispose);database.Base.metadata.create_all(engine)
            with engine.begin() as connection:
                for column in ('commute_minutes','commute_destination','commute_mode','parking','accessibility'):
                    connection.execute(text('ALTER TABLE member_listing_notes DROP COLUMN '+column))
                connection.execute(text("INSERT INTO users(id,telegram_id,is_premium,active,created_at) VALUES (1,42,0,1,'2026-10-26')"))
                connection.execute(text("INSERT INTO listings(id,source,external_id,url,first_seen_at) VALUES (1,'pap','legacy-note','https://www.pap.fr/legacy','2026-10-26')"))
                connection.execute(text("INSERT INTO member_listing_notes(user_id,listing_id,note,stage,updated_at) VALUES (1,1,'Note conservée','contacted','2026-10-26')"))
            with patch.object(database,'engine',engine):database.init_db();database.init_db()
            with engine.connect() as connection:
                row=connection.execute(text('SELECT note,stage,commute_minutes,parking,accessibility FROM member_listing_notes')).one()
                self.assertEqual(tuple(row),('Note conservée','contacted',None,None,None))
