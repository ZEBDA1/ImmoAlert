from __future__ import annotations

import re
import unicodedata
from typing import Optional, Dict, Any

from utils.ai import call_ai

_PROPERTY_SYNONYMS = {
    "studio": ("studio", "studette", "t1", "f1"),
    "appartement": ("appartement", "appart", "t2", "t3", "t4", "t5", "f2", "f3", "f4", "f5", "duplex", "loft"),
    "maison": ("maison", "villa", "pavillon"),
}

_CITY_STOPWORDS = {
    "cherche",
    "recherche",
    "veux",
    "voudrais",
    "besoin",
    "budget",
    "loyer",
    "prix",
    "surface",
    "minimum",
    "maximum",
    "max",
    "min",
    "avec",
    "sans",
    "charges",
    "comprises",
    "piece",
    "pieces",
    "chambre",
    "chambres",
    "appartement",
    "appart",
    "studio",
    "maison",
    "proche",
    "autour",
    "rayon",
}

_ALLOWED_OUTPUT_KEYS = {
    "city",
    "postal_code",
    "radius_km",
    "price_min",
    "price_max",
    "surface_min",
    "rooms_min",
    "property_type",
    "budget_max_with_charges",
}


def _coerce_int(v) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        return int(float(str(v).replace(" ", "").replace(",", ".")))
    except Exception:
        return None


