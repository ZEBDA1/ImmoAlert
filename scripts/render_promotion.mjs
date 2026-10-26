/** Render the editable, self-contained SVG promotion assets. No network requests. */
import {readFile,mkdir} from 'node:fs/promises';
import {dirname,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {browserTools} from './browser.mjs';
const index=process.argv.indexOf('--root');
const root=index>=0?resolve(process.argv[index+1]):resolve(dirname(fileURLToPath(import.meta.url)),'..');
const {chromium}=await browserTools(),browser=await chromium.launch({headless:true});
try{
 for(const [source,target] of [['docs/assets/banner.svg','docs/assets/banner.png'],['docs/assets/social-banner.svg','web/public/social.png']]){
  const svg=await readFile(resolve(root,source),'utf8');const dimensions=svg.match(/width="(\d+)" height="(\d+)"/);if(!dimensions)throw new Error('SVG dimensions missing');
  const page=await browser.newPage({viewport:{width:Number(dimensions[1]),height:Number(dimensions[2])},deviceScaleFactor:1});
  await page.route(/^https?:/,route=>route.abort());await page.goto(pathToFileURL(resolve(root,source)).href);await page.evaluate(()=>document.fonts.ready);
  await mkdir(dirname(resolve(root,target)),{recursive:true});await page.screenshot({path:resolve(root,target)});await page.close();console.log('Rendered '+target);
 }
}finally{await browser.close()}
