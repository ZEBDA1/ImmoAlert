from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import datetime, timedelta
from html import escape
from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from config import settings
from database import repo
from database import models as m
from services.scheduler import (
    get_sources_health,
    sample_source_listings,
    run_filter_backfill_once,
    run_full_scan_once,
    source_names,
)
from utils.firecrawl import firecrawl_available, firecrawl_rate_limited, firecrawl_status
from services.matcher import match_and_score
from services.scam import detect_scam
from services.notification import send_alert, format_message, listing_keyboard
from scraper.base import ScrapedListing
from .states import FilterStates
from .keyboards import skip_kb, main_panel_kb
from services.ai_features import ai_parse_filter

log = logging.getLogger(__name__)

router = Router()


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    txt = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    txt = txt.lower().strip()
    txt = re.sub(r"[^a-z0-9]+", " ", txt)
    return " ".join(txt.split())


def _is_admin(telegram_id: int) -> bool:
    return telegram_id in settings.admin_telegram_ids


def _panel(msg: Message):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    return main_panel_kb(is_admin=_is_admin(msg.from_user.id), is_premium=user.is_premium)


def _panel_for_user_id(telegram_id: int):
    u = repo.get_user_by_telegram_id(telegram_id)
    is_premium = bool(u and u.is_premium)
    return main_panel_kb(is_admin=_is_admin(telegram_id), is_premium=is_premium)


def _parse_target_telegram_id(args: str | None) -> int | None:
    if not args:
        return None
    raw = args.strip().split()[0]
    if raw.startswith('@'):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _sanitize_filter_name(name: str | None) -> str:
    if not name:
        return "default"
    cleaned = "".join(ch for ch in name.strip() if ch.isalnum() or ch in ("-", "_"))
    return cleaned[:32] if cleaned else "default"


def _is_skip(value: str | None) -> bool:
    return _normalize_text(value) in {"passer", "skip", "ignorer", "aucun", "aucune", "toute ville", "toutes villes"}


def _extract_postal(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\b(\d{5})\b", str(value))
    return match.group(1) if match else None


def _clean_city(value: str | None) -> str | None:
    if not value or _is_skip(value):
        return None
    cleaned = re.sub(r"\b\d{5}\b", " ", str(value))
    cleaned = re.sub(r"\s*-\s*", "-", cleaned.replace("’", "'"))
    cleaned = " ".join(cleaned.strip(" -_,.;:()[]{}").split())
    return cleaned or None


def _clean_radius(value) -> float | None:
    try:
        radius = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return radius if radius > 0 else None


def _parse_positive_int(value: str | None) -> int | None:
    if value is None or _is_skip(value):
        return None
    match = re.search(r"\d[\d\s\xa0]*", str(value))
    if not match:
        return None
    parsed = int(match.group(0).replace(" ", "").replace("\xa0", ""))
    return parsed if parsed > 0 else None


def _parse_positive_float(value: str | None) -> float | None:
    if value is None or _is_skip(value):
        return None
    try:
        parsed = float(str(value).replace(",", ".").replace(" ", ""))
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _clean_property_type(value: str | None) -> str | None:
    if not value or _is_skip(value):
        return None
    norm = _normalize_text(value)
    if norm in {"studio", "studette", "t1", "f1"}:
        return "studio"
    if norm in {"appartement", "appart", "duplex", "loft", "t2", "t3", "t4", "t5", "f2", "f3", "f4", "f5"}:
        return "appartement"
    if norm in {"maison", "villa", "pavillon"}:
        return "maison"
    return None


def _favorite_sort_from_args(args: str | None) -> str:
    raw = _normalize_text((args or "").split()[0] if args else "")
    if raw in {"price", "prix"}:
        return "price"
    if raw in {"surface", "taille"}:
        return "surface"
    return "recent"


def _clean_filter_payload(data: dict) -> dict:
    postal = _extract_postal(data.get("postal_code")) or _extract_postal(data.get("city"))
    price_min = _parse_positive_int(str(data.get("price_min"))) if data.get("price_min") is not None else None
    price_max = _parse_positive_int(str(data.get("price_max"))) if data.get("price_max") is not None else None
    if price_min is not None and price_max is not None and price_min > price_max:
        price_min, price_max = price_max, price_min
    return {
        "price_min": price_min,
        "price_max": price_max,
        "surface_min": _parse_positive_float(str(data.get("surface_min"))) if data.get("surface_min") is not None else None,
        "rooms_min": _parse_positive_int(str(data.get("rooms_min"))) if data.get("rooms_min") is not None else None,
        "property_type": _clean_property_type(data.get("property_type")),
        "budget_max_with_charges": _parse_positive_int(str(data.get("budget_max_with_charges"))) if data.get("budget_max_with_charges") is not None else None,
        "city": _clean_city(data.get("city")),
        "postal_code": postal,
        "radius_km": _clean_radius(data.get("radius_km")),
    }


def _next_filter_name(filters: list[m.Filter]) -> str:
    existing = {f.name for f in filters}
    idx = 1
    while True:
        candidate = f"filter_{idx}"
        if candidate not in existing:
            return candidate
        idx += 1


async def _start_filter_wizard(msg: Message, state: FSMContext, *, filter_name: str):
    await state.clear()
    await state.update_data(filter_name=filter_name)
    await state.set_state(FilterStates.waiting_city)
    await msg.answer(
        f"⚙️ Configuration du filtre <b>{filter_name}</b>\n\n🏙️ Ville (ex: Paris) - ou tapez 'Passer' pour ignorer:",
        reply_markup=skip_kb(),
    )


def _filter_summary(f: m.Filter) -> str:
    return (
        f"\n🎯 <b>{escape(f.name)}</b>\n"
        f"📍 {escape(f.city or 'Toute ville')} · CP {escape(f.postal_code or '—')} · rayon {f.radius_km or '—'} km\n"
        f"💰 {f.price_min or '—'} → {f.price_max or '—'} € · charges {f.budget_max_with_charges or '—'} €\n"
        f"📐 {f.surface_min or '—'} m² min · 🛏️ {f.rooms_min or '—'} pièce(s) min\n"
        f"🏷️ {escape(f.property_type or 'Tous types')}"
    )


def _manage_filters_kb(filters: list[m.Filter], *, is_premium: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for f in filters:
        rows.append(
            [
                InlineKeyboardButton(text=f"✏️ {f.name}", callback_data=f"editflt:{f.name}"),
                InlineKeyboardButton(text="🗑️", callback_data=f"delflt:{f.name}"),
            ]
        )
    if is_premium:
        rows.append([InlineKeyboardButton(text="➕ Nouveau filtre", callback_data="addflt:auto")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _favorite_action_kb(listing_id: int, *, liked: bool) -> InlineKeyboardMarkup:
    if liked:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💔 Retirer des favoris", callback_data=f"fav:remove:{listing_id}")]]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❤️ J'aime", callback_data=f"fav:add:{listing_id}")]]
    )


