"""Durable price alerts, visit reminders and one daily Telegram summary."""
from __future__ import annotations

import asyncio
from datetime import datetime,timedelta
from html import escape
import json
import logging
from urllib.parse import urlsplit
from uuid import uuid4

from aiogram.exceptions import TelegramBadRequest,TelegramForbiddenError,TelegramRetryAfter
from sqlalchemy import select,func
from config import settings
from database import models as m,repo
from scraper.base import ScrapedListing
from services import alert_preferences as preferences
from services.matcher import match_and_score
from services.property_tracking import iso

log=logging.getLogger(__name__)
_dispatch_lock=asyncio.Lock()


def as_scraped(row):
    return ScrapedListing(source=row.source,external_id=row.external_id,url=row.url,title=row.title,price=row.price,surface_m2=row.surface_m2,location=row.location,rooms=row.rooms,description=row.description)


def _queue(session,user,listing,kind,key,payload,now,not_before=None):
    if session.scalar(select(m.MemberNotification.id).where(m.MemberNotification.user_id==user.id,m.MemberNotification.event_key==key)):return
    session.add(m.MemberNotification(id=str(uuid4()),user_id=user.id,listing_id=listing.id,kind=kind,event_key=key,payload=json.dumps(payload,ensure_ascii=False),created_at=now,not_before=not_before or now,telegram_status='pending' if user.active and user.telegram_id else 'canceled'))


def queue_events(now=None):
    now=now or datetime.utcnow()
    with repo.session_scope() as session:
        disabled=set(session.scalars(select(m.WebAccount.user_id).where(m.WebAccount.enabled==False)))
        users={user.id:user for user in session.scalars(select(m.User)) if user.id not in disabled}
        for user in users.values():repo._refresh_premium_status(user)
        prefs={row.user_id:row for row in session.scalars(select(m.NotificationPreference))}
        filters=list(session.scalars(select(m.Filter).where(m.Filter.active==True)))
        favorites={}
        for favorite in session.scalars(select(m.Favorite)):
            favorites.setdefault(favorite.listing_id,set()).add(favorite.user_id)
        changes=list(session.scalars(select(m.ListingPriceHistory).where(m.ListingPriceHistory.announced_at.is_(None)).order_by(m.ListingPriceHistory.id).limit(100)))
        for history in changes:
            history.announced_at=now
            if history.previous_price is None or history.price>=history.previous_price:continue
            listing=session.get(m.Listing,history.listing_id)
            if not listing or listing.removed_at is not None or listing.inventory_status=='invalid':continue
            recipients=set(favorites.get(listing.id,set()))
            scraped=as_scraped(listing)
            # Match the price on this publication rather than another portal's
            # canonical rent; the lower price can newly satisfy a search.
            scraped.price=history.price
            for search in filters:
                if search.user_id in users and match_and_score(search,scraped).matched:recipients.add(search.user_id)
            publication=session.get(m.ListingPublication,history.publication_id)
            for user_id in recipients:
                user=users.get(user_id)
                if not user or not user.active or user_id in prefs and not prefs[user_id].price_alerts:continue
                delay=0 if user.is_premium else settings.premium_free_delay_seconds
                _queue(session,user,listing,'price_drop',f'price:{history.id}',{'title':listing.title,'source':publication.source,'previous_price':history.previous_price,'price':history.price,'observed_at':iso(history.observed_at)},now,now+timedelta(seconds=delay))
        for note in session.scalars(select(m.MemberListingNote).where(m.MemberListingNote.reminder_at<=now,m.MemberListingNote.reminder_sent_at.is_(None))):
            listing=session.get(m.Listing,note.listing_id);user=users.get(note.user_id)
            if not listing or listing.removed_at is not None or not user or note.stage!='visit' or not note.visit_at or note.visit_at<=now:continue
            if note.user_id not in favorites.get(note.listing_id,set()):continue
            _queue(session,user,listing,'visit_reminder',f'visit:{listing.id}:{iso(note.reminder_at)}',{'title':listing.title,'visit_at':iso(note.visit_at),'reminder_at':iso(note.reminder_at)},now)
            note.reminder_sent_at=now


