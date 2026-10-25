from __future__ import annotations

import asyncio
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import uuid4

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from database import repo
from database import models as m
from scraper.base import ScrapedListing
from scraper.quality import (
    charges_included,
    extract_postal_code,
    listing_quality_flags,
    normalize_city_name,
    normalize_listing,
    quality_signature,
)
from scraper.leboncoin import LeboncoinScraper
from scraper.seloger import SeLogerScraper
from scraper.pap import PAPScraper
from scraper.paruvendu import ParuVenduScraper
from scraper.entreparticuliers import EntreParticuliersScraper
from scraper.logicimmo import LogicImmoScraper
from scraper.rentola import RentolaScraper
from scraper.ouestfranceimmo import OuestFranceImmoScraper
from services.matcher import match_and_score
from services.scam import detect_scam
from services.notification import send_alert
from config import settings
from utils.images import decode_images, encode_images
from utils.fetch_diagnostics import capture_problems

log = logging.getLogger(__name__)


SCRAPERS = [
    ParuVenduScraper(),
    EntreParticuliersScraper(),
    LeboncoinScraper(),
    SeLogerScraper(),
    PAPScraper(),
    LogicImmoScraper(),
    RentolaScraper(),
    OuestFranceImmoScraper(),
]
_source_health: dict[str, dict] = {}
_source_inflight: dict[str, dict] = {}
# Timed-out synchronous fetches may keep running until their HTTP timeout.
# Keep them out of asyncio's default executor, which Telegram's DNS lookups
# also use, so slow portals cannot prevent the bot from replying.
_scraper_executor = ThreadPoolExecutor(
    max_workers=max(8, min(32, len(SCRAPERS) * settings.per_source_concurrency)),
    thread_name_prefix="scraper",
)


@dataclass
class LocationFetchResult:
    city: str | None
    postal: str | None
    listings: list[ScrapedListing] = field(default_factory=list)
    raw_count: int = 0
    normalized_count: int = 0
    dropped_count: int = 0
    timeout_count: int = 0
    error_count: int = 0
    error: str | None = None


@dataclass
class SourceCollectResult:
    source: str
    started_at: datetime
    finished_at: datetime
    listings: list[ScrapedListing] = field(default_factory=list)
    locations_count: int = 0
    raw_count: int = 0
    normalized_count: int = 0
    dropped_count: int = 0
    unique_count: int = 0
    timeout_count: int = 0
    error_count: int = 0
    last_error: str | None = None
    status: str = "success"

    @property
    def duration_seconds(self) -> float:
        return round((self.finished_at - self.started_at).total_seconds(), 2)


@dataclass
class ListingDeliveryResult:
    status: str
    is_new: bool = False


@dataclass
class ExistingBackfillResult:
    sources_count: int = 0
    unique_count: int = 0
    matched_count: int = 0
    sent_count: int = 0
    pending_count: int = 0
    skipped_count: int = 0
    new_listing_count: int = 0
    error_count: int = 0

    @property
    def action_count(self) -> int:
        return self.sent_count + self.pending_count


def _now_utc() -> datetime:
    return datetime.utcnow()


def _ensure_source_health(source: str) -> dict:
    if source not in _source_health:
        _source_health[source] = {
            "consecutive_failures": 0,
            "total_failures": 0,
            "total_success": 0,
            "total_runs": 0,
            "total_listings": 0,
            "last_listings_count": 0,
            "last_raw_count": 0,
            "last_normalized_count": 0,
            "last_dropped_count": 0,
            "last_unique_count": 0,
            "last_timeout_count": 0,
            "last_error_count": 0,
            "consecutive_empty_runs": 0,
            "last_error": None,
            "last_failure_at": None,
            "last_success_at": None,
            "disabled_until": None,
            "last_cycle_seconds": None,
            "last_locations_count": 0,
            "last_new_count": 0,
            "last_status": "never_run",
            "last_cycle_id": None,
        }
    return _source_health[source]


