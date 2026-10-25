"""Operational snapshots and bounded request history, without request bodies."""
from __future__ import annotations

from datetime import datetime, timedelta
import re

from sqlalchemy import select, func, delete
from database import repo, models as m
from services import web_auth


def _date(value):
    return value.isoformat()+"Z" if value else None


def safe_error(value):
    value=re.sub(r"(https?://)[^ /:@]+:[^ /@]+@",r"\1[masqué]@",str(value or ""))
    return re.sub(r"(?i)(token|api[_-]?key|password|secret)(\s*[=:]\s*)[^\s,;]+",r"\1\2[masqué]",value)[:2000]


def record_request(item):
    with repo.session_scope() as session:
        session.add(m.WebRequest(**item))
        # Bound storage, including sustained polling; no query string or payload.
        session.execute(delete(m.WebRequest).where(m.WebRequest.created_at<datetime.utcnow()-timedelta(days=3)))
        older=select(m.WebRequest.id).order_by(m.WebRequest.created_at.desc()).offset(20000).limit(1000)
        session.execute(delete(m.WebRequest).where(m.WebRequest.id.in_(older)))


def recent_requests(limit=300):
    with repo.session_scope() as session:
        rows=list(session.scalars(select(m.WebRequest).order_by(m.WebRequest.created_at.desc()).limit(limit)))
        return [{"id":r.id,"user_id":r.user_id,"actor":r.actor,"method":r.method,"path":r.path,"action":r.action,"status":r.status,"duration_ms":round(r.duration_ms,1),"created_at":_date(r.created_at)} for r in rows]


def operations():
    from services.scheduler import source_names, get_sources_health, _source_inflight
    health=get_sources_health()
    running=list(_source_inflight.values())
    runs=repo.get_latest_source_runs(limit=200)
    sources=[]
    for name in source_names():
        state=health.get(name,{})
        recent=[r for r in runs if r.source==name]
        latest=recent[0] if recent else None
        previous_success=next((r.finished_at for r in recent if r.status=="success" and r.unique_count>0),None)
        previous_failure=next((r.finished_at for r in recent if r.status in {"failed","partial"}),None)
        sources.append({"name":name,"enabled":repo.get_runtime_setting("source_enabled:"+name)!="false",
            "status":state.get("last_status","never_run"),"last_error":safe_error(state.get("last_error")),
            "last_success_at":_date(state.get("last_success_at") or previous_success),"last_failure_at":_date(state.get("last_failure_at") or previous_failure),
            "disabled_until":_date(state.get("disabled_until")),"consecutive_failures":state.get("consecutive_failures",0),
            "consecutive_empty_runs":state.get("consecutive_empty_runs",0),"duration_seconds":state.get("last_cycle_seconds"),
            "locations_count":state.get("last_locations_count",0),"raw_count":state.get("last_raw_count",0),
            "normalized_count":state.get("last_normalized_count",0),"dropped_count":state.get("last_dropped_count",0),
            "unique_count":state.get("last_unique_count",0),"new_count":state.get("last_new_count",0),
            "timeout_count":state.get("last_timeout_count",0),"error_count":state.get("last_error_count",0),
            "last_run_at":_date(latest.finished_at if latest else None),"recent_runs":len(recent),
            "success_rate":round(100*sum(r.status=="success" for r in recent)/len(recent)) if recent else None,
            "running":[dict(v) for v in running if v["source"]==name]})
    with repo.session_scope() as session:
        queued=session.scalar(select(func.count()).select_from(m.PendingAlert).where(m.PendingAlert.status=="pending"))
        sessions=session.scalar(select(func.count()).select_from(m.WebSession).where(m.WebSession.expires_at>datetime.utcnow(),(m.WebSession.user_id.is_not(None)) | (m.WebSession.telegram_id.is_not(None))))
    return {"sources":sources,"accounts":web_auth.list_accounts(),"pending_alerts":queued,"sessions":sessions,
        "source_runs":[{"id":r.id,"source":r.source,"cycle_id":r.cycle_id,"status":r.status,"started_at":_date(r.started_at),"finished_at":_date(r.finished_at),"duration_seconds":r.duration_seconds,"locations_count":r.locations_count,"raw_count":r.raw_count,"normalized_count":r.normalized_count,"dropped_count":r.dropped_count,"unique_count":r.unique_count,"new_count":r.new_count,"timeout_count":r.timeout_count,"error_count":r.error_count,"last_error":safe_error(r.last_error)} for r in runs]}
