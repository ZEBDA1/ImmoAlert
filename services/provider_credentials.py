"""Encrypted runtime provider credentials and read-only official usage probes.

Only the internal runtime_keys() function returns secrets. Everything returned
by the admin functions is a whitelist of safe fields. Environment credentials
are imported once on first administrative access; the persistent configuration
marker prevents a deleted pool from falling back to the environment.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import os
from pathlib import Path
import stat
import tempfile
from threading import RLock
from datetime import datetime, timezone
import uuid

import requests
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from config import settings
from database import models as m, repo

_LOCK = RLock()
_MASTER_PATH = Path(__file__).resolve().parents[1] / "storage" / "provider-credentials.key"
_PROVIDERS = {
    "firecrawl": {
        "name": "Firecrawl", "unit": "credits",
        "documentation_url": "https://docs.firecrawl.dev/api-reference/endpoint/credit-usage",
        "detail": "Rendu et extraction de secours. Les clés d’une même équipe partagent leurs crédits.",
    },
    "flashproxy": {
        "name": "FlashProxy", "unit": "GB",
        "documentation_url": "https://www.flashproxy.com/docs/api#usage-of-all-active-plans",
        "detail": "Proxy des collecteurs. Une clé active peut utiliser deux plans en alternance, avec un quota par plan.",
    },
}
_ERRORS = {
    401: ("invalid", "Clé refusée par le fournisseur."),
    403: ("invalid", "Permissions de la clé insuffisantes."),
    402: ("exhausted", "Crédits épuisés chez le fournisseur."),
    429: ("rate_limited", "Limite de requêtes atteinte ; réessai différé."),
}


def _provider(provider):
    if provider not in _PROVIDERS:
        raise ValueError("Fournisseur inconnu.")
    return provider


def environment_keys(provider: str) -> tuple[str, ...]:
    """Internal only; never include this result in HTTP responses."""
    _provider(provider)
    values = (*settings.firecrawl_api_keys, settings.firecrawl_api_key or "") if provider == "firecrawl" else (settings.flashproxy_api_key or "",)
    return tuple(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def _fingerprint(key):
    return hashlib.sha256(key.encode()).hexdigest()


def _mask(key):
    return "•••• " + key[-4:] if len(key) > 4 else "••••"


def _cipher(*, create=False):
    # Import lazily: a disabled provider does not need cryptography at startup.
    from cryptography.fernet import Fernet
    try:
        if create and not _MASTER_PATH.exists():
            _MASTER_PATH.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".provider-key-", dir=_MASTER_PATH.parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, _MASTER_PATH)
                except FileExistsError:
                    pass
            finally:
                os.unlink(temporary)
        descriptor = os.open(_MASTER_PATH, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError
            os.fchmod(stream.fileno(), 0o600)
            key = stream.read(128)
        return Fernet(key)
    except (OSError, ValueError, TypeError):
        raise RuntimeError("Stockage des clés indisponible : vérifier la clé maîtresse privée.") from None


def _encrypt(provider, key_id, api_key, *, create=False):
    payload = json.dumps({"provider": provider, "id": key_id, "key": api_key}, separators=(",", ":")).encode()
    return _cipher(create=create).encrypt(payload).decode()


def _decrypt(row):
    from cryptography.fernet import InvalidToken
    try:
        payload = json.loads(_cipher().decrypt(row.encrypted_key.encode()))
        if payload["provider"] != row.provider or payload["id"] != row.id or _fingerprint(payload["key"]) != row.fingerprint:
            raise ValueError
        return payload["key"]
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise RuntimeError("Clé chiffrée illisible : vérifier la clé maîtresse privée.") from None


def _insert(session, provider, key, *, label, origin="admin", enabled=True):
    key_id = uuid.uuid4().hex
    # Never recreate a missing master file when encrypted rows already exist.
    create = session.execute(select(m.ProviderCredential.id).limit(1)).first() is None
    row = m.ProviderCredential(id=key_id, provider=provider, label=label,
        encrypted_key=_encrypt(provider, key_id, key, create=create),
        fingerprint=_fingerprint(key), masked_key=_mask(key), enabled=enabled, origin=origin,
        quota_json="{}", status="unchecked", detail="Quota pas encore vérifié.")
    session.add(row)
    session.flush()
    return row


def _ensure_managed(session, provider):
    if session.get(m.ProviderConfiguration, provider) is not None:
        return
    keys = environment_keys(provider)
    session.add(m.ProviderConfiguration(provider=provider, keyless_blocked=bool(keys)))
    for index, key in enumerate(keys, 1):
        _insert(session, provider, key, label=f"Clé importée {index}", origin="environment")
    session.flush()


def runtime_keys(provider: str) -> tuple[str, ...]:
    """Resolve fresh credentials at call time. No environment resurrection."""
    _provider(provider)
    try:
        with _LOCK, repo.session_scope() as session:
            if session.get(m.ProviderConfiguration, provider) is None:
                return environment_keys(provider)
            rows = session.execute(select(m.ProviderCredential).where(
                m.ProviderCredential.provider == provider, m.ProviderCredential.enabled.is_(True)
            ).order_by(m.ProviderCredential.created_at, m.ProviderCredential.id)).scalars().all()
            return tuple(_decrypt(row) for row in rows)
    except OperationalError as exc:
        # Legacy callers/tests may run before init_db. A real unavailable DB
        # must fail closed instead of silently reviving revoked credentials.
        if "no such table: provider_configurations" in str(exc):
            return environment_keys(provider)
        raise RuntimeError("Stockage des clés temporairement indisponible.") from None


def managed_empty(provider: str) -> bool:
    """True for an explicitly emptied pool (keyless CLI must stay disabled)."""
    try:
        with repo.session_scope() as session:
            config = session.get(m.ProviderConfiguration, _provider(provider))
            return bool(config and config.keyless_blocked) and session.execute(
                select(m.ProviderCredential.id).where(m.ProviderCredential.provider == provider,
                    m.ProviderCredential.enabled.is_(True)).limit(1)).first() is None
    except OperationalError:
        return False


def provider_enabled(provider: str) -> bool:
    _provider(provider)
    try:
        with _LOCK, repo.session_scope() as session:
            row = session.get(m.ProviderConfiguration, provider)
            return bool(row.enabled) if row and row.enabled is not None else bool(getattr(settings, provider + "_enabled"))
    except OperationalError as exc:
        if "no such table: provider_configurations" in str(exc):
            return bool(getattr(settings, provider + "_enabled"))
        raise RuntimeError("Stockage des clés temporairement indisponible.") from None


def _validate_plan_ids(values):
    if not isinstance(values,list) or len(values)>2:raise ValueError("Renseignez au maximum deux plans FlashProxy.")
    result=[]
    for value in values:
        if not isinstance(value,str):raise ValueError("Chaque plan ID doit être du texte.")
        value=value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}",value):
            raise ValueError("Plan ID invalide : 128 caractères maximum, lettres, chiffres, points, tirets ou soulignements.")
        if value in result:raise ValueError("Les deux plans doivent avoir des IDs différents.")
        result.append(value)
    return tuple(result)


def _environment_plan_ids():
    values=list(settings.flashproxy_plan_ids) or ([settings.flashproxy_plan_id] if settings.flashproxy_plan_id else [])
    return _validate_plan_ids(values)


def _plan_ids(session):
    override=session.get(m.RuntimeSetting,"flashproxy_plan_ids")
    if override is not None:
        try:return _validate_plan_ids(json.loads(override.value))
        except (ValueError,TypeError):raise RuntimeError("Configuration des plans FlashProxy invalide.") from None
    legacy=session.get(m.RuntimeSetting,"flashproxy_plan_id")
    return _validate_plan_ids([legacy.value] if legacy.value else []) if legacy is not None else _environment_plan_ids()


def _plan_id(session):
    return next(iter(_plan_ids(session)),"")


def flashproxy_plan_ids() -> tuple[str, ...]:
    try:
        with _LOCK, repo.session_scope() as session:return _plan_ids(session)
    except OperationalError as exc:
        if "no such table: runtime_settings" in str(exc):return _environment_plan_ids()
        raise RuntimeError("Configuration FlashProxy temporairement indisponible.") from None


def flashproxy_plan_id() -> str:
    """Compatibility for older admin clients. New collectors use the full pair."""
    return next(iter(flashproxy_plan_ids()),"")


def _plan_snapshot(session):
    version=session.get(m.RuntimeSetting,"flashproxy_plan_revision")
    return _plan_ids(session),version.value if version else None


def _write_plan_ids(session,values):
    previous=_plan_ids(session)
    for name,value in (("flashproxy_plan_ids",json.dumps(values)),("flashproxy_plan_id",next(iter(values),""))):
        row=session.get(m.RuntimeSetting,name)
        if row is None:session.add(m.RuntimeSetting(key=name,value=value))
        else:row.value=value
    if previous!=values:
        version=session.get(m.RuntimeSetting,"flashproxy_plan_revision")
        if version is None:session.add(m.RuntimeSetting(key="flashproxy_plan_revision",value=uuid.uuid4().hex))
        else:version.value=uuid.uuid4().hex
        for row in session.scalars(select(m.ProviderCredential).where(m.ProviderCredential.provider=="flashproxy")):
            row.quota_json="{}";row.checked_at=None;row.status="unchecked"
            row.detail="Plans modifiés ; quotas à vérifier."


def save_flashproxy_plans(plan_ids) -> None:
    values=_validate_plan_ids(plan_ids)
    with _LOCK, repo.session_scope() as session:_write_plan_ids(session,values)


def save_flashproxy_plan(plan_id) -> None:
    if not isinstance(plan_id,str):raise ValueError("Le plan ID doit être du texte.")
    values=_validate_plan_ids([plan_id.strip()] if plan_id.strip() else [])
    with _LOCK, repo.session_scope() as session:
        if len(_plan_ids(session))>1:raise ValueError("Deux plans sont configurés. Utilisez le formulaire des deux plans pour les modifier.")
        _write_plan_ids(session,values)


def set_provider_enabled(provider: str, enabled: bool) -> dict:
    _provider(provider)
    if not isinstance(enabled, bool):
        raise ValueError("Le statut du fournisseur doit être un booléen.")
    with _LOCK, repo.session_scope() as session:
        _ensure_managed(session, provider)
        session.get(m.ProviderConfiguration, provider).enabled = enabled
    return next(item for item in list_providers()["providers"] if item["id"] == provider)


def _iso(value):
    return value.isoformat(timespec="seconds") + "Z" if value else None


def _empty_quota(provider):
    return {"remaining": None, "used": None, "total": None, "unit": _PROVIDERS[provider]["unit"], "reset_at": None}


def _safe(row):
    quota = _empty_quota(row.provider)
    stored={}
    try:
        stored = json.loads(row.quota_json or "{}")
        if not isinstance(stored,dict):stored={}
        quota.update({key: stored[key] for key in quota if key in stored})
    except (ValueError, TypeError):
        pass
    return {"id": row.id, "label": row.label, "masked_key": row.masked_key,
        "fingerprint": row.fingerprint[:12], "enabled": row.enabled, "origin": row.origin,
        "status": row.status if row.enabled else "disabled", "detail": row.detail,
        "checked_at": _iso(row.checked_at), "last_used_at": _iso(row.last_used_at), "quota": quota,
        **({"plan_quotas":stored["plans"]} if row.provider=="flashproxy" and isinstance(stored.get("plans"),list) else {})}


def list_providers() -> dict:
    """Admin-only: imports env keys once and returns masked metadata only."""
    result = []
    with _LOCK, repo.session_scope() as session:
        for provider, metadata in _PROVIDERS.items():
            _ensure_managed(session, provider)
            rows = session.execute(select(m.ProviderCredential).where(m.ProviderCredential.provider == provider)
                .order_by(m.ProviderCredential.created_at, m.ProviderCredential.id)).scalars().all()
            config = session.get(m.ProviderConfiguration, provider)
            enabled = config.enabled if config.enabled is not None else bool(getattr(settings, provider + "_enabled"))
            result.append({"id": provider, "name": metadata["name"], "enabled": enabled,
                "managed": True, "detail": metadata["detail"], "documentation_url": metadata["documentation_url"],
                "credentials": [_safe(row) for row in rows],
                **({"plan_id":_plan_id(session),"plan_ids":list(_plan_ids(session))} if provider=="flashproxy" else {})})
    return {"providers": result}


def save_credential(provider: str, *, api_key=None, key_id=None, label=None, enabled=None) -> dict:
    _provider(provider)
    if api_key is not None:
        if not isinstance(api_key, str) or not 8 <= len(api_key.strip()) <= 1024 or any(ord(c) < 33 or ord(c) > 126 for c in api_key.strip()):
            raise ValueError("La clé doit contenir entre 8 et 1 024 caractères sans espace.")
        api_key = api_key.strip()
    if label is not None and (not isinstance(label, str) or not 1 <= len(label.strip()) <= 60 or any(ord(c) < 32 for c in label)):
        raise ValueError("Le nom de la clé doit contenir entre 1 et 60 caractères.")
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError("Le statut de la clé doit être un booléen.")
    with _LOCK, repo.session_scope() as session:
        _ensure_managed(session, provider)
        row = session.get(m.ProviderCredential, key_id) if key_id else None
        if key_id and (row is None or row.provider != provider):
            raise ValueError("Clé introuvable pour ce fournisseur.")
        if api_key:
            duplicate = session.execute(select(m.ProviderCredential).where(m.ProviderCredential.provider == provider,
                m.ProviderCredential.fingerprint == _fingerprint(api_key))).scalar_one_or_none()
            if duplicate and duplicate.id != key_id:
                raise ValueError("Cette clé est déjà enregistrée.")
        if row is None:
            if not api_key:
                raise ValueError("Une clé API est nécessaire.")
            row = _insert(session, provider, api_key, label=(label.strip() if label else "Clé fournisseur"), enabled=enabled is not False)
        else:
            if api_key and row.fingerprint != _fingerprint(api_key):
                row.encrypted_key = _encrypt(provider, row.id, api_key)
                row.fingerprint = _fingerprint(api_key)
                row.masked_key = _mask(api_key)
                row.origin = "admin"
                row.status, row.detail, row.quota_json = "unchecked", "Quota pas encore vérifié.", "{}"
                row.checked_at = row.last_used_at = None
            if label is not None:
                row.label = label.strip()
            if enabled is not None:
                row.enabled = enabled
                if enabled:
                    row.status = "unchecked"
            row.updated_at = datetime.utcnow()
        session.get(m.ProviderConfiguration, provider).keyless_blocked = True
        if provider == "flashproxy" and row.enabled:
            for other in session.execute(select(m.ProviderCredential).where(m.ProviderCredential.provider == provider,
                    m.ProviderCredential.id != row.id)).scalars():
                other.enabled = False
        session.flush()
        return _safe(row)


def delete_credential(provider: str, key_id: str) -> None:
    with _LOCK, repo.session_scope() as session:
        _ensure_managed(session, _provider(provider))
        row = session.get(m.ProviderCredential, key_id)
        if row is None or row.provider != provider:
            raise ValueError("Clé introuvable pour ce fournisseur.")
        session.delete(row)


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if date.tzinfo else None
    except ValueError:
        return None


def _fetch_quota(provider, api_key):
    quota = _empty_quota(provider)
    selected_ids=flashproxy_plan_ids() if provider=="flashproxy" else ()
    endpoint = "https://api.firecrawl.dev/v2/team/credit-usage" if provider == "firecrawl" else "https://www.flashproxy.com/api/v1/usage"
    try:
        response = requests.get(endpoint, headers={"Authorization": "Bearer " + api_key, "Accept": "application/json"},
            timeout=(3, 7), allow_redirects=False)
    except requests.RequestException:
        return "error", "Fournisseur injoignable ; quota non actualisé.", quota
    if response.status_code != 200:
        status, detail = _ERRORS.get(response.status_code, ("error", "Le fournisseur ne peut pas communiquer le quota actuellement."))
        return status, detail, quota
    try:
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) and payload.get("success") is True else None
        if not isinstance(data, dict):
            raise ValueError
        if provider == "firecrawl":
            remaining, total = _number(data.get("remainingCredits")), _number(data.get("planCredits"))
            quota.update(remaining=remaining, total=total, reset_at=_date(data.get("billingPeriodEnd")))
            # Add-on credits can exceed planCredits: never invent used credits.
            quota["used"] = total - remaining if total is not None and remaining is not None and total >= remaining else None
            detail = "Crédits vérifiés auprès de Firecrawl (quota de l’équipe)." if remaining is not None else "Connexion valide ; crédits non communiqués par le fournisseur."
        else:
            plans = data.get("plans")
            if not isinstance(plans, list) or any(not isinstance(plan, dict) for plan in plans):
                raise ValueError
            if len(selected_ids)>1:
                items=[]
                for plan_id in selected_ids:
                    found=[plan for plan in plans if str(plan.get("plan_id"))==plan_id]
                    item_quota=_empty_quota("flashproxy")
                    status="error";detail="Plan absent ou quota non communiqué dans les plans actifs."
                    if len(found)==1 and isinstance(found[0].get("usage"),dict):
                        usage=found[0]["usage"]
                        for output,field in (("remaining","remaining_bytes"),("used","bytes_used"),("total","max_bytes")):
                            value=_number(usage.get(field));item_quota[output]=value/1_000_000_000 if value is not None else None
                        status="exhausted" if item_quota["remaining"]==0 else "ok"
                        detail="Quota vérifié auprès de FlashProxy." if item_quota["total"] is not None else "Plan sans plafond de données déclaré."
                    items.append({"plan_id":plan_id,"status":status,"detail":detail,"quota":item_quota})
                for field in ("remaining","used","total"):
                    values=[item["quota"][field] for item in items]
                    quota[field]=sum(values) if all(value is not None for value in values) else None
                quota["plans"]=items
                status="error" if any(item["status"]=="error" for item in items) else "exhausted" if all(item["status"]=="exhausted" for item in items) else "ok"
                return status,"Quotas des deux plans vérifiés." if status!="error" else "Un des plans ne communique pas de quota vérifiable.",quota
            plan_id=next(iter(selected_ids),"")
            selected=[plan for plan in plans if str(plan.get("plan_id"))==plan_id] if plan_id else plans
            if len(selected)!=1:
                return "error","Quota indisponible : renseignez les plans FlashProxy dans l’administration (FLASHPROXY_PLAN_ID).",quota
            usage=selected[0].get("usage")
            if not isinstance(usage,dict):raise ValueError
            for output,field in (("remaining","remaining_bytes"),("used","bytes_used"),("total","max_bytes")):
                value=_number(usage.get(field));quota[output]=value/1_000_000_000 if value is not None else None
            detail="Données du plan vérifiées auprès de FlashProxy."
            if quota["total"] is None and usage.get("max_bytes") is None:
                detail="Plan sans plafond de données déclaré ; consommation affichée si disponible."
        return ("exhausted" if quota["remaining"] == 0 else "ok"), detail, quota
    except (ValueError, TypeError, AttributeError):
        return "error", "Réponse du fournisseur invalide ; quota non actualisé.", quota


def refresh_quota(provider: str, key_id: str) -> dict:
    _provider(provider)
    with _LOCK, repo.session_scope() as session:
        _ensure_managed(session, provider)
        row = session.get(m.ProviderCredential, key_id)
        if row is None or row.provider != provider:
            raise ValueError("Clé introuvable pour ce fournisseur.")
        key, fingerprint = _decrypt(row), row.fingerprint
        plan_before=_plan_snapshot(session) if provider=="flashproxy" else None
    # Network request runs without DB transaction/credential lock. A concurrent
    # replacement must never receive the old key's quota result.
    status, detail, quota = _fetch_quota(provider, key)
    with _LOCK, repo.session_scope() as session:
        row = session.get(m.ProviderCredential, key_id)
        if row is None or row.provider != provider or row.fingerprint != fingerprint:
            raise ValueError("La clé a changé pendant la vérification ; recommencez.")
        if provider=="flashproxy" and _plan_snapshot(session)!=plan_before:
            raise ValueError("Le plan a changé pendant la vérification ; recommencez.")
        row.status, row.detail = status, detail
        row.quota_json = json.dumps(quota, allow_nan=False)
        row.checked_at = datetime.utcnow()
        return _safe(row)


def record_key_use(provider: str, api_key: str | None, status="ok") -> None:
    """Best-effort safe operational signal; never retain provider error text."""
    if not api_key or status not in {"ok", "invalid", "exhausted", "rate_limited", "error"}:
        return
    try:
        with _LOCK, repo.session_scope() as session:
            row = session.execute(select(m.ProviderCredential).where(m.ProviderCredential.provider == provider,
                m.ProviderCredential.fingerprint == _fingerprint(api_key))).scalar_one_or_none()
            if row:
                row.last_used_at = datetime.utcnow()
                # Successful use cannot establish that a previously sampled
                # balance is still current; checked_at remains the probe time.
                row.status = status
                if status == "ok":
                    row.detail = "Dernier appel au fournisseur réussi."
                else:
                    row.detail = {"invalid": "Clé refusée pendant la collecte.", "exhausted": "Crédits épuisés pendant la collecte.",
                        "rate_limited": "Limite fournisseur atteinte ; temporisation en cours.", "error": "Dernier appel au fournisseur en échec."}[status]
    except Exception:
        # Telemetry must never interrupt a collection or leak a secret through
        # an exception repr. Credential resolution itself still fails closed.
        pass