def _health_from_run(run: m.SourceRun) -> dict:
    return {
        "consecutive_failures": 1 if run.status in {"failed", "partial"} else 0,
        "total_failures": 1 if run.status in {"failed", "partial"} else 0,
        "total_success": 1 if run.status == "success" and run.unique_count > 0 else 0,
        "total_runs": 1,
        "total_listings": run.unique_count,
        "last_listings_count": run.unique_count,
        "last_raw_count": run.raw_count,
        "last_normalized_count": run.normalized_count,
        "last_dropped_count": run.dropped_count,
        "last_unique_count": run.unique_count,
        "last_timeout_count": run.timeout_count,
        "last_error_count": run.error_count,
        "consecutive_empty_runs": 1 if run.unique_count == 0 else 0,
        "last_error": run.last_error,
        "last_failure_at": run.finished_at if run.status in {"failed", "partial"} else None,
        "last_success_at": run.finished_at if run.status == "success" and run.unique_count > 0 else None,
        "disabled_until": run.disabled_until,
        "last_cycle_seconds": run.duration_seconds,
        "last_locations_count": run.locations_count,
        "last_new_count": run.new_count,
        "last_status": run.status,
        "last_cycle_id": run.cycle_id,
    }


def get_sources_health() -> dict[str, dict]:
    out = {source: dict(raw) for source, raw in _source_health.items()}
    try:
        for run in repo.get_latest_source_runs(limit=50):
            if run.source not in out or not out[run.source].get("last_cycle_id"):
                out[run.source] = _health_from_run(run)
    except Exception:
        pass
    return out


def source_names() -> list[str]:
    """Registered source slugs, in scheduler order (single source of truth)."""
    return [s.source for s in SCRAPERS]


def get_scraper(source: str):
    for s in SCRAPERS:
        if s.source == source:
            return s
    return None


def _source_enabled(source: str) -> bool:
    if repo.get_runtime_setting(f"source_enabled:{source}") == "false":
        return False
    h = _ensure_source_health(source)
    until = h.get("disabled_until")
    if not until:
        return True
    return until <= _now_utc()


def _disable_backoff_seconds(consecutive_failures: int) -> int:
    if consecutive_failures < 3:
        return 0
    step = settings.scheduler_source_backoff_min_seconds * (2 ** (consecutive_failures - 3))
    return min(settings.scheduler_source_backoff_max_seconds, step)


def _record_source_result(cycle_id: str, result: SourceCollectResult, new_count: int) -> None:
    h = _ensure_source_health(result.source)
    h["total_runs"] += 1
    h["last_cycle_id"] = cycle_id
    h["last_status"] = result.status
    h["last_listings_count"] = result.unique_count
    h["last_raw_count"] = result.raw_count
    h["last_normalized_count"] = result.normalized_count
    h["last_dropped_count"] = result.dropped_count
    h["last_unique_count"] = result.unique_count
    h["last_timeout_count"] = result.timeout_count
    h["last_error_count"] = result.error_count
    h["total_listings"] += max(0, result.unique_count)
    h["last_cycle_seconds"] = result.duration_seconds
    h["last_locations_count"] = result.locations_count
    h["last_new_count"] = new_count
    h["last_error"] = result.last_error[:400] if result.last_error else None

    if result.unique_count > 0:
        h["consecutive_empty_runs"] = 0
    else:
        h["consecutive_empty_runs"] += 1

    if result.status == "success" and result.unique_count > 0:
        h["consecutive_failures"] = 0
        h["total_success"] += 1
        h["last_success_at"] = _now_utc()
        h["last_failure_at"] = None
        h["disabled_until"] = None
    else:
        h["consecutive_failures"] += 1
        h["total_failures"] += 1
        h["last_failure_at"] = _now_utc()
        backoff = _disable_backoff_seconds(h["consecutive_failures"])
        h["disabled_until"] = _now_utc() + timedelta(seconds=backoff) if backoff > 0 else None

    try:
        repo.save_source_run(
            cycle_id=cycle_id,
            source=result.source,
            status=result.status,
            started_at=result.started_at,
            finished_at=result.finished_at,
            duration_seconds=result.duration_seconds,
            locations_count=result.locations_count,
            raw_count=result.raw_count,
            normalized_count=result.normalized_count,
            dropped_count=result.dropped_count,
            unique_count=result.unique_count,
            new_count=new_count,
            timeout_count=result.timeout_count,
            error_count=result.error_count,
            disabled_until=h.get("disabled_until"),
            last_error=h.get("last_error"),
        )
    except Exception as exc:
        log.warning("Failed to persist source run for %s: %s", result.source, exc)


