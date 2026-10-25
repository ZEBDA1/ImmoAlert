"""Self-hosted website: Telegram login, local data and shared bot actions."""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import hmac
import json
import logging
from pathlib import Path
import time
from uuid import uuid4

from aiohttp import web
from sqlalchemy import text

from config import settings
from database import repo, web_repo
from services import site_sync as service
from services import web_auth, admin_monitor

log = logging.getLogger(__name__)
COOKIE = "immo_session"
ADMIN_ACTIONS = {"scrapling", "source", "scan", "premium"}
ACTIONS = ADMIN_ACTIONS | {"save_filter", "delete_filter", "favorite", "alerts"}


@dataclass
class Website:
    bot: object
    directory: Path
    origin: str
    bot_username: str
    global_cache: dict | None = None
    global_at: float = 0
    account_cache: dict = field(default_factory=dict)
    snapshot_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    limits: dict = field(default_factory=lambda: defaultdict(deque))
    inflight: dict = field(default_factory=dict)
    presence: dict = field(default_factory=dict)
    proxy_nonces: dict = field(default_factory=dict)
    started_at: float = field(default_factory=time.monotonic)
    admin_cache: dict | None = None
    admin_at: float = 0

    def check_rate(self, key: str, maximum: int, seconds: int):
        now = time.monotonic()
        if len(self.limits) > 4096:
            self.limits = defaultdict(deque, {k:v for k,v in self.limits.items() if v and v[-1] > now-600})
        queue = self.limits[key]
        while queue and queue[0] < now-seconds:
            queue.popleft()
        if len(queue) >= maximum:
            raise web.HTTPTooManyRequests(text=json.dumps({"error":"Trop de demandes. Réessayez dans quelques instants."}), content_type="application/json")
        queue.append(now)

    async def snapshot(self):
        async with self.snapshot_lock:
            if self.global_cache is None or time.monotonic()-self.global_at > 8:
                self.global_cache = await asyncio.to_thread(service.global_snapshot, self.bot_username)
                self.global_at = time.monotonic()
            return dict(self.global_cache)

    async def account(self, user_id):
        cached = self.account_cache.get(user_id)
        if not cached or time.monotonic()-cached[0] > 4:
            data = await asyncio.to_thread(service.account_snapshot, user_id=user_id)
            if len(self.account_cache) > 128:
                self.account_cache.clear()
            self.account_cache[user_id] = time.monotonic(), data
            return data
        return cached[1]


STATE = web.AppKey("website", Website)


def response(data: dict, status=200):
    return web.json_response(data, status=status, dumps=lambda x:json.dumps(x, ensure_ascii=False))


def cookie(response, token: str, website: Website, request=None):
    origin=request.get("public_origin",website.origin) if request is not None else website.origin
    response.set_cookie(COOKIE, token, httponly=True, secure=origin.startswith("https://"), samesite="Lax", max_age=30*86400, path="/")


async def verify_proxy(request):
    """Only a signed gateway may supply its public origin and client address."""
    site=request.app[STATE]
    public=request.headers.get("X-Immo-Public-Origin")
    if public is None:
        return
    if not settings.web_proxy_secret or public!=settings.web_proxy_origin:
        raise web.HTTPForbidden()
    stamp=request.headers.get("X-Immo-Timestamp","")
    nonce=request.headers.get("X-Immo-Nonce","")
    client=request.headers.get("X-Immo-Client-IP","")
    import re
    if not stamp.isdigit() or abs(time.time()-int(stamp))>60 or not re.fullmatch(r"[a-f0-9]{32}",nonce) or len(client)>64:
        raise web.HTTPForbidden()
    body=await request.read()
    signed="\n".join((request.method,request.path_qs,stamp,nonce,hashlib.sha256(body).hexdigest(),public,client))
    expected=hmac.new(settings.web_proxy_secret.encode(),signed.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,request.headers.get("X-Immo-Signature","")) or nonce in site.proxy_nonces:
        raise web.HTTPForbidden()
    site.proxy_nonces={key:value for key,value in site.proxy_nonces.items() if value>time.time()-65}
    site.proxy_nonces[nonce]=time.time()
    request["public_origin"],request["client_ip"]=public,client


