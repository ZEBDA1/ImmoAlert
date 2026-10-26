"""Conservative, scheduled availability checks for saved properties."""
from __future__ import annotations

from datetime import datetime,timedelta
import asyncio
import logging
import math
from time import perf_counter
import re
from urllib.parse import urlsplit
from sqlalchemy import select,func,case
from database import models as m,repo
from config import settings
from scraper.details import extract_details
from scraper.quality import SOURCE_HOSTS
from services.property_tracking import record_availability
from utils.fetch_diagnostics import capture_problems
from utils.http import fetch_html,is_probably_blocked


_last_run={}


def _query(session):
    disabled=list(session.scalars(select(m.RuntimeSetting.key).where(m.RuntimeSetting.key.like('source_enabled:%'),m.RuntimeSetting.value=='false')))
    disabled=[key.removeprefix('source_enabled:') for key in disabled]
    return select(m.ListingPublication).join(m.Listing,m.Listing.id==m.ListingPublication.listing_id).where(
        m.Listing.removed_at.is_(None),m.ListingPublication.source.in_(SOURCE_HOSTS),m.ListingPublication.source.not_in(disabled),
        m.ListingPublication.listing_id.in_(select(m.Favorite.listing_id).where(m.Favorite.user_id.not_in(select(m.WebAccount.user_id).where(m.WebAccount.enabled==False)))))


def freshness_plan(now=None):
    now=now or datetime.utcnow();target=settings.favorite_freshness_target_hours
    with repo.session_scope() as session:
        query=_query(session).subquery()
        due=(query.c.availability_checked_at.is_(None))|(query.c.availability_checked_at<=now-timedelta(hours=target))
        rows=session.execute(select(query.c.source,func.count(),func.sum(case((due,1),else_=0))).group_by(query.c.source)).all()
        sources=[]
        for source,total,waiting in rows:
            until=session.scalar(select(m.SourceRun.disabled_until).where(m.SourceRun.source==source).order_by(m.SourceRun.finished_at.desc(),m.SourceRun.id.desc()).limit(1))
            sources.append({'source':source,'publications':total,'due':int(waiting or 0),'paused_until':until.isoformat()+'Z' if until and until>now else None})
    active=[row for row in sources if not row['paused_until']]
    total=sum(row['publications'] for row in active);due=sum(row['due'] for row in active)
    interval=max(60,math.ceil(_last_run.get('duration_ms',0)/1000)+5)
    requested=max(2,math.ceil(total*interval/(target*3600)))
    batch=min(settings.favorite_freshness_max_batch,requested)
    capacity=min(batch,sum(min(2,row['publications']) for row in active))
    coverage=max(target,total*interval/(capacity*3600)) if capacity else None
    return {'target_hours':target,'batch_size':batch,'max_batch':settings.favorite_freshness_max_batch,'source_batch_limit':2,'parallel_checks':3,
            'interval_seconds':interval,'eligible_publications':total,'due_publications':due,'paused_publications':sum(row['publications'] for row in sources if row['paused_until']),
            'estimated_coverage_hours':round(coverage,2) if coverage is not None else None,'capacity_limited':bool(coverage and coverage>target),
            'sources':sources,'last_run':dict(_last_run)}


def due_publications(now=None,limit=None):
    now=now or datetime.utcnow();plan=freshness_plan(now)
    maximum=plan['batch_size'] if limit is None else max(0,min(limit,24))
    rows=[]
    with repo.session_scope() as session:
        query=_query(session).where((m.ListingPublication.availability_checked_at.is_(None))|(m.ListingPublication.availability_checked_at<=now-timedelta(hours=plan['target_hours'])))
        for source in plan['sources']:
            if source['paused_until'] or not source['due']:continue
            rows.extend(session.scalars(query.where(m.ListingPublication.source==source['source']).order_by(m.ListingPublication.availability_checked_at.asc(),m.ListingPublication.id).limit(min(2,maximum))))
    return sorted(rows,key=lambda row:(row.availability_checked_at or datetime.min,row.id))[:maximum]


def check_publication(publication,*,now=None):
    expected=SOURCE_HOSTS.get(publication.source)
    host=(urlsplit(publication.url).hostname or '').lower()
    if not expected or not (host==expected or host.endswith('.'+expected)):
        record_availability(publication.id,'check_failed',now=now);return 'check_failed'
    problems=[];events=[]
    try:
        with capture_problems(problems,events,source=publication.source):
            html=fetch_html(publication.url,timeout=15)
        if html and not is_probably_blocked(html):
            from bs4 import BeautifulSoup
            soup=BeautifulSoup(html,'lxml')
            for node in soup.select('script,style,template,footer'):node.decompose()
            text=soup.get_text(' ',strip=True).lower()
            absent=re.compile(r"(?:cette|l[’'])\s*annonce\s+(?:n[’']est plus (?:disponible|en ligne)|a été supprimée|a expiré)|ce bien n[’']est plus disponible|annonce (?:introuvable|supprimée|indisponible|expirée)|this (?:listing|property) is no longer available")
            primary=soup.find('h1') or soup.find('h2')
            banners=([primary.get_text(' ',strip=True).lower()] if primary else [])+[node.get_text(' ',strip=True).lower() for node in soup.select('[role="alert"]')]
            explicit_absence=any(absent.search(value) for value in banners) or len(text)<600 and bool(absent.match(text))
            data=extract_details(html,publication.url,publication.external_id)
            # Portals can retain the old JSON-LD after marking an ad expired.
            # Its visible, primary expiry notice supersedes that stale data.
            outcome='absent' if explicit_absence else 'available' if data.get('description') or data.get('images') or data.get('features') else 'check_failed'
        else:
            # A provider API error, a challenge or a transport error is never
            # evidence of a deleted listing. Only the portal's own 404/410 is.
            missing=any(event.get('code')=='http_404' or event.get('http_status') in {404,410} and event.get('engine') in {'scrapling_http','chromium'} and (urlsplit(event.get('url','')).hostname or '').lower()==host for event in events)
            outcome='absent' if missing else 'check_failed'
    except Exception:
        outcome='check_failed'
    record_availability(publication.id,outcome,now=now)
    return outcome


async def run_freshness_monitor():
    global _last_run
    from services import performance
    while True:
        started=perf_counter()
        try:
            rows=await asyncio.to_thread(due_publications)
            slots=asyncio.Semaphore(3);source_slots={row.source:asyncio.Semaphore(1) for row in rows}
            async def check(row):
                async with source_slots[row.source],slots:
                    began=perf_counter()
                    try:return await asyncio.to_thread(check_publication,row)
                    finally:performance.elapsed('favorite_check',began,row.source)
            outcomes=await asyncio.gather(*(check(row) for row in rows))
            _last_run={'checked_at':datetime.utcnow().isoformat()+'Z','count':len(rows),'available':outcomes.count('available'),
                       'absent':outcomes.count('absent'),'failed':outcomes.count('check_failed'),'duration_ms':round((perf_counter()-started)*1000,2)}
        except asyncio.CancelledError:raise
        except Exception:logging.getLogger(__name__).exception('Favorite availability check failed')
        await asyncio.sleep(max(5,60-(perf_counter()-started)))
