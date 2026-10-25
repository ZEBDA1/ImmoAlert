from __future__ import annotations

import re
from typing import Iterable, Optional

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedListing
from utils.http import fetch_html
from utils.text import parse_price_eur, parse_surface_m2, parse_rooms, clean_text
from scraper.html_helpers import canonical_url, card_images, location_from_url


class EntreParticuliersScraper(BaseScraper):
    source = "entreparticuliers"

    def _slug(self, txt: str) -> str:
        s = txt.lower().strip()
        s = re.sub(r"[àâä]", "a", s)
        s = re.sub(r"[éèêë]", "e", s)
        s = re.sub(r"[îï]", "i", s)
        s = re.sub(r"[ôö]", "o", s)
        s = re.sub(r"[ùûü]", "u", s)
        s = re.sub(r"[^a-z0-9]+", "-", s)
        return s.strip("-")

    def _candidate_urls(self, city: Optional[str], postal_code: Optional[str]) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()

        def _add(url: str) -> None:
            if url not in seen:
                seen.add(url)
                urls.append(url)

        if city and postal_code:
            _add(
                f"https://www.entreparticuliers.com/annonces-immobilieres/location/{self._slug(city)}-{postal_code}"
            )
        if city:
            _add(f"https://www.entreparticuliers.com/annonces-immobilieres/location/{self._slug(city)}")
        if postal_code and len(postal_code) >= 2:
            dep = postal_code[:2]
            _add(f"https://www.entreparticuliers.com/annonces-immobilieres/location/{dep}")
        if not urls:
            _add("https://www.entreparticuliers.com/annonces-immobilieres/location")
        return urls

    def _parse_cards(self, html: str) -> list[ScrapedListing]:
        soup = BeautifulSoup(html, "html.parser")
        rows, seen = [], set()
        for card in soup.select("article"):
            anchor = card.select_one("a[href*='/ref-']")
            if not anchor:
                continue
            url = canonical_url(anchor.get("href") or "", "https://www.entreparticuliers.com")
            if "/location/" not in url or url in seen:
                continue
            match = re.search(r"/ref-(\d+)$", url)
            if not match:
                continue
            title = clean_text(anchor.get("aria-label"))
            if not title:
                description = card.select_one("p")
                title = clean_text(description.get_text(" ", strip=True)) if description else None
            text = card.get_text(" ", strip=True)
            label = next((a.get_text(" ", strip=True) for a in card.select("a[href*='/ref-']") if re.search(r"\(\d{2,5}\)", a.get_text(" ", strip=True))), None)
            rows.append(ScrapedListing(source=self.source, external_id=match.group(1), url=url, title=title,
                price=parse_price_eur(text, monthly_only=True), surface_m2=parse_surface_m2(title or text),
                rooms=parse_rooms(title or text), location=location_from_url(url, label),
                images=card_images(card, "https://www.entreparticuliers.com") or None))
            seen.add(url)
        return rows

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        for url in self._candidate_urls(city, postal_code):
            html = fetch_html(url, referer="https://www.entreparticuliers.com/")
            if not html:
                continue
            rows = self._parse_cards(html)
            if rows:
                yield from rows[:100]
                return
