"""Thin, defensive bridge to the Firecrawl CLI.

Several French real-estate portals (Leboncoin, SeLoger, ...) are protected by
DataDome/Cloudflare-like anti-bot layers. Plain ``requests`` or even Playwright
often get an interstitial page instead of the listing results. The Firecrawl
CLI renders the page with a stealth browser and can return **structured JSON**
through an extraction schema, which makes the scrapers dramatically more
reliable.

This module abstracts the CLI:

* available?      -> :func:`firecrawl_available`
* run a scrape    -> :meth:`FirecrawlClient.scrape_json`
* health/stats    -> :func:`firecrawl_status`

Everything degrades gracefully: if the CLI is missing, disabled, unauthenticated
or times out, callers receive ``None`` and keep their existing HTTP/Playwright
fallback. Nothing here ever raises on a network/browser problem.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Optional

from config import settings
from utils.cache import TTLCache

log = logging.getLogger(__name__)

_CACHE = TTLCache(ttl_seconds=300)


def _env_truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "y", "on"}


#: The CLI only accepts ``auto`` or ``basic``; anything else is coerced.
_VALID_PROXY_MODES = {"auto", "basic"}

#: Set when the keyless free tier answers 429; scrapes short-circuit until then.
_RATE_LIMITED_UNTIL = 0.0


def _resolve_proxy(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    mode = value.strip().lower()
    if mode in _VALID_PROXY_MODES:
        return mode
    # ``stealth`` / ``enhanced`` / ... are not CLI modes: let the server choose.
    return None


def firecrawl_rate_limited() -> bool:
    """True while the free tier is throttling us (avoids wasted calls)."""
    return time.monotonic() < _RATE_LIMITED_UNTIL


#: Markers the CLI uses when the keyless tier is exhausted. The wording is
#: "You've hit Firecrawl's keyless free tier rate limit", which contains no
#: space in "rate limit" in some builds and is printed on *stderr*.
_RATE_LIMIT_MARKERS = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
    "quota exceeded",
    "free tier",
    "429",
)


def _note_rate_limit(stderr: str) -> None:
    global _RATE_LIMITED_UNTIL
    low = (stderr or "").lower()
    if any(marker in low for marker in _RATE_LIMIT_MARKERS):
        _RATE_LIMITED_UNTIL = time.monotonic() + 300
        log.warning("firecrawl rate limited by the free tier; pausing for 5 min")


def firecrawl_cli_path() -> Optional[str]:
    """Return the Firecrawl executable path, or ``None`` when unavailable."""
    explicit = settings.firecrawl_cli_path
    if explicit:
        return shutil.which(explicit)
    return shutil.which("firecrawl")


def firecrawl_available() -> bool:
    """True when the CLI is installed, enabled and not currently throttled."""
    if not settings.firecrawl_enabled or firecrawl_rate_limited():
        return False
    return firecrawl_cli_path() is not None


@dataclass
class FirecrawlStatus:
    installed: bool
    enabled: bool
    authenticated: bool
    credits_remaining: Optional[int] = None
    concurrency: Optional[int] = None
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.installed and self.enabled


class FirecrawlClient:
    """Runs ``firecrawl scrape`` and returns the extracted JSON payload."""

    def __init__(
        self,
        *,
        timeout_seconds: Optional[int] = None,
        cache_ttl_seconds: Optional[int] = None,
        executable: Optional[str] = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds or settings.firecrawl_timeout_seconds
        self.cache = TTLCache(
            ttl_seconds=settings.firecrawl_cache_ttl_seconds if cache_ttl_seconds is None else cache_ttl_seconds
        )
        self.executable = executable

    # -- internals ---------------------------------------------------------
    def _binary(self) -> Optional[str]:
        return self.executable or firecrawl_cli_path()

    @staticmethod
    def _extract_json(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Firecrawl wraps results as ``{"json": {...}, "metadata": {...}}``."""
        data = payload.get("json")
        if data is None:
            inner = payload.get("data")
            if isinstance(inner, dict):
                data = inner.get("json")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (TypeError, ValueError):
                return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _status_code(payload: dict[str, Any]) -> Optional[int]:
        meta = payload.get("metadata")
        if isinstance(meta, dict):
            code = meta.get("statusCode")
            if isinstance(code, int):
                return code
        inner = payload.get("data")
        if isinstance(inner, dict):
            return FirecrawlClient._status_code(inner)
        return None

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        country: Optional[str] = None,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        """Run ``firecrawl search`` and return the raw web results.

        Used to discover the canonical listing URL of a portal (SeLoger in
        particular encodes a ``adXXfrYY`` place id in the path, and hand-built
        URLs without it return ``410 Gone``).
        """
        if not firecrawl_available():
            return []
        query = (query or "").strip()
        if not query:
            return []

        cache_key = f"search|{query}|{int(limit)}|{country or ''}"
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        binary = self._binary()
        if not binary:
            return []

        cmd: list[str] = [binary, "search", query, "--limit", str(int(limit)), "--json"]
        if country:
            cmd.extend(["--country", country])
        api_key = settings.firecrawl_api_key
        if api_key:
            cmd.extend(["--api-key", api_key])

        env = dict(os.environ)
        env.setdefault("NO_COLOR", "1")

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(20, int(self.timeout_seconds) // 2),
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log.warning("firecrawl search timeout for %r", query)
            return []
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("firecrawl search error for %r: %s", query, exc)
            return []

        # The CLI prints quota/auth failures on stderr and still exits 0, so
        # inspecting stderr is the only way to arm the backoff for searches.
        _note_rate_limit(proc.stderr or "")

        raw = (proc.stdout or "").strip()
        if proc.returncode != 0:
            return []
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except ValueError:
            log.warning("firecrawl search returned non-JSON output for %r", query)
            return []
        if not isinstance(payload, dict):
            return []
        if payload.get("success") is False:
            _note_rate_limit(str(payload.get("error", "")))
            return []

        results: list[dict[str, Any]] = []
        data = payload.get("data")
        web = data.get("web") if isinstance(data, dict) else None
        if isinstance(web, list):
            for item in web:
                if isinstance(item, dict) and isinstance(item.get("url"), str):
                    results.append(item)
        elif isinstance(payload.get("web"), list):
            for item in payload["web"]:
                if isinstance(item, dict) and isinstance(item.get("url"), str):
                    results.append(item)

        if use_cache:
            self.cache.set(cache_key, results)
        return results

    def fetch_markdown(
        self,
        url: str,
        *,
        wait_for_ms: Optional[int] = None,
        only_main_content: bool = True,
        max_age_ms: Optional[int] = None,
        use_cache: bool = True,
        country: Optional[str] = None,
        proxy: Optional[str] = None,
    ) -> Optional[str]:
        """Scrape *url* and return its markdown rendering, or ``None``.

        Fallback for pages where structured extraction yields nothing: portal
        SERPs still expose their listing cards as markdown links.
        """
        if not firecrawl_available():
            return None
        if not url or not url.startswith(("http://", "https://")):
            return None

        cache_key = f"md|{url}"
        if use_cache:
            cached = self.cache.get(cache_key)
            if isinstance(cached, str):
                return cached

        binary = self._binary()
        if not binary:
            return None

        cmd: list[str] = [binary, "scrape", url, "--format", "markdown", "--json"]
        if only_main_content:
            cmd.append("--only-main-content")
        if wait_for_ms:
            cmd.extend(["--wait-for", str(int(wait_for_ms))])
        if max_age_ms is not None:
            cmd.extend(["--max-age", str(int(max_age_ms))])
        if country:
            cmd.extend(["--country", country])
        proxy_mode = _resolve_proxy(proxy)
        if proxy_mode:
            cmd.extend(["--proxy", proxy_mode])
        api_key = settings.firecrawl_api_key
        if api_key:
            cmd.extend(["--api-key", api_key])

        env = dict(os.environ)
        env.setdefault("NO_COLOR", "1")

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log.warning("firecrawl markdown timeout after %ss for %s", self.timeout_seconds, url)
            return None
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("firecrawl markdown error for %s: %s", url, exc)
            return None

        _note_rate_limit(proc.stderr or "")
        raw = (proc.stdout or "").strip()
        if proc.returncode != 0 or not raw:
            return None

        markdown = ""
        try:
            payload = json.loads(raw)
        except ValueError:
            markdown = raw  # older CLI builds print raw markdown
        else:
            if isinstance(payload, dict):
                if payload.get("success") is False:
                    _note_rate_limit(str(payload.get("error", "")))
                    return None
                code = self._status_code(payload)
                if code is not None and code >= 400:
                    if code == 429:
                        _note_rate_limit("HTTP 429")
                    log.info("firecrawl markdown http %s for %s", code, url)
                    return None
                for key in ("markdown", "content"):
                    value = payload.get(key)
                    if isinstance(value, str) and value.strip():
                        markdown = value
                        break
                if not markdown:
                    data = payload.get("data")
                    if isinstance(data, dict):
                        for key in ("markdown", "content"):
                            value = data.get(key)
                            if isinstance(value, str) and value.strip():
                                markdown = value
                                break
                    elif isinstance(data, str):
                        markdown = data

        markdown = markdown.strip()
        if not markdown:
            return None
        if use_cache:
            self.cache.set(cache_key, markdown)
        return markdown

    def scrape_json(
        self,
        url: str,
        schema: Optional[dict[str, Any]] = None,
        *,
        prompt: Optional[str] = None,
        query: Optional[str] = None,
        wait_for_ms: Optional[int] = None,
        only_main_content: bool = True,
        max_age_ms: Optional[int] = None,
        use_cache: bool = True,
        country: Optional[str] = None,
        proxy: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
    ) -> Optional[dict[str, Any]]:
        """Scrape *url* and return the structured JSON object, or ``None``.

        ``schema`` is a JSON-schema; when omitted, ``query`` (alias ``prompt``)
        drives the extraction. Nothing is scraped when neither is supplied,
        because raw markdown has its own entry point (:meth:`fetch_markdown`).
        """
        if not firecrawl_available():
            return None
        if not url or not url.startswith(("http://", "https://")):
            return None
        query = query or prompt
        if schema is None and not query:
            return None

        cache_key = f"{url}|{json.dumps({'schema': schema, 'prompt': query}, sort_keys=True)}"
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        binary = self._binary()
        if not binary:
            return None

        tmp_path = ""
        schema_path = ""
        try:
            fd, tmp_path = tempfile.mkstemp(prefix="firecrawl-", suffix=".json")
            os.close(fd)

            cmd: list[str] = [binary, "scrape", url, "--format", "json", "--json", "-o", tmp_path]
            if only_main_content:
                cmd.append("--only-main-content")
            if wait_for_ms:
                cmd.extend(["--wait-for", str(int(wait_for_ms))])
            if max_age_ms is not None:
                cmd.extend(["--max-age", str(int(max_age_ms))])
            if country:
                cmd.extend(["--country", country])
            proxy_mode = _resolve_proxy(proxy)
            if proxy_mode:
                cmd.extend(["--proxy", proxy_mode])
            # CLI 1.26 uses --query for a separate question/answer format.
            # JSON extraction always needs a schema, including prompt-only
            # fallbacks. Its description carries the extraction instruction.
            extraction_schema = dict(schema) if schema is not None else {"type": "object"}
            if query:
                extraction_schema["description"] = "\n".join(
                    part for part in (extraction_schema.get("description"), query) if part
                )
            schema_fd, schema_path = tempfile.mkstemp(prefix="firecrawl-schema-", suffix=".json")
            with os.fdopen(schema_fd, "w", encoding="utf-8") as fh:
                json.dump(extraction_schema, fh, ensure_ascii=False)
            cmd.extend(["--schema-file", schema_path])
            if extra_args:
                cmd.extend(extra_args)

            api_key = settings.firecrawl_api_key
            if api_key:
                cmd.extend(["--api-key", api_key])

            env = dict(os.environ)
            env.setdefault("NO_COLOR", "1")
            env.pop("FIRECRAWL_NO_ENDPOINT_FEEDBACK", None)

            started = time.monotonic()
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    env=env,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                log.warning("firecrawl timeout after %ss for %s", self.timeout_seconds, url)
                return None
            finally:
                if schema_path:
                    try:
                        os.unlink(schema_path)
                    except OSError:
                        pass

            _note_rate_limit(proc.stderr or "")
            if proc.returncode != 0:
                stderr = (proc.stderr or "").strip()[:300]
                if "Not authenticated" in stderr or "Invalid token" in stderr:
                    log.warning("firecrawl not authenticated: %s", stderr)
                elif "rate limit" in stderr.lower():
                    log.info("firecrawl free tier throttled; falling back to legacy path")
                else:
                    log.warning("firecrawl failed rc=%s for %s: %s", proc.returncode, url, stderr)
                return None

            raw = ""
            try:
                with open(tmp_path, "r", encoding="utf-8") as fh:
                    raw = fh.read()
            except OSError:
                pass
            if not raw.strip():
                raw = proc.stdout or ""

            if not raw.strip():
                return None

            try:
                payload = json.loads(raw)
            except ValueError:
                log.warning("firecrawl returned non-JSON payload for %s", url)
                return None
            if not isinstance(payload, dict):
                return None
            if payload.get("success") is False:
                _note_rate_limit(str(payload.get("error", "")))
                return None

            code = self._status_code(payload)
            if code is not None and code >= 400:
                if code == 429:
                    _note_rate_limit("HTTP 429")
                log.info("firecrawl http %s for %s", code, url)
                return None

            data = self._extract_json(payload)
            if data is None:
                log.info("firecrawl returned no structured json for %s", url)
                return None

            elapsed = round(time.monotonic() - started, 2)
            log.debug("firecrawl ok url=%s elapsed=%ss", url, elapsed)
            if use_cache:
                self.cache.set(cache_key, data)
            return data
        except FileNotFoundError:
            log.warning("firecrawl executable not found")
            return None
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("firecrawl unexpected error for %s: %s", url, exc)
            return None
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


_CLIENT: Optional[FirecrawlClient] = None


def get_client() -> FirecrawlClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = FirecrawlClient()
    return _CLIENT


def firecrawl_status() -> FirecrawlStatus:
    """Best-effort status; never raises and never blocks for long."""
    binary = firecrawl_cli_path()
    if not binary:
        return FirecrawlStatus(
            installed=False,
            enabled=settings.firecrawl_enabled,
            authenticated=False,
            detail="CLI firecrawl introuvable (npm i -g firecrawl-cli)",
        )
    try:
        env = dict(os.environ)
        if settings.firecrawl_api_key:
            env["FIRECRAWL_API_KEY"] = settings.firecrawl_api_key
        proc = subprocess.run(
            [binary, "--status"],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
            check=False,
        )
        out = re.sub(r"\x1b\[[0-9;]*m", "", f"{proc.stdout}\n{proc.stderr}")
    except Exception as exc:
        return FirecrawlStatus(
            installed=True,
            enabled=settings.firecrawl_enabled,
            authenticated=False,
            detail=f"status indisponible: {exc}",
        )

    if proc.returncode != 0:
        return FirecrawlStatus(
            installed=True,
            enabled=settings.firecrawl_enabled,
            authenticated=False,
            detail=f"statut indisponible (code {proc.returncode})",
        )
    authenticated = bool(re.search(r"\bAuthenticated\b", out)) and "Not authenticated" not in out
    credits = None
    concurrency = None
    for line in out.splitlines():
        low = line.lower()
        if "credit" in low and (match := re.search(r"credits?\s*:\s*([\d, ]+)", low)):
            credits = int(re.sub(r"\D", "", match.group(1)))
        if "concurren" in low and (match := re.search(r"(\d+)\s*/\s*(\d+)", line)):
            concurrency = int(match.group(2))

    return FirecrawlStatus(
        installed=True,
        enabled=settings.firecrawl_enabled,
        authenticated=authenticated,
        credits_remaining=credits,
        concurrency=concurrency,
        detail="ok" if authenticated else "authentification requise (firecrawl login)",
    )
