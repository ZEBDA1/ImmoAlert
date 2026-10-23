from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

from config import settings
from scraper.base import ScrapedListing
from utils.hash import hash_str
from utils.text import parse_price_eur, parse_rooms, parse_surface_m2


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
    if not listing.title or _norm_text(listing.title) in {"annonce", "annonce immobiliere"}:
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


def normalize_listing(
    listing: ScrapedListing,
    *,
    fallback_city: str | None = None,
    fallback_postal: str | None = None,
) -> ScrapedListing | None:
    if not _valid_http_url(listing.url):
        return None

    title = _clean(listing.title, max_len=240)
    description = _clean(listing.description, max_len=1200)
    location = _clean(listing.location, max_len=220)

    haystack = " ".join(v for v in (title, description, location) if v)
    price = listing.price if listing.price and listing.price > 0 else _infer_price(haystack)
    surface = listing.surface_m2 if listing.surface_m2 and listing.surface_m2 > 0 else _infer_surface(haystack)
    rooms = listing.rooms if listing.rooms and listing.rooms > 0 else _infer_rooms(haystack)
    price_per_m2 = listing.price_per_m2
    if not price_per_m2 and price and surface and surface > 0:
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
    if "low_signal" in listing_quality_flags(normalized):
        return None
    return normalized


def quality_signature(listing: ScrapedListing) -> str:
    title = _norm_text(_clean(listing.title) or "")
    location = _norm_text(normalize_city_name(listing.location) or _clean(listing.location) or "")
    postal = extract_postal_code(listing.location) or ""
    price = str(listing.price or "")
    surface = f"{listing.surface_m2:.1f}" if listing.surface_m2 else ""
    rooms = str(listing.rooms or "")
    return hash_str("|".join([title, location, postal, price, surface, rooms]))
