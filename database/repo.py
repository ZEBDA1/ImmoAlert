from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional
import threading

from sqlalchemy import select, and_, func, delete, update, desc, asc

from config import settings
from .session import SessionLocal
from . import models as m
from utils.images import decode_images, encode_images, merge_images

_listing_lock=threading.RLock()


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


def get_runtime_setting(key: str) -> str | None:
    with session_scope() as s:
        row = s.get(m.RuntimeSetting, key)
        return row.value if row else None


def set_runtime_setting(key: str, value: str) -> None:
    with session_scope() as s:
        s.merge(m.RuntimeSetting(key=key, value=value))


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
    last_seen_at: Optional[datetime] = None,
    inventory_status: Optional[str] = None,
) -> ListingSaveResult:
    from services.deduplication import same_property,normalized
    from services.property_tracking import record_observation
    incoming = m.Listing(source=source, external_id=external_id, url=url, title=title,
        price=price, surface_m2=surface_m2, rooms=rooms, location=location,
        description=description, images=images)
    with _listing_lock, session_scope() as s:
        publication = s.scalar(select(m.ListingPublication).where(
            m.ListingPublication.source == source, m.ListingPublication.external_id == external_id))
        if publication:
            existing = s.get(m.Listing, publication.listing_id)
            if last_seen_at and publication.last_seen_at and last_seen_at<publication.last_seen_at:
                return ListingSaveResult(existing,False,"source_external_id",existing.id)
            record_observation(s,existing,source,external_id,price,last_seen_at,inventory_status)
            _refresh_listing(existing, incoming, last_seen_at, inventory_status)
            publication.url = url
            if last_seen_at:
                publication.last_seen_at = last_seen_at
            s.flush()
            return ListingSaveResult(existing, False, "source_external_id", existing.id)
        existing = s.execute(
            select(m.Listing).where(m.Listing.source == source, m.Listing.external_id == external_id)
        ).scalar_one_or_none()
        if existing:
            if existing.duplicate_of_id:
                existing = s.get(m.Listing, existing.duplicate_of_id)
            if existing.source==source and existing.external_id==external_id and last_seen_at and existing.last_seen_at and last_seen_at<existing.last_seen_at:
                return ListingSaveResult(existing,False,"source_external_id",existing.id)
            record_observation(s,existing,source,external_id,price,last_seen_at,inventory_status)
            _record_publication(s, existing, source, external_id, url, last_seen_at)
            _refresh_listing(existing, incoming, last_seen_at, inventory_status)
            if inventory_status == "active" and last_seen_at is not None and existing.removed_at is None:
                # An observed rental may change its price, photos or details.
                # Refresh the same row, preserving favorites and sent-alert IDs.
                for key, value in {
                    "url":url,"title":title,"price":price,"surface_m2":surface_m2,"price_per_m2":price_per_m2,
                    "location":location,"rooms":rooms,
                    "published_at":published_at,"canonical_signature":canonical_signature,"quality_flags":quality_flags,
                    "normalized_city":normalized_city,"normalized_postal_code":normalized_postal_code,
                    "charges_included":charges_included,"last_seen_at":last_seen_at,"inventory_status":"active",
                }.items():
                    if value is not None:
                        setattr(existing,key,value)
                s.add(existing)
                s.flush()
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
                    m.Listing.duplicate_of_id.is_(None),
                    (m.Listing.first_seen_at >= since) | (m.Listing.removed_at.is_not(None)),
                )
                .order_by(m.Listing.first_seen_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if duplicate:
                record_observation(s,duplicate,source,external_id,price,last_seen_at,inventory_status)
                _record_publication(s, duplicate, source, external_id, url, last_seen_at)
                _refresh_listing(duplicate, incoming, last_seen_at, inventory_status)
                s.flush()
                return ListingSaveResult(
                    listing=duplicate,
                    is_new=False,
                    duplicate_reason="canonical_signature",
                    duplicate_listing_id=duplicate.id,
                )

        if price and surface_m2 and location:
            since = datetime.utcnow() - timedelta(days=30)
            candidates = s.scalars(select(m.Listing).where(
                m.Listing.duplicate_of_id.is_(None), m.Listing.source != source,
                m.Listing.price.between(price-max(30, price*.06), price+max(30,price*.06)),
                m.Listing.surface_m2.between(surface_m2-max(.7,surface_m2*.025),surface_m2+max(.7,surface_m2*.025)),
                (m.Listing.last_seen_at >= since) | (m.Listing.first_seen_at >= since) | (m.Listing.removed_at.is_not(None)),
            ).order_by(m.Listing.id))
            for duplicate in candidates:
                if same_property(incoming, duplicate):
                    record_observation(s,duplicate,source,external_id,price,last_seen_at,inventory_status)
                    _record_publication(s, duplicate, source, external_id, url, last_seen_at)
                    _refresh_listing(duplicate, incoming, last_seen_at, inventory_status)
                    s.flush()
                    return ListingSaveResult(duplicate, False, "cross_platform", duplicate.id)

        listing = m.Listing(
            source=source,
            external_id=external_id,
            url=url,
            title=title,
            search_text=normalized((title or '')+' '+(location or '')),
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
            last_seen_at=last_seen_at,
            inventory_status=inventory_status,
        )
        s.add(listing)
        s.flush()
        _record_publication(s, listing, source, external_id, url, last_seen_at)
        record_observation(s,listing,source,external_id,price,last_seen_at,inventory_status)
        return ListingSaveResult(listing=listing, is_new=True)


def _record_publication(session, listing, source, external_id, url, seen):
    # Include the original source when merging an older row without publications.
    for src, ext, link, moment in [(listing.source,listing.external_id,listing.url,listing.last_seen_at), (source,external_id,url,seen)]:
        row = session.scalar(select(m.ListingPublication).where(m.ListingPublication.source==src,m.ListingPublication.external_id==ext))
        if row is None:
            row = m.ListingPublication(listing_id=listing.id,source=src,external_id=ext,url=link,last_seen_at=moment)
            session.add(row)
            session.flush()
        else:
            row.listing_id, row.url = listing.id, link
            if moment:
                row.last_seen_at = moment


def _refresh_listing(existing, incoming, seen, status):
    if existing.removed_at is not None:
        return
    if existing.source==incoming.source and existing.external_id==incoming.external_id and status=="active" and seen:
        for key in ("url","title","price","surface_m2","rooms","location"):
            if getattr(incoming,key) is not None:
                setattr(existing,key,getattr(incoming,key))
        if existing.price and existing.surface_m2:
            existing.price_per_m2=existing.price/existing.surface_m2
    if len(incoming.description or "") > len(existing.description or ""):
        existing.description = incoming.description
    existing.images = encode_images(merge_images(existing.images,incoming.images))
    if seen and (not existing.last_seen_at or seen > existing.last_seen_at):
        existing.last_seen_at = seen
    if status == "active":
        existing.inventory_status = status
    for key in ("title","price","surface_m2","rooms","location"):
        if getattr(existing,key) is None and getattr(incoming,key) is not None:
            setattr(existing,key,getattr(incoming,key))
    if incoming.details:
        import json
        features=json.loads(existing.details or "{}")
        features.update(json.loads(incoming.details))
        existing.details=json.dumps(features,ensure_ascii=False)
    if incoming.latitude is not None and (existing.latitude is None or incoming.geo_precision=="portal"):
        existing.latitude,existing.longitude,existing.geo_precision=incoming.latitude,incoming.longitude,incoming.geo_precision
    if incoming.details_checked_at and (not existing.details_checked_at or incoming.details_checked_at>existing.details_checked_at):
        existing.details_checked_at,existing.details_status=incoming.details_checked_at,incoming.details_status
    from services.deduplication import normalized
    existing.search_text=normalized((existing.title or '')+' '+(existing.location or ''))


def possible_duplicate_listings(listing, *, limit=2) -> list[m.Listing]:
    from services.deduplication import same_location
    if not listing.price or not listing.surface_m2:
        return []
    with session_scope() as s:
        rows=s.scalars(select(m.Listing).where(current_inventory_condition(),
            m.Listing.id!=listing.id,m.Listing.source!=listing.source,
            m.Listing.price.between(listing.price-max(30,listing.price*.06),listing.price+max(30,listing.price*.06)),
            m.Listing.surface_m2.between(listing.surface_m2-max(.7,listing.surface_m2*.025),listing.surface_m2+max(.7,listing.surface_m2*.025)),
        ).order_by(m.Listing.last_seen_at.desc()))
        result=[]
        for row in rows:
            if same_location(listing,row) and (not listing.rooms or not row.rooms or listing.rooms==row.rooms):
                result.append(row)
                if len(result)>=limit:
                    break
        return result


def listing_publications(listing_id: int) -> list[dict]:
    with session_scope() as s:
        row = s.get(m.Listing, listing_id)
        if not row:
            return []
        rows = list(s.scalars(select(m.ListingPublication).where(m.ListingPublication.listing_id==listing_id).order_by(m.ListingPublication.id)))
        return [{"source":p.source,"url":p.url,"price":p.price} for p in rows] or [{"source":row.source,"url":row.url,"price":row.price}]


def reconcile_duplicate_listings(*, identifier=None) -> int:
    """Merge confident existing matches, preserving records and user relations."""
    from services.deduplication import same_property
    from scraper.quality import extract_postal_code
    merged = 0
    with _listing_lock, session_scope() as s:
        query=select(m.Listing).where(current_inventory_condition())
        if identifier is not None:
            seed=s.get(m.Listing,identifier)
            if not seed:return 0
            if seed.duplicate_of_id:seed=s.get(m.Listing,seed.duplicate_of_id)
            if not seed or not seed.price or not seed.surface_m2:return 0
            query=query.where((m.Listing.id==seed.id)|(m.Listing.source!=seed.source),m.Listing.price.between(seed.price-max(35,seed.price*.06),seed.price+max(35,seed.price*.06)),m.Listing.surface_m2.between(seed.surface_m2-max(1,seed.surface_m2*.03),seed.surface_m2+max(1,seed.surface_m2*.03)))
            postal=seed.normalized_postal_code or extract_postal_code(seed.location)
            if postal:query=query.where((m.Listing.normalized_postal_code==postal)|(m.Listing.normalized_postal_code.is_(None))|(m.Listing.normalized_postal_code==""))
            from services.deduplication import same_location
            rows=[row for row in s.scalars(query.order_by(m.Listing.id)) if row.id==seed.id or same_location(seed,row)]
        else:
            rows=list(s.scalars(query.order_by(m.Listing.id)))
        buckets = {}
        for row in rows:
            key = (row.normalized_postal_code or extract_postal_code(row.location) or "", round((row.surface_m2 or 0)/5))
            # Boundary-safe shortlist; same_property checks exact geographic evidence.
            pool = [v for offset in (-1,0,1) for v in buckets.get((key[0],key[1]+offset),[])]
            target = next((v for v in pool if same_property(row,v)),None)
            if target is None:
                buckets.setdefault(key,[]).append(row)
                continue
            _record_publication(s,target,row.source,row.external_id,row.url,row.last_seen_at)
            for pub in s.scalars(select(m.ListingPublication).where(m.ListingPublication.listing_id==row.id)):
                pub.listing_id=target.id
            for history in s.scalars(select(m.ListingPriceHistory).where(m.ListingPriceHistory.listing_id==row.id)):
                history.listing_id=target.id
            for notice in s.scalars(select(m.MemberNotification).where(m.MemberNotification.listing_id==row.id)):
                notice.listing_id=target.id
            for note in list(s.scalars(select(m.MemberListingNote).where(m.MemberListingNote.listing_id==row.id))):
                other=s.get(m.MemberListingNote,(note.user_id,target.id))
                if other:
                    from services.property_tracking import merge_note_rows
                    merge_note_rows(other,note)
                    s.delete(note)
                else:note.listing_id=target.id
            _refresh_listing(target,row,row.last_seen_at,row.inventory_status)
            target.first_seen_at = min(target.first_seen_at,row.first_seen_at)
            for model in (m.Favorite,m.SentAlert,m.PendingAlert,m.ListingRead,m.ListingReport):
                for rel in s.scalars(select(model).where(model.listing_id==row.id)):
                    other=s.scalar(select(model).where(model.listing_id==target.id,model.user_id==rel.user_id))
                    if other:
                        if model is m.ListingReport:
                            if rel.status=="open":
                                other.status="open";other.reviewed_at=None;other.reviewed_by=None
                            other.note="\n".join(v for v in [other.note,rel.note] if v)[:500]
                            other.created_at=min(other.created_at,rel.created_at)
                        if model is m.ListingRead:
                            other.read_at=min(other.read_at,rel.read_at)
                        if model is m.PendingAlert and rel.status=="sent":
                            other.status="sent"
                        elif model is m.PendingAlert and rel.status=="pending" and other.status!="sent":
                            other.status="pending"
                            other.not_before=min(other.not_before,rel.not_before)
                        s.delete(rel)
                    else:
                        rel.listing_id=target.id
                    s.flush()
            row.duplicate_of_id=target.id
            s.execute(update(m.Listing).where(m.Listing.duplicate_of_id==row.id,m.Listing.id!=row.id).values(duplicate_of_id=target.id))
            s.flush()
            merged+=1
    return merged


def reconcile_listing_duplicates(identifier: int) -> int:
    """Compare only the enriched property and its geographic/price shortlist."""
    return reconcile_duplicate_listings(identifier=identifier)


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
        listing=s.get(m.Listing,listing_id)
        if listing is not None and listing.removed_at is not None:
            return False
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
        row = s.get(m.Listing,listing_id)
        visited=set()
        while row and row.duplicate_of_id:
            if row.id in visited:return None
            visited.add(row.id);row=s.get(m.Listing,row.duplicate_of_id)
        return row


def current_inventory_condition(now: datetime | None = None):
    """Only coherent rentals actually observed on a portal in the last month."""
    return (m.Listing.duplicate_of_id.is_(None)) & (m.Listing.removed_at.is_(None)) & (m.Listing.inventory_status == "active") & (m.Listing.last_seen_at >= (now or datetime.utcnow()) - timedelta(days=30))


def is_current_listing(listing: m.Listing) -> bool:
    return listing.removed_at is None and listing.inventory_status == "active" and listing.last_seen_at is not None and listing.last_seen_at >= datetime.utcnow() - timedelta(days=30)


def get_recent_listings(*, hours: int = 48, limit: int = 300) -> list[m.Listing]:
    since = datetime.utcnow() - timedelta(hours=hours)
    with session_scope() as s:
        rows = s.execute(
            select(m.Listing)
            .where(m.Listing.first_seen_at >= since, current_inventory_condition())
            .order_by(m.Listing.first_seen_at.desc())
            .limit(limit)
        ).scalars()
        return list(rows)


# Favorites
def add_favorite(user_id: int, listing_id: int) -> bool:
    listing=get_listing_by_id(listing_id)
    if not listing or listing.removed_at is not None:
        return False
    listing_id=listing.id
    with session_scope() as s:
        existing = s.execute(
            select(m.Favorite).where(m.Favorite.user_id == user_id, m.Favorite.listing_id == listing_id)
        ).scalar_one_or_none()
        if existing:
            return False
        s.add(m.Favorite(user_id=user_id, listing_id=listing_id))
        return True


def remove_favorite(user_id: int, listing_id: int) -> bool:
    listing=get_listing_by_id(listing_id)
    listing_id=listing.id if listing else listing_id
    with session_scope() as s:
        fav = s.execute(
            select(m.Favorite).where(m.Favorite.user_id == user_id, m.Favorite.listing_id == listing_id)
        ).scalar_one_or_none()
        if not fav:
            return False
        s.delete(fav)
        return True


def is_favorite(user_id: int, listing_id: int) -> bool:
    listing=get_listing_by_id(listing_id)
    listing_id=listing.id if listing else listing_id
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
            .where(m.Favorite.user_id == user_id,m.Listing.removed_at.is_(None))
            .order_by(*order)
            .limit(limit)
        ).scalars()
        return list(rows)


