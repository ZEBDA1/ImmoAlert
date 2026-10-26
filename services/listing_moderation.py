"""Per-member reports and administrative removal, with durable tombstones."""
from datetime import datetime
from sqlalchemy import select,func,update
from database import repo,models as m
from services import web_auth


def _canonical(session, identifier):
    row=session.get(m.Listing,identifier);seen=set()
    while row and row.duplicate_of_id:
        if row.id in seen:raise ValueError("Annonce introuvable.")
        seen.add(row.id);row=session.get(m.Listing,row.duplicate_of_id)
    if row is None:raise ValueError("Annonce introuvable.")
    return row


def report_listing(user_id, identifier, note=""):
    if not isinstance(note,str) or len(note)>500:
        raise ValueError("Le commentaire doit contenir au maximum 500 caractères.")
    with repo._listing_lock,repo.session_scope() as session:
        user=session.get(m.User,user_id)
        if not user:raise PermissionError("Compte introuvable.")
        account=session.scalar(select(m.WebAccount).where(m.WebAccount.user_id==user_id))
        if account and not account.enabled:raise PermissionError("Compte désactivé.")
        row=_canonical(session,identifier)
        if row.removed_at is not None or row.inventory_status=="invalid":raise ValueError("Cette annonce n’est plus disponible sur le site.")
        report=session.scalar(select(m.ListingReport).where(m.ListingReport.user_id==user_id,m.ListingReport.listing_id==row.id))
        created=report is None
        if created:
            report=m.ListingReport(user_id=user_id,listing_id=row.id,note=note.strip(),status="open")
            session.add(report);session.flush()
        return {"listing_id":row.id,"status":report.status,"created":created}


def own_reports(user_id):
    with repo.session_scope() as session:
        return [{"listing_id":r.listing_id,"status":r.status} for r in session.scalars(select(m.ListingReport).where(m.ListingReport.user_id==user_id))]


def _admin(user_id):
    user=repo.get_user_by_id(user_id)
    if not user or not web_auth.is_admin(user_id):
        raise PermissionError("Accès administrateur requis.")


def moderate(user_id, identifier, operation):
    _admin(user_id)
    if operation not in {"dismiss","remove"}:raise ValueError("Action de modération inconnue.")
    with repo._listing_lock,repo.session_scope() as session:
        row=_canonical(session,identifier);now=datetime.utcnow()
        identifiers={row.id};front={row.id}
        while front:
            children=set(session.scalars(select(m.Listing.id).where(m.Listing.duplicate_of_id.in_(front))))-identifiers
            identifiers.update(children);front=children
        if operation=="remove":
            for listing in session.scalars(select(m.Listing).where(m.Listing.id.in_(identifiers))):
                if listing.removed_at is None:listing.removed_at=now;listing.removed_by=user_id
            session.execute(update(m.PendingAlert).where(m.PendingAlert.listing_id.in_(identifiers),m.PendingAlert.status=="pending").values(status="canceled"))
            session.execute(update(m.ListingReport).where(m.ListingReport.listing_id.in_(identifiers),m.ListingReport.status=="open").values(status="removed",reviewed_at=now,reviewed_by=user_id))
        else:
            if row.removed_at is not None:raise ValueError("Cette annonce a déjà été supprimée.")
            session.execute(update(m.ListingReport).where(m.ListingReport.listing_id.in_(identifiers),m.ListingReport.status=="open").values(status="dismissed",reviewed_at=now,reviewed_by=user_id))
        return {"listing_id":row.id,"removed":row.removed_at is not None,"message":"Annonce supprimée du site. Les prochaines collectes ne la réafficheront pas." if operation=="remove" else "Signalement traité : l’annonce est conservée."}


def admin_reports(user_id):
    _admin(user_id)
    with repo.session_scope() as session:
        reports=list(session.scalars(select(m.ListingReport).order_by((m.ListingReport.status=="open").desc(),m.ListingReport.created_at.desc(),m.ListingReport.id.desc()).limit(500)))
        total=session.scalar(select(func.count()).select_from(m.ListingReport))
        pending=session.scalar(select(func.count()).select_from(m.ListingReport).where(m.ListingReport.status=="open"))
        groups={}
        for report in reports:
            listing=_canonical(session,report.listing_id)
            group=groups.setdefault(listing.id,{"listing":{"id":listing.id,"title":listing.title or "Annonce immobilière","source":listing.source,"url":listing.url,"location":listing.location,"price":listing.price,"removed":listing.removed_at is not None,"removed_at":listing.removed_at.isoformat()+"Z" if listing.removed_at else None},"reports":[],"open_count":0})
            account=session.scalar(select(m.WebAccount).where(m.WebAccount.user_id==report.user_id))
            reviewer=session.scalar(select(m.WebAccount).where(m.WebAccount.user_id==report.reviewed_by)) if report.reviewed_by else None
            group['reports'].append({"id":report.id,"reporter":account.login if account else f"Membre #{report.user_id}","note":report.note,"status":report.status,"created_at":report.created_at.isoformat()+"Z","reviewed_at":report.reviewed_at.isoformat()+"Z" if report.reviewed_at else None,"reviewed_by":reviewer.login if reviewer else None})
            group['open_count']+=int(report.status=="open")
        return {"items":list(groups.values()),"pending_count":pending,"total_count":total,"has_more":total>len(reports)}
