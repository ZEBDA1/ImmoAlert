"""Tests for property-type inference, filter-relative scoring and scam rules.

These lock in the behaviours that were previously wrong:

* ``T2``/``F2`` were parsed as *studios* because the old synonym table did a
  substring match.
* The matcher scored rentals with fixed "€/m² of a sale" thresholds (18/30),
  which flagged every Parisian rental as expensive.
* ``detect_scam`` rejected any listing under 200 € and ignored explicit rent
  amounts, so a deposit could be mistaken for the rent.
"""

from __future__ import annotations

import unittest

from database import models as m
from scraper.base import ScrapedListing
from services.matcher import _infer_property_type, match_and_score
from services.scam import detect_scam


def _listing(**kwargs) -> ScrapedListing:
    params = {
        "source": "leboncoin",
        "external_id": "1",
        "url": "https://www.leboncoin.fr/ad/locations/1",
    }
    params.update(kwargs)
    return ScrapedListing(**params)


def _filter(**kwargs) -> m.Filter:
    """Build a transient Filter (never persisted) with sensible defaults."""
    flt = m.Filter(user_id=1, name="test", active=True)
    for key, value in kwargs.items():
        setattr(flt, key, value)
    return flt


class PropertyTypeInferenceTests(unittest.TestCase):
    def test_studio_tokens(self):
        for title in ("Studio 24m2 refait a neuf", "T1 bis lumineux", "F1 proche gare"):
            with self.subTest(title=title):
                self.assertEqual(_infer_property_type(_listing(title=title)), "studio")

    def test_apartment_tokens(self):
        for title in ("T2 45m2 proche gare", "T4 duplex", "Appartement 3 pieces", "Loft" ):
            with self.subTest(title=title):
                self.assertEqual(_infer_property_type(_listing(title=title)), "appartement")

    def test_house_tokens(self):
        for title in ("Maison de ville 5 pieces", "Villa avec jardin", "Pavillon"):
            with self.subTest(title=title):
                self.assertEqual(_infer_property_type(_listing(title=title)), "maison")

    def test_houses_of_others_are_not_houses(self):
        for title in ("Maison d'hotes a louer", "Maison medicale a louer"):
            with self.subTest(title=title):
                self.assertNotEqual(_infer_property_type(_listing(title=title)), "maison")

    def test_room_count_is_a_fallback_only(self):
        self.assertEqual(_infer_property_type(_listing(title="Beau logement", rooms=1)), "studio")
        self.assertEqual(_infer_property_type(_listing(title="Beau logement", rooms=3)), "appartement")

    def test_no_signal_returns_none(self):
        self.assertIsNone(_infer_property_type(_listing()))
        self.assertIsNone(_infer_property_type(_listing(title="", description="")))


class MatchScoringTests(unittest.TestCase):
    def test_cheap_against_budget_is_a_good_deal(self):
        flt = _filter(city="Paris", price_max=1200)
        res = match_and_score(flt, _listing(title="T2 Paris", price=800, location="Paris 11e"))
        self.assertTrue(res.matched)
        self.assertEqual(res.score_label, "Bon prix")

    def test_over_budget_is_rejected(self):
        flt = _filter(city="Paris", price_max=1000)
        res = match_and_score(flt, _listing(title="T2", price=1500, location="Paris"))
        self.assertFalse(res.matched)
        self.assertEqual(res.reject_reason, "price_above_max")

    def test_unknown_property_type_is_not_a_rejection(self):
        flt = _filter(city="Lyon", property_type="appartement")
        res = match_and_score(flt, _listing(title="Beau logement", price=900, location="Lyon"))
        self.assertTrue(res.matched)
        self.assertIn("type:unknown", res.reasons or [])

    def test_wrong_property_type_is_rejected(self):
        flt = _filter(city="Lyon", property_type="maison")
        res = match_and_score(flt, _listing(title="T2 lumineux", price=900, location="Lyon"))
        self.assertFalse(res.matched)
        self.assertEqual(res.reject_reason, "property_type_mismatch")

    def test_paris_rent_is_not_punished_by_sale_price_thresholds(self):
        """22 €/m²/month is normal in Paris: must not be labelled "Cher"."""
        flt = _filter(city="Paris", price_max=1100)
        listing = _listing(
            title="T2 Paris 11e",
            price=950,
            surface_m2=42.0,
            price_per_m2=22.6,
            location="Paris 11e",
        )
        res = match_and_score(flt, listing)
        self.assertTrue(res.matched)
        self.assertNotEqual(res.score_label, "Cher")


class ScamDetectionTests(unittest.TestCase):
    def assert_scam(self, listing: ScrapedListing) -> None:
        self.assertTrue(detect_scam(listing).is_scam, detect_scam(listing).reason)

    def assert_clean(self, listing: ScrapedListing) -> None:
        result = detect_scam(listing)
        self.assertFalse(result.is_scam, result.reason)

    def test_normal_listings_pass(self):
        self.assert_clean(
            _listing(title="Studio 20m2", description="Refait a neuf, libre.", price=700, surface_m2=20.0)
        )
        self.assert_clean(
            _listing(
                title="Studio meuble",
                description="Loyer 650 euros par mois, charges comprises.",
                price=650,
                surface_m2=22.0,
            )
        )
        self.assert_clean(
            _listing(title="Loft 300 m2", price=2400, surface_m2=300.0)
        )

    def test_cheap_but_plausible_studio_passes(self):
        """The old rule rejected anything under 200 €; 380 € for 18 m² is fine."""
        self.assert_clean(_listing(title="Studio cosy", price=450, surface_m2=18.0))

    def test_room_share_is_not_a_scam(self):
        self.assert_clean(
            _listing(title="Chambre chez l habitant", description="9 m2", price=380, surface_m2=9.0)
        )

    def test_deposit_is_not_mistaken_for_rent(self):
        """1700 € deposit + 850 € rent: only the rent must be checked."""
        self.assert_clean(
            _listing(
                title="T2 45m2",
                description="Depot de garantie 1700 euros. Loyer 850 euros /mois.",
                price=1700,
                surface_m2=45.0,
            )
        )

    def test_strong_payment_keyword_is_decisive(self):
        self.assert_scam(
            _listing(title="T1", description="Virement international uniquement.", price=700, surface_m2=20.0)
        )

    def test_two_weak_keywords_corroborate(self):
        self.assert_scam(
            _listing(
                title="Appartement 40m2",
                description="Urgent partir a l etranger, je ne suis pas sur place.",
                price=800,
                surface_m2=40.0,
            )
        )

    def test_single_weak_keyword_alone_is_not_enough(self):
        self.assert_clean(
            _listing(title="T2", description="Merci de ne pas appeler avant 18h.", price=900, surface_m2=40.0)
        )

    def test_absurd_rent_per_m2(self):
        self.assert_scam(_listing(title="T1", price=45, surface_m2=18.0))

    def test_absurd_surface(self):
        self.assert_scam(_listing(title="Maison", price=1800, surface_m2=2.0))

    def test_zero_price(self):
        self.assert_scam(_listing(title="T2", price=0, surface_m2=40.0))

    def test_signals_are_exposed_for_debugging(self):
        result = detect_scam(
            _listing(title="T1", description="Western Union accepte.", price=700, surface_m2=20.0)
        )
        self.assertTrue(result.is_scam)
        self.assertTrue(result.signals)
        self.assertTrue(any("western union" in s for s in result.signals))


if __name__ == "__main__":
    unittest.main()
