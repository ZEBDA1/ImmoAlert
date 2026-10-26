import { useState } from "react";
import { ArrowLeft, ArrowRight, Check, ChevronDown, Loader2, MapPin, Search, X } from "lucide-react";

export type SearchFilter={id?:number;name:string;city:string;postal_code:string;price_min:number|string;price_max:number|string;surface_min:number|string;rooms_min:number|string;property_type:string;budget_max_with_charges:number|string;radius_km:number|string;active?:boolean};
export default function SearchDialog({initial,premium,busy,onClose,onSave}:{initial:SearchFilter;premium:boolean;busy:boolean;onClose:()=>void;onSave:(value:SearchFilter)=>Promise<boolean>}) {
 const [value,setValue]=useState(initial),[step,setStep]=useState(0),[error,setError]=useState("");
 const update=(name:keyof SearchFilter,next:string)=>{setValue(v=>({...v,[name]:next}));setError("")};
 const location=[value.city,value.postal_code].filter(Boolean).join(" · ");
 async function submit(e:React.FormEvent<HTMLFormElement>){
  e.preventDefault();setError("");
  if(!step){
   if(!value.city.trim()&&!value.postal_code.trim()){setError("Indiquez une ville ou un code postal pour cibler votre recherche.");return}
   if(value.price_min!==""&&value.price_max!==""&&Number(value.price_min)>Number(value.price_max)){setError("Le budget minimum doit être inférieur au maximum.");return}
   if(premium&&!value.name.trim())setValue(v=>({...v,name:`${v.city||v.postal_code} · location`.slice(0,64)}));
   setStep(1);return;
  }
  try{if(await onSave(value))onClose()}catch(e){setError((e as Error).message)}
 }
 return <div className="modal-backdrop" onClick={e=>{if(e.target===e.currentTarget&&!busy)onClose()}}><section className="modal filter-modal" role="dialog" aria-modal="true" aria-labelledby="filter-title"><button className="modal-close icon-button" aria-label="Fermer" disabled={busy} onClick={onClose}><X size={20}/></button>
  <div className="dialog-heading"><span className="dialog-symbol"><Search size={22}/></span><h2 id="filter-title">{initial.id?"Affiner ma recherche":"Votre recherche commence ici."}</h2><p>{step?"Ajoutez ce qui compte pour vous. Ces critères sont facultatifs.":"Un lieu, un budget. Nous réunissons les annonces pour vous."}</p></div>
  <ol className="search-steps" aria-label="Étapes de création"><li aria-current={!step?"step":undefined} className={!step?"active":"complete"}><span>{step?<Check size={14}/>:"1"}</span>Lieu et budget</li><li aria-current={step?"step":undefined} className={step?"active":""}><span>2</span>Vos critères</li></ol>
  <p className="sr-only" role="status">Étape {step+1} sur 2 : {step?"vos critères":"lieu et budget"}.</p>
  <form onSubmit={submit}>
   <div className="search-step" key={step}>
    {!step?<><div className="form-grid"><label>Ville<input autoFocus placeholder="Ex. Rouen" maxLength={120} autoComplete="address-level2" value={value.city} onChange={e=>update("city",e.target.value)}/></label><label>Code postal<input placeholder="Ex. 76000" inputMode="numeric" pattern="[0-9]{5}" maxLength={5} autoComplete="postal-code" value={value.postal_code} onChange={e=>update("postal_code",e.target.value)}/></label><div className="full"><label>Budget maximum (€ / mois)<input aria-describedby="budget-hint" type="number" min="0" max="1000000" placeholder="Ex. 900" value={value.price_max} onChange={e=>update("price_max",e.target.value)}/></label><p className="field-help" id="budget-hint">Laissez vide si vous préférez explorer tous les budgets.</p></div></div>
     <details className="search-extra"><summary>Préciser le budget et la zone<ChevronDown size={16}/></summary><div className="form-grid"><label>Budget minimum (€ / mois)<input type="number" min="0" max="1000000" placeholder="Sans minimum" value={value.price_min} onChange={e=>update("price_min",e.target.value)}/></label><label>Rayon de recherche (km)<input type="number" min="0" max="300" placeholder="Facultatif" value={value.radius_km} onChange={e=>update("radius_km",e.target.value)}/></label><label className="full">Plafond charges comprises (€ / mois)<input type="number" min="0" max="1000000" placeholder="Facultatif" value={value.budget_max_with_charges} onChange={e=>update("budget_max_with_charges",e.target.value)}/></label></div></details>
    </>:<><div className="search-recap"><MapPin size={18}/><div><strong>{location}</strong><span>{value.price_max?`Jusqu’à ${new Intl.NumberFormat("fr-FR").format(Number(value.price_max))} € / mois`:"Tous les budgets"}</span></div><button type="button" className="text-button" onClick={()=>setStep(0)}>Modifier</button></div>
     <div className="form-grid"><label className="full">Type de logement<select value={value.property_type} onChange={e=>update("property_type",e.target.value)}><option value="">Tous les logements</option><option value="studio">Studio</option><option value="appartement">Appartement</option><option value="maison">Maison</option></select></label><label>Surface minimum (m²)<input type="number" min="0" max="100000" step="0.5" placeholder="Indifférent" value={value.surface_min} onChange={e=>update("surface_min",e.target.value)}/></label><label>Pièces minimum<select value={value.rooms_min} onChange={e=>update("rooms_min",e.target.value)}><option value="">Indifférent</option>{[1,2,3,4,5,6].map(n=><option key={n} value={n}>{n} pièce{n>1?"s":""}</option>)}</select></label>{premium&&<label className="full">Nom de la recherche<input required disabled={!!initial.id} maxLength={64} placeholder="Ex. Mon appartement à Rouen" value={value.name} onChange={e=>update("name",e.target.value)}/></label>}</div></>}
   </div>
   {error&&<p className="inline-error" role="alert">{error}</p>}
   <div className="modal-actions"><button type="button" className="button outline" disabled={busy} onClick={()=>step?setStep(0):onClose()}>{step?<><ArrowLeft size={16}/>Retour</>:"Annuler"}</button><button className="button primary" disabled={busy}>{busy?<Loader2 className="spin" size={16}/>:step?<Search size={16}/>:null}{step?initial.id?"Enregistrer et rechercher":"Lancer ma recherche":"Continuer"}{!step&&<ArrowRight size={16}/>}</button></div>
   <p className="search-footnote">{step?"Les portails sont consultés dès l’enregistrement.":"Vous pourrez ajuster vos critères à tout moment."}</p>
  </form>
 </section></div>;
}
