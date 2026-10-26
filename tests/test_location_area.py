from __future__ import annotations
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import os
os.environ['BOT_TOKEN']='test-token';os.environ['DATABASE_URL']='sqlite://';os.environ['ENABLE_GEOCODING']='false'
from services import location_area as area

POLYGON={'type':'Polygon','coordinates':[[[1.08,49.43],[1.10,49.43],[1.10,49.45],[1.08,49.43]]]}

def listing(**changes):
    values=dict(latitude=49.44,longitude=1.09,geo_precision='city',normalized_city='Rouen',normalized_postal_code='76000',location='Rouen 76000');values.update(changes);return SimpleNamespace(**values)

class LocationAreaTests(unittest.TestCase):
    def setUp(self):area._cache.clear()

    def test_portal_area_is_indicative_without_an_exact_address_marker(self):
        with patch.object(area,'public_json') as fetch,patch.object(area,'resolve_commune') as commune:
            result=area._cached_area(listing(geo_precision='portal'))
        self.assertEqual(result['radius_m'],500);self.assertEqual(result['kind'],'indicative')
        self.assertIn('indicatif',result['detail']);fetch.assert_not_called();commune.assert_not_called()

    def test_commune_outline_is_cached_and_uses_the_verified_insee_code(self):
        with patch.object(area,'resolve_commune',return_value={'nom':'Rouen','code':'76540'}),patch.object(area,'public_json',return_value={'type':'Feature','geometry':POLYGON}) as fetch:
            first=area._cached_area(listing());second=area._cached_area(listing())
        self.assertEqual(first,second);self.assertEqual(first['kind'],'commune');self.assertEqual(first['bounds'],[1.08,49.43,1.10,49.45])
        self.assertIn('/76540',fetch.call_args.args[0]);self.assertEqual(fetch.call_count,1)

    def test_failed_outline_is_an_explicit_indicative_circle(self):
        with patch.object(area,'resolve_commune',return_value={'nom':'Rouen','code':'76540'}),patch.object(area,'public_json',return_value=None):result=area._cached_area(listing())
        self.assertEqual(result['kind'],'indicative');self.assertEqual(result['radius_m'],3000)
        self.assertIn('ni les limites',result['detail'])

    def test_postcode_without_city_does_not_guess_the_largest_commune(self):
        with patch.object(area,'resolve_commune') as resolve:result=area._cached_area(listing(location='76000',normalized_city=None,latitude=None,longitude=None))
        resolve.assert_not_called();self.assertEqual(result['kind'],'unavailable')

    def test_invalid_coordinates_and_geometries_are_rejected(self):
        for geometry in [{'type':'Point','coordinates':[1,2]},{'type':'Polygon','coordinates':[[[1,2],[3,4],[5,6]]]}, {'type':'Polygon','coordinates':[[[1,2],[1,float('nan')],[3,4],[1,2]]]}, {'type':'Polygon','coordinates':[[[True,2],[1,2],[3,4],[True,2]]]}]:
            with self.subTest(geometry=geometry):self.assertIsNone(area.validate_geometry(geometry))
        self.assertEqual(area.validate_geometry(POLYGON),POLYGON)
        with patch.object(area,'resolve_commune',return_value=None):self.assertEqual(area._cached_area(listing(latitude=999))['kind'],'unavailable')

class SharedAreaTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_one_reader_does_not_cancel_the_shared_geographic_work(self):
        area._tasks.clear();area._cache.clear()
        import threading
        entered,release=threading.Event(),threading.Event()
        def load(row):entered.set();release.wait(3);return {'kind':'indicative'}
        with patch.object(area,'_cached_area',side_effect=load) as load_mock,patch.object(area,'_slots',asyncio.Semaphore(2)):
            first=asyncio.create_task(area.for_listing(listing()));await asyncio.to_thread(entered.wait,1)
            second=asyncio.create_task(area.for_listing(listing()));await asyncio.sleep(0)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):await first
            release.set();self.assertEqual((await second)['kind'],'indicative');self.assertEqual(load_mock.call_count,1)


    async def test_cached_area_does_not_wait_behind_slow_external_requests(self):
        row=listing();cached={'kind':'commune','geometry':POLYGON}
        area._cache[area.cache_key(row)]=(area.monotonic()+60,cached)
        with patch.object(area,'_slots',asyncio.Semaphore(0)):
            self.assertEqual(await asyncio.wait_for(area.for_listing(row),.1),cached)
        area._cache.clear()

    async def test_a_full_queue_returns_an_explicit_fallback_without_more_work(self):
        area._cache.clear()
        with patch.dict(area._tasks,{('queued',index):object() for index in range(64)},clear=True),patch.object(area,'_cached_area') as fetch:
            result=await area.for_listing(listing())
        self.assertEqual(result['kind'],'indicative');self.assertIn('ni les limites',result['detail']);fetch.assert_not_called()
