"""Exercise the real PHP gateway against a disposable TLS backend.

Requires Docker images tgbbot-tgbbot and php:8.3-cli-alpine with cURL enabled.
Only temporary configuration, a temporary database and loopback ports are used.
"""
from __future__ import annotations

import json
from http.cookies import SimpleCookie
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import quote
from urllib.request import Request,urlopen
from urllib.error import HTTPError


def free_port():
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));return listener.getsockname()[1]


def call(url,*,body=None,headers=None):
    request=Request(url,data=json.dumps(body).encode() if body is not None else None,headers={**({'Content-Type':'application/json'} if body is not None else {}),**(headers or {})})
    try:response=urlopen(request,timeout=30)
    except HTTPError as error:response=error
    return response.status,response.headers,json.loads(response.read())


def run():
    project=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='tgbbot-member-gateway-') as temporary:
        root=Path(temporary);(root/'httpdocs/api').mkdir(parents=True);(root/'private').mkdir()
        shutil.copyfile(project/'deploy/plesk/httpdocs/api/index.php',root/'httpdocs/api/index.php')
        backend_port,php_port=free_port(),free_port();public='https://site.example.test';secret=secrets.token_hex(32)
        (root/'private/immoalert.php').write_text("<?php return ['backend_url'=>'https://127.0.0.1:"+str(backend_port)+"','public_origin'=>'"+public+"','shared_secret'=>'"+secret+"'];")
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(root/'key.pem'),'-out',str(root/'cert.pem'),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
        (root/'backend.py').write_text('''import asyncio,json,os,ssl,time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock
from aiohttp import web
from database.session import init_db
from database import repo,models as m
from services import web_auth,provider_credentials,listing_details
from services.web_api import create_app
provider_credentials._MASTER_PATH=Path('/qa/provider-key')
init_db();account=web_auth.create_account(None,'gateway_demo',admin=True,bootstrap=True)
ids=[]
for index in range(2):
 row=repo.save_listing(source='pap',external_id='qa-'+str(index),url='https://www.pap.fr/annonces/qa-'+str(index),title='Appartement fictif '+str(index),price=800+index*200,surface_m2=40+index*20,location='Rouen 76000',last_seen_at=datetime.utcnow(),inventory_status='active').listing
 ids.append(row.id);repo.add_favorite(account['account']['user_id'],row.id)
def load(identifier):
 time.sleep(.9)
 with repo.session_scope() as session:
  row=session.get(m.Listing,identifier);row.details_status='complete';row.details_checked_at=datetime.utcnow()
 return repo.get_listing_by_id(identifier)
listing_details._load=load
from services import location_area
location_area._area=lambda row: {'kind':'indicative','center':[49.44,1.09],'radius_m':500,'geometry':None,'label':'Secteur fictif','detail':'Démonstration.'}
Path('/qa/credentials.json').write_text(json.dumps({'credentials':account['credentials'],'ids':ids}))
app=create_app(AsyncMock(),directory=Path('/qa/httpdocs'),origin='http://127.0.0.1',bot_username='demo_bot')
tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain('/qa/cert.pem','/qa/key.pem')
web.run_app(app,host='127.0.0.1',port=int(os.environ['QA_PORT']),ssl_context=tls,print=None)
''')
        names=['tgbbot-member-qa-backend-'+str(php_port),'tgbbot-member-qa-php-'+str(php_port)];processes=[]
        try:
            command=['docker','run','--rm','--name',names[0],'--network','host','--entrypoint','python','-v',str(project)+':/app:ro','-v',temporary+':/qa','--tmpfs','/app/storage','-w','/app']
            environment=dict(PYTHONPATH='/app',BOT_TOKEN='123456:demo-only',DATABASE_URL='sqlite:////tmp/gateway-demo.db',ENABLE_GEOCODING='false',FIRECRAWL_ENABLED='false',FIRECRAWL_API_KEY='',FIRECRAWL_API_KEYS='',FLASHPROXY_ENABLED='false',FLASHPROXY_API_KEY='',SCRAPLING_ENABLED='false',ADMIN_TELEGRAM_IDS='',WEB_PROXY_ORIGIN=public,WEB_PROXY_SECRET=secret,QA_PORT=str(backend_port))
            for key,value in environment.items():command+=['-e',key+'='+value]
            processes.append(subprocess.Popen(command+['tgbbot-tgbbot','/qa/backend.py'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
            for _ in range(150):
                if (root/'credentials.json').exists():break
                if processes[0].poll() is not None:raise RuntimeError('Disposable backend failed to start')
                time.sleep(.1)
            info=json.loads((root/'credentials.json').read_text())
            processes.append(subprocess.Popen(['docker','run','--rm','--name',names[1],'--network','host','-v',temporary+':/qa','php:8.3-cli-alpine','php','-d','curl.cainfo=/qa/cert.pem','-S','127.0.0.1:'+str(php_port),'-t','/qa/httpdocs'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
            prefix='http://127.0.0.1:'+str(php_port)+'/api/index.php?route='
            url=lambda route:prefix+quote(route,safe='')
            for _ in range(150):
                try:
                    status,_,_=call(url('/api/health'))
                    if status==200:break
                except OSError:pass
                time.sleep(.1)
            assert status==200,'PHP cURL/TLS health check failed'
            assert call(url('/api/login'),body=info['credentials'],headers={'Origin':'https://evil.example.test'})[0]==403
            status,headers,_=call(url('/api/login'),body=info['credentials'],headers={'Origin':public});assert status==200
            cookie=SimpleCookie();cookie.load(headers['Set-Cookie']);token=cookie['immo_session'].value
            assert 'Secure' in headers['Set-Cookie'] and 'HttpOnly' in headers['Set-Cookie']
            auth={'Origin':public,'Cookie':'immo_session='+token}
            assert call(url('/api/bootstrap'),headers=auth)[2]['snapshot']['listings']==[]
            status,_,page=call(url('/api/inventory?q=Rouen&limit=1&page=2&sort=price'),headers=auth)
            assert status==200 and page['total']==2 and len(page['items'])==1 and page['page']==2
            first,second=info['ids']
            status,_,initial=call(url(f'/api/listings/{first}'),headers=auth);assert status==200 and initial['enriching']
            started=time.monotonic();status,_,ready=call(url(f'/api/listings/{first}/updates'),headers=auth)
            assert status==200 and not ready['enriching'] and time.monotonic()-started<20
            assert call(url(f'/api/listings/{first}/area'),headers=auth)[0]==200
            status,_,history=call(url(f'/api/listings/{first}/prices?limit=1'),headers=auth);assert status==200 and len(history['items'])==1
            status,_,note=call(url(f'/api/listings/{first}/note'),body={'note':'é'*4000,'stage':'contacted'},headers=auth);assert status==200 and len(note['tracking']['note'])==4000
            status,_,tracking=call(url('/api/tracking?stage=contacted&scope=active&q=Rouen&sort=recent&limit=1&page=1&upcoming=0'),headers=auth)
            assert status==200 and tracking['total']==1 and tracking['items'][0]['tracking']['note']=='é'*4000
            assert call(url('/api/tracking?user_id=42'),headers=auth)[0]==400
            assert call(url('/api/admin/providers'),body={'provider':'flashproxy','operation':'configure_plan','plan_id':'gateway-test-plan'},headers=auth)[0]==200
            status,_,providers=call(url('/api/admin/providers'),headers=auth)
            assert status==200 and next(row for row in providers['providers'] if row['id']=='flashproxy')['plan_id']=='gateway-test-plan'
            assert call(url('/api/admin/providers'),body={'provider':'flashproxy','operation':'configure_plans','plan_ids':['gateway-one','gateway-two']},headers=auth)[0]==200
            status,_,providers=call(url('/api/admin/providers'),headers=auth)
            assert status==200 and next(row for row in providers['providers'] if row['id']=='flashproxy')['plan_ids']==['gateway-one','gateway-two']
            assert call(url('/api/admin/providers'),body={'provider':'flashproxy','operation':'configure_plan','plan_id':'legacy'},headers=auth)[0]==400
            status,_,prefs=call(url('/api/preferences'),body={'daily_summary':True,'summary_time':'09:00','quiet_start':'22:00','quiet_end':'07:00','timezone':'Europe/Paris','price_alerts':True},headers=auth);assert status==200 and prefs['preferences']['daily_summary']
            assert call(url(f'/api/comparison?ids={first},{second}'),headers=auth)[0]==200
            assert call(url('/api/performance'),body={'samples':[{'name':'detail_visible','value_ms':150}]},headers=auth)[0]==202
            assert call(url('/api/admin/performance'),headers=auth)[0]==200
            assert call(url('/api/inventory?q[]=Rouen'),headers=auth)[0]==400
            assert call(url(f'/api/listings/{first}/events'),headers=auth)[0]==404
            assert call(url('https://evil.example.test/'),headers=auth)[0]==404
            assert call(url(f'/api/listings/{first}/note'),body={'note':'x'*66000},headers=auth)[0]==413
            print('PHP/TLS gateway passed: signed queries, compact bootstrap, pagination, buffered detail updates, price history, Unicode notes, preferences, comparison, performance, cookie/CSRF handling and route/body limits.')
        finally:
            for name in names:subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            for process in processes:
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate()


if __name__=='__main__':run()
