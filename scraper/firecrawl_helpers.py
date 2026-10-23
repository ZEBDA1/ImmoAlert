"""Shared building blocks for the Firecrawl-backed scrapers.

Portals like Leboncoin and SeLoger render their result pages with JavaScript and
protect them with anti-bot layers, so CSS selectors break constantly. Firecrawl
returns the page as *structured JSON* driven by a schema, which is far more
stable. This module centralises:

* :data:`LISTING_SCHEMA` – the JSON-schema handed to ``firecrawl scrape``;
* URL builders for the two portals (SeLoger requires an ``adXXfrYY`` place id);
* defensive converters from loosely-typed Firecrawl payloads to
  :class:`~scraper.base.ScrapedListing`;
* parsers for the markdown/description fallbacks that Firecrawl also returns
  (``firecrawl search`` inlines the whole SERP inside ``description``).

Everything is defensive: a malformed card is skipped, never raised.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from urllib.parse import urlencode, urlparse

from scraper.base import ScrapedListing
from utils.hash import hash_str
from utils.text import (
    clean_text,
    parse_float,
    parse_int,
    parse_price_eur,
    parse_rooms,
    parse_surface_m2,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON schema
# ---------------------------------------------------------------------------
LISTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "listings": {
            "type": "array",
            "description": "Every rental listing card visible on the results page.",
            "items": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Absolute URL of the listing detail page.",
                    },
                    "title": {"type": "string"},
                    "price": {
                        "type": "number",
                        "description": "Monthly rent in euros, digits only (no currency symbol, no spaces).",
                    },
                    "surface_m2": {"type": "number"},
                    "rooms": {"type": "number"},
                    "location": {
                        "type": "string",
                        "description": "Neighbourhood, city and postal code as displayed.",
                    },
                    "description": {"type": "string"},
                    "image": {"type": "string"},
                    "published_text": {
                        "type": "string",
                        "description": "Relative publication date as displayed (e.g. 'il y a 2 heures').",
                    },
                },
                "required": ["url"],
            },
        }
    },
    "required": ["listings"],
}

#: A shorter prompt used when a schema cannot be supplied (kept for fallback).
LISTING_PROMPT = (
    "Extract every rental property listing card from this page. For each card return: "
    "url (absolute), title, price (monthly rent in euros as a number), surface_m2 (number), "
    "rooms (number), location (neighbourhood + city + postal code), description, image, "
    "and published_text (relative date as displayed)."
)


# ---------------------------------------------------------------------------
# Small coercion helpers
# ---------------------------------------------------------------------------
def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            text = _as_text(item)
            if text:
                return text
    if isinstance(value, dict):
        for key in ("text", "label", "name", "value", "url"):
            text = _as_text(value.get(key))
            if text:
                return text
    return None


_BARE_NUMBER_RE = re.compile(r"\d{1,3}(?:[.,]\d{1,2})?|\d+(?:[ .\u00a0]\d{3})*")


def _as_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _as_text(value)
    if not text:
        return None
    return parse_float(text)


def _as_price(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        price = int(round(float(value)))
        return price if price > 0 else None
    text = _as_text(value)
    if not text:
        return None
    parsed = parse_price_eur(text)
    if parsed is None:
        parsed = parse_int(text)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _as_surface(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        surface = float(value)
        return round(surface, 1) if 0 < surface <= 5000 else None
    text = _as_text(value)
    if not text:
        return None
    surface = parse_surface_m2(text)
    if surface is not None:
        return surface
    # Schemas sometimes return a bare number ("29.5" / "29,5") and nothing else.
    if _BARE_NUMBER_RE.fullmatch(text.strip()):
        bare = parse_float(text)
        if bare is not None and 0 < bare <= 5000:
            return round(bare, 1)
    return None


def _as_rooms(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        rooms = int(value)
    else:
        text = (_as_text(value) or "").strip()
        parsed = parse_rooms(text)
        if parsed is None and _BARE_NUMBER_RE.fullmatch(text):
            # Bare "2" is acceptable; "Paris 11eme" is not.
            parsed = parse_int(text)
        rooms = parsed or 0
    return rooms if 0 < rooms <= 30 else None


_MONTH_NAMES = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}
_REL_RE = re.compile(
    r"il y a\s+(\d+)\s*(minute|heure|jour|semaine|mois)",
    re.IGNORECASE,
)
_UNIT_DELTA = {
    "minute": timedelta(minutes=1),
    "heure": timedelta(hours=1),
    "jour": timedelta(days=1),
    "semaine": timedelta(weeks=1),
    "mois": timedelta(days=30),
}


def parse_published_at(value: Any) -> Optional[datetime]:
    """Parse ``il y a 2 heures`` / ``Aujourd'hui`` / ``12/03/2024`` into UTC."""
    text = clean_text(_as_text(value))
    if not text:
        return None
    now = datetime.now(timezone.utc)
    lowered = text.lower()
    if "aujourd" in lowered or "à l'instant" in lowered or "a l'instant" in lowered:
        return now
    if "hier" in lowered:
        return now - timedelta(days=1)

    match = _REL_RE.search(lowered)
    if match:
        try:
            count = int(match.group(1))
        except ValueError:
            return None
        unit = match.group(2).lower()
        delta = _UNIT_DELTA.get(unit)
        if delta:
            return now - delta * count

    match = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", text)
    if match:
        try:
            return datetime(
                int(match.group(3)), int(match.group(2)), int(match.group(1)), tzinfo=timezone.utc
            )
        except ValueError:
            return None

    norm = "".join(
        c for c in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(c)
    )
    match = re.search(r"\b(\d{1,2})\s+([a-z]+)\s+(\d{4})\b", norm)
    if match:
        month = _MONTH_NAMES.get(match.group(2))
        if month:
            try:
                return datetime(int(match.group(3)), month, int(match.group(1)), tzinfo=timezone.utc)
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# Geographie / URL building
# ---------------------------------------------------------------------------
def guess_department(postal_code: Optional[str]) -> Optional[str]:
    """Return the French department code for a postal code."""
    postal = re.sub(r"\D", "", postal_code or "")
    if len(postal) != 5:
        return None
    if postal.startswith("20"):  # Corse
        return "2A" if int(postal) < 20200 else "2B"
    if postal.startswith("97") or postal.startswith("98"):  # DOM/TOM
        return postal[:3]
    return postal[:2]


