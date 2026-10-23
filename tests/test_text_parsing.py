from __future__ import annotations

import os
import unittest

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("ENABLE_GEOCODING", "false")
os.environ.setdefault("AI_SCAM_ENABLED", "false")

from utils.text import (
    is_monthly_rent_text,
    parse_float,
    parse_int,
    parse_price_eur,
    parse_rooms,
    parse_surface_m2,
)


class ParseIntTests(unittest.TestCase):
    def test_plain(self) -> None:
        self.assertEqual(parse_int("1250"), 1250)

    def test_space_grouped_thousands(self) -> None:
        # A plain ASCII space is a thousands separator in French listings and
        # must not be stripped before matching.
        self.assertEqual(parse_int("1 250"), 1250)
        self.assertEqual(parse_int("1 250 000"), 1_250_000)

    def test_nbsp_grouped_thousands(self) -> None:
        self.assertEqual(parse_int("1\u00a0250\u00a0000"), 1_250_000)

    def test_dotted_thousands(self) -> None:
        self.assertEqual(parse_int("1.250.000"), 1_250_000)

    def test_decimal_is_truncated(self) -> None:
        self.assertEqual(parse_int("1.250,99"), 1250)

    def test_none_and_garbage(self) -> None:
        self.assertIsNone(parse_int(None))
        self.assertIsNone(parse_int(""))
        self.assertIsNone(parse_int("aucun prix"))


class ParseFloatTests(unittest.TestCase):
    def test_comma_decimal(self) -> None:
        self.assertEqual(parse_float("29,5"), 29.5)

    def test_dot_decimal(self) -> None:
        self.assertEqual(parse_float("29.5"), 29.5)

    def test_space_thousands_with_decimal(self) -> None:
        self.assertEqual(parse_float("1 196,5"), 1196.5)

    def test_dotted_thousands_with_comma_decimal(self) -> None:
        self.assertEqual(parse_float("1.196,5"), 1196.5)


class ParsePriceTests(unittest.TestCase):
    def test_simple_rent(self) -> None:
        self.assertEqual(parse_price_eur("1 250 €"), 1250)

    def test_no_space_before_symbol(self) -> None:
        self.assertEqual(parse_price_eur("1250EUR"), 1250)

    def test_euros_word(self) -> None:
        self.assertEqual(parse_price_eur("890 euros par mois"), 890)

    def test_does_not_merge_room_label_into_price(self) -> None:
        # "T2 950 €" is a T2 at 950 €, never 2950 €.
        self.assertEqual(parse_price_eur("T2 950 €"), 950)

    def test_does_not_start_mid_number(self) -> None:
        # Must not answer 250 out of "1.250,50".
        self.assertEqual(parse_price_eur("Loyer 1.250,50 EUR"), 1250)

    def test_repeated_dot_thousands(self) -> None:
        self.assertEqual(parse_price_eur("Prix: 1.250.000 EUR"), 1_250_000)

    def test_monthly_only_skips_deposit(self) -> None:
        text = "Dépôt de garantie 2400 € loyer 800 €/mois"
        self.assertEqual(parse_price_eur(text), 2400)
        self.assertEqual(parse_price_eur(text, monthly_only=True), 800)

    def test_no_price(self) -> None:
        self.assertIsNone(parse_price_eur("beau studio lumineux"))
        self.assertIsNone(parse_price_eur(None))


class ParseSurfaceTests(unittest.TestCase):
    def test_m2_variants(self) -> None:
        self.assertEqual(parse_surface_m2("29m²"), 29.0)
        self.assertEqual(parse_surface_m2("29 m2"), 29.0)
        self.assertEqual(parse_surface_m2("29,5 m²"), 29.5)

    def test_space_thousands(self) -> None:
        self.assertEqual(parse_surface_m2("1 196 m² de bureaux"), 1196.0)

    def test_does_not_merge_rooms_into_surface(self) -> None:
        # "2 pièces 1 196 m²": the surface is 1196, not 21196.
        self.assertEqual(parse_surface_m2("2 pièces 1 196 m²"), 1196.0)

    def test_rejects_absurd(self) -> None:
        self.assertIsNone(parse_surface_m2("9999 m²"))

    def test_no_surface(self) -> None:
        self.assertIsNone(parse_surface_m2("1 250 €/mois"))


class ParseRoomsTests(unittest.TestCase):
    def test_t_prefix(self) -> None:
        self.assertEqual(parse_rooms("T2"), 2)
        self.assertEqual(parse_rooms("appartement T3 lumineux"), 3)

    def test_pieces(self) -> None:
        self.assertEqual(parse_rooms("4 pièces"), 4)
        self.assertEqual(parse_rooms("3 pieces"), 3)

    def test_studio(self) -> None:
        self.assertEqual(parse_rooms("studio meublé"), 1)

    def test_chambres(self) -> None:
        self.assertEqual(parse_rooms("2 chambres"), 2)

    def test_no_rooms(self) -> None:
        self.assertIsNone(parse_rooms("1 250 €/mois"))
        self.assertIsNone(parse_rooms(None))


class MonthlyRentTests(unittest.TestCase):
    def test_detects_monthly(self) -> None:
        self.assertTrue(is_monthly_rent_text("1 250 €/mois"))
        self.assertTrue(is_monthly_rent_text("loyer mensuel 800 €"))

    def test_rejects_non_monthly(self) -> None:
        self.assertFalse(is_monthly_rent_text("prix de vente 250 000 €"))


class QualityInferenceTests(unittest.TestCase):
    """The normalisation stage must agree with the shared parsers."""

    def test_infer_helpers_delegate(self) -> None:
        from scraper.quality import _infer_price, _infer_rooms, _infer_surface

        haystack = "Appartement 3 pièces 1 250 €/mois 29,5 m²"
        self.assertEqual(_infer_price(haystack), 1250)
        self.assertEqual(_infer_surface(haystack), 29.5)
        self.assertEqual(_infer_rooms(haystack), 3)

    def test_studio_is_one_room(self) -> None:
        from scraper.quality import _infer_rooms

        self.assertEqual(_infer_rooms("Studio meublé 890 euros par mois"), 1)


if __name__ == "__main__":
    unittest.main()
