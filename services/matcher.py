from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Optional

from database import models as m
from scraper.base import ScrapedListing
from utils.geo import geocode, distance_km

#: Canonical property types understood by the bot.
PROPERTY_TYPES = ("studio", "appartement", "maison")

_POSTAL_RE = re.compile(r"\b\d{5}\b")

#: Whole-word typed hints. ``T4``/``F4`` are appartements, ``T1``/``F1`` are
#: studios. ``\b`` plus a capture group keeps ``T25`` or ``T2A`` out.
_TYPE_ROOM_RE = re.compile(r"\b([tf])([1-9])\b", re.IGNORECASE)
_STUDIO_RE = re.compile(r"\b(studio|studette|chambre de bonne)\b", re.IGNORECASE)
_APARTMENT_RE = re.compile(
    r"\b(appartement|appartements|appart|duplex|loft|souplex)\b", re.IGNORECASE
)
_HOUSE_RE = re.compile(
    r"\b(maison|villa|pavillon|chalet|longere|corps de ferme)\b", re.IGNORECASE
)
#: Rooms that describe a *house-mate* listing rather than a house.
_HOUSE_FALSE_FRIENDS = re.compile(r"\bmaison\s+(?:d.hotes|medicale|de\s+retraite)\b", re.IGNORECASE)


@dataclass
class MatchResult:
    matched: bool
    score_label: str = ""
    score_value: float = 0.0
    reasons: list[str] | None = None
    reject_reason: str | None = None


def _strip_accents(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(c)
    )


