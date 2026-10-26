"""FlashProxy customer API: cache and alternate up to two existing plan routes.

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


def provider_enabled(provider):
    from services.provider_credentials import provider_enabled as resolve
    return resolve(provider)


def _api_key() -> str | None:
    from services.provider_credentials import runtime_keys
    keys = runtime_keys("flashproxy")
    return keys[0] if keys else None


def _plan_ids() -> tuple[str, ...]:
    from services.provider_credentials import flashproxy_plan_ids
    return flashproxy_plan_ids()


class FlashProxyUnavailable(RuntimeError):
    def __init__(self,message,*,retry_after=60):
        super().__init__(message)
        self.retry_after=retry_after


def configuration_id() -> str:
    # Used only in cache keys. A key or proxy change invalidates direct HTML.
    values = (provider_enabled("flashproxy"), _api_key(),
              _plan_ids(), settings.flashproxy_country,
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


@dataclass
class _PlanSession:
    route: ProxyRoute | None = None
    expires: float = 0
    retry_at: float = 0
    detail: str = "Connexion en attente."


class FlashProxyClient:
    def __init__(self):
        self._lock = Lock()
        self._identity = None
        self._api_key = None
        self._sessions = {}
        self._cursor = 0
        self._automatic_plan = None
        self._retry_at = 0.0
        self._detail = "Clé API en attente."

    def _reset_if_changed(self):
        identity = configuration_id()
        if identity != self._identity:
            self._identity = identity
            self._api_key = _api_key()
            self._sessions = {}
            self._cursor = 0
            self._automatic_plan = None
            self._retry_at = 0.0
            self._detail = "Prêt à valider la connexion." if self._api_key else "Clé API en attente."

    def _request(self, method, path, **kwargs):
        from services.provider_credentials import record_key_use
        try:
            # API credentials go exclusively to FlashProxy, never target pages.
            response = requests.request(method, _API + path,
                headers={"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"},
                timeout=settings.flashproxy_timeout_seconds, allow_redirects=False, **kwargs)
        except requests.RequestException:
            record_key_use("flashproxy", self._api_key, "error")
            raise FlashProxyUnavailable("API FlashProxy injoignable.") from None
        if response.status_code == 429:
            record_key_use("flashproxy", self._api_key, "rate_limited")
            try:
                pause = max(60, int(response.headers.get("Retry-After", "60")))
            except (ValueError, TypeError):
                pause = 60
            self._retry_at = time.monotonic() + pause
            raise FlashProxyUnavailable("Limite API FlashProxy atteinte ; réessai différé.")
        messages = {402: "Quota du plan FlashProxy épuisé.", 401: "Clé FlashProxy refusée.", 403: "Permission FlashProxy manquante (usage/proxies).",
                    404: "Plan FlashProxy introuvable.", 422: "Plan ou pays FlashProxy incompatible avec la génération de proxies."}
        if response.status_code != 200:
            record_key_use("flashproxy", self._api_key, "invalid" if response.status_code in {401, 403} else "exhausted" if response.status_code==402 else "error")
            if response.status_code == 401:
                self._retry_at = time.monotonic() + 300
            raise FlashProxyUnavailable(messages.get(response.status_code, "API FlashProxy temporairement indisponible."),retry_after=300 if response.status_code in {401,402,403,404,422} else 60)
        try:
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) and payload.get("success") is True else None
            if not isinstance(data, dict):
                raise ValueError
            record_key_use("flashproxy", self._api_key)
            return data
        except (ValueError, TypeError):
            record_key_use("flashproxy", self._api_key, "error")
            raise FlashProxyUnavailable("Réponse API FlashProxy invalide.") from None

    def _automatic(self):
        """Automatic mode remains explicit: never guess among several plans."""
        now=time.monotonic()
        existing=self._sessions.get(self._automatic_plan)
        if existing and (existing.expires>now or existing.retry_at>now):return (self._automatic_plan,)
        if now<self._retry_at:raise FlashProxyUnavailable(self._detail)
        try:
            data=self._request("GET","/plans",params={"status":"active","limit":100})
            plans=data.get("plans")
            if (not isinstance(plans,list) or len(plans)!=1 or not isinstance(plans[0],dict)
                    or not isinstance(plans[0].get("plan_id"),str) or not plans[0]["plan_id"] or data.get("total",1)!=1):
                raise FlashProxyUnavailable("Renseignez vos plans FlashProxy dans l’administration (FLASHPROXY_PLAN_ID) : le mode automatique nécessite un plan actif unique.")
            self._automatic_plan=plans[0]["plan_id"]
            self._sessions={self._automatic_plan:existing or _PlanSession()}
            return (self._automatic_plan,)
        except FlashProxyUnavailable as exc:
            self._detail=str(exc);self._retry_at=max(self._retry_at,now+exc.retry_after)
            note_problem("flashproxy_unavailable");log.warning("%s",self._detail)
            raise

    def route(self) -> ProxyRoute:
        # Serialize only route allocation. Scrapers use the returned cached
        # routes concurrently; each plan retains its own sticky session.
        with self._lock:
            self._reset_if_changed()
            if not self._api_key:raise FlashProxyUnavailable("Clé API FlashProxy en attente.")
            plans=_plan_ids() or self._automatic()
            start=self._cursor%len(plans);self._cursor=(start+1)%len(plans)
            candidates=plans[start:]+plans[:start]
            detail=self._detail
            for plan_id in candidates:
                slot=self._sessions.setdefault(plan_id,_PlanSession());now=time.monotonic()
                if slot.route and now<slot.expires:
                    self._detail="Connexion FlashProxy disponible ; plans utilisés en alternance." if len(plans)>1 else "Proxy connecté ; session persistante."
                    return slot.route
                if now<self._retry_at:continue
                if now<slot.retry_at:
                    detail=slot.detail;continue
                try:
                    data=self._request("POST",f"/plans/{quote(plan_id,safe='')}/proxies",json={
                        "count":1,"country":settings.flashproxy_country.upper(),"session":"sticky",
                        "sticky_minutes":settings.flashproxy_sticky_minutes,"protocol":"http","format":"url"})
                    proxies=data.get("proxies")
                    if not isinstance(proxies,list) or len(proxies)!=1:raise FlashProxyUnavailable("Réponse proxy FlashProxy invalide.")
                    slot.route=_route(proxies[0]);slot.retry_at=0
                    slot.expires=time.monotonic()+max(10,settings.flashproxy_sticky_minutes*60-15)
                    self._detail="Proxy connecté ; deux plans utilisés en alternance." if len(plans)>1 else "Proxy connecté ; session française persistante." if settings.flashproxy_country.upper()=="FR" else "Proxy connecté ; session persistante."
                    return slot.route
                except FlashProxyUnavailable as exc:
                    slot.route=None;slot.expires=0;slot.detail=str(exc)
                    slot.retry_at=time.monotonic()+exc.retry_after;detail=str(exc)
                    if self._retry_at>now:self._detail=detail
                    note_problem("flashproxy_unavailable");log.warning("%s",detail)
            self._detail=detail
            raise FlashProxyUnavailable(detail)

    def status(self) -> dict:
        with self._lock:
            self._reset_if_changed()
            return {"enabled": provider_enabled("flashproxy"), "configured": bool(self._api_key),
                    "connected": any(slot.route and time.monotonic()<slot.expires for slot in self._sessions.values()),
                    "country": settings.flashproxy_country.upper(),
                    "detail": self._detail if provider_enabled("flashproxy") else "Désactivé ; clé API à configurer avant activation."}


_CLIENT = FlashProxyClient()


def get_proxy_route() -> ProxyRoute | None:
    if provider_enabled("flashproxy"):
        return _CLIENT.route()
    return ProxyRoute(settings.proxy_url) if settings.proxy_url else None


def flashproxy_status() -> dict:
    return _CLIENT.status()
