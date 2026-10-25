from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedListing
from utils.http import fetch_html
from scraper.html_helpers import jsonld_rentals
from utils.locations import resolve_commune, city_slug



class RentolaScraper(BaseScraper):
    source = "rentola"

    def _search_urls(self, city: Optional[str], postal_code: Optional[str]) -> list[str]:
        base = "https://rentola.fr"
        if not city and postal_code:
            commune = resolve_commune(None, postal_code)
            city = commune["nom"] if commune else None
            if not city:
                return []
        # A bare postcode is interpreted as a different place by Rentola.
        return [f"{base}/location/{city_slug(city)}"] if city else [f"{base}/location"]

    def _extract_cards(self, html: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        urls: list[str] = []
        seen: set[str] = set()
        for a in soup.select("a[href*='/annonces/'], a[href*='/listings/'], a[href*='/property/']"):
            href = a.get("href") or ""
            if not href:
                continue
            full = urljoin("https://rentola.fr", href)
            if full in seen:
                continue
            seen.add(full)
            urls.append(full)
        return urls

    def _parse_detail(self, url: str) -> Optional[ScrapedListing]:
        html = fetch_html(url, referer="https://rentola.fr/")
        if not html:
            return None
        structured = jsonld_rentals(html, self.source, "https://rentola.fr")
        return next((row for row in structured if row.url == url.split("?")[0].rstrip("/")), None)
        # Historical whole-page heuristics are deliberately not used: FAQs and
        # neighbouring properties contain prices and surfaces of other homes.

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        for u in self._search_urls(city, postal_code):
            html = fetch_html(u, referer="https://rentola.fr/")
            if not html:
                continue
            # Search pages already contain complete, scoped JSON-LD listings.
            # Avoid 25 detail requests and the unrelated prices in their FAQs.
            rows = jsonld_rentals(html, self.source, "https://rentola.fr")
            if rows:
                yield from rows[:100]
                return
            if self._extract_cards(html):
                raise RuntimeError("Rentola: liens d’annonces présents mais données structurées non reconnues")
