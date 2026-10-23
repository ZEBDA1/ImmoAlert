"""Leboncoin rental scraper.

Leboncoin serves its result pages through a heavily protected Next.js app
(DataDome). A plain ``requests`` call or even Playwright usually receives an
interstitial, which is why the previous selector-based implementation silently
returned nothing.

The primary path now goes through the **Firecrawl** CLI: it renders the page in
a stealth browser and returns a JSON payload shaped by
:data:`scraper.firecrawl_helpers.LISTING_SCHEMA`. The historical HTML / Next.js
parsing is kept as a last-resort fallback so the source degrades gracefully when
Firecrawl is disabled or out of credits.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Iterable, Optional

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedListing
from scraper.firecrawl_helpers import (
    LISTING_PROMPT,
    LISTING_SCHEMA,
    build_leboncoin_urls,
    merge_listings,
    payload_to_listings,
)
from utils.firecrawl import firecrawl_available, get_client
from utils.hash import hash_str
from utils.http import fetch_html, http_client
from utils.text import parse_int, parse_surface_m2

log = logging.getLogger(__name__)

_MAX_LISTINGS = 120
_MAX_PAGES_FIRECRAWL = 3


class LeboncoinScraper(BaseScraper):
    source = "leboncoin"
    uses_browser = True

    # -- timeout -----------------------------------------------------------
    @property
    def timeout_seconds(self) -> Optional[int]:
        # A stealth render plus JSON extraction: allow a generous window.
        return max(45, self._firecrawl_budget())

    def _firecrawl_budget(self) -> int:
        from config import settings

        pages = min(settings.firecrawl_max_pages, _MAX_PAGES_FIRECRAWL)
        per_page = settings.firecrawl_timeout_seconds
        return int(per_page * pages * 0.75) + 20

    # -- URL builders ------------------------------------------------------
    def _candidate_urls(self, city: Optional[str], postal_code: Optional[str]) -> list[str]:
        return build_leboncoin_urls(city, postal_code)

    # -- Firecrawl path ----------------------------------------------------
    def _fetch_with_firecrawl(self, city: Optional[str], postal_code: Optional[str]) -> list[ScrapedListing]:
        if not firecrawl_available():
            return []
        from config import settings

        client = get_client()
        collected: list[ScrapedListing] = []
        max_pages = max(1, min(settings.firecrawl_max_pages, _MAX_PAGES_FIRECRAWL))

        for page in range(1, max_pages + 1):
            urls = build_leboncoin_urls(city, postal_code, page=page if page > 1 else None)
            if not urls:
                break

            page_results: list[ScrapedListing] = []
            for url in urls[:1]:  # the "locations" query is the precise one
                payload = client.scrape_json(
                    url,
                    LISTING_SCHEMA,
                    prompt=LISTING_PROMPT,
                    wait_for_ms=settings.firecrawl_wait_for_ms,
                    country=settings.firecrawl_country,
                    proxy=settings.firecrawl_proxy,
                )
                if payload:
                    page_results = payload_to_listings(
                        payload,
                        source=self.source,
                        fallback_city=city,
                        fallback_postal=postal_code,
                    )
                if not page_results:
                    # Schema extraction can come back empty on a redesigned page;
                    # try the prompt-only mode before giving up on this page.
                    payload = client.scrape_json(
                        url,
                        prompt=LISTING_PROMPT,
                        wait_for_ms=settings.firecrawl_wait_for_ms,
                        country=settings.firecrawl_country,
                        proxy=settings.firecrawl_proxy,
                        use_cache=False,
                    )
                    if payload:
                        page_results = payload_to_listings(
                            payload,
                            source=self.source,
                            fallback_city=city,
                            fallback_postal=postal_code,
                        )
                if page_results:
                    break

            if not page_results:
                log.info("leboncoin: no firecrawl results for %s/%s page %s", city, postal_code, page)
                break

            collected.extend(page_results)
            if len(collected) >= _MAX_LISTINGS:
                break

        return collected[:_MAX_LISTINGS]

    # -- legacy HTML fallback ---------------------------------------------
    def _legacy_fetch(self, city: Optional[str], postal_code: Optional[str]) -> list[ScrapedListing]:
        """Old selector-based path, kept as a fallback only."""
        cookie = os.getenv("LEBONCOIN_COOKIE", "").strip()
        extra_headers = {"Cookie": cookie} if cookie else None
        results: list[ScrapedListing] = []

        for base_url in self._candidate_urls(city, postal_code):
            for page in range(1, 4):
                url = f"{base_url}&page={page}" if "?" in base_url else f"{base_url}?page={page}"
                html: Optional[str] = None
                try:
                    resp = http_client.get(
                        url,
                        referer="https://www.leboncoin.fr/",
                        headers=extra_headers,
                        retries=1,
                        allow_statuses={403, 404, 429},
                    )
                    if resp.status_code == 200:
                        html = resp.text
                except Exception:
                    html = None

                if not html:
                    html = fetch_html(url, referer="https://www.leboncoin.fr/")
                if not html:
                    break

                soup = BeautifulSoup(html, "html.parser")
                cards = (
                    soup.select("a[data-qa-id='aditem_container']")
                    or soup.select("a[data-test-id='ad-card-link']")
                    or soup.select("a[href*='/ad/']")
                    or soup.select("a[href*='/vi/']")
                )
                if cards:
                    for card in cards:
                        listing = self._listing_from_anchor(card)
                        if listing:
                            results.append(listing)
                else:
                    results.extend(self._listings_from_next_data(soup))

                if not results:
                    break
            if results:
                break

        return results[:_MAX_LISTINGS]

    def _listing_from_anchor(self, anchor) -> Optional[ScrapedListing]:
        href = (anchor.get("href") or "").strip()
        if not href:
            return None
        if href.startswith("/"):
            href = "https://www.leboncoin.fr" + href

        title_node = (
            anchor.select_one("p[data-qa-id='aditem_title']")
            or anchor.select_one("p[data-test-id='ad-title']")
            or anchor.find("h2")
        )
        price_node = (
            anchor.select_one("span[data-qa-id='aditem_price']")
            or anchor.select_one("p[data-test-id='price']")
            or anchor.find(string=lambda s: s and "€" in s)
        )
        loc_node = anchor.select_one("p[data-qa-id='aditem_location']") or anchor.select_one(
            "p[data-test-id='location']"
        )
        desc_node = anchor.select_one("p[data-qa-id='aditem_description']") or anchor.select_one(
            "p[data-test-id='ad-description']"
        )

        def _text(node) -> Optional[str]:
            if node is None:
                return None
            if hasattr(node, "get_text"):
                return node.get_text(" ", strip=True) or None
            return str(node).strip() or None

        title = _text(title_node)
        price_text = _text(price_node)
        location = _text(loc_node)
        description = _text(desc_node)

        price = parse_int(price_text)

        # The ``a[href*='/ad/']`` selector is deliberately broad and also picks
        # up navigation links that happen to contain ``/ad/``. A real card always
        # exposes at least a title or a price; anything else is noise and would
        # otherwise be persisted as an unusable listing with no signal at all.
        if not title and price is None:
            return None

        surface = parse_surface_m2(" ".join(filter(None, (title, description))))

        raw_id = href.rstrip("/").split("/")[-1].split("?")[0]
        external_id = raw_id if raw_id else hash_str(href or (title or ""))

        return ScrapedListing(
            source=self.source,
            external_id=external_id,
            url=href,
            title=title,
            price=price,
            surface_m2=surface,
            location=location,
            description=description,
        )

    def _listings_from_next_data(self, soup: BeautifulSoup) -> list[ScrapedListing]:
        script = soup.find("script", id="__NEXT_DATA__")
        if script is None or not script.string:
            return []
        try:
            data = json.loads(script.string)
        except (TypeError, ValueError):
            return []

        props = data.get("props", {}).get("pageProps", {}) if isinstance(data, dict) else {}
        ads = None
        if isinstance(props, dict):
            search_data = props.get("searchData")
            if isinstance(search_data, dict):
                ads = search_data.get("ads")
            if ads is None:
                initial = props.get("initialState")
                if isinstance(initial, dict):
                    ads_state = initial.get("ads")
                    if isinstance(ads_state, dict):
                        ads = ads_state.get("ads")
        if not isinstance(ads, list):
            return []

        listings: list[ScrapedListing] = []
        for ad in ads[:_MAX_LISTINGS]:
            if not isinstance(ad, dict):
                continue
            href = ad.get("url") or ad.get("link")
            if not isinstance(href, str) or not href:
                continue
            if href.startswith("/"):
                href = "https://www.leboncoin.fr" + href
            pricing = ad.get("pricing") if isinstance(ad.get("pricing"), dict) else {}
            price = ad.get("price") or pricing.get("price") or pricing.get("amount")
            attr = ad.get("attributes") if isinstance(ad.get("attributes"), list) else []
            surface = None
            rooms = None
            for item in attr:
                if not isinstance(item, dict):
                    continue
                key = (item.get("key") or "").lower()
                value = item.get("value") or item.get("value_label")
                if key in {"square", "surface"} and surface is None:
                    surface = parse_surface_m2(str(value))
                if key == "rooms" and rooms is None:
                    rooms = parse_int(str(value))
            listings.append(
                ScrapedListing(
                    source=self.source,
                    external_id=str(ad.get("list_id") or ad.get("id") or hash_str(href)),
                    url=href,
                    title=(ad.get("subject") or ad.get("title") or None),
                    price=parse_int(str(price)) if price is not None else None,
                    surface_m2=surface,
                    location=ad.get("location_label") or ad.get("city"),
                    rooms=rooms,
                )
            )
        return listings

    # -- public API --------------------------------------------------------
    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        firecrawl_rows: list[ScrapedListing] = []
        try:
            firecrawl_rows = self._fetch_with_firecrawl(city, postal_code)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("leboncoin firecrawl path failed for %s/%s: %s", city, postal_code, exc)

        if len(firecrawl_rows) >= 5:
            for row in firecrawl_rows:
                yield row
            return

        legacy_rows: list[ScrapedListing] = []
        try:
            legacy_rows = self._legacy_fetch(city, postal_code)
        except Exception as exc:  # pragma: no cover - defensive
            log.info("leboncoin legacy path failed for %s/%s: %s", city, postal_code, exc)

        merged = merge_listings(firecrawl_rows, legacy_rows)
        for row in merged:
            yield row