def _score_label_for_filter(filter_name: str | None, score_label: str) -> str:
    if not filter_name or filter_name == "default":
        return score_label
    return f"{score_label} · filtre {filter_name}"


def _unique_locations(filters: list[m.Filter]) -> list[tuple[str | None, str | None]]:
    seen: set[tuple[str | None, str | None]] = set()
    out: list[tuple[str | None, str | None]] = []

    def _norm(value: str | None) -> str | None:
        if value is None:
            return None
        v = value.strip()
        return v or None

    for f in filters:
        key = (_norm(f.city), _norm(f.postal_code))
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _build_scheduler_locations(filters: list[m.Filter]) -> list[tuple[str | None, str | None]]:
    base = _unique_locations(filters)
    if not settings.expand_location_variants:
        out = list(base)
        if settings.include_generic_location_pass and (None, None) not in out:
            out.append((None, None))
        return out

    seen: set[tuple[str | None, str | None]] = set()
    out: list[tuple[str | None, str | None]] = []

    def _add(city: str | None, postal: str | None) -> None:
        key = (city, postal)
        if key in seen:
            return
        seen.add(key)
        out.append(key)

    for city, postal in base:
        _add(city, postal)
        if city and postal:
            _add(city, None)
            _add(None, postal)
        elif city:
            _add(city, None)
        elif postal:
            _add(None, postal)

    if settings.include_generic_location_pass:
        _add(None, None)

    return out


def _listing_metadata(listing: ScrapedListing) -> dict:
    text = " ".join(v for v in (listing.title, listing.description, listing.location) if v)
    return {
        "canonical_signature": quality_signature(listing),
        "quality_flags": ",".join(listing_quality_flags(listing)) or None,
        "normalized_city": normalize_city_name(listing.location),
        "normalized_postal_code": extract_postal_code(text),
        "charges_included": charges_included(text),
    }


def _match_reasons_text(reasons: list[str] | None) -> str | None:
    return "; ".join(reasons or []) or None


def _persist_listing_candidate(listing: ScrapedListing) -> repo.ListingSaveResult:
    original = listing
    listing = normalize_listing(listing)
    if listing is None:
        return repo.ListingSaveResult(listing=None, is_new=False, duplicate_reason="invalid_rental")
    metadata = _listing_metadata(listing)
    if listing.price is not None and listing.surface_m2 and not listing.price_per_m2 and listing.surface_m2 > 0:
        listing.price_per_m2 = listing.price / listing.surface_m2
    saved = repo.save_listing(
        source=listing.source,
        external_id=listing.external_id,
        url=listing.url,
        title=listing.title,
        price=listing.price,
        surface_m2=listing.surface_m2,
        price_per_m2=listing.price_per_m2,
        location=listing.location,
        rooms=listing.rooms,
        description=listing.description,
        images=encode_images(listing.images),
        published_at=listing.published_at,
        last_seen_at=_now_utc(),
        inventory_status="active",
        **metadata,
    )
    if saved.listing:
        listing.db_id = saved.listing.id
        original.db_id = saved.listing.id
    return saved