def mark_listing_read(user_id: int, listing_id: int) -> int:
    """Idempotent per-profile read state, shared by aliases and devices."""
    with _listing_lock, session_scope() as session:
        row=session.get(m.Listing,listing_id)
        if not row or row.inventory_status=="invalid" or row.removed_at is not None:
            raise ValueError("Annonce introuvable.")
        visited=set()
        while row.duplicate_of_id:
            if row.id in visited:
                raise ValueError("Annonce introuvable.")
            visited.add(row.id)
            row=session.get(m.Listing,row.duplicate_of_id)
            if row is None:
                raise ValueError("Annonce introuvable.")
        if session.get(m.ListingRead,(user_id,row.id)) is None:
            session.add(m.ListingRead(user_id=user_id,listing_id=row.id))
        return row.id


def read_listing_ids(user_id: int) -> list[int]:
    with session_scope() as session:
        return list(session.scalars(select(m.ListingRead.listing_id).where(m.ListingRead.user_id==user_id)))


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
    diagnostics_json: str | None = None,
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
            diagnostics_json=diagnostics_json,
        )
        s.add(row)
        s.flush()
        return row


def get_latest_source_runs(*, limit: int = 50) -> list[m.SourceRun]:
    with session_scope() as s:
        rows = s.execute(
            select(m.SourceRun)
            .order_by(desc(m.SourceRun.finished_at), desc(m.SourceRun.id))
            .limit(limit)
        ).scalars()
        return list(rows)


def prune_source_runs(*, keep_days: int) -> int:
    cutoff = datetime.utcnow() - timedelta(days=keep_days)
    with session_scope() as s:
        res = s.execute(delete(m.SourceRun).where(m.SourceRun.started_at < cutoff))
        return int(res.rowcount or 0)
