from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from urllib.parse import urlparse

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import models as m
from scraper.base import ScrapedListing
from scraper.quality import charges_included

#: Telegram caps photo captions at 1024 characters, regular messages at 4096.
_CAPTION_LIMIT = 1024
_MESSAGE_LIMIT = 4096


SOURCE_LABELS = {
    "leboncoin": "Leboncoin",
    "seloger": "SeLoger",
    "pap": "PAP",
    "paruvendu": "ParuVendu",
    "entreparticuliers": "Entreparticuliers",
    "logicimmo": "Logic-Immo",
    "rentola": "Rentola",
    "ouestfranceimmo": "OuestFrance-Immo",
}


def _money(value: int | None) -> str | None:
    if value is None:
        return None
    return f"{value:,} €".replace(",", " ")


def _compact(text: str | None, *, limit: int = 180) -> str | None:
    if not text:
        return None
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _source_label(source: str | None) -> str:
    if not source:
        return "Source inconnue"
    return SOURCE_LABELS.get(source, source.replace("_", " ").title())


def _host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).netloc
    except Exception:
        return None
    return host.removeprefix("www.") if host else None


def _relative_time(value: datetime | None) -> str | None:
    """Render ``publié il y a 3 h`` from a (possibly naive-UTC) timestamp."""
    if value is None:
        return None
    try:
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - moment
    except Exception:
        return None
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return None
    if seconds < 3600:
        minutes = max(1, seconds // 60)
        return f"il y a {minutes} min"
    if seconds < 86400:
        return f"il y a {seconds // 3600} h"
    days = seconds // 86400
    if days < 30:
        return f"il y a {days} j"
    return None


def _charges_chip(listing: ScrapedListing) -> str | None:
    """Rental-aware: only claim "charges comprises" when the text says so."""
    blob = " ".join(
        part for part in (listing.title, listing.description) if part
    )
    if charges_included(blob) is True:
        return "✅ charges comprises"
    return None


def format_message(l: ScrapedListing, score_label: str, scam_tag: str | None) -> str:
    title = escape(_compact(l.title, limit=110) or "Annonce immobilière")
    source = escape(_source_label(l.source))
    host = escape(_host(l.url) or l.source or "source")

    chips: list[str] = []
    if l.price is not None:
        chips.append(f"💰 <b>{_money(l.price)}</b>")
    if l.surface_m2 is not None:
        chips.append(f"📐 {l.surface_m2:.0f} m²")
    if l.rooms is not None:
        chips.append(f"🛏️ {l.rooms} p.")
    if l.price_per_m2 is not None:
        chips.append(f"📊 {int(l.price_per_m2):,} €/m²".replace(",", " "))
    if (chip := _charges_chip(l)) is not None:
        chips.append(chip)

    header = ["🏡 <b>Nouvelle opportunité</b>", f"<b>{title}</b>", f"🛰️ {source} · <code>{host}</code>"]
    if chips:
        header.append(" · ".join(chips))

    meta: list[str] = []
    if l.location:
        meta.append(f"📍 {escape(_compact(l.location, limit=90) or l.location)}")
    if (age := _relative_time(l.published_at)) is not None:
        meta.append(f"🕒 {escape(age)}")

    body = list(header)
    if meta:
        body.append(" · ".join(meta))
    if l.description:
        body.append(f"\n{escape(_compact(l.description, limit=220) or '')}")
    if score_label:
        body.append(f"⭐ <b>Matching</b>: {escape(score_label)}")
    if scam_tag:
        body.append(f"🚨 <b>{escape(scam_tag)}</b>")

    if l.url:
        body.append(f"\n🔗 <a href=\"{escape(l.url, quote=True)}\">Ouvrir l'annonce</a>")
    else:
        body.append(f"\n🔗 <i>Lien indisponible ({source})</i>")

    text = "\n".join(body)
    # Never let an over-long body be silently dropped by the API.
    if len(text) > _MESSAGE_LIMIT:
        text = text[: _MESSAGE_LIMIT - 1].rstrip() + "…"
    return text


def listing_keyboard(l: ScrapedListing, *, liked: bool = False) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    if l.url:
        rows.append([InlineKeyboardButton(text="🔎 Ouvrir l'annonce", url=l.url)])
    if getattr(l, "db_id", None):
        if liked:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="💔 Retirer des favoris",
                        callback_data=f"fav:remove:{l.db_id}",
                    )
                ]
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="❤️ Garder cette annonce",
                        callback_data=f"fav:add:{l.db_id}",
                    )
                ]
            )
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def send_alert(bot: Bot, user: m.User, l: ScrapedListing, score_label: str, scam_tag: str | None):
    text = format_message(l, score_label, scam_tag)
    kb = listing_keyboard(l)
    image = next((u for u in (l.images or []) if isinstance(u, str) and u.startswith("http")), None)

    # A caption is capped at 1024 chars: sending a longer one always raises and
    # then costs a second API call. Only attach the photo when it fits.
    if image and len(text) <= _CAPTION_LIMIT:
        try:
            await bot.send_photo(chat_id=user.telegram_id, photo=image, caption=text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=user.telegram_id, text=text, disable_web_page_preview=False, reply_markup=kb)
