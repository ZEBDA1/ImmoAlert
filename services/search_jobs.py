"""Immediate, bounded inventory refreshes for account-owned website searches."""
from __future__ import annotations

import asyncio
from datetime import datetime
from hashlib import sha256
import json
import logging
import threading
from uuid import uuid4
from weakref import WeakKeyDictionary

from sqlalchemy import select

from database import models as m, repo

log = logging.getLogger(__name__)
_lock = threading.RLock()
_tasks: dict[str, asyncio.Task] = {}
_active: set[str] = set()
_controls: WeakKeyDictionary = WeakKeyDictionary()
_PUBLIC = (
    "id", "filter_id", "filter_name", "status", "stage", "started_at", "finished_at",
    "sources_total", "sources_done", "listings_seen", "matches_count", "error",
)
_FILTER_FIELDS = (
    "id", "user_id", "name", "price_min", "price_max", "surface_min", "rooms_min",
    "property_type", "budget_max_with_charges", "city", "postal_code", "radius_km", "active",
)
_PENDING = {"queued", "running"}


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _key(user_id: int) -> str:
    return f"search_jobs:{int(user_id)}"


def _read(user_id: int) -> list[dict]:
    raw = repo.get_runtime_setting(_key(user_id))
    try:
        rows = json.loads(raw) if raw else []
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    except (ValueError, TypeError):
        return []


def _write(user_id: int, rows: list[dict]):
    # Keep running searches and a small history. No URLs, provider errors, API
    # keys or account credentials are included in this record.
    pending = [row for row in rows if row.get("status") in _PENDING]
    finished = [row for row in rows if row.get("status") not in _PENDING][-12:]
    repo.set_runtime_setting(_key(user_id), json.dumps(finished + pending, ensure_ascii=False))


def _public(job: dict) -> dict:
    return {key: job.get(key) for key in _PUBLIC}


def _filter(user_id: int, filter_id: int):
    with repo.session_scope() as session:
        account = session.scalar(select(m.WebAccount).where(m.WebAccount.user_id == user_id))
        if account and not account.enabled:
            return None
        row = session.get(m.Filter, filter_id)
        if not row or row.user_id != user_id or not row.active:
            return None
        return row


def _signature(flt: m.Filter) -> str:
    values = {name: getattr(flt, name) for name in _FILTER_FIELDS}
    return sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _save(user_id: int, job: dict):
    with _lock:
        rows = _read(user_id)
        for index, row in enumerate(rows):
            if row.get("id") == job["id"]:
                rows[index] = dict(job)
                break
        else:
            rows.append(dict(job))
        _write(user_id, rows)


def user_jobs(user_id: int) -> list[dict]:
    """Read only this account's jobs, detecting interrupted work after restart."""
    with _lock:
        rows = _read(user_id)
        changed = False
        for row in rows:
            if row.get("status") in _PENDING and row.get("id") not in _active:
                row.update(status="failed", stage="Collecte interrompue", finished_at=_now(),
                           error="La collecte a été interrompue par un redémarrage. Enregistrez à nouveau la recherche pour la relancer.")
                changed = True
        if changed:
            _write(user_id, rows)
        return [_public(row) for row in reversed(rows)]


def _prepare(user_id: int, filter_id: int):
    flt = _filter(user_id, filter_id)
    if not flt:
        raise ValueError("Recherche introuvable ou désactivée.")
    signature = _signature(flt)
    with _lock:
        rows = _read(user_id)
        canceled = []
        for row in rows:
            if row.get("filter_id") != filter_id or row.get("status") not in _PENDING:
                continue
            if row.get("_signature") == signature and row.get("id") in _active:
                return row, False, []
            row.update(status="canceled", stage="Recherche remplacée", finished_at=_now(),
                       error="Les critères de cette recherche ont été modifiés.")
            canceled.append(row["id"])
        job = {
            "id": str(uuid4()), "filter_id": filter_id, "filter_name": flt.name,
            "status": "queued", "stage": "En attente de démarrage", "started_at": None,
            "finished_at": None, "sources_total": 0, "sources_done": 0,
            "listings_seen": 0, "matches_count": 0, "error": None,
            "_signature": signature,
        }
        _active.add(job["id"])
        try:
            _write(user_id, rows + [job])
        except Exception:
            _active.discard(job["id"])
            raise
        return job, True, canceled


def _loop_controls(user_id: int):
    loop = asyncio.get_running_loop()
    if loop not in _controls:
        _controls[loop] = {"slots": asyncio.Semaphore(2), "users": {}}
    controls = _controls[loop]
    users = controls["users"]
    if user_id not in users:
        users[user_id] = asyncio.Lock()
    return controls["slots"], users[user_id]


async def start_search(bot, user_id: int, filter_id: int) -> dict:
    """Queue immediately; identical pending requests reuse the same job."""
    # Finish the short persistence/enqueue section even if the HTTP client
    # disconnects: a saved filter must never retain an ownerless queued job.
    preparation = asyncio.create_task(asyncio.to_thread(_prepare, user_id, filter_id))
    interrupted = False
    try:
        job, created, obsolete = await asyncio.shield(preparation)
    except asyncio.CancelledError:
        interrupted = True
        job, created, obsolete = await preparation
    for identifier in obsolete:
        task = _tasks.get(identifier)
        if task:
            task.cancel()
    if created:
        task = asyncio.create_task(_run(bot, user_id, job), name=f"search-{job['id']}")
        task._search_owner = user_id
        task._search_job = job
        _tasks[job["id"]] = task
        task.add_done_callback(lambda completed, identifier=job["id"]: _forget(identifier, completed))
    if interrupted:
        raise asyncio.CancelledError()
    return _public(job)


