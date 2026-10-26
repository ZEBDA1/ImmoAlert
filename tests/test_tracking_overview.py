"""Private tracking overview: ownership, archives, filters and bounded pages."""
from datetime import datetime,timedelta
import unittest
from unittest.mock import patch

from database import models as m,repo
from services import property_tracking,provider_credentials
import test_web_api as base


class TrackingOverviewTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=base.SelfHostedWebTests.asyncSetUp
    login=base.SelfHostedWebTests.login
    post=base.SelfHostedWebTests.post

    async def get_page(self,query=''):
        response=await self.client.get('/api/tracking'+query)
        self.assertEqual(response.status,200,await response.text())
        return await response.json()

    async def test_private_even_when_another_user_id_is_supplied(self):
        self.assertEqual((await self.client.get('/api/tracking')).status,401)
        repo.add_favorite(self.member.id,self.item.id)
        property_tracking.save_note(self.member.id,self.item.id,{'stage':'contacted','note':'Note privée agence'})
        await self.login(42)
        self.assertEqual((await self.get_page('?user_id='+str(self.member.id)))['total'],0)
        await self.login(99)
        own=await self.get_page();self.assertEqual(own['items'][0]['tracking']['note'],'Note privée agence')

    async def test_favorites_without_note_and_archived_notes_are_included(self):
        await self.login(99);repo.add_favorite(self.member.id,self.item.id)
        self.assertEqual((await self.get_page())['items'][0]['tracking']['stage'],'to_contact')
        property_tracking.save_note(self.member.id,self.item.id,{'stage':'contacted','note':'À rappeler'})
        repo.remove_favorite(self.member.id,self.item.id)
        page=await self.get_page('?scope=archived')
        self.assertEqual(page['total'],1);self.assertTrue(page['items'][0]['archived'])
        self.assertEqual(page['items'][0]['tracking']['note'],'À rappeler')
        self.assertEqual((await self.get_page('?scope=active'))['total'],0)

    async def test_accent_insensitive_note_destination_search_and_literal_wildcards(self):
        await self.login(99);repo.add_favorite(self.member.id,self.item.id)
        property_tracking.save_note(self.member.id,self.item.id,{'stage':'contacted','note':'Échanger avec l’agence','commute_destination':'École de musique'})
        for query in ('echanger','école','agence'):
            with self.subTest(query=query):self.assertEqual((await self.get_page('?q='+query))['total'],1)
        self.assertEqual((await self.get_page('?q=introuvable'))['total'],0)

    async def test_removed_content_stays_hidden_but_private_note_survives(self):
        await self.login(99);repo.add_favorite(self.member.id,self.item.id)
        property_tracking.save_note(self.member.id,self.item.id,{'stage':'rejected','note':'Mon compte rendu'})
        with repo.session_scope() as session:session.get(m.Listing,self.item.id).removed_at=datetime.utcnow()
        page=await self.get_page();item=page['items'][0]
        self.assertFalse(item['available']);self.assertTrue(item['archived'])
        self.assertEqual(item['listing']['title'],'Annonce retirée');self.assertEqual(item['listing']['images'],[])
        self.assertEqual(item['tracking']['note'],'Mon compte rendu')
        self.assertEqual((await self.get_page('?q=rouen'))['total'],0)

    async def test_stages_upcoming_visits_and_stable_pagination(self):
        await self.login(99);repo.add_favorite(self.member.id,self.item.id)
        visit=datetime.utcnow()+timedelta(days=2)
        property_tracking.save_note(self.member.id,self.item.id,{'stage':'visit','note':'Visite prévue','visit_at':visit.isoformat()+'Z'})
        with repo.session_scope() as session:
            rows=[m.Listing(source='unit',external_id='tracking-'+str(i),url='https://example.test/'+str(i),title='Bien '+str(i),price=800,location='Rouen',inventory_status='active') for i in range(53)]
            session.add_all(rows);session.flush();session.add_all(m.Favorite(user_id=self.member.id,listing_id=row.id) for row in rows)
        first=await self.get_page();self.assertEqual(first['total'],54);self.assertEqual(first['items'][0]['listing']['id'],self.item.id)
        second=await self.get_page('?page=2');self.assertTrue({i['listing']['id'] for i in first['items']}.isdisjoint(i['listing']['id'] for i in second['items']))
        last=await self.get_page('?page=999');self.assertEqual(last['page'],3);self.assertEqual(len(last['items']),14)
        upcoming=await self.get_page('?upcoming=1');self.assertEqual(upcoming['total'],1);self.assertEqual(upcoming['upcoming_count'],1)
        self.assertEqual(upcoming['counts']['to_contact'],53)
        self.assertEqual((await self.get_page('?stage=contacted'))['total'],0)
        with repo.session_scope() as session:session.get(m.MemberListingNote,(self.member.id,self.item.id)).visit_at=datetime.utcnow()-timedelta(days=1)
        self.assertEqual((await self.get_page('?upcoming=1'))['total'],0)

    async def test_invalid_filters_are_rejected(self):
        await self.login(99)
        for query in ('limit=0','limit=49','page=-1','stage=other','scope=other','upcoming=yes','sort=other','q='+'x'*121):
            with self.subTest(query=query):self.assertEqual((await self.client.get('/api/tracking?'+query)).status,400)

    async def test_configuring_plan_requires_admin_and_never_sends_provider_requests(self):
        payload={'provider':'flashproxy','operation':'configure_plan','plan_id':'new-test-plan'}
        await self.login(99)
        self.assertEqual((await self.client.post('/api/admin/providers',json=payload,headers={'Origin':self.origin})).status,403)
        await self.login(42)
        with patch.object(provider_credentials.requests,'get') as get,patch.object(provider_credentials.requests,'request') as request:
            response=await self.client.post('/api/admin/providers',json=payload,headers={'Origin':self.origin})
            self.assertEqual(response.status,200,await response.text());get.assert_not_called();request.assert_not_called()
        self.assertEqual(provider_credentials.flashproxy_plan_id(),'new-test-plan')
        for value in (None,4,'../escape','x'*129):
            response=await self.client.post('/api/admin/providers',json={**payload,'plan_id':value},headers={'Origin':self.origin})
            self.assertEqual(response.status,400)
        response=await self.client.post('/api/admin/providers',json={**payload,'provider':'firecrawl'},headers={'Origin':self.origin})
        self.assertEqual(response.status,400)

    async def test_two_plan_admin_configuration_preserves_pair_and_validates_lists(self):
        payload={'provider':'flashproxy','operation':'configure_plans','plan_ids':['plan-one','plan-two']}
        await self.login(99)
        self.assertEqual((await self.client.post('/api/admin/providers',json=payload,headers={'Origin':self.origin})).status,403)
        await self.login(42)
        with patch.object(provider_credentials,'environment_keys',return_value=()):
            response=await self.client.post('/api/admin/providers',json=payload,headers={'Origin':self.origin})
            self.assertEqual(response.status,200,await response.text())
            data=await (await self.client.get('/api/admin/providers')).json()
            self.assertEqual(next(p for p in data['providers'] if p['id']=='flashproxy')['plan_ids'],['plan-one','plan-two'])
        for values in (None,'plan-one',['a','b','c'],['same','same']):
            response=await self.client.post('/api/admin/providers',json={**payload,'plan_ids':values},headers={'Origin':self.origin})
            self.assertEqual(response.status,400)
        response=await self.client.post('/api/admin/providers',json={'provider':'flashproxy','operation':'configure_plan','plan_id':'old-client'},headers={'Origin':self.origin})
        self.assertEqual(response.status,400);self.assertEqual(provider_credentials.flashproxy_plan_ids(),('plan-one','plan-two'))
        response=await self.client.post('/api/admin/providers',json={**payload,'plan_ids':['plan-one']},headers={'Origin':self.origin})
        self.assertEqual(response.status,200);self.assertEqual(provider_credentials.flashproxy_plan_ids(),('plan-one',))