async def _deliver_listing_to_user_filter(bot: Bot, user: m.User, f: m.Filter, listing: ScrapedListing) -> ListingDeliveryResult:
    if not user.telegram_id:
        return ListingDeliveryResult(status="telegram_not_linked")
    res = match_and_score(f, listing)
    if not res.matched:
        return ListingDeliveryResult(status="not_matched")

    saved = _persist_listing_candidate(listing)
    if saved.is_new and saved.listing:
        from services.listing_details import resolve_new_listing
        original_id=saved.listing.id
        saved.listing = await resolve_new_listing(saved.listing)
        saved.is_new=saved.listing.id==original_id
        listing.db_id = saved.listing.id
    if not saved.listing:
        return ListingDeliveryResult(status="not_saved")

    db_listing = saved.listing
    if repo.has_sent_alert(user.id, db_listing.id):
        return ListingDeliveryResult(status="already_sent", is_new=saved.is_new)

    score_label = _score_label_for_filter(f.name, res.score_label)
    match_reasons = _match_reasons_text(res.reasons)

    if user.is_premium:
        scam = await asyncio.to_thread(detect_scam, listing)
        scam_tag = "Potentielle arnaque" if scam.is_scam else None
        await send_alert(bot, user, listing, score_label, scam_tag)
        repo.mark_alert_sent(
            user.id,
            db_listing.id,
            filter_id=f.id,
            filter_name=f.name,
            score_value=res.score_value,
            score_label=score_label,
            match_reasons=match_reasons,
        )
        return ListingDeliveryResult(status="sent", is_new=saved.is_new)

    not_before = datetime.utcnow() + timedelta(seconds=settings.premium_free_delay_seconds)
    added = repo.add_pending_alert(
        user.id,
        db_listing.id,
        not_before,
        filter_id=f.id,
        filter_name=f.name,
        score_value=res.score_value,
        score_label=score_label,
        match_reasons=match_reasons,
    )
    return ListingDeliveryResult(status="pending" if added else "already_pending", is_new=saved.is_new)


async def _process_new_listing(bot: Bot, listing: ScrapedListing) -> bool:
    saved = _persist_listing_candidate(listing)
    if saved.is_new and saved.listing:
        from services.listing_details import resolve_new_listing
        original_id=saved.listing.id
        saved.listing = await resolve_new_listing(saved.listing)
        saved.is_new=saved.listing.id==original_id
        listing.db_id = saved.listing.id
    if not saved.listing:
        return False
    db_listing = saved.listing

    filters = repo.get_all_active_filters()
    users_cache: dict[int, m.User] = {}
    scam_tag: str | None = None
    scam_checked = False

    for f in filters:
        if f.user_id not in users_cache:
            u = repo.get_user_by_id(f.user_id)
            if not u or not u.active or not u.telegram_id:
                continue
            users_cache[f.user_id] = u
        user = users_cache[f.user_id]

        res = match_and_score(f, listing)
        if not res.matched:
            continue

        if repo.has_sent_alert(user.id, db_listing.id):
            continue

        if not scam_checked:
            scam = await asyncio.to_thread(detect_scam, listing)
            scam_tag = "Potentielle arnaque" if scam.is_scam else None
            scam_checked = True

        score_label = _score_label_for_filter(f.name, res.score_label)
        match_reasons = _match_reasons_text(res.reasons)

        if user.is_premium:
            try:
                await send_alert(bot, user, listing, score_label, scam_tag)
                repo.mark_alert_sent(
                    user.id,
                    db_listing.id,
                    filter_id=f.id,
                    filter_name=f.name,
                    score_value=res.score_value,
                    score_label=score_label,
                    match_reasons=match_reasons,
                )
            except Exception as e:
                log.exception("Failed to send premium alert: %s", e)
                repo.add_pending_alert(
                    user.id,
                    db_listing.id,
                    _now_utc() + timedelta(seconds=60),
                    filter_id=f.id,
                    filter_name=f.name,
                    score_value=res.score_value,
                    score_label=score_label,
                    match_reasons=match_reasons,
                )
        else:
            not_before = datetime.utcnow() + timedelta(seconds=settings.premium_free_delay_seconds)
            repo.add_pending_alert(
                user.id,
                db_listing.id,
                not_before,
                filter_id=f.id,
                filter_name=f.name,
                score_value=res.score_value,
                score_label=score_label,
                match_reasons=match_reasons,
            )

    return saved.is_new