def slugify_city(city: Optional[str]) -> str:
    """``"Paris 11e"`` -> ``"paris-11e"`` (ASCII, dashes, lowercase)."""
    text = clean_text(city) or ""
    norm = unicodedata.normalize("NFKD", text)
    norm = "".join(c for c in norm if not unicodedata.combining(c))
    norm = norm.lower().replace("'", "-").replace("’", "-")
    norm = re.sub(r"[^a-z0-9]+", "-", norm)
    return norm.strip("-")


def build_leboncoin_urls(
    city: Optional[str],
    postal_code: Optional[str],
    *,
    page: Optional[int] = None,
    extra: Optional[dict[str, str]] = None,
) -> list[str]:
    """Result-page URLs for Leboncoin rentals (category 10)."""
    term = (postal_code or city or "").strip()
    base_params: dict[str, str] = {"category": "10", "sort": "time"}
    if extra:
        base_params.update(extra)

    urls: list[str] = []
    if term:
        params = dict(base_params)
        params["locations"] = term
        urls.append(f"https://www.leboncoin.fr/recherche?{urlencode(params)}")
        params = dict(base_params)
        params["text"] = term
        urls.append(f"https://www.leboncoin.fr/recherche?{urlencode(params)}")
    else:
        urls.append(f"https://www.leboncoin.fr/recherche?{urlencode(base_params)}")

    if page and page > 1:
        return [f"{url}&page={page}" for url in urls]
    return urls


def build_seloger_urls(
    city: Optional[str],
    postal_code: Optional[str],
    *,
    place_id: Optional[str] = None,
    page: Optional[int] = None,
    transaction: str = "location",
    estate: str = "appartement",
) -> list[str]:
    """Candidate SeLoger result URLs.

    SeLoger returns ``410 Gone`` unless the path ends with a ``adXXfrYY`` place
    identifier, so ``place_id`` should come from
    :func:`find_seloger_place_url`. The city segment is only used to build
    human-readable URLs; SeLoger resolves the place id, not the slug.
    """
    postal = re.sub(r"\D", "", postal_code or "")
    dept = guess_department(postal) or "75"

    if place_id:
        pid = place_id.lower()
        if not pid.startswith("ad"):
            pid = f"ad{dept}fr{pid}"

        prefix = f"https://www.seloger.com/recherche/{transaction}/{estate}/{dept}-{dept}000"
        slugs: list[str] = []
        city_slug = slugify_city(city)
        if city_slug:
            for candidate in (f"{city_slug}-{postal}" if postal else None, f"{city_slug}-{dept}000"):
                if candidate and candidate not in slugs:
                    slugs.append(candidate)

        urls = [f"{prefix}/{pid}"]
        for slug in slugs:
            urls.append(f"{prefix}/{slug}/{pid}")

        if page and page > 1:
            return [f"{url}?page={page}" for url in urls]
        return urls

    # Without a place id SeLoger answers ``410 Gone``: let the caller run a
    # search-based discovery instead of wasting a scrape credit.
    return []


