/** Resolve an optional Playwright install without depending on a developer's machine. */
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
const require=createRequire(import.meta.url);
export async function browserTools(){
 const explicit=process.env.IMD_PLAYWRIGHT;
 if(explicit)return import(pathToFileURL(explicit).href);
 try{return await import(pathToFileURL(require.resolve('playwright',{paths:[fileURLToPath(new URL('../web/',import.meta.url)),fileURLToPath(new URL('../',import.meta.url))]})).href)}
 catch{throw new Error('Install Playwright for browser checks, or set IMD_PLAYWRIGHT to its index.mjs path.')}
}