def inbox(user_id,*,before=None,limit=30):
    with repo.session_scope() as session:
        query=select(m.MemberNotification).where(m.MemberNotification.user_id==user_id)
        if before:
            cursor=session.get(m.MemberNotification,before)
            if not cursor or cursor.user_id!=user_id:raise ValueError('Notification introuvable.')
            query=query.where((m.MemberNotification.created_at<cursor.created_at)|((m.MemberNotification.created_at==cursor.created_at)&(m.MemberNotification.id<cursor.id)))
        rows=list(session.scalars(query.order_by(m.MemberNotification.created_at.desc(),m.MemberNotification.id.desc()).limit(limit+1)))
        unread=session.scalar(select(func.count()).select_from(m.MemberNotification).where(m.MemberNotification.user_id==user_id,m.MemberNotification.read_at.is_(None)))
        items=[{'id':row.id,'listing_id':row.listing_id,'kind':row.kind,'payload':json.loads(row.payload),'created_at':iso(row.created_at),'read_at':iso(row.read_at)} for row in rows[:limit]]
    return {'items':items,'unread':unread,'next_cursor':items[-1]['id'] if len(rows)>limit else None}


def mark_read(user_id,identifier):
    with repo.session_scope() as session:
        row=session.get(m.MemberNotification,identifier)
        if not row or row.user_id!=user_id:raise ValueError('Notification introuvable.')
        row.read_at=datetime.utcnow()
    return {'id':identifier,'read':True}


def _link(listing):
    site=(settings.web_site_url or '').rstrip('/')
    candidate=f'{site}/?listing={listing.id}' if site else listing.url
    return candidate if urlsplit(candidate).scheme in {'http','https'} else ''


def _line(listing,notice=None):
    title=escape((listing.title or 'Bien immobilier')[:100])
    link=_link(listing)
    title=f'<a href="{escape(link,quote=True)}">{title}</a>' if link else title
    if notice and notice.kind=='price_drop':
        data=json.loads(notice.payload)
        from services.notification import _source_label
        return f'{title} ({escape(_source_label(data.get("source",listing.source)))}) : {data["previous_price"]} → {data["price"]} €/mois'
    return f'{title} : {listing.price} €/mois' if listing.price is not None else title


def _pending_for_summary(user,now):
    result=[]
    searches=repo.get_user_filters(user.id)
    with repo.session_scope() as session:
        pending=list(session.scalars(select(m.PendingAlert).where(m.PendingAlert.user_id==user.id,m.PendingAlert.status=='pending',m.PendingAlert.not_before<=now).order_by(m.PendingAlert.id)))
    for item in pending:
        listing=repo.get_listing_by_id(item.listing_id)
        match=None
        if listing and repo.is_current_listing(listing) and not repo.has_sent_alert(user.id,listing.id):
            for search in searches:
                if not search.active:continue
                score=match_and_score(search,as_scraped(listing))
                if score.matched:match=(search,score);break
        if not match:repo.set_pending_alert_status(item.id,'canceled')
        else:result.append(('listing',item,listing,match))
    return result


def _due_notices(user_id,now,limit=100):
    with repo.session_scope() as session:
        query=select(m.MemberNotification).where(m.MemberNotification.user_id==user_id,m.MemberNotification.telegram_status=='pending',m.MemberNotification.not_before<=now).order_by(m.MemberNotification.created_at,m.MemberNotification.id)
        return list(session.scalars(query.limit(limit) if limit else query))


def _valid_notice(notice,prefs,now):
    listing=repo.get_listing_by_id(notice.listing_id)
    valid=listing is not None and listing.removed_at is None and listing.inventory_status!='invalid'
    if notice.kind=='price_drop':valid=valid and prefs['price_alerts']
    if notice.kind=='visit_reminder':
        with repo.session_scope() as session:
            note=session.get(m.MemberListingNote,(notice.user_id,notice.listing_id))
            favorite=session.scalar(select(m.Favorite.id).where(m.Favorite.user_id==notice.user_id,m.Favorite.listing_id==notice.listing_id))
            data=json.loads(notice.payload)
            valid=valid and bool(favorite and note and note.stage=='visit' and note.visit_at and note.visit_at>now and iso(note.reminder_at)==data.get('reminder_at'))
    if not valid:
        with repo.session_scope() as session:session.get(m.MemberNotification,notice.id).telegram_status='canceled'
        return None
    return listing


def _sent(entry):
    kind,item,listing,match=entry
    if kind=='listing':
        search,score=match
        repo.mark_alert_sent(item.user_id,listing.id,filter_id=search.id,filter_name=search.name,score_value=score.score_value,score_label=score.score_label,match_reasons='; '.join(score.reasons or []))
    else:
        with repo.session_scope() as session:session.get(m.MemberNotification,item.id).telegram_status='sent'


