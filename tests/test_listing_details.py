from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ["BOT_TOKEN"]="test-token"
os.environ["DATABASE_URL"]="sqlite://"
os.environ["ENABLE_GEOCODING"]="false"

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from database import repo, models as m
from database.session import Base, _make_engine
from scraper.base import ScrapedListing
from scraper.details import extract_details
from scraper.quality import normalize_listing
from services import listing_details
from services.notification import detail_pages, listing_keyboard
from utils.images import decode_images

DESCRIPTION="Ce bel appartement lumineux possède une cuisine aménagée ouverte sur le séjour, deux grandes fenêtres côté jardin et un balcon privatif au calme. Il se situe au deuxième étage d’une petite résidence entretenue, proche du tramway et des commerces du quartier Saint-Sever."


class PropertyDedupeTests(unittest.TestCase):
    def setUp(self):
        engine=_make_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        patcher=patch.object(repo,"SessionLocal",sessionmaker(bind=engine,expire_on_commit=False))
        patcher.start();self.addCleanup(patcher.stop)

    def save(self,source="pap",external_id="one",**kwargs):
        values=dict(source=source,external_id=external_id,url=f"https://{source}.fr/annonces/{external_id}",title="Appartement T2",price=800,surface_m2=40,rooms=2,location="Rouen (76000)",description=DESCRIPTION,normalized_city="Rouen",normalized_postal_code="76000",last_seen_at=datetime.utcnow(),inventory_status="active")
        values.update(kwargs)
        return repo.save_listing(**values)

    def test_cross_portal_wording_price_rounding_and_source_alias(self):
        first=self.save()
        second=self.save("seloger","two",title="Location à Rouen — beau deux pièces",price=815,surface_m2=40.3,description="À découvrir : "+DESCRIPTION)
        self.assertFalse(second.is_new)
        self.assertEqual(second.listing.id,first.listing.id)
        self.assertEqual(second.duplicate_reason,"cross_platform")
        self.assertEqual({p["source"] for p in repo.listing_publications(first.listing.id)},{"pap","seloger"})
        repeated=self.save("seloger","two",description=None)
        self.assertEqual(repeated.listing.id,first.listing.id)
        self.assertIn("cuisine aménagée",repeated.listing.description)

    def test_identical_basic_criteria_do_not_merge_other_properties(self):
        self.save(description="Bel appartement T2")
        second=self.save("seloger","two",description="Bel appartement T2")
        self.assertTrue(second.is_new)

    def test_conflicting_locality_rooms_and_surface_do_not_merge(self):
        self.save()
        for i,change in enumerate([{"location":"Rouen (76100)"},{"location":"Dieppe (76000)"},{"rooms":3},{"surface_m2":48}]):
            with self.subTest(change=change):
                self.assertTrue(self.save("seloger",f"other-{i}",**change).is_new)

    def test_common_photo_is_evidence_and_all_photos_are_kept(self):
        first=self.save(description=None,images=json.dumps(["https://cdn.test/home.jpg?w=200"]))
        second=self.save("seloger","two",description=None,images=json.dumps(["https://cdn.test/home.jpg?w=1000","https://cdn.test/kitchen.jpg"]))
        self.assertEqual(second.listing.id,first.listing.id)
        self.assertEqual(len(decode_images(second.listing.images)),2)

    def test_distinct_query_photo_ids_are_not_shared_images(self):
        self.save(description=None,images=json.dumps(["https://cdn.test/photo?id=111&w=200"]))
        other=self.save("seloger","two",description=None,images=json.dumps(["https://cdn.test/photo?id=222&w=1000"]))
        self.assertTrue(other.is_new)

    def test_no_second_alert_or_favorite_for_other_portal(self):
        user=repo.get_or_create_user(10)
        first=self.save().listing
        repo.mark_alert_sent(user.id,first.id)
        repo.add_favorite(user.id,first.id)
        second=self.save("seloger","two").listing
        self.assertTrue(repo.has_sent_alert(user.id,second.id))
        self.assertFalse(repo.add_favorite(user.id,second.id))
        self.assertFalse(repo.add_pending_alert(user.id,second.id,datetime.utcnow()))

    def test_reconcile_preserves_favorites_sent_alerts_and_old_links(self):
        first=self.save().listing
        user=repo.get_or_create_user(10)
        repo.mark_alert_sent(user.id,first.id)
        with repo.session_scope() as session:
            second=m.Listing(source="seloger",external_id="legacy",url="https://seloger.com/legacy",title="T2",price=800,surface_m2=40,rooms=2,location="Rouen (76000)",description=DESCRIPTION,normalized_postal_code="76000",last_seen_at=datetime.utcnow(),inventory_status="active")
            session.add(second);session.flush();legacy_id=second.id
            session.add(m.Favorite(user_id=user.id,listing_id=second.id))
            session.add(m.SentAlert(user_id=user.id,listing_id=second.id))
        self.assertEqual(repo.reconcile_duplicate_listings(),1)
        self.assertEqual(repo.get_listing_by_id(legacy_id).id,first.id)
        self.assertEqual(repo.get_user_favorite_listings(user.id)[0].id,first.id)
        self.assertEqual(len(repo.get_recent_listings()),1)
        self.assertEqual(repo.reconcile_duplicate_listings(),0)
        self.assertTrue(repo.remove_favorite(user.id,legacy_id))

    def test_search_card_does_not_erase_enriched_photos_and_description(self):
        first=self.save(description=DESCRIPTION*12,images=json.dumps([f"https://cdn.test/{i}.jpg" for i in range(8)]))
        updated=self.save(description="Texte court",images=json.dumps(["https://cdn.test/0.jpg"]),price=850)
        self.assertEqual(first.listing.id,updated.listing.id)
        self.assertEqual(updated.listing.price,850)
        self.assertEqual(len(decode_images(updated.listing.images)),8)
        self.assertEqual(updated.listing.description,DESCRIPTION*12)


