"""FlashProxy customer API: generate and cache an existing plan's proxy route.

No purchases/renewals. A failed enabled proxy never silently becomes direct.
Only get_status() is exposed to admins; it contains no credentials.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from threading import Lock
from urllib.parse import quote, unquote, urlsplit

import requests

from config import settings
from utils.fetch_diagnostics import note_problem

log = logging.getLogger(__name__)
_API = "https://www.flashproxy.com/api/v1"


class FlashProxyUnavailable(RuntimeError):
    pass


def configuration_id() -> str:
    # Used only in cache keys. A key or proxy change invalidates direct HTML.
    values = (settings.flashproxy_enabled, settings.flashproxy_api_key,
              settings.flashproxy_plan_id, settings.flashproxy_country,
              settings.flashproxy_sticky_minutes, settings.proxy_url)
    return hashlib.sha256(repr(values).encode()).hexdigest()[:20]


@dataclass(frozen=True)
class ProxyRoute:
    url: str = field(repr=False)

    @property
    def browser(self) -> dict[str, str]:
        parsed = urlsplit(self.url)
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 1080 if parsed.scheme == "socks5" else 80)
        result = {"server": f"{parsed.scheme}://{host}:{port}"}
        if parsed.username is not None:
            result["username"] = unquote(parsed.username)
            result["password"] = unquote(parsed.password or "")
        return result


def _route(value) -> ProxyRoute:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.port
                or not parsed.username or not parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise FlashProxyUnavailable("Réponse proxy FlashProxy invalide.") from None
    return ProxyRoute(value)


class FlashProxyClient:
    def __init__(self):
        self._lock = Lock()
        self._identity = None
        self._route = None
        self._expires = 0.0
        self._retry_at = 0.0
        self._detail = "Clé API en attente."

    def _reset_if_changed(self):
        identity = configuration_id()
        if identity != self._identity:
            self._identity = identity
            self._route = None
            self._expires = self._retry_at = 0.0
            self._detail = "Prêt à valider la connexion." if settings.flashproxy_api_key else "Clé API en attente."

    def _request(self, method, path, **kwargs):
        try:
            # API credentials go exclusively to FlashProxy, never target pages.
            response = requests.request(method, _API + path,
                headers={"Authorization": f"Bearer {settings.flashproxy_api_key}", "Accept": "application/json"},
                timeout=settings.flashproxy_timeout_seconds, allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise FlashProxyUnavailable("API FlashProxy injoignable.") from None
        if response.status_code == 429:
            try:
                pause = max(60, int(response.headers.get("Retry-After", "60")))
            except (ValueError, TypeError):
                pause = 60
            self._retry_at = time.monotonic() + pause
            raise FlashProxyUnavailable("Limite API FlashProxy atteinte ; réessai différé.")
        messages = {401: "Clé FlashProxy refusée.", 403: "Permission FlashProxy manquante (usage/proxies).",
                    404: "Plan FlashProxy introuvable.", 422: "Plan ou pays FlashProxy incompatible avec la génération de proxies."}
        if response.status_code != 200:
            if response.status_code in {401, 403, 404, 422}:
                self._retry_at = time.monotonic() + 300
            raise FlashProxyUnavailable(messages.get(response.status_code, "API FlashProxy temporairement indisponible."))
        try:
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) and payload.get("success") is True else None
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, TypeError):
            raise FlashProxyUnavailable("Réponse API FlashProxy invalide.") from None

    def route(self) -> ProxyRoute:
        with self._lock:
            self._reset_if_changed()
            if not settings.flashproxy_api_key:
                raise FlashProxyUnavailable("Clé API FlashProxy en attente.")
            if time.monotonic() < self._retry_at:
                raise FlashProxyUnavailable(self._detail)
            if self._route and time.monotonic() < self._expires:
                return self._route
            try:
                plan_id = settings.flashproxy_plan_id
                if not plan_id:
                    data = self._request("GET", "/plans", params={"status": "active", "limit": 100})
                    plans = data.get("plans")
                    if (not isinstance(plans, list) or len(plans) != 1 or not isinstance(plans[0], dict)
                            or not plans[0].get("plan_id") or data.get("total", 1) != 1):
                        raise FlashProxyUnavailable("Renseignez FLASHPROXY_PLAN_ID : un plan actif unique est nécessaire.")
                    plan_id = plans[0]["plan_id"]
                data = self._request("POST", f"/plans/{quote(str(plan_id), safe='')}/proxies", json={
                    "count": 1, "country": settings.flashproxy_country.upper(), "session": "sticky",
                    "sticky_minutes": settings.flashproxy_sticky_minutes, "protocol": "http", "format": "url"})
                proxies = data.get("proxies")
                if not isinstance(proxies, list) or len(proxies) != 1:
                    raise FlashProxyUnavailable("Réponse proxy FlashProxy invalide.")
                route = _route(proxies[0])
                self._route = route
                # Renew before the sticky session expires. One route is shared
                # by the bounded scraper workers and both HTTP/browser stages.
                self._expires = time.monotonic() + max(10, settings.flashproxy_sticky_minutes * 60 - 15)
                self._detail = "Proxy connecté ; session française persistante." if settings.flashproxy_country.upper() == "FR" else "Proxy connecté ; session persistante."
                return route
            except FlashProxyUnavailable as exc:
                self._route = None
                self._detail = str(exc)  # only our fixed messages, never provider payloads
                self._retry_at = max(self._retry_at, time.monotonic() + 60)
                note_problem("flashproxy_unavailable")
                log.warning("%s", self._detail)
                raise

    def status(self) -> dict:
        with self._lock:
            self._reset_if_changed()
            return {"enabled": settings.flashproxy_enabled, "configured": bool(settings.flashproxy_api_key),
                    "connected": bool(self._route and time.monotonic() < self._expires),
                    "country": settings.flashproxy_country.upper(),
                    "detail": self._detail if settings.flashproxy_enabled else "Désactivé ; clé API à configurer avant activation."}


_CLIENT = FlashProxyClient()


def get_proxy_route() -> ProxyRoute | None:
    if settings.flashproxy_enabled:
        return _CLIENT.route()
    return ProxyRoute(settings.proxy_url) if settings.proxy_url else None


def flashproxy_status() -> dict:
    return _CLIENT.status()
