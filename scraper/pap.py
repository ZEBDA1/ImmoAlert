from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedListing
from scraper.html_helpers import canonical_url, card_images
from utils.http import fetch_html
from utils.locations import pap_geo_id
from utils.text import clean_text, parse_price_eur, parse_rooms, parse_surface_m2


class PAPScraper(BaseScraper):
    source = "pap"

    def _candidate_urls(self, city: Optional[str], postal_code: Optional[str]) -> list[str]:
        # PAP ignores the former ?q=postcode URL. Its own autocomplete returns
        # a real geographic ID; never fall back to the national home/sales page.
        suffix = ""
        if city or postal_code:
            identifier = pap_geo_id(city, postal_code)
            if identifier is None:
                return []
            suffix = f"-g{identifier}"
        return [f"https://www.pap.fr/annonce/locations-{estate}{suffix}" for estate in ("appartement", "maison")]

    def _parse_cards(self, html: str) -> list[ScrapedListing]:
        soup = BeautifulSoup(html, "lxml")
        heading = soup.select_one("h1")
        if heading and not re.search(r"\blocation", heading.get_text(" ", strip=True), re.I):
            return []
        rows, seen = [], set()
        for card in soup.select(".search-list-item-alt, .search-list-item"):
            anchor = card.select_one("a.item-title[href*='/annonces/']") or card.select_one("a[href*='/annonces/']")
            if not anchor:
                continue
            url = canonical_url(anchor.get("href") or "", "https://www.pap.fr")
            identifier = re.search(r"-r(\d+)$", url)
            if not identifier or url in seen or not re.search(r"/annonces/(?:appartement|maison|studio|chambre)-", url):
                continue
            description_node = card.select_one(".item-description")
            category = description_node.get("data-gaq", "") if description_node else ""
            if "VENTE" in category.upper():
                continue
            price_node, tags, location_node = card.select_one(".item-price"), card.select_one(".item-tags"), card.select_one(".h1")
            if not price_node or not location_node:
                continue
            description = clean_text(description_node.get_text(" ", strip=True)) if description_node else None
            details = tags.get_text(" ", strip=True) if tags else ""
            location = clean_text(location_node.get_text(" ", strip=True))
            # A card may say "Limay (78520) à 1km de Mantes-la-Jolie".
            # The search city is not the property's city.
            location = re.sub(r"\s+[àa]\s*\d+(?:[.,]\d+)?\s*km\b.*$", "", location or "", flags=re.I).strip() or None
            property_name = "Maison" if "/annonces/maison-" in url else "Appartement"
            if re.search(r"\bstudio\b", description or "", re.I):
                property_name = "Studio"
            if re.search(r"\bcolocation\b", anchor.get_text(" ", strip=True), re.I):
                property_name = "Chambre en colocation"
            title = " · ".join(value for value in (property_name, details, location) if value)
            rows.append(ScrapedListing(source=self.source, external_id="r"+identifier.group(1), url=url, title=title,
                price=parse_price_eur(price_node.get_text(" ", strip=True), monthly_only=True),
                surface_m2=parse_surface_m2(details), rooms=parse_rooms(details), location=location,
                description=description, images=card_images(card, "https://www.pap.fr") or None))
            seen.add(url)
        return rows

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        seen = set()
        for base in self._candidate_urls(city, postal_code):
            url = base
            for _ in range(2):
                html = fetch_html(url, referer="https://www.pap.fr/")
                if not html:
                    break
                rows = self._parse_cards(html)
                for row in rows:
                    if row.external_id not in seen:
                        seen.add(row.external_id)
                        yield row
                if not rows:
                    break
                soup = BeautifulSoup(html, "lxml")
                next_link = soup.select_one('.pagination a[rel="next"], a[rel="next"][href*="/annonce/locations"]')
                if not next_link:
                    next_link = next((a for a in soup.select(".pagination a[href]") if a.get_text(" ", strip=True).lower() == "suivante" and str(a.get("href", "")).startswith("/annonce/locations")), None)
                if not next_link:
                    break
                url = urljoin(base, next_link.get("href") or "")
