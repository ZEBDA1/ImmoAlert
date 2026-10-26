"""Notification times are interpreted in each member's IANA time zone."""
from __future__ import annotations

from datetime import datetime,timedelta,timezone
import re
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from database import models as m,repo

DEFAULTS={"timezone":"Europe/Paris","quiet_start":None,"quiet_end":None,"daily_summary":False,"summary_time":"08:00","price_alerts":True}


def get_preferences(user_id):
    with repo.session_scope() as session:
        row=session.get(m.NotificationPreference,user_id)
        return {**DEFAULTS,**({key:getattr(row,key) for key in DEFAULTS} if row else {}),"last_summary_at":row.last_summary_at if row else None,"retry_after":row.retry_after if row else None}


def public_preferences(user_id):
    prefs=get_preferences(user_id)
    return {key:prefs[key] for key in DEFAULTS}


def save_preferences(user_id,payload):
    values={**public_preferences(user_id),**payload}
    if set(payload)-set(DEFAULTS):raise ValueError("Réglage d’alerte inconnu.")
    if not isinstance(values['timezone'],str) or len(values['timezone'])>64:raise ValueError("Fuseau horaire invalide.")
    try:ZoneInfo(values['timezone'])
    except (ZoneInfoNotFoundError,ValueError):raise ValueError("Fuseau horaire inconnu.") from None
    for field in ('quiet_start','quiet_end','summary_time'):
        if values[field]=='' and field!='summary_time':values[field]=None
        value=values[field]
        if value is None and field!='summary_time':continue
        if not isinstance(value,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value):raise ValueError("Indiquez une heure valide au format HH:MM.")
    if bool(values['quiet_start'])!=bool(values['quiet_end']) or (values['quiet_start'] and values['quiet_start']==values['quiet_end']):
        raise ValueError("Indiquez le début et la fin de la plage silencieuse, à des heures différentes.")
    for field in ('daily_summary','price_alerts'):
        if type(values[field]) is not bool:raise ValueError("Une valeur activée/désactivée est requise.")
    with repo.session_scope() as session:
        user=session.get(m.User,user_id)
        if not user:raise ValueError("Compte introuvable.")
        row=session.get(m.NotificationPreference,user_id)
        if not row:row=m.NotificationPreference(user_id=user_id);session.add(row)
        for key in DEFAULTS:setattr(row,key,values[key])
    return values


def local_time(prefs,now):
    return now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(prefs['timezone']))


def is_quiet(prefs,now=None):
    start,end=prefs.get('quiet_start'),prefs.get('quiet_end')
    if not start or not end:return False
    value=local_time(prefs,now or datetime.utcnow()).strftime('%H:%M')
    return start<=value<end if start<end else value>=start or value<end


def next_allowed(prefs,now):
    if not is_quiet(prefs,now):return now
    # Walk in UTC, so nonexistent or repeated wall-clock hours across DST
    # never create an invalid scheduled instant.
    candidate=now.replace(second=0,microsecond=0)
    for _ in range(1501):
        candidate+=timedelta(minutes=1)
        if not is_quiet(prefs,candidate):return candidate
    raise ValueError("Plage silencieuse invalide.")


def summary_due(prefs,now):
    if not prefs['daily_summary'] or is_quiet(prefs,now):return False
    if prefs.get('retry_after') and prefs['retry_after']>now:return False
    local=local_time(prefs,now)
    if local.strftime('%H:%M')<prefs['summary_time']:return False
    last=prefs.get('last_summary_at')
    return not last or local_time(prefs,last).date()<local.date()


def hold_ordinary(user_id,now=None):
    prefs=get_preferences(user_id)
    return prefs['daily_summary'] or is_quiet(prefs,now)
