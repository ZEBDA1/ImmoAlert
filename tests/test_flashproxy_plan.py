"""Runtime plan overrides persist independently of encrypted credentials."""
import json
import unittest
from unittest.mock import patch
from sqlalchemy import select
from config import settings
from database import models as m,repo
from services import provider_credentials as credentials
from utils import flashproxy
import test_provider_credentials as provider_tests
response=provider_tests.response

class FlashProxyPlanTests(unittest.TestCase):
    setUp=provider_tests.ProviderCredentialsTests.setUp
    add=provider_tests.ProviderCredentialsTests.add

    def test_override_and_explicit_automatic_mode_survive_environment_changes(self):
        self.assertEqual(credentials.flashproxy_plan_id(),'plan-test')
        credentials.save_flashproxy_plan(' plan-new ')
        with patch.object(settings,'flashproxy_plan_id','environment-old'):
            self.assertEqual(credentials.flashproxy_plan_id(),'plan-new')
            provider=next(item for item in credentials.list_providers()['providers'] if item['id']=='flashproxy')
            self.assertEqual(provider['plan_id'],'plan-new')
            credentials.save_flashproxy_plan('')
            self.assertEqual(credentials.flashproxy_plan_id(),'')
        self.assertEqual(credentials.flashproxy_plan_id(),'')

    def test_plan_change_invalidates_proxy_cache_and_quota_without_replacing_key(self):
        record=self.add('flashproxy','flash-test-key',enabled=False)
        with repo.session_scope() as session:
            row=session.get(m.ProviderCredential,record['id']);row.quota_json=json.dumps({'remaining':99});row.status='ok'
        before=flashproxy.configuration_id();credentials.save_flashproxy_plan('other-plan')
        self.assertNotEqual(before,flashproxy.configuration_id())
        provider=next(item for item in credentials.list_providers()['providers'] if item['id']=='flashproxy')
        key=provider['credentials'][0]
        self.assertEqual(key['id'],record['id']);self.assertFalse(key['enabled']);self.assertIsNone(key['quota']['remaining']);self.assertIsNone(key['checked_at'])

    def test_generation_and_quota_use_selected_plan(self):
        self.add('flashproxy','flash-test-key');credentials.save_flashproxy_plan('new-plan')
        client=flashproxy.FlashProxyClient()
        with patch.object(flashproxy.requests,'request',return_value=response({'proxies':['http://user:pass@proxy.example:8080']})) as request:
            client.route()
            self.assertTrue(request.call_args.args[1].endswith('/plans/new-plan/proxies'))
            credentials.save_flashproxy_plan('next-plan');client.route()
            self.assertEqual(request.call_count,2);self.assertTrue(request.call_args.args[1].endswith('/plans/next-plan/proxies'))
        record=next(item for item in credentials.list_providers()['providers'] if item['id']=='flashproxy')['credentials'][0]
        with patch.object(credentials.requests,'get',return_value=response({'plans':[{'plan_id':'wrong','usage':{'remaining_bytes':9000000000}},{'plan_id':'next-plan','usage':{'remaining_bytes':2000000000,'max_bytes':3000000000}}]})):
            result=credentials.refresh_quota('flashproxy',record['id'])
        self.assertEqual(result['quota']['remaining'],2)

    def test_old_plan_quota_cannot_overwrite_new_configuration(self):
        record=self.add('flashproxy','flash-test-key')
        def switch(*args):
            credentials.save_flashproxy_plan('new-plan')
            return 'ok','old quota',{'remaining':42}
        with patch.object(credentials,'_fetch_quota',side_effect=switch):
            with self.assertRaisesRegex(ValueError,'plan a changé'):credentials.refresh_quota('flashproxy',record['id'])
        provider=next(item for item in credentials.list_providers()['providers'] if item['id']=='flashproxy')
        self.assertIsNone(provider['credentials'][0]['quota']['remaining'])
