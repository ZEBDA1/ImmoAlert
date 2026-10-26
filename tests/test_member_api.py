from __future__ import annotations

import asyncio
from datetime import datetime,timedelta
import hashlib
import hmac
import json
import time
import unittest
import os
os.environ['BOT_TOKEN']='test-token'
os.environ['DATABASE_URL']='sqlite://'
os.environ['ENABLE_GEOCODING']='false'
from unittest.mock import AsyncMock,patch
from uuid import uuid4

from sqlalchemy import select
from database import models as m,repo
from config import settings
from services.web_api import STATE
import test_web_api as base


class MemberApiTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=base.SelfHostedWebTests.asyncSetUp
    post=base.SelfHostedWebTests.post
    login=base.SelfHostedWebTests.login

    async def test_member_routes_require_authentication(self):
        for path in [f'/api/listings/{self.item.id}/area','/api/inventory','/api/preferences','/api/notifications','/api/comparison?ids=1,2',f'/api/listings/{self.item.id}/note',f'/api/listings/{self.item.id}/prices',f'/api/listings/{self.item.id}/updates',f'/api/listings/{self.item.id}/events']:
            with self.subTest(path=path):self.assertEqual((await self.client.get(path)).status,401)

    async def test_bootstrap_does_not_download_inventory_and_pages_pass_800_rows(self):
        await self.login(99)
        now=datetime.utcnow()
        with repo.session_scope() as session:
            session.add_all([m.Listing(source='unit',external_id=f'page-{index}',url=f'https://example.test/{index}',title=f'Appartement {index}',price=800+index,surface_m2=40,rooms=2,location='Rouen 76000',last_seen_at=now,first_seen_at=now+timedelta(seconds=index),inventory_status='active') for index in range(900)])
        self.app[STATE].global_at=0
        compact=await (await self.client.get('/api/bootstrap')).json()
        self.assertEqual(compact['snapshot']['listings'],[])
        self.assertEqual(compact['snapshot']['total_listings'],901)
        page=await (await self.client.get('/api/inventory?limit=24&page=38&sort=recent')).json()
        self.assertEqual(page['total'],901);self.assertEqual(len(page['items']),13)
        self.assertIsNone(page['next_page'])
        self.assertTrue(all(len(item['images'])<=1 and item['description']=='' for item in page['items']))
        older=await (await self.client.get('/api/inventory?limit=24&page=37&sort=recent')).json()
        self.assertTrue(set(item['id'] for item in page['items']).isdisjoint(item['id'] for item in older['items']))

    async def test_inventory_filters_sort_and_saved_search_ownership(self):
        await self.login(99)
        own=repo.create_or_update_filter(self.member.id,name='default',city='Rouen',price_max=1000)
        other=repo.create_or_update_filter(self.admin.id,name='private',city='Paris')
        result=await (await self.client.get(f'/api/inventory?q=rouen&budget=900&surface=30&rooms=2&filter_id={own.id}&sort=price')).json()
        self.assertEqual(result['total'],1)
        self.assertEqual((await self.client.get(f'/api/inventory?filter_id={other.id}')).status,400)
        for query in ['limit=0','limit=1000','page=-1','budget=nan','sort=private','rooms=2.5']:
            with self.subTest(query=query):self.assertEqual((await self.client.get('/api/inventory?'+query)).status,400)

    async def test_notes_are_owned_and_support_full_unicode_limit(self):
        await self.login(99)
        path=f'/api/listings/{self.item.id}/note'
        self.assertEqual((await self.client.post(path,json={'note':'Privée'},headers={'Origin':self.origin})).status,403)
        repo.add_favorite(self.member.id,self.item.id)
        payload={'note':'é'*4000,'stage':'contacted'}
        response=await self.client.post(path,json=payload,headers={'Origin':self.origin})
        self.assertEqual(response.status,200)
        self.assertEqual((await response.json())['tracking']['note'],payload['note'])
        await self.login(42);repo.add_favorite(self.admin.id,self.item.id)
        self.assertIsNone((await (await self.client.get(path)).json())['tracking'])

    async def test_comparison_only_accepts_two_to_four_owned_favorites(self):
        await self.login(99)
        other=repo.save_listing(source='unit',external_id='two',url='https://example.test/2',title='Maison',price=1000,surface_m2=80,location='Rouen',rooms=4,last_seen_at=datetime.utcnow(),inventory_status='active').listing
        repo.add_favorite(self.member.id,self.item.id)
        self.assertEqual((await self.client.get(f'/api/comparison?ids={self.item.id},{other.id}')).status,403)
        repo.add_favorite(self.member.id,other.id)
        with repo.session_scope() as session:session.get(m.Listing,self.item.id).details=json.dumps({'DPE':'C'})
        response=await self.client.get(f'/api/comparison?ids={self.item.id},{other.id}')
        self.assertEqual(response.status,200)
        data=await response.json();self.assertEqual(len(data['items']),2)
        self.assertEqual(data['items'][0]['features']['Classe énergie (DPE)'],'C')
        self.assertIsNone(data['items'][1]['charges_included'])
        self.assertEqual((await self.client.get(f'/api/comparison?ids={self.item.id}')).status,400)
        self.assertEqual((await self.client.get(f'/api/comparison?ids={self.item.id},{self.item.id}')).status,400)

    async def test_metrics_are_validated_and_only_admin_can_read_aggregates(self):
        await self.login(99)
        response=await self.client.post('/api/performance',json={'samples':[{'name':'detail_visible','value_ms':123}]},headers={'Origin':self.origin})
        self.assertEqual(response.status,202)
        self.assertEqual((await self.client.get('/api/admin/performance')).status,403)
        bad=await self.client.post('/api/performance',json={'samples':[{'name':'private_note','value_ms':123}]},headers={'Origin':self.origin})
        self.assertEqual(bad.status,400)
        await self.login(42)
        data=await (await self.client.get('/api/admin/performance')).json()
        self.assertTrue(any(item['name']=='detail_visible' and item['median_ms']==123 for item in data['metrics']))

    async def test_sse_and_buffered_update_return_when_the_worker_completes(self):
        await self.login(99)
        gate=asyncio.Event()
        async def collect(identifier):await gate.wait();return self.item
        with patch('services.listing_details.get_listing_details',side_effect=collect):
            response=await self.client.get(f'/api/listings/{self.item.id}/events')
            self.assertEqual(response.headers['Content-Type'],'text/event-stream; charset=utf-8')
            self.assertEqual(await response.content.readline(),b'event: connected\n')
            gate.set();body=await asyncio.wait_for(response.text(),1)
            self.assertIn('event: listing',body);self.assertIn('price_history',body)
        with patch('services.listing_details.get_listing_details',new=AsyncMock(return_value=self.item)):
            response=await self.client.get(f'/api/listings/{self.item.id}/updates')
            self.assertEqual(response.status,200);self.assertFalse((await response.json())['enriching'])
        self.assertEqual(self.app[STATE].detail_connections[self.member.id],0)

    async def test_signed_gateway_preserves_inventory_query_and_rejects_replay(self):
        await self.login(99)
        secret='s'*48;public='https://example.test';stamp=str(int(time.time()));nonce=uuid4().hex
        path='/api/inventory?limit=2&page=1&q=Rouen'
        signed='\n'.join(['GET',path,stamp,nonce,hashlib.sha256(b'').hexdigest(),public,'127.0.0.1'])
        headers={'Origin':public,'X-Immo-Public-Origin':public,'X-Immo-Timestamp':stamp,'X-Immo-Nonce':nonce,'X-Immo-Client-IP':'127.0.0.1','X-Immo-Signature':hmac.new(secret.encode(),signed.encode(),hashlib.sha256).hexdigest()}
        with patch.object(settings,'web_proxy_secret',secret),patch.object(settings,'web_proxy_origin',public):
            response=await self.client.get(path,headers=headers)
            self.assertEqual(response.status,200)
            self.assertEqual((await response.json())['total'],1)
            self.assertEqual((await self.client.get(path,headers=headers)).status,403)


    async def test_location_area_is_private_and_returns_only_an_area(self):
        await self.login(99)
        expected={'kind':'indicative','center':[49.44,1.09],'radius_m':500,'geometry':None,'label':'Secteur indicatif','detail':'Sans adresse exacte.'}
        with patch('services.location_area.for_listing',new=AsyncMock(return_value=expected)):
            response=await self.client.get(f'/api/listings/{self.item.id}/area')
            self.assertEqual(response.status,200);self.assertEqual((await response.json())['area'],expected)
        self.assertEqual((await self.client.get('/api/listings/999999/area')).status,404)

    async def test_comparison_only_includes_the_viewers_criteria_and_omits_private_note_text(self):
        from services import property_tracking
        await self.login(99)
        second=repo.save_listing(source='unit',external_id='criteria-two',url='https://example.test/criteria-two',title='Deuxième',price=900,surface_m2=45,location='Rouen',last_seen_at=datetime.utcnow(),inventory_status='active').listing
        for user in (self.member,self.admin):
            for identifier in (self.item.id,second.id):repo.add_favorite(user.id,identifier)
        private='TEXTE PRIVÉ À NE PAS TRANSMETTRE AU COMPARATEUR'
        property_tracking.save_note(self.member.id,self.item.id,{'note':private,'commute_minutes':18,'parking':'yes'})
        path=f'/api/comparison?ids={self.item.id},{second.id}'
        response=await self.client.get(path);data=await response.json()
        self.assertEqual(data['items'][0]['personal_criteria']['commute_minutes'],18)
        self.assertNotIn(private,json.dumps(data));self.assertNotIn('tracking',data['items'][0])
        await self.login(42)
        self.assertIsNone((await (await self.client.get(path)).json())['items'][0]['personal_criteria'])