async def _dispatch_pending(bot: Bot):
    now = datetime.utcnow()
    for pa in repo.fetch_due_pending_alerts(now):
        user = repo.get_user_by_id(pa.user_id)
        if not user or not user.active or not user.telegram_id:
            repo.set_pending_alert_status(pa.id, "canceled")
            continue
        db_listing = repo.get_listing_by_id(pa.listing_id)
        if not db_listing or not repo.is_current_listing(db_listing):
            repo.set_pending_alert_status(pa.id, "canceled")
            continue
        l = ScrapedListing(
            source=db_listing.source,
            external_id=db_listing.external_id,
            url=db_listing.url,
            title=db_listing.title,
            price=db_listing.price,
            surface_m2=db_listing.surface_m2,
            price_per_m2=db_listing.price_per_m2,
            location=db_listing.location,
            rooms=db_listing.rooms,
            description=db_listing.description,
            images=decode_images(db_listing.images) or None,
            published_at=db_listing.published_at,
            db_id=db_listing.id,
        )
        if repo.has_sent_alert(user.id, db_listing.id):
            repo.set_pending_alert_status(pa.id, "canceled")
            continue

        matched_filter: m.Filter | None = None
        matched_result = None
        filters = repo.get_user_filters(user.id)
        if pa.filter_id:
            filters = sorted(filters, key=lambda f: 0 if f.id == pa.filter_id else 1)
        for f in filters:
            res = match_and_score(f, l)
            if res.matched:
                matched_filter = f
                matched_result = res
                break
        if not matched_filter or not matched_result:
            repo.set_pending_alert_status(pa.id, "canceled")
            continue

        try:
            scam = await asyncio.to_thread(detect_scam, l)
            scam_tag = "Potentielle arnaque" if scam.is_scam else None
            score_label = _score_label_for_filter(matched_filter.name, matched_result.score_label)
            await send_alert(bot, user, l, score_label=score_label, scam_tag=scam_tag)
            repo.mark_alert_sent(
                user.id,
                db_listing.id,
                filter_id=matched_filter.id,
                filter_name=matched_filter.name,
                score_value=matched_result.score_value,
                score_label=score_label,
                match_reasons=_match_reasons_text(matched_result.reasons),
            )
        except TelegramForbiddenError:
            with repo.session_scope() as s:
                stored_user = s.get(m.User, user.id)
                if stored_user:
                    stored_user.active = False
            repo.cancel_pending_alerts_for_user(user.id)
            log.info("Alerts paused for unreachable user %s", user.id)
        except TelegramBadRequest as exc:
            repo.set_pending_alert_status(pa.id, "canceled")
            log.warning("Invalid pending alert %s: %s", pa.id, exc)
        except Exception as exc:
            retry_after = exc.retry_after if isinstance(exc, TelegramRetryAfter) else 60
            with repo.session_scope() as s:
                pending = s.get(m.PendingAlert, pa.id)
                if pending:
                    pending.not_before = _now_utc() + timedelta(seconds=retry_after)
            log.warning("Pending alert %s will be retried: %s", pa.id, exc)


