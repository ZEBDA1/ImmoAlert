from __future__ import annotations

import re
import unicodedata
import math
from urllib.parse import urlparse

from config import settings
from scraper.base import ScrapedListing
from utils.hash import hash_str
from utils.text import parse_price_eur, parse_rooms, parse_surface_m2

SOURCE_HOSTS = {"pap":"pap.fr", "entreparticuliers":"entreparticuliers.com", "paruvendu":"paruvendu.fr",
    "leboncoin":"leboncoin.fr", "seloger":"seloger.com", "logicimmo":"logic-immo.com",
    "rentola":"rentola.fr", "ouestfranceimmo":"ouestfrance-immo.com"}


def _clean(value: str | None, *, max_len: int | None = None) -> str | None:
    if not value:
        return None
    cleaned = " ".join(str(value).replace("\xa0", " ").split())
    if not cleaned:
        return None
    return cleaned[:max_len].rstrip() if max_len and len(cleaned) > max_len else cleaned


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


# The inference helpers delegate to ``utils.text`` so that the Firecrawl
# scrapers, the legacy HTML scrapers and the normalisation stage all agree on
# what "1 250 €", "1.250,50" or "29,5 m²" mean. Keeping a second, slightly
# different regex here is how the two pipelines used to drift apart.
def _infer_price(text: str) -> int | None:
    return parse_price_eur(text)


def _infer_surface(text: str) -> float | None:
    return parse_surface_m2(text)


def _infer_rooms(text: str) -> int | None:
    return parse_rooms(text)


def _valid_http_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def extract_postal_code(value: str | None) -> str | None:
    match = re.search(r"\b(\d{5})\b", value or "")
    return match.group(1) if match else None