_SELOGER_PLACE_RE = re.compile(r"/ad(\d{2}[a-z]{0,2})fr\d+", re.IGNORECASE)


def find_seloger_place_url(results: Iterable[dict[str, Any]]) -> Optional[str]:
    """Pick the best SeLoger search-result URL out of ``firecrawl search`` output."""
    best: Optional[str] = None
    for item in results or []:
        url = _as_text(item.get("url") if isinstance(item, dict) else None) or ""
        if "seloger.com" not in url:
            continue
        if not _SELOGER_PLACE_RE.search(url):
            continue
        if "/recherche/" not in url:
            continue
        if best is None:
            best = url
        # Prefer a rental search URL when present.
        if "/recherche/location/" in url:
            return url.split("#")[0]
    return best.split("#")[0] if best else None


# ---------------------------------------------------------------------------
# Fallback parsers (markdown / search descriptions)
# ---------------------------------------------------------------------------
_SELOGER_CARD_RE = re.compile(
    r"\[(?P<label>[^\]]{3,300})\]\((?P<url>https://www\.seloger\.com/annonce/[^)\s]+)\)"
)
_SELO_PRICE_RE = re.compile(r"(\d[\d\s\u00a0\u202f.]*)\s*€\s*/\s*mois", re.IGNORECASE)
_SELO_DETAIL_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*pi[eè]ce", re.IGNORECASE
)

_LEBON_CARD_RE = re.compile(
    r"\[(?P<label>[^\]]{0,200})\]\((?P<url>https://www\.leboncoin\.fr/(?:ad|vi)/[^)\s]+)\)"
)


@dataclass
class ParsedCard:
    """Loosely-typed listing parsed out of markdown text."""

    url: str
    title: Optional[str] = None
    price: Optional[int] = None
    surface_m2: Optional[float] = None
    rooms: Optional[int] = None
    location: Optional[str] = None
    description: Optional[str] = None
    published_at: Optional[datetime] = None


def _split_seloger_label(label: str) -> dict[str, Any]:
    """``Appartement à louer - Paris 11e - 3 073 €/mois - 4 pièces, 2 chambres``."""
    parts = [p.strip() for p in re.split(r"\s+-\s+", label) if p.strip()]
    out: dict[str, Any] = {}
    if not parts:
        return out
    out["title"] = parts[0]
    for part in parts[1:]:
        lowered = part.lower()
        is_price_part = "€" in part or "eur" in lowered
        if is_price_part:
            price = _as_price(part)
            if price:
                out["price"] = price
        # Surface / rooms may live in the price segment OR the detail segment.
        if out.get("surface_m2") is None:
            surface = _as_surface(part)
            if surface:
                out["surface_m2"] = surface
        if out.get("rooms") is None:
            rooms = _as_rooms(part)
            if rooms:
                out["rooms"] = rooms
        if out.get("location") is None and not is_price_part and re.search(r"[A-Za-zÀ-ÿ]{3,}", part):
            out["location"] = part
    return out


def parse_seloger_markdown(text: Optional[str]) -> list[ParsedCard]:
    """Extract listing cards from a SeLoger SERP rendered as markdown.

    Works both on a scraped page and on the ``description`` field returned by
    ``firecrawl search`` (which embeds the whole SERP).
    """
    if not text:
        return []
    cards: list[ParsedCard] = []
    seen: set[str] = set()
    for match in _SELOGER_CARD_RE.finditer(text):
        url = match.group("url").split("?")[0]
        if url in seen:
            continue
        label = clean_text(match.group("label")) or ""
        # Skip navigation / "voir plus" style anchors.
        if not re.search(r"à louer|à vendre", label, re.IGNORECASE):
            continue
        seen.add(url)
        data = _split_seloger_label(label)
        cards.append(
            ParsedCard(
                url=url,
                title=data.get("title"),
                price=data.get("price"),
                surface_m2=data.get("surface_m2"),
                rooms=data.get("rooms"),
                location=data.get("location"),
                description=label,
            )
        )
    return cards


