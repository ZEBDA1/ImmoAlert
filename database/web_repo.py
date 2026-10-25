"""Local website sessions and command history, sharing the bot's SQLite DB."""
from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
import re
import secrets
from uuid import uuid4

from sqlalchemy import select, delete, update
from database import models as m, repo
from config import settings


def session_id(token: str) -> str | None:
    return sha256(token.encode()).hexdigest() if re.fullmatch(r"[a-f0-9]{64}", token or "") else None


def find_session(token: str) -> dict | None:
    key = session_id(token)
    if not key:
        return None
    with repo.session_scope() as session:
        row = session.get(m.WebSession, key)
        if not row or row.expires_at <= datetime.utcnow():
            return None
        from services.web_auth import identity
        return identity(session,row)


def begin_pairing(token: str) -> tuple[str, str, str]:
    now = datetime.utcnow()
    key = session_id(token)
    with repo.session_scope() as session:
        session.execute(delete(m.WebSession).where(m.WebSession.expires_at < now))
        session.execute(delete(m.WebCommand).where(m.WebCommand.created_at < now-timedelta(days=7)))
        row = session.get(m.WebSession, key) if key else None
        if not row or row.expires_at <= now:
            token = secrets.token_hex(32)
            key = session_id(token)
            row = m.WebSession(id=key, expires_at=now+timedelta(minutes=15))
            session.add(row)
        if row.pair_token:
            session.execute(delete(m.RuntimeSetting).where(m.RuntimeSetting.key == "site_pair:"+row.pair_token))
            previous = session.get(m.WebCommand, row.pair_command_id)
            if previous:
                previous.status, previous.result = "failed", json.dumps({"message":"Connexion remplacée par une nouvelle demande."})
        if row.telegram_id is None and row.user_id is None:
            row.expires_at = now+timedelta(minutes=15)
        pair, command_id = secrets.token_hex(16), str(uuid4())
        row.pair_token, row.pair_command_id = pair, command_id
        row.pair_expires_at = now+timedelta(minutes=10)
        session.add(m.WebCommand(id=command_id, session_id=key, telegram_id=row.telegram_id, profile_user_id=row.user_id, kind="link", status="pending", result="{}"))
        return token, pair, command_id


def complete_pairing(token: str) -> tuple[dict | None, str | None]:
    key = session_id(token)
    if not key:
        return None, None
    now = datetime.utcnow()
    with repo.session_scope() as session:
        row = session.get(m.WebSession, key)
        if not row or row.expires_at <= now:
            return None, None
        from services import web_auth
        identity = web_auth.identity(session,row)
        if identity is None:
            return None,None
        if not row.pair_token:
            return identity, None
        command = session.get(m.WebCommand, row.pair_command_id)
        approval = session.get(m.RuntimeSetting, "site_pair:"+row.pair_token)
        if not row.pair_expires_at or row.pair_expires_at <= now:
            if command:
                command.status, command.result = "failed", json.dumps({"message":"Connexion expirée. Réessayez."})
            row.pair_token, row.pair_command_id, row.pair_expires_at = None, None, None
            if approval:
                session.delete(approval)
            return identity, None
        if not approval:
            return identity, None
        try:
            approved = json.loads(approval.value)
            telegram_id = int(approved["telegram_id"])
            age = now.timestamp()-float(approved["approved_at"])
        except (ValueError, TypeError, KeyError):
            return identity, None
        if not 0 <= age <= 600 or not session.scalar(select(m.User.id).where(m.User.telegram_id == telegram_id)):
            return identity, None
        telegram_user=session.scalar(select(m.User).where(m.User.telegram_id==telegram_id))
        attached=session.scalar(select(m.WebAccount).where(m.WebAccount.user_id==telegram_user.id))
        local=session.get(m.WebAccount,row.web_account_id) if row.web_account_id else None
        failure=None
        if local:
            try:
                telegram_user=web_auth.link_profile(session,local,telegram_user,keep_session_id=row.id)
            except ValueError as exc:
                failure=str(exc)
        elif not (telegram_id in settings.admin_telegram_ids or attached and attached.enabled):
            failure="Un administrateur doit d’abord créer votre accès au site."
        if failure:
            if command:
                command.status,command.result="failed",json.dumps({"message":failure},ensure_ascii=False)
            session.delete(approval)
            row.pair_token,row.pair_command_id,row.pair_expires_at=None,None,None
            return identity,None
        # Rotate the anonymous cookie on login, then consume the approval.
        fresh_token = secrets.token_hex(32)
        fresh_id = session_id(fresh_token)
        fresh=m.WebSession(id=fresh_id,telegram_id=telegram_id,user_id=telegram_user.id,web_account_id=local.id if local else None,expires_at=now+timedelta(days=30))
        session.add(fresh)
        if command:
            command.status, command.telegram_id,command.profile_user_id = "succeeded", telegram_id,telegram_user.id
            command.result = json.dumps({"message":"Connexion Telegram établie."})
        session.delete(approval)
        session.delete(row)
        session.flush()
        return web_auth.identity(session,fresh), fresh_token