def normalize_city_name(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\b\d{5}\b", " ", value)
    cleaned = re.sub(r"\([^)]*\)", " ", cleaned)
    cleaned = cleaned.replace("’", "'")
    cleaned = re.sub(r"\s*-\s*", "-", cleaned)
    cleaned = " ".join(cleaned.strip(" -_,.;:").split())
    if not cleaned:
        return None
    # Keep the first useful segment for strings like "Paris 11e - Bastille".
    cleaned = re.split(r"\s+-\s+|,|/", cleaned, maxsplit=1)[0].strip()
    return cleaned[:120] or None


def charges_included(text: str | None) -> bool | None:
    norm = _norm_text(text)
    if not norm:
        return None
    if re.search(r"\b(cc|charges comprises|charges incluses|avec charges|tout compris)\b", norm):
        return True
    if re.search(r"\bhors charges\b|\bhc\b", norm):
        return False
    return None


def listing_quality_flags(listing: ScrapedListing) -> list[str]:
    flags: list[str] = []
    if not _valid_http_url(listing.url):
        flags.append("invalid_url")
    if not listing.title or _norm_text(listing.title) in {"annonce", "annonce immobiliere", "en attente de photo", "voir l annonce", "photo non fournie"}:
        flags.append("weak_title")
    if listing.price is None:
        flags.append("missing_price")
    if listing.surface_m2 is None:
        flags.append("missing_surface")
    if listing.location is None:
        flags.append("missing_location")
    if listing.rooms is None:
        flags.append("missing_rooms")
    useful_fields = sum(
        1
        for value in (listing.url, listing.title, listing.price, listing.surface_m2, listing.location, listing.rooms)
        if value not in (None, "")
    )
    if useful_fields < settings.min_listing_quality_fields:
        flags.append("low_signal")
    return flags


def coherence_issues(listing: ScrapedListing) -> list[str]:
    """Reasons a known portal record cannot be published as a housing rental."""
    issues = []
    flags = listing_quality_flags(listing)
    if "invalid_url" in flags:
        issues.append("invalid_url")
    if listing.source not in SOURCE_HOSTS:
        return issues
    host = (urlparse(listing.url).hostname or "").lower()
    expected = SOURCE_HOSTS[listing.source]
    if host not in {expected, "www."+expected}:
        issues.append("source_host_mismatch")
    issues.extend(flag for flag in ("weak_title", "missing_price", "missing_location") if flag in flags)
    if listing.surface_m2 is None and listing.rooms is None:
        issues.append("missing_size_and_rooms")
    title, path = _norm_text(listing.title), urlparse(listing.url).path.lower()
    if re.search(r"/(?:vente|ventes|achat|achats)/", path) or re.search(r"\b(?:a vendre|viager|vente appartement|vente maison)\b", title):
        issues.append("not_a_rental")
    if re.search(r"/(?:local-commercial|bureaux?|commerce|entrepot|terrain|garage|parking|stationnement)(?:[-/]|$)", path) or re.search(r"^(?:(?:location|louer|a louer)\s+)?(?:parking|garage|bureau|local commercial|terrain|entrepot|commerce)\b", title):
        issues.append("non_residential")
    positive = title + " " + _norm_text(path)
    if not re.search(r"\b(?:appartement|appartements|maison|studio|studette|chambre|colocation|duplex|loft|villa|pavillon|logement|[tf][1-9])\b", positive):
        issues.append("unidentified_property_type")
    if listing.price is not None and (not isinstance(listing.price, (int,float)) or isinstance(listing.price,bool) or not math.isfinite(listing.price) or not 0 < listing.price <= 50000):
        issues.append("rent_price_to_review")
    if listing.surface_m2 is not None and (not isinstance(listing.surface_m2, (int,float)) or not math.isfinite(listing.surface_m2) or not 0 < listing.surface_m2 <= 5000):
        issues.append("surface_to_review")
    return list(dict.fromkeys(issues))


def normalize_listing(
    listing: ScrapedListing,
    *,
    fallback_city: str | None = None,
    fallback_postal: str | None = None,
) -> ScrapedListing | None:
    if not _valid_http_url(listing.url):
        return None

    title = _clean(listing.title, max_len=240)
    description = _clean(listing.description, max_len=60000)
    location = _clean(listing.location, max_len=220)

    haystack = " ".join(v for v in (title, description, location) if v)
    for value in (listing.price, listing.surface_m2, listing.rooms):
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0):
            return None
    # Portal parsers extract price from the scoped price field. Never turn a
    # deposit or a market statistic in the description into the monthly rent.
    price = listing.price if listing.source in SOURCE_HOSTS else listing.price or _infer_price(haystack)
    surface = listing.surface_m2 or _infer_surface(haystack)
    rooms = listing.rooms or _infer_rooms(title or "")
    price_per_m2 = None
    if price and surface and surface > 0:
        price_per_m2 = price / surface

    images = []
    for image in listing.images or []:
        if _valid_http_url(image) and image not in images:
            images.append(image)

    external_id = _clean(listing.external_id, max_len=128)
    if not external_id:
        external_id = hash_str(f"{listing.source}|{listing.url}|{title or ''}|{price or ''}")

    normalized = ScrapedListing(
        source=listing.source,
        external_id=external_id,
        url=listing.url,
        title=title or "Annonce immobilière",
        price=price,
        surface_m2=surface,
        price_per_m2=price_per_m2,
        location=location,
        rooms=rooms,
        description=description,
        images=images or None,
        published_at=listing.published_at,
        db_id=listing.db_id,
    )
    if "low_signal" in listing_quality_flags(normalized) or coherence_issues(normalized):
        return None
    return normalized


def quality_signature(listing: ScrapedListing) -> str:
    title = _norm_text(_clean(listing.title) or "")
    location = _norm_text(normalize_city_name(listing.location) or _clean(listing.location) or "")
    postal = extract_postal_code(listing.location) or ""
    price = str(listing.price or "")
    surface = f"{listing.surface_m2:.1f}" if listing.surface_m2 else ""
    rooms = str(listing.rooms or "")
    description = _norm_text(listing.description)
    # Generic "Appartement T2 / Rouen / 42m² / 800€" is not proof that two
    # distinct advertisements concern the same home. Only a substantial common
    # description permits a cross-portal signature; otherwise use the URL.
    identity = description[:700] if len(description) >= 120 else listing.url.split("?")[0].split("#")[0].rstrip("/")
    return hash_str("|".join([title, location, postal, price, surface, rooms, identity]))
