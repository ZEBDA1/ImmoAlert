"""Account data and actions shared by the local website and Telegram bot."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, func

from config import settings
from database import repo, models as m
from services.listing_features import normalize_features
from scraper.base import ScrapedListing
from services.matcher import match_and_score
from utils.scrapling import scrapling_status, set_scrapling_enabled
from utils.firecrawl import firecrawl_available, key_pool_status
from utils.flashproxy import flashproxy_status
from utils.images import decode_images

log = logging.getLogger(__name__)
_scan_tasks: dict[str, asyncio.Task] = {}


def approve_pairing(token: str, telegram_id: int) -> bool:
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        return False
    repo.set_runtime_setting(f"site_pair:{token}", json.dumps({"telegram_id": telegram_id, "approved_at": time.time()}))
    return True


def _listing(row: m.Listing, *, full=False, publications=None) -> dict:
    images=decode_images(row.images)
    return {
        "id": row.id, "source": row.source, "url": row.url, "title": row.title,
        "price": row.price, "surface_m2": row.surface_m2, "rooms": row.rooms,
        "location": row.location, "description": (row.description or "") if full else (row.description or "")[:900],
        "images": images if full else images[:1],"image_count":len(images),
        "first_seen_at": row.first_seen_at.isoformat() + "Z" if row.first_seen_at else None,
        "last_seen_at": row.last_seen_at.isoformat() + "Z" if row.last_seen_at else None,
        "inventory_status": "active" if repo.is_current_listing(row) else "stale" if row.inventory_status == "active" else row.inventory_status or "unverified",
        "charges_included": row.charges_included,
        "publications": publications if publications is not None else [{"source":row.source,"url":row.url}],
        "price_per_m2": row.price_per_m2,
        "published_at": row.published_at.isoformat()+"Z" if row.published_at else None,
        "features": normalize_features(row.details) if full else {},
        "latitude": row.latitude, "longitude": row.longitude, "geo_precision": row.geo_precision,
        "details_status": row.details_status,
    }


def _publications(session, rows):
    ids=[row.id for row in rows]
    result={}
    if ids:
        for pub in session.scalars(select(m.ListingPublication).where(m.ListingPublication.listing_id.in_(ids))):
            result.setdefault(pub.listing_id,[]).append({"source":pub.source,"url":pub.url,"price":pub.price})
    return result


def _filter(row: m.Filter) -> dict:
    return {key: getattr(row, key) for key in ["id", "name", "price_min", "price_max", "surface_min", "rooms_min", "property_type", "budget_max_with_charges", "city", "postal_code", "radius_km", "active"]}


def account_snapshot(telegram_id: int | None = None, *, user_id=None, metadata_only=False) -> dict | None:
    user = repo.get_user_by_id(user_id) if user_id else repo.get_user_by_telegram_id(telegram_id) if telegram_id else None
    if not user:
        return None
    filters = repo.get_user_filters(user.id)
    from services.listing_moderation import own_reports
    favorites = [] if metadata_only else repo.get_user_favorite_listings(user.id, limit=300)
    with repo.session_scope() as session:
        web_account=session.scalar(select(m.WebAccount).where(m.WebAccount.user_id==user.id))
        if web_account and not web_account.enabled:
            return None
        publications=_publications(session,favorites)
        sent = session.scalar(select(func.count()).select_from(m.SentAlert).where(m.SentAlert.user_id == user.id))
        pending = session.scalar(select(func.count()).select_from(m.PendingAlert).where(m.PendingAlert.user_id == user.id, m.PendingAlert.status == "pending"))
        favorite_count=session.scalar(select(func.count()).select_from(m.Favorite).where(m.Favorite.user_id==user.id))
        candidates = [] if metadata_only else list(session.scalars(select(m.Listing).where(repo.current_inventory_condition()).order_by(m.Listing.first_seen_at.desc(), m.Listing.id.desc()).limit(800)))
        matches = {str(f.id): [] for f in filters}
        for row in candidates:
            listing = ScrapedListing(source=row.source, external_id=row.external_id, url=row.url, title=row.title,
                price=row.price, surface_m2=row.surface_m2, location=row.location, rooms=row.rooms, description=row.description)
            for f in filters:
                if match_and_score(f, listing).matched:
                    matches[str(f.id)].append(row.id)
    return {"user_id":user.id,"telegram_id":user.telegram_id,"username": user.username,"login":web_account.login if web_account else None,"web_account_id":web_account.id if web_account else None,
            "is_admin": user.telegram_id in settings.admin_telegram_ids or bool(web_account and web_account.is_admin),
            "is_premium": user.is_premium, "active": user.active, "filters": [_filter(f) for f in filters],
            "favorite_count":favorite_count,"favorites": [_listing(row,publications=publications.get(row.id)) for row in favorites if row.inventory_status != "invalid"], "read_listing_ids":[] if metadata_only else repo.read_listing_ids(user.id), "listing_reports":own_reports(user.id), "sent_alerts": sent, "pending_alerts": pending, "matches": matches}


def global_snapshot(bot_username: str, *, metadata_only=False) -> dict:
    from services.scheduler import source_names, get_sources_health
    from services.provider_credentials import provider_enabled

    midnight = datetime.now(ZoneInfo("Europe/Paris")).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    with repo.session_scope() as session:
        current = repo.current_inventory_condition()
        rows = [] if metadata_only else list(session.scalars(select(m.Listing).where(current).order_by(m.Listing.first_seen_at.desc(), m.Listing.id.desc()).limit(800)))
        stored = session.scalar(select(func.count()).select_from(m.Listing))
        total = session.scalar(select(func.count()).select_from(m.Listing).where(current))
        today = session.scalar(select(func.count()).select_from(m.Listing).where(current, m.Listing.first_seen_at >= midnight))
        observations=select(m.Listing.source.label("source"),m.Listing.id.label("listing_id")).where(current).union(
            select(m.ListingPublication.source,m.ListingPublication.listing_id).join(m.Listing,m.Listing.id==m.ListingPublication.listing_id).where(current)).subquery()
        counts = dict(session.execute(select(observations.c.source,func.count()).group_by(observations.c.source)).all())
        users = list(session.scalars(select(m.User).where((m.User.telegram_id.is_not(None)) | m.User.id.in_(select(m.WebAccount.user_id))).order_by(m.User.created_at.desc()).limit(200)))
        web_accounts={a.user_id:a for a in session.scalars(select(m.WebAccount))}
        publications=_publications(session,rows)
        listing_data = [_listing(row,publications=publications.get(row.id)) for row in rows]
        user_data = [{"user_id":row.id,"telegram_id": row.telegram_id, "username": row.username, "active": row.active, "is_premium": row.is_premium, "is_admin": row.telegram_id in settings.admin_telegram_ids or bool(web_accounts.get(row.id) and web_accounts[row.id].is_admin),"web_account_id":web_accounts[row.id].id if row.id in web_accounts else None,"login":web_accounts[row.id].login if row.id in web_accounts else None} for row in users]
    health = get_sources_health()
    sources = []
    for name in source_names():
        item = health.get(name, {})
        sources.append({"name": name, "enabled": repo.get_runtime_setting(f"source_enabled:{name}") != "false",
                        "total": counts.get(name, 0), "status": item.get("last_status", "never_run"),
                        "last_count": item.get("last_unique_count", 0),
                        "last_success": str(item.get("last_success_at") or ""),
                        "last_error": (item.get("last_error") or "")[:180]})
    status = scrapling_status()
    return {"listings": listing_data, "total_listings": total, "stored_listings": stored, "new_today": today, "sources": sources,
            "users": user_data, "bot_username": bot_username, "scrapling": {"enabled": status.enabled, "ready": status.ready, "detail": status.detail},
            "flashproxy": flashproxy_status(),
            "firecrawl": {"enabled": provider_enabled("firecrawl"), "available": firecrawl_available(), **key_pool_status()}, "generated_at": datetime.utcnow().isoformat() + "Z"}


def _number(payload: dict, name: str, maximum: float, *, integer=False):
    value = payload.get(name)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"Valeur invalide : {name}.")
    parsed = float(value)
    if not 0 <= parsed <= maximum or (integer and parsed != int(parsed)):
        raise ValueError(f"Valeur invalide : {name}.")
    return int(parsed) if integer else parsed


def _boolean(payload: dict, key="enabled") -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError("Une valeur activée/désactivée est requise.")
    return value


def apply_command(command: dict, linked: dict[str, int]) -> dict | None:
    """Authorize again against live bot state; return None for unapproved links."""
    from services.scheduler import source_names

    kind, payload = command["kind"], command.get("payload", {})
    if kind == "link":
        token = payload.get("token", "")
        if not re.fullmatch(r"[a-f0-9]{32}", token):
            raise ValueError("Demande de connexion invalide.")
        if time.time() * 1000 - command["created_at"] > 600000:
            raise ValueError("Cette connexion a expiré. Réessayez depuis le site.")
        raw = repo.get_runtime_setting(f"site_pair:{token}")
        if not raw:
            return None
        approved = json.loads(raw)
        if time.time() - approved["approved_at"] > 600:
            raise ValueError("Cette autorisation a expiré.")
        account = account_snapshot(approved["telegram_id"])
        if not account:
            raise ValueError("Compte Telegram introuvable.")
        return {"telegram_id": approved["telegram_id"], "account": account, "message": "Compte Telegram relié."}

    telegram_id = linked.get(command["user_id"])
    user = repo.get_user_by_id(command["_profile_id"]) if command.get("_profile_id") else repo.get_user_by_telegram_id(telegram_id) if telegram_id else None
    if not user:
        raise ValueError("Connectez-vous au site.")
    from services.web_auth import is_admin
    if kind in {"scrapling", "source", "scan", "premium"} and not is_admin(user.id):
        raise ValueError("Accès administrateur requis.")
    if kind == "save_filter":
        name = str(payload.get("name") or "default").strip()[:64]
        if not user.is_premium:
            name = "default"
        city, postal = str(payload.get("city") or "").strip()[:120], str(payload.get("postal_code") or "").strip()
        if not city and not re.fullmatch(r"\d{5}", postal):
            raise ValueError("Indiquez une ville ou un code postal valide.")
        if postal and not re.fullmatch(r"\d{5}", postal):
            raise ValueError("Le code postal doit contenir cinq chiffres.")
        property_type = payload.get("property_type") or None
        if property_type not in {None, "studio", "appartement", "maison"}:
            raise ValueError("Type de logement inconnu.")
        price_min = _number(payload, "price_min", 1000000, integer=True)
        price_max = _number(payload, "price_max", 1000000, integer=True)
        if price_min is not None and price_max is not None and price_min > price_max:
            raise ValueError("Le budget minimum dépasse le maximum.")
        flt = repo.create_or_update_filter(user.id, name=name, city=city or None, postal_code=postal or None,
            price_min=price_min, price_max=price_max, surface_min=_number(payload, "surface_min", 100000),
            rooms_min=_number(payload, "rooms_min", 30, integer=True), property_type=property_type,
            budget_max_with_charges=_number(payload, "budget_max_with_charges", 1000000, integer=True),
            radius_km=_number(payload, "radius_km", 300))
        return {"message": "Recherche enregistrée. La collecte des annonces démarre immédiatement.",
                "filter_id": flt.id, "_search_user_id": user.id}
    if kind == "delete_filter":
        name = str(payload.get("name") or "default")
        flt = next((row for row in repo.get_user_filters(user.id) if row.name == name), None)
        repo.delete_filter(user.id, name)
        return {"message": "Recherche supprimée.", "filter_id": flt.id if flt else None,
                "_cancel_user_id": user.id}
    if kind == "favorite":
        listing_id = _number(payload, "listing_id", 1000000000, integer=True)
        if not listing_id or not repo.get_listing_by_id(listing_id):
            raise ValueError("Annonce introuvable.")
        if _boolean(payload):
            repo.add_favorite(user.id, listing_id)
        else:
            repo.remove_favorite(user.id, listing_id)
        return {"message": "Favoris mis à jour."}
    if kind == "alerts":
        enabled = _boolean(payload)
        with repo.session_scope() as session:
            session.get(m.User, user.id).active = enabled
        if not enabled:
            repo.cancel_pending_alerts_for_user(user.id)
            with repo.session_scope() as session:
                for notification in session.scalars(select(m.MemberNotification).where(m.MemberNotification.user_id==user.id,m.MemberNotification.telegram_status=="pending")):
                    notification.telegram_status="canceled"
        return {"message": "Alertes activées." if enabled else "Alertes en pause."}
    if kind == "scrapling":
        enabled = _boolean(payload)
        if enabled and not scrapling_status().ready:
            raise ValueError("Le navigateur Scrapling est indisponible.")
        set_scrapling_enabled(enabled)
        return {"message": "Moteur principal Scrapling activé." if enabled else "Moteur principal Scrapling désactivé."}
    if kind == "source":
        source = payload.get("source")
        if source not in source_names():
            raise ValueError("Source inconnue.")
        repo.set_runtime_setting(f"source_enabled:{source}", "true" if _boolean(payload) else "false")
        return {"message": "Source mise à jour pour les prochaines collectes."}
    if kind == "premium":
        if payload.get("target_user_id") is not None:
            target=_number(payload,"target_user_id",1000000000,integer=True)
            if not target or not repo.get_user_by_id(target):
                raise ValueError("Utilisateur introuvable.")
            with repo.session_scope() as session:
                profile=session.get(m.User,target)
                profile.is_premium=_boolean(payload);profile.premium_until=None
            return {"message":"Accès Premium mis à jour."}
        target = _number(payload, "telegram_id", 10**15, integer=True)
        if not target or not repo.get_user_by_telegram_id(target):
            raise ValueError("Utilisateur introuvable.")
        repo.set_user_premium(target, _boolean(payload))
        return {"message": "Accès Premium mis à jour."}
    if kind == "scan":
        return {"scan": True}
    raise ValueError("Action inconnue.")


async def _scan(bot, command_id: str):
    from services.scheduler import run_full_scan_once
    try:
        counts = await run_full_scan_once(bot)
        result = {"id": command_id, "status": "succeeded", "result": {"message": f"Collecte terminée : {sum(counts.values())} annonces traitées."}}
    except Exception as exc:
        log.warning("Website scan failed (%s)", type(exc).__name__)
        result = {"id": command_id, "status": "failed", "result": {"message": "La collecte n’a pas pu terminer. Consultez l’état des sources."}}
    await asyncio.to_thread(repo.set_runtime_setting, f"site_command:{command_id}", json.dumps(result))


async def process_commands(bot, commands: list, accounts: list) -> list:
    linked = {account["user_id"]: account["telegram_id"] for account in accounts}
    results = []
    for command in commands:
        key = f"site_command:{command['id']}"
        saved = await asyncio.to_thread(repo.get_runtime_setting, key)
        if saved:
            result = json.loads(saved)
            if result["status"] == "running" and command["id"] not in _scan_tasks:
                result = {"id": command["id"], "status": "failed", "result": {"message": "Collecte interrompue par un redémarrage."}}
            results.append(result)
            continue
        try:
            result = await asyncio.to_thread(apply_command, command, linked)
            if result is None:
                continue
            search_user_id = result.pop("_search_user_id", None)
            cancel_user_id = result.pop("_cancel_user_id", None)
            if search_user_id:
                from services.search_jobs import start_search
                job = await start_search(bot, search_user_id, result["filter_id"])
                result["search_id"] = job["id"]
            if cancel_user_id and result.get("filter_id"):
                from services.search_jobs import cancel_search
                await cancel_search(cancel_user_id, result["filter_id"])
            status = "running" if result.pop("scan", False) else "succeeded"
            response = {"id": command["id"], "status": status, "result": result}
            await asyncio.to_thread(repo.set_runtime_setting, key, json.dumps(response))
            if status == "running":
                _scan_tasks[command["id"]] = asyncio.create_task(_scan(bot, command["id"]))
            results.append(response)
        except (ValueError, TypeError) as exc:
            response = {"id": command["id"], "status": "failed", "result": {"message": str(exc)[:200]}}
            await asyncio.to_thread(repo.set_runtime_setting, key, json.dumps(response))
            results.append(response)
        except Exception as exc:
            log.warning("Website action failed (%s)", type(exc).__name__)
            response = {"id": command["id"], "status": "failed", "result": {"message": "L’action n’a pas pu être appliquée."}}
            await asyncio.to_thread(repo.set_runtime_setting, key, json.dumps(response))
            results.append(response)
    return results
