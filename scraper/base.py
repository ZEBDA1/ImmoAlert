from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional


@dataclass
class ScrapedListing:
    source: str
    external_id: str
    url: str
    title: Optional[str] = None
    price: Optional[int] = None
    surface_m2: Optional[float] = None
    price_per_m2: Optional[float] = None
    location: Optional[str] = None
    rooms: Optional[int] = None
    description: Optional[str] = None
    images: Optional[list[str]] = None
    published_at: Optional[datetime] = None
    db_id: Optional[int] = None


class BaseScraper:
    source: str

    #: True when the scraper drives a headless browser (Firecrawl). The
    #: scheduler grants such sources a longer per-location timeout because a
    #: stealth render easily takes 10-30 seconds.
    uses_browser: bool = False

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        raise NotImplementedError

    @property
    def timeout_seconds(self) -> Optional[int]:
        """Override the scheduler timeout for this source, or ``None``."""
        return None
