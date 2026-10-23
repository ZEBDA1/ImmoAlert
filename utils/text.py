"""Text and number parsing helpers tuned for French real-estate listings.

French listings mix many numeric formats:

* ``1 250 €`` / ``1 250,50 €`` / ``1.250 €`` / ``1250€``
* ``29m²`` / ``29 m2`` / ``29,5 m²`` / ``1 196 m² de bureaux``
* ``T2`` / ``2 pièces`` / ``4p`` / ``studio``

The helpers below normalise those into plain Python numbers and return
``None`` instead of raising, so a single malformed card never breaks a
whole scraping cycle.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Only the non-breaking variants are collapsed. A plain ASCII space must survive
# so that `\b` word boundaries keep working ("890 euros", "3 pieces", "T2 950").
_NBSP = ("\xa0", "\u202f", "\u2009")
_DASHES = ("\u2013", "\u2014", "\u2212")


def _clean_numeric(value: Optional[str]) -> str:
    if value is None:
        return ""
    # NFKD folds compatibility forms (``m²`` -> ``m2``, ligatures) which the
    # surface/price patterns rely on, but it also splits accents into a base
    # letter plus a combining mark, which would defeat patterns like
    # ``pi[eè]ces?``. Recomposing with NFC keeps the folding and restores
    # single-codepoint accents.
    s = unicodedata.normalize("NFKD", str(value))
    s = unicodedata.normalize("NFC", s)
    for ch in _NBSP:
        s = s.replace(ch, " ")
    for dash in _DASHES:
        s = s.replace(dash, "-")
    return " ".join(s.split())


# A number whose thousands groups are separated by a space: "1 250", "1 250 000".
# The lookahead keeps "T2 950" from being read as the single number 2950.
_NUMBER_RE = re.compile(
    r"(?<![\d.,\u00a0\u202fA-Za-z])(?:"
    r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d{1,3})*"
    r"|\d+(?:[.,]\d{1,3})*)"
)


def parse_int(s: Optional[str]) -> Optional[int]:
    """Extract the first integer from *s*.

    Handles French thousand separators (space, dot) and decimal commas::

        >>> parse_int("1 250 €")
        1250
        >>> parse_int("1.250,00 €")
        1250
        >>> parse_int("T2 - 29 m²")
        2
        >>> parse_int(None) is None
        True
    """
    s = _clean_numeric(s)
    if not s:
        return None

    match = _NUMBER_RE.search(s)
    if not match:
        return None
    number = _normalize_number(match.group(0))
    if number is None:
        return None
    return int(number)


def parse_float(s: Optional[str]) -> Optional[float]:
    """Extract the first decimal number from *s* (French formats supported)."""
    s = _clean_numeric(s)
    if not s:
        return None
    match = _NUMBER_RE.search(s)
    if not match:
        return None
    return _normalize_number(match.group(0))


def _normalize_number(raw: str) -> Optional[float]:
    """Turn ``1.250,50`` / ``1 250.50`` / ``1,5`` into a float."""
    if not raw:
        return None
    negative = raw.startswith("-")
    # Spaces only ever act as thousands separators at this point ("1 250").
    body = raw.lstrip("-").replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
    if not body:
        return None

    has_comma = "," in body
    has_dot = "." in body

    if has_comma and has_dot:
        # The right-most separator is the decimal one.
        if body.rfind(",") > body.rfind("."):
            body = body.replace(".", "").replace(",", ".")
        else:
            body = body.replace(",", "")
    elif has_comma:
        # Repeated separators can only be thousands groups: "1,250,000".
        if body.count(",") > 1:
            body = body.replace(",", "")
        else:
            # "1,250" is ambiguous: treat 3 trailing digits as thousands.
            head, _, tail = body.rpartition(",")
            if len(tail) == 3 and head:
                body = head + tail
            else:
                body = body.replace(",", ".")
    elif has_dot:
        if body.count(".") > 1:
            # "1.250.000" -> repeated separators, all thousands.
            body = body.replace(".", "")
        else:
            # "1.250" -> thousands, "1.5" -> decimal.
            head, _, tail = body.rpartition(".")
            if len(tail) == 3 and head and not head.endswith("."):
                body = head + tail
            # else keep the dot as decimal separator

    try:
        value = float(body)
    except (TypeError, ValueError):
        return None
    return -value if negative else value


# The leading lookbehind stops the engine from starting in the middle of a
# number ("1.250,50" must not yield "250,50") or inside a room label
# ("T2 950 €" must yield 950, not 2950).
_NOT_MID_NUMBER = r"(?<![\d.,\u00a0\u202fA-Za-z])"
_PRICE_RE = re.compile(
    _NOT_MID_NUMBER
    # The space-grouped branch is lazy so that "2 400 € ... 800 €/mois" yields
    # two amounts instead of one merged "2 400 800"; the currency anchor below
    # makes the engine backtrack for genuinely grouped values ("1 250 000 €").
    + r"(\d{1,3}(?:[ \u00a0\u202f]\d{3})+?(?:[.,]\d{1,3})*|\d+(?:[.,]\d{1,3})*)"
    r"\s*(?:€|eur(?:os?)?\b)",
    re.IGNORECASE,
)
_MONTHLY_HINT_RE = re.compile(r"(/|\bpar\b|\bmois\b|/mois|mensuel)", re.IGNORECASE)
# Amounts introduced by these words are not rents.
_NON_RENT_PREFIX_RE = re.compile(
    r"(?:d[ée]p[ôo]t\s+de\s+garantie|caution|honoraires|frais\s+d.agence|charges?\s*:)\s*$",
    re.IGNORECASE,
)


def parse_price_eur(s: Optional[str], *, monthly_only: bool = False) -> Optional[int]:
    """Parse a price in euros out of free text.

    ``monthly_only`` rejects amounts that are clearly not rents (deposit,
    fees, "honoraires") when the surrounding text says so.
    """
    if not s:
        return None
    text = _clean_numeric(s)
    if not text:
        return None

    for match in _PRICE_RE.finditer(text):
        # When a deposit and a rent share a card ("caution 2 400 €, loyer
        # 800 €/mois"), the first amount is not the rent: skip it instead of
        # discarding the whole string.
        if monthly_only and _NON_RENT_PREFIX_RE.search(text[: match.start()]):
            continue
        value = _normalize_number(match.group(1))
        if value is None or value <= 0:
            continue
        return int(round(value))
    return None


_SURFACE_RE = re.compile(
    _NOT_MID_NUMBER
    + r"(\d{1,3}(?:[ \u00a0\u202f]\d{3})+?(?:[.,]\d{1,3})*|\d+(?:[.,]\d{1,3})*)"
    r"\s*(?:m²|m2|m\^2|mètres?\s*carrés?|metres?\s*carres?)",
    re.IGNORECASE,
)


def parse_surface_m2(s: Optional[str]) -> Optional[float]:
    """Parse a surface in m² out of free text (``29m²``, ``1 196 m²``...)."""
    if not s:
        return None
    text = _clean_numeric(s)
    match = _SURFACE_RE.search(text)
    if not match:
        return None
    value = _normalize_number(match.group(1).replace(" ", ""))
    if value is None or value <= 0 or value > 5000:
        return None
    return round(value, 1)


_ROOMS_RE = re.compile(
    r"\b(\d{1,2})\s*(?:pi[eè]ces?|p\b|chambres?)\b|"
    r"\b[tT]\s*(\d{1,2})\b|"
    r"\b[fF]\s*(\d{1,2})\b",
    re.IGNORECASE,
)


def parse_rooms(s: Optional[str]) -> Optional[int]:
    """Parse a room count from ``T2``, ``4 pièces``, ``2 chambres`` or ``studio``."""
    if not s:
        return None
    text = _clean_numeric(s)
    if not text:
        return None
    if re.search(r"\bstud(?:io|ette)\b", text, re.IGNORECASE):
        return 1
    match = _ROOMS_RE.search(text)
    if not match:
        return None
    raw = next((g for g in match.groups() if g), None)
    if not raw:
        return None
    try:
        rooms = int(raw)
    except ValueError:
        return None
    if rooms <= 0 or rooms > 30:
        return None
    return rooms


def clean_text(value: Optional[str], *, max_len: Optional[int] = None) -> Optional[str]:
    """Collapse whitespace, drop nbsp artefacts and optionally truncate."""
    if not value:
        return None
    cleaned = " ".join(str(value).replace("\xa0", " ").replace("\u202f", " ").split())
    if not cleaned:
        return None
    if max_len and len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" ,;:-–—") + "…"
    return cleaned


def is_monthly_rent_text(value: Optional[str]) -> bool:
    """Heuristic: does the text describe a monthly rent?"""
    return bool(_MONTHLY_HINT_RE.search(value or ""))
