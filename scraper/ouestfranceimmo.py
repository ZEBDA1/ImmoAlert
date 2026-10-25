from __future__ import annotations

import json
import re
from typing import Iterable, Optional

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedListing
from scraper.html_helpers import canonical_url, card_images
from utils.http import fetch_html
from utils.locations import resolve_commune, city_slug
from utils.text import clean_text, parse_price_eur, parse_rooms, parse_surface_m2


class OuestFranceImmoScraper(BaseScraper):
    source = "ouestfranceimmo"

    def _candidate_urls(self, city: Optional[str], postal_code: Optional[str]) -> list[str]:
        commune = resolve_commune(city, postal_code)
        if not commune:
            return []
        code = commune["code"]
        department = code[:3] if code.startswith(("97", "98")) else code[:2]
        place = f"{city_slug(commune['nom'])}-{department}-{code}"
        return [f"https://www.ouestfrance-immo.com/immobilier/location/{estate}/{place}/" for estate in ("appartement", "maison")]

    def _parse_cards(self, html: str) -> list[ScrapedListing]:
        soup = BeautifulSoup(html, "lxml")
        rows, seen = [], set()
        for card in soup.select("a.annLink[href*='/immobilier/location/']"):
            price_node, title_node = card.select_one(".annPrix"), card.select_one(".annTitre")
            if not price_node or not title_node:
                continue
            url = canonical_url(card.get("href") or "", "https://www.ouestfrance-immo.com")
            identifier = re.search(r"-(\d+)\.htm$", url)
            if not identifier or url in seen:
                continue
            try:
                metadata = json.loads(card.get("data-trk-payload") or "{}").get("annonce", {})
            except (TypeError, ValueError):
                metadata = {}
            if metadata.get("vendu") or metadata.get("tra", "L") != "L":
                continue
            location_data = metadata.get("lieu") or {}
            city, postal = location_data.get("ville"), location_data.get("cp")
            location = " ".join(value for value in (city, postal) if value)
            if not location:
                location_node = card.select_one(".annAdresse, .annVille")
                location = clean_text(location_node.get_text(" ", strip=True)) if location_node else None
            title = clean_text(title_node.get_text(" ", strip=True))
            if metadata.get("colocation") and title:
                title = "Chambre en colocation · " + title
            description_node = card.select_one(".annTexte")
            description = clean_text(description_node.get_text(" ", strip=True)) if description_node else None
            features = card.select_one(".annCriteres")
            details = features.get_text(" ", strip=True) if features else ""
            rows.append(ScrapedListing(source=self.source, external_id=identifier.group(1), url=url,
                title=title, price=parse_price_eur(price_node.get_text(" ", strip=True), monthly_only=True),
                surface_m2=parse_surface_m2(details), rooms=parse_rooms(title), location=location,
                description=description, images=card_images(card, "https://www.ouestfrance-immo.com") or None))
            seen.add(url)
        return rows

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        seen = set()
        for url in self._candidate_urls(city, postal_code):
            html = fetch_html(url, referer="https://www.ouestfrance-immo.com/")
            if not html:
                continue
            for row in self._parse_cards(html):
                if row.external_id not in seen:
                    seen.add(row.external_id)
                    yield row
