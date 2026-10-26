"""Bounded, cached geographic areas; never infer a property's exact address."""
from __future__ import annotations

from collections import OrderedDict
import asyncio
import math
import re
from threading import Lock
from time import monotonic

from utils.locations import public_json,resolve_commune

_cache=OrderedDict()
_lock=Lock()
_tasks={}
_slots=asyncio.Semaphore(2)


def _point(latitude,longitude):
    return all(type(v) in (int,float) and math.isfinite(v) for v in (latitude,longitude)) and abs(latitude)<=85.0511 and abs(longitude)<=180


def validate_geometry(value):
    if not isinstance(value,dict) or value.get('type') not in {'Polygon','MultiPolygon'}:return None
    polygons=[value.get('coordinates')] if value['type']=='Polygon' else value.get('coordinates')
    if not isinstance(polygons,list) or not polygons or len(polygons)>100:return None
    count=0
    for polygon in polygons:
        if not isinstance(polygon,list) or not polygon:return None
        for ring in polygon:
            if not isinstance(ring,list) or len(ring)<4 or ring[0]!=ring[-1]:return None
            for point in ring:
                count+=1
                if count>20000 or not isinstance(point,list) or len(point)!=2 or not _point(point[1],point[0]):return None
    return {'type':value['type'],'coordinates':value['coordinates']}


def _bounds(geometry):
    polygons=[geometry['coordinates']] if geometry['type']=='Polygon' else geometry['coordinates']
    points=[p for polygon in polygons for ring in polygon for p in ring]
    return [min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]


def _fallback(row):
    lat,lon=row.latitude,row.longitude
    if _point(lat,lon):
        portal=row.geo_precision=='portal'
        return {'kind':'indicative','center':[lat,lon],'radius_m':500 if portal else 3000,'geometry':None,
                'label':'Secteur indicatif' if portal else 'Repère communal indicatif',
                'detail':'Rayon indicatif de 500 m autour du point du portail. Le portail peut décaler cette position ; ce cercle ne garantit pas l’adresse du bien.' if portal else 'Périmètre indicatif de 3 km autour du repère communal. Le contour officiel est indisponible ; ce cercle ne représente ni les limites de la commune ni une adresse.'}
    return {'kind':'unavailable','geometry':None,'center':None,'label':'Localisation non disponible',
            'detail':'Les informations disponibles ne permettent pas de délimiter une zone fiable.'}


def _area(row):
    if row.geo_precision=='portal' and _point(row.latitude,row.longitude):return _fallback(row)
    city=row.normalized_city or re.split(r'[\d(]',row.location or '')[0].strip(' ,')
    postal=row.normalized_postal_code
    if not postal:
        match=re.search(r'\b\d{5}\b',row.location or '');postal=match.group() if match else None
    commune=resolve_commune(city,postal) if city else None
    if commune and re.fullmatch(r'[0-9AB]{5}',commune.get('code','')):
        feature=public_json('https://geo.api.gouv.fr/communes/'+commune['code'],{'fields':'nom,code','format':'geojson','geometry':'contour'})
        geometry=validate_geometry(feature.get('geometry')) if isinstance(feature,dict) and feature.get('type')=='Feature' else None
        if geometry:
            bounds=_bounds(geometry)
            return {'kind':'commune','center':[(bounds[1]+bounds[3])/2,(bounds[0]+bounds[2])/2],'bounds':bounds,'geometry':geometry,'radius_m':None,
                    'label':'Contour de la commune','detail':f"{commune['nom']} · limites administratives officielles. La localisation connue reste à l’échelle de la commune, sans adresse précise.",
                    'attribution':'API Découpage administratif · données publiques'}
    return _fallback(row)


def cache_key(row):
    return (row.geo_precision,row.latitude,row.longitude,row.normalized_city,row.normalized_postal_code,row.location)


def _cached_area(row):
    key=cache_key(row)
    with _lock:
        cached=_cache.get(key)
        if cached and monotonic()<cached[0]:_cache.move_to_end(key);return cached[1]
    result=_area(row)
    ttl=86400 if result['kind']=='commune' or row.geo_precision=='portal' else 120
    with _lock:
        _cache[key]=(monotonic()+ttl,result);_cache.move_to_end(key)
        while len(_cache)>256:_cache.popitem(last=False)
    return result


async def for_listing(row):
    key=cache_key(row)
    with _lock:
        cached=_cache.get(key)
        if cached and monotonic()<cached[0]:_cache.move_to_end(key);return cached[1]
    if row.geo_precision=='portal' and _point(row.latitude,row.longitude):return _fallback(row)
    if key not in _tasks and len(_tasks)>=64:return _fallback(row)

    if key not in _tasks:
        async def run():
            async with _slots:return await asyncio.to_thread(_cached_area,row)
        task=asyncio.create_task(run());_tasks[key]=task
        def completed(done):
            if _tasks.get(key) is done:_tasks.pop(key,None)
            if not done.cancelled():done.exception()
        task.add_done_callback(completed)
    return await asyncio.shield(_tasks[key])