def _forget(identifier: str, task: asyncio.Task):
    with _lock:
        # A task canceled before its coroutine starts never enters _run's
        # cancellation handler (for example during an immediate shutdown).
        if task.cancelled():
            job = dict(task._search_job)
            job.update(status="canceled", stage="Collecte arrêtée", finished_at=_now(),
                       error="La collecte a été interrompue avant son démarrage.")
            try:
                _save_if_current(task._search_owner, job, allow_obsolete=True)
            except Exception as exc:
                log.warning("Could not finalize canceled search (%s)", type(exc).__name__)
        if _tasks.get(identifier) is task:
            _tasks.pop(identifier, None)
        _active.discard(identifier)


def _current(user_id: int, job: dict) -> bool:
    with _lock:
        rows = _read(user_id)
        current = next((row for row in rows if row.get("id") == job["id"]), None)
        if not current or current.get("status") not in _PENDING:
            return False
        flt = _filter(user_id, job["filter_id"])
        return bool(flt and _signature(flt) == job["_signature"])


async def _run(bot, user_id: int, job: dict):
    from services.scheduler import collect_filter_inventory_once

    async def progress(result, stage):
        job.update(stage=stage, sources_total=result.sources_total, sources_done=result.sources_done,
                   listings_seen=result.listings_seen, matches_count=result.matches_count)
        await asyncio.to_thread(_save_if_current, user_id, job)

    slots, user_slot = _loop_controls(user_id)
    try:
        async with user_slot, slots:
            if not await asyncio.to_thread(_current, user_id, job):
                raise asyncio.CancelledError()
            flt = await asyncio.to_thread(_filter, user_id, job["filter_id"])
            user = await asyncio.to_thread(repo.get_user_by_id, user_id)
            if not flt or not user:
                raise asyncio.CancelledError()
            job.update(status="running", stage="Démarrage de la recherche", started_at=_now())
            await asyncio.to_thread(_save_if_current, user_id, job)
            result = await collect_filter_inventory_once(
                bot, user, flt, progress=progress,
                is_current=lambda: _current(user_id, job),
            )
            job.update(sources_total=result.sources_total, sources_done=result.sources_done,
                       listings_seen=result.listings_seen, matches_count=result.matches_count,
                       finished_at=_now())
            if result.sources_total == 0:
                job.update(status="failed", stage="Aucune source disponible",
                           error="Les sources sont désactivées, en pause ou sans quota de collecte. Consultez l’administrateur.")
            elif result.failed_sources:
                job.update(status="failed", stage="Collecte partielle" if result.matches_count else "Collecte indisponible",
                           error="Certaines sources n’ont pas pu être vérifiées. Les annonces trouvées restent disponibles. " + " ".join(result.errors[:3]))
            else:
                job.update(status="succeeded", stage="Recherche terminée", error=None)
            await asyncio.to_thread(_save_if_current, user_id, job)
    except asyncio.CancelledError:
        job.update(status="canceled", stage="Collecte arrêtée", finished_at=_now(),
                   error="Cette recherche a été modifiée, supprimée ou interrompue.")
        await asyncio.to_thread(_save_if_current, user_id, job, allow_obsolete=True)
    except Exception as exc:
        log.warning("Immediate search failed (%s)", type(exc).__name__)
        job.update(status="failed", stage="Collecte interrompue", finished_at=_now(),
                   error="La collecte n’a pas pu se terminer. Consultez l’état des sources ou réessayez.")
        await asyncio.to_thread(_save_if_current, user_id, job, allow_obsolete=True)


def _save_if_current(user_id: int, job: dict, allow_obsolete=False):
    with _lock:
        rows = _read(user_id)
        current = next((row for row in rows if row.get("id") == job["id"]), None)
        if not current or current.get("status") not in _PENDING:
            return
        if not allow_obsolete:
            flt = _filter(user_id, job["filter_id"])
            if not flt or _signature(flt) != job["_signature"]:
                raise asyncio.CancelledError()
        _save(user_id, job)


def _cancel_saved(user_id: int, filter_id: int) -> list[str]:
    with _lock:
        rows = _read(user_id)
        identifiers = []
        for job in rows:
            if job.get("filter_id") == filter_id and job.get("status") in _PENDING:
                job.update(status="canceled", stage="Recherche supprimée", finished_at=_now(),
                           error="Cette recherche a été supprimée.")
                identifiers.append(job["id"])
        if identifiers:
            _write(user_id, rows)
        return identifiers


async def cancel_search(user_id: int, filter_id: int):
    for identifier in await asyncio.to_thread(_cancel_saved, user_id, filter_id):
        task = _tasks.get(identifier)
        if task:
            task.cancel()


async def shutdown_search_jobs():
    """Stop queued/running jobs and persist a terminal state before shutdown."""
    pending = list(_tasks.values())
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
