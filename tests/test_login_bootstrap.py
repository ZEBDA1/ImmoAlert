"""Authentication must complete through the endpoint used by the current site."""
from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit
import unittest

import test_web_api as base
from database import models as m, repo, web_repo
from services import site_sync, web_auth
from services.web_api import COOKIE


class LoginBootstrapTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base.SelfHostedWebTests.asyncSetUp
    post = base.SelfHostedWebTests.post

    def session_cookie(self):
        return self.client.session.cookie_jar.filter_cookies(self.client.make_url('/'))[COOKIE].value

    async def bootstrap(self):
        response = await self.client.get('/api/bootstrap')
        self.assertEqual(response.status, 200, await response.text())
        return response, await response.json()

    async def begin_pairing(self):
        response = await self.post('link')
        self.assertEqual(response.status, 202)
        command = await response.json()
        pair = parse_qs(urlsplit(command['url']).query)['start'][0][5:]
        return command['id'], pair

    async def test_anonymous_bootstrap_returns_json_instead_of_static_404(self):
        _, data = await self.bootstrap()
        self.assertIsNone(data['account'])
        self.assertIsNone(data['snapshot'])
        self.assertEqual(data['bot_username'], 'immoalert_test_bot')

    async def test_password_session_is_immediately_visible_to_bootstrap(self):
        account = web_auth.create_account(self.admin.id, 'password_only')
        response = await self.client.post('/api/login', json=account['credentials'], headers={'Origin': self.origin})
        self.assertEqual(response.status, 200)
        self.assertIn(COOKIE, response.cookies)
        _, data = await self.bootstrap()
        self.assertEqual(data['account']['user_id'], account['account']['user_id'])
        self.assertEqual(data['account']['login'], 'password_only')
        self.assertFalse(data['account']['is_admin'])
        self.assertIsNone(data['account']['telegram_id'])
        self.assertEqual(data['snapshot']['listings'], [])
        self.assertEqual(data['snapshot']['total_listings'], 1)
        self.assertEqual((await self.client.get('/api/inventory')).status, 200)

    async def test_wrong_password_does_not_create_an_authenticated_session(self):
        account = web_auth.create_account(self.admin.id, 'password_only')
        response = await self.client.post('/api/login', json={**account['credentials'], 'password': 'incorrect'}, headers={'Origin': self.origin})
        self.assertEqual(response.status, 401)
        _, data = await self.bootstrap()
        self.assertIsNone(data['account'])

    async def test_telegram_approval_completes_on_bootstrap_and_rotates_cookie(self):
        command_id, pair = await self.begin_pairing()
        anonymous = self.session_cookie()
        _, waiting = await self.bootstrap()
        self.assertIsNone(waiting['account'])
        self.assertEqual(next(c for c in waiting['commands'] if c['id'] == command_id)['status'], 'pending')
        self.assertTrue(site_sync.approve_pairing(pair, self.member.telegram_id))
        response, linked = await self.bootstrap()
        self.assertIn(COOKIE, response.cookies)
        self.assertNotEqual(self.session_cookie(), anonymous)
        self.assertIsNone(web_repo.find_session(anonymous))
        self.assertIsNone(repo.get_runtime_setting('site_pair:' + pair))
        self.assertEqual(linked['account']['user_id'], self.member.id)
        self.assertEqual(linked['account']['telegram_id'], self.member.telegram_id)
        self.assertEqual(next(c for c in linked['commands'] if c['id'] == command_id)['status'], 'succeeded')
        _, subsequent = await self.bootstrap()
        self.assertEqual(subsequent['account']['user_id'], self.member.id)
        self.assertEqual((await self.client.get('/api/inventory')).status, 200)

    async def test_expired_telegram_link_is_reported_as_failed_on_bootstrap(self):
        command_id, _ = await self.begin_pairing()
        with repo.session_scope() as session:
            session.get(m.WebSession, web_repo.session_id(self.session_cookie())).pair_expires_at = datetime.utcnow() - timedelta(seconds=1)
        _, data = await self.bootstrap()
        self.assertIsNone(data['account'])
        command = next(c for c in data['commands'] if c['id'] == command_id)
        self.assertEqual(command['status'], 'failed')
        self.assertIn('expir', command['result'])