def _source_timeout_seconds(scraper) -> float:
    """Per-source timeout.

    Firecrawl-backed sources render pages in a stealth browser (10-30 s per
    page), so they need a much larger budget than the HTTP-only scrapers.
    """
    base = max(8, settings.scraper_task_timeout_seconds)
    from utils.scrapling import scrapling_enabled

    if scrapling_enabled():
        base = max(base, settings.scrapling_timeout_seconds * 2 + settings.flashproxy_timeout_seconds * 2 + 10)
    if settings.firecrawl_enabled:
        base = max(base, settings.scrapling_timeout_seconds * 2 + settings.firecrawl_timeout_seconds + 30)
    override = getattr(scraper, "timeout_seconds", None)
    if callable(override):
        try:
            override = override()
        except Exception:
            override = None
    if isinstance(override, (int, float)) and override > 0:
        base = max(base, int(override))
    elif getattr(scraper, "uses_browser", False):
        base = max(base, settings.firecrawl_timeout_seconds * 2)
    return float(base)


async def _fetch_listings_non_blocking(scraper, city: str | None, postal: str | None, problems: list[str] | None = None) -> list[ScrapedListing]:
    loop = asyncio.get_running_loop()
    def fetch():
        with capture_problems(problems if problems is not None else []):
            return list(scraper.fetch_city(city, postal))
    return await asyncio.wait_for(
        loop.run_in_executor(_scraper_executor, fetch),
        timeout=_source_timeout_seconds(scraper),
    )


async def _collect_one_location(scraper, city: str | None, postal: str | None) -> LocationFetchResult:
    result = LocationFetchResult(city=city, postal=postal)
    try:
        problems: list[str] = []
        raw = await _fetch_listings_non_blocking(scraper, city, postal, problems)
        result.raw_count = len(raw)
        for listing in raw:
            item = normalize_listing(listing, fallback_city=city, fallback_postal=postal)
            if item:
                result.listings.append(item)
                result.normalized_count += 1
            else:
                result.dropped_count += 1
        if not result.listings and (problems or raw):
            result.error_count = 1
            result.error = "; ".join(problems)[:400] if problems else "all_records_rejected_by_quality_checks"
    except asyncio.TimeoutError:
        result.timeout_count = 1
        result.error = f"timeout for {city}/{postal}"
        log.warning("Scraper %s timed out for %s/%s", scraper.source, city, postal)
    except Exception as exc:
        result.error_count = 1
        result.error = str(exc)[:400]
        if "404 Client Error" in result.error or "Playwright Sync API" in result.error:
            log.info("Scraper %s unavailable for %s/%s: %s", scraper.source, city, postal, exc)
        else:
            log.warning("Scraper %s failed for %s/%s: %s", scraper.source, city, postal, exc)
    return result


async def _collect_source_listings(scraper, locs: list[tuple[str | None, str | None]]) -> SourceCollectResult:
    identifier=uuid4().hex
    progress={"id":identifier,"source":scraper.source,"started_at":_now_utc().isoformat()+"Z","locations_count":len(locs),"completed":0}
    _source_inflight[identifier]=progress
    try:
        return await _collect_source_listings_impl(scraper,locs,progress)
    finally:
        _source_inflight.pop(identifier,None)


