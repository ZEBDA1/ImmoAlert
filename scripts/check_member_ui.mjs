import {browserTools} from './browser.mjs';
/** Real backend/browser checks. Start serve_member_demo.py first.
 * Only its disposable database and fake accounts are used.
 */
import {readFile,mkdir,writeFile,mkdtemp} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {resolve,dirname} from 'node:path';

let demo,infoFile=process.env.MEMBER_DEMO_INFO;
if(!infoFile){
 const temporary=await mkdtemp('/tmp/tgbbot-member-browser-');infoFile=temporary+'/session.json';
 const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
 const args=[resolve(root,'scripts/serve_member_demo.py'),'--output',infoFile];
 if(process.env.MEMBER_PHP_FRONTEND)args.push('--php-frontend',process.env.MEMBER_PHP_FRONTEND);
 demo=spawn('python3',args,{cwd:root,stdio:['ignore','ignore','pipe']});let diagnostics='';demo.stderr.on('data',chunk=>diagnostics+=chunk.toString());
 let ready=false;for(let attempt=0;attempt<150;attempt++){try{await readFile(infoFile);ready=true;break}catch{}if(demo.exitCode!==null)throw new Error('Demo failed: '+diagnostics);await new Promise(done=>setTimeout(done,100))}
 if(!ready){demo.kill('SIGTERM');throw new Error('Demo startup timed out: '+diagnostics)}
}
const info=JSON.parse(await readFile(infoFile,'utf8'));
const output=process.env.UI_QA_OUTPUT||`/tmp/tgbbot-member-ui-${info.mode}`;await mkdir(output,{recursive:true});
const {chromium}=await browserTools();
const browser=await chromium.launch({headless:true});
const failures=[];const requestPath=path=>info.mode==='php'?'/api/index.php?route='+encodeURIComponent(path):path;
const qa=async(operation,values={})=>{const response=await fetch(info.qa_url+'/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation,...values}),signal:AbortSignal.timeout(5000)});assert.equal(response.status,200)};
const wait=ms=>new Promise(done=>setTimeout(done,ms));
async function context(credentials,options={}){
 const ctx=await browser.newContext({viewport:{width:1280,height:960},locale:'fr-FR',timezoneId:'Europe/Paris',reducedMotion:'reduce',...options});
 ctx.setDefaultTimeout(12000);
 const page=await ctx.newPage();const tiles={fail:false},assets=[];page.on('request',request=>assets.push(request.url()));page.on('pageerror',error=>{failures.push(error.message);console.error('Browser script error:',error.stack)});
 await page.route('https://tile.openstreetmap.org/**',route=>tiles.fail?route.fulfill({status:503,body:'Temporary test tile failure'}):route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><rect width="256" height="256" fill="#f0f2ed"/><path d="M0 42H256M0 188H256M58 0V256M211 0V256M0 0L256 256" stroke="#fff" stroke-width="12"/><path d="M0 41H256M0 189H256M59 0V256M210 0V256" stroke="#ccd4ca" stroke-width="1"/><text x="72" y="112" font-family="sans-serif" font-size="11" fill="#596657">Démonstration</text></svg>'}));
 await page.route('https://www.openstreetmap.org/**',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><p>Carte de démonstration hors ligne</p>'}));
 await page.addInitScript(()=>{
  const original=window.fetch.bind(window);window.__pollProbe={active:0,max:0,bootstrap:0,inventory:0};
  window.fetch=(...args)=>{const url=new URL(typeof args[0]==='string'?args[0]:args[0] instanceof URL?args[0].href:args[0].url,location.href);const route=url.pathname==='/api/index.php'?(url.searchParams.get('route')||''):url.pathname;const key=route.split('?')[0];const tracked=key==='/api/bootstrap';if(tracked){window.__pollProbe.active++;window.__pollProbe.bootstrap++;window.__pollProbe.max=Math.max(window.__pollProbe.max,window.__pollProbe.active)}if(key==='/api/inventory')window.__pollProbe.inventory++;return original(...args).finally(()=>{if(tracked)window.__pollProbe.active--})};
 });
 const login=await ctx.request.post(info.url+requestPath('/api/login'),{data:credentials,headers:{Origin:info.url}});assert.equal(login.status(),200);
 await page.goto(info.url);await page.locator('.photo-open').first().waitFor();return {ctx,page,tiles,assets};
}
async function nav(page,name){const desktop=page.getByRole('button',{name,exact:true}).first();if(await desktop.count())return desktop.click();const compact=({'Mes favoris':'Favoris','Mes recherches':'Recherches'})[name]||name;const shortcut=page.getByRole('button',{name:compact,exact:true}).first();if(await shortcut.count())return shortcut.click();await page.getByRole('button',{name:'Ouvrir le menu',exact:true}).click();await page.getByRole('dialog',{name:'Navigation',exact:true}).getByRole('button',{name,exact:true}).click()}
async function open(page,index=0){await page.locator('.photo-open').nth(index).click();await page.getByRole('dialog',{name:/Appartement|Studio|Maison|Démonstration|lumineux/i}).waitFor()}
async function close(page){await page.getByRole('button',{name:'Fermer le détail',exact:true}).click();await page.locator('.detail-drawer').waitFor({state:'hidden'})}
async function loaded(page){await page.locator('.detail-progress.is-ready').waitFor();await page.locator('.price-history').waitFor()}
async function noOverflow(page){assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Page overflow');const panel=page.locator('.detail-drawer');if(await panel.count())assert(await panel.evaluate(element=>element.scrollWidth<=element.clientWidth+1),'Detail overflow')}
async function keyboardSkip(page){const link=page.getByRole('link',{name:'Aller au contenu',exact:true});await link.focus();assert(await link.evaluate(element=>{const r=element.getBoundingClientRect(),s=getComputedStyle(element),luminance=color=>{const values=color.match(/[\d.]+/g).slice(0,3).map(value=>{const v=Number(value)/255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4});return values[0]*.2126+values[1]*.7152+values[2]*.0722},foreground=luminance(s.color),background=luminance(s.backgroundColor);return r.x>=0&&r.y>=0&&r.width>24&&r.height>=24&&(Math.max(foreground,background)+.05)/(Math.min(foreground,background)+.05)>=4.5}),'Keyboard skip link must be visible with AA contrast');await link.press('Enter')}
async function api(ctx,path,method='GET',data){const response=await ctx.request.fetch(info.url+requestPath(path),{method,...(data?{data}:{}),headers:{Origin:info.url}});return {response,data:await response.json()}}

let member,admin;
try{
 member=await context(info.member);const {ctx,page}=member;
 assert.equal(await page.locator('.property-card').count(),18);
 const firstPage=await page.locator('.property-card').evaluateAll(elements=>elements.map(element=>element.dataset.listingId));
 await page.getByRole('button',{name:'Suivant',exact:true}).click();await page.getByText('Page 2 sur 3',{exact:true}).waitFor();
 await page.waitForFunction(first=>[...document.querySelectorAll('.property-card')].every(element=>!first.includes(element.dataset.listingId)),firstPage);
 await page.getByRole('button',{name:'Précédent',exact:true}).click();await page.getByText('Page 1 sur 3',{exact:true}).waitFor();
 await page.waitForFunction(id=>document.querySelector('.property-card')?.dataset.listingId===String(id),info.listing_ids[0]);
 console.log('Real server pagination passed; each page contains at most 18 cards.');
 assert(!member.assets.some(url=>/location-map-.*\.js$/.test(url)),'Map code stays deferred until the map approaches the viewport');

 const previousState=await (await fetch(info.qa_url+'/status')).json();
 await qa('slow');await open(page);await page.locator('.detail-progress.is-loading').waitFor();await loaded(page);
 const state=await (await fetch(info.qa_url+'/status')).json();assert.equal(state.collections[String(info.listing_ids[0])]-(previousState.collections[String(info.listing_ids[0])]||0),1);
 assert((state.requests[info.mode==='php'?`/api/listings/${info.listing_ids[0]}/updates`:`/api/listings/${info.listing_ids[0]}/events`]||0)>0);
 assert((await page.locator('.price-history .price-decrease').count())>0);
 const characteristics=page.locator('.detail-features');const publication=characteristics.locator('dl>div').filter({has:page.getByText('Date de publication',{exact:true})});assert.equal(await publication.locator('dd').innerText(),'26/10/2026 16:04:02');
 const readable=await characteristics.innerText();assert(!/activity_sector|external_ad_id|store_logo|INTERNAL-NETTY|2026-10-26T15/.test(readable));assert.match(readable,/Agence de démonstration/);assert.match(readable,/Exclusif/);assert.match(readable,/605 €/);assert.match(readable,/2e étage sur 4/);
 assert.equal(await characteristics.getByRole('link',{name:'Voir la visite',exact:true}).getAttribute('href'),'https://tour.example.test/demo');assert.match(await page.locator('.detail-dates').innerText(),/Publié le 26\/10\/2026 16:04:02/);
 await characteristics.scrollIntoViewIfNeeded();await page.screenshot({path:output+'/characteristics-desktop.png'});

 await page.locator('.listing-tracking').waitFor();assert.equal(await page.getByLabel('Vos notes',{exact:true}).inputValue(),'Demander le détail des charges.');
 assert.equal(await page.getByText('NOTE PRIVÉE ADMINISTRATEUR',{exact:true}).count(),0);
 await page.getByLabel('Vos notes',{exact:true}).fill('Contacter l’agence jeudi. Vérifier le montant des charges.');
 await page.getByLabel('Où en êtes-vous ?',{exact:true}).selectOption('visit');
 const dates=await page.evaluate(()=>{const pad=value=>String(value).padStart(2,'0');const input=date=>`${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;const visit=new Date(Date.now()+2*86400000);visit.setHours(17,30,0,0);const reminder=new Date(visit.getTime()-3600000);return {visit:input(visit),reminder:input(reminder),visitUtc:visit.toISOString(),reminderUtc:reminder.toISOString()}});
 await page.getByLabel('Date et heure de la visite',{exact:true}).fill(dates.visit);await page.getByRole('checkbox',{name:'Me rappeler cette visite',exact:true}).check();await page.getByLabel('Date et heure du rappel',{exact:true}).fill(dates.reminder);
 await page.getByLabel('Destination du trajet',{exact:true}).fill('Gare de démonstration');await page.getByLabel('Mode de transport',{exact:true}).selectOption('bike');await page.getByLabel('Durée du trajet (min)',{exact:true}).fill('18');await page.getByLabel('Stationnement vérifié',{exact:true}).selectOption('yes');await page.getByLabel('Accessibilité pour vos besoins',{exact:true}).selectOption('no');
 await page.getByRole('button',{name:'Enregistrer mon suivi',exact:true}).click();await page.getByText('Suivi enregistré',{exact:true}).waitFor();
 const note=(await api(ctx,`/api/listings/${info.listing_ids[0]}/note`)).data.tracking;
 assert.equal(note.commute_minutes,18);assert.equal(note.parking,'yes');assert.equal(note.accessibility,'no');
 assert.equal(note.stage,'visit');assert.equal(note.visit_at,dates.visitUtc.replace('.000Z','Z'));assert.equal(note.reminder_at,dates.reminderUtc.replace('.000Z','Z'));
 await page.locator('.location-map-slot').scrollIntoViewIfNeeded();await page.locator('.leaflet-overlay-pane path').waitFor();await page.getByText('Contour fictif',{exact:true}).waitFor();
 const shape=page.locator('.leaflet-overlay-pane path').first(),beforeZoom=await shape.getAttribute('d');await page.getByRole('button',{name:'Agrandir la carte',exact:true}).click();await page.waitForFunction(d=>document.querySelector('.leaflet-overlay-pane path')?.getAttribute('d')!==d,beforeZoom);await page.getByRole('button',{name:'Recentrer sur la zone',exact:true}).click();await page.locator('.location-map').focus();const beforePan=await page.locator('.leaflet-map-pane').getAttribute('style');await page.keyboard.press('ArrowRight');await page.waitForFunction(style=>document.querySelector('.leaflet-map-pane')?.getAttribute('style')!==style,beforePan);await page.getByRole('button',{name:'Recentrer sur la zone',exact:true}).click();await page.screenshot({path:output+'/location-area-desktop.png'});
 assert(member.assets.some(url=>/location-map-.*\.js$/.test(url)));assert.equal(await page.locator('.leaflet-marker-icon').count(),0);
 member.tiles.fail=true;for(let step=0;step<3;step++)await page.getByRole('button',{name:'Agrandir la carte',exact:true}).click();await page.locator('.map-tile-error').waitFor();assert(await shape.isVisible());member.tiles.fail=false;await page.getByRole('button',{name:'Recharger le fond de carte',exact:true}).click();await page.locator('.map-tile-error').waitFor({state:'hidden'});await page.getByRole('button',{name:'Recentrer sur la zone',exact:true}).click();
 await page.locator('.tracking-form').scrollIntoViewIfNeeded();await page.screenshot({path:output+'/tracking-desktop.png'});
 await page.getByLabel('Vos notes',{exact:true}).fill('Brouillon non enregistré');page.once('dialog',dialog=>dialog.dismiss());await page.getByRole('button',{name:'Fermer le détail',exact:true}).click();assert.equal(await page.locator('.detail-drawer').count(),1);
 page.once('dialog',dialog=>dialog.accept());await close(page);await open(page);await loaded(page);await page.getByLabel('Vos notes',{exact:true}).waitFor();assert.match(await page.getByLabel('Vos notes',{exact:true}).inputValue(),/Contacter l’agence/);await close(page);
 console.log('Live enrichment, per-publication price history, private notes, visit/reminder persistence and unsaved-draft protection passed.');

 await nav(page,'Mes suivis');await page.locator('.tracking-overview-row').first().waitFor();
 const trackedRow=page.locator('.tracking-overview-row').filter({has:page.getByRole('heading',{name:'Appartement lumineux avec balcon',exact:true})});
 await trackedRow.waitFor();assert.match(await trackedRow.innerText(),/Contacter l’agence/);assert.match(await trackedRow.innerText(),/Visite prévue/);
 await page.getByRole('group',{name:'Étape du suivi',exact:true}).getByRole('button',{name:/^Contacté/}).click();await page.getByRole('heading',{name:'Aucun suivi pour ces filtres',exact:true}).waitFor();
 await page.getByRole('group',{name:'Étape du suivi',exact:true}).getByRole('button',{name:/^Visite prévue/}).click();await trackedRow.waitFor();
 await page.getByRole('searchbox',{name:'Rechercher dans mes suivis',exact:true}).fill('agence');await trackedRow.waitFor();await page.waitForTimeout(350);
 await noOverflow(page);await page.screenshot({path:output+'/tracking-overview-desktop.png'});
 await trackedRow.getByRole('button',{name:'Ouvrir le suivi',exact:true}).click();await loaded(page);await page.getByLabel('Vos notes',{exact:true}).waitFor();
 await page.waitForFunction(()=>document.activeElement?.closest('.listing-tracking'));
 assert.match(await page.getByLabel('Vos notes',{exact:true}).inputValue(),/Contacter l’agence/);await close(page);
 console.log('Tracking overview, stage/note filters and direct editor focus passed.');

 await nav(page,'Mes favoris');await page.locator('.compare-choice').first().waitFor();
 await page.getByRole('checkbox',{name:`Comparer : Appartement lumineux avec balcon`,exact:true}).check();await page.getByRole('checkbox',{name:`Comparer : Studio au calme`,exact:true}).check();
 await page.getByRole('button',{name:'Comparer (2/4)',exact:true}).click();await page.getByRole('dialog',{name:'Comparez vos favoris.',exact:true}).waitFor();
 await page.locator('.comparison-table tbody').waitFor();assert.equal(await page.locator('.comparison-table thead th').count(),3);
 const dpe=page.getByRole('row').filter({has:page.getByRole('rowheader',{name:'DPE',exact:true})});assert.match(await dpe.innerText(),/C/);assert.match(await dpe.innerText(),/Non communiqué/);
 assert.match(await page.getByRole('row').filter({has:page.getByRole('rowheader',{name:'Votre trajet',exact:true})}).innerText(),/18 min à vélo/);assert.match(await page.getByRole('row').filter({has:page.getByRole('rowheader',{name:'Stationnement',exact:true})}).innerText(),/votre vérification/);
 const downloadPromise=page.waitForEvent('download');await page.getByRole('button',{name:'Exporter le comparatif CSV',exact:true}).click();const download=await downloadPromise;await download.saveAs(output+'/comparison.csv');const csv=await readFile(output+'/comparison.csv','utf8');assert.match(csv,/18 min à vélo/);assert.match(csv,/Stationnement/);
 await noOverflow(page);await page.screenshot({path:output+'/comparison-desktop.png'});await page.keyboard.press('Escape');await page.locator('.comparison-dialog').waitFor({state:'hidden'});

 await nav(page,'Mon compte');await page.getByLabel('Fuseau horaire',{exact:true}).waitFor();await page.getByLabel('Fuseau horaire',{exact:true}).fill('Europe/Paris');await page.getByRole('checkbox',{name:'Suspendre les envois à certaines heures',exact:true}).check();await page.getByLabel('Début',{exact:true}).fill('22:30');await page.getByLabel('Fin',{exact:true}).fill('07:30');await page.getByRole('checkbox',{name:'Recevoir un résumé quotidien sur Telegram',exact:true}).check();await page.getByLabel('Heure du résumé',{exact:true}).fill('08:45');await page.getByRole('checkbox',{name:'M’avertir des baisses de loyer de mes favoris et de mes recherches',exact:true}).uncheck();await page.getByRole('button',{name:'Enregistrer mes préférences',exact:true}).click();await page.getByText('Préférences enregistrées',{exact:true}).waitFor();
 await page.reload();await page.locator('.photo-open').first().waitFor();await nav(page,'Mon compte');await page.getByLabel('Heure du résumé',{exact:true}).waitFor();assert.equal(await page.getByLabel('Heure du résumé',{exact:true}).inputValue(),'08:45');assert.equal(await page.getByLabel('Début',{exact:true}).inputValue(),'22:30');assert.equal(await page.getByRole('checkbox',{name:'M’avertir des baisses de loyer de mes favoris et de mes recherches',exact:true}).isChecked(),false);
 await page.locator('.member-settings').scrollIntoViewIfNeeded();await page.screenshot({path:output+'/preferences-desktop.png'});
 await nav(page,'Alertes');await page.locator('.member-notifications li').first().waitFor();await page.getByRole('button',{name:/Marquer comme lue :/}).first().click();await page.getByText('0 non lue',{exact:true}).waitFor();
 console.log('Comparison, missing DPE/charges, persisted time-zone/quiet/digest settings and notification inbox passed.');

 const viewports=[{width:320,height:740},{width:390,height:844},{width:768,height:1024},{width:1024,height:768},{width:1440,height:1000},{width:844,height:390}];
 for(const theme of ['dark','light']){
  await page.evaluate(theme=>localStorage.setItem('immoalert.theme',theme),theme);await page.reload();await page.locator('.photo-open').first().waitFor();
  await keyboardSkip(page);
  for(const viewport of viewports){
   console.log(`Checking ${theme} ${viewport.width}x${viewport.height}`);
   await page.setViewportSize(viewport);await nav(page,'Mes favoris');await page.locator('.compare-choice').first().waitFor();
   if(viewport.width<=900){const rows=await page.locator('.mobile-bottom-nav button').evaluateAll(buttons=>buttons.map(button=>Math.round(button.getBoundingClientRect().top)));assert.equal(new Set(rows).size,1,'Mobile navigation stays on one row')}
   await page.getByRole('checkbox',{name:'Comparer : Appartement lumineux avec balcon',exact:true}).check();await page.getByRole('checkbox',{name:'Comparer : Studio au calme',exact:true}).check();await page.getByRole('button',{name:'Comparer (2/4)',exact:true}).click();await page.locator('.comparison-table tbody').waitFor();await noOverflow(page);
   assert(await page.getByRole('button',{name:'Fermer le comparateur',exact:true}).evaluate(element=>{const box=element.getBoundingClientRect();return box.x>=0&&box.right<=innerWidth+1&&box.y>=0&&box.bottom<=innerHeight+1}));
   if(viewport.width===390)await page.screenshot({path:output+`/comparison-${theme}-mobile.png`});
   await page.keyboard.press('Escape');await page.locator('.comparison-dialog').waitFor({state:'hidden'});
   await open(page);await loaded(page);await page.locator('.location-map-slot').scrollIntoViewIfNeeded();await page.locator('.leaflet-overlay-pane path').waitFor();await page.getByText('Contour fictif',{exact:true}).waitFor();await noOverflow(page);if(viewport.width===390)await page.screenshot({path:output+`/location-area-${theme}-mobile.png`});await page.locator('.listing-tracking').scrollIntoViewIfNeeded();await noOverflow(page);await close(page);
   await nav(page,'Mes suivis');await page.locator('.tracking-overview-row').first().waitFor();await noOverflow(page);
   if(viewport.width===390)await page.screenshot({path:output+`/tracking-overview-${theme}-mobile.png`});
   await nav(page,'Mon compte');await page.getByLabel('Fuseau horaire',{exact:true}).waitFor();await noOverflow(page);
   if(viewport.width===390){await page.locator('.member-settings').scrollIntoViewIfNeeded();await page.screenshot({path:output+`/preferences-${theme}-mobile.png`})}
  }
 }
 console.log('New member surfaces passed in both themes at 320, 390, 768, 1024, 1440 px and landscape.');

 await page.setViewportSize({width:1280,height:960});await nav(page,'Explorer');await page.locator('.photo-open').first().waitFor();
 await qa('bootstrap_delay',{seconds:5.2});
 await page.waitForFunction(()=>window.__pollProbe.active===1,{},{timeout:12000});await wait(1000);
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))});
 const hiddenCounts=await page.evaluate(()=>({...window.__pollProbe}));await wait(5500);const stillHidden=await page.evaluate(()=>({...window.__pollProbe}));assert.equal(stillHidden.bootstrap,hiddenCounts.bootstrap);assert.equal(stillHidden.inventory,hiddenCounts.inventory);
 await qa('bootstrap_delay',{seconds:0});await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'))});
 await page.waitForFunction(previous=>window.__pollProbe.bootstrap>previous,stillHidden.bootstrap,{timeout:1500});await wait(300);assert.equal(await page.evaluate(()=>window.__pollProbe.max),1);
 console.log('Bootstrap requests never overlap; hidden tabs stop requests and resume immediately.');

 assert.equal((await api(ctx,'/api/admin/performance')).response.status(),403);
 admin=await context(info.admin);await admin.page.locator('.admin-menu summary').click();await admin.page.getByRole('button',{name:'Sources & activité',exact:true}).click();await admin.page.getByRole('tab',{name:'Performance',exact:true}).click();await admin.page.getByRole('heading',{name:'Les temps réels du site.',exact:true}).waitFor();await admin.page.locator('.performance-panel tbody tr').first().waitFor();
 const metrics=(await api(admin.ctx,'/api/admin/performance')).data;assert(metrics.metrics.some(metric=>metric.name==='detail_visible'));assert(!JSON.stringify(metrics).includes(note.note));await admin.page.screenshot({path:output+'/performance-desktop.png'});
 await admin.page.getByRole('tab',{name:'Clés et quotas',exact:true}).click();
 const planInput=admin.page.getByLabel('Plan ID FlashProxy',{exact:true});await planInput.waitFor();await planInput.fill('demo-plan-selected');await admin.page.getByLabel('Plan ID FlashProxy 2 (facultatif)',{exact:true}).fill('demo-plan-second');
 await admin.page.getByRole('button',{name:'Enregistrer les plans',exact:true}).click();await admin.page.getByText('Plans FlashProxy enregistrés. Les prochaines connexions utilisent cette configuration.',{exact:true}).waitFor();
 const providers=(await api(admin.ctx,'/api/admin/providers')).data.providers;assert.deepEqual(providers.find(provider=>provider.id==='flashproxy').plan_ids,['demo-plan-selected','demo-plan-second']);
 await admin.page.reload();await admin.page.locator('.photo-open').first().waitFor();await admin.page.locator('.admin-menu summary').click();await admin.page.getByRole('button',{name:'Sources & activité',exact:true}).click();await admin.page.getByRole('tab',{name:'Clés et quotas',exact:true}).click();await planInput.waitFor();assert.equal(await planInput.inputValue(),'demo-plan-selected');
 await planInput.fill('');await admin.page.getByLabel('Plan ID FlashProxy 2 (facultatif)',{exact:true}).fill('');await admin.page.getByRole('button',{name:'Enregistrer les plans',exact:true}).click();await admin.page.getByText('Plans FlashProxy enregistrés. Les prochaines connexions utilisent cette configuration.',{exact:true}).waitFor();
 assert.equal((await api(admin.ctx,'/api/admin/providers')).data.providers.find(provider=>provider.id==='flashproxy').plan_id,'');
 await admin.page.screenshot({path:output+'/flashproxy-plan-desktop.png'});
 console.log('Administrator plan ID save, persistence and explicit automatic mode passed.');
 assert.deepEqual(failures,[]);await writeFile(output+'/result.json',JSON.stringify({mode:info.mode,status:'passed',viewports,themes:['dark','light'],metrics:metrics.metrics.length},null,2));
 console.log('Administrator median/p95 metrics are populated and private. All member UI checks passed.');
}catch(error){
 console.error(error);if(member)await member.page.screenshot({path:output+'/failure.png'}).catch(()=>{});throw error;
}finally{
 await member?.ctx.close();await admin?.ctx.close();await browser.close();
 if(demo){await qa('stop').catch(()=>{});if(demo.exitCode===null)await Promise.race([new Promise(done=>demo.once('exit',done)),wait(5000)]);if(demo.exitCode===null)demo.kill('SIGTERM')}
 else await qa('bootstrap_delay',{seconds:0});
}
