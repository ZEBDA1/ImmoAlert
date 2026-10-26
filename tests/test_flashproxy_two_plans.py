"""Two plans actually share load, preserve sticky sessions and isolate failures."""
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
from config import settings
from database import models as m,repo
from services import provider_credentials as credentials
from utils import flashproxy as fp
import test_provider_credentials as fixtures

response=fixtures.response
A='http://user-a:private-pass@gateway-a.example:8080'
B='http://user-b:private-pass@gateway-b.example:8080'

class TwoPlansTests(unittest.TestCase):
    setUp=fixtures.ProviderCredentialsTests.setUp
    add=fixtures.ProviderCredentialsTests.add

    def pair(self):
        record=self.add('flashproxy','fake-flash-key')
        credentials.save_flashproxy_plans(['plan-a','plan-b'])
        return record

    def test_legacy_configuration_and_explicit_empty_override_are_preserved(self):
        with repo.session_scope() as session:session.add(m.RuntimeSetting(key='flashproxy_plan_id',value='legacy-id'))
        with patch.object(settings,'flashproxy_plan_ids',('environment-a','environment-b')):
            self.assertEqual(credentials.flashproxy_plan_ids(),('legacy-id',))
            credentials.save_flashproxy_plans(['new-a','new-b'])
            self.assertEqual(credentials.flashproxy_plan_ids(),('new-a','new-b'))
            credentials.save_flashproxy_plans([])
            self.assertEqual(credentials.flashproxy_plan_ids(),())

    def test_plural_environment_configuration_is_used_only_without_admin_override(self):
        with patch.object(settings,'flashproxy_plan_ids',('environment-a','environment-b')):
            self.assertEqual(credentials.flashproxy_plan_ids(),('environment-a','environment-b'))
            credentials.save_flashproxy_plans(['selected-a'])
            self.assertEqual(credentials.flashproxy_plan_ids(),('selected-a',))

    def test_invalid_pairs_and_old_client_cannot_silently_remove_second_plan(self):
        self.pair()
        for value in (None,'plan-a',['a','b','c'],['same',' same '],[''],[23],['bad/path']):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):credentials.save_flashproxy_plans(value)
                self.assertEqual(credentials.flashproxy_plan_ids(),('plan-a','plan-b'))
        with self.assertRaisesRegex(ValueError,'Deux plans'):credentials.save_flashproxy_plan('replacement')
        self.assertEqual(credentials.flashproxy_plan_ids(),('plan-a','plan-b'))

    def test_concurrent_requests_use_both_plans_with_only_two_generations(self):
        self.pair();client=fp.FlashProxyClient()
        def generate(method,url,**kwargs):return response({'proxies':[A if '/plan-a/' in url else B]})
        with patch.object(fp.requests,'request',side_effect=generate) as request:
            with ThreadPoolExecutor(8) as pool:routes=list(pool.map(lambda _:client.route().url,range(20)))
        self.assertEqual(routes.count(A),10);self.assertEqual(routes.count(B),10);self.assertEqual(request.call_count,2)
        self.assertNotIn('fake-flash-key',str(client.status()));self.assertNotIn('plan-a',str(client.status()))

    def test_expiring_one_session_preserves_other_cached_plan(self):
        self.pair();client=fp.FlashProxyClient()
        with patch.object(fp.requests,'request',side_effect=[response({'proxies':[A]}),response({'proxies':[B]}),response({'proxies':[A]})]) as request:
            with patch.object(fp.time,'monotonic',return_value=100):self.assertEqual(client.route().url,A)
            with patch.object(fp.time,'monotonic',return_value=200):self.assertEqual(client.route().url,B)
            with patch.object(fp.time,'monotonic',return_value=700):
                self.assertEqual(client.route().url,A);self.assertEqual(client.route().url,B)
            self.assertEqual(request.call_count,3)

    def test_exhausted_plan_falls_back_and_only_retries_after_its_cooldown(self):
        self.pair();client=fp.FlashProxyClient()
        with patch.object(fp.requests,'request',side_effect=[response({},status=402),response({'proxies':[B]}),response({'proxies':[A]})]) as request:
            with patch.object(fp.time,'monotonic',return_value=100):
                self.assertEqual(client.route().url,B)
                for _ in range(6):self.assertEqual(client.route().url,B)
                self.assertEqual(request.call_count,2)
            with patch.object(fp.time,'monotonic',return_value=401):
                routes=[client.route().url,client.route().url]
                self.assertIn(A,routes);self.assertIn(B,routes)
                self.assertEqual(request.call_count,3)

    def test_both_failed_plans_fail_closed_without_repeated_requests(self):
        self.pair();client=fp.FlashProxyClient()
        with patch.object(fp.requests,'request',return_value=response({'sensitive':A},status=503)) as request:
            for _ in range(3):
                with self.assertRaises(fp.FlashProxyUnavailable) as error:client.route()
                self.assertNotIn('private-pass',str(error.exception))
            self.assertEqual(request.call_count,2)

    def test_authentication_failure_stops_all_plan_generation(self):
        self.pair();client=fp.FlashProxyClient()
        with patch.object(fp.requests,'request',return_value=response({},status=401)) as request:
            for _ in range(2):
                with self.assertRaises(fp.FlashProxyUnavailable):client.route()
            request.assert_called_once()

    def test_global_rate_limit_still_allows_existing_cached_route(self):
        self.pair();client=fp.FlashProxyClient();limited=response({},status=429);limited.headers={'Retry-After':'180'}
        with patch.object(fp.time,'monotonic',return_value=100),patch.object(fp.requests,'request',side_effect=[response({'proxies':[A]}),limited]) as request:
            self.assertEqual(client.route().url,A)
            self.assertEqual(client.route().url,A)
            self.assertEqual(client.route().url,A)
            self.assertEqual(request.call_count,2);self.assertGreaterEqual(client._retry_at,280)

    def test_changing_only_second_plan_invalidates_both_old_sessions(self):
        self.pair();client=fp.FlashProxyClient()
        def generate(method,url,**kwargs):return response({'proxies':[A if '/plan-a/' in url else B]})
        with patch.object(fp.requests,'request',side_effect=generate) as request:
            client.route();client.route();before=fp.configuration_id()
            credentials.save_flashproxy_plans(['plan-a','plan-c'])
            self.assertNotEqual(fp.configuration_id(),before)
            client.route();client.route();self.assertEqual(request.call_count,4)
            self.assertTrue(request.call_args.args[1].endswith('/plans/plan-c/proxies'))

    def quota(self,plans):
        record=self.pair()
        with patch.object(credentials.requests,'get',return_value=response({'plans':plans})):
            return credentials.refresh_quota('flashproxy',record['id'])

    def test_quotas_are_separate_and_sum_only_verified_values(self):
        plans=[{'plan_id':identifier,'usage':{'remaining_bytes':remaining,'bytes_used':1_000_000_000,'max_bytes':remaining+1_000_000_000}} for identifier,remaining in [('plan-a',2_000_000_000),('plan-b',4_000_000_000)]]
        data=self.quota(plans)
        self.assertEqual(data['status'],'ok');self.assertEqual(data['quota']['remaining'],6)
        self.assertEqual([item['quota']['remaining'] for item in data['plan_quotas']],[2,4])
        self.assertEqual([item['plan_id'] for item in data['plan_quotas']],['plan-a','plan-b'])

    def test_missing_plan_does_not_invent_aggregate_balance(self):
        data=self.quota([{'plan_id':'plan-a','usage':{'remaining_bytes':2_000_000_000,'max_bytes':3_000_000_000}}])
        self.assertEqual(data['status'],'error');self.assertIsNone(data['quota']['remaining'])
        self.assertEqual(data['plan_quotas'][0]['quota']['remaining'],2)
        self.assertEqual(data['plan_quotas'][1]['status'],'error')

    def test_unlimited_plan_does_not_turn_unknown_remaining_into_zero(self):
        data=self.quota([{'plan_id':'plan-a','usage':{'remaining_bytes':2_000_000_000}}, {'plan_id':'plan-b','usage':{'remaining_bytes':None,'bytes_used':1_000_000_000,'max_bytes':None}}])
        self.assertEqual(data['status'],'ok');self.assertIsNone(data['quota']['remaining'])
        self.assertIsNone(data['plan_quotas'][1]['quota']['remaining'])

    def test_quota_result_is_rejected_even_when_plans_change_back_to_same_ids(self):
        record=self.pair()
        def switch(*args):
            credentials.save_flashproxy_plans(['other-a','other-b']);credentials.save_flashproxy_plans(['plan-a','plan-b'])
            return 'ok','outdated',{'remaining':99}
        with patch.object(credentials,'_fetch_quota',side_effect=switch):
            with self.assertRaisesRegex(ValueError,'plan a changé'):credentials.refresh_quota('flashproxy',record['id'])
        key=next(p for p in credentials.list_providers()['providers'] if p['id']=='flashproxy')['credentials'][0]
        self.assertIsNone(key['quota']['remaining']);self.assertNotIn('plan_quotas',key)
