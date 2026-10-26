"""Commune lookup must distinguish bad search input from a service outage."""
import unittest
from unittest.mock import Mock, patch

import requests

from scraper.ouestfranceimmo import OuestFranceImmoScraper
from scraper.paruvendu import ParuVenduScraper
from utils import locations
from utils.cache import TTLCache
from utils.fetch_diagnostics import capture_problems


BRY = {"nom": "Bry-sur-Marne", "code": "94015", "codesPostaux": ["94360"], "population": 17524}


class CommuneResolutionTests(unittest.TestCase):
    def setUp(self):
        cache = patch.object(locations, "_CACHE", TTLCache(86400))
        cache.start()
        self.addCleanup(cache.stop)

    def test_correct_postal_is_resolved_to_portal_insee_routes_and_shared_cache(self):
        with patch.object(locations, "public_json", return_value=[BRY]) as request:
            url = ParuVenduScraper()._search_url("Bry sur Marne", "94360")
            urls = OuestFranceImmoScraper()._candidate_urls("Bry-sur-Marne", "94360")
        self.assertIn("codeINSEE=94015", url)
        self.assertIn("lo=94360", url)
        self.assertEqual(len(urls), 2)
        self.assertTrue(all("bry-sur-marne-94-94015" in url for url in urls))
        request.assert_called_once()
        self.assertEqual(request.call_args.args[1]["codePostal"], "94360")

    def test_insee_entered_as_postal_is_explained_without_contacting_portals(self):
        for scraper, module in ((ParuVenduScraper(), "scraper.paruvendu"), (OuestFranceImmoScraper(), "scraper.ouestfranceimmo")):
            problems, events = [], []
            with self.subTest(source=scraper.source), patch.object(locations, "public_json", return_value=[]), patch(module + ".fetch_html") as fetch, capture_problems(problems, events):
                self.assertEqual(list(scraper.fetch_city("Bry sur Marne", "94015")), [])
                fetch.assert_not_called()
            self.assertEqual(len(problems), 1)
            self.assertIn("94015", problems[0])
            self.assertIn("code INSEE", problems[0])
            self.assertEqual(events[0]["code"], "commune_not_resolved")
            self.assertEqual(events[0]["stage"], "localisation")

    def test_conflicting_city_and_postal_never_selects_an_unrelated_commune(self):
        with patch.object(locations, "public_json", return_value=[BRY]):
            self.assertIsNone(ParuVenduScraper()._resolve_insee("Rouen", "94360"))
            self.assertEqual(OuestFranceImmoScraper()._candidate_urls("Rouen", "94360"), [])

    def test_city_only_does_not_accept_the_first_fuzzy_result(self):
        with patch.object(locations, "public_json", return_value=[BRY]):
            self.assertIsNone(ParuVenduScraper()._resolve_insee("Bry", None))
        with patch.object(locations, "public_json", return_value=[{"nom": "Saint-Étienne", "code": "42218"}]):
            self.assertEqual(locations.resolve_commune("Saint Etienne", None)["code"], "42218")

    def _session(self, response=None, exception=None):
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=None)
        if exception:
            session.get.side_effect = exception
        else:
            session.get.return_value = response
        return session

    def test_official_http_failures_do_not_use_portal_proxy_and_are_not_bad_input(self):
        for status in (429, 503):
            session = self._session(Mock(status_code=status))
            problems, events = [], []
            with self.subTest(status=status), patch.object(locations.requests, "Session", return_value=session), patch.object(locations, "fetch_json_with_scrapling") as proxy, patch.object(locations.http_client, "get") as fallback, capture_problems(problems, events):
                self.assertIsNone(locations.resolve_commune("Bry sur Marne", "94360"))
            self.assertFalse(session.trust_env)
            proxy.assert_not_called()
            fallback.assert_not_called()
            self.assertTrue(any(e["code"] == "geolocation_unavailable" and e["http_status"] == status for e in events))
            self.assertFalse(any(e["code"] == "commune_not_resolved" for e in events))
            self.assertEqual(len(problems), 1)

    def test_official_timeout_and_invalid_json_are_service_errors_without_proxy(self):
        cases = [self._session(exception=requests.Timeout("official API timeout")), self._session(Mock(status_code=200, json=Mock(side_effect=ValueError("invalid JSON"))))]
        for session in cases:
            problems, events = [], []
            with patch.object(locations.requests, "Session", return_value=session), patch.object(locations, "fetch_json_with_scrapling") as proxy, patch.object(locations.http_client, "get") as fallback, capture_problems(problems, events):
                self.assertIsNone(locations.resolve_commune("Bry sur Marne", "94360"))
            proxy.assert_not_called()
            fallback.assert_not_called()
            self.assertEqual(len(problems), 1)
            self.assertTrue(any(e["code"] == "geolocation_unavailable" and e.get("exception_type") for e in events))
            self.assertFalse(any(e["code"] == "commune_not_resolved" for e in events))

    def test_wrong_json_shape_does_not_look_like_a_missing_commune(self):
        problems, events = [], []
        with patch.object(locations, "public_json", return_value={"error": "unexpected"}), capture_problems(problems, events):
            self.assertIsNone(locations.resolve_commune("Bry sur Marne", "94360"))
        self.assertEqual(events[0]["code"], "geolocation_unavailable")
        self.assertNotIn("commune_not_resolved", problems[0])

    def test_failed_lookup_does_not_poison_later_success(self):
        with patch.object(locations, "public_json", side_effect=[None, [BRY]]) as request:
            self.assertIsNone(locations.resolve_commune("Bry sur Marne", "94360"))
            self.assertEqual(locations.resolve_commune("Bry sur Marne", "94360")["code"], "94015")
        self.assertEqual(request.call_count, 2)
