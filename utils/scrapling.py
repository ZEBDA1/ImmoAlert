"""Primary HTML fetcher, controlled by the persisted admin setting."""
from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore
from contextlib import contextmanager

from utils.flashproxy import get_proxy_route, FlashProxyUnavailable, flashproxy_status
from utils.fetch_diagnostics import note_problem, note_event

from config import settings
from database import repo

log = logging.getLogger(__name__)
_BROWSERS = BoundedSemaphore(2)
_SETTING_KEY = "scrapling_enabled"


@contextmanager
def _private_engine_logs():
    token = None
    try:
        from scrapling.core.utils import set_logger
        quiet = logging.getLogger("scrapling.private_transport")
        quiet.propagate = False
        if not quiet.handlers:
            quiet.addHandler(logging.NullHandler())
        token = set_logger(quiet)
    except ImportError:
        pass
    try:
        yield
    finally:
        if token is not None:
            from scrapling.core.utils import reset_logger
            reset_logger(token)


def scrapling_enabled() -> bool:
    value = repo.get_runtime_setting(_SETTING_KEY)
    return settings.scrapling_enabled if value is None else value == "true"


def set_scrapling_enabled(enabled: bool) -> None:
    repo.set_runtime_setting(_SETTING_KEY, "true" if enabled else "false")


def _browser_path() -> str | None:
    if settings.scrapling_browser_path:
        return shutil.which(settings.scrapling_browser_path)
    for name in ("chromium", "chromium-browser", "google-chrome"):
        if path := shutil.which(name):
            return path
    # Also support the Chromium installed by `scrapling install` locally.
    from patchright.sync_api import sync_playwright

    with sync_playwright() as p:
        path = p.chromium.executable_path
    return path if Path(path).is_file() else None


@dataclass(frozen=True)
class ScraplingStatus:
    enabled: bool
    ready: bool
    detail: str


def scrapling_status() -> ScraplingStatus:
    enabled = scrapling_enabled()
    try:
        from scrapling.fetchers import StealthyFetcher  # noqa: F401

        if not _browser_path():
            return ScraplingStatus(enabled, False, "Chromium introuvable ; installer le navigateur Scrapling.")
    except ImportError:
        return ScraplingStatus(enabled, False, "Scrapling ou ses dépendances navigateur ne sont pas installés.")
    except Exception:
        return ScraplingStatus(enabled, False, "Impossible de vérifier le navigateur Scrapling.")
    return ScraplingStatus(enabled, True, "Moteur principal ; navigateur disponible. " + flashproxy_status()["detail"])


@_private_engine_logs()
def fetch_with_scrapling(url: str, *, referer: str | None = None, headers: dict | None = None) -> str | None:
    """Called first from scraper workers; never run on the bot loop."""
    if not scrapling_enabled():
        return None
    from utils.http import is_probably_blocked
    try:
        route = get_proxy_route()
    except FlashProxyUnavailable:
        note_problem("flashproxy_unavailable", url)
        return None

    # TLS/browser impersonation often suffices for normal public HTML pages.
    # Keep it outside the browser semaphore so blocked portals cannot starve
    # other sources that only need this inexpensive HTTP transport.
    try:
        from scrapling.fetchers import Fetcher
        started=time.monotonic()
        response = Fetcher.get(url, impersonate="chrome", timeout=settings.scrapling_timeout_seconds,
            retries=1, proxy=route.url if route else None,
            headers={**({"Referer": referer} if referer else {}), **(headers or {})})
        blocked=is_probably_blocked(response.html_content or "") if response.html_content else False
        note_event("http_blocked" if blocked or response.status in {403,429} else "http_ok" if response.status==200 else "http_error",url,engine="scrapling_http",status=response.status,elapsed=time.monotonic()-started)
        if response.status == 200 and response.html_content and not blocked:
            return response.html_content
        if response.status == 404:
            return None
    except Exception as exc:
        note_event("transport_exception",url,engine="scrapling_http",exception=exc)
        log.info("Scrapling HTTP primary failed (%s)", type(exc).__name__)
    # Limit browser pressure when several sources fail at once.
    if not _BROWSERS.acquire(timeout=2):
        note_event("browser_busy",url,engine="chromium",message="Aucune place disponible pour le navigateur.")
        log.info("Scrapling primary busy")
        return None
    try:
        from scrapling.fetchers import StealthyFetcher

        browser = _browser_path()
        if not browser:
            log.warning("Scrapling primary unavailable: Chromium missing")
            return None
        response = StealthyFetcher.fetch(
            url,
            headless=True,
            executable_path=browser,
            extra_flags=["--no-sandbox", "--disable-dev-shm-usage"],
            timeout=settings.scrapling_timeout_seconds * 1000,
            retries=1,
            wait=1000,
            locale="fr-FR",
            timezone_id="Europe/Paris",
            google_search=False,
            extra_headers={**({"Referer": referer} if referer else {}), **(headers or {})},
            proxy=route.browser if route else None,
        )
        html = response.html_content
        blocked=is_probably_blocked(html or "") if html else False
        note_event("http_blocked" if blocked or response.status in {403,429} else "http_ok" if response.status==200 else "http_error",url,engine="chromium",status=response.status)
        if response.status != 200 or not html or blocked:
            return None
        return html
    except Exception as exc:
        note_event("transport_exception",url,engine="chromium",exception=exc)
        # Do not log exception messages which can include proxy credentials.
        log.warning("Scrapling primary failed (%s)", type(exc).__name__)
        return None
    finally:
        _BROWSERS.release()


@_private_engine_logs()
def fetch_json_with_scrapling(url: str, params: dict):
    if not scrapling_enabled():
        return None
    try:
        from scrapling.fetchers import Fetcher
        route = get_proxy_route()
        response = Fetcher.get(url, params=params, impersonate="chrome", timeout=12, retries=1,
                               proxy=route.url if route else None)
        note_event("http_ok" if response.status==200 else "http_error",url,engine="scrapling_json",status=response.status)
        return response.json() if response.status == 200 else None
    except Exception as exc:
        note_event("json_transport_exception",url,engine="scrapling_json",exception=exc)
        return None