def parse_leboncoin_markdown(text: Optional[str]) -> list[ParsedCard]:
    """Extract listing cards from a Leboncoin SERP rendered as markdown.

    Cards look like ``[Voir l'annonce](https://www.leboncoin.fr/ad/locations/123)``
    followed by price / category lines, so we only trust the URL and recover the
    rest from the surrounding block of text.
    """
    if not text:
        return []
    cards: list[ParsedCard] = []
    seen: set[str] = set()
    matches = list(_LEBON_CARD_RE.finditer(text))
    for index, match in enumerate(matches):
        url = match.group("url").split("?")[0]
        if url in seen:
            continue
        seen.add(url)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else min(len(text), start + 600)
        block = text[start:end]
        price = None
        for price_match in _SELO_PRICE_RE.finditer(block):
            price = _as_price(price_match.group(0))
            if price:
                break
        if price is None:
            euro = re.search(r"(\d[\d\s\u00a0\u202f]*)\s*€", block)
            if euro:
                price = _as_price(euro.group(0))
        surface = _as_surface(block)
        rooms = _as_rooms(block)
        published = parse_published_at(block)
        location = None
        loc_match = re.search(r"\b(\d{5})\b[^\n]{0,60}", block)
        if loc_match:
            location = clean_text(loc_match.group(0))
        label = clean_text(match.group("label")) or ""
        title = label if label and label.lower() not in {"voir l'annonce", "voir l’annonce"} else None
        cards.append(
            ParsedCard(
                url=url,
                title=title,
                price=price,
                surface_m2=surface,
                rooms=rooms,
                location=location,
                published_at=published,
            )
        )
    return cards


# ---------------------------------------------------------------------------
# Payload -> ScrapedListing
# ---------------------------------------------------------------------------
_HOST_PREFIX = {
    "leboncoin": "https://www.leboncoin.fr",
    "seloger": "https://www.seloger.com",
    "pap": "https://www.pap.fr",
    "paruvendu": "https://www.paruvendu.fr",
    "logicimmo": "https://www.logic-immo.com",
}


def _absolute_url(url: Optional[str], source: str) -> Optional[str]:
    text = clean_text(url)
    if not text:
        return None
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("/"):
        return (_HOST_PREFIX.get(source, "") + text) or None
    if text.startswith(("http://", "https://")):
        return text
    return None


def external_id_from_url(url: str) -> Optional[str]:
    """Derive a stable listing id from a detail URL."""
    try:
        path = urlparse(url).path.rstrip("/")
    except Exception:
        return None
    if not path:
        return None
    tail = path.rsplit("/", 1)[-1]
    tail = tail.split("?")[0]
    # Leboncoin: /ad/locations/1234567890 ; SeLoger: /annonce/.../26JT3S1KC5GW
    match = re.search(r"(\d{6,})$", tail)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9]{6,}", tail):
        return tail
    match = re.search(r"(\d{5,})", path)
    return match.group(1) if match else None


