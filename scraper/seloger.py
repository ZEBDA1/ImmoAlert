"""SeLoger rental scraper.

SeLoger is the hardest French portal to scrape:

* every path now requires a trailing ``adXXfrYY`` **place id** – without it the
  server answers ``410 Gone`` (this is why the previous
  ``https://www.seloger.com/list.htm?q=...`` implementation never worked);
* the results grid is rendered client-side and protected by DataDome;
* the documented ``serp-bff`` endpoints need a valid browser session.

Strategy:

1. **Discover** the canonical result URL once per city with ``firecrawl search``
   (the SERP is indexed with its place id). It is cached for a long time by
   :class:`~utils.cache.TTLCache`.
2. **Extract** the listings from that URL with ``firecrawl scrape`` driven by
   :data:`~scraper.firecrawl_helpers.LISTING_SCHEMA`, paging with ``?page=N``.
3. **Fallback** to parsing the markdown Firecrawl returns (and to the legacy
   Playwright/HTML path) when the structured extraction comes back empty.
"""

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
    def _discover_result_url(self, city: Optional[str], postal_code: Optional[str]) -> Optional[str]:
        """Find a working SeLoger search URL (with its ``adXXfrYY`` place id)."""
        cache_key = f"{(city or '').strip().lower()}|{(postal_code or '').strip()}"
        cached = _PLACE_CACHE.get(cache_key)
        if cached:
            return cached

        if not firecrawl_available():
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
            if url:
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
            f"https://www.seloger.com/annonces/achat/appartement/{slugify_city(city)}-{term}/liste.htm"
            if city
            else f"https://www.seloger.com/list.htm?q={term}",
        ]

        for url in candidates:
            html = fetch_html(url, referer="https://www.seloger.com/")
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            cards = soup.select(".c-pa-list .c-pa-list_item a") or soup.select("a[href*='/annonce/']")
            if not cards:
                continue

            rows: list[ScrapedListing] = []
            for anchor in cards[:_MAX_LISTINGS]:
                href = (anchor.get("href") or "").strip()
                if not href:
                    continue
                if href.startswith("/"):
                    href = "https://www.seloger.com" + href
                if "/annonce/" not in href:
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
        firecrawl_rows: list[ScrapedListing] = []
        try:
            firecrawl_rows = self._fetch_with_firecrawl(city, postal_code)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("seloger firecrawl path failed for %s/%s: %s", city, postal_code, exc)

        if len(firecrawl_rows) >= 5:
            for row in firecrawl_rows:
                yield row
            return

        legacy_rows: list[ScrapedListing] = []
        for loader in (self._legacy_html, self._search_api):
            try:
                legacy_rows = loader(city, postal_code)
            except Exception as exc:  # pragma: no cover - defensive
                log.info("seloger fallback %s failed for %s/%s: %s", loader.__name__, city, postal_code, exc)
                legacy_rows = []
            if legacy_rows:
                break

        for row in merge_listings(firecrawl_rows, legacy_rows):
            yield row