def _norm_text(value: str | None) -> str:
    if not value:
        return ""
    value = _strip_accents(value).lower().replace("\xa0", " ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _contains_term(haystack: str | None, needle: str | None) -> bool:
    h = _norm_text(haystack)
    n = _norm_text(needle)
    if not h or not n:
        return False
    return f" {n} " in f" {h} "


def _listing_evidence(l: ScrapedListing) -> str:
    return " ".join(v for v in (l.location, l.title, l.url, l.description) if v)


def _text_postal_codes(value: str | None) -> set[str]:
    return set(_POSTAL_RE.findall(value or ""))


def _paris_arr_postal_from_text(value: str | None) -> set[str]:
    out: set[str] = set()
    norm = _norm_text(value)
    for m in re.finditer(r"\bparis\s*(?:0?)([1-9]|1\d|20)(?:e|eme|er)?\b", norm):
        out.add(f"750{int(m.group(1)):02d}")
    return out


def _geocode_query(city: str | None, postal_code: str | None) -> str | None:
    parts = [p for p in (city, postal_code, "France") if p]
    return ", ".join(parts) if parts else None


def _location_match(f: m.Filter, l: ScrapedListing) -> tuple[bool, str]:
    city = (f.city or "").strip() or None
    postal_code = (f.postal_code or "").strip() or None
    radius_km = f.radius_km if f.radius_km and f.radius_km > 0 else None

    if not city and not postal_code:
        return True, "no_location_filter"

    evidence = _listing_evidence(l)
    if not evidence:
        return False, "missing_location_evidence"

    city_match = _contains_term(evidence, city) if city else False
    listing_postals = _text_postal_codes(evidence) | _paris_arr_postal_from_text(evidence)
    postal_match = postal_code in listing_postals if postal_code else False

    if postal_code and postal_match:
        return True, f"postal_code:{postal_code}"
    if city and city_match and (not postal_code or not listing_postals):
        return True, f"city:{city}"
    if city and city_match and postal_code in listing_postals:
        return True, f"city_postal:{city}:{postal_code}"

    if not radius_km:
        if postal_code and listing_postals:
            return False, f"postal_mismatch:{postal_code}"
        return False, f"city_mismatch:{city or postal_code}"

    center_query = _geocode_query(city, postal_code)
    listing_query = l.location or evidence
    if not center_query or not listing_query:
        return False, "missing_geocode_query"
    center = geocode(center_query)
    listing_point = geocode(f"{listing_query}, France")
    if not center or not listing_point:
        return False, "geocode_unavailable"
    distance = distance_km(center, listing_point)
    if distance <= radius_km:
        return True, f"radius:{distance:.1f}km<={radius_km:g}km"
    return False, f"radius_mismatch:{distance:.1f}km>{radius_km:g}km"


def _matches_location(f: m.Filter, l: ScrapedListing) -> bool:
    return _location_match(f, l)[0]


def _infer_property_type(l: ScrapedListing) -> str | None:
    """Guess ``studio``/``appartement``/``maison`` from title, rooms and text.

    Structured data wins over text: an explicit ``rooms`` count is more
    reliable than a mention buried in a free-form description.
    """
    title = l.title or ""
    description = l.description or ""
    text = f"{title} {description}"
    if not text.strip() and l.rooms is None:
        return None

    # 1. Explicit synonyms in the title (most reliable free text).
    if _STUDIO_RE.search(title):
        return "studio"
    if _HOUSE_FALSE_FRIENDS.search(title):
        return None
    if _HOUSE_RE.search(title):
        return "maison"
    if _APARTMENT_RE.search(title):
        return "appartement"
    if (match := _TYPE_ROOM_RE.search(title)) is not None:
        return "studio" if match.group(2) == "1" else "appartement"

    # 2. Same scan over the description.
    if _STUDIO_RE.search(description):
        return "studio"
    if not _HOUSE_FALSE_FRIENDS.search(description) and _HOUSE_RE.search(description):
        return "maison"
    if _APARTMENT_RE.search(description):
        return "appartement"

    # 3. Fall back on the structured room count.
    if l.rooms is not None:
        return "studio" if l.rooms <= 1 else "appartement"
    return None


def _score_against_filter(f: m.Filter, l: ScrapedListing) -> tuple[str, float, list[str]]:
    """Score a listing *relative to the user's own filter*, not absolute prices.

    ``price_per_m2`` on a rental is a **monthly rent per m²**, so fixed
    thresholds (18 €/m², 30 €/m²) are meaningless. Everything here is derived
    from the budget the user actually gave.
    """
    reasons: list[str] = []
    shortfalls: list[str] = []
    overruns: list[str] = []

    # -- rent vs budget ---------------------------------------------------
    budget = f.price_max or f.budget_max_with_charges
    if budget and l.price:
        ratio = l.price / budget
        reasons.append(f"price_vs_budget:{ratio:.2f}")
        if ratio <= 0.75:
            shortfalls.append("well_under_budget")
        elif ratio <= 0.9:
            shortfalls.append("under_budget")
        elif ratio > 1.0:
            overruns.append("over_budget")

    # -- surface vs minimum ----------------------------------------------
    if f.surface_min and l.surface_m2:
        ratio = l.surface_m2 / f.surface_min
        reasons.append(f"surface_vs_min:{ratio:.2f}")
        if ratio >= 1.5:
            shortfalls.append("much_larger_than_asked")
        elif ratio >= 1.15:
            shortfalls.append("larger_than_asked")

    # -- rooms vs minimum -------------------------------------------------
    if f.rooms_min and l.rooms is not None:
        extra = l.rooms - f.rooms_min
        if extra >= 1:
            shortfalls.append("more_rooms_than_asked")

    if len(shortfalls) >= 2:
        return "Tres bonne affaire", 0.9, reasons
    if shortfalls:
        return "Bon prix", 0.7, reasons
    if overruns:
        return "Cher", 0.3, reasons
    return "Bon prix", 0.5, reasons


def match_and_score(f: m.Filter, l: ScrapedListing) -> MatchResult:
    reasons: list[str] = []
    if f.price_min is not None and (l.price is None or l.price < f.price_min):
        return MatchResult(matched=False, reject_reason="price_below_min")
    if f.price_min is not None:
        reasons.append(f"price>=min:{f.price_min}")
    if f.price_max is not None and (l.price is None or l.price > f.price_max):
        return MatchResult(matched=False, reject_reason="price_above_max")
    if f.price_max is not None:
        reasons.append(f"price<=max:{f.price_max}")
    if f.surface_min is not None and (l.surface_m2 is None or l.surface_m2 < f.surface_min):
        return MatchResult(matched=False, reject_reason="surface_below_min")
    if f.surface_min is not None:
        reasons.append(f"surface>=min:{f.surface_min:g}")
    if f.rooms_min is not None and (l.rooms is None or l.rooms < f.rooms_min):
        return MatchResult(matched=False, reject_reason="rooms_below_min")
    if f.rooms_min is not None:
        reasons.append(f"rooms>=min:{f.rooms_min}")
    if f.budget_max_with_charges is not None and (l.price is None or l.price > f.budget_max_with_charges):
        return MatchResult(matched=False, reject_reason="budget_with_charges_above_max")
    if f.budget_max_with_charges is not None:
        reasons.append(f"budget_charges<=max:{f.budget_max_with_charges}")
    if f.property_type:
        wanted = f.property_type.strip().lower()
        listing_type = _infer_property_type(l)
        if listing_type and listing_type != wanted:
            return MatchResult(matched=False, reject_reason="property_type_mismatch")
        if listing_type:
            reasons.append(f"type:{listing_type}")
        else:
            # Unknown type is not a rejection: many listings omit it entirely.
            reasons.append("type:unknown")

    location_ok, location_reason = _location_match(f, l)
    if not location_ok:
        return MatchResult(matched=False, reject_reason=location_reason)
    reasons.append(location_reason)

    score_label, score_value, score_reasons = _score_against_filter(f, l)
    reasons.extend(score_reasons)

    # An absurd rent-per-m² is a strong scam signal: surface it in the score
    # instead of pretending the listing is a bargain.
    if l.price_per_m2 is not None and l.price_per_m2 < 4:
        reasons.append(f"suspicious_rent_per_m2:{l.price_per_m2:.1f}")

    return MatchResult(matched=True, score_label=score_label, score_value=score_value, reasons=reasons)
