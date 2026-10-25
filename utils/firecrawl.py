"""Firecrawl backup renderer and structured extraction via the installed CLI.

The credential pool retries refused/exhausted keys within one request deadline.
A provider rate limit pauses all keys. Credentials are supplied only through the
child process environment; provider error bodies are never logged.
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
import hashlib
from threading import Lock
from dataclasses import dataclass
from typing import Any, Optional

from config import settings
from utils.cache import TTLCache
from utils.fetch_diagnostics import note_problem

log = logging.getLogger(__name__)

_CACHE = TTLCache(ttl_seconds=300)


def _env_truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "y", "on"}


#: The CLI only accepts ``auto`` or ``basic``; anything else is coerced.
_VALID_PROXY_MODES = {"auto", "basic"}

#: Set when the keyless free tier answers 429; scrapes short-circuit until then.
_RATE_LIMITED_UNTIL = 0.0


_KEY_LOCK = Lock()
_KEY_CURSOR = 0
_KEY_PAUSES: dict[str, tuple[float, str]] = {}


def configured_keys() -> tuple[str, ...]:
    return tuple(dict.fromkeys(key.strip() for key in (*settings.firecrawl_api_keys, settings.firecrawl_api_key or "") if key.strip()))


def _key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _key_candidates() -> list[str | None]:
    global _KEY_CURSOR
    keys = configured_keys()
    if not keys:
        return [None]  # preserve an existing CLI login/keyless installation
    with _KEY_LOCK:
        offset = _KEY_CURSOR % len(keys)
        _KEY_CURSOR += 1
        ordered = keys[offset:] + keys[:offset]
        return [key for key in ordered if _KEY_PAUSES.get(_key_id(key), (0, ""))[0] <= time.monotonic()]


def key_pool_status() -> dict:
    keys = configured_keys()
    with _KEY_LOCK:
        paused = sum(_KEY_PAUSES.get(_key_id(key), (0, ""))[0] > time.monotonic() for key in keys)
    return {"keys_configured": len(keys), "keys_paused": paused, "keys_available": len(keys) - paused,
            "rate_limited": firecrawl_rate_limited()}


def _pause_key(key: str | None, reason: str):
    if key:
        with _KEY_LOCK:
            _KEY_PAUSES[_key_id(key)] = (time.monotonic() + (3600 if reason == "auth" else 300), reason)
        note_problem("firecrawl_auth_failed" if reason == "auth" else "firecrawl_quota_exhausted")
        log.warning("Firecrawl credential paused (%s); checking remaining configured credentials", reason)
    else:
        _note_rate_limit("quota exceeded")


def _cli_env(key: str | None) -> dict:
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    for name in tuple(env):
        if name.startswith("FLASHPROXY_") or name == "FIRECRAWL_API_KEYS":
            env.pop(name, None)
    if key:
        env["FIRECRAWL_API_KEY"] = key
    else:
        env.pop("FIRECRAWL_API_KEY", None)
    return env


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
    limited = time.monotonic() < _RATE_LIMITED_UNTIL
    if limited:
        note_problem("firecrawl_quota_exhausted")
    return limited


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
    "insufficient credits",
    "not enough credits",
    "payment required",
    "402",
)


def _note_rate_limit(stderr: str) -> None:
    global _RATE_LIMITED_UNTIL
    low = (stderr or "").lower()
    if any(marker in low for marker in _RATE_LIMIT_MARKERS):
        _RATE_LIMITED_UNTIL = time.monotonic() + 300
        note_problem("firecrawl_quota_exhausted")
        log.warning("Firecrawl quota/credits exhausted; pausing for 5 min")


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
    pool = key_pool_status()
    return firecrawl_cli_path() is not None and (not pool["keys_configured"] or pool["keys_available"] > 0)


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

    def _run(self, cmd, *, timeout, output_path=None):
        """Try eligible credentials within one deadline; never rotate past 429."""
        failed = subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        deadline = time.monotonic() + timeout
        for key in _key_candidates():
            if firecrawl_rate_limited():
                break
            remaining = deadline - time.monotonic()
            if remaining < 1:
                break
            if output_path:
                with open(output_path, "w", encoding="utf-8"):
                    pass
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=remaining,
                                      env=_cli_env(key), check=False)
            except Exception as exc:
                log.warning("Firecrawl transport failed (%s)", type(exc).__name__)
                return failed
            raw = proc.stdout or ""
            if output_path:
                try:
                    with open(output_path, encoding="utf-8") as stream:
                        raw = stream.read() or raw
                except OSError:
                    pass
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            provider_error = payload.get("error", "") if payload.get("success") is False else ""
            message = f"{proc.stderr or ''} {provider_error}".lower()
            code = self._status_code(payload)
            if code == 429 or any(m in message for m in ("rate limit", "rate_limit", "ratelimit", "too many requests", "free tier", "429")):
                # The CLI exposes no Retry-After header. Pause every key for
                # 5 minutes; credential rotation must not bypass rate limits.
                _note_rate_limit("HTTP 429")
                return failed
            if code == 402 or any(m in message for m in ("insufficient credits", "not enough credits", "payment required", "402", "quota exceeded")):
                _pause_key(key, "credits")
                continue
            if code in {401, 403} or any(m in message for m in ("not authenticated", "invalid token", "invalid api key", "unauthorized", "401", "403")):
                _pause_key(key, "auth")
                continue
            if code is not None and code >= 400:
                return failed
            if proc.returncode != 0 or payload.get("success") is False:
                return failed
            return subprocess.CompletedProcess(cmd, 0, stdout=proc.stdout, stderr="")
        return failed

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

        try:
            proc = self._run(cmd, timeout=max(20, int(self.timeout_seconds) // 2))
        except subprocess.TimeoutExpired:
            log.warning("firecrawl search timeout for %r", query)
            return []
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("firecrawl search error for %r: %s", query, type(exc).__name__)
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

        try:
            proc = self._run(cmd, timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            log.warning("firecrawl markdown timeout after %ss for %s", self.timeout_seconds, url)
            return None
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("firecrawl markdown error for %s: %s", url, type(exc).__name__)
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

    def fetch_html(self, url: str, *, use_cache=True) -> Optional[str]:
        if not firecrawl_available() or not url.startswith(("http://", "https://")):
            return None
        cache_key = f"rawhtml|{url}"
        if use_cache and (cached := self.cache.get(cache_key)):
            return cached
        binary = self._binary()
        if not binary:
            return None
        cmd = [binary, "scrape", url, "--format", "rawHtml", "--json",
               "--wait-for", str(settings.firecrawl_wait_for_ms), "--country", settings.firecrawl_country]
        if proxy := _resolve_proxy(settings.firecrawl_proxy):
            cmd.extend(["--proxy", proxy])
        proc = self._run(cmd, timeout=self.timeout_seconds)
        if proc.returncode != 0:
            return None
        try:
            payload = json.loads(proc.stdout)
            inner = payload.get("data", payload)
            html = inner.get("rawHtml") or inner.get("html")
            if isinstance(html, str) and html.strip():
                if use_cache:
                    self.cache.set(cache_key, html)
                return html
        except (ValueError, TypeError, AttributeError):
            pass
        return None

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
                if any(arg in {"--api-key", "--api-url"} or arg.startswith(("--api-key=", "--api-url=")) for arg in extra_args):
                    return None
                cmd.extend(extra_args)


            started = time.monotonic()
            try:
                proc = self._run(cmd, timeout=self.timeout_seconds, output_path=tmp_path)
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
                log.info("Firecrawl backup unavailable for %s", url)
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
            log.warning("firecrawl unexpected error for %s: %s", url, type(exc).__name__)
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
        candidates = _key_candidates()
        env = _cli_env(candidates[0] if candidates else None)
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
            detail=f"status indisponible: {type(exc).__name__}",
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