def _listing_from_item(
    item: Any,
    *,
    source: str,
    fallback_city: Optional[str] = None,
    fallback_postal: Optional[str] = None,
) -> Optional[ScrapedListing]:
    if not isinstance(item, dict):
        return None

    raw_url = (
        item.get("url")
        or item.get("link")
        or item.get("href")
        or item.get("permalink")
        or item.get("annonce_url")
    )
    url = _absolute_url(_as_text(raw_url), source)
    if not url:
        return None

    title = clean_text(_as_text(item.get("title") or item.get("subject") or item.get("label")), max_len=240)
    description = clean_text(
        _as_text(item.get("description") or item.get("summary") or item.get("body")), max_len=1200
    )
    location = clean_text(
        _as_text(
            item.get("location")
            or item.get("city")
            or item.get("address")
            or item.get("district")
        ),
        max_len=220,
    )

    haystack = " ".join(v for v in (title, description, location) if v)
    price = _as_price(item.get("price") or item.get("rent") or item.get("loyer"))
    if price is None:
        price = parse_price_eur(haystack, monthly_only=True)
    surface = _as_surface(
        item.get("surface_m2")
        or item.get("surface")
        or item.get("area")
        or item.get("living_area")
    )
    if surface is None:
        surface = parse_surface_m2(haystack)
    rooms = _as_rooms(item.get("rooms") or item.get("pieces") or item.get("number_of_rooms"))
    if rooms is None:
        rooms = parse_rooms(haystack)

    images: list[str] = []
    for key in ("image", "images", "photos", "pictures", "thumbnail"):
        value = item.get(key)
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            image = _absolute_url(_as_text(candidate), source)
            if image and image not in images:
                images.append(image)

    external_id = clean_text(_as_text(item.get("id") or item.get("external_id")), max_len=128)
    if not external_id:
        external_id = external_id_from_url(url) or hash_str(f"{source}|{url}")

    return ScrapedListing(
        source=source,
        external_id=external_id,
        url=url,
        title=title,
        price=price,
        surface_m2=surface,
        location=location or fallback_city,
        rooms=rooms,
        description=description,
        images=images or None,
        published_at=parse_published_at(item.get("published_text") or item.get("published_at")),
    )


def _iter_items(payload: Any) -> Iterable[Any]:
    """Yield the listing dicts from the many shapes Firecrawl may return."""
    if isinstance(payload, list):
        yield from payload
        return
    if not isinstance(payload, dict):
        return
    if isinstance(payload.get("listings"), list):
        yield from payload["listings"]
        return
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("listings", "results", "items", "ads", "classifieds"):
            if isinstance(data.get(key), list):
                yield from data[key]
                return
    if isinstance(data, list):
        yield from data
        return
    for key in ("results", "items", "ads", "classifieds", "annonces"):
        if isinstance(payload.get(key), list):
            yield from payload[key]
            return


def payload_to_listings(
    payload: Any,
    *,
    source: str,
    fallback_city: Optional[str] = None,
    fallback_postal: Optional[str] = None,
) -> list[ScrapedListing]:
    """Convert a Firecrawl JSON payload into :class:`ScrapedListing` objects."""
    listings: list[ScrapedListing] = []
    seen: set[str] = set()
    for item in _iter_items(payload):
        listing = _listing_from_item(
            item,
            source=source,
            fallback_city=fallback_city,
            fallback_postal=fallback_postal,
        )
        if listing is None:
            continue
        key = listing.url or listing.external_id
        if key in seen:
            continue
        seen.add(key)
        listings.append(listing)
    return listings


def cards_to_listings(
    cards: Iterable[ParsedCard],
    *,
    source: str,
    fallback_city: Optional[str] = None,
    fallback_postal: Optional[str] = None,
) -> list[ScrapedListing]:
    """Convert markdown-derived cards into :class:`ScrapedListing` objects."""
    listings: list[ScrapedListing] = []
    seen: set[str] = set()
    for card in cards:
        url = _absolute_url(card.url, source)
        if not url or url in seen:
            continue
        seen.add(url)
        listings.append(
            ScrapedListing(
                source=source,
                external_id=external_id_from_url(url) or hash_str(f"{source}|{url}"),
                url=url,
                title=card.title,
                price=card.price,
                surface_m2=card.surface_m2,
                location=card.location or fallback_city,
                rooms=card.rooms,
                description=card.description,
                published_at=card.published_at,
            )
        )
    return listings


def merge_listings(primary: Iterable[ScrapedListing], secondary: Iterable[ScrapedListing]) -> list[ScrapedListing]:
    """Merge two scraper outputs, keeping the richest record per URL."""
    merged: dict[str, ScrapedListing] = {}
    for listing in list(primary) + list(secondary):
        key = listing.url or listing.external_id
        current = merged.get(key)
        if current is None:
            merged[key] = listing
            continue
        # Prefer the record that carries more useful fields.
        def score(entry: ScrapedListing) -> int:
            return sum(
                1
                for value in (
                    entry.title,
                    entry.price,
                    entry.surface_m2,
                    entry.location,
                    entry.rooms,
                    entry.description,
                    entry.images,
                )
                if value
            )

        if score(listing) > score(current):
            merged[key] = listing
    return list(merged.values())


def summarise_payload(payload: Any) -> str:
    """Short debug string used in logs (never leaks page content)."""
    try:
        return json.dumps(payload)[:200]
    except Exception:
        return repr(payload)[:200]
