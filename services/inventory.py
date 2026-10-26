"""Server-side filtering and bounded listing pages, without the 800-row cap."""
from __future__ import annotations

from math import isfinite
import re
from sqlalchemy import select,func
from database import models as m,repo
from services.deduplication import normalized
from services.matcher import match_and_score
from services.member_notifications import as_scraped
from services.site_sync import _listing,_publications


def integer(params,name,default=None,maximum=1000000000):
    value=params.get(name)
    if value in (None,''):return default
    if not isinstance(value,str) or not re.fullmatch(r'\d{1,10}',value):raise ValueError(f'Paramètre invalide : {name}.')
    number=int(value)
    if number<0 or number>maximum:raise ValueError(f'Paramètre invalide : {name}.')
    return number


def _number(params,name,maximum):
    value=params.get(name)
    if value in (None,''):return None
    try:
        number=float(value)
        if not isfinite(number) or not 0<=number<=maximum:raise ValueError()
        return number
    except (ValueError,TypeError):raise ValueError(f'Paramètre invalide : {name}.') from None


def listing_page(user_id,params):
    limit=integer(params,'limit',18,48);page=integer(params,'page',1,1000000)
    if not limit or not page:raise ValueError('La page et sa taille doivent être positives.')
    query_text=params.get('q','')
    if not isinstance(query_text,str) or len(query_text)>120:raise ValueError('La recherche doit contenir au maximum 120 caractères.')
    source=params.get('source','')
    if source and not re.fullmatch(r'[a-z][a-z0-9_]{0,31}',source):raise ValueError('Source invalide.')
    sort=params.get('sort','recent')
    if sort not in {'recent','price','surface'}:raise ValueError('Tri inconnu.')
    favorite=params.get('favorites','0')
    if favorite not in {'0','1'}:raise ValueError('Sélection de favoris invalide.')
    budget=_number(params,'budget',1000000);surface=_number(params,'surface',100000);rooms=integer(params,'rooms',None,30)
    filter_id=integer(params,'filter_id')
    with repo.session_scope() as session:
        condition=(m.Listing.removed_at.is_(None))&((m.Listing.inventory_status!='invalid')|(m.Listing.inventory_status.is_(None))) if favorite=='1' else repo.current_inventory_condition()
        query=select(m.Listing).where(condition)
        if favorite=='1':query=query.where(m.Listing.id.in_(select(m.Favorite.listing_id).where(m.Favorite.user_id==user_id)))
        searchable=func.coalesce(m.Listing.search_text,func.lower(func.coalesce(m.Listing.title,'')+' '+func.coalesce(m.Listing.location,'')))
        for token in normalized(query_text).split():query=query.where(searchable.like('%'+token+'%'))
        if source:query=query.where((m.Listing.source==source)|(m.Listing.id.in_(select(m.ListingPublication.listing_id).where(m.ListingPublication.source==source))))
        if budget is not None:query=query.where(m.Listing.price<=budget)
        if surface is not None:query=query.where(m.Listing.surface_m2>=surface)
        if rooms is not None:query=query.where(m.Listing.rooms>=rooms)
        selected=None
        if filter_id:
            selected=session.scalar(select(m.Filter).where(m.Filter.id==filter_id,m.Filter.user_id==user_id))
            if not selected:raise ValueError('Recherche introuvable.')
            for column,value,minimum in [(m.Listing.price,selected.price_min,True),(m.Listing.price,selected.price_max,False),(m.Listing.price,selected.budget_max_with_charges,False),(m.Listing.surface_m2,selected.surface_min,True),(m.Listing.rooms,selected.rooms_min,True)]:
                if value is not None:query=query.where(column>=value if minimum else column<=value)
        order=(m.Listing.price.asc().nullslast(),m.Listing.id.desc()) if sort=='price' else (m.Listing.surface_m2.desc().nullslast(),m.Listing.id.desc()) if sort=='surface' else (m.Listing.first_seen_at.desc(),m.Listing.id.desc())
        query=query.order_by(*order);offset=(page-1)*limit
        if selected:
            # SQL reduces the numeric shortlist; stream only the remaining
            # evidence through the shared matcher (radius/type/location).
            total=0;rows=[]
            for row in session.scalars(query.execution_options(yield_per=200)):
                if not match_and_score(selected,as_scraped(row)).matched:continue
                if offset<=total<offset+limit:rows.append(row)
                total+=1
        else:
            total=session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
            rows=list(session.scalars(query.offset(offset).limit(limit)))
        ids=[row.id for row in rows]
        favorites=set(session.scalars(select(m.Favorite.listing_id).where(m.Favorite.user_id==user_id,m.Favorite.listing_id.in_(ids))))
        read=set(session.scalars(select(m.ListingRead.listing_id).where(m.ListingRead.user_id==user_id,m.ListingRead.listing_id.in_(ids))))
        notes={row.listing_id:row for row in session.scalars(select(m.MemberListingNote).where(m.MemberListingNote.user_id==user_id,m.MemberListingNote.listing_id.in_(ids)))}
        publications=_publications(session,rows)
        items=[]
        for row in rows:
            item=_listing(row,publications=publications.get(row.id));item['description']=''
            item.update(is_favorite=row.id in favorites,is_read=row.id in read,tracking_stage=notes[row.id].stage if row.id in notes else None)
            items.append(item)
    return {'items':items,'total':total,'page':page,'limit':limit,'next_page':page+1 if offset+len(items)<total else None}
