"""Administrator-provisioned logins sharing the bot's existing user profiles."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import hmac
import re
import secrets
import threading

from sqlalchemy import select, delete, or_
from sqlalchemy.exc import IntegrityError

from config import settings
from database import models as m, repo

_lock=threading.RLock()
_LOGIN=re.compile(r"[a-z0-9][a-z0-9_.-]{2,39}\Z")


def hash_password(password: str, *, salt=None) -> str:
    salt=salt or secrets.token_hex(16)
    derived=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,maxmem=64*1024*1024)
    return f"scrypt$16384$8$1${salt}${derived.hex()}"


def verify_password(password, encoded):
    try:
        _,n,r,p,salt,expected=encoded.split("$")
        if (n,r,p)!=("16384","8","1"):
            return False
        return hmac.compare_digest(hash_password(password,salt=salt).rsplit("$",1)[1],expected)
    except (ValueError,TypeError):
        return False


def _admin(session,user):
    if not user:
        return False
    account=session.scalar(select(m.WebAccount).where(m.WebAccount.user_id==user.id))
    if account and not account.enabled:
        return False
    return user.telegram_id in settings.admin_telegram_ids or bool(account and account.is_admin)


def is_admin(user_id):
    with repo.session_scope() as session:
        return _admin(session,session.get(m.User,user_id))


def identity(session,row):
    account=session.get(m.WebAccount,row.web_account_id) if row.web_account_id else None
    user=session.get(m.User,account.user_id if account else row.user_id) if account or row.user_id else session.scalar(select(m.User).where(m.User.telegram_id==row.telegram_id)) if row.telegram_id else None
    if not user:
        return {"id":row.id,"telegram_id":None,"user_id":None,"web_account_id":None,"is_admin":False,"login":None}
    attached=account or session.scalar(select(m.WebAccount).where(m.WebAccount.user_id==user.id))
    if attached and not attached.enabled:
        return None
    return {"id":row.id,"telegram_id":user.telegram_id,"user_id":user.id,"web_account_id":account.id if account else None,
        "is_admin":_admin(session,user),"login":attached.login if attached else user.username or "Telegram"}


def _revoke(session,user,keep_session_id=None):
    session.execute(delete(m.WebSession).where(or_(m.WebSession.user_id==user.id,
        m.WebSession.telegram_id==user.telegram_id if user.telegram_id else False,
        m.WebSession.web_account_id.in_(select(m.WebAccount.id).where(m.WebAccount.user_id==user.id))),m.WebSession.id!=keep_session_id if keep_session_id else True))


def account_data(account,user):
    return {"id":account.id,"user_id":user.id,"login":account.login,"username":user.username,
        "telegram_id":user.telegram_id,"is_admin":account.is_admin or user.telegram_id in settings.admin_telegram_ids,
        "enabled":account.enabled,"is_premium":user.is_premium,
        "created_at":account.created_at.isoformat()+"Z","last_login_at":account.last_login_at.isoformat()+"Z" if account.last_login_at else None}


def list_accounts():
    with repo.session_scope() as session:
        return [account_data(account,user) for account,user in session.execute(select(m.WebAccount,m.User).join(m.User,m.User.id==m.WebAccount.user_id).order_by(m.WebAccount.created_at.desc()))]


def create_account(actor_user_id,login,*,premium=False,admin=False,target_user_id=None,bootstrap=False):
    login=str(login or "").strip().lower()
    if not _LOGIN.fullmatch(login):
        raise ValueError("L’identifiant doit contenir 3 à 40 caractères : lettres, chiffres, point, tiret ou soulignement.")
    if not isinstance(premium,bool) or not isinstance(admin,bool):
        raise ValueError("Les options Premium et administrateur doivent être booléennes.")
    password=secrets.token_urlsafe(18)
    encoded=hash_password(password)
    try:
        with _lock,repo.session_scope() as session:
            if not bootstrap and not _admin(session,session.get(m.User,actor_user_id)):
                raise PermissionError("Accès administrateur requis.")
            if session.scalar(select(m.WebAccount.id).where(m.WebAccount.login==login)):
                raise ValueError("Cet identifiant existe déjà.")
            user=session.get(m.User,target_user_id) if target_user_id else None
            if target_user_id and not user:
                raise ValueError("Profil introuvable.")
            if user and session.scalar(select(m.WebAccount.id).where(m.WebAccount.user_id==user.id)):
                raise ValueError("Ce profil possède déjà un accès par mot de passe.")
            if user is None:
                user=m.User(username=login,telegram_id=None,is_premium=premium)
                session.add(user);session.flush()
            else:
                user.is_premium=premium
                user.premium_until=None
            account=m.WebAccount(user_id=user.id,login=login,password_hash=encoded,is_admin=admin,enabled=True,created_by=actor_user_id)
            session.add(account);session.flush()
            return {"account":account_data(account,user),"credentials":{"login":login,"password":password}}
    except IntegrityError:
        raise ValueError("Cet identifiant ou ce profil possède déjà un accès.") from None


def authenticate(login,password,previous_token=""):
    from database.web_repo import session_id
    login=str(login or "").strip().lower()
    if not isinstance(password,str) or not 1<=len(password)<=256:
        return None
    with repo.session_scope() as session:
        account=session.scalar(select(m.WebAccount).where(m.WebAccount.login==login))
        encoded=account.password_hash if account else hash_password("dummy-password",salt="00"*16)
        valid=verify_password(password,encoded)
        if not account or not valid or not account.enabled:
            return None
        user=session.get(m.User,account.user_id)
        if not user:
            return None
        old=session.get(m.WebSession,session_id(previous_token)) if session_id(previous_token) else None
        if old:
            session.delete(old)
        token=secrets.token_hex(32)
        row=m.WebSession(id=session_id(token),user_id=user.id,telegram_id=user.telegram_id,web_account_id=account.id,expires_at=datetime.utcnow()+timedelta(days=30))
        session.add(row);account.last_login_at=datetime.utcnow();session.flush()
        return token,identity(session,row)


def update_account(actor_user_id,identifier,operation,value=None):
    password=secrets.token_urlsafe(18) if operation=="reset_password" else None
    encoded=hash_password(password) if password else None
    with _lock,repo.session_scope() as session:
        actor=session.get(m.User,actor_user_id)
        if not _admin(session,actor):
            raise PermissionError("Accès administrateur requis.")
        account=session.get(m.WebAccount,identifier)
        if not account:
            raise ValueError("Compte introuvable.")
        user=session.get(m.User,account.user_id)
        if operation=="reset_password":
            account.password_hash=encoded
            _revoke(session,user)
        elif operation in {"enabled","premium","admin"}:
            if not isinstance(value,bool):
                raise ValueError("Une valeur activée/désactivée est requise.")
            if user.id==actor_user_id and operation in {"enabled","admin"} and not value:
                raise ValueError("Vous ne pouvez pas retirer votre propre accès administrateur.")
            if operation=="premium":
                user.is_premium=value;user.premium_until=None
            else:
                setattr(account,"is_admin" if operation=="admin" else "enabled",value)
                _revoke(session,user)
        else:
            raise ValueError("Opération inconnue.")
        result={"account":account_data(account,user)}
        if password:
            result["credentials"]={"login":account.login,"password":password}
        return result


def link_profile(session,account,telegram_user,keep_session_id=None):
    """Keep the verified Telegram profile and merge the invited web profile."""
    other=session.scalar(select(m.WebAccount).where(m.WebAccount.user_id==telegram_user.id))
    if other and other.id!=account.id:
        raise ValueError("Ce compte Telegram est déjà relié à un autre identifiant.")
    source=session.get(m.User,account.user_id)
    if source.id==telegram_user.id:
        return telegram_user
    if source.telegram_id is not None:
        raise ValueError("Cet identifiant est déjà relié à un autre compte Telegram.")
    target=telegram_user
    target.is_premium=bool(source.is_premium or target.is_premium)
    if source.is_premium and source.premium_until is None:
        target.premium_until=None
    names={name for name in session.scalars(select(m.Filter.name).where(m.Filter.user_id==target.id))}
    for row in session.scalars(select(m.Filter).where(m.Filter.user_id==source.id)):
        if row.name in names:
            row.name=(row.name[:42]+f"-site-{row.id}")[:64]
            if not target.is_premium:
                row.active=False
        names.add(row.name);row.user_id=target.id
    for model in (m.Favorite,m.SentAlert,m.PendingAlert,m.ListingRead,m.ListingReport):
        for row in session.scalars(select(model).where(model.user_id==source.id)):
            existing=session.scalar(select(model).where(model.user_id==target.id,model.listing_id==row.listing_id))
            if existing:
                if model is m.ListingReport:
                    if row.status=="open" and existing.status!="removed":
                        existing.status="open";existing.reviewed_at=None;existing.reviewed_by=None
                    existing.note="\n".join(v for v in [existing.note,row.note] if v)[:500]
                if model is m.ListingRead:
                    existing.read_at=min(existing.read_at,row.read_at)
                if model is m.PendingAlert and row.status=="sent":
                    existing.status="sent"
                session.delete(row)
            else:
                row.user_id=target.id
            session.flush()
    from services.property_tracking import merge_member_profile
    merge_member_profile(session,source.id,target.id)
    _revoke(session,source,keep_session_id)
    account.user_id=target.id
    source.active=False
    session.flush()
    return target