@web.middleware
async def boundary(request, handler):
    site = request.app[STATE]
    started=time.monotonic()
    tracked=request.path.startswith("/api/") and request.path not in {"/api/admin/live","/api/health"}
    identifier=str(uuid4())
    event=None
    try:
        await verify_proxy(request)
        if tracked:
            identity=await asyncio.to_thread(web_repo.find_session,request.cookies.get(COOKIE,""))
            request["identity"]=identity
            event={"id":identifier,"user_id":identity.get("user_id") if identity else None,"actor":identity.get("login") or "Visiteur" if identity else "Visiteur",
                "method":request.method,"path":request.path[:180],"action":None,"created_at":datetime.utcnow()}
            site.inflight[identifier]=event
            if event["user_id"]:
                site.presence[event["user_id"]]={"actor":event["actor"],"last_seen":time.time()}
        origin=request.get("public_origin",site.origin)
        if request.method not in {"GET","HEAD","OPTIONS"} and request.headers.get("Origin") != origin:
            result=response({"error":"Origine refusée."},403)
        else:
            result = await handler(request)
    except web.HTTPException as exc:
        result = web.Response(status=exc.status, body=exc.body, headers=exc.headers) if exc.content_type=="application/json" else response({"error":exc.reason},exc.status)
    except Exception:
        log.exception("Website request failed: %s",request.path)
        result=response({"error":"Le serveur n’a pas pu traiter cette demande."},500)
    if tracked and event:
        site.inflight.pop(identifier,None)
        identity=request.get("identity")
        if identity:
            event.update(user_id=identity.get("user_id"),actor=identity.get("login") or "Visiteur")
        event.update(status=result.status,duration_ms=(time.monotonic()-started)*1000,action=request.get("audit_action"))
        try:
            await asyncio.to_thread(admin_monitor.record_request,event)
        except Exception:
            log.warning("Could not persist web request metadata")
    result.headers.update({"X-Content-Type-Options":"nosniff", "X-Frame-Options":"DENY", "Referrer-Policy":"no-referrer",
        "Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; font-src 'self'; connect-src 'self'; frame-src https://www.openstreetmap.org; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"})
    result.headers["Cache-Control"] = "private, no-store" if request.path.startswith("/api/") or request.path in {"/","/login"} else "public, max-age=3600"
    return result


async def current_identity(request, *, complete=True):
    token = request.cookies.get(COOKIE, "")
    if complete:
        identity,fresh=await asyncio.to_thread(web_repo.complete_pairing, token)
    else:
        identity,fresh=await asyncio.to_thread(web_repo.find_session, token),None
    request["identity"]=identity
    return identity,fresh


async def state(request):
    site = request.app[STATE]
    identity, fresh = await current_identity(request)
    if not identity or not identity.get("user_id"):
        commands = await asyncio.to_thread(web_repo.commands, identity, set(service._scan_tasks)) if identity else []
        return response({"user":None,"account":None,"snapshot":None,"bot_username":site.bot_username,"commands":commands,"synced_at":None})
    account = await site.account(identity["user_id"])
    if not account:
        await asyncio.to_thread(web_repo.logout, fresh or request.cookies.get(COOKIE, ""))
        result = response({"error":"Compte introuvable ou désactivé. Reconnectez-vous."},401)
        result.del_cookie(COOKIE, path="/")
        return result
    snapshot = await site.snapshot()
    account=dict(account)
    account["is_admin"]=identity["is_admin"]
    if not identity["is_admin"]:
        snapshot.pop("users", None)
    commands = await asyncio.to_thread(web_repo.commands, identity, set(service._scan_tasks))
    result = response({"user":{"name":account.get("login") or account.get("username") or "Membre","linked":bool(identity.get("telegram_id"))}, "account":account,
                       "snapshot":snapshot,"synced_at":int(time.time()*1000),"commands":commands})
    if fresh:
        cookie(result, fresh, site,request)
    return result


