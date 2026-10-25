"""Capture the real UI using an offline, disposable demonstration database.

Run after building web/. Requires Playwright and Chromium (included in Docker).
The application .env is deliberately not loaded, and no scheduler is started.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit


def capture(frontend: Path, output: Path, browser_path: str):
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    output.mkdir(parents=True,exist_ok=True)
    if not (frontend/"index.html").is_file():
        raise SystemExit("Build web/ first: npm --prefix web ci && npm --prefix web run build")
    with tempfile.TemporaryDirectory(prefix="immoalert-docs-") as temporary:
        os.environ.update(BOT_TOKEN="123456:documentation-only",DATABASE_URL="sqlite:///"+str(Path(temporary)/"demo.db"),
            ADMIN_TELEGRAM_IDS="",ENABLE_GEOCODING="false",FIRECRAWL_ENABLED="false",FIRECRAWL_API_KEY="",FIRECRAWL_API_KEYS="",
            FLASHPROXY_ENABLED="false",FLASHPROXY_API_KEY="",SCRAPLING_ENABLED="false",WEB_PROXY_ORIGIN="",WEB_PROXY_SECRET="",AI_SCAM_ENABLED="false")
        with patch("dotenv.load_dotenv",return_value=False):
            from aiohttp import web
            from database import repo,models as m
            from database.session import init_db
            from services import web_auth
            from services.web_api import create_app,STATE
            from services.scheduler import _source_inflight
        from playwright.sync_api import sync_playwright
        init_db()
        admin=web_auth.create_account(None,"demo_admin",admin=True,bootstrap=True)
        member=web_auth.create_account(admin["account"]["user_id"],"camille_demo",premium=True)
        now=datetime.utcnow()
        examples=[("Appartement lumineux avec balcon",850,45,2,"Rouen (76000)","pap"),
            ("Maison avec jardin au calme",1150,82,4,"Rouen (76100)","seloger"),
            ("Studio à deux pas du centre",520,24,1,"Rouen (76000)","leboncoin"),
            ("Duplex avec un espace bureau",980,61,3,"Rouen (76000)","paruvendu"),
            ("Deux pièces dans une petite résidence",740,39,2,"Rouen (76100)","rentola"),
            ("Appartement familial avec terrasse",1280,88,4,"Rouen (76000)","ouestfranceimmo")]
        ids=[]
        with repo.session_scope() as session:
            for index,(title,price,surface,rooms,location,source) in enumerate(examples):
                row=m.Listing(source=source,external_id=f"demo-{index}",url=f"https://example.test/annonce-fictive/{index}",
                    title=title,price=price,surface_m2=surface,rooms=rooms,location=location,
                    description="ANNONCE FICTIVE POUR LA DOCUMENTATION.\n\nUn séjour lumineux, une cuisine aménagée et un balcon donnent sur une cour arborée. Les commerces et les transports se trouvent à proximité.\n\nCette fiche illustre les informations accessibles sur le site : description, caractéristiques, galerie et publications regroupées. Elle ne correspond à aucun bien proposé à la location.",
                    images=json.dumps([f"https://demo.immoalert.invalid/interieur-{index}-{photo}.svg" for photo in range(3)]),
                    price_per_m2=round(price/surface,2),charges_included=True,first_seen_at=now-timedelta(hours=index+1),
                    last_seen_at=now-timedelta(minutes=index*7+1),published_at=now-timedelta(hours=index+2),inventory_status="active",
                    normalized_city="Rouen",normalized_postal_code="76100" if "76100" in location else "76000",
                    details=json.dumps({"Type de bien":"Appartement","Étage":"2","DPE":"C","Meublé":"Oui","Balcon":"Oui","Disponibilité":"À confirmer auprès de l’annonceur"},ensure_ascii=False),
                    details_checked_at=now,details_status="complete",latitude=49.44,longitude=1.09,geo_precision="city")
                session.add(row);session.flush();ids.append(row.id)
                session.add(m.ListingPublication(listing_id=row.id,source=source,external_id=row.external_id,url=row.url,last_seen_at=now))
            session.add(m.ListingPublication(listing_id=ids[0],source="seloger",external_id="demo-secondary",url="https://example.test/annonce-fictive/autre-publication",last_seen_at=now))
            for minutes,actor,method,path,action,status in [(1,"camille_demo","GET","/api/listings/1",None,200),(2,"alex_demo","POST","/api/action","save_filter",202),(3,"camille_demo","POST","/api/action","favorite",202),(4,"demo_admin","POST","/api/admin/accounts","account_create",201),(5,"Visiteur","POST","/api/login","login",401)]:
                from uuid import uuid4
                session.add(m.WebRequest(id=str(uuid4()),user_id=member["account"]["user_id"],actor=actor,method=method,path=path,action=action,status=status,duration_ms=80+minutes*31,created_at=now-timedelta(minutes=minutes)))
        repo.add_favorite(member["account"]["user_id"],ids[0])
        repo.create_or_update_filter(member["account"]["user_id"],name="Rouen · location",city="Rouen",postal_code="76000",price_max=1000,surface_min=30)
        statuses={"paruvendu":"success","entreparticuliers":"partial","leboncoin":"success","seloger":"success","pap":"success","logicimmo":"failed","rentola":"success","ouestfranceimmo":"success"}
        for index,(source,status) in enumerate(statuses.items()):
            repo.save_source_run(cycle_id="demonstration-"+source,source=source,status=status,started_at=now-timedelta(minutes=4),finished_at=now-timedelta(minutes=3),
                duration_seconds=7+index,locations_count=3,raw_count=24+index*3,normalized_count=20+index*2,dropped_count=4,unique_count=14+index,new_count=2+index%3,
                error_count=1 if status in {"failed","partial"} else 0,last_error="Exemple de diagnostic : accès refusé par le portail (403)." if status=="failed" else "Exemple de diagnostic : une zone a dépassé le délai de collecte." if status=="partial" else None)
        _source_inflight["docs-demo"]={"id":"docs-demo","source":"pap","started_at":(now-timedelta(seconds=12)).isoformat()+"Z","locations_count":3,"completed":1}
        ready=threading.Event();server={}
        async def run():
            app=create_app(AsyncMock(),directory=frontend,origin="http://127.0.0.1",bot_username="immoalert_demo_bot")
            runner=web.AppRunner(app);await runner.setup();listener=web.TCPSite(runner,"127.0.0.1",0);await listener.start()
            port=listener._server.sockets[0].getsockname()[1]
            app[STATE].origin=f"http://127.0.0.1:{port}"
            server.update(url=app[STATE].origin,loop=asyncio.get_running_loop(),stop=asyncio.Event());ready.set()
            await server["stop"].wait();await runner.cleanup()
        thread=threading.Thread(target=lambda:asyncio.run(run()),daemon=True);thread.start()
        if not ready.wait(15):
            raise RuntimeError("Preview did not start")
        scene=(root/"docs/assets/demo-property.svg").read_text()
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=browser_path,headless=True,args=["--no-sandbox"])
                context=browser.new_context(viewport={"width":1440,"height":1050},device_scale_factor=1)
                page=context.new_page();errors=[]
                page.on("pageerror",lambda error:errors.append(str(error)))
                def route(request):
                    host=urlsplit(request.request.url).hostname
                    if host=="127.0.0.1":
                        request.continue_()
                    elif host=="demo.immoalert.invalid":
                        request.fulfill(content_type="image/svg+xml",body=scene)
                    else:
                        request.fulfill(content_type="text/html",body="<!doctype html><p>Carte de démonstration — position fictive</p>")
                page.route("**/*",route)
                page.goto(server["url"])
                page.get_by_role("button",name="Se connecter",exact=True).last.click()
                page.get_by_role("dialog").screenshot(path=str(output/"login-demo.png"))
                def login(credentials):
                    page.get_by_label("Identifiant",exact=True).fill(credentials["login"])
                    page.get_by_label("Mot de passe",exact=True).fill(credentials["password"])
                    page.get_by_role("button",name="Se connecter",exact=True).last.click()
                    page.locator(".hero").wait_for()
                login(member["credentials"])
                page.locator(".property-card").first.wait_for()
                page.evaluate("document.fonts.ready");page.locator(".property-card img").first.wait_for()
                page.locator(".stats-strip").evaluate("el=>window.scrollTo(0,el.getBoundingClientRect().top+window.scrollY-24)")
                page.screenshot(path=str(output/"website-demo.png"))
                page.get_by_role("button",name="Détails de Appartement lumineux avec balcon",exact=True).click()
                page.get_by_role("heading",name="Appartement lumineux avec balcon",exact=True).wait_for()
                page.get_by_text("LE BIEN EN DÉTAIL.",exact=True).wait_for()
                page.locator(".detail-progress .spin").wait_for(state="hidden")
                page.locator(".gallery-thumbnails img").evaluate_all("images=>images.forEach(image=>image.loading='eager')")
                page.locator(".detail-gallery img").evaluate_all("images=>Promise.all(images.map(image=>image.decode()))")
                page.locator(".detail-drawer").evaluate("el=>el.scrollTop=0")
                page.screenshot(path=str(output/"listing-demo.png"))
                page.get_by_label("Fermer le détail",exact=True).click()
                page.set_viewport_size({"width":390,"height":960})
                page.locator(".listing-section").scroll_into_view_if_needed()
                page.screenshot(path=str(output/"mobile-demo.png"))
                assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
                page.set_viewport_size({"width":1440,"height":1050})
                page.evaluate("window.scrollTo(0,0)")
                page.get_by_role("button",name="Mon accès",exact=True).click()
                page.get_by_role("button",name="Me déconnecter",exact=True).click()
                page.get_by_role("button",name="Se connecter",exact=True).last.click();login(admin["credentials"])
                page.get_by_role("button",name="Console",exact=True).click()
                page.locator(".source-monitor-card").first.wait_for()
                page.evaluate("window.scrollTo(0,460)")
                page.screenshot(path=str(output/"admin-demo.png"))
                page.get_by_role("button",name="Trafic en direct",exact=True).click()
                page.locator(".request-table tbody tr").first.wait_for()
                page.evaluate("window.scrollTo(0,330)")
                page.screenshot(path=str(output/"traffic-demo.png"))
                browser.close()
                if errors:
                    raise RuntimeError("Browser errors: "+"; ".join(errors))
            print("Captured six UI views using fictitious profiles, listings, illustrations and metrics; no production data.")
        finally:
            server["loop"].call_soon_threadsafe(server["stop"].set);thread.join(timeout=8)


if __name__=="__main__":
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser()
    parser.add_argument("--frontend",type=Path,default=root/"web/dist")
    parser.add_argument("--output",type=Path,default=root/"docs/assets")
    parser.add_argument("--browser",default=os.environ.get("SCRAPLING_BROWSER_PATH","/usr/bin/chromium"))
    args=parser.parse_args()
    capture(args.frontend.resolve(),args.output.resolve(),args.browser)
