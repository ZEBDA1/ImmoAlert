"""Scoped HTML helpers: never infer an advertisement from an entire page."""
from __future__ import annotations

import json
import re
import math
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from scraper.base import ScrapedListing
from utils.text import clean_text, parse_price_eur, parse_rooms, parse_surface_m2


def canonical_url(url: str, base: str = "") -> str:
    parts = urlsplit(urljoin(base, url))
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def card_images(node, base: str) -> list[str]:
    images = []
    values = []
    for image in node.select("img"):
        values.extend(image.get(attr) for attr in ("src", "data-src", "original", "data-original"))
    # ParuVendu installs its first photo from a literal URL in an inline script.
    for script in node.select("script"):
        values.extend(re.findall(r"\.src\s*=\s*['\"](https?://[^'\"]+)['\"]", script.get_text()))
    for value in values:
        if not isinstance(value, str) or value.startswith("data:"):
            continue
        url = urljoin(base, value)
        if not url.startswith(("https://", "http://")) or any(token in url.lower() for token in ("transparent", "novisu", "no-photo", "placeholder", "logo", "1x1")):
            continue
        if url not in images:
            images.append(url)
    return images[:12]


def location_from_url(url: str, label: str | None = None) -> str | None:
    """Use a postcode actually present in a listing URL, never the search input."""
    match = re.search(r"/([a-zA-ZÀ-ÿ0-9'-]+)-(\d{5})(?:/|$)", urlsplit(url).path)
    if match:
        city = match.group(1).replace("-", " ").title()
        if label:
            city = re.sub(r"\s*\(\d{2,5}\)\s*$", "", label).strip()
        return f"{city} ({match.group(2)})"
    return clean_text(label)


def jsonld_rentals(html: str, source: str, base: str) -> list[ScrapedListing]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    seen = set()

    def walk(value):
        if isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            kind = value.get("@type", "")
            if kind == "RealEstateListing" or isinstance(kind, list) and "RealEstateListing" in kind:
                yield value
                return
            # Explicit listing containers only: FAQ answers and market-price
            # datasets are not properties, even when they contain euro amounts.
            for key in ("@graph", "mainEntity", "itemListElement", "item"):
                if key in value:
                    yield from walk(value[key])

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (TypeError, ValueError):
            continue
        for item in walk(data):
            offers = item.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if not isinstance(offers, dict):
                continue
            if offers.get("availability", "").endswith(("OutOfStock", "SoldOut", "Discontinued")):
                continue
            offered = offers.get("itemOffered") or item.get("about") or {}
            if not isinstance(offered, dict):
                offered = {}
            url = canonical_url(item.get("url") or offers.get("url") or "", base)
            if not url or "/listings/" not in url or url in seen:
                continue
            title = clean_text(item.get("name"))
            property_name = {"Apartment": "Appartement", "House": "Maison", "Room": "Chambre"}.get(offered.get("@type"))
            if property_name and not re.search(r"\b(?:appartement|maison|studio|chambre|colocation|duplex|loft|villa|[tf][1-9])\b", title or "", re.I):
                title = f"{property_name} · {title}" if title else property_name
            address = offered.get("address") or {}
            if not isinstance(address, dict):
                address = {"streetAddress": str(address)}
            city, postal = address.get("addressLocality"), address.get("postalCode")
            street = address.get("streetAddress") or ""
            if not postal:
                match = re.search(r"\b\d{5}\b", street)
                postal = match.group(0) if match else None
            # Aggregators sometimes use a city-centre landmark as a geocoder
            # address. Keep the explicit city/postcode, not an invented street.
            location = " ".join(str(x) for x in (city, postal) if x) or clean_text(street)
            floor = offered.get("floorSize") or {}
            surface = floor.get("value") if isinstance(floor, dict) else None
            rooms = offered.get("numberOfRooms")
            if isinstance(rooms, dict):
                rooms = rooms.get("value")
            # numberOfBedrooms is deliberately not a room count.
            room_count = parse_rooms(title) or (int(rooms) if isinstance(rooms, (int, float)) and math.isfinite(rooms) and 0 < rooms <= 30 else None)
            price = offers.get("price")
            if not isinstance(price, (int, float)) or isinstance(price, bool):
                price = parse_price_eur(str(price) + " €") if price is not None else None
            image = item.get("image")
            images = image if isinstance(image, list) else [image]
            image_urls = [image.get("url") if isinstance(image, dict) else image for image in images]
            images = [urljoin(base, image) for image in image_urls if isinstance(image, str) and image]
            if not isinstance(price, (int, float)) or not math.isfinite(price):
                continue
            try:
                surface = float(surface) if surface else parse_surface_m2(title)
            except (TypeError, ValueError):
                surface = None
            rows.append(ScrapedListing(source=source, external_id=urlsplit(url).path.rsplit("/", 1)[-1], url=url,
                title=title, price=int(price) if price else None, surface_m2=surface,
                rooms=room_count, location=location, description=clean_text(item.get("description")), images=images or None))
            seen.add(url)
    return rows