def _failed(user,entries,exc,now):
    permanent=isinstance(exc,TelegramBadRequest)
    wait=exc.retry_after if isinstance(exc,TelegramRetryAfter) else 60
    with repo.session_scope() as session:
        for kind,item,_,_ in entries:
            row=session.get(m.PendingAlert if kind=='listing' else m.MemberNotification,item.id)
            if row:
                if permanent:
                    if kind=='listing':row.status='canceled'
                    else:row.telegram_status='canceled'
                else:row.not_before=now+timedelta(seconds=wait)
        pref=session.get(m.NotificationPreference,user.id)
        if not pref:pref=m.NotificationPreference(user_id=user.id);session.add(pref)
        pref.retry_after=None if permanent else now+timedelta(seconds=wait)
        if isinstance(exc,TelegramForbiddenError):
            session.get(m.User,user.id).active=False
            for row in session.scalars(select(m.MemberNotification).where(m.MemberNotification.user_id==user.id,m.MemberNotification.telegram_status=='pending')):row.telegram_status='canceled'
    if isinstance(exc,TelegramForbiddenError):repo.cancel_pending_alerts_for_user(user.id)
    log.warning('Member notifications deferred for user %s (%s)',user.id,type(exc).__name__)


async def dispatch_member_notifications(bot,*,now=None):
    now=now or datetime.utcnow()
    async with _dispatch_lock:
        await asyncio.to_thread(queue_events,now)
        with repo.session_scope() as session:
            disabled=set(session.scalars(select(m.WebAccount.user_id).where(m.WebAccount.enabled==False)))
            users=[user for user in session.scalars(select(m.User).where(m.User.active==True,m.User.telegram_id.is_not(None))) if user.id not in disabled]
        for user in users:
            prefs=await asyncio.to_thread(preferences.get_preferences,user.id)
            if prefs.get('retry_after') and prefs['retry_after']>now:continue
            notices=await asyncio.to_thread(_due_notices,user.id,now,None if prefs['daily_summary'] else 100)
            if preferences.is_quiet(prefs,now):continue
            failed=False
            for notice in notices:
                if notice.kind=='price_drop' and prefs['daily_summary']:continue
                listing=await asyncio.to_thread(_valid_notice,notice,prefs,now)
                if not listing:continue
                if notice.kind=='visit_reminder':
                    date=preferences.local_time(prefs,datetime.fromisoformat(json.loads(notice.payload)['visit_at'].replace('Z','+00:00')).replace(tzinfo=None)).strftime('%d/%m à %H:%M')
                    message=f'<b>Rappel de visite</b>\n{_line(listing)}\nVisite le {date} ({escape(prefs["timezone"])}).'
                else:message='<b>Baisse de loyer</b>\n'+_line(listing,notice)
                entry=('notice',notice,listing,None)
                try:
                    await bot.send_message(user.telegram_id,message,disable_web_page_preview=True)
                    await asyncio.to_thread(_sent,entry)
                except Exception as exc:
                    await asyncio.to_thread(_failed,user,[entry],exc,now)
                    failed=True
                    break
            if failed:continue
            if not preferences.summary_due(prefs,now):continue
            entries=await asyncio.to_thread(_pending_for_summary,user,now)
            for notice in notices:
                if notice.kind!='price_drop':continue
                listing=await asyncio.to_thread(_valid_notice,notice,prefs,now)
                if listing:entries.append(('notice',notice,listing,None))
            title='<b>Votre résumé immobilier du '+preferences.local_time(prefs,now).strftime('%d/%m/%Y')+'</b>\n'
            chunks=[];chunk=[];size=len(title)
            for entry in entries:
                line=_line(entry[2],entry[1] if entry[0]=='notice' else None)
                if chunk and size+len(line)>3500:chunks.append(chunk);chunk=[];size=len(title)
                chunk.append((entry,line));size+=len(line)+2
            if chunk:chunks.append(chunk)
            delivered=True
            for chunk in chunks:
                try:
                    await bot.send_message(user.telegram_id,title+'\n\n'.join(line for _,line in chunk),disable_web_page_preview=True)
                    for entry,_ in chunk:await asyncio.to_thread(_sent,entry)
                except Exception as exc:
                    await asyncio.to_thread(_failed,user,[entry for entry,_ in chunk],exc,now)
                    delivered=False;break
            if delivered:
                with repo.session_scope() as session:
                    row=session.get(m.NotificationPreference,user.id)
                    if row:row.last_summary_at=now;row.retry_after=None


async def run_member_monitor(bot):
    from services import performance
    from services.scheduler import _dispatch_pending
    iteration=0
    while True:
        try:
            await _dispatch_pending(bot)
            await dispatch_member_notifications(bot)
            await asyncio.to_thread(performance.flush)
            if iteration%120==0:await asyncio.to_thread(performance.prune)
        except asyncio.CancelledError:raise
        except Exception:
            log.exception('Member notification or freshness monitor failed')
        iteration+=1
        await asyncio.sleep(30)
