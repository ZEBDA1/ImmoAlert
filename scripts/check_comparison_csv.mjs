/** Exercise exported CSV against spreadsheet injection and quoting hazards. */
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {resolve,dirname} from 'node:path';
import assert from 'node:assert/strict';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..'),require=createRequire(import.meta.url);
const ts=require(resolve(root,'web/node_modules/typescript'));
const source=await readFile(resolve(root,'web/app/comparison-data.ts'),'utf8');
const javascript=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const {comparisonCsv}=await import('data:text/javascript;base64,'+Buffer.from(javascript).toString('base64'));
const base={price:null,charges_included:null,surface_m2:null,rooms:null,location:'Rouen',features:{},url:'https://example.test/1'};
for(const title of ['=HYPERLINK("https://example.test")','+SUM(1,2)','-2+3','@SUM(A1:A2)','\t=SUM(1,2)','  =SUM(1,2)']){
 const csv=comparisonCsv([{...base,title}]);assert(csv.startsWith('\uFEFF'));assert(csv.split('\r\n')[0].includes(';"\''),'Untrusted cell receives a text prefix');
}
const csv=comparisonCsv([{...base,title:'Appartement; "Balcon"\ncalme',personal_criteria:{commute_minutes:18,commute_mode:'bike',commute_destination:'Gare',parking:'yes'}}]);
assert(csv.includes('"Appartement; ""Balcon""\ncalme"'));assert(csv.includes('18 min à vélo'));assert(csv.includes('Non communiqué'));assert(csv.includes('votre vérification'));
console.log('CSV passed: UTF-8, separators, quotes/newlines, explicit missing values, private criteria and formula injection protection.');
