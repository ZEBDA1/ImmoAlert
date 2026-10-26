/** Offline UI checks. IMD_PLAYWRIGHT may point to an installed Playwright module.
 * Build web/ first. Only synthetic data, local images and loopback HTTP are used.
 */
import { createServer } from 'node:http';
import { readFile, mkdir, stat } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve, extname, sep } from 'node:path';
import assert from 'node:assert/strict';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const require=createRequire(import.meta.url);
const modulePath=process.env.IMD_PLAYWRIGHT||require.resolve('playwright',{paths:[root,resolve(root,'web')]});
const {chromium}=await import(pathToFileURL(modulePath).href);
const out=process.env.UI_QA_OUTPUT||'/tmp/immoalert-listing-ui';
await mkdir(out,{recursive:true});
const images=await Promise.all(['web/public/images/studio-interior.webp','docs/assets/demo-studio-a.webp','docs/assets/demo-studio-b.webp'].map(p=>readFile(resolve(root,p))));
const now=new Date().toISOString();
const indexed=new Date(Date.now()-2*86400000).toISOString();
const readIds=new Set();
let origin,scenario='ready',abortedRequests=0,enrichmentCalls=0,adminPreview=false;
const timers=new Set();
const first={id:1,title:'Appartement lumineux avec balcon',price:850,surface_m2:45,rooms:2,source:'pap',location:'Rouen (76000)',url:'https://example.test/annonce-fictive',description:'',images:[],first_seen_at:indexed,last_seen_at:now,charges_included:true,inventory_status:'active',publications:[{source:'pap',url:'https://example.test/fictif'}]};
const second={...first,id:2,title:'Studio à découvrir',price:520,surface_m2:24,images:[]};
const mime={'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.ttf':'font/ttf','.png':'image/png','.webp':'image/webp'};
const server=createServer(async(req,res)=>{
 const url=new URL(req.url,origin||'http://127.0.0.1');
 const send=(data,type='application/json',status=200)=>{if(res.destroyed)return;res.writeHead(status,{'Content-Type':type,'Cache-Control':type==='image/webp'?'private, max-age=300':'no-store'});res.end(type==='application/json'?JSON.stringify(data):data)};
 const later=(ms,fn)=>{const timer=setTimeout(()=>{timers.delete(timer);fn()},ms);timers.add(timer);res.on('close',()=>{if(!res.writableEnded)abortedRequests++;clearTimeout(timer);timers.delete(timer)})};
 if(['/api/state','/api/bootstrap'].includes(url.pathname))return send({account:{user_id:1,login:'camille_demo',is_admin:adminPreview,is_premium:true,read_listing_ids:[...readIds],filters:[{id:1,name:'Mon appartement',city:'Rouen',postal_code:'76000',price_max:1000,active:true}],favorites:[],matches:{'1':[1,2]}},snapshot:{listings:[{...first,images:[`${origin}/photo/0.webp`]},second],total_listings:2,new_today:2,sources:[{name:'pap',status:'success'}]},commands:[],search_jobs:[]});
 if(url.pathname==='/api/performance')return send({accepted:1});
 if(url.pathname==='/api/inventory')return send({items:[{...first,images:[`${origin}/photo/0.webp`],is_read:readIds.has(1),is_favorite:false},{...second,is_read:readIds.has(2),is_favorite:false}],total:2,page:1,limit:18,next_page:null});
 if(url.pathname==='/api/admin/live')return send({sources:[],accounts:[],requests:[],inflight:[],health_summary:{},synced_at:now});
 if(url.pathname==='/api/action'&&req.method==='POST'){
  let body='';for await(const chunk of req)body+=chunk;
  const data=JSON.parse(body);if(data.kind==='mark_read'){readIds.add(data.payload.listing_id);return send({read:true,listing_id:data.payload.listing_id})}
  return send({error:'Unsupported fixture action'},'application/json',400);
 }
 if(url.pathname.endsWith('/area'))return send({area:{kind:'indicative',center:[49.44,1.09],geometry:null,radius_m:500,label:'Secteur fictif',detail:'Zone de démonstration.'}});
 if(url.pathname.startsWith('/api/listings/')){
  const base=url.pathname.endsWith('/2')?second:first;
  const description='Un logement fictif, calme et lumineux. La description complète arrive après la réponse du portail.\n\n'+('De grandes fenêtres et des espaces bien agencés pour le quotidien. '.repeat(6));
  const listing={...base,description,images:scenario==='empty'?[]:scenario==='broken'?[`${origin}/photo/broken.webp`]:[0,1,2].map(i=>`${origin}/photo/${i}.webp?detail=${scenario}`),features:{DPE:'C',Chauffage:'Individuel électrique','Équipements':'Cuisine aménagée et espace de rangement pour les affaires du quotidien'},latitude:49.44,longitude:1.09,geo_precision:'city',details_status:'complete'};
  if(scenario==='enriching'&&enrichmentCalls++===0)return send({listing:{...listing,description:'Premières informations disponibles.',images:listing.images.slice(0,2)},enriching:true});
  if(scenario==='pending'){enrichmentCalls++;return send({listing:{...listing,description:'Premières informations disponibles.'},enriching:true,retry_after_ms:2000})}
  if(scenario==='partial')return send({listing:{...listing,details_status:'partial'},enriching:false});
  if(scenario==='error')return later(650,()=>send({error:'La connexion au portail est momentanément indisponible.'},'application/json',503));
  return later(['slow','abort'].includes(scenario)?1800:0,()=>send({listing,enriching:false}));
 }
 if(url.pathname.startsWith('/photo/')){
  if(url.pathname.includes('broken'))return send('','image/webp',404);
  const index=Number(url.pathname.match(/\d+/)?.[0])||0;
  return later(scenario==='slow'&&index===1?1400:0,()=>send(images[index%3],'image/webp'));
 }
 try{
  const file=resolve(root,'web/dist','.'+decodeURIComponent(url.pathname),url.pathname.endsWith('/')?'index.html':'');
  assert(file.startsWith(resolve(root,'web/dist')+sep));
  assert((await stat(file)).isFile());
  send(await readFile(file),mime[extname(file)]||'application/octet-stream');
 }catch{send('Not found','text/plain',404)}
});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
origin=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_BROWSER_PATH?{executablePath:process.env.PLAYWRIGHT_BROWSER_PATH}:{})});
const errors=[];
const pause=ms=>new Promise(done=>setTimeout(done,ms));
async function waitFor(check,label){for(let i=0;i<80;i++){if(await check())return;await pause(25)}throw new Error(label)}
async function context(options={}){
 const ctx=await browser.newContext(options),page=await ctx.newPage();
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('https://**/*',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><p>Carte fictive hors ligne</p>'}));
 await page.goto(origin);await page.locator('.photo-open').first().waitFor();
 return {ctx,page};
}
async function open(page,which=0){await page.locator('.photo-open').nth(which).click();await page.getByRole('dialog').waitFor()}
async function loaded(page){await page.locator('.detail-retry').waitFor();await page.locator('.detail-progress.is-ready').waitFor();await page.waitForFunction(()=>document.querySelector('.detail-description')?.textContent?.startsWith('Un logement fictif'))}
async function close(page){await page.getByRole('button',{name:'Fermer le détail',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'})}
async function noOverflow(page){
 const result=await page.evaluate(()=>{
  const panel=document.querySelector('.detail-drawer');
  return {page:document.documentElement.scrollWidth<=innerWidth+1,panel:!panel||panel.scrollWidth<=panel.clientWidth+1,close:[...document.querySelectorAll('[aria-label="Fermer le détail"]')].every(el=>{const b=el.getBoundingClientRect();return b.x>=0&&b.right<=innerWidth+1&&b.y>=0&&b.bottom<=innerHeight+1})};
 });assert.deepEqual(result,{page:true,panel:true,close:true});
}
try{
 // Delayed information, then a separately delayed full-size photo.
 scenario='slow';let {ctx,page}=await context({viewport:{width:390,height:844},hasTouch:true,isMobile:true});
 await open(page,1);
 await page.locator('.gallery-pending').waitFor();
 await page.locator('.description-skeleton').waitFor();
 assert.equal(await page.locator('.detail-description-slot').getAttribute('aria-busy'),'true');
 assert.equal(await page.locator('#listing-title').textContent(),second.title);
 await page.screenshot({path:resolve(out,'loading-mobile.png')});
 await loaded(page);assert.equal(await page.locator('.gallery-thumbnails button').count(),3);
 const before=await page.locator('.gallery-stage').boundingBox();
 await page.getByRole('button',{name:'Photo suivante',exact:true}).click();
 await page.locator('.gallery-frame [data-image-state="loading"]').waitFor();
 const during=await page.locator('.gallery-stage').boundingBox();assert.equal(before.height,during.height);
 await page.locator('.gallery-frame [data-image-state="ready"]').waitFor();
 await page.waitForFunction(()=>getComputedStyle(document.querySelector('.gallery-frame img')).opacity==='1');
 await page.screenshot({path:resolve(out,'loaded-mobile.png')});
 // A horizontal touch gesture changes photos without preventing vertical scrolling.
 const touchSession=await ctx.newCDPSession(page),box=await page.locator('.gallery-frame').boundingBox();
 const x=box.x+box.width-80,y=box.y+box.height*.45;
 await touchSession.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
 for(let i=1;i<=4;i++)await touchSession.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x-i*30,y}]});
 await touchSession.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 await page.waitForFunction(()=>document.querySelector('.gallery-counter')?.textContent==='3 / 3');
 await page.getByRole('button',{name:'Agrandir la galerie',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.detail-gallery.expanded')?.getBoundingClientRect().width>=innerWidth-1);
 await page.keyboard.press('Escape');assert.equal(await page.getByRole('dialog').count(),1);
 await page.locator('.detail-gallery.expanded').waitFor({state:'hidden'});
 await close(page);await ctx.close();
 console.log('Slow loading, decoded photo reveal, stable gallery size, touch swipe and fullscreen Escape passed.');

 scenario='ready';({ctx,page}=await context({viewport:{width:390,height:844},reducedMotion:'reduce'}));
 const firstCard=page.locator('.property-card[data-listing-id="1"]');
 assert.equal(await firstCard.locator('.property-footer small').textContent(),'Il y a 2 j');
 await open(page);await loaded(page);await close(page);await firstCard.locator('.read-badge').waitFor();
 await page.reload();await firstCard.locator('.read-badge').waitFor();
 assert.equal(await firstCard.locator('.property-footer small').textContent(),'Il y a 2 j');
 assert.equal(await firstCard.locator('.new-badge').count(),0);
 await ctx.close();console.log('Read badge survives reload; first-indexing age stays two days despite a fresh observation.');

 // A partial response remains usable while enrichment adds content and photos.
 scenario='enriching';enrichmentCalls=0;({ctx,page}=await context({viewport:{width:768,height:1024}}));
 await open(page);await page.getByText('Premières informations disponibles.',{exact:true}).waitFor();
 await page.getByRole('button',{name:'Afficher la photo 2',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.gallery-counter')?.textContent==='2 / 2');
 await loaded(page);assert.equal(await page.locator('.gallery-counter').textContent(),'2 / 3');
 await page.locator('.gallery-frame [data-image-state="ready"]').waitFor();
 const knownDescription=await page.locator('.detail-description').textContent();
 scenario='slow';await page.locator('.detail-retry').click();await page.locator('.detail-progress.is-loading .detail-loading-mark').waitFor();
 assert.equal(await page.locator('.detail-description').textContent(),knownDescription);
 assert.equal(await page.locator('.gallery-counter').textContent(),'2 / 3');
 await loaded(page);await close(page);await ctx.close();console.log('Polling enrichment and refresh passed: existing content and selected photo remain visible while details load.');

 // Closing before response aborts work, and rapid reopening shows the new listing.
 scenario='abort';({ctx,page}=await context({viewport:{width:1440,height:1000}}));
 await open(page);await page.locator('.description-skeleton').waitFor();
 const aborts=abortedRequests;await close(page);await waitFor(()=>abortedRequests>aborts,'Detail request was not aborted');
 scenario='ready';await open(page,1);await loaded(page);assert.equal(await page.locator('#listing-title').textContent(),second.title);
 await page.getByRole('button',{name:'Fermer le détail',exact:true}).focus();
 await page.keyboard.press('Shift+Tab');assert(await page.getByRole('dialog').evaluate(el=>el.contains(document.activeElement)));
 await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement?.getAttribute('aria-label')),'Fermer le détail');
 await close(page);assert.notEqual(await page.evaluate(()=>document.body.style.overflow),'hidden');
 assert.equal(await page.evaluate(()=>document.activeElement?.getAttribute('aria-label')),`Détails de ${second.title}`);
 await ctx.close();console.log('Close/abort, rapid reopening and trigger focus restoration passed.');

 for(const state of ['error','empty','broken']){
  scenario=state;({ctx,page}=await context({viewport:{width:320,height:740},reducedMotion:'reduce'}));await open(page,1);
  if(state==='error'){
   await page.locator('.detail-progress.warn').waitFor();await page.locator('.detail-description').waitFor();
   assert.equal(await page.locator('.description-skeleton').count(),0);
   scenario='ready';await page.locator('.detail-retry').click();await loaded(page);
  }else if(state==='empty'){
   await loaded(page);await page.getByText('Aucune photo disponible',{exact:true}).waitFor();
  }else{
   await loaded(page);await page.locator('.gallery-frame [data-image-state="error"]').waitFor();
   await page.getByText('Photo indisponible',{exact:true}).waitFor();
  }
  await noOverflow(page);await close(page);await ctx.close();
 }
 console.log('Request error/retry, missing photos and broken photos passed at 320px with reduced motion.');

 scenario='ready';
 const viewports=[{width:320,height:740},{width:390,height:844},{width:768,height:1024},{width:1024,height:768},{width:1440,height:1000},{width:844,height:390}];
 for(const theme of ['dark','light']){
  ({ctx,page}=await context({reducedMotion:'reduce'}));
  await page.evaluate(theme=>{localStorage.setItem('immoalert.theme',theme)},theme);await page.reload();await page.locator('.photo-open').first().waitFor();
  for(const viewport of viewports){
   await page.setViewportSize(viewport);await open(page);await loaded(page);await noOverflow(page);
   await page.locator('.gallery-frame [data-image-state="ready"]').waitFor();
   await page.getByRole('button',{name:'Agrandir la galerie',exact:true}).click();
   await noOverflow(page);await page.keyboard.press('Escape');
   // Header remains reachable after reading a long description.
   await page.locator('.detail-drawer').evaluate(el=>el.scrollTop=el.scrollHeight);await noOverflow(page);
   if(viewport.width===768)await page.screenshot({path:resolve(out,`tablet-${theme}.png`)});
   await close(page);
  }
  assert.equal(await page.evaluate(()=>document.getAnimations().filter(a=>a.playState==='running'&&a.effect?.getTiming().iterations===Infinity).length),0);
  await ctx.close();
 }
 console.log('Dark/light layouts passed at 320, 390, 768, 1024, 1440px and landscape; no page overflow or reduced-motion loops.');

 // A genuinely slow portal: real status, bounded polling, motion preferences,
 // and a useful retry state instead of an endless loading animation.
 scenario='pending';enrichmentCalls=0;({ctx,page}=await context({viewport:{width:390,height:844}}));
 await open(page);await page.getByText('Récupération des compléments…',{exact:true}).waitFor();
 await page.locator('.detail-load-status').scrollIntoViewIfNeeded();
 assert.equal(await page.locator('.detail-load-status').getAttribute('role'),'status');
 assert.notEqual(await page.locator('.detail-loading-mark i').first().evaluate(el=>getComputedStyle(el).animationName),'none');
 assert.equal(await page.getByText('Premières informations disponibles.',{exact:true}).count(),1);
 await page.screenshot({path:resolve(out,'progressive-loading-dark.png')});
 await page.getByText('Le portail met un peu plus de temps à répondre.',{exact:true}).waitFor({timeout:11000});
 assert(enrichmentCalls>=3&&enrichmentCalls<=5,`Unexpected polling count: ${enrichmentCalls}`);
 await page.screenshot({path:resolve(out,'slow-portal-dark.png')});
 await close(page);const stoppedCalls=enrichmentCalls;await pause(2600);assert.equal(enrichmentCalls,stoppedCalls);
 await ctx.close();

 ({ctx,page}=await context({viewport:{width:320,height:740},reducedMotion:'reduce'}));
 await page.getByRole('button',{name:'Passer en mode clair',exact:true}).click();
 await open(page);await page.getByText('Récupération des compléments…',{exact:true}).waitFor();
 assert.equal(await page.locator('.detail-loading-content').evaluate(el=>el.getAnimations({subtree:true}).filter(a=>a.playState==='running').length),0);
 await page.locator('.detail-load-status').scrollIntoViewIfNeeded();await noOverflow(page);
 await page.screenshot({path:resolve(out,'progressive-loading-light-reduced.png')});
 await page.clock.install();await page.clock.fastForward(121000);
 await page.locator('.detail-progress.warn').waitFor();
 assert.match(await page.locator('.detail-progress.warn').textContent(),/plus de temps que prévu/);
 assert.equal(await page.locator('.detail-loading-mark').count(),0);
 assert.equal(await page.getByText('Premières informations disponibles.',{exact:true}).count(),1);
 await page.clock.resume();scenario='ready';await page.locator('.detail-retry').click();await loaded(page);
 await close(page);await ctx.close();

 scenario='partial';({ctx,page}=await context({reducedMotion:'reduce'}));await open(page);
 await page.locator('.detail-progress.warn').waitFor();assert.equal(await page.locator('.detail-loading-mark').count(),0);
 await page.locator('.detail-retry').waitFor();await close(page);await ctx.close();
 console.log('Progressive status, slow-portal copy, bounded polling, cancellation, 2-minute retry and reduced-motion loading passed.');

 // Administration must not be downloaded until it is opened.
 scenario='ready';adminPreview=true;({ctx,page}=await context({viewport:{width:1440,height:1000},reducedMotion:'reduce'}));
 const adminDownloads=()=>page.evaluate(()=>performance.getEntriesByType('resource').filter(entry=>/admin-panel-.*\.js/.test(entry.name)).length);
 assert.equal(await adminDownloads(),0);
 await page.locator('.admin-menu summary').click();await page.getByRole('button',{name:'Sources & activité',exact:true}).click();
 await page.getByRole('heading',{name:'Votre poste de contrôle.',exact:true}).waitFor();
 assert.equal(await adminDownloads(),1);await ctx.close();
 assert.deepEqual(errors,[]);console.log('Administration loads on demand; no browser script errors.');
}finally{
 await browser.close();for(const timer of timers)clearTimeout(timer);server.closeAllConnections();await new Promise(done=>server.close(done));
}