class DetailExtractionTests(unittest.TestCase):
    def test_structured_details_all_photos_coordinates_and_features(self):
        data={"@type":"RealEstateListing","url":"https://pap.fr/annonce/123","description":DESCRIPTION*20,"image":[f"https://img.test/{i}.jpg" for i in range(12)],"about":{"@type":"Apartment","numberOfBedrooms":2,"floorLevel":3,"address":{"streetAddress":"12 rue du Jardin","postalCode":"76000","addressLocality":"Rouen"},"geo":{"latitude":49.44,"longitude":1.1},"amenityFeature":[{"name":"Balcon","value":True}]}}
        parsed=extract_details('<script type="application/ld+json">'+json.dumps(data)+'</script>',data["url"],"123")
        self.assertEqual(parsed["description"],DESCRIPTION*20)
        self.assertEqual(len(parsed["images"]),12)
        self.assertEqual(parsed["latitude"],49.44)
        self.assertEqual(parsed["features"]["Balcon"],"Oui")
        self.assertIn("12 rue du Jardin",parsed["features"]["Adresse publiée"])

    def test_hydration_selects_current_ad_and_excludes_recommendations(self):
        data={"props":{"ad":{"id":123,"body":DESCRIPTION,"images":{"urls_large":["https://cdn.test/main.jpg"]},"attributes":[{"key_label":"DPE","value_label":"C"}],"location":{"lat":49.44,"lng":1.1}},"recommendations":[{"id":999,"body":"Ne pas extraire cette annonce","images":["https://cdn.test/other.jpg"]}]}}
        parsed=extract_details('<script id="__NEXT_DATA__" type="application/json">'+json.dumps(data)+'</script>',"https://leboncoin.fr/ad/123","123")
        self.assertEqual(parsed["images"],["https://cdn.test/main.jpg"])
        self.assertEqual(parsed["description"],DESCRIPTION)
        self.assertEqual(parsed["features"]["DPE"],"C")

    def test_scoped_gallery_and_description_ignore_other_images(self):
        html='<div id="description">Texte complet du logement</div><div class="gallery"><img src="/photo.jpg"><img data-src="/kitchen.jpg"></div><aside><img src="/other-home.jpg"></aside>'
        parsed=extract_details(html,"https://pap.fr/annonce/1","1")
        self.assertEqual(len(parsed["images"]),2)
        self.assertNotIn("https://pap.fr/other-home.jpg",parsed["images"])

    def test_normalization_keeps_complete_description(self):
        listing=normalize_listing(ScrapedListing(source="unit",external_id="1",url="https://test.example/1",title="Appartement",price=800,surface_m2=40,location="Rouen",description=DESCRIPTION*20))
        self.assertEqual(listing.description,DESCRIPTION*20)

    def test_nuxt_literal_is_scoped_to_current_property_without_execution(self):
        html='<script>window.__NUXT__=(function(a){return {annonce:{id:123,texte:'+json.dumps(DESCRIPTION)+',location:{latitude:49.44,longitude:1.1},photos:["https:\\u002F\\u002Fcdn.test\\u002Fmain.jpg"]},recommendations:[{id:999,photos:["https://cdn.test/other.jpg"]}]}})(false)</script>'
        parsed=extract_details(html,"https://www.ouestfrance-immo.com/123.htm","123")
        self.assertEqual(parsed["images"],["https://cdn.test/main.jpg"])
        self.assertEqual(parsed["description"],DESCRIPTION)
        self.assertEqual(parsed["longitude"],1.1)

    def test_telegram_pagination_escapes_html_within_limits(self):
        row=m.Listing(id=1,source="unit",external_id="1",url="https://test.example/1",title="T2 <jardin>",description=("<tag> & 🏠 "*1400),price=800,surface_m2=40,rooms=2)
        with patch.object(repo,"listing_publications",return_value=[{"source":"pap"}]):
            pages=detail_pages(row)
        self.assertGreater(len(pages),2)
        self.assertTrue(all(len(p.encode("utf-16-le"))//2<=3500 for p in pages))
        import html
        self.assertIn(row.description,html.unescape("".join(pages)))
        self.assertTrue(all("<tag>" not in page for page in pages))
        kb=listing_keyboard(ScrapedListing(source="unit",external_id="1",url="https://test.example/1",db_id=1))
        self.assertIn("info:1:0",[b.callback_data for buttons in kb.inline_keyboard for b in buttons])


class DetailEnrichmentTests(unittest.TestCase):
    setUp=PropertyDedupeTests.setUp
    save=PropertyDedupeTests.save
    def test_enrichment_is_cached_and_persists_full_gallery(self):
        first=self.save().listing
        html='<script type="application/ld+json">'+json.dumps({"@type":"RealEstateListing","description":DESCRIPTION*15,"image":[f"https://cdn.test/{i}.jpg" for i in range(9)]})+'</script>'
        with patch.object(listing_details,"fetch_html",return_value=html) as fetch,patch.object(listing_details,"_city_point",return_value=[1.1,49.44]):
            enriched=listing_details._load(first.id)
            again=listing_details._load(first.id)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(len(decode_images(again.images)),9)
        self.assertEqual(enriched.geo_precision,"city")
        self.assertEqual(enriched.description,DESCRIPTION*15)

    def test_portal_failure_keeps_existing_information(self):
        first=self.save().listing
        with patch.object(listing_details,"fetch_html",return_value=None),patch.object(listing_details,"_city_point",return_value=None):
            row=listing_details._load(first.id)
        self.assertEqual(row.description,DESCRIPTION)
        self.assertEqual(row.details_status,"partial")


class BeforeAlertEnrichmentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        PropertyDedupeTests.setUp(self)

    async def test_ambiguous_cards_are_enriched_before_resolving_property_id(self):
        first=PropertyDedupeTests.save(self,description=None).listing
        second=PropertyDedupeTests.save(self,"seloger","two",description=None).listing
        self.assertNotEqual(first.id,second.id)
        async def details(identifier):
            with repo.session_scope() as session:
                row=session.get(m.Listing,identifier)
                row.description=DESCRIPTION
            repo.reconcile_duplicate_listings()
            return repo.get_listing_by_id(identifier)
        with patch.object(listing_details,"get_listing_details",side_effect=details) as fetch:
            canonical=await listing_details.resolve_new_listing(second)
        self.assertEqual(canonical.id,first.id)
        self.assertEqual(fetch.call_count,2)