async def _collect_source_listings_impl(scraper, locs, progress):
    started = _now_utc()
    sem = asyncio.Semaphore(max(1, settings.per_source_concurrency))

    async def _bounded(city: str | None, postal: str | None) -> LocationFetchResult:
        async with sem:
            try:
                return await _collect_one_location(scraper, city, postal)
            finally:
                progress["completed"]+=1

    tasks = [asyncio.create_task(_bounded(city, postal)) for city, postal in locs]
    gathered = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

    location_results: list[LocationFetchResult] = []
    unexpected_errors: list[str] = []
    for item in gathered:
        if isinstance(item, Exception):
            unexpected_errors.append(str(item)[:400])
        else:
            location_results.append(item)

    all_listings: list[ScrapedListing] = []
    raw_count = normalized_count = dropped_count = timeout_count = error_count = 0
    errors: list[str] = list(unexpected_errors)
    for item in location_results:
        raw_count += item.raw_count
        normalized_count += item.normalized_count
        dropped_count += item.dropped_count
        timeout_count += item.timeout_count
        error_count += item.error_count
        if item.error:
            errors.append(item.error)
        all_listings.extend(item.listings)
    error_count += len(unexpected_errors)

    unique: list[ScrapedListing] = []
    seen: set[str] = set()
    for listing in all_listings:
        key = f"{listing.source}:{listing.external_id}"
        fuzzy = quality_signature(listing)
        if key in seen or fuzzy in seen:
            continue
        seen.add(key)
        seen.add(fuzzy)
        unique.append(listing)

    if timeout_count or error_count:
        status = "partial" if unique else "failed"
    elif unique:
        status = "success"
    else:
        status = "empty"
        errors.append("No rental extracted; source availability could not be confirmed")

    return SourceCollectResult(
        source=scraper.source,
        started_at=started,
        finished_at=_now_utc(),
        listings=unique,
        locations_count=len(locs),
        raw_count=raw_count,
        normalized_count=normalized_count,
        dropped_count=dropped_count,
        unique_count=len(unique),
        timeout_count=timeout_count,
        error_count=error_count,
        last_error=errors[-1] if errors else None,
        status=status,
    )


def _failed_collect_result(source: str, locs_count: int, exc: Exception) -> SourceCollectResult:
    now = _now_utc()
    return SourceCollectResult(
        source=source,
        started_at=now,
        finished_at=now,
        locations_count=locs_count,
        error_count=1,
        last_error=str(exc)[:400],
        status="failed",
    )


def _sample_locs_for_source(source: str, locs: list[tuple[str | None, str | None]]) -> list[tuple[str | None, str | None]]:
    if not locs:
        return []
    if settings.full_scan_mode:
        return list(locs)
    quota = settings.source_quota_per_cycle.get(source, settings.default_source_quota)
    if quota <= 0:
        return []
    if len(locs) <= quota:
        return list(locs)
    try:
        return random.sample(locs, k=quota)
    except Exception:
        return list(locs)[:quota]


async def sample_source_listings(
    source: str,
    locs: list[tuple[str | None, str | None]],
    *,
    limit_locations: int = 1,
) -> tuple[list[ScrapedListing], str | None]:
    scraper = get_scraper(source)
    if not scraper:
        return ([], f"Unknown source: {source}")
    results: list[ScrapedListing] = []
    last_error: str | None = None
    to_visit = list(locs)[: max(0, limit_locations)] if limit_locations > 0 else []
    if not to_visit:
        to_visit = [(None, None)]
    for city, postal in to_visit:
        item = await _collect_one_location(scraper, city, postal)
        results.extend(item.listings)
        if item.error:
            last_error = item.error
    return (results, last_error)


async def _run_scan_cycle(
    bot: Bot,
    *,
    cycle_id: str,
    scan_locs: list[tuple[str | None, str | None]] | None = None,
    full_scan: bool = False,
) -> dict[str, int]:
    filters = repo.get_all_active_filters()
    locs = scan_locs if scan_locs is not None else _build_scheduler_locations(filters)
    enabled: list[tuple[object, list[tuple[str | None, str | None]]]] = []
    for scraper in SCRAPERS:
        if not _source_enabled(scraper.source):
            h = _ensure_source_health(scraper.source)
            log.info("Skipping source %s (disabled until %s)", scraper.source, h.get("disabled_until"))
            continue
        source_locs = list(locs) if full_scan else _sample_locs_for_source(scraper.source, locs)
        if source_locs:
            enabled.append((scraper, source_locs))

    tasks = [asyncio.create_task(_collect_source_listings(scraper, source_locs)) for scraper, source_locs in enabled]
    gathered = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

    per_source_counts: dict[str, int] = {}
    for (scraper, source_locs), item in zip(enabled, gathered):
        result = _failed_collect_result(scraper.source, len(source_locs), item) if isinstance(item, Exception) else item
        new_count = 0
        for listing in result.listings:
            if await _process_new_listing(bot, listing):
                new_count += 1
        _record_source_result(cycle_id, result, new_count)
        per_source_counts[result.source] = result.unique_count
        log.info(
            "scheduler cycle=%s source=%s status=%s locs=%s raw=%s normalized=%s dropped=%s unique=%s new=%s timeouts=%s errors=%s duration=%s",
            cycle_id,
            result.source,
            result.status,
            result.locations_count,
            result.raw_count,
            result.normalized_count,
            result.dropped_count,
            result.unique_count,
            new_count,
            result.timeout_count,
            result.error_count,
            result.duration_seconds,
        )

    await _dispatch_pending(bot)
    try:
        repo.prune_source_runs(keep_days=settings.source_run_retention_days)
    except Exception:
        pass
    return per_source_counts


