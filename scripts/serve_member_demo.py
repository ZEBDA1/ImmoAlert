"""Disposable, loopback-only demo for real API/browser regression checks.

No .env, bot polling, scheduler, external collector or production database is
loaded. --php-frontend serves a PHP-transport build through a local adapter;
the actual PHP/TLS signature path is verified separately.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime,timedelta
import json
import logging
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
from unittest.mock import AsyncMock,patch


async def serve(root,frontend,output,php_frontend):
    with tempfile.TemporaryDirectory(prefix='tgbbot-member-demo-') as temporary:
        os.environ.update(BOT_TOKEN='123456:demo-only',DATABASE_URL='sqlite:///'+str(Path(temporary)/'demo.db'),ADMIN_TELEGRAM_IDS='',ENABLE_GEOCODING='false',FIRECRAWL_ENABLED='false',FIRECRAWL_API_KEY='',FIRECRAWL_API_KEYS='',FLASHPROXY_ENABLED='false',FLASHPROXY_API_KEY='',SCRAPLING_ENABLED='false',WEB_PROXY_ORIGIN='',WEB_PROXY_SECRET='',AI_SCAM_ENABLED='false')
        with patch('dotenv.load_dotenv',return_value=False):
            from aiohttp import web,ClientSession
            from sqlalchemy import select
            from database.session import init_db
            from database import models as m,repo
            from services import web_auth,listing_details,property_tracking,member_notifications,provider_credentials,location_area
            from services.web_api import create_app,STATE
        provider_credentials._MASTER_PATH=Path(temporary)/'provider-master.key'
        init_db();admin=web_auth.create_account(None,'demo_admin',admin=True,bootstrap=True)
        member=web_auth.create_account(admin['account']['user_id'],'camille_demo',premium=True)
        user_id=member['account']['user_id'];now=datetime.utcnow();ids=[]
        for index in range(40):
            row=repo.save_listing(source='pap',external_id=f'demo-{index}',url=f'https://www.pap.fr/annonces/demo-{index}',title=['Appartement lumineux avec balcon','Studio au calme','Maison avec un jardin'][index] if index<3 else f'Appartement de démonstration {index+1}',price=[850,520,1100][index] if index<3 else 650+index*20,surface_m2=[45,24,80][index] if index<3 else 30+index*2,rooms=2 if index%3 else 3,location='Rouen (76000)',description='Un logement de démonstration, sans annonce ni personne réelle. Un séjour lumineux, une cuisine et des espaces agréables pour le quotidien. '*4,normalized_city='Rouen',normalized_postal_code='76000',last_seen_at=now,inventory_status='active',charges_included=True if index==0 else False if index==1 else None).listing
            ids.append(row.id)
            with repo.session_scope() as session:
                stored=session.get(m.Listing,row.id);stored.first_seen_at=now-timedelta(hours=index+1)
                stored.details=json.dumps({'DPE':'C' if index==0 else 'B','Chauffage':'Individuel'} if index!=1 else {'Chauffage':'Collectif'})
                if index==0:
                    demo_features=json.loads(stored.details);demo_features.update({'Date de publication':'2026-10-26T15:04:02.000Z','activity_sector':2,'external_ad_id':'INTERNAL-NETTY-DEMO','store_logo':'https://images.example.test/agency-logo.png','store_name':'Agence de démonstration','mandate_type':'exclusive','lease_type':'rent','Honoraires':'605','floor_display':'Étage 2/4','Charges incluses':'true','virtual_tour':'https://tour.example.test/demo'})
                    stored.details=json.dumps(demo_features);stored.published_at=datetime(2026,10,26,15,4,2)
                stored.details_status='complete';stored.details_checked_at=now
                stored.latitude=49.44;stored.longitude=1.09;stored.geo_precision='city'
        for identifier in ids[:4]:repo.add_favorite(user_id,identifier)
        repo.add_favorite(admin['account']['user_id'],ids[0])
        property_tracking.save_note(user_id,ids[0],{'note':'Demander le détail des charges.','stage':'contacted'})
        property_tracking.save_note(admin['account']['user_id'],ids[0],{'note':'NOTE PRIVÉE ADMINISTRATEUR','stage':'to_contact'})
        repo.create_or_update_filter(user_id,name='Rouen',city='Rouen',postal_code='76000',price_max=1600)
        repo.save_listing(source='pap',external_id='demo-0',url='https://www.pap.fr/annonces/demo-0',price=800,last_seen_at=now+timedelta(seconds=1),inventory_status='active')
        member_notifications.queue_events(now)
        state={'bootstrap_delay':0,'collections':{},'requests':{},'bootstrap_max':0,'bootstrap_active':0}
        app=create_app(AsyncMock(),directory=frontend,origin='http://127.0.0.1',bot_username='demo_bot')
        @web.middleware
        async def probe(request,handler):
            state['requests'][request.path]=state['requests'].get(request.path,0)+1
            if request.path=='/api/bootstrap':
                state['bootstrap_active']+=1;state['bootstrap_max']=max(state['bootstrap_max'],state['bootstrap_active'])
                try:
                    if state['bootstrap_delay']:await asyncio.sleep(state['bootstrap_delay'])
                    return await handler(request)
                finally:state['bootstrap_active']-=1
            return await handler(request)
        app.middlewares.insert(0,probe)
        runner=web.AppRunner(app,access_log=None);await runner.setup();listener=web.TCPSite(runner,'127.0.0.1',0);await listener.start()
        backend='http://127.0.0.1:'+str(listener._server.sockets[0].getsockname()[1]);base=backend
        front_runner=None;client=None
        if php_frontend:
            client=ClientSession()
            front=web.Application(client_max_size=65536)
            async def gateway(request):
                route=request.query.get('route','')
                if not route.startswith('/api/') or route.startswith('//'):return web.json_response({'error':'Route invalide.'},status=404)
                headers={key:request.headers[key] for key in ['Content-Type','Origin','Cookie'] if key in request.headers}
                async with client.request(request.method,backend+route,headers=headers,data=await request.read(),allow_redirects=False) as upstream:
                    response=web.Response(status=upstream.status,body=await upstream.read(),content_type='application/json')
                    for cookie in upstream.headers.getall('Set-Cookie',[]):response.headers.add('Set-Cookie',cookie)
                    return response
            async def static(request):
                relative=request.match_info.get('path','') or 'index.html';target=(php_frontend/relative).resolve()
                if php_frontend not in target.parents or not target.is_file():raise web.HTTPNotFound()
                return web.FileResponse(target)
            front.router.add_route('*','/api/index.php',gateway);front.router.add_get('/{path:.*}',static)
            front_runner=web.AppRunner(front,access_log=None);await front_runner.setup();front_site=web.TCPSite(front_runner,'127.0.0.1',0);await front_site.start()
            base='http://127.0.0.1:'+str(front_site._server.sockets[0].getsockname()[1])
        app[STATE].origin=base
        with repo.session_scope() as session:
            for row in session.scalars(select(m.Listing)):
                row.images=json.dumps([base+'/images/studio-interior.webp'+f'?photo={index}' for index in range(3)])
        stop=asyncio.Event()
        qa=web.Application()
        async def command(request):
            payload=await request.json();operation=payload.get('operation')
            if operation=='slow':
                with repo.session_scope() as session:session.get(m.Listing,ids[0]).details_checked_at=None
            elif operation=='bootstrap_delay':state['bootstrap_delay']=min(8,max(0,float(payload.get('seconds',0))));state['bootstrap_max']=0
            elif operation=='provider_usage':
                from unittest.mock import Mock
                plans=provider_credentials.flashproxy_plan_ids()
                key=provider_credentials.save_credential('flashproxy',api_key='demo-only-fake-flashproxy-key',label='Clé de démonstration')
                rows=[{'plan_id':identifier,'usage':{'remaining_bytes':(index+1)*2_000_000_000,'bytes_used':1_000_000_000,'max_bytes':(index+1)*2_000_000_000+1_000_000_000}} for index,identifier in enumerate(plans)]
                with patch.object(provider_credentials.requests,'get',return_value=Mock(status_code=200,json=lambda:{'success':True,'data':{'plans':rows}})):
                    await asyncio.to_thread(provider_credentials.refresh_quota,'flashproxy',key['id'])
            elif operation=='stop':stop.set()
            return web.json_response({'ok':True})
        async def status(request):return web.json_response(state)
        qa.router.add_post('/command',command);qa.router.add_get('/status',status)
        qa_runner=web.AppRunner(qa,access_log=None);await qa_runner.setup();qa_site=web.TCPSite(qa_runner,'127.0.0.1',0);await qa_site.start()
        qa_url='http://127.0.0.1:'+str(qa_site._server.sockets[0].getsockname()[1])
        def load(identifier):
            state['collections'][str(identifier)]=state['collections'].get(str(identifier),0)+1
            time.sleep(1.2)
            with repo.session_scope() as session:
                row=session.get(m.Listing,identifier);row.details_checked_at=datetime.utcnow();row.details_status='complete'
            return repo.get_listing_by_id(identifier)
        info={'url':base,'backend_url':backend,'qa_url':qa_url,'mode':'php' if php_frontend else 'native','member':member['credentials'],'admin':admin['credentials'],'listing_ids':ids,'user_id':user_id}
        pending_output=output.with_name(output.name+'.pending')
        pending_output.write_text(json.dumps(info));pending_output.chmod(0o600);pending_output.replace(output)
        print('Isolated member demo ready: '+base,flush=True)
        loop=asyncio.get_running_loop()
        for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stop.set)
        try:
            with patch.object(listing_details,'_load',side_effect=load),patch.object(location_area,'_area',return_value={'kind':'commune','center':[49.44,1.09],'geometry':{'type':'Polygon','coordinates':[[[1.08,49.435],[1.095,49.43],[1.105,49.44],[1.09,49.45],[1.08,49.435]]]},'radius_m':None,'label':'Contour fictif','detail':'Zone de démonstration, sans adresse réelle.'}):await stop.wait()
        finally:
            await asyncio.gather(*listing_details._tasks.values(),return_exceptions=True)
            await qa_runner.cleanup()
            if front_runner:await front_runner.cleanup()
            if client:await client.close()
            await runner.cleanup()


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
    parser=argparse.ArgumentParser();parser.add_argument('--frontend',type=Path,default=root/'web/dist');parser.add_argument('--output',type=Path,default=Path('/tmp/tgbbot-member-demo.json'));parser.add_argument('--php-frontend',type=Path)
    args=parser.parse_args();logging.basicConfig(level=logging.ERROR)
    asyncio.run(serve(root,args.frontend.resolve(),args.output,args.php_frontend.resolve() if args.php_frontend else None))
