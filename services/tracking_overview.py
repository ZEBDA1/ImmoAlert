"""A bounded, private overview of favorites and retained property notes."""
from datetime import datetime

from sqlalchemy import select, func, case, or_, and_
from database import models as m, repo
from services.deduplication import normalized
from services.inventory import integer
from services.property_tracking import STAGES, serialize_note, iso
from services.site_sync import _listing


def listing_page(user_id, params):
    limit=integer(params,'limit',20,48);page=integer(params,'page',1,1000000)
    if not limit or not page:raise ValueError('La page et sa taille doivent être positives.')
    text=params.get('q','');stage=params.get('stage','');scope=params.get('scope','all')
    upcoming=params.get('upcoming','0');sort=params.get('sort','upcoming')
    if not isinstance(text,str) or len(text)>120:raise ValueError('La recherche doit contenir au maximum 120 caractères.')
    if stage and stage not in STAGES:raise ValueError('Étape de suivi inconnue.')
    if scope not in {'all','active','archived'}:raise ValueError('Sélection de suivis invalide.')
    if upcoming not in {'0','1'} or sort not in {'upcoming','recent'}:raise ValueError('Tri des suivis invalide.')
    now=datetime.utcnow()
    note=m.MemberListingNote;favorite=m.Favorite;listing=m.Listing
    with repo.session_scope() as session:
        account=session.scalar(select(m.WebAccount).where(m.WebAccount.user_id==user_id))
        if not session.get(m.User,user_id) or (account and not account.enabled):raise PermissionError('Compte désactivé.')
        available=and_(listing.removed_at.is_(None),or_(listing.inventory_status.is_(None),listing.inventory_status!='invalid'))
        active=and_(favorite.id.is_not(None),available)
        step=func.coalesce(note.stage,'to_contact')
        upcoming_visit=and_(step=='visit',note.visit_at>=now,active)
        query=(select(listing,note,favorite).select_from(listing).outerjoin(note,and_(note.listing_id==listing.id,note.user_id==user_id))
               .outerjoin(favorite,and_(favorite.listing_id==listing.id,favorite.user_id==user_id))
               .where(or_(note.user_id.is_not(None),favorite.id.is_not(None))))
        if scope=='active':query=query.where(active)
        elif scope=='archived':query=query.where(~active)
        searchable=(case((available,func.coalesce(listing.search_text,'')),else_='')+' '+func.coalesce(note.search_text,''))
        for token in normalized(text).split():query=query.where(searchable.contains(token,autoescape=True))
        counts={key:0 for key in STAGES}
        for key,count in session.execute(query.with_only_columns(step,func.count()).group_by(step)):
            if key in counts:counts[key]=count
        upcoming_count=session.scalar(query.where(upcoming_visit).with_only_columns(func.count())) or 0
        if stage:query=query.where(step==stage)
        if upcoming=='1':query=query.where(upcoming_visit)
        total=session.scalar(query.with_only_columns(func.count())) or 0
        page=min(page,max(1,(total+limit-1)//limit));offset=(page-1)*limit
        updated=func.coalesce(note.updated_at,favorite.created_at,listing.first_seen_at)
        order=[updated.desc(),listing.id.desc()]
        if sort=='upcoming':order=[case((upcoming_visit,0),else_=1),case((upcoming_visit,note.visit_at),else_=None).asc().nullslast(),*order]
        rows=session.execute(query.order_by(*order).offset(offset).limit(limit)).all()
        items=[]
        for row,private,saved in rows:
            visible=row.removed_at is None and row.inventory_status!='invalid'
            tracked=serialize_note(private) if private else {'listing_id':row.id,'note':'','stage':'to_contact','visit_at':None,'reminder_at':None,'updated_at':iso(saved.created_at)}
            # Removed content stays removed, while the owner's personal notes remain readable.
            summary=_listing(row) if visible else {'id':row.id,'title':'Annonce retirée','location':'','price':None,'images':[],'surface_m2':None,'rooms':None}
            items.append({'listing':{key:summary.get(key) for key in ('id','title','location','price','images','surface_m2','rooms')},
                          'tracking':tracked,'is_favorite':saved is not None,'available':visible,'archived':saved is None or not visible})
    return {'items':items,'total':total,'counts':counts,'upcoming_count':upcoming_count,'page':page,'limit':limit,'next_page':page+1 if offset+len(items)<total else None}
