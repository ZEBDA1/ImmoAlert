"""Resolve real portal/commune identifiers instead of guessing search URLs."""
from __future__ import annotations

import re
import unicodedata

from utils.cache import TTLCache
from utils.http import http_client
from utils.scrapling import scrapling_enabled, fetch_json_with_scrapling
from config import settings

_CACHE = TTLCache(86400)

# Native routes verified on the portals on 2026-10-24. Their AD identifiers
# are portal IDs, not French department/INSEE codes; never derive them.
_VERIFIED_PORTAL_ROUTES = {
    ("seloger", "rouen"): "https://www.seloger.com/recherche/location/immobilier/normandie/rouen-76000/ad08fr31632",
    ("seloger", "manteslajolie"): "https://www.seloger.com/recherche/location/immobilier/ile-de-france/mantes-la-jolie-78200/ad08fr32499",
    ("logicimmo", "rouen"): "https://www.logic-immo.com/recherche-immo/location/immobilier/normandie/rouen-76000/ad08fr31632",
    ("logicimmo", "manteslajolie"): "https://www.logic-immo.com/recherche-immo/location/immobilier/ile-de-france/mantes-la-jolie-78200/ad08fr32499",
}


def verified_portal_route(portal: str, city: str | None, postal: str | None) -> str | None:
    name = normalized_name(city)
    if not name:
        name = {"76000": "rouen", "76100": "rouen", "78200": "manteslajolie"}.get(postal, "")
    if postal and postal not in {"76000", "76100", "78200"}:
        return None
    return _VERIFIED_PORTAL_ROUTES.get((portal, name))


def normalized_name(value: str | None) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", text.lower())


def city_slug(value: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def public_json(url: str, params: dict):
    if scrapling_enabled():
        return fetch_json_with_scrapling(url, params)
    if settings.flashproxy_enabled:
        return None
    try:
        response = http_client.get(url, params=params, timeout=10, retries=1, allow_statuses={403,404,429})
        return response.json() if response.status_code == 200 else None
    except Exception:
        return None


def resolve_commune(city: str | None, postal: str | None) -> dict | None:
    key = f"commune:{normalized_name(city)}:{postal or ''}"
    if cached := _CACHE.get(key):
        return cached
    if not city and not postal:
        return None
    params = {"fields": "nom,code,codesPostaux,population", "format": "json", "boost": "population"}
    if postal:
        params["codePostal"] = postal
    else:
        params["nom"] = city
        params["limit"] = "5"
    rows = public_json("https://geo.api.gouv.fr/communes", params)
    if not isinstance(rows, list):
        return None
    candidates = [row for row in rows if isinstance(row, dict) and row.get("code") and row.get("nom")]
    if city:
        candidates = [row for row in candidates if normalized_name(row["nom"]) == normalized_name(city)]
    if not candidates:
        return None
    result = max(candidates, key=lambda row: row.get("population") or 0)
    _CACHE.set(key, result)
    return result


def pap_geo_id(city: str | None, postal: str | None) -> int | None:
    query = city or postal
    if not query:
        return None
    key = f"pap_geo:{normalized_name(query)}"
    if cached := _CACHE.get(key):
        return cached
    rows = public_json("https://www.pap.fr/json/ac-geo", {"q": query})
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int):
            continue
        label = re.sub(r"\s*\([^)]*\)\s*$", "", str(row.get("name") or ""))
        if normalized_name(label) == normalized_name(query):
            _CACHE.set(key, row["id"])
            return row["id"]
    return None
