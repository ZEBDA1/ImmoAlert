"""Per-publication prices and private, per-account property tracking."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from sqlalchemy import select
from database import models as m, repo

STAGES={"to_contact":"À contacter","contacted":"Contacté","visit":"Visite prévue","rejected":"Écarté"}


class TrackingConflict(ValueError):
    pass


def iso(value):
    return value.isoformat()+"Z" if value else None


def record_observation(session, listing, source, external_id, price, seen, status):
    """Run in the listing transaction, before replacing its old price."""
    if listing.removed_at is not None:return
    publication=session.scalar(select(m.ListingPublication).where(m.ListingPublication.source==source,m.ListingPublication.external_id==external_id))
    if publication is None:
        repo._record_publication(session,listing,source,external_id,listing.url,seen)
        publication=session.scalar(select(m.ListingPublication).where(m.ListingPublication.source==source,m.ListingPublication.external_id==external_id))
    if seen and publication.last_seen_at and seen<publication.last_seen_at:
        return
    if status=="active" and seen and listing.removed_at is None:
        publication.availability_status="available"
        publication.availability_checked_at=seen
        publication.availability_detail="Annonce observée sur le portail."
        publication.absence_count=0;publication.absence_first_at=None
    if status!='active' or not seen:return
    if type(price) not in (int,float) or not math.isfinite(price) or price<=0:return
    price=int(price)
    previous=publication.price
    # Existing installations can bootstrap only the canonical publication's
    # known price. Another portal's price is never a historical baseline.
    if previous is None and listing.source==source and listing.external_id==external_id and listing.price and listing.price!=price and listing.last_seen_at and listing.inventory_status=='active':
        previous=listing.price
        session.add(m.ListingPriceHistory(listing_id=listing.id,publication_id=publication.id,price=previous,observed_at=listing.last_seen_at or listing.first_seen_at))
    if previous!=price:
        session.add(m.ListingPriceHistory(listing_id=listing.id,publication_id=publication.id,price=price,previous_price=previous,observed_at=seen or datetime.utcnow()))
        publication.price=price


def price_history(identifier, *, before=None, limit=50):
    row=repo.get_listing_by_id(identifier)
    if not row or row.removed_at is not None:raise ValueError("Annonce introuvable.")
    with repo.session_scope() as session:
        query=select(m.ListingPriceHistory,m.ListingPublication).join(m.ListingPublication,m.ListingPriceHistory.publication_id==m.ListingPublication.id).where(m.ListingPriceHistory.listing_id==row.id)
        if before:query=query.where(m.ListingPriceHistory.id<before)
        rows=session.execute(query.order_by(m.ListingPriceHistory.id.desc()).limit(limit+1)).all()
        items=[{"id":history.id,"source":pub.source,"publication_id":pub.id,"price":history.price,"previous_price":history.previous_price,"observed_at":iso(history.observed_at)} for history,pub in rows[:limit]]
    return {"items":items,"next_cursor":items[-1]["id"] if len(rows)>limit else None}


PERSONAL_FIELDS=("commute_minutes","commute_destination","commute_mode","parking","accessibility")


def serialize_note(row):
    return {"listing_id":row.listing_id,"note":row.note,"stage":row.stage,"visit_at":iso(row.visit_at),"reminder_at":iso(row.reminder_at),"updated_at":iso(row.updated_at), **{key:getattr(row,key) for key in PERSONAL_FIELDS}} if row else None


def _favorite(session,user_id,identifier):
    user=session.get(m.User,user_id)
    account=session.scalar(select(m.WebAccount).where(m.WebAccount.user_id==user_id))
    if not user or (account and not account.enabled):raise PermissionError("Compte désactivé.")
    row=session.get(m.Listing,identifier)
    if row and row.duplicate_of_id:row=session.get(m.Listing,row.duplicate_of_id)
    if not row or row.removed_at is not None:raise ValueError("Annonce introuvable.")
    if not session.scalar(select(m.Favorite.id).where(m.Favorite.user_id==user_id,m.Favorite.listing_id==row.id)):
        raise PermissionError("Ajoutez ce bien à vos favoris pour conserver un suivi privé.")
    return row


def note_for(user_id,identifier):
    with repo.session_scope() as session:
        row=_favorite(session,user_id,identifier)
        return serialize_note(session.get(m.MemberListingNote,(user_id,row.id)))


def _date(value,name):
    if value in (None,""):return None
    if not isinstance(value,str):raise ValueError(f"Date invalide : {name}.")
    try:
        result=datetime.fromisoformat(value.replace("Z","+00:00"))
        if result.tzinfo is None:raise ValueError()
        return result.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError,TypeError):raise ValueError(f"Date invalide : {name}. Un fuseau horaire est requis.") from None


def save_note(user_id,identifier,payload,*,now=None):
    now=now or datetime.utcnow()
    note=payload.get("note","");stage=payload.get("stage","to_contact")
    if not isinstance(note,str) or len(note)>4000:raise ValueError("La note doit contenir au maximum 4 000 caractères.")
    if stage not in STAGES:raise ValueError("Étape de suivi inconnue.")
    visit=_date(payload.get("visit_at"),"visite");reminder=_date(payload.get("reminder_at"),"rappel")
    if visit and visit>now+timedelta(days=730):raise ValueError("La visite doit avoir lieu dans les deux prochaines années.")
    if stage=="visit" and (not visit or visit<=now):raise ValueError("Indiquez une date de visite à venir.")
    if reminder and (not visit or reminder>visit or reminder<=now or stage!="visit"):
        raise ValueError("Le rappel doit être à venir et précéder la visite prévue.")
    personal={key:payload[key] for key in PERSONAL_FIELDS if key in payload}
    minutes=personal.get('commute_minutes')
    if minutes is not None and (type(minutes) is not int or not 0<=minutes<=600):
        raise ValueError('Le trajet doit durer de 0 à 600 minutes.')
    destination=personal.get('commute_destination')
    if destination is not None:
        if not isinstance(destination,str) or len(destination)>160:raise ValueError('La destination doit contenir au maximum 160 caractères.')
        personal['commute_destination']=destination.strip() or None
    if personal.get('commute_mode') is not None and (not isinstance(personal['commute_mode'],str) or personal['commute_mode'] not in {'walk','bike','car','transit'}):raise ValueError('Mode de trajet inconnu.')
    for key in ('parking','accessibility'):
        if personal.get(key) is not None and (not isinstance(personal[key],str) or personal[key] not in {'yes','no','unknown'}):raise ValueError('Critère personnel invalide.')
        if personal.get(key)=='unknown':personal[key]=None
    with repo.session_scope() as session:
        listing=_favorite(session,user_id,identifier)
        row=session.get(m.MemberListingNote,(user_id,listing.id))
        if 'updated_at' in payload and payload['updated_at']!=iso(row.updated_at if row else None):
            raise TrackingConflict('Ce suivi a été modifié sur un autre appareil. Rechargez-le avant d’enregistrer votre brouillon.')
        if row is None:row=m.MemberListingNote(user_id=user_id,listing_id=listing.id);session.add(row)
        if row.reminder_at!=reminder:
            row.reminder_sent_at=None
            for alert in session.scalars(select(m.MemberNotification).where(m.MemberNotification.user_id==user_id,m.MemberNotification.listing_id==listing.id,m.MemberNotification.kind=="visit_reminder",m.MemberNotification.telegram_status=="pending")):
                alert.telegram_status="canceled"
        for key,value in personal.items():setattr(row,key,value)
        row.note=note.strip();row.stage=stage;row.visit_at=visit;row.reminder_at=reminder;row.updated_at=now
        from services.deduplication import normalized
        row.search_text=normalized(row.note+" "+(row.commute_destination or ""))
        session.flush()
        return serialize_note(row)


def favorite_notes(user_id):
    with repo.session_scope() as session:
        return [serialize_note(row) for row in session.scalars(select(m.MemberListingNote).join(m.Favorite,(m.Favorite.user_id==m.MemberListingNote.user_id)&(m.Favorite.listing_id==m.MemberListingNote.listing_id)).where(m.MemberListingNote.user_id==user_id))]


def publication_freshness(identifier):
    with repo.session_scope() as session:
        rows=list(session.scalars(select(m.ListingPublication).where(m.ListingPublication.listing_id==identifier).order_by(m.ListingPublication.id)))
        items=[{"source":row.source,"status":row.availability_status or "unchecked","checked_at":iso(row.availability_checked_at),"detail":row.availability_detail or "Pas encore contrôlée.","confirmations":row.absence_count or 0} for row in rows]
    statuses={item["status"] for item in items}
    status="available" if "available" in statuses else "unavailable" if statuses=={"unavailable"} else "check_failed" if "check_failed" in statuses else "unconfirmed" if "unconfirmed" in statuses else "unchecked"
    return {"status":status,"publications":items}


def record_availability(publication_id,outcome,*,now=None):
    if outcome not in {"available","absent","check_failed"}:raise ValueError("État de contrôle invalide.")
    now=now or datetime.utcnow()
    with repo.session_scope() as session:
        pub=session.get(m.ListingPublication,publication_id)
        if not pub:return
        if outcome=="available":
            pub.availability_status="available";pub.absence_count=0;pub.absence_first_at=None
            pub.availability_detail="Annonce observée sur le portail."
        elif outcome=="absent":
            if pub.absence_first_at is None:
                pub.absence_first_at=now;pub.absence_count=1
            elif now-pub.absence_first_at>=timedelta(hours=6):
                pub.absence_count=max(2,pub.absence_count or 0)
            pub.availability_status="unavailable" if (pub.absence_count or 0)>=2 else "unconfirmed"
            pub.availability_detail="Indisponibilité confirmée à deux reprises espacées." if pub.availability_status=="unavailable" else "Annonce introuvable ; une confirmation est nécessaire."
        else:
            pub.availability_status="check_failed";pub.availability_detail="Contrôle impossible. La disponibilité reste à confirmer."
        pub.availability_checked_at=now


def merge_note_rows(target,source):
    """Merge two records of the same owner without losing personal criteria."""
    target.note="\n\n".join(dict.fromkeys(value for value in [target.note,source.note] if value))
    newer,older=(source,target) if source.updated_at>target.updated_at else (target,source)
    personal={key:getattr(newer,key) if getattr(newer,key) is not None else getattr(older,key) for key in PERSONAL_FIELDS}
    if newer is source:
        for key in ('stage','visit_at','reminder_at','reminder_sent_at','updated_at'):setattr(target,key,getattr(source,key))
    for key,value in personal.items():setattr(target,key,value)
    from services.deduplication import normalized
    target.search_text=normalized(target.note+" "+(target.commute_destination or ""))


def merge_member_profile(session,source_id,target_id):
    for note in list(session.scalars(select(m.MemberListingNote).where(m.MemberListingNote.user_id==source_id))):
        existing=session.get(m.MemberListingNote,(target_id,note.listing_id))
        if existing:merge_note_rows(existing,note);session.delete(note)
        else:note.user_id=target_id
        session.flush()
    source=session.get(m.NotificationPreference,source_id)
    if source:
        target=session.get(m.NotificationPreference,target_id)
        if target:
            for key in ('timezone','quiet_start','quiet_end','daily_summary','summary_time','price_alerts'):setattr(target,key,getattr(source,key))
            session.delete(source)
        else:source.user_id=target_id
    for notice in list(session.scalars(select(m.MemberNotification).where(m.MemberNotification.user_id==source_id))):
        existing=session.scalar(select(m.MemberNotification).where(m.MemberNotification.user_id==target_id,m.MemberNotification.event_key==notice.event_key))
        if existing:
            if notice.telegram_status=='sent':existing.telegram_status='sent'
            if notice.read_at and not existing.read_at:existing.read_at=notice.read_at
            session.delete(notice)
        else:notice.user_id=target_id
        session.flush()
