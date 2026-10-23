from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from html import unescape as html_unescape
from typing import Optional

import requests

from scraper.base import ScrapedListing
from config import settings
from utils.text import parse_price_eur, parse_surface_m2

#: Payment rails that never belong in a French rental transaction. A single
#: hit is enough to flag the listing.
STRONG_KEYWORDS = (
    "western union",
    "mandat cash",
    "mandat postal",
    "virement international",
    "transfert d argent",
    "prepaiement",
    "prepaye",
    "bitcoin",
    "crypto monnaie",
    "cryptomonnaie",
    "moneygram",
    "ria transfert",
)

#: Ambiguous phrases: common in scams but also in legitimate listings. Two of
#: them (or one plus a price anomaly) are required.
WEAK_KEYWORDS = (
    "urgent partir",
    "ne pas appeler",
    "pas sur place",
    "je suis a l etranger",
    "proprietaire a l etranger",
    "aucune visite",
    "sans visite",
    "payer a distance",
    "copies de papiers",
    "copie des papiers",
    "sans etat des lieux",
    "ne pas se deplacer",
    "envoi des cles",
    "cles par transporteur",
)

#: Plausible monthly rent per m² for a whole dwelling in France. Below this the
#: listing is almost certainly bait; between ``_LOW`` and ``_TIGHT`` it is only
#: a warning sign.
_PPM2_SCAM = 5.0
_PPM2_TIGHT = 8.0
#: Below this monthly rent, outside of student housing, something is off.
_MIN_MONTHLY_RENT = 200
#: Above this a "rental" is probably a sale price or a fake.
_MAX_MONTHLY_RENT = 20000

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_POSTAL_RE = re.compile(r"\b\d{5}\b")


@dataclass
class ScamResult:
    is_scam: bool
    reason: Optional[str] = None
    #: Every signal found, strongest first. Useful for /debug_sample and logs.
    signals: list[str] = field(default_factory=list)


def _norm(value: str | None) -> str:
    """Lowercase, unaccent, and flatten punctuation to single spaces."""
    if not value:
        return ""
    flat = unicodedata.normalize("NFKD", html_unescape(value))
    flat = "".join(c for c in flat if not unicodedata.combining(c)).lower()
    flat = flat.replace("'", " ").replace("’", " ")
    return " ".join(_NON_ALNUM.sub(" ", flat).split())


def _hits(text: str, keywords: tuple[str, ...]) -> list[str]:
    """Whole-phrase matches only: ``prepaye`` must not match ``prepayement``."""
    return [kw for kw in keywords if f" {kw} " in f" {text} "]


def _detect_rent(text: str) -> Optional[int]:
    """Extract the *rent* specifically, skipping deposits and agency fees."""
    return parse_price_eur(text, monthly_only=True)


def detect_scam(l: ScrapedListing) -> ScamResult:
    """Rental-aware scam heuristic.

    Returns a verdict plus every signal that fired. Strong payment-rail
    keywords are decisive on their own; everything else needs corroboration
    so that legitimate cheap studios are not labelled as scams.
    """
    text = _norm(f"{l.title or ''} {l.description or ''}")
    if not text and l.price is None:
        return ScamResult(is_scam=False)

    strong = _hits(text, STRONG_KEYWORDS)
    weak = _hits(text, WEAK_KEYWORDS)
    signals: list[str] = []

    if strong:
        signals.append(f"paiement suspect: {strong[0]}")

    if l.price is not None and l.price <= 0:
        signals.append("prix nul")

    # Ambiguity guard: ``l.price`` can be a deposit when the listing advertises
    # "Dépôt de garantie 2400 €, loyer 800 €/mois". Trust the explicit rent.
    rent = _detect_rent(text)
    effective_price = rent if rent is not None else l.price
    if rent is not None and l.price is not None and rent != l.price:
        signals.append(f"loyer explicite {rent} € (prix affiche {l.price} €)")

    if effective_price is not None and effective_price > 0:
        if effective_price < _MIN_MONTHLY_RENT:
            signals.append(f"loyer tres bas ({effective_price} €)")
        elif effective_price > _MAX_MONTHLY_RENT:
            signals.append(f"montant invraisemblable ({effective_price} €)")

    surface = l.surface_m2
    if surface is None:
        # Recover the surface from the description when the scraper missed it.
        surface = parse_surface_m2(text)

    if effective_price and surface and surface > 0:
        ppm2 = effective_price / surface
        if ppm2 < _PPM2_SCAM:
            signals.append(f"loyer a {ppm2:.1f} €/m2/mois")
        elif ppm2 < _PPM2_TIGHT:
            weak.append(f"loyer bas ({ppm2:.1f} €/m2/mois)")

    if surface is not None:
        if surface < 9:
            signals.append(f"surface invraisemblable ({surface:g} m²)")
        elif surface > 1000:
            signals.append(f"surface excessive ({surface:g} m²)")

    for kw in weak:
        signals.append(f"formulation suspecte: {kw}")

    if not signals:
        return ScamResult(is_scam=False)

    # One hard signal, or two corroborating ones.
    hard = strong or any(
        s.startswith(("prix nul", "loyer tres bas", "loyer a ", "montant invraisemblable", "surface "))
        for s in signals
    )
    is_scam = bool(hard) or len(signals) >= 2
    reason = "; ".join(signals[:3])[:200] if is_scam else None
    return ScamResult(is_scam=is_scam, reason=reason, signals=signals)

    # Optional AI-based analysis
    if settings.ai_scam_enabled and settings.ai_scam_endpoint:
        try:
            result = _ai_assess_listing(l)
            result = _validate_ai_assessment(result)
            if result and result.get("is_scam"):
                reason = str(result.get("reason") or "IA: signal scam")[:200]
                return ScamResult(is_scam=True, reason=reason)
        except Exception:
            pass

    return ScamResult(is_scam=False)


def _ai_assess_listing(l: ScrapedListing) -> Optional[dict]:
    """
    Calls a configurable AI endpoint to assess scam likelihood.
    Expected JSON response schema: {"is_scam": bool, "reason": str}
    """
    endpoint = settings.ai_scam_endpoint
    if not endpoint:
        return None
    headers = {"Content-Type": "application/json"}
    if settings.ai_scam_api_key:
        headers["Authorization"] = f"Bearer {settings.ai_scam_api_key}"
    payload = {
        "model": settings.ai_scam_model,
        "task": "scam-detection",
        "input": {
            "title": l.title,
            "description": l.description,
            "price": l.price,
            "surface_m2": l.surface_m2,
            "price_per_m2": l.price_per_m2,
            "location": l.location,
            "rooms": l.rooms,
            "url": l.url,
            "source": l.source,
        },
    }
    try:
        resp = requests.post(endpoint, headers=headers, data=json.dumps(payload), timeout=settings.ai_scam_timeout)
        if resp.status_code >= 200 and resp.status_code < 300:
            data = resp.json()
            # Accept either direct schema or nested result
            if isinstance(data, dict) and ("is_scam" in data or "reason" in data):
                return data
            if isinstance(data, dict) and isinstance(data.get("result"), dict):
                return data["result"]
    except Exception:
        return None
    return None


def _validate_ai_assessment(data: Optional[dict]) -> Optional[dict]:
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("is_scam"), bool):
        return None
    reason = data.get("reason")
    if reason is not None and not isinstance(reason, str):
        reason = str(reason)
    return {"is_scam": data["is_scam"], "reason": reason}
