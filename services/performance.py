"""Bounded, content-free performance measurements for the live application."""
from __future__ import annotations

from collections import defaultdict,deque
from datetime import datetime, timedelta
import logging
import math
from statistics import median
from time import perf_counter
from threading import Lock

from sqlalchemy import delete, select
from database import models as m, repo

log=logging.getLogger(__name__)
CLIENT_METRICS={"detail_visible","enrichment_complete"}
METRICS=CLIENT_METRICS | {"detail_api","detail_db","detail_collect","detail_reconcile","portal_collect","cache_age","inventory_api","favorite_check","location_area"}
_lock=Lock()
_buffer=deque(maxlen=1000)
_factory=None
_dropped=0


def record(name, value_ms, source=None):
    global _factory,_dropped
    if name not in METRICS or type(value_ms) not in (int,float) or not math.isfinite(value_ms) or not 0<=value_ms<=604800000:
        raise ValueError("Mesure de performance invalide.")
    if source is not None:
        from scraper.quality import SOURCE_HOSTS
        if source not in SOURCE_HOSTS:source=None
    with _lock:
        # A test or an isolated demo can deliberately replace the repository's
        # session factory. Never flush another database's pending samples here.
        if _factory is not repo.SessionLocal:_factory=repo.SessionLocal;_buffer.clear();_dropped=0
        if len(_buffer)==_buffer.maxlen:_dropped+=1
        _buffer.append({'name':name,'value_ms':round(value_ms,3),'source':source,'created_at':datetime.utcnow()})


def flush():
    global _dropped
    with _lock:
        if _factory is not repo.SessionLocal:return
        samples=list(_buffer);_buffer.clear();factory=_factory
    if not samples:return
    session=factory()
    try:
        session.add_all(m.PerformanceSample(**sample) for sample in samples);session.commit()
    except Exception:
        session.rollback()
        with _lock:_dropped+=len(samples)
        log.debug('Performance samples could not be persisted')
    finally:session.close()


def elapsed(name, started, source=None):
    record(name,(perf_counter()-started)*1000,source)


def summary(now=None):
    flush()
    now=now or datetime.utcnow()
    with repo.session_scope() as session:
        rows=list(session.scalars(select(m.PerformanceSample).where(m.PerformanceSample.created_at>=now-timedelta(hours=24)).order_by(m.PerformanceSample.id.desc()).limit(10000)))
    groups=defaultdict(list)
    for row in rows:groups[(row.name,row.source)].append(row.value_ms)
    metrics=[]
    for (name,source),values in sorted(groups.items(),key=lambda pair:(pair[0][0],pair[0][1] or "")):
        values.sort()
        metrics.append({"name":name,"source":source,"count":len(values),"median_ms":round(median(values),2),"p95_ms":round(values[max(0,math.ceil(.95*len(values))-1)],2)})
    with _lock:pending,dropped=len(_buffer) if _factory is repo.SessionLocal else 0,_dropped if _factory is repo.SessionLocal else 0
    return {"window_hours":24,"sample_limit":10000,"sample_count":len(rows),"pending_samples":pending,"dropped_samples":dropped,"metrics":metrics,"generated_at":now.isoformat()+"Z"}


def prune(now=None):
    flush()
    with repo.session_scope() as session:
        session.execute(delete(m.PerformanceSample).where(m.PerformanceSample.created_at<(now or datetime.utcnow())-timedelta(days=7)))
        cutoff=session.scalar(select(m.PerformanceSample.id).order_by(m.PerformanceSample.id.desc()).offset(20000).limit(1))
        if cutoff:session.execute(delete(m.PerformanceSample).where(m.PerformanceSample.id<=cutoff))