def _coerce_float(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(str(v).replace(" ", "").replace(",", "."))
    except Exception:
        return None


def _strip_accents(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(c)
    )


def _norm_text(value: str) -> str:
    return _strip_accents(value).lower().replace("\xa0", " ")


def _compact_city(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.replace("’", "'")
    cleaned = re.sub(r"\s*-\s*", "-", cleaned)
    cleaned = " ".join(cleaned.strip(" -_,.;:()[]{}").split())
    if not cleaned or len(cleaned) > 80:
        return None
    if _norm_text(cleaned) in _CITY_STOPWORDS:
        return None
    return cleaned


def _amount_to_int(raw: str | None, suffix: str | None = None, *, has_decimal: bool = False) -> Optional[int]:
    if not raw:
        return None
    suffix_norm = (suffix or "").lower()
    cleaned = raw.replace(" ", "")
    if not has_decimal and suffix_norm not in {"k", "ke", "k€"}:
        cleaned = cleaned.replace(".", "")
    value = _coerce_float(cleaned)
    if value is None:
        return None
    if suffix_norm in {"k", "ke", "k€"}:
        value *= 1000
    return int(round(value))


def _iter_amounts(text: str):
    pattern = re.compile(
        r"(?<!\d)(\d{1,3}(?:[\s.]?\d{3})*|\d+)(?:[,.](\d+))?\s*(k€|ke|k(?!m)|€|eur|euros?)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        raw = match.group(1)
        has_decimal = bool(match.group(2))
        if match.group(2):
            raw = f"{raw}.{match.group(2)}"
        yield match, _amount_to_int(raw, match.group(3), has_decimal=has_decimal)


def _extract_prices(text: str, out: Dict[str, Any]) -> None:
    norm = _norm_text(text)

    between = re.search(
        r"\bentre\s+(\d[\d\s.,]*)\s*(k€|k|ke|€|eur|euros?)?\s+(?:et|a|-)\s+(\d[\d\s.,]*)\s*(k€|k|ke|€|eur|euros?)",
        norm,
        re.IGNORECASE,
    )
    if between:
        lo = _amount_to_int(between.group(1), between.group(2) or between.group(4))
        hi = _amount_to_int(between.group(3), between.group(4))
        if lo is not None and hi is not None:
            out["price_min"], out["price_max"] = sorted((lo, hi))

    for match, amount in _iter_amounts(norm):
        if amount is None:
            continue
        left = norm[max(0, match.start() - 42):match.start()]
        right = norm[match.end():match.end() + 42]
        window = f"{left} {right}"
        if any(token in window for token in ("charges comprises", "cc", "avec charges", "charge comprise", "tout compris")):
            out["budget_max_with_charges"] = amount
        if any(token in window for token in ("max", "maximum", "moins de", "jusqu", "budget", "loyer", "prix", "sous")):
            out.setdefault("price_max", amount)
        elif any(token in window for token in ("min", "minimum", "au moins", "a partir", "partir de", "plus de")):
            out.setdefault("price_min", amount)
        else:
            out.setdefault("price_max", amount)

    if out.get("budget_max_with_charges") and not out.get("price_max"):
        out["price_max"] = out["budget_max_with_charges"]


def _extract_surface(text: str, out: Dict[str, Any]) -> None:
    norm = _norm_text(text)
    surface_patterns = [
        r"(?:surface\s*)?(?:min(?:imum)?|au moins|plus de|a partir de)\s*(\d+(?:[,.]\d+)?)\s*m(?:2|²|etres?\s*carres?)",
        r"(\d+(?:[,.]\d+)?)\s*m(?:2|²|etres?\s*carres?)\s*(?:min(?:imum)?|mini|au moins)?",
    ]
    for pattern in surface_patterns:
        m = re.search(pattern, norm, re.IGNORECASE)
        if m:
            out["surface_min"] = _coerce_float(m.group(1))
            return


def _extract_rooms(text: str, out: Dict[str, Any]) -> None:
    norm = _norm_text(text)
    if re.search(r"\b(studio|studette)\b", norm):
        out["rooms_min"] = 1
        out["property_type"] = "studio"
        return

    values: list[int] = []
    for m in re.finditer(r"\b(?:t|f)\s*([1-9])\b", norm, re.IGNORECASE):
        values.append(int(m.group(1)))
    for m in re.finditer(r"\b(\d+)\s*p(?:ieces?)?\b|\b(\d+)\s*pi[eè]ces?\b", norm, re.IGNORECASE):
        values.append(int(m.group(1) or m.group(2)))
    for m in re.finditer(r"\b(\d+)\s*chambres?\b", norm, re.IGNORECASE):
        # In French listings, rooms/pieces usually include the living room.
        values.append(int(m.group(1)) + 1)
    if values:
        out["rooms_min"] = max(values)


def _extract_property_type(text: str, out: Dict[str, Any]) -> None:
    norm = _norm_text(text)
    for canonical, words in _PROPERTY_SYNONYMS.items():
        if any(re.search(rf"\b{re.escape(word)}\b", norm) for word in words):
            out["property_type"] = canonical
            return


def _extract_location(text: str, out: Dict[str, Any]) -> None:
    norm = _norm_text(text)
    postal = re.search(r"\b(\d{5})\b", text)
    if postal:
        out["postal_code"] = postal.group(1)

    paris_arr = re.search(r"\bparis\s*(?:-|/|\s)?(?:0?)([1-9]|1\d|20)(?:e|eme|er)?\b", norm)
    if paris_arr:
        out["city"] = "Paris"
        out["postal_code"] = f"750{int(paris_arr.group(1)):02d}"
        return

    candidates: list[str] = []
    location_patterns = [
        r"(?:a|à|sur|dans|autour de|proche de|pres de|près de|vers)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' -]{1,60})(?=,|\.|;|\s+(?:avec|sans|pour|budget|loyer|prix|max|min|rayon|dans|a|à|de|du|des|et)\b|$)",
        r"\b([A-ZÀ-Ÿ][A-Za-zÀ-ÿ' -]{2,60})\s*(?:\(\s*\d{5}\s*\))",
    ]
    for pattern in location_patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            city = _compact_city(m.group(1))
            if city and _norm_text(city) not in _CITY_STOPWORDS:
                candidates.append(city)

    if not candidates:
        caps = re.findall(r"\b([A-ZÀ-Ÿ][A-Za-zÀ-ÿ'’-]{2,}(?:[-\s][A-ZÀ-Ÿ]?[A-Za-zÀ-ÿ'’-]{2,}){0,3})\b", text)
        for city in caps:
            city = _compact_city(city)
            if city and _norm_text(city).split()[0] not in _CITY_STOPWORDS:
                candidates.append(city)

    if candidates and not out.get("city"):
        out["city"] = candidates[0].replace("’", "'")


def _extract_radius(text: str, out: Dict[str, Any]) -> None:
    norm = _norm_text(text)
    m = re.search(
        r"(?:rayon|autour|alentours|dans)\s*(?:de|d'environ|environ|max)?\s*(\d+(?:[,.]\d+)?)\s*(?:km|kilometres?)",
        norm,
        re.IGNORECASE,
    )
    if m:
        out["radius_km"] = _coerce_float(m.group(1))


def _normalize_property_type(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    norm = _norm_text(value)
    for canonical, words in _PROPERTY_SYNONYMS.items():
        if norm == canonical or any(word in norm for word in words):
            return canonical
    return None


def _clean_output(out: Dict[str, Any]) -> Dict[str, Any]:
    city = _compact_city(out.get("city")) if isinstance(out.get("city"), str) else None
    postal = out.get("postal_code")
    postal = postal if isinstance(postal, str) and len(postal) == 5 and postal.isdigit() else None
    price_min = _coerce_int(out.get("price_min"))
    price_max = _coerce_int(out.get("price_max"))
    if price_min is not None and price_max is not None and price_min > price_max:
        price_min, price_max = price_max, price_min
    radius = _coerce_float(out.get("radius_km"))
    surface = _coerce_float(out.get("surface_min"))
    rooms = _coerce_int(out.get("rooms_min"))
    budget_charges = _coerce_int(out.get("budget_max_with_charges"))

    cleaned: Dict[str, Any] = {
        "city": city,
        "postal_code": postal,
        "radius_km": radius if radius and radius > 0 else None,
        "price_min": price_min if price_min and price_min > 0 else None,
        "price_max": price_max if price_max and price_max > 0 else None,
        "surface_min": surface if surface and surface > 0 else None,
        "rooms_min": rooms if rooms and rooms > 0 else None,
        "property_type": _normalize_property_type(out.get("property_type")),
        "budget_max_with_charges": budget_charges if budget_charges and budget_charges > 0 else None,
    }
    if cleaned["budget_max_with_charges"] and not cleaned["price_max"]:
        cleaned["price_max"] = cleaned["budget_max_with_charges"]
    return cleaned


def _validate_ai_output(result: Any) -> Dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    unknown = set(result) - _ALLOWED_OUTPUT_KEYS
    if unknown:
        return None
    cleaned = _clean_output(result)
    # A valid AI response may be empty, but it must not contain uncoercible explicit values only.
    explicit_values = [value for value in result.values() if value not in (None, "")]
    if explicit_values and not any(value is not None for value in cleaned.values()):
        return None
    return cleaned


def _merge_ai_and_fallback(result: Dict[str, Any] | None, fallback: Dict[str, Any]) -> Dict[str, Any]:
    ai = _validate_ai_output(result) or {}
    fb = _clean_output(fallback)
    merged = dict(ai)
    for key, value in fb.items():
        if value is not None and merged.get(key) is None:
            merged[key] = value

    # Local parsing is usually more reliable for explicit numeric constraints.
    for key in ("postal_code", "price_min", "price_max", "surface_min", "rooms_min", "budget_max_with_charges", "radius_km"):
        if fb.get(key) is not None:
            merged[key] = fb[key]
    if fb.get("property_type") is not None:
        merged["property_type"] = fb["property_type"]
    if fb.get("city") is not None and (
        not merged.get("city")
        or len(str(merged["city"])) <= 2
        or len(_norm_text(str(fb["city"])).split()) > len(_norm_text(str(merged["city"])).split())
    ):
        merged["city"] = fb["city"]
    return {k: v for k, v in merged.items() if v is not None}


def _fallback_parse(text: str) -> Dict[str, Any]:
    """Compatibility wrapper kept for imports/tests."""
    out: Dict[str, Any] = {}
    _extract_location(text, out)
    _extract_radius(text, out)
    _extract_prices(text, out)
    _extract_surface(text, out)
    _extract_rooms(text, out)
    _extract_property_type(text, out)
    return {k: v for k, v in out.items() if v is not None}


def ai_parse_filter(text: str) -> Dict[str, Any]:
    """
    Use AI (if available) to parse a natural language filter text into filter fields.
    Returns a dict with keys compatible with repo.create_or_update_filter.
    Fallbacks to a simple regex-based parser if AI is disabled or fails.
    """
    text = text.strip()
    if not text:
        return {}

    result = call_ai(
        task="parse-filter",
        input={
            "instruction": (
                "Extract a French real-estate search filter. Be conservative: only fill fields that are explicit. "
                "Understand synonyms: T2/F2 means 2 rooms, 2 chambres means at least 3 pieces, "
                "CC/charges comprises means budget_max_with_charges, Paris 11 means city Paris and postal_code 75011. "
                "For ranges like entre 800 et 1200 euros, set price_min=800 and price_max=1200."
            ),
            "fields": [
                "city", "postal_code", "radius_km", "price_min", "price_max",
                "surface_min", "rooms_min", "property_type", "budget_max_with_charges"
            ],
            "text": text,
            "constraints": {
                "city": "string or null",
                "postal_code": "5-digit string or null",
                "radius_km": "float or null",
                "price_min": "int or null",
                "price_max": "int or null",
                "surface_min": "float or null",
                "rooms_min": "int or null",
                "property_type": "one of: studio, appartement, maison, null",
                "budget_max_with_charges": "int or null",
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "city": {"type": ["string", "null"]},
                    "postal_code": {"type": ["string", "null"]},
                    "radius_km": {"type": ["number", "null"]},
                    "price_min": {"type": ["integer", "null"]},
                    "price_max": {"type": ["integer", "null"]},
                    "surface_min": {"type": ["number", "null"]},
                    "rooms_min": {"type": ["integer", "null"]},
                    "property_type": {"type": ["string", "null"]},
                    "budget_max_with_charges": {"type": ["integer", "null"]},
                },
                "additionalProperties": False,
            },
        },
    )

    fb = _fallback_parse(text)
    result = _validate_ai_output(result)
    if not isinstance(result, dict):
        return {k: v for k, v in _clean_output(fb).items() if v is not None}

    return _merge_ai_and_fallback(result, fb)
