import type {Listing} from './dashboard';
const money=new Intl.NumberFormat('fr-FR',{style:'currency',currency:'EUR',maximumFractionDigits:0});
const missing='Non communiqué';
const modes:Record<string,string>={walk:'à pied',bike:'à vélo',car:'en voiture',transit:'en transports'};
const announced=(item:Listing,match:RegExp)=>{const value=Object.entries(item.features||{}).filter(([label])=>match.test(label)).map(([label,value])=>`${label} : ${value}`).join(' · ');return value?`Annonce : ${value}`:missing};
export const comparisonRows:{label:string;value:(item:Listing)=>string}[]=[
 {label:'Loyer mensuel',value:item=>item.price!==null?money.format(item.price):missing},
 {label:'Charges',value:item=>item.charges_included===true?'Comprises':item.charges_included===false?'Hors charges':missing},
 {label:'Surface',value:item=>item.surface_m2?`${item.surface_m2} m²`:missing},
 {label:'Loyer mensuel au m²',value:item=>item.price!==null&&item.surface_m2?`${(item.price/item.surface_m2).toLocaleString('fr-FR',{maximumFractionDigits:2})} €/m²`:missing},
 {label:'Pièces',value:item=>item.rooms!==null?String(item.rooms):missing},
 {label:'DPE',value:item=>Object.entries(item.features||{}).find(([label])=>/dpe|classe.?.?nergie|performance.*nerg/i.test(label))?.[1]||missing},
 {label:'Localisation',value:item=>item.location||missing},
 {label:'Votre trajet',value:item=>{const t=item.personal_criteria;if(t?.commute_minutes!=null)return `${t.commute_minutes} min${t.commute_mode?` ${modes[t.commute_mode]}`:''}${t.commute_destination?` · ${t.commute_destination}`:''} (relevé par vous)`;return t?.commute_destination?`${t.commute_destination} · durée à relever`:'À renseigner dans votre suivi'}},
 {label:'Stationnement',value:item=>item.personal_criteria?.parking==='yes'?'Possible (votre vérification)':item.personal_criteria?.parking==='no'?'Indisponible (votre vérification)':announced(item,/parking|stationnement|garage/i)},
 {label:'Accessibilité',value:item=>item.personal_criteria?.accessibility==='yes'?'Adaptée à vos besoins (votre vérification)':item.personal_criteria?.accessibility==='no'?'Non adaptée à vos besoins (votre vérification)':announced(item,/accessib|pmr|fauteuil/i)},
 {label:'Disponibilité',value:item=>item.freshness?.status==='available'?'Annonce observée':item.freshness?.status==='unavailable'?'Indisponibilité confirmée':'À confirmer'},
];
const cell=(value:string)=>`"${(/^\s*[=+\-@\t\r\n]/.test(value)?"'"+value:value).replaceAll('"','""')}"`;
export function comparisonCsv(items:Listing[]){return '\uFEFF'+[['Les critères',...items.map(item=>item.title||'Annonce')],...comparisonRows.map(row=>[row.label,...items.map(row.value)]),['Liens des publications',...items.map(item=>(item.publications||[{url:item.url}]).map(pub=>pub.url).join(' · '))]].map(row=>row.map(cell).join(';')).join('\r\n')}
