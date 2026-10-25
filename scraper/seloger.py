"""SeLoger rentals: verified/native HTML first, Firecrawl extraction as backup."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Iterable, Optional

from scraper.firecrawl_helpers import (
    LISTING_PROMPT,
    LISTING_SCHEMA,
    build_seloger_urls,
    cards_to_listings,
    find_seloger_place_url,
    guess_department,
    merge_listings,
    parse_seloger_markdown,
    payload_to_listings,
    slugify_city,
)
from utils.cache import TTLCache
from utils.firecrawl import firecrawl_available, get_client
from utils.hash import hash_str
from utils.text import parse_float, parse_int

from .base import BaseScraper, ScrapedListing

log = logging.getLogger(__name__)

_MAX_LISTINGS = 120
_MAX_PAGES = 3

#: Long-lived cache of ``(city, postal) -> result URL`` (place ids never change).
_PLACE_CACHE = TTLCache(ttl_seconds=7 * 24 * 3600)


class SeLogerScraper(BaseScraper):
    source = "seloger"
    uses_browser = True

    @property
    def timeout_seconds(self) -> Optional[int]:
        from config import settings

        # discovery + up to _MAX_PAGES scrapes
        budget = (settings.firecrawl_timeout_seconds * (_MAX_PAGES + 1)) // 2
        return max(60, budget)

    # ------------------------------------------------------------------
    # URL discovery
    # ------------------------------------------------------------------
    def _discover_result_url(self, city: Optional[str], postal_code: Optional[str], *, allow_firecrawl: bool = True) -> Optional[str]:
        """Find a working SeLoger search URL (with its ``adXXfrYY`` place id)."""
        cache_key = f"{(city or '').strip().lower()}|{(postal_code or '').strip()}"
        cached = _PLACE_CACHE.get(cache_key)
        if cached:
            return cached

        from utils.locations import verified_portal_route
        if verified := verified_portal_route(self.source, city, postal_code):
            _PLACE_CACHE.set(cache_key, verified)
            return verified

        if not allow_firecrawl or not firecrawl_available():
            return None

        postal = re.sub(r"\D", "", postal_code or "")
        dept = guess_department(postal)
        queries: list[str] = []
        if postal:
            city_part = (city or "").strip()
            queries.append(f"seloger location appartement {postal} {city_part}".strip())
            queries.append(f"seloger location {postal}")
        elif city:
            queries.append(f"seloger location appartement {city}".strip())
        if not queries:
            return None

        client = get_client()
        for query in queries:
            try:
                results = client.search(query, limit=8)
            except Exception as exc:  # pragma: no cover - defensive
                log.info("seloger discovery search failed for %r: %s", query, exc)
                continue
            url = find_seloger_place_url(results)
            from utils.locations import city_slug
            if url and "/recherche/location/" in url and (not city or city_slug(city) in url):
                # Some hits are scoped to the right department but for another city;
                # keep them anyway, SeLoger resolves the place id.
                if dept and f"ad{dept}fr" not in url.lower() and "/recherche/" in url:
                    log.debug("seloger discovery: dept mismatch for %s (%s)", postal, url)
                _PLACE_CACHE.set(cache_key, url)
                log.info("seloger place url for %s/%s -> %s", city, postal_code, url)
                return url
        return None

    def _result_urls(self, city: Optional[str], postal_code: Optional[str]) -> list[str]:
        from config import settings

        url = self._discover_result_url(city, postal_code)
        if not url:
            # Last resort: derive the place id from the department code. Many
            # departments follow ``ad<dept>fr<code>`` but the code is not
            # predictable, so this only helps when the search tier is exhausted.
            return []
        urls: list[str] = []
        for page in range(1, min(settings.firecrawl_max_pages, _MAX_PAGES) + 1):
            urls.append(url if page == 1 else f"{url}{'&' if '?' in url else '?'}page={page}")
        return urls

    # ------------------------------------------------------------------
    # Firecrawl scraping
    # ------------------------------------------------------------------
    def _fetch_with_firecrawl(self, city: Optional[str], postal_code: Optional[str]) -> list[ScrapedListing]:
        if not firecrawl_available():
            return []

        from config import settings

        urls = self._result_urls(city, postal_code)
        if not urls:
            return []

        client = get_client()
        collected: list[ScrapedListing] = []

        for url in urls:
            payload = client.scrape_json(
                url,
                LISTING_SCHEMA,
                prompt=LISTING_PROMPT,
                wait_for_ms=settings.firecrawl_wait_for_ms,
                country=settings.firecrawl_country,
                proxy=settings.firecrawl_proxy,
            )
            page_rows = payload_to_listings(
                payload, source=self.source, fallback_city=city, fallback_postal=postal_code
            ) if payload else []

            if not page_rows:
                # The SERP often renders as markdown but defeats schema extraction.
                markdown = client.fetch_markdown(
                    url,
                    wait_for_ms=settings.firecrawl_wait_for_ms,
                    country=settings.firecrawl_country,
                    proxy=settings.firecrawl_proxy,
                )
                if markdown:
                    page_rows = cards_to_listings(
                        parse_seloger_markdown(markdown),
                        source=self.source,
                        fallback_city=city,
                        fallback_postal=postal_code,
                    )

            if not page_rows:
                log.info("seloger: no rows extracted from %s", url)
                break

            collected.extend(page_rows)
            if len(collected) >= _MAX_LISTINGS:
                break

        return collected[:_MAX_LISTINGS]

    # ------------------------------------------------------------------
    # Legacy API / HTML fallbacks
    # ------------------------------------------------------------------
    def _legacy_html(self, city: Optional[str], postal_code: Optional[str]) -> list[ScrapedListing]:
        from bs4 import BeautifulSoup

        from utils.http import fetch_html

        term = (postal_code or city or "").strip()
        if not term:
            return []
        candidates = [
            f"https://www.seloger.com/list.htm?q={term}",
            f"https://www.seloger.com/annonces/locations/appartement/{slugify_city(city)}-{term}/liste.htm"
            if city
            else f"https://www.seloger.com/list.htm?q={term}",
        ]
        # Reuse the canonical place URL discovered by Firecrawl, even while its
        # quota is exhausted. The legacy URLs alone may return 410 Gone.
        cache_key = f"{(city or '').strip().lower()}|{(postal_code or '').strip()}"
        if discovered := (_PLACE_CACHE.get(cache_key) or self._discover_result_url(city, postal_code, allow_firecrawl=False)):
            candidates.insert(0, discovered)

        for url in candidates:
            html = fetch_html(url, referer="https://www.seloger.com/")
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            cards = soup.select(".c-pa-list .c-pa-list_item a") or soup.select(
                "a[href*='/annonce/'], a[href*='/annonces/']"
            )
            if not cards:
                continue

            rows: list[ScrapedListing] = []
            for anchor in cards[:_MAX_LISTINGS]:
                href = (anchor.get("href") or "").strip()
                if not href:
                    continue
                if href.startswith("/"):
                    href = "https://www.seloger.com" + href
                if "/annonce/" not in href and "/annonces/" not in href:
                    continue
                title = anchor.get_text(" ", strip=True) or None
                price_text = anchor.find_next(string=lambda s: s and "€" in s)
                details = anchor.find_next("ul")
                details_text = details.get_text(" ", strip=True) if details else ""
                from utils.text import parse_rooms, parse_surface_m2

                rows.append(
                    ScrapedListing(
                        source=self.source,
                        external_id=href.rstrip("/").split("/")[-1].split("?")[0] or hash_str(href),
                        url=href,
                        title=title,
                        price=parse_int(str(price_text)) if price_text else None,
                        surface_m2=parse_surface_m2(details_text or (title or "")),
                        rooms=parse_rooms(details_text or (title or "")),
                    )
                )
            if rows:
                return rows
        return []

    def _search_api(self, city: Optional[str], postal_code: Optional[str]) -> list[ScrapedListing]:
        """Original BFF/Playwright path, kept as an additional fallback."""
        from utils.http import fetch_json_with_playwright

        term = postal_code or city or ""
        if not term:
            return []

        autocomplete_payload = {
            "text": term,
            "limit": 8,
            "placeTypes": ["NBH1", "NBH2", "NBH3", "AD04", "AD06", "AD08", "AD09", "POCO", "AD02"],
            "parentTypes": ["NBH1", "NBH2", "NBH3", "AD04", "AD06", "AD08", "AD09", "POCO", "AD02"],
            "locale": "fr",
        }
        ac_data = fetch_json_with_playwright(
            "https://www.seloger.com/search-mfe-bff/autocomplete",
            method="POST",
            json_body=autocomplete_payload,
            warmup_url="https://www.seloger.com/",
            timeout_ms=35000,
        )
        if not ac_data:
            ac_data = self._api_with_cookie("https://www.seloger.com/search-mfe-bff/autocomplete", autocomplete_payload)

        place_id = None
        if isinstance(ac_data, list) and ac_data and isinstance(ac_data[0], dict):
            place_id = ac_data[0].get("id") or ac_data[0].get("placeId")
        if not place_id:
            return []

        search_payload = {
            "criteria": {
                "distributionTypes": ["Rent"],
                "estateTypes": ["Apartment", "House"],
                "projectTypes": ["Resale", "New_Build", "Projected", "Life_Annuity"],
                "location": {"placeIds": [str(place_id)]},
            },
            "paging": {"page": 1, "size": 100, "order": "Default"},
        }
        search_data = fetch_json_with_playwright(
            "https://www.seloger.com/serp-bff/search",
            method="POST",
            json_body=search_payload,
            warmup_url="https://www.seloger.com/classified-search",
            timeout_ms=35000,
        )
        if not search_data:
            search_data = self._api_with_cookie("https://www.seloger.com/serp-bff/search", search_payload)
        if not isinstance(search_data, dict):
            return []

        rows = search_data.get("classifieds") or []
        ids = [str(row.get("id") or row.get("classifiedId")) for row in rows if isinstance(row, dict)]
        ids = [i for i in ids if i and i != "None"]
        if not ids:
            return []

        details = fetch_json_with_playwright(
            f"https://www.seloger.com/classifiedList/{','.join(ids[:50])}",
            method="GET",
            json_body=None,
            headers={"accept": "*/*", "x-language": "fr"},
            warmup_url="https://www.seloger.com/classified-search",
            timeout_ms=35000,
        )
        if not details:
            details = self._api_with_cookie(
                f"https://www.seloger.com/classifiedList/{','.join(ids[:50])}", None, method="GET"
            )
        if not isinstance(details, list):
            details = rows

        return payload_to_listings(details, source=self.source, fallback_city=city, fallback_postal=postal_code)

    def _api_with_cookie(self, url: str, payload: Optional[dict[str, Any]], *, method: str = "POST") -> Optional[Any]:
        raw_cookie = os.getenv("SELOGER_COOKIE", "").strip()
        if not raw_cookie:
            return None

        import requests

        headers = {
            "accept": "application/json, text/plain, */*",
            "origin": "https://www.seloger.com",
            "referer": "https://www.seloger.com/classified-search",
            "user-agent": os.getenv(
                "SELOGER_USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            ),
            "cookie": raw_cookie,
        }
        if method.upper() == "POST":
            headers["content-type"] = "application/json"
        try:
            if method.upper() == "GET":
                resp = requests.get(url, headers=headers, timeout=20)
            else:
                resp = requests.post(url, headers=headers, json=payload, timeout=20)
            if not 200 <= resp.status_code < 300:
                return None
            return resp.json()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        from scraper.quality import normalize_listing
        try:
            primary = [valid for row in self._legacy_html(city, postal_code)
                       if (valid := normalize_listing(row)) is not None]
        except Exception as exc:
            log.info("seloger primary failed (%s)", type(exc).__name__)
            primary = []
        if primary:
            yield from primary
            return
        try:
            yield from self._fetch_with_firecrawl(city, postal_code)
        except Exception as exc:
            log.info("seloger backup failed (%s)", type(exc).__name__)
