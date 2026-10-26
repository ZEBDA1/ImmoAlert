"""Operational snapshots and bounded request history, without request bodies."""
from __future__ import annotations

from datetime import datetime, timedelta
import re

from sqlalchemy import select, func, delete
from database import repo, models as m
from services import web_auth
from config import settings
from utils.fetch_diagnostics import decode_events, safe_message


def _date(value):
    return value.isoformat()+"Z" if value else None


def safe_error(value):
    return safe_message(value)


def source_health(*, enabled, state, latest, running, now=None):
    """Separate real evidence, stale checks and provider failures from 'healthy'."""
    now=now or datetime.utcnow()
    observed=latest.finished_at or latest.started_at if latest else None
    age=(now-observed).total_seconds() if observed else None
    status=state.get("last_status", "never_run")
    error=str(state.get("last_error") or "").lower()
    until=state.get("disabled_until")
    if not enabled:
        code,label,detail,severity="disabled","Désactivé","Ce collecteur a été désactivé par un administrateur.","neutral"
    elif running:
        code,label,detail,severity="collecting","Collecte en cours","Le collecteur recherche actuellement des annonces.","neutral"
    elif until and until>now:
        code,label,detail,severity="cooldown","Reprise différée","Des erreurs répétées ont déclenché une pause automatique.","warning"
    elif not observed or status=="never_run":
        code,label,detail,severity="unverified","À vérifier","Aucune collecte récente ne permet encore de confirmer son état.","neutral"
    elif age is not None and age>settings.scheduler_interval_seconds+300:
        code,label,detail,severity="stale","Contrôle ancien","La prochaine collecte attendue est dépassée de plus de 5 minutes. Un diagnostic est conseillé.","warning"
    elif status=="failed" and any(value in error for value in ("quota","credit","402","rate_limit","429")):
        code,label,detail,severity="quota_exhausted","Quota ou limite atteinte","Vérifiez le crédit et les limites du fournisseur dans Clés et quotas.","danger"
    elif status=="failed" and any(value in error for value in ("403","captcha","access denied","blocked","refus")):
        code,label,detail,severity="blocked","Accès bloqué","Le portail refuse la collecte. Ce résultat ne confirme pas une panne du programme.","danger"
    elif status=="failed":
        code,label,detail,severity="unavailable","En difficulté","La dernière collecte a échoué. Le diagnostic indique l’erreur rencontrée.","danger"
    elif status=="partial":
        code,label,detail,severity="degraded","À surveiller","Des annonces ont été trouvées, mais certaines zones ou requêtes ont échoué.","warning"
    elif status=="empty" or not state.get("last_unique_count",latest.unique_count if latest else 0):
        code,label,detail,severity="no_results","Aucun résultat","Le collecteur a terminé sans annonce validée. Cela peut venir de la zone ou de l’extraction.","warning"
    elif state.get("last_raw_count",0)>=5 and state.get("last_dropped_count",0)/max(1,state.get("last_raw_count",0))>.65:
        code,label,detail,severity="degraded","Extraction à vérifier","Une grande partie des annonces a été écartée par les contrôles de qualité.","warning"
    else:
        code,label,detail,severity="healthy","Normal","La dernière collecte récente a obtenu des annonces cohérentes sans erreur signalée.","good"
    return {"state":code,"label":label,"detail":detail,"severity":severity,"checked_at":_date(now),"last_observed_at":_date(observed)}


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
    from services.scheduler import source_names, get_sources_health, _source_inflight, schedule_status
    health=get_sources_health()
    running=list(_source_inflight.values())
    runs=repo.get_latest_source_runs(limit=200)
    sources=[]
    for name in source_names():
        state=health.get(name,{})
        recent=[r for r in runs if r.source==name]
        latest=recent[0] if recent else None
        incident=next((r for r in recent if r.status in {"failed","partial","empty"}),None)
        if incident is None:
            with repo.session_scope() as session:
                incident=session.scalar(select(m.SourceRun).where(m.SourceRun.source==name,m.SourceRun.status.in_(["failed","partial","empty"])).order_by(m.SourceRun.id.desc()).limit(1))
        previous_success=next((r.finished_at for r in recent if r.status=="success" and r.unique_count>0),None)
        previous_failure=next((r.finished_at for r in recent if r.status in {"failed","partial"}),None)
        enabled=repo.get_runtime_setting("source_enabled:"+name)!="false"
        current_runs=[dict(v) for v in running if v["source"]==name]
        health_detail=source_health(enabled=enabled,state=state,latest=latest,running=current_runs)
        sources.append({"name":name,"enabled":enabled,"health":health_detail,
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
            "running":current_runs,"diagnostics":decode_events(latest.diagnostics_json) if latest else [],
            "last_incident":{"at":_date(incident.finished_at),"error":safe_error(incident.last_error),"diagnostics":decode_events(incident.diagnostics_json),"cycle_id":incident.cycle_id} if incident else None})
    with repo.session_scope() as session:
        queued=session.scalar(select(func.count()).select_from(m.PendingAlert).where(m.PendingAlert.status=="pending"))
        sessions=session.scalar(select(func.count()).select_from(m.WebSession).where(m.WebSession.expires_at>datetime.utcnow(),(m.WebSession.user_id.is_not(None)) | (m.WebSession.telegram_id.is_not(None))))
    summary={key:sum(row["health"]["severity"]==value for row in sources) for key,value in (("healthy","good"),("warnings","warning"),("issues","danger"))}
    summary.update(running=sum(bool(row["running"]) for row in sources),disabled=sum(not row["enabled"] for row in sources),total=len(sources))
    return {"sources":sources,"health_summary":summary,"schedule":schedule_status(),"accounts":web_auth.list_accounts(),"pending_alerts":queued,"sessions":sessions,
        "source_runs":[{"id":r.id,"source":r.source,"cycle_id":r.cycle_id,"status":r.status,"started_at":_date(r.started_at),"finished_at":_date(r.finished_at),"duration_seconds":r.duration_seconds,"locations_count":r.locations_count,"raw_count":r.raw_count,"normalized_count":r.normalized_count,"dropped_count":r.dropped_count,"unique_count":r.unique_count,"new_count":r.new_count,"timeout_count":r.timeout_count,"error_count":r.error_count,"last_error":safe_error(r.last_error)} for r in runs]}