def _locations_for_filter(f: m.Filter) -> list[tuple[str | None, str | None]]:
    locs = _build_scheduler_locations([f])
    if locs:
        return locs
    return [(None, None)]


async def run_filter_backfill_once(
    bot: Bot,
    user: m.User,
    flt: m.Filter,
    *,
    limit: int = 5,
) -> ExistingBackfillResult:
    """
    Fetch current source inventory for one user filter.

    This is intentionally quota-bound and reuses the same source collection path as
    the scheduler, but delivery is scoped to the requesting user/filter so existing
    announcements can be shown even when they are not brand-new globally.
    """
    out = ExistingBackfillResult()
    if limit <= 0:
        return out

    locs = _locations_for_filter(flt)
    enabled: list[tuple[object, list[tuple[str | None, str | None]]]] = []
    for scraper in SCRAPERS:
        if not _source_enabled(scraper.source):
            continue
        source_locs = _sample_locs_for_source(scraper.source, locs)
        if source_locs:
            enabled.append((scraper, source_locs))

    out.sources_count = len(enabled)
    cycle_id = f"backfill-{_now_utc():%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
    tasks = [asyncio.create_task(_collect_source_listings(scraper, source_locs)) for scraper, source_locs in enabled]
    gathered = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

    for (scraper, source_locs), item in zip(enabled, gathered):
        result = _failed_collect_result(scraper.source, len(source_locs), item) if isinstance(item, Exception) else item
        out.error_count += result.timeout_count + result.error_count
        out.unique_count += result.unique_count
        new_count_for_source = 0

        for listing in result.listings:
            if out.action_count >= limit:
                break
            delivery = await _deliver_listing_to_user_filter(bot, user, flt, listing)
            if delivery.status == "not_matched":
                continue
            out.matched_count += 1
            if delivery.is_new:
                out.new_listing_count += 1
                new_count_for_source += 1
            if delivery.status == "sent":
                out.sent_count += 1
            elif delivery.status == "pending":
                out.pending_count += 1
            else:
                out.skipped_count += 1

        _record_source_result(cycle_id, result, new_count_for_source)

    return out


async def run_scheduler(bot: Bot, *, interval_min: int = 30, interval_max: int = 60):
    while True:
        cycle_id = f"{_now_utc():%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
        try:
            await _run_scan_cycle(bot, cycle_id=cycle_id)
        except Exception as e:
            log.exception("Scheduler iteration failed: %s", e)
        await asyncio.sleep(random.randint(interval_min, interval_max))


async def run_full_scan_once(bot: Bot) -> dict[str, int]:
    filters = repo.get_all_active_filters()
    locs = _build_scheduler_locations(filters)
    scan_locs = list(locs) or [(None, None)]
    cycle_id = f"full-{_now_utc():%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
    return await _run_scan_cycle(bot, cycle_id=cycle_id, scan_locs=scan_locs, full_scan=True)
