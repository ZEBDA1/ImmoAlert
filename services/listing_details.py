"""Shared on-demand detail enrichment, with persistent success/failure caching."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
from hashlib import sha256
import logging
import threading
from urllib.parse import urlsplit

import requests

from database import repo, models as m
from scraper.details import extract_details
from scraper.quality import SOURCE_HOSTS, extract_postal_code, normalize_city_name
from services.deduplication import normalized
from utils.http import fetch_html
from utils.images import decode_images, encode_images, merge_images

log=logging.getLogger(__name__)
_slots=asyncio.Semaphore(2)
_tasks: dict[int,asyncio.Task]={}
_geo_lock=threading.Lock()
_merge_lock=threading.Lock()


def _city_point(location):
    postal=extract_postal_code(location)
    city=normalize_city_name(location)
    if not city:
        return None
    key="property_geo:"+sha256(((postal or "")+":"+normalized(city)).encode()).hexdigest()[:40]
    with _geo_lock:
        cached=repo.get_runtime_setting(key)
        if cached:
            data=json.loads(cached)
            if data.get("point") or datetime.fromisoformat(data["at"])>datetime.utcnow()-timedelta(hours=12):
                return data.get("point")
        try:
            params={"fields":"nom,centre","format":"json","geometry":"centre"}
            params.update({"codePostal":postal} if postal else {"nom":city})
            response=requests.get("https://geo.api.gouv.fr/communes",params=params,timeout=5)
            response.raise_for_status()
            rows=response.json()
            matches=[r for r in rows if normalized(r.get("nom"))==normalized(city)]
            match=matches[0] if len(matches)==1 else None
            point=match.get("centre",{}).get("coordinates") if match else None
            if not isinstance(point,list) or len(point)!=2:
                point=None
            repo.set_runtime_setting(key,json.dumps({"point":point,"at":datetime.utcnow().isoformat()}))
            return point
        except (requests.RequestException,ValueError,TypeError):
            return None


def _load(identifier):
    row=repo.get_listing_by_id(identifier)
    if not row:
        return None
    cooldown=timedelta(hours=24) if row.details_status=="complete" else timedelta(minutes=15)
    if row.details_checked_at and row.details_checked_at>datetime.utcnow()-cooldown:
        return row
    merged={"images":[],"features":{}}
    try:
        for pub in repo.listing_publications(row.id):
            if repo.get_runtime_setting("source_enabled:"+pub["source"])=="false":
                continue
            expected=SOURCE_HOSTS.get(pub["source"])
            hosts=(expected,) if expected else ()
            host=(urlsplit(pub["url"]).hostname or "").lower()
            if not any(host==h or host.endswith("."+h) for h in hosts):
                continue
            html=fetch_html(pub["url"],timeout=18)
            if not html:
                continue
            # Portal IDs are stored separately because a canonical property may
            # have a different identifier on each source.
            with repo.session_scope() as session:
                from sqlalchemy import select
                publication=session.scalar(select(m.ListingPublication).where(m.ListingPublication.listing_id==row.id,m.ListingPublication.source==pub["source"]))
                external_id=publication.external_id if publication else row.external_id
            data=extract_details(html,pub["url"],external_id)
            if len(data.get("description",""))>len(merged.get("description","")):
                merged["description"]=data["description"]
            merged["images"].extend(data["images"])
            merged["features"].update(data["features"])
            if "latitude" in data:
                merged.update({k:data[k] for k in ("latitude","longitude","geo_precision")})
    except Exception:
        log.warning("Detail collection unavailable for listing %s",row.id)
    if "latitude" not in merged and row.latitude is None:
        point=_city_point(row.location)
        if point:
            merged.update(latitude=point[1],longitude=point[0],geo_precision="city")
    with repo.session_scope() as session:
        current=session.get(m.Listing,row.id)
        if len(merged.get("description",""))>len(current.description or ""):
            current.description=merged["description"]
        current.images=encode_images(merge_images(merged["images"],current.images))
        features=json.loads(current.details or "{}")
        features.update(merged["features"])
        current.details=json.dumps(features,ensure_ascii=False)
        for key in ("latitude","longitude","geo_precision"):
            if key in merged:
                setattr(current,key,merged[key])
        current.details_checked_at=datetime.utcnow()
        current.details_status="complete" if merged.get("description") or merged["features"] or merged["images"] else "partial"
    return repo.get_listing_by_id(row.id)


async def get_listing_details(identifier: int):
    row=await asyncio.to_thread(repo.get_listing_by_id,identifier)
    if not row:
        return None
    identifier=row.id
    if identifier not in _tasks:
        async def run():
            async with _slots:
                return await asyncio.to_thread(_load,identifier)
        task=asyncio.create_task(run())
        _tasks[identifier]=task
        task.add_done_callback(lambda finished:_tasks.pop(identifier,None))
    row=await asyncio.shield(_tasks[identifier])
    def reconcile():
        with _merge_lock:
            repo.reconcile_duplicate_listings()
        return repo.get_listing_by_id(identifier)
    return await asyncio.to_thread(reconcile)


async def resolve_new_listing(row):
    """Before alerting, inspect plausible copies whose cards lack evidence."""
    if row.source not in SOURCE_HOSTS:
        return row
    candidates=await asyncio.to_thread(repo.possible_duplicate_listings,row)
    if not candidates:
        return row
    await asyncio.gather(*(get_listing_details(item.id) for item in [row,*candidates]))
    return await asyncio.to_thread(repo.get_listing_by_id,row.id)


def _inventory_candidates(limit=2):
    from sqlalchemy import select
    with repo.session_scope() as session:
        rows=list(session.scalars(select(m.Listing).where(repo.current_inventory_condition(),
            (m.Listing.details_checked_at.is_(None)) | (m.Listing.details_checked_at<datetime.utcnow()-timedelta(hours=24))
        ).order_by(m.Listing.details_checked_at.asc(),m.Listing.id).limit(800)))
    result=[]
    for row in rows:
        if row.source in SOURCE_HOSTS and repo.get_runtime_setting("source_enabled:"+row.source)!="false" and repo.possible_duplicate_listings(row):
            result.append(row)
            if len(result)>=limit:
                break
    return result


async def reconcile_inventory_details():
    """Gradually verify existing ambiguous cards without blocking Telegram."""
    while True:
        try:
            rows=await asyncio.to_thread(_inventory_candidates)
            for row in rows:
                await resolve_new_listing(row)
        except Exception:
            log.exception("Property duplicate verification failed")
        await asyncio.sleep(60)