def logout(token: str) -> None:
    key = session_id(token)
    if not key:
        return
    with repo.session_scope() as session:
        row = session.get(m.WebSession, key)
        if row:
            if row.pair_token:
                session.execute(delete(m.RuntimeSetting).where(m.RuntimeSetting.key == "site_pair:"+row.pair_token))
            session.delete(row)


def create_command(identity: dict, kind: str) -> str:
    now = datetime.utcnow()
    with repo.session_scope() as session:
        owner=m.WebCommand.profile_user_id==identity["user_id"] if identity.get("user_id") else m.WebCommand.telegram_id==identity.get("telegram_id")
        waiting = list(session.scalars(select(m.WebCommand).where(owner, m.WebCommand.status.in_(["pending","running"]), m.WebCommand.created_at >= now-timedelta(minutes=15))))
        if len(waiting) >= 4:
            raise ValueError("Des actions sont déjà en cours. Patientez quelques secondes.")
        if kind == "scan" and session.scalar(select(m.WebCommand.id).where(m.WebCommand.kind=="scan", m.WebCommand.status.in_(["pending","running"]), m.WebCommand.created_at >= now-timedelta(minutes=15))):
            raise ValueError("Une collecte est déjà en cours.")
        identifier = str(uuid4())
        session.add(m.WebCommand(id=identifier, session_id=identity["id"], telegram_id=identity.get("telegram_id"),profile_user_id=identity.get("user_id"), kind=kind, status="pending"))
        return identifier


def finish_command(identifier: str, status: str, result: dict) -> None:
    with repo.session_scope() as session:
        row = session.get(m.WebCommand, identifier)
        if row:
            row.status, row.result = status, json.dumps({"message":result.get("message", "")}, ensure_ascii=False)


def commands(identity: dict, running: set[str]) -> list[dict]:
    with repo.session_scope() as session:
        from sqlalchemy import or_
        condition = or_(m.WebCommand.profile_user_id==identity["user_id"],m.WebCommand.telegram_id==identity["telegram_id"] if identity.get("telegram_id") else False) if identity.get("user_id") else m.WebCommand.telegram_id == identity["telegram_id"] if identity.get("telegram_id") else m.WebCommand.session_id == identity["id"]
        rows = list(session.scalars(select(m.WebCommand).where(condition).order_by(m.WebCommand.created_at.desc()).limit(12)))
        for row in rows:
            if row.status == "running":
                saved = session.get(m.RuntimeSetting, "site_command:"+row.id)
                result = json.loads(saved.value) if saved else None
                if result and result.get("status") in {"succeeded","failed"}:
                    row.status, row.result = result["status"], json.dumps(result.get("result", {}), ensure_ascii=False)
                elif row.id not in running:
                    row.status, row.result = "failed", json.dumps({"message":"Collecte interrompue par un redémarrage."})
            elif row.status == "pending" and row.kind != "link" and row.created_at < datetime.utcnow()-timedelta(minutes=1):
                row.status, row.result = "failed", json.dumps({"message":"Action interrompue. Réessayez."})
        return [{"id":r.id,"kind":r.kind,"status":r.status,"result":r.result,"created_at":int(r.created_at.timestamp()*1000)} for r in rows]