async def action(request):
    site = request.app[STATE]
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError()
    except (ValueError, TypeError):
        return response({"error":"Requête invalide."},400)
    kind, payload = data.get("kind"), data.get("payload") or {}
    if not isinstance(kind, str) or not isinstance(payload, dict):
        return response({"error":"Paramètres invalides."},400)
    if kind == "link":
        request["audit_action"]="telegram_link"
        site.check_rate("login:"+(request.remote or "unknown"), 40, 600)
        token, pair, identifier = await asyncio.to_thread(web_repo.begin_pairing, request.cookies.get(COOKIE, ""))
        result = response({"id":identifier,"url":f"https://t.me/{site.bot_username}?start=site_{pair}"},202)
        cookie(result, token, site,request)
        return result
    if kind not in ACTIONS:
        return response({"error":"Action inconnue."},400)
    identity, _ = await current_identity(request, complete=False)
    if not identity or not identity.get("user_id"):
        return response({"error":"Connectez-vous au site."},401)
    # Read rights directly for every mutation; a cached admin flag cannot grant access.
    if kind in ADMIN_ACTIONS and not identity["is_admin"]:
        return response({"error":"Accès administrateur requis."},403)
    request["audit_action"]=kind
    site.check_rate("action:"+str(identity["user_id"]),60,60)
    try:
        identifier = await asyncio.to_thread(web_repo.create_command, identity, kind)
    except ValueError as exc:
        return response({"error":str(exc)},409)
    command = {"id":identifier,"user_id":identity["id"],"_profile_id":identity["user_id"],"kind":kind,"payload":payload,"created_at":int(time.time()*1000)}
    results = await service.process_commands(site.bot, [command], [{"user_id":identity["id"],"telegram_id":identity["telegram_id"]}])
    if results:
        item = results[0]
        await asyncio.to_thread(web_repo.finish_command, identifier, item["status"], item.get("result",{}))
        if item["status"] == "failed":
            return response({"id":identifier,"error":item.get("result",{}).get("message","Action impossible.")},400)
    site.account_cache.clear()
    site.global_at = 0
    return response({"id":identifier},202)


async def logout(request):
    request["audit_action"]="logout"
    await asyncio.to_thread(web_repo.logout, request.cookies.get(COOKIE,""))
    result = response({"message":"Déconnexion effectuée."})
    result.del_cookie(COOKIE,path="/")
    return result


async def login(request):
    site=request.app[STATE]
    request["audit_action"]="login"
    site.check_rate("password-ip:"+request.get("client_ip",request.remote or "unknown"),30,600)
    try:
        data=await request.json()
        if not isinstance(data,dict) or not isinstance(data.get("login"),str) or not isinstance(data.get("password"),str):
            raise ValueError()
    except (ValueError,TypeError):
        return response({"error":"Requête invalide."},400)
    key=hashlib.sha256(data["login"].strip().lower().encode()).hexdigest()
    site.check_rate("password-name:"+key,10,600)
    result=await asyncio.to_thread(web_auth.authenticate,data["login"],data["password"],request.cookies.get(COOKIE,""))
    if result is None:
        return response({"error":"Identifiant ou mot de passe incorrect, ou accès désactivé."},401)
    token,identity=result
    request["identity"]=identity
    site.account_cache.clear()
    result=response({"message":"Connexion établie."})
    cookie(result,token,site,request)
    return result


async def require_admin(request):
    identity,_=await current_identity(request,complete=False)
    if not identity or not identity.get("user_id"):
        raise web.HTTPUnauthorized(text=json.dumps({"error":"Connectez-vous au site."}),content_type="application/json")
    if not identity["is_admin"]:
        raise web.HTTPForbidden(text=json.dumps({"error":"Accès administrateur requis."}),content_type="application/json")
    return identity


async def admin_live(request):
    await require_admin(request)
    site=request.app[STATE]
    if site.admin_cache is None or time.monotonic()-site.admin_at>3:
        site.admin_cache=await asyncio.to_thread(admin_monitor.operations)
        site.admin_at=time.monotonic()
    history=await asyncio.to_thread(admin_monitor.recent_requests)
    inflight=[{**item,"created_at":item["created_at"].isoformat()+"Z","status":None,"duration_ms":round((datetime.utcnow()-item["created_at"]).total_seconds()*1000)} for item in list(site.inflight.values())]
    site.presence={key:value for key,value in site.presence.items() if value["last_seen"]>time.time()-300}
    return response({**site.admin_cache,"requests":history,"inflight":inflight,
        "online_users":[{"user_id":key,**value} for key,value in site.presence.items()],"uptime_seconds":int(time.monotonic()-site.started_at),"synced_at":int(time.time()*1000)})