#: Telegram hard-limits message bodies to 4096 characters.
_TG_MESSAGE_LIMIT = 4000


def _split_message(text: str, limit: int = _TG_MESSAGE_LIMIT) -> list[str]:
    """Split *text* on blank lines, never exceeding *limit* per chunk."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for block in text.split("\n\n"):
        # A single oversized block still has to go out; hard-cut it.
        while len(block) > limit:
            chunks.append(block[:limit])
            block = block[limit:]
        extra = len(block) + 2 if current else len(block)
        if current and size + extra > limit:
            chunks.append("\n\n".join(current))
            current, size = [block], len(block)
        else:
            current.append(block)
            size += extra
    if current:
        chunks.append("\n\n".join(current))
    return [c for c in chunks if c.strip()]


def _source_status_icon(h: dict) -> str:
    if h.get("disabled_until"):
        return "⏳"
    if h.get("consecutive_failures", 0) > 0:
        return "⚠️"
    if h.get("consecutive_empty_runs", 0) >= 3:
        return "🟡"
    return "🟢"


def _compact_source_health() -> str:
    health = get_sources_health()
    if not health:
        return "🛰️ <b>Radar scraping</b>\n\nAucun cycle terminé pour le moment."
    lines = ["🛰️ <b>Radar scraping</b>", ""]
    for source, h in sorted(health.items()):
        icon = _source_status_icon(h)
        status = h.get("last_status", "-")
        raw = h.get("last_raw_count", h.get("last_listings_count", 0))
        normalized = h.get("last_normalized_count", h.get("last_listings_count", 0))
        dropped = h.get("last_dropped_count", 0)
        unique = h.get("last_unique_count", h.get("last_listings_count", 0))
        new = h.get("last_new_count", 0)
        total = h.get("total_listings", 0)
        failures = h.get("consecutive_failures", 0)
        empty = h.get("consecutive_empty_runs", 0)
        seconds = h.get("last_cycle_seconds")
        speed = f" · {seconds}s" if seconds is not None else ""
        lines.append(
            f"{icon} <b>{escape(str(source))}</b> · {escape(str(status))} · raw {raw} · ok {normalized} · rejet {dropped} · unique {unique} · nouveau {new} · total {total} · fail {failures} · vide {empty}{speed}"
        )
    return "\n".join(lines)


async def _firecrawl_status_block() -> str:
    """Render the Firecrawl CLI health for /debug_sources.

    ``firecrawl_status`` shells out to the CLI (up to 20s), so it runs in a
    worker thread to keep the bot loop responsive. It never raises, but we
    still guard the thread hop in case the event loop is shutting down.
    """
    try:
        status = await asyncio.to_thread(firecrawl_status)
    except Exception as exc:  # pragma: no cover - defensive
        return f"🔥 <b>Firecrawl</b> — statut indisponible ({escape(str(exc))})"

    if not status.installed:
        icon, label = "🔴", "CLI introuvable"
    elif not status.enabled:
        icon, label = "⚪", "désactivé"
    elif firecrawl_rate_limited():
        icon, label = "⏳", "quota atteint"
    elif status.authenticated:
        icon, label = "🟢", "prêt"
    else:
        icon, label = "🟡", "quota gratuit"

    lines = [f"🔥 <b>Firecrawl</b> — {icon} {escape(label)}"]
    if status.installed:
        lines.append(f"  • auth={escape('oui' if status.authenticated else 'non')}")
        if status.credits_remaining is not None:
            lines.append(f"  • crédits={status.credits_remaining}")
        if status.concurrency is not None:
            lines.append(f"  • parallélisme={status.concurrency}")
    if status.detail:
        lines.append(f"  • {escape(status.detail)}")
    if firecrawl_rate_limited():
        lines.append("  • ⏳ quota gratuit épuisé — renseigne FIRECRAWL_API_KEY")
    elif not settings.firecrawl_api_key:
        lines.append("  • ⚠️ sans FIRECRAWL_API_KEY le quota gratuit est très limité")
    transport = "firecrawl" if firecrawl_available() else "fallback http (souvent 403)"
    lines.append(f"  • leboncoin/seloger via {escape(transport)}")
    return "\n".join(lines)


async def _show_manage_filters(msg: Message):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    filters = repo.get_user_filters(user.id)
    if not filters:
        await msg.answer("ℹ️ Aucun filtre actif. Utilise /set_filters pour créer le premier.", reply_markup=_panel(msg))
        return
    text = (
        "🗂️ <b>Gestion des filtres</b>\n\n"
        f"Tu as <b>{len(filters)}</b> filtre(s).\n"
        "Choisis un filtre à modifier ou supprimer:"
    )
    await msg.answer(text, reply_markup=_manage_filters_kb(filters, is_premium=user.is_premium))


async def _send_initial_matches(msg: Message, user: m.User, flt: m.Filter, *, limit: int = 5) -> int:
    # Build a pool of recent matches, then interleave sources to diversify output
    pool_by_source: dict[str, list[ScrapedListing]] = {}
    for db_l in repo.get_recent_listings(hours=72, limit=400):
        if repo.has_sent_alert(user.id, db_l.id):
            continue
        l = ScrapedListing(
            source=db_l.source,
            external_id=db_l.external_id,
            url=db_l.url,
            title=db_l.title,
            price=db_l.price,
            surface_m2=db_l.surface_m2,
            price_per_m2=db_l.price_per_m2,
            location=db_l.location,
            rooms=db_l.rooms,
            description=db_l.description,
            images=db_l.images.split(",") if db_l.images else None,
            published_at=db_l.published_at,
            db_id=db_l.id,
        )
        res = match_and_score(flt, l)
        if not res.matched:
            continue
        pool_by_source.setdefault(l.source, []).append(l)

    # Round-robin per source
    sent = 0
    sources = list(pool_by_source.keys())
    idx = 0
    while sent < limit and sources:
        src = sources[idx % len(sources)]
        arr = pool_by_source.get(src) or []
        if not arr:
            sources.pop(idx % len(sources))
            continue
        l = arr.pop(0)
        try:
            res = match_and_score(flt, l)
            if not res.matched:
                continue
            if user.is_premium:
                scam = await asyncio.to_thread(detect_scam, l)
                scam_tag = "Potentielle arnaque" if scam.is_scam else None
                await send_alert(msg.bot, user, l, score_label=res.score_label, scam_tag=scam_tag)
                if l.db_id is not None:
                    repo.mark_alert_sent(
                        user.id,
                        l.db_id,
                        filter_id=flt.id,
                        filter_name=flt.name,
                        score_value=res.score_value,
                        score_label=res.score_label,
                        match_reasons="; ".join(res.reasons or []) or None,
                    )
                sent += 1
            elif l.db_id is not None:
                added = repo.add_pending_alert(
                    user.id,
                    l.db_id,
                    datetime.utcnow() + timedelta(seconds=settings.premium_free_delay_seconds),
                    filter_id=flt.id,
                    filter_name=flt.name,
                    score_value=res.score_value,
                    score_label=res.score_label,
                    match_reasons="; ".join(res.reasons or []) or None,
                )
                if added:
                    sent += 1
        except Exception:
            pass
        idx += 1
    return sent


async def _send_initial_and_existing_matches(msg: Message, user: m.User, flt: m.Filter, *, limit: int = 5) -> None:
    db_count = await _send_initial_matches(msg, user, flt, limit=limit)
    await msg.answer("🔎 Je cherche aussi les annonces déjà en ligne sur les sources compatibles avec tes critères.")
    backfill = await run_filter_backfill_once(msg.bot, user, flt, limit=limit)

    total_actions = db_count + backfill.action_count
    if total_actions > 0:
        if user.is_premium:
            await msg.answer(
                f"✅ Recherche initiale terminée: {total_actions} annonce(s) correspondant aux critères envoyée(s)."
            )
        else:
            await msg.answer(
                f"✅ Recherche initiale terminée: {total_actions} annonce(s) correspondant aux critères programmée(s) avec le délai FREE."
            )
        return

    details = ""
    if backfill.sources_count:
        details = f"\nSources testées: {backfill.sources_count} · annonces uniques vues: {backfill.unique_count}"
    await msg.answer(
        "ℹ️ Aucune annonce existante trouvée pour ce filtre pour le moment. Je surveille les nouvelles en continu."
        + details
    )


@router.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    if not user.active:
        with repo.session_scope() as s:
            u = s.get(m.User, user.id)
            if u:
                u.active = True
                s.add(u)

    # Refresh in-memory premium status and display expiration, if any
    user = repo.get_user_by_telegram_id(msg.from_user.id) or user
    plan = "👑 PREMIUM" if user.is_premium else "🆓 FREE"
    premium_exp = ""
    if getattr(user, "premium_until", None):
        premium_exp = f"\n⏳ Expire le: {user.premium_until:%Y-%m-%d %H:%M} UTC"
    admin_tag = "\n🛠️ Mode admin actif" if _is_admin(msg.from_user.id) else ""

    filters = repo.get_user_filters(user.id)
    health = get_sources_health()
    active_sources = sum(
        1 for source in source_names()
        if not health.get(source, {}).get("disabled_until")
        or health[source]["disabled_until"] <= datetime.utcnow()
    )
    text = (
        "🏠 <b>ImmoAlert — radar immobilier</b>\n\n"
        f"Statut: {plan}{premium_exp}{admin_tag}\n"
        f"🎯 Filtres actifs: <b>{len(filters)}</b>\n"
        f"🛰️ Sources suivies: <b>{active_sources}</b>\n\n"
        "Choisis une action dans le panneau.\n"
        "Astuce: envoie une phrase comme <i>Paris 11, 2 pièces, 1200 € max</i> puis utilise /ai_filter."
    )
    await msg.answer(text, reply_markup=_panel(msg))


@router.message(Command("status"))
async def cmd_status(msg: Message):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    filters = repo.get_user_filters(user.id)
    favs = repo.get_user_favorite_listings(user.id, limit=1)
    plan = "👑 Premium" if user.is_premium else "🆓 Free"
    text = (
        "🧭 <b>Mon radar</b>\n\n"
        f"Compte: <b>{plan}</b>\n"
        f"Alertes: <b>{'actives' if user.active else 'en pause'}</b>\n"
        f"Filtres: <b>{len(filters)}</b>\n"
        f"Favoris: <b>{'au moins 1' if favs else '0'}</b>\n\n"
        f"{_compact_source_health()}"
    )
    await msg.answer(text, reply_markup=_panel(msg))


@router.message(Command("premium"))
async def cmd_premium(msg: Message):
    u = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    exp = f"\n⏳ Expire le: {u.premium_until:%Y-%m-%d %H:%M} UTC" if getattr(u, "premium_until", None) else ""
    cur = "\n\nStatut actuel: 👑 PREMIUM" + exp if u.is_premium else "\n\nStatut actuel: 🆓 FREE"
    text = (
        "💎 <b>Plans disponibles</b>\n\n"
        "🆓 <b>FREE</b>\n"
        f"• ⏱️ Alertes différées: {settings.premium_free_delay_seconds // 60 or settings.premium_free_delay_seconds} {'min' if settings.premium_free_delay_seconds >= 60 else 's'}\n"
        "• 🎯 1 filtre actif: default\n"
        "• 🧭 Mode guidé sans IA\n\n"
        "👑 <b>PREMIUM</b>\n"
        "• ⚡ Alertes instantanées\n"
        "• 🗺️ Multi-villes\n"
        "• 🧩 Filtres avancés\n"
        "• 🎛️ Priorité de traitement\n"
        "• 🧠 Filtre IA (description -> filtre)\n"
        f"{cur}\n\n"
        "🗂️ Multi-filtres:\n"
        "• /add_filter [nom]\n"
        "• /edit_filters [nom]"
    )
    await msg.answer(text, reply_markup=_panel(msg))


@router.message(Command("ai_filter"))
async def cmd_ai_filter(msg: Message, command: CommandObject):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    if not user.is_premium:
        await msg.answer(
            "🔒 /ai_filter est réservé au PREMIUM.\nEn FREE, utilise 🎯 Configurer mes filtres pour ton filtre default.",
            reply_markup=_panel(msg),
        )
        return
    raw = (command.args or "").strip()
    if not raw:
        await msg.answer(
            "Usage: /ai_filter &lt;description libre&gt;\nExemple: /ai_filter Paris 11, 2 pièces, 35 m² min, 1200 € max",
            reply_markup=_panel(msg),
        )
        return
    parsed = await asyncio.to_thread(ai_parse_filter, raw)
    if not parsed:
        await msg.answer("🤔 Impossible d'interpréter ce texte. Réessaye avec une ville, un budget et une surface.", reply_markup=_panel(msg))
        return

    # Determine target filter name
    filters = repo.get_user_filters(user.id)
    if not user.is_premium:
        target = "default"
    else:
        target = _next_filter_name(filters)

    payload = _clean_filter_payload(parsed)
    repo.create_or_update_filter(
        user_id=user.id,
        name=target,
        **payload,
    )

    # Reactivate alerts if needed
    with repo.session_scope() as s:
        u = s.get(m.User, user.id)
        if u:
            u.active = True
            s.add(u)

    flt = next((f for f in repo.get_user_filters(user.id) if f.name == target), None)
    summary = _filter_summary(flt) if flt else ""
    await msg.answer(f"✅ Filtre IA <b>{target}</b> enregistré.{summary}", reply_markup=_panel(msg))

    if flt:
        await _send_initial_and_existing_matches(msg, user, flt, limit=5)


@router.message(Command("my_id"))
async def cmd_my_id(msg: Message):
    await msg.answer(f"🆔 Votre Telegram ID: <code>{msg.from_user.id}</code>", reply_markup=_panel(msg))


@router.message(Command("stop"))
async def cmd_stop(msg: Message, state: FSMContext | None = None):
    if state is not None:
        await state.clear()
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    with repo.session_scope() as s:
        u = s.get(m.User, user.id)
        if u:
            u.active = False
            s.add(u)
    await msg.answer("⏸️ Alertes en pause. Utilise /start pour réactiver.", reply_markup=_panel(msg))


@router.message(Command("view_filters"))
async def cmd_view_filters(msg: Message):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    flt = repo.get_user_filters(user.id)
    if not flt:
        await msg.answer("ℹ️ Aucun filtre actif. Utilise /set_filters pour commencer.", reply_markup=_panel(msg))
        return

    plan = "👑 PREMIUM" if user.is_premium else "🆓 FREE"
    header = f"📋 <b>Mes critères actifs</b>\nStatut: {plan}\nNombre: {len(flt)}"
    text = header + "\n" + "\n".join(_filter_summary(f) for f in flt)
    await msg.answer(text, reply_markup=_panel(msg))


@router.message(Command("manage_filters"))
async def cmd_manage_filters(msg: Message):
    await _show_manage_filters(msg)


@router.message(Command("search_now"))
async def cmd_search_now(msg: Message, command: CommandObject):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    filters = repo.get_user_filters(user.id)
    if not filters:
        await msg.answer("ℹ️ Aucun filtre actif. Utilise /set_filters pour créer le premier.", reply_markup=_panel(msg))
        return

    target = _sanitize_filter_name(command.args)
    if not user.is_premium:
        target = "default"
    elif command.args is None and len(filters) == 1:
        target = filters[0].name

    flt = next((f for f in filters if f.name == target), None)
    if not flt:
        names = ", ".join(f.name for f in filters)
        await msg.answer(
            f"⚠️ Filtre introuvable: <code>{target}</code>\nFiltres existants: <code>{names}</code>",
            reply_markup=_panel(msg),
        )
        return

    await msg.answer(f"🔎 Recherche immédiate lancée pour le filtre <b>{escape(flt.name)}</b>.", reply_markup=_panel(msg))
    await _send_initial_and_existing_matches(msg, user, flt, limit=5)


@router.message(Command("favorites"))
async def cmd_favorites(msg: Message, command: CommandObject | None = None):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    sort = _favorite_sort_from_args(command.args if command else None)
    favs = repo.get_user_favorite_listings(user.id, limit=50, sort=sort)
    if not favs:
        await msg.answer("ℹ️ Tu n'as pas encore de favoris. Clique sur ❤️ sous une annonce.", reply_markup=_panel(msg))
        return

    sort_label = {"recent": "récents", "price": "prix croissant", "surface": "surface décroissante"}[sort]
    await msg.answer(
        f"❤️ <b>Mes favoris</b> ({len(favs)}) · tri {sort_label}\n"
        "Tri: <code>/favorites recent</code>, <code>/favorites price</code>, <code>/favorites surface</code>",
        reply_markup=_panel(msg),
    )
    for i, l in enumerate(favs, start=1):
        listing = ScrapedListing(
            source=l.source,
            external_id=l.external_id,
            url=l.url,
            title=f"#{i} · {l.title or 'Annonce'}",
            price=l.price,
            surface_m2=l.surface_m2,
            price_per_m2=l.price_per_m2,
            location=l.location,
            rooms=l.rooms,
            description=l.description,
            images=l.images.split(",") if l.images else None,
            published_at=l.published_at,
            db_id=l.id,
        )
        await msg.answer(
            format_message(listing, score_label="", scam_tag=None),
            disable_web_page_preview=True,
            reply_markup=listing_keyboard(listing, liked=True),
        )


@router.message(Command("edit_filters"))
async def cmd_edit_filters(msg: Message, state: FSMContext, command: CommandObject):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    filters = repo.get_user_filters(user.id)
    if not filters:
        await msg.answer("ℹ️ Aucun filtre à modifier. Utilise /set_filters pour créer le premier.", reply_markup=_panel(msg))
        return
    target = _sanitize_filter_name(command.args)
    if target == "default" and user.is_premium and command.args is None and len(filters) > 1:
        names = ", ".join(f.name for f in filters)
        await msg.answer(
            f"ℹ️ Tu as plusieurs filtres: <code>{names}</code>\nUtilise /edit_filters &lt;nom&gt; pour choisir.",
            reply_markup=_panel(msg),
        )
        return
    if target not in {f.name for f in filters}:
        names = ", ".join(f.name for f in filters)
        await msg.answer(
            f"⚠️ Filtre introuvable: <code>{target}</code>\nFiltres existants: <code>{names}</code>",
            reply_markup=_panel(msg),
        )
        return
    await _start_filter_wizard(msg, state, filter_name=target)


@router.message(Command("set_filters"))
async def cmd_set_filters(msg: Message, state: FSMContext, command: CommandObject):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    filters = repo.get_user_filters(user.id)
    target = _sanitize_filter_name(command.args)

    if not user.is_premium and target != "default":
        await msg.answer(
            "ℹ️ En mode FREE, un seul filtre est disponible (default).\nPasse en PREMIUM pour créer plusieurs filtres.",
            reply_markup=_panel(msg),
        )
        return

    if not user.is_premium and filters and target != "default":
        target = "default"

    await _start_filter_wizard(msg, state, filter_name=target)


@router.message(Command("add_filter"))
async def cmd_add_filter(msg: Message, state: FSMContext, command: CommandObject):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    filters = repo.get_user_filters(user.id)
    if not user.is_premium:
        await msg.answer(
            "🔒 Multi-filtres réservé au PREMIUM.\nEn FREE, utilise /set_filters pour ton filtre unique.",
            reply_markup=_panel(msg),
        )
        return

    requested = _sanitize_filter_name(command.args) if command.args else None
    if requested and requested != "default":
        target = requested
    else:
        target = _next_filter_name(filters)

    if target in {f.name for f in filters}:
        await msg.answer(f"⚠️ Le filtre <code>{target}</code> existe déjà. Utilise /edit_filters {target}", reply_markup=_panel(msg))
        return

    await _start_filter_wizard(msg, state, filter_name=target)


@router.message(Command("delete_filter"))
async def cmd_delete_filter(msg: Message, command: CommandObject):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    raw_name = (command.args or "").strip()
    if not raw_name:
        await msg.answer("Usage: /delete_filter &lt;nom&gt;", reply_markup=_panel(msg))
        return
    name = _sanitize_filter_name(raw_name)
    ok = repo.delete_filter(user.id, name)
    if ok:
        await msg.answer(f"🗑️ Filtre <b>{name}</b> supprimé.", reply_markup=_panel(msg))
    else:
        await msg.answer(f"⚠️ Filtre introuvable: <code>{name}</code>.", reply_markup=_panel(msg))


@router.message(FilterStates.waiting_city, F.text)
async def set_city(msg: Message, state: FSMContext):
    city = msg.text.strip()
    await state.update_data(city=_clean_city(city), postal_code=_extract_postal(city))
    await state.set_state(FilterStates.waiting_postal)
    await msg.answer("📮 Code postal (ex: 75011) - ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_postal, F.text)
async def set_postal(msg: Message, state: FSMContext):
    postal = msg.text.strip()
    if _is_skip(postal):
        data = await state.get_data()
        postal = data.get("postal_code")
    else:
        postal = _extract_postal(postal)
        if not postal:
            await msg.answer("⚠️ Code postal invalide. Envoie 5 chiffres, ex: 75011, ou 'Passer'.", reply_markup=skip_kb())
            return
    await state.update_data(postal_code=postal)
    await state.set_state(FilterStates.waiting_radius)
    await msg.answer("📏 Rayon (km, ex: 3) - ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_radius, F.text)
async def set_radius(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    radius = _parse_positive_float(txt)
    if not _is_skip(txt) and radius is None:
        await msg.answer("⚠️ Rayon invalide. Envoie un nombre positif, ex: 3, ou 'Passer'.", reply_markup=skip_kb())
        return
    await state.update_data(radius_km=radius)
    await state.set_state(FilterStates.waiting_price_min)
    await msg.answer("💰 Prix minimum (€) - ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_price_min, F.text)
async def set_price_min(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    pmin = _parse_positive_int(txt)
    if not _is_skip(txt) and pmin is None:
        await msg.answer("⚠️ Prix minimum invalide. Envoie un montant positif, ex: 700, ou 'Passer'.", reply_markup=skip_kb())
        return
    await state.update_data(price_min=pmin)
    await state.set_state(FilterStates.waiting_price_max)
    await msg.answer("💰 Prix maximum (€) - ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_price_max, F.text)
async def set_price_max(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    pmax = _parse_positive_int(txt)
    if not _is_skip(txt) and pmax is None:
        await msg.answer("⚠️ Prix maximum invalide. Envoie un montant positif, ex: 1200, ou 'Passer'.", reply_markup=skip_kb())
        return
    await state.update_data(price_max=pmax)
    await state.set_state(FilterStates.waiting_surface_min)
    await msg.answer("📐 Surface minimum (m²) - ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_surface_min, F.text)
async def set_surface_min(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    surf = _parse_positive_float(txt)
    if not _is_skip(txt) and surf is None:
        await msg.answer("⚠️ Surface invalide. Envoie un nombre positif, ex: 35, ou 'Passer'.", reply_markup=skip_kb())
        return
    await state.update_data(surface_min=surf)
    await state.set_state(FilterStates.waiting_rooms_min)
    await msg.answer("🛏️ Nombre de pieces minimum - ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_rooms_min, F.text)
async def set_rooms_min(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    rooms = _parse_positive_int(txt)
    if not _is_skip(txt) and rooms is None:
        await msg.answer("⚠️ Nombre de pièces invalide. Envoie un entier positif, ex: 2, ou 'Passer'.", reply_markup=skip_kb())
        return
    await state.update_data(rooms_min=rooms)
    await state.set_state(FilterStates.waiting_property_type)
    await msg.answer("🏷️ Type de bien (studio, appartement, maison) - ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_property_type, F.text)
async def set_property_type(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    property_type = _clean_property_type(txt)
    if not _is_skip(txt) and property_type is None:
        await msg.answer("⚠️ Type invalide. Choisis studio, appartement, maison, ou 'Passer'.", reply_markup=skip_kb())
        return
    await state.update_data(property_type=property_type)
    await state.set_state(FilterStates.waiting_budget_charges)
    await msg.answer("🧾 Budget max avec charges (€) - ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_budget_charges, F.text)
async def set_budget_charges(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    budget = _parse_positive_int(txt)
    if not _is_skip(txt) and budget is None:
        await msg.answer("⚠️ Budget charges invalide. Envoie un montant positif, ex: 1300, ou 'Passer'.", reply_markup=skip_kb())
        return
    await state.update_data(budget_max_with_charges=budget)

    data = await state.get_data()
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    filter_name = _sanitize_filter_name(data.get("filter_name"))
    if not user.is_premium and filter_name != "default":
        await state.clear()
        await msg.answer(
            "🔒 Multi-filtres réservé au PREMIUM. Utilise /set_filters pour ton filtre default.",
            reply_markup=_panel(msg),
        )
        return
    payload = _clean_filter_payload(data)
    repo.create_or_update_filter(
        user_id=user.id,
        name=filter_name,
        **payload,
    )
    flt = next((f for f in repo.get_user_filters(user.id) if f.name == filter_name), None)
    with repo.session_scope() as s:
        u = s.get(m.User, user.id)
        if u:
            u.active = True
            s.add(u)
    await state.clear()
    summary = _filter_summary(flt) if flt else ""
    await msg.answer(
        f"✅ Filtre <b>{filter_name}</b> enregistré avec succès. Les alertes sont réactivées.{summary}",
        reply_markup=_panel(msg),
    )
    if flt:
        await _send_initial_and_existing_matches(msg, user, flt, limit=5)


@router.message(Command("admin"))
async def cmd_admin(msg: Message):
    if not _is_admin(msg.from_user.id):
        await msg.answer("⛔ Accès refusé.", reply_markup=_panel(msg))
        return
    text = (
        "🛠️ <b>Mode admin</b>\n\n"
        "Commandes:\n"
        "• /grant_premium &lt;telegram_id&gt; [durée ex: 7d|12h|90m]\n"
        "• /revoke_premium &lt;telegram_id&gt;\n"
        "• /debug_sources\n"
        "• /debug_sample &lt;source&gt;\n"
        "• /full_scan\n"
        "• /all_listings\n"
        "• /admin\n\n"
        "Exemple: <code>/grant_premium 123456789</code>"
    )
    await msg.answer(text, reply_markup=_panel(msg))


@router.message(Command("grant_premium"))
async def cmd_grant_premium(msg: Message, command: CommandObject):
    if not _is_admin(msg.from_user.id):
        await msg.answer("⛔ Accès refusé.", reply_markup=_panel(msg))
        return
    raw = (command.args or "").strip() if command else ""
    parts = raw.split()
    target_id = None
    duration = None
    if parts:
        try:
            target_id = int(parts[0])
        except Exception:
            target_id = None
    if len(parts) >= 2:
        duration = parts[1]
    if not target_id:
        await msg.answer(
            "Usage: /grant_premium &lt;telegram_id&gt; [durée ex: 7d|12h|90m]",
            reply_markup=_panel(msg),
        )
        return

    seconds = None
    if duration:
        d = duration.lower()
        try:
            if d.endswith("d"):
                seconds = int(d[:-1]) * 24 * 3600
            elif d.endswith("h"):
                seconds = int(d[:-1]) * 3600
            elif d.endswith("m"):
                seconds = int(d[:-1]) * 60
            else:
                seconds = int(d)
        except Exception:
            seconds = None

    if seconds and seconds > 0:
        repo.set_user_premium_for(target_id, seconds=seconds)
    else:
        repo.set_user_premium(target_id, True)

    user = repo.get_user_by_telegram_id(target_id)
    status = "👑 PREMIUM" if user and user.is_premium else "Inconnu"
    exp = f"\n⏳ Expire le: {user.premium_until:%Y-%m-%d %H:%M} UTC" if getattr(user, "premium_until", None) else ""
    await msg.answer(
        f"✅ Utilisateur <code>{target_id}</code> en PREMIUM. Statut: {status}{exp}",
        reply_markup=_panel(msg),
    )


@router.message(Command("revoke_premium"))
async def cmd_revoke_premium(msg: Message, command: CommandObject):
    if not _is_admin(msg.from_user.id):
        await msg.answer("⛔ Accès refusé.", reply_markup=_panel(msg))
        return
    target_id = _parse_target_telegram_id(command.args)
    if not target_id:
        await msg.answer("Usage: /revoke_premium &lt;telegram_id&gt;", reply_markup=_panel(msg))
        return
    repo.set_user_premium(target_id, False)
    await msg.answer(f"✅ Utilisateur <code>{target_id}</code> repassé en 🆓 FREE.", reply_markup=_panel(msg))


@router.message(Command("debug_sources"))
async def cmd_debug_sources(msg: Message):
    if not _is_admin(msg.from_user.id):
        await msg.answer("⛔ Accès refusé.", reply_markup=_panel(msg))
        return
    health = get_sources_health()

    # Firecrawl health is reported even before the first cycle: it is the most
    # common cause of "0 annonce" and admins need it to diagnose quickly.
    lines = ["🛰️ <b>État des sources scraping</b>", "", _compact_source_health()]
    lines.append("")
    lines.append(await _firecrawl_status_block())
    lines.append("")
    lines.append(
        "📚 <b>Sources configurées</b>: " + escape(", ".join(source_names()))
    )
    if not health:
        lines.append("ℹ️ Aucun cycle terminé — lance /full_scan ou attends le scheduler.")
        await msg.answer("\n".join(lines), reply_markup=_panel(msg))
        return
    for source, h in sorted(health.items()):
        disabled_until = h.get("disabled_until")
        status = "🟢 active"
        if disabled_until:
            status = f"⏳ backoff jusqu'à {disabled_until}"
        if not disabled_until and h.get("consecutive_empty_runs", 0) >= 3:
            status = "⚠️ active (aucune annonce récente)"
        safe_source = escape(str(source))
        safe_last_error = escape(str(h.get("last_error") or "-"))
        lines.append(
            "\n".join(
                [
                    f"• <b>{safe_source}</b> - {status}",
                    f"  • cycle={escape(str(h.get('last_cycle_id') or '-'))} | statut={escape(str(h.get('last_status') or '-'))}",
                    f"  • runs={h.get('total_runs', 0)} | success={h.get('total_success', 0)} | fail={h.get('total_failures', 0)}",
                    f"  • raw={h.get('last_raw_count', 0)} | normalisées={h.get('last_normalized_count', 0)} | rejetées={h.get('last_dropped_count', 0)} | uniques={h.get('last_unique_count', h.get('last_listings_count', 0))}",
                    f"  • annonces_total={h.get('total_listings', 0)} | nouvelles={h.get('last_new_count', 0)} | vide={h.get('consecutive_empty_runs', 0)}",
                    f"  • zones={h.get('last_locations_count', 0)} | timeouts={h.get('last_timeout_count', 0)} | erreurs={h.get('last_error_count', 0)} | secondes={h.get('last_cycle_seconds') or '-'}",
                    f"  • échecs_série={h.get('consecutive_failures', 0)}",
                    f"  • dernière_erreur={safe_last_error}",
                ]
            )
        )
    # 8 sources × ~500 chars overflows 4096: send in chunks, keyboard on the last.
    chunks = _split_message("\n\n".join(lines))
    for idx, chunk in enumerate(chunks):
        is_last = idx == len(chunks) - 1
        await msg.answer(
            chunk,
            disable_web_page_preview=True,
            reply_markup=_panel(msg) if is_last else None,
        )


@router.message(Command("debug_sample"))
async def cmd_debug_sample(msg: Message, command: CommandObject):
    if not _is_admin(msg.from_user.id):
        await msg.answer("⛔ Accès refusé.", reply_markup=_panel(msg))
        return
    arg = (command.args or "").strip().lower()
    if not arg:
        available = ", ".join(source_names())
        await msg.answer(
            f"Usage: /debug_sample <source>\nSources: {escape(available)}",
            reply_markup=_panel(msg),
        )
        return

    filters = repo.get_all_active_filters()
    locs_set: set[tuple[str | None, str | None]] = set()
    for f in filters:
        locs_set.add((f.city, f.postal_code))
    locs = list(locs_set) or [(None, None)]

    listings, last_error = await sample_source_listings(arg, locs, limit_locations=2)
    if listings:
        # Show up to 3 sample items
        lines = [f"🔎 Echantillon <b>{escape(arg)}</b>: {len(listings)} annonce(s)"]
        for i, l in enumerate(listings[:3], start=1):
            title = escape(l.title or "Annonce")
            loc = escape(l.location or "-")
            link = escape(l.url or "#", quote=True)
            price = f"{l.price:,} €".replace(",", " ") if l.price is not None else "-"
            lines.append(f"#{i} <b>{title}</b> — {loc} — {price}\n🔗 <a href=\"{link}\">Ouvrir</a>")
        await msg.answer("\n".join(lines), disable_web_page_preview=True, reply_markup=_panel(msg))
        return
    safe_err = escape(last_error or "Aucune annonce et aucune erreur retournée.")
    await msg.answer(
        f"🔎 Echantillon <b>{escape(arg)}</b>: 0 annonce.\nDernière erreur: <code>{safe_err}</code>",
        reply_markup=_panel(msg),
    )


@router.message(Command("full_scan"))
async def cmd_full_scan(msg: Message):
    if not _is_admin(msg.from_user.id):
        await msg.answer("⛔ Accès refusé.", reply_markup=_panel(msg))
        return
    note = await msg.answer("🚀 Full scan lancé en arrière-plan. Je t’envoie le résultat dès que c’est fini.")

    async def _run_and_report():
        try:
            counts = await run_full_scan_once(msg.bot)
            if counts:
                lines = ["✅ Full scan terminé:"]
                for src, n in sorted(counts.items()):
                    lines.append(f"• {escape(src)}: {n} annonce(s)")
                await note.edit_text("\n".join(lines))
            else:
                await note.edit_text("✅ Full scan terminé: 0 annonce collectée.")
        except Exception as e:
            await note.edit_text(f"❌ Echec du full scan: <code>{escape(str(e))}</code>")

    asyncio.create_task(_run_and_report())


@router.message(Command("all_listings"))
async def cmd_all_listings(msg: Message):
    if not _is_admin(msg.from_user.id):
        await msg.answer("⛔ Accès refusé.", reply_markup=_panel(msg))
        return
    rows = repo.get_recent_listings(hours=72, limit=30)
    if not rows:
        await msg.answer("ℹ️ Aucune annonce récente en base.", reply_markup=_panel(msg))
        return
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    await msg.answer(f"📦 <b>Toutes les annonces récentes</b> ({len(rows)})", reply_markup=_panel(msg))
    for db_l in rows:
        l = ScrapedListing(
            source=db_l.source,
            external_id=db_l.external_id,
            url=db_l.url,
            title=db_l.title,
            price=db_l.price,
            surface_m2=db_l.surface_m2,
            price_per_m2=db_l.price_per_m2,
            location=db_l.location,
            rooms=db_l.rooms,
            description=db_l.description,
            images=db_l.images.split(",") if db_l.images else None,
            published_at=db_l.published_at,
            db_id=db_l.id,
        )
        text = format_message(l, score_label="", scam_tag=None)
        await msg.answer(
            text,
            disable_web_page_preview=True,
            reply_markup=listing_keyboard(l, liked=repo.is_favorite(user.id, db_l.id)),
        )


@router.message(F.text == "🎯 Configurer mes filtres")
async def panel_set_filters(msg: Message, state: FSMContext):
    await _start_filter_wizard(msg, state, filter_name="default")


@router.message(F.text == "Configurer mes filtres")
async def panel_set_filters_legacy(msg: Message, state: FSMContext):
    await _start_filter_wizard(msg, state, filter_name="default")


@router.message(F.text == "👀 Voir mes filtres")
async def panel_view_filters(msg: Message):
    await cmd_view_filters(msg)


@router.message(F.text == "📋 Mes critères")
async def panel_view_filters_new(msg: Message):
    await cmd_view_filters(msg)


@router.message(F.text == "Voir mes filtres")
async def panel_view_filters_legacy(msg: Message):
    await cmd_view_filters(msg)


@router.message(F.text == "🗂️ Gérer mes filtres")
async def panel_manage_filters(msg: Message):
    await _show_manage_filters(msg)


@router.message(F.text == "✏️ Modifier mes filtres")
async def panel_edit_filters(msg: Message, state: FSMContext):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    filters = repo.get_user_filters(user.id)
    if not filters:
        await msg.answer("ℹ️ Aucun filtre à modifier.", reply_markup=_panel(msg))
        return
    if len(filters) == 1:
        await _start_filter_wizard(msg, state, filter_name=filters[0].name)
        return
    names = ", ".join(f.name for f in filters)
    await msg.answer(
        f"ℹ️ Tu as plusieurs filtres: <code>{names}</code>\nUtilise /edit_filters &lt;nom&gt;.",
        reply_markup=_panel(msg),
    )


@router.message(F.text == "Modifier mes filtres")
async def panel_edit_filters_legacy(msg: Message, state: FSMContext):
    await panel_edit_filters(msg, state)


@router.message(F.text == "💎 Plans Premium")
async def panel_premium(msg: Message):
    await cmd_premium(msg)


@router.message(F.text == "Plans Premium")
async def panel_premium_legacy(msg: Message):
    await cmd_premium(msg)


@router.message(F.text == "❤️ Mes favoris")
async def panel_favorites(msg: Message):
    await cmd_favorites(msg)


@router.message(F.text == "🧭 Mon radar")
async def panel_status(msg: Message):
    await cmd_status(msg)


@router.message(F.text == "➕ Ajouter un filtre")
async def panel_add_filter(msg: Message, state: FSMContext):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    if not user.is_premium:
        await msg.answer(
            "🔒 Multi-filtres réservé au PREMIUM.\nUtilise /premium pour voir les avantages.",
            reply_markup=_panel(msg),
        )
        return
    filters = repo.get_user_filters(user.id)
    target = _next_filter_name(filters)
    await _start_filter_wizard(msg, state, filter_name=target)


@router.message(F.text == "🧠 Filtre IA")
async def panel_ai_filter(msg: Message):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    if not user.is_premium:
        await msg.answer(
            "🔒 Fonctionnalité réservée au PREMIUM.\nUtilise /premium pour voir les avantages.",
            reply_markup=_panel(msg),
        )
        return
    await msg.answer(
        "Décris ton besoin comme tu parlerais à un agent immobilier.\n"
        "Ex: <i>T2 ou 2 chambres à Paris 11, 35 m² min, entre 950 et 1300 € CC, proche métro</i>.\n"
        "Puis envoie: /ai_filter &lt;ta description&gt;",
        reply_markup=_panel(msg),
    )


@router.message(F.text == "⏸️ Pause alertes")
async def panel_stop(msg: Message):
    await cmd_stop(msg)


@router.message(F.text == "Pause alertes")
async def panel_stop_legacy(msg: Message):
    await cmd_stop(msg)


@router.message(F.text == "🛠️ Admin")
async def panel_admin(msg: Message):
    await cmd_admin(msg)


@router.message(F.text == "📦 Toutes annonces")
async def panel_all_listings(msg: Message):
    await cmd_all_listings(msg)


@router.message(F.text == "❓ Aide")
async def panel_help(msg: Message):
    text = (
        "❓ <b>Aide rapide</b>\n\n"
        "• /start: ouvrir le tableau de bord\n"
        "• /status: voir ton radar et l'état scraping\n"
        "• /set_filters [nom]: créer/modifier un filtre\n"
        "• /add_filter [nom]: ajouter un filtre (PREMIUM)\n"
        "• /manage_filters: menu des filtres (boutons)\n"
        "• /view_filters: voir vos critères\n"
        "• /search_now [nom]: chercher les annonces déjà en ligne\n"
        "• /favorites: voir vos annonces aimées\n"
        "• /edit_filters [nom]: modifier un filtre existant\n"
        "• /premium: voir les plans\n"
        "• /stop: mettre les alertes en pause\n"
        "• /my_id: afficher votre Telegram ID"
    )
    if _is_admin(msg.from_user.id):
        text += "\n• /admin: commandes d'administration"
        text += "\n• /debug_sources: diagnostic des sources"
        text += "\n• /all_listings: voir toutes les annonces récentes"
    await msg.answer(text, reply_markup=_panel(msg))


@router.message(F.text == "Aide")
async def panel_help_legacy(msg: Message):
    await panel_help(msg)


@router.message(F.text)
async def panel_text_fallback(msg: Message, state: FSMContext):
    # Keep wizard flows untouched.
    if await state.get_state():
        return
    raw = (msg.text or "").strip()
    if not raw or raw.startswith("/"):
        return

    t = _normalize_text(raw)
    if "configurer" in t and "filtre" in t:
        await _start_filter_wizard(msg, state, filter_name="default")
        return
    if "gerer" in t and "filtre" in t:
        await _show_manage_filters(msg)
        return
    if "modifier" in t and "filtre" in t:
        await panel_edit_filters(msg, state)
        return
    if "ajouter" in t and "filtre" in t:
        await panel_add_filter(msg, state)
        return
    if "plans" in t and "premium" in t:
        await cmd_premium(msg)
        return
    if "filtre" in t and "ia" in t:
        await panel_ai_filter(msg)
        return
    if "favoris" in t:
        await cmd_favorites(msg)
        return
    if "pause" in t and "alerte" in t:
        await cmd_stop(msg)
        return
    if t == "admin":
        await cmd_admin(msg)
        return
    if "toutes" in t and "annonce" in t:
        await cmd_all_listings(msg)
        return
    if t == "aide":
        await panel_help(msg)
        return
    looks_like_search = any(x in t for x in ("eur", "euro", "max", "piece", "pieces", "studio", "appartement", "maison"))
    if looks_like_search:
        user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
        if user.is_premium:
            text = (
                "✨ Je peux transformer cette phrase en filtre.\n"
                f"Envoie: <code>/ai_filter {escape(raw)}</code>\n\n"
                "Ou utilise 🎯 Configurer mes filtres pour le mode guidé."
            )
        else:
            text = "✨ En FREE, utilise 🎯 Configurer mes filtres pour créer ton filtre default avec le mode guidé."
        await msg.answer(text, reply_markup=_panel(msg))
        return


@router.callback_query(F.data.startswith("editflt:"))
async def cb_edit_filter(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not callback.message:
        return
    name = _sanitize_filter_name(callback.data.split(":", 1)[1] if callback.data else None)
    user = repo.get_or_create_user(callback.from_user.id, callback.from_user.username)
    filters = repo.get_user_filters(user.id)
    if name not in {f.name for f in filters}:
        await callback.message.answer(
            "⚠️ Ce filtre n'existe plus. Ouvre à nouveau la gestion des filtres.",
            reply_markup=_panel_for_user_id(callback.from_user.id),
        )
        return
    await _start_filter_wizard(callback.message, state, filter_name=name)


@router.callback_query(F.data == "addflt:auto")
async def cb_add_filter(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not callback.message:
        return
    user = repo.get_or_create_user(callback.from_user.id, callback.from_user.username)
    if not user.is_premium:
        await callback.message.answer(
            "🔒 Multi-filtres réservé au PREMIUM.\nUtilise /premium pour voir les avantages.",
            reply_markup=_panel_for_user_id(callback.from_user.id),
        )
        return
    filters = repo.get_user_filters(user.id)
    target = _next_filter_name(filters)
    await _start_filter_wizard(callback.message, state, filter_name=target)


@router.callback_query(F.data.startswith("delflt:"))
async def cb_delete_filter(callback: CallbackQuery):
    await callback.answer()
    if not callback.message or not callback.data:
        return
    name = _sanitize_filter_name(callback.data.split(":", 1)[1])
    user = repo.get_or_create_user(callback.from_user.id, callback.from_user.username)
    ok = repo.delete_filter(user.id, name)
    if ok:
        await callback.message.answer(
            f"🗑️ Filtre <b>{name}</b> supprimé.", reply_markup=_panel_for_user_id(callback.from_user.id)
        )
    else:
        await callback.message.answer(
            f"⚠️ Filtre introuvable: <code>{name}</code>.", reply_markup=_panel_for_user_id(callback.from_user.id)
        )


@router.callback_query(F.data.startswith("fav:add:"))
async def cb_add_favorite(callback: CallbackQuery):
    await callback.answer()
    if not callback.data:
        return
    try:
        listing_id = int(callback.data.split(":", 2)[2])
    except Exception:
        if callback.message:
            await callback.message.answer("⚠️ Impossible d'ajouter ce favori.")
        return

    user = repo.get_or_create_user(callback.from_user.id, callback.from_user.username)
    added = repo.add_favorite(user.id, listing_id)
    if callback.message:
        if added:
            await callback.message.answer("✅ Ajouté aux favoris.")
        else:
            await callback.message.answer("ℹ️ Déjà dans tes favoris.")
        try:
            await callback.message.edit_reply_markup(reply_markup=_favorite_action_kb(listing_id, liked=True))
        except Exception:
            pass


@router.callback_query(F.data.startswith("fav:remove:"))
async def cb_remove_favorite(callback: CallbackQuery):
    await callback.answer()
    if not callback.data:
        return
    try:
        listing_id = int(callback.data.split(":", 2)[2])
    except Exception:
        if callback.message:
            await callback.message.answer("⚠️ Impossible de retirer ce favori.")
        return

    user = repo.get_or_create_user(callback.from_user.id, callback.from_user.username)
    removed = repo.remove_favorite(user.id, listing_id)
    if callback.message:
        if removed:
            await callback.message.answer("✅ Retiré des favoris.")
        else:
            await callback.message.answer("ℹ️ Cette annonce n'était plus en favoris.")
        try:
            await callback.message.edit_reply_markup(reply_markup=_favorite_action_kb(listing_id, liked=False))
        except Exception:
            pass
