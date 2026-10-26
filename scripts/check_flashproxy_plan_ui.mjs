import {browserTools} from './browser.mjs';
/** Real-backend check of simultaneous plans, per-plan quotas and pending drafts. Fake data only. */
import {readFile,mkdtemp,mkdir,writeFile} from 'node:fs/promises';
import {spawn} from 'node:child_process';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {dirname,resolve} from 'node:path';
import assert from 'node:assert/strict';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..'),temporary=await mkdtemp('/tmp/tgbbot-plan-browser-'),file=temporary+'/session.json';
const args=[root+'/scripts/serve_member_demo.py','--output',file];if(process.env.MEMBER_PHP_FRONTEND)args.push('--php-frontend',process.env.MEMBER_PHP_FRONTEND);
const demo=spawn('python3',args,{cwd:root,stdio:['ignore','ignore','pipe']});let diagnostic='';demo.stderr.on('data',chunk=>diagnostic+=chunk);
let info,browser;
try{
 for(let i=0;i<150;i++){try{info=JSON.parse(await readFile(file,'utf8'));break}catch{}if(demo.exitCode!==null)throw new Error('Demo failed: '+diagnostic);await new Promise(done=>setTimeout(done,100))}
 assert(info,'Demo did not start');
 const {chromium}=await browserTools();
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1280,height:960},locale:'fr-FR',reducedMotion:'reduce'}),page=await context.newPage();page.setDefaultTimeout(12000);const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const path=value=>info.mode==='php'?'/api/index.php?route='+encodeURIComponent(value):value;
 const providers=async()=>{const data=await (await context.request.get(info.url+path('/api/admin/providers'))).json();return data.providers.find(provider=>provider.id==='flashproxy')};
 assert.equal((await context.request.post(info.url+path('/api/login'),{data:info.admin,headers:{Origin:info.url}})).status(),200);
 await page.route(url=>url.pathname==='/api/admin/providers'||url.searchParams.get('route')==='/api/admin/providers',async route=>{
  if(route.request().method()==='POST'&&route.request().postDataJSON()?.operation==='configure_plans'){
   const response=await route.fetch();await new Promise(done=>setTimeout(done,600));await route.fulfill({response});
  }else await route.continue();
 });
 async function admin(){
  const desktop=page.locator('.admin-menu summary');
  if(await desktop.isVisible()){await desktop.click();await page.getByRole('button',{name:'Sources & activité',exact:true}).click()}
  else{await page.getByRole('button',{name:'Ouvrir le menu',exact:true}).click();await page.getByRole('dialog',{name:'Navigation',exact:true}).getByRole('button',{name:'Sources & activité',exact:true}).click()}
  await page.getByRole('tab',{name:'Clés et quotas',exact:true}).click();await page.getByLabel('Plan ID FlashProxy 1',{exact:true}).waitFor();
 }
 await page.goto(info.url);await page.locator('.photo-open').first().waitFor();await admin();
 const first=page.getByLabel('Plan ID FlashProxy 1',{exact:true}),second=page.getByLabel('Plan ID FlashProxy 2 (facultatif)',{exact:true}),save=page.getByRole('button',{name:'Enregistrer les plans',exact:true});
 async function saved(){await page.getByText('Plans FlashProxy enregistrés. Les prochaines connexions utilisent cette configuration.',{exact:true}).waitFor();await page.waitForFunction(()=>document.querySelector('#flashproxy-plan-id')?.disabled===false)}
 await first.fill('demo-plan-one');await second.fill('demo-plan-two');await save.click();
 await page.waitForFunction(()=>document.querySelector('#flashproxy-plan-id')?.disabled===true&&document.querySelector('#flashproxy-plan-id-2')?.disabled===true);await saved();
 assert.deepEqual((await providers()).plan_ids,['demo-plan-one','demo-plan-two']);
 await page.reload();await page.locator('.photo-open').first().waitFor();await admin();assert.equal(await first.inputValue(),'demo-plan-one');assert.equal(await second.inputValue(),'demo-plan-two');
 await second.fill('demo-plan-one');await save.click();await page.getByText('Les deux plans doivent avoir des IDs différents.',{exact:true}).waitFor();assert.deepEqual((await providers()).plan_ids,['demo-plan-one','demo-plan-two']);
 await second.fill('demo-plan-two');await save.click();await saved();
 const seeded=await fetch(info.qa_url+'/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation:'provider_usage'})});assert.equal(seeded.status,200);
 await page.reload();await page.locator('.photo-open').first().waitFor();await admin();await page.locator('.ops-plan-quota').first().waitFor();assert.equal(await page.locator('.ops-plan-quota').count(),2);
 assert.deepEqual(await page.locator('.ops-plan-quota code').allTextContents(),['demo-plan-one','demo-plan-two']);assert.match(await page.locator('.ops-plan-quotas').innerText(),/2 GB/);assert.match(await page.locator('.ops-plan-quotas').innerText(),/4 GB/);
 const output=process.env.UI_QA_OUTPUT||temporary;await mkdir(output,{recursive:true});
 const viewports=[{width:320,height:740},{width:390,height:844},{width:768,height:1024},{width:1024,height:768},{width:1440,height:1000},{width:844,height:390}];
 for(const theme of ['dark','light']){
  await page.evaluate(theme=>localStorage.setItem('immoalert.theme',theme),theme);await page.reload();await page.locator('.photo-open').first().waitFor();await admin();
  for(const viewport of viewports){await page.setViewportSize(viewport);await first.scrollIntoViewIfNeeded();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Provider page overflows');
   if(viewport.width===390||viewport.width===1440){await page.screenshot({path:output+`/plans-${theme}-${viewport.width}.png`});await page.locator('.ops-plan-quotas').scrollIntoViewIfNeeded();await page.screenshot({path:output+`/quotas-${theme}-${viewport.width}.png`})}
  }
 }
 const originalKey=(await providers()).credentials[0].id;
 await second.fill('');await save.click();await saved();let result=await providers();assert.deepEqual(result.plan_ids,['demo-plan-one']);assert.equal(result.credentials[0].id,originalKey);assert.equal(result.credentials[0].quota.remaining,null);
 await first.fill('');await save.click();await saved();assert.deepEqual((await providers()).plan_ids,[]);
 assert.deepEqual(errors,[]);
 await writeFile(output+'/plan-result.json',JSON.stringify({status:'passed',mode:info.mode,checks:['two-plans-active','both-pending-inputs-locked','reload-persistence','duplicate-rejected','per-plan-quotas','remove-second-preserves-first-and-key','explicit-automatic-plan'],themes:['dark','light'],viewports},null,2));
 console.log('Two FlashProxy plans and separate quotas passed:',info.mode);
}catch(error){throw error}finally{
 await browser?.close();
 if(info)await fetch(info.qa_url+'/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation:'stop'}),signal:AbortSignal.timeout(3000)}).catch(()=>{});
 if(demo.exitCode===null)demo.kill('SIGTERM');
}