async def admin_accounts(request):
    identity=await require_admin(request)
    site=request.app[STATE]
    site.check_rate("accounts:"+str(identity["user_id"]),20,60)
    audit_id=None
    try:
        payload=await request.json()
        if not isinstance(payload,dict):
            raise ValueError("Paramètres invalides.")
        if request.match_info.get("identifier"):
            identifier=int(request.match_info["identifier"])
            operation=payload.get("operation")
            if operation not in {"reset_password","enabled","premium","admin"}:
                raise ValueError("Opération inconnue.")
            request["audit_action"]="account_"+operation
            audit_id=await asyncio.to_thread(web_repo.create_command,identity,request["audit_action"])
            result=await asyncio.to_thread(web_auth.update_account,identity["user_id"],identifier,operation,payload.get("value"))
            message="Accès mis à jour."
        else:
            request["audit_action"]="account_create"
            target=payload.get("target_user_id")
            if target is not None and (isinstance(target,bool) or not isinstance(target,int) or target<=0):
                raise ValueError("Profil invalide.")
            audit_id=await asyncio.to_thread(web_repo.create_command,identity,request["audit_action"])
            result=await asyncio.to_thread(web_auth.create_account,identity["user_id"],payload.get("login"),premium=payload.get("premium",False),admin=payload.get("admin",False),target_user_id=target)
            message="Compte créé. Le mot de passe n’est affiché qu’une seule fois."
        await asyncio.to_thread(web_repo.finish_command,audit_id,"succeeded",{"message":message})
    except (ValueError,TypeError) as exc:
        if audit_id:
            await asyncio.to_thread(web_repo.finish_command,audit_id,"failed",{"message":str(exc)[:200]})
        return response({"error":str(exc) or "Paramètres invalides."},400)
    except PermissionError:
        if audit_id:
            await asyncio.to_thread(web_repo.finish_command,audit_id,"failed",{"message":"Accès administrateur requis."})
        return response({"error":"Accès administrateur requis."},403)
    site.account_cache.clear();site.global_at=0;site.admin_at=0
    return response({**result,"message":message},201 if request["audit_action"]=="account_create" else 200)


async def listing_detail(request):
    from services.listing_details import get_listing_details
    site=request.app[STATE]
    identity,_=await current_identity(request,complete=False)
    if not identity or not identity.get("user_id"):
        return response({"error":"Connectez-vous au site."},401)
    site.check_rate("detail:"+str(identity["user_id"]),40,60)
    try:
        identifier=int(request.match_info["identifier"])
        if not 0<identifier<=1000000000:
            raise ValueError()
    except ValueError:
        return response({"error":"Annonce introuvable."},404)
    row=await asyncio.to_thread(repo.get_listing_by_id,identifier)
    if not row or row.inventory_status=="invalid":
        return response({"error":"Annonce introuvable."},404)
    enriching=False
    try:
        row=await asyncio.wait_for(get_listing_details(row.id),timeout=20)
    except asyncio.TimeoutError:
        enriching=True
        row=await asyncio.to_thread(repo.get_listing_by_id,row.id)
    publications=await asyncio.to_thread(repo.listing_publications,row.id)
    return response({"listing":service._listing(row,full=True,publications=publications),"enriching":enriching})


async def health(request):
    try:
        def check():
            with repo.session_scope() as session:
                session.execute(text("SELECT 1"))
        await asyncio.to_thread(check)
        return response({"status":"ok"})
    except Exception:
        return response({"status":"unavailable"},503)


async def static(request):
    root = request.app[STATE].directory.resolve()
    name = request.match_info.get("path", "")
    if name.startswith("api/"):
        return response({"error":"Route inconnue."},404)
    candidate = (root / ("index.html" if name in {"","login"} else name)).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file() or any(part.startswith(".") for part in Path(name).parts):
        raise web.HTTPNotFound()
    return web.FileResponse(candidate)


def create_app(bot, *, directory: Path, origin: str, bot_username: str):
    app = web.Application(middlewares=[boundary], client_max_size=8192)
    app[STATE] = Website(bot, directory, origin.rstrip("/"), bot_username)
    app.router.add_get("/api/state", state)
    app.router.add_post("/api/action", action)
    app.router.add_post("/api/logout", logout)
    app.router.add_post("/api/login", login)
    app.router.add_get("/api/admin/live",admin_live)
    app.router.add_post("/api/admin/accounts",admin_accounts)
    app.router.add_post("/api/admin/accounts/{identifier}",admin_accounts)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/listings/{identifier}", listing_detail)
    app.router.add_get("/{path:.*}", static)
    return app


async def start_web_server(bot):
    if not settings.web_enabled:
        return None
    directory = Path(settings.web_dist_path)
    if not (directory/"index.html").is_file() or not settings.web_site_url:
        raise RuntimeError("Build frontend or WEB_SITE_URL missing for self-hosted website")
    info = await bot.get_me()
    runner = web.AppRunner(create_app(bot, directory=directory, origin=settings.web_site_url, bot_username=info.username), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, settings.web_bind_host, settings.web_port).start()
    log.info("Self-hosted website listening on port %s",settings.web_port)
    return runner
