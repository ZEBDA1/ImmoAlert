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
from types import SimpleNamespace
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
            from services import web_auth, provider_credentials, scheduler
            from scraper.base import ScrapedListing
            from services.web_api import create_app,STATE
            from services.scheduler import _source_inflight
        from playwright.sync_api import sync_playwright, expect
        provider_credentials._MASTER_PATH=Path(temporary)/"provider-credentials.key"
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
                    images=json.dumps([f"https://demo.immoalert.invalid/interieur-{index}-{photo}.webp" for photo in range(3)]),
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
        scenes=[(root/"web/public/images/studio-interior.webp").read_bytes(),(root/"docs/assets/demo-studio-a.webp").read_bytes(),(root/"docs/assets/demo-studio-b.webp").read_bytes()]
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=browser_path,headless=True,args=["--no-sandbox"])
                context=browser.new_context(viewport={"width":1440,"height":1050},device_scale_factor=1)
                page=context.new_page();page.emulate_media(reduced_motion="reduce");errors=[]
                page.on("pageerror",lambda error:errors.append(str(error)))
                def route(request):
                    host=urlsplit(request.request.url).hostname
                    if host=="127.0.0.1":
                        request.continue_()
                    elif host=="demo.immoalert.invalid":
                        request.fulfill(content_type="image/webp",body=scenes[int(request.request.url.rsplit("/",1)[-1].split("-")[1])%len(scenes)])
                    else:
                        request.fulfill(content_type="text/html",body="<!doctype html><p>Carte de démonstration — position fictive</p>")
                page.route("**/*",route)
                page.goto(server["url"])
                page.locator(".landing").wait_for();page.evaluate("document.fonts.ready")
                expect(page.locator("html")).to_have_attribute("data-theme","dark")
                page.screenshot(path=str(output/"landing-demo.png"))
                page.get_by_role("button",name="Passer en mode clair",exact=True).click()
                page.reload();page.locator(".landing-hero").wait_for()
                expect(page.locator("html")).to_have_attribute("data-theme","light")
                page.screenshot(path=str(output/"landing-light-demo.png"))
                page.get_by_role("button",name="Passer en mode sombre",exact=True).click()
                page.get_by_role("button",name="Se connecter",exact=True).last.click()
                page.get_by_role("dialog").screenshot(path=str(output/"login-demo.png"))
                def login(credentials):
                    page.get_by_label("Identifiant",exact=True).fill(credentials["login"])
                    page.get_by_label("Mot de passe",exact=True).fill(credentials["password"])
                    page.get_by_role("button",name="Se connecter",exact=True).last.click()
                    page.locator(".welcome-heading").wait_for()
                login(member["credentials"])
                page.locator(".property-card").first.wait_for()
                # Exercise the two-step form without creating a live search.
                page.locator(".welcome-heading").get_by_role("button",name="Créer une recherche",exact=True).click()
                dialog=page.get_by_role("dialog")
                dialog.get_by_role("button",name="Continuer",exact=True).click()
                expect(dialog.get_by_role("alert")).to_contain_text("ville ou un code postal")
                dialog.get_by_label("Ville",exact=True).fill("Lyon")
                dialog.get_by_label("Code postal",exact=True).fill("69001")
                dialog.get_by_label("Budget maximum (€ / mois)",exact=True).fill("1250")
                dialog.get_by_role("button",name="Continuer",exact=True).click()
                dialog.get_by_label("Surface minimum (m²)",exact=True).fill("35")
                dialog.get_by_role("button",name="Retour",exact=True).click()
                expect(dialog.get_by_label("Ville",exact=True)).to_have_value("Lyon")
                expect(dialog.get_by_label("Budget maximum (€ / mois)",exact=True)).to_have_value("1250")
                dialog.get_by_role("button",name="Continuer",exact=True).click()
                expect(dialog.get_by_label("Surface minimum (m²)",exact=True)).to_have_value("35")
                dialog.screenshot(path=str(output/"search-form-demo.png"))
                page.keyboard.press("Escape");dialog.wait_for(state="hidden")
                # All retained viewports must fit, including the narrowest phone.
                for width in (320,390,768,1024,1440):
                    page.set_viewport_size({"width":width,"height":960})
                    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth"),width
                page.set_viewport_size({"width":1440,"height":1050})
                first=page.locator(".favorite-button").first
                first.click();expect(first).to_have_attribute("aria-pressed","false")
                first.click();expect(first).to_have_attribute("aria-pressed","true")
                page.get_by_role("button",name="Affichage en liste",exact=True).click()
                expect(page.get_by_role("button",name="Affichage en liste",exact=True)).to_have_attribute("aria-pressed","true")
                page.get_by_role("button",name="Affichage en grille",exact=True).click()
                page.evaluate("document.fonts.ready");page.locator(".property-card img").first.wait_for()
                if page.get_by_role("button",name="Fermer la notification",exact=True).count():
                    page.get_by_role("button",name="Fermer la notification",exact=True).click()
                page.evaluate("window.scrollTo(0,0)")
                page.screenshot(path=str(output/"website-demo.png"))
                page.get_by_role("button",name="Passer en mode clair",exact=True).click()
                page.screenshot(path=str(output/"website-light-demo.png"))
                page.get_by_role("button",name="Passer en mode sombre",exact=True).click()
                page.get_by_role("button",name="Détails de Appartement lumineux avec balcon",exact=True).click()
                page.get_by_role("heading",name="Appartement lumineux avec balcon",exact=True).wait_for()
                page.locator(".detail-progress .spin").wait_for(state="hidden")
                page.locator(".gallery-thumbnails img").evaluate_all("images=>images.forEach(image=>image.loading='eager')")
                page.locator(".detail-gallery img").evaluate_all("images=>Promise.all(images.map(image=>image.decode()))")
                page.get_by_role("button",name="Photo suivante",exact=True).click()
                expect(page.locator(".gallery-counter")).to_have_text("2 / 3")
                page.get_by_role("button",name="Afficher la photo 1",exact=True).click()
                page.get_by_role("button",name="Agrandir la galerie",exact=True).click()
                page.keyboard.press("Escape")
                expect(page.locator(".detail-gallery")).not_to_have_class("detail-gallery expanded")
                expect(page.get_by_role("dialog")).to_be_visible()
                page.locator(".detail-drawer").evaluate("el=>el.scrollTop=0")
                page.screenshot(path=str(output/"listing-demo.png"))
                page.get_by_label("Fermer le détail",exact=True).click()
                page.locator('.property-card').filter(has=page.get_by_role("button",name="Détails de Appartement lumineux avec balcon",exact=True)).get_by_text("Déjà lue",exact=True).wait_for()
                indexed_text=page.locator('.property-card').filter(has=page.get_by_role("button",name="Détails de Appartement lumineux avec balcon",exact=True)).locator('.property-footer small').inner_text()
                assert "1 h" in indexed_text
                page.reload();page.locator('.read-badge').first.wait_for()
                assert "1 h" in page.locator('.property-card').filter(has=page.get_by_role("button",name="Détails de Appartement lumineux avec balcon",exact=True)).locator('.property-footer small').inner_text()

                page.emulate_media(reduced_motion="reduce")
                page.set_viewport_size({"width":390,"height":960})
                page.locator(".listing-section").scroll_into_view_if_needed()
                page.screenshot(path=str(output/"mobile-demo.png"))
                assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
                page.set_viewport_size({"width":1440,"height":1050})
                page.evaluate("window.scrollTo(0,0)")
                page.get_by_role("button",name="Mes recherches",exact=True).first.click()
                page.locator(".search-card-actions .icon-button.danger").first.click()
                expect(page.get_by_role("heading",name="Supprimer cette recherche ?",exact=True)).to_be_visible()
                page.get_by_role("button",name="Conserver",exact=True).click()
                page.get_by_role("dialog").wait_for(state="hidden")
                assert len(repo.get_user_filters(member["account"]["user_id"]))==1
                page.get_by_role("button",name="Mon compte",exact=True).first.click()
                page.get_by_role("button",name="Me déconnecter",exact=True).click()
                page.get_by_role("button",name="Se connecter",exact=True).last.click();login(admin["credentials"])
                page.locator(".admin-menu summary").click()
                page.get_by_role("button",name="Sources & activité",exact=True).click()
                page.locator(".ops-source-card").first.wait_for()
                page.get_by_role("tab",name="Santé des sources",exact=True).focus()
                page.keyboard.press("ArrowRight")
                expect(page.get_by_role("tab",name="Clés et quotas",exact=True)).to_have_attribute("aria-selected","true")
                page.keyboard.press("ArrowLeft")
                expect(page.get_by_role("tab",name="Santé des sources",exact=True)).to_have_attribute("aria-selected","true")
                page.evaluate("window.scrollTo(0,0)")
                page.screenshot(path=str(output/"admin-demo.png"))
                page.get_by_role("tab",name="Activité web",exact=True).click()
                page.locator(".ops-workspace table tbody tr").first.wait_for()
                page.evaluate("window.scrollTo(0,0)")
                page.screenshot(path=str(output/"traffic-demo.png"))
                page.get_by_role("tab",name="Clés et quotas",exact=True).click()
                page.locator(".ops-provider").first.wait_for()
                page.get_by_role("button",name="Ajouter une clé",exact=True).first.click()
                page.get_by_label("Nom de la clé",exact=True).fill("Clé de démonstration")
                page.get_by_label("Clé API",exact=True).fill("fc-documentation-fixture-only")
                page.get_by_role("button",name="Enregistrer la clé",exact=True).click()
                page.get_by_role("dialog").wait_for(state="hidden")
                page.locator(".ops-key").first.wait_for()
                assert "fc-documentation-fixture-only" not in page.locator(".ops-key").first.inner_text()
                page.get_by_role("switch",name="Activer Firecrawl",exact=True).click()
                page.get_by_text("Fournisseur activé.",exact=True).wait_for()
                with patch.object(provider_credentials,"_fetch_quota",return_value=("ok","Solde fictif pour la documentation.",{"remaining":720,"used":280,"total":1000,"unit":"credits","reset_at":None})):
                    page.get_by_role("button",name="Vérifier le quota",exact=True).first.click()
                    page.get_by_text("720 crédits",exact=True).wait_for()
                page.get_by_role("button",name="Vérifier le quota",exact=True).first.wait_for(state="visible")
                expect(page.get_by_role("button",name="Vérifier le quota",exact=True).first).to_be_enabled()
                page.evaluate("window.scrollTo(0,0)")
                page.screenshot(path=str(output/"providers-demo.png"))
                page.emulate_media(reduced_motion="reduce")
                page.set_viewport_size({"width":390,"height":960})
                page.screenshot(path=str(output/"admin-mobile-demo.png"))
                assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
                for viewport in [{"width":320,"height":740},{"width":768,"height":1024},{"width":1024,"height":768},{"width":844,"height":390}]:
                    page.set_viewport_size(viewport)
                    for title in ["Santé des sources","Activité web","Historique","Clés et quotas"]:
                        page.get_by_role("tab",name=title,exact=True).click()
                        assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth"), (viewport,title)
                page.set_viewport_size({"width":390,"height":960})
                page.get_by_role("button",name="Modifier",exact=True).first.click()
                page.get_by_label("Nom de la clé",exact=True).fill("Clé modifiée")
                page.get_by_role("button",name="Enregistrer la clé",exact=True).click()
                page.get_by_role("dialog").wait_for(state="hidden")
                page.get_by_role("heading",name="Clé modifiée",exact=True).wait_for()
                page.get_by_role("button",name="Supprimer",exact=True).first.click()
                page.get_by_role("button",name="Supprimer la clé",exact=True).click()
                page.get_by_role("heading",name="Clé modifiée",exact=True).wait_for(state="hidden")
                # Exercise the real HTTP -> job -> inventory -> UI path using
                # an offline source response, without starting any live scraper.
                async def collect_fixture(scraper, locations):
                    started=datetime.utcnow()
                    await asyncio.sleep(5)
                    item=ScrapedListing(source="pap",external_id="browser-search-result",url="https://www.pap.fr/annonces/location-appartement-fictif-r123456789",title="Logement du scénario de recherche",price=877,surface_m2=47,rooms=3,location="Rouen 76000")
                    return scheduler.SourceCollectResult(source="pap",started_at=started,finished_at=datetime.utcnow(),listings=[item],locations_count=1,raw_count=1,normalized_count=1,unique_count=1,status="success")
                page.set_viewport_size({"width":1440,"height":1050})
                page.get_by_role("button",name="Explorer",exact=True).first.click()
                with patch.object(scheduler,"SCRAPERS",[SimpleNamespace(source="pap")]), patch.object(scheduler,"_collect_source_listings",side_effect=collect_fixture), patch("services.listing_details.resolve_new_listing",new=AsyncMock(side_effect=lambda stored:stored)):
                    page.get_by_role("button",name="Créer une recherche",exact=True).last.click()
                    dialog=page.get_by_role("dialog")
                    dialog.get_by_label("Ville",exact=True).fill("Rouen")
                    dialog.get_by_label("Code postal",exact=True).fill("76000")
                    dialog.get_by_role("button",name="Continuer",exact=True).click()
                    dialog.get_by_role("button",name="Lancer ma recherche",exact=True).click()
                    page.locator(".collection-progress.is-running").wait_for()
                    assert page.locator(".listing-skeleton").count()>0
                    page.screenshot(path=str(output/"search-progress-demo.png"))
                    page.locator(".collection-progress.is-complete").wait_for()
                    page.get_by_role("button",name="Détails de Logement du scénario de recherche",exact=True).wait_for()
                # Report and moderation use the real isolated API/database.
                page.get_by_role("button",name="Mon compte",exact=True).first.click()
                page.get_by_role("button",name="Me déconnecter",exact=True).click()
                page.get_by_role("button",name="Se connecter",exact=True).last.click();login(member["credentials"])
                for theme in ["dark","light"]:
                    page.evaluate("theme=>document.documentElement.dataset.theme=theme",theme)
                    colors=page.locator('.source-select option').first.evaluate("element=>({text:getComputedStyle(element).color,background:getComputedStyle(element).backgroundColor,scheme:getComputedStyle(document.documentElement).colorScheme})")
                    assert colors["text"]!=colors["background"] and colors["background"]!="rgba(0, 0, 0, 0)"
                    assert colors["scheme"]==theme
                    import re
                    def luminance(value):
                        channels=[float(channel)/255 for channel in re.findall(r"[\d.]+",value)[:3]]
                        linear=[channel/12.92 if channel<=.04045 else ((channel+.055)/1.055)**2.4 for channel in channels]
                        return sum(channel*weight for channel,weight in zip(linear,[.2126,.7152,.0722]))
                    light,dark=sorted([luminance(colors['text']),luminance(colors['background'])],reverse=True)
                    assert (light+.05)/(dark+.05)>=4.5
                    page.get_by_label("Choisir un portail",exact=True).click()
                    page.screenshot(path=str(output/f"select-{theme}-demo.png"))
                    page.keyboard.press("Escape")
                page.evaluate("document.documentElement.dataset.theme='dark'")
                page.get_by_role("button",name="Détails de Appartement lumineux avec balcon",exact=True).click()
                page.locator('.detail-retry').wait_for()
                assert page.get_by_role("button",name="Supprimer cette annonce du site",exact=True).count()==0
                page.get_by_role("button",name="Signaler une annonce supprimée",exact=True).click()
                page.get_by_label("Précisions (facultatif)",exact=True).fill("Le portail indique que cette annonce a été supprimée.")
                page.get_by_role("button",name="Envoyer le signalement",exact=True).scroll_into_view_if_needed()
                page.screenshot(path=str(output/"report-form-demo.png"))
                page.get_by_role("button",name="Envoyer le signalement",exact=True).click()
                page.get_by_text("Signalement envoyé. Un administrateur va le vérifier.",exact=True).wait_for()
                page.get_by_role("button",name="Fermer le détail",exact=True).click()
                assert repo.get_listing_by_id(ids[0]).removed_at is None
                page.get_by_role("button",name="Mon compte",exact=True).first.click()
                page.get_by_role("button",name="Me déconnecter",exact=True).click()
                page.get_by_role("button",name="Se connecter",exact=True).last.click();login(admin["credentials"])
                page.locator(".admin-menu summary").click()
                page.get_by_role("button",name="Sources & activité",exact=True).click()
                page.get_by_role("tab",name="Signalements",exact=True).click()
                page.locator('.report-item').wait_for()
                for viewport in [{"width":320,"height":740},{"width":390,"height":844},{"width":768,"height":1024},{"width":1440,"height":1050}]:
                    page.set_viewport_size(viewport)
                    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
                page.screenshot(path=str(output/"reports-demo.png"))
                page.get_by_role("button",name="Supprimer du site",exact=True).click()
                confirmation=page.locator('dialog.moderation-confirm')
                confirmation.wait_for();page.screenshot(path=str(output/"report-delete-demo.png"))
                confirmation.get_by_role("button",name="Conserver l’annonce",exact=True).click()
                assert repo.get_listing_by_id(ids[0]).removed_at is None
                page.get_by_role("button",name="Supprimer du site",exact=True).click()
                page.keyboard.press("Escape");confirmation.wait_for(state="hidden")
                page.get_by_role("button",name="Supprimer du site",exact=True).click()
                confirmation.get_by_role("button",name="Supprimer du site",exact=True).click()
                page.get_by_text("Aucun signalement en attente",exact=True).wait_for()
                assert repo.get_listing_by_id(ids[0]).removed_at is not None
                page.get_by_label("Afficher les signalements",exact=True).select_option('closed')
                page.locator('.report-item').wait_for()
                assert "Annonce supprimée" in page.locator('.report-item').inner_text()
                page.get_by_role("button",name="Explorer",exact=True).first.click()
                page.locator('.property-card').first.wait_for()
                assert page.get_by_role("button",name="Détails de Appartement lumineux avec balcon",exact=True).count()==0
                # Admin can remove an unreported property directly from its detail.
                page.locator('.photo-open').first.click();page.locator('.detail-retry').wait_for()
                removed_title=page.locator('#listing-title').inner_text()
                page.get_by_role("button",name="Supprimer cette annonce du site",exact=True).click()
                confirmation.wait_for()
                confirmation.get_by_role("button",name="Supprimer du site",exact=True).click()
                page.locator('.detail-drawer').wait_for(state="hidden")
                page.get_by_role("button",name="Détails de "+removed_title,exact=True).wait_for(state="hidden")
                assert page.evaluate("document.body.style.overflow!=='hidden'")
                browser.close()
                if errors:
                    raise RuntimeError("Browser errors: "+"; ".join(errors))
            print("Captured eighteen UI views; verified dark/light native select contrast, responsive layout, forms, favorites, read state, gallery, key CRUD, real immediate search, member reporting, admin review and confirmed removal using isolated fictitious data; no production data.")
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
