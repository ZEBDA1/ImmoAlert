from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import select, and_, func, delete, update, desc, asc

from config import settings
from .session import SessionLocal
from . import models as m


@dataclass
class ListingSaveResult:
    listing: m.Listing | None
    is_new: bool
    duplicate_reason: str | None = None
    duplicate_listing_id: int | None = None


def _refresh_premium_status(user: m.User) -> None:
    try:
        if user.premium_until and user.premium_until <= datetime.utcnow():
            user.is_premium = False
            user.premium_until = None
    except Exception:
        pass



@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Users
def get_or_create_user(telegram_id: int, username: Optional[str] = None) -> m.User:
    with session_scope() as s:
        user = s.execute(select(m.User).where(m.User.telegram_id == telegram_id)).scalar_one_or_none()
        if user:
            if username and user.username != username:
                user.username = username
                s.add(user)
            _refresh_premium_status(user)
            return user
        user = m.User(telegram_id=telegram_id, username=username)
        s.add(user)
        s.flush()
        return user


def set_user_premium(telegram_id: int, is_premium: bool) -> None:
    with session_scope() as s:
        user = s.execute(select(m.User).where(m.User.telegram_id == telegram_id)).scalar_one_or_none()
        if not user:
            user = m.User(telegram_id=telegram_id)
        user.is_premium = is_premium
        if not is_premium:
            user.premium_until = None
        s.add(user)


def set_user_premium_for(telegram_id: int, *, seconds: int) -> None:
    if seconds <= 0:
        set_user_premium(telegram_id, False)
        return
    until = datetime.utcnow() + timedelta(seconds=seconds)
    with session_scope() as s:
        user = s.execute(select(m.User).where(m.User.telegram_id == telegram_id)).scalar_one_or_none()
        if not user:
            user = m.User(telegram_id=telegram_id)
        user.is_premium = True
        user.premium_until = until
        s.add(user)


def get_user_by_id(user_id: int) -> m.User | None:
    with session_scope() as s:
        user = s.execute(select(m.User).where(m.User.id == user_id)).scalar_one_or_none()
        if user:
            _refresh_premium_status(user)
            s.add(user)
        return user


def get_user_by_telegram_id(telegram_id: int) -> m.User | None:
    with session_scope() as s:
        user = s.execute(select(m.User).where(m.User.telegram_id == telegram_id)).scalar_one_or_none()
        if user:
            _refresh_premium_status(user)
            s.add(user)
        return user


def get_user_filters(user_id: int) -> list[m.Filter]:
    with session_scope() as s:
        return list(s.execute(select(m.Filter).where(m.Filter.user_id == user_id, m.Filter.active == True)).scalars())


def create_or_update_filter(
    user_id: int,
    name: str = "default",
    price_min: Optional[int] = None,
    price_max: Optional[int] = None,
    surface_min: Optional[float] = None,
    rooms_min: Optional[int] = None,
    property_type: Optional[str] = None,
    budget_max_with_charges: Optional[int] = None,
    city: Optional[str] = None,
    postal_code: Optional[str] = None,
    radius_km: Optional[float] = None,
) -> m.Filter:
    with session_scope() as s:
        f = s.execute(
            select(m.Filter).where(m.Filter.user_id == user_id, m.Filter.name == name)
        ).scalar_one_or_none()
        if not f:
            f = m.Filter(user_id=user_id, name=name)
        f.price_min = price_min
        f.price_max = price_max
        f.surface_min = surface_min
        f.rooms_min = rooms_min
        f.property_type = property_type
        f.budget_max_with_charges = budget_max_with_charges
        f.city = city
        f.postal_code = postal_code
        f.radius_km = radius_km
        # Always reactivate the filter on create/update
        f.active = True
        s.add(f)
        s.flush()
        s.execute(
            update(m.PendingAlert)
            .where(
                m.PendingAlert.user_id == user_id,
                m.PendingAlert.filter_id == f.id,
                m.PendingAlert.status == "pending",
            )
            .values(status="canceled")
        )
        s.flush()
        return f


def delete_filter(user_id: int, name: str) -> bool:
    with session_scope() as s:
        f = s.execute(select(m.Filter).where(m.Filter.user_id == user_id, m.Filter.name == name)).scalar_one_or_none()
        if not f:
            return False
        f.active = False
        s.add(f)
        s.execute(
            update(m.PendingAlert)
            .where(
                m.PendingAlert.user_id == user_id,
                m.PendingAlert.filter_id == f.id,
                m.PendingAlert.status == "pending",
            )
            .values(status="canceled")
        )
        return True


# Listings
def get_or_create_listing(
    *,
    source: str,
    external_id: str,
    url: str,
    title: Optional[str] = None,
    price: Optional[int] = None,
    surface_m2: Optional[float] = None,
    price_per_m2: Optional[float] = None,
    location: Optional[str] = None,
    rooms: Optional[int] = None,
    description: Optional[str] = None,
    images: Optional[str] = None,
    published_at: Optional[datetime] = None,
) -> m.Listing | None:
    result = save_listing(
        source=source,
        external_id=external_id,
        url=url,
        title=title,
        price=price,
        surface_m2=surface_m2,
        price_per_m2=price_per_m2,
        location=location,
        rooms=rooms,
        description=description,
        images=images,
        published_at=published_at,
    )
    return result.listing if result.is_new else None


