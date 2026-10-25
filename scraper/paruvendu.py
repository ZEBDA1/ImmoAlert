from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedListing
from utils.cache import TTLCache
from utils.http import fetch_html, http_client
from utils.text import parse_float, parse_int
from utils.text import parse_price_eur, parse_rooms, parse_surface_m2, clean_text
from scraper.html_helpers import card_images, canonical_url


INSEE_CACHE = TTLCache(ttl_seconds=24 * 3600)


class ParuVenduScraper(BaseScraper):
    source = "paruvendu"

    def _resolve_insee(self, city: Optional[str], postal_code: Optional[str]) -> Optional[str]:
        if postal_code:
            key = f"insee:postal:{postal_code}:{(city or '').lower()}"
            cached = INSEE_CACHE.get(key)
            if cached:
                return cached

            params = {
                "codePostal": postal_code,
                "fields": "nom,code,codesPostaux",
                "format": "json",
            }
            from utils.locations import public_json
            communes = public_json("https://geo.api.gouv.fr/communes", params) or []
            if not communes:
                return None
            if city:
                city_norm = city.lower().replace("-", " ").strip()
                for c in communes:
                    name = (c.get("nom") or "").lower().replace("-", " ").strip()
                    if city_norm in name or name in city_norm:
                        code = c.get("code")
                        if code:
                            INSEE_CACHE.set(key, code)
                            return code
            code = communes[0].get("code")
            if code:
                INSEE_CACHE.set(key, code)
            return code

        if city:
            key = f"insee:city:{city.lower()}"
            cached = INSEE_CACHE.get(key)
            if cached:
                return cached
            params = {
                "nom": city,
                "fields": "nom,code,codesPostaux",
                "format": "json",
                "boost": "population",
                "limit": "5",
            }
            from utils.locations import public_json
            communes = public_json("https://geo.api.gouv.fr/communes", params) or []
            if communes:
                code = communes[0].get("code")
                if code:
                    INSEE_CACHE.set(key, code)
                    return code
        return None

    def _search_url(self, city: Optional[str], postal_code: Optional[str]) -> str:
        insee = self._resolve_insee(city, postal_code)
        if insee:
            params = {
                "tt": "5",  # location
                "pa": "FR",
                "codeINSEE": insee,
            }
            if postal_code:
                params["lo"] = postal_code
            if city or postal_code:
                params["ray"] = "15"
            return f"https://www.paruvendu.fr/immobilier/annonceimmofo/liste/listeAnnonces?{urlencode(params)}"
        return "https://www.paruvendu.fr/immobilier/location/"

    def _listing_urls(self, search_url: str) -> list[str]:
        html = fetch_html(search_url, referer="https://www.paruvendu.fr/")
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        urls: list[str] = []
        seen: set[str] = set()
        for a in soup.select("a[href*='/immobilier/location/']"):
            href = a.get("href") or ""
            if not href:
                continue
            full = urljoin("https://www.paruvendu.fr", href)
            if not re.search(r"/\d+[A-Z0-9]+$", full):
                continue
            if full in seen:
                continue
            seen.add(full)
            urls.append(full)
        return urls

    def _parse_cards(self, html: str) -> list[ScrapedListing]:
        soup = BeautifulSoup(html, "html.parser")
        rows, seen = [], set()
        for card in soup.select(".blocAnnonce[data-id]"):
            anchor = card.select_one("h3 a[href*='/immobilier/location/']")
            if not anchor:
                continue
            url = canonical_url(anchor.get("href") or "", "https://www.paruvendu.fr")
            if url in seen or not re.search(r"/\d+[A-Z0-9]+$", url):
                continue
            title = clean_text(anchor.get("title") or anchor.get_text(" ", strip=True))
            text = card.get_text(" ", strip=True)
            price_node = card.select_one("[data-o]")
            price = parse_price_eur(price_node.get_text(" ", strip=True) if price_node else text, monthly_only=True)
            location = None
            for label in anchor.stripped_strings:
                if re.search(r"\(\d{2,5}\)", label):
                    location = clean_text(label)
            if location:
                location = re.sub(r"^.*?m\s*2\s+", "", location)
            description_node = card.select_one("p")
            description = description_node.get_text(" ", strip=True) if description_node else None
            rows.append(ScrapedListing(source=self.source, external_id=url.rsplit("/",1)[-1], url=url,
                title=title, price=price, surface_m2=parse_surface_m2(title), rooms=parse_rooms(text),
                location=location, description=description, images=card_images(card.select_one(".blocMedia") or card, "https://www.paruvendu.fr") or None))
            seen.add(url)
        return rows

    def _parse_detail(self, url: str) -> Optional[ScrapedListing]:
        html = fetch_html(url, referer="https://www.paruvendu.fr/")
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        h1 = soup.select_one("h1")
        h1_txt = " ".join(h1.get_text(" ", strip=True).split()) if h1 else ""
        meta_desc = soup.select_one("meta[name='description']")
        desc = (meta_desc.get("content") if meta_desc else None) or ""
        all_txt = " ".join([h1_txt, desc]).strip()
        if not all_txt:
            return None

        external_id = url.rstrip("/").split("/")[-1]
        price = None
        m_price = re.search(r"loyer de\s*([\d\s]+)\s*€", desc, re.IGNORECASE)
        if not m_price:
            m_price = re.search(r"([\d\s]{3,})\s*€", h1_txt)
        if m_price:
            price = parse_int(m_price.group(1))

        surface = None
        m_surface = re.search(r"(\d+(?:[.,]\d+)?)\s*m(?:²|2)", h1_txt, re.IGNORECASE)
        if m_surface:
            surface = parse_float(m_surface.group(1))

        rooms = None
        m_rooms = re.search(r"(\d+)\s*pi[eè]ces?", h1_txt, re.IGNORECASE)
        if m_rooms:
            try:
                rooms = int(m_rooms.group(1))
            except ValueError:
                rooms = None

        location = None
        m_loc = re.search(r"m[²2]\s+(.+)$", h1_txt, re.IGNORECASE)
        if m_loc:
            location = " ".join(m_loc.group(1).split())
        elif "(" in h1_txt:
            location = " ".join(h1_txt.split("(")[0].split())

        img = soup.select_one("meta[property='og:image']")
        img_url = img.get("content") if img else None
        title = h1_txt or (soup.title.get_text(strip=True) if soup.title else "Annonce")

        return ScrapedListing(
            source=self.source,
            external_id=external_id,
            url=url,
            title=title,
            price=price,
            surface_m2=surface,
            location=location,
            rooms=rooms,
            description=desc or None,
            images=[img_url] if img_url else None,
        )

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        search_url = self._search_url(city, postal_code)
        html = fetch_html(search_url, referer="https://www.paruvendu.fr/")
        if html:
            rows = self._parse_cards(html)
            if rows:
                yield from rows[:100]
                return
        if html and self._listing_urls(search_url):
            raise RuntimeError("ParuVendu: liens présents mais cartes non reconnues")