def save_listing(
    *,
    source: str,
    external_id: str,
    url: str,
    title: Optional[str] = None,
    price: Optional[int] = None,
    surface_m2: Optional[float] = None,
    price_per_m2: Optional[float] = None,
    location: Optional[str] = None,
    rooms: Optional[int] = None,
    description: Optional[str] = None,
    images: Optional[str] = None,
    published_at: Optional[datetime] = None,
    canonical_signature: Optional[str] = None,
    quality_flags: Optional[str] = None,
    normalized_city: Optional[str] = None,
    normalized_postal_code: Optional[str] = None,
    charges_included: Optional[bool] = None,
) -> ListingSaveResult:
    with session_scope() as s:
        existing = s.execute(
            select(m.Listing).where(m.Listing.source == source, m.Listing.external_id == external_id)
        ).scalar_one_or_none()
        if existing:
            return ListingSaveResult(
                listing=existing,
                is_new=False,
                duplicate_reason="source_external_id",
                duplicate_listing_id=existing.id,
            )

        if canonical_signature:
            since = datetime.utcnow() - timedelta(hours=settings.dedupe_window_hours)
            duplicate = s.execute(
                select(m.Listing)
                .where(
                    m.Listing.canonical_signature == canonical_signature,
                    m.Listing.first_seen_at >= since,
                )
                .order_by(m.Listing.first_seen_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if duplicate:
                return ListingSaveResult(
                    listing=duplicate,
                    is_new=False,
                    duplicate_reason="canonical_signature",
                    duplicate_listing_id=duplicate.id,
                )

        listing = m.Listing(
            source=source,
            external_id=external_id,
            url=url,
            title=title,
            price=price,
            surface_m2=surface_m2,
            price_per_m2=price_per_m2,
            location=location,
            rooms=rooms,
            description=description,
            images=images,
            published_at=published_at,
            canonical_signature=canonical_signature,
            quality_flags=quality_flags,
            normalized_city=normalized_city,
            normalized_postal_code=normalized_postal_code,
            charges_included=charges_included,
        )
        s.add(listing)
        s.flush()
        return ListingSaveResult(listing=listing, is_new=True)


def has_sent_alert(user_id: int, listing_id: int) -> bool:
    with session_scope() as s:
        cnt = s.execute(
            select(func.count()).select_from(m.SentAlert).where(
                m.SentAlert.user_id == user_id, m.SentAlert.listing_id == listing_id
            )
        ).scalar_one()
        return cnt > 0


def mark_alert_sent(
    user_id: int,
    listing_id: int,
    *,
    filter_id: int | None = None,
    filter_name: str | None = None,
    score_value: float | None = None,
    score_label: str | None = None,
    match_reasons: str | None = None,
) -> None:
    with session_scope() as s:
        s.execute(
            update(m.PendingAlert)
            .where(
                m.PendingAlert.user_id == user_id,
                m.PendingAlert.listing_id == listing_id,
                m.PendingAlert.status == "pending",
            )
            .values(status="sent")
        )
        existing = s.execute(
            select(m.SentAlert).where(
                m.SentAlert.user_id == user_id,
                m.SentAlert.listing_id == listing_id,
            )
        ).scalar_one_or_none()
        if existing:
            return
        rec = m.SentAlert(
            user_id=user_id,
            listing_id=listing_id,
            filter_id=filter_id,
            filter_name=filter_name,
            score_value=score_value,
            score_label=score_label,
            match_reasons=match_reasons,
        )
        s.add(rec)


def add_pending_alert(
    user_id: int,
    listing_id: int,
    not_before: datetime,
    *,
    filter_id: int | None = None,
    filter_name: str | None = None,
    score_value: float | None = None,
    score_label: str | None = None,
    match_reasons: str | None = None,
) -> bool:
    with session_scope() as s:
        sent = s.execute(
            select(m.SentAlert.id).where(
                m.SentAlert.user_id == user_id,
                m.SentAlert.listing_id == listing_id,
            )
        ).first()
        if sent:
            return False
        existing = s.execute(
            select(m.PendingAlert).where(
                m.PendingAlert.user_id == user_id,
                m.PendingAlert.listing_id == listing_id,
            )
        ).scalar_one_or_none()
        if existing and existing.status != "canceled":
            return False
        pa = existing or m.PendingAlert(user_id=user_id, listing_id=listing_id)
        pa.status = "pending"
        pa.not_before = not_before
        pa.filter_id = filter_id
        pa.filter_name = filter_name
        pa.score_value = score_value
        pa.score_label = score_label
        pa.match_reasons = match_reasons
        s.add(pa)
        return True


def cancel_pending_alerts_for_user(user_id: int) -> int:
    with session_scope() as s:
        res = s.execute(
            update(m.PendingAlert)
            .where(m.PendingAlert.user_id == user_id, m.PendingAlert.status == "pending")
            .values(status="canceled")
        )
        return int(res.rowcount or 0)


def fetch_due_pending_alerts(now: datetime) -> list[m.PendingAlert]:
    with session_scope() as s:
        rows = list(
            s.execute(
                select(m.PendingAlert).where(m.PendingAlert.status == "pending", m.PendingAlert.not_before <= now)
            ).scalars()
        )
        # Eager load IDs before session closes
        for r in rows:
            _ = r.id
        return rows


def set_pending_alert_status(pending_id: int, status: str) -> None:
    with session_scope() as s:
        s.execute(update(m.PendingAlert).where(m.PendingAlert.id == pending_id).values(status=status))


def get_all_active_filters() -> list[m.Filter]:
    with session_scope() as s:
        return list(s.execute(select(m.Filter).where(m.Filter.active == True)).scalars())


def get_listing_by_id(listing_id: int) -> m.Listing | None:
    with session_scope() as s:
        return s.execute(select(m.Listing).where(m.Listing.id == listing_id)).scalar_one_or_none()


def get_recent_listings(*, hours: int = 48, limit: int = 300) -> list[m.Listing]:
    since = datetime.utcnow() - timedelta(hours=hours)
    with session_scope() as s:
        rows = s.execute(
            select(m.Listing)
            .where(m.Listing.first_seen_at >= since)
            .order_by(m.Listing.first_seen_at.desc())
            .limit(limit)
        ).scalars()
        return list(rows)


# Favorites
def add_favorite(user_id: int, listing_id: int) -> bool:
    with session_scope() as s:
        existing = s.execute(
            select(m.Favorite).where(m.Favorite.user_id == user_id, m.Favorite.listing_id == listing_id)
        ).scalar_one_or_none()
        if existing:
            return False
        s.add(m.Favorite(user_id=user_id, listing_id=listing_id))
        return True


def remove_favorite(user_id: int, listing_id: int) -> bool:
    with session_scope() as s:
        fav = s.execute(
            select(m.Favorite).where(m.Favorite.user_id == user_id, m.Favorite.listing_id == listing_id)
        ).scalar_one_or_none()
        if not fav:
            return False
        s.delete(fav)
        return True


def is_favorite(user_id: int, listing_id: int) -> bool:
    with session_scope() as s:
        cnt = s.execute(
            select(func.count()).select_from(m.Favorite).where(
                m.Favorite.user_id == user_id, m.Favorite.listing_id == listing_id
            )
        ).scalar_one()
        return cnt > 0


def get_user_favorite_listings(user_id: int, *, limit: int = 50, sort: str = "recent") -> list[m.Listing]:
    with session_scope() as s:
        if sort == "price":
            order = (asc(m.Listing.price), desc(m.Favorite.created_at))
        elif sort == "surface":
            order = (desc(m.Listing.surface_m2), desc(m.Favorite.created_at))
        else:
            order = (desc(m.Favorite.created_at),)
        rows = s.execute(
            select(m.Listing)
            .join(m.Favorite, m.Favorite.listing_id == m.Listing.id)
            .where(m.Favorite.user_id == user_id)
            .order_by(*order)
            .limit(limit)
        ).scalars()
        return list(rows)


def save_source_run(
    *,
    cycle_id: str,
    source: str,
    status: str,
    started_at: datetime,
    finished_at: datetime | None = None,
    duration_seconds: float | None = None,
    locations_count: int = 0,
    raw_count: int = 0,
    normalized_count: int = 0,
    dropped_count: int = 0,
    unique_count: int = 0,
    new_count: int = 0,
    timeout_count: int = 0,
    error_count: int = 0,
    disabled_until: datetime | None = None,
    last_error: str | None = None,
) -> m.SourceRun:
    with session_scope() as s:
        row = m.SourceRun(
            cycle_id=cycle_id,
            source=source,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration_seconds,
            locations_count=locations_count,
            raw_count=raw_count,
            normalized_count=normalized_count,
            dropped_count=dropped_count,
            unique_count=unique_count,
            new_count=new_count,
            timeout_count=timeout_count,
            error_count=error_count,
            disabled_until=disabled_until,
            last_error=last_error,
        )
        s.add(row)
        s.flush()
        return row


def get_latest_source_runs(*, limit: int = 50) -> list[m.SourceRun]:
    with session_scope() as s:
        rows = s.execute(
            select(m.SourceRun)
            .order_by(desc(m.SourceRun.started_at), desc(m.SourceRun.id))
            .limit(limit)
        ).scalars()
        return list(rows)


def prune_source_runs(*, keep_days: int) -> int:
    cutoff = datetime.utcnow() - timedelta(days=keep_days)
    with session_scope() as s:
        res = s.execute(delete(m.SourceRun).where(m.SourceRun.started_at < cutoff))
        return int(res.rowcount or 0)
