import { useEffect, useRef, useState } from "react";
import { Check, ExternalLink, Eye, EyeOff, KeyRound, Loader2, Pencil, Plus, RefreshCw, ShieldCheck, Trash2, X } from "lucide-react";
import { apiFetch } from "./api";
import { usePresence } from "./motion";

type PlanQuota={plan_id:string;status:string;detail:string;quota:{remaining:number|null;used:number|null;total:number|null;unit:string}};
type Credential={id:string;label:string;masked_key:string;fingerprint:string;enabled:boolean;origin:string;status:string;detail:string;plan_quotas?:PlanQuota[];checked_at:string|null;last_used_at:string|null;quota:{remaining:number|null;used:number|null;total:number|null;unit:string;reset_at:string|null}};
type Provider={id:string;name:string;enabled:boolean;detail:string;documentation_url:string;plan_id?:string;plan_ids?:string[];credentials:Credential[]};
const date=(v:string|null)=>v?new Date(v).toLocaleString("fr-FR",{dateStyle:"short",timeStyle:"short"}):"Pas encore";
const quantity=(n:number|null,unit:string)=>n===null?"Non communiqué":`${new Intl.NumberFormat("fr-FR",{maximumFractionDigits:2}).format(n)} ${unit==="credits"?"crédits":unit}`;
const statuses:Record<string,string>={unchecked:"À vérifier",ok:"Connectée",invalid:"Clé refusée",exhausted:"Quota épuisé",rate_limited:"Limite atteinte",error:"Vérification impossible",disabled:"Désactivée"};
function Toggle({enabled,label,disabled,onClick}:{enabled:boolean;label:string;disabled:boolean;onClick:()=>void}){return <button type="button" className={`toggle ${enabled?"on":""}`} role="switch" aria-checked={enabled} aria-label={label} disabled={disabled} onClick={onClick}><i/><span>{enabled?"Actif":"Inactif"}</span></button>}
function KeyEditor({provider,credential,busy,error,onClose,onSave}:{provider:Provider;credential:Credential|null;busy:boolean;error:string;onClose:()=>void;onSave:(payload:any)=>Promise<boolean>}){
 const [label,setLabel]=useState(credential?.label||""),[key,setKey]=useState(""),[enabled,setEnabled]=useState(credential?.enabled??true),[visible,setVisible]=useState(false);
 const ref=useRef<HTMLDivElement>(null), closeRef=useRef(onClose), busyRef=useRef(busy);closeRef.current=onClose;busyRef.current=busy;
 useEffect(()=>{
  const previous=document.activeElement as HTMLElement,overflow=document.body.style.overflow;document.body.style.overflow="hidden";
  ref.current?.querySelector<HTMLInputElement>("input")?.focus();
  function keyboard(e:KeyboardEvent){if(e.key==="Escape"&&!busyRef.current)closeRef.current();if(e.key!=="Tab")return;const els=ref.current?.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),a[href]');if(!els?.length)return;const first=els[0],last=els[els.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}
  document.addEventListener("keydown",keyboard);return()=>{document.removeEventListener("keydown",keyboard);document.body.style.overflow=overflow;previous?.focus()};
 },[]);
 return <div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget&&!busy)onClose()}}><div className="modal ops-key-editor" ref={ref} role="dialog" aria-modal="true" aria-labelledby="key-editor-title"><button className="modal-close icon-button" disabled={busy} aria-label="Fermer" onClick={onClose}><X size={19}/></button><div className="ops-key-symbol"><KeyRound size={24}/></div><p className="eyebrow">{provider.name}</p><h2 id="key-editor-title">{credential?"Modifier cette clé":"Connecter une clé API"}</h2><p>{credential?"Saisissez une nouvelle clé pour la remplacer, ou conservez le champ vide.":"Ajoutez la clé créée depuis votre compte fournisseur."}</p>
  {error&&<div className="notice error" role="alert">{error}</div>}
  <form onSubmit={async e=>{e.preventDefault();if(await onSave({provider:provider.id,operation:"save",...(credential?{key_id:credential.id}:{}),label,enabled,...(key?{api_key:key}:{})})){setKey("");onClose()}}}>
   <label>Nom de la clé<input required maxLength={60} placeholder="Ex. Compte principal" autoComplete="off" value={label} onChange={e=>setLabel(e.target.value)} disabled={busy}/></label>
   <label>{credential?"Nouvelle clé (facultatif)":"Clé API"}<span className="ops-key-input"><input type={visible?"text":"password"} required={!credential} minLength={8} maxLength={1024} autoComplete="new-password" spellCheck={false} placeholder={credential?"Laisser vide pour conserver la clé":"Collez votre clé ici"} value={key} onChange={e=>setKey(e.target.value)} disabled={busy}/><button type="button" className="icon-button" aria-label={visible?"Masquer la clé saisie":"Afficher la clé saisie"} onClick={()=>setVisible(!visible)}>{visible?<EyeOff size={18}/>:<Eye size={18}/>}</button></span></label>
   <label className="ops-checkbox"><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)} disabled={busy}/>Utiliser cette clé pour les collectes</label>
   <p className="fine-print"><ShieldCheck size={15}/> La clé est chiffrée sur le serveur. Après l’enregistrement, seule sa version masquée est consultable.</p>
   <a className="text-button" href={provider.documentation_url} target="_blank" rel="noreferrer">Documentation {provider.name}<ExternalLink size={13}/></a>
   <div className="modal-actions"><button type="button" className="button outline" onClick={onClose} disabled={busy}>Annuler</button><button className="button acid" disabled={busy}>{busy?<Loader2 className="spin" size={16}/>:<Check size={16}/>}Enregistrer la clé</button></div>
  </form>
 </div></div>;
}
function FlashProxyPlans({value,busy,onSave}:{value:string[];busy:boolean;onSave:(payload:any)=>Promise<boolean>}){
 const source=JSON.stringify(value);
 const [draft,setDraft]=useState([value[0]||"",value[1]||""]),[dirty,setDirty]=useState(false);
 useEffect(()=>{if(!dirty){const values=JSON.parse(source);setDraft([values[0]||"",values[1]||""])}},[source,dirty]);
 return <form className="ops-plan-form" onSubmit={async event=>{event.preventDefault();if(await onSave({provider:'flashproxy',operation:'configure_plans',plan_ids:draft.map(value=>value.trim()).filter(Boolean)}))setDirty(false)}}>
  <div className="ops-plan-inputs">{draft.map((value,index)=><label key={index} htmlFor={index===0?'flashproxy-plan-id':'flashproxy-plan-id-2'}>Plan ID FlashProxy {index+1}{index===1?' (facultatif)':''}<input id={index===0?'flashproxy-plan-id':'flashproxy-plan-id-2'} disabled={busy} value={value} maxLength={128} autoComplete="off" spellCheck={false} aria-describedby="flashproxy-plan-help" placeholder={`Identifiant du plan ${index+1}`} onChange={event=>{setDraft(previous=>previous.map((value,i)=>i===index?event.target.value:value));setDirty(true)}}/></label>)}</div>
  <div className="ops-plan-fields"><button className="button outline" disabled={busy||!dirty}>{busy?<Loader2 className="spin" size={15}/>:<Check size={15}/>}Enregistrer les plans</button>{dirty&&<button type="button" className="text-button" disabled={busy} onClick={()=>{setDraft([value[0]||"",value[1]||""]);setDirty(false)}}>Annuler</button>}</div>
  <p id="flashproxy-plan-help" className="fine-print">Les deux plans utilisent la même clé API et se partagent les connexions en alternance. Laissez les deux champs vides pour sélectionner automatiquement votre unique plan actif.</p>
 </form>;
}
export default function ProviderPanel({paused,onChange}:{paused:boolean;onChange:()=>Promise<void>}){
 const [providers,setProviders]=useState<Provider[]>([]),[loading,setLoading]=useState(true),[error,setError]=useState(""),[notice,setNotice]=useState(""),[busy,setBusy]=useState(""),[editor,setEditor]=useState<{provider:Provider;credential:Credential|null}|null>(null),[confirm,setConfirm]=useState("");
 const editorPanel=usePresence(editor);
 const alive=useRef(true);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 async function load(signal?:AbortSignal){const r=await apiFetch("/api/admin/providers",{cache:"no-store",signal}),d=await r.json();if(!r.ok)throw new Error(d.error||"La liste des clés est indisponible.");if(alive.current){setProviders(d.providers);setLoading(false)}}
 useEffect(()=>{
  if(paused&&!loading)return;
  const controller=new AbortController();let fetching=false;
  async function tick(){if(fetching)return;fetching=true;try{await load(controller.signal)}catch(e){if(!controller.signal.aborted){setError((e as Error).message);setLoading(false)}}finally{fetching=false}}
  void tick();const timer=paused?undefined:setInterval(tick,30000);return()=>{controller.abort();clearInterval(timer)};
 },[paused]);
 async function mutate(payload:any):Promise<boolean>{
  setBusy(payload.key_id||payload.provider);setError("");setNotice("");
  try{const r=await apiFetch("/api/admin/providers",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)}),d=await r.json();if(!r.ok)throw new Error(d.error||"La modification n’a pas pu être enregistrée.");await load();if(alive.current){setNotice(d.message||"Modification enregistrée.");setConfirm("")}await onChange();return true}
  catch(e){if(alive.current)setError((e as Error).message);return false}finally{if(alive.current)setBusy("")}
 }
 return <section className="ops-providers">
  <div className="ops-section-title"><div><h2>Des connexions sous contrôle.</h2><p>Modifiez vos clés sans redémarrer le bot. Les crédits sont vérifiés auprès des fournisseurs toutes les 5 minutes.</p></div><ShieldCheck size={25}/></div>
  {error&&<div className="notice error" role="alert">{error}</div>}{notice&&<div className="notice success" role="status"><Check size={16}/>{notice}</div>}
  {loading&&<div className="ops-provider-loading" role="status"><Loader2 className="spin" size={22}/>Chargement des connexions…</div>}
  {providers.map(provider=><article className="ops-provider" key={provider.id}>
   <div className="ops-provider-heading"><div className={`ops-provider-logo ${provider.id}`}><KeyRound size={23}/></div><div><h2>{provider.name}</h2><p>{provider.detail}</p></div><Toggle label={`Activer ${provider.name}`} enabled={provider.enabled} disabled={!!busy} onClick={()=>void mutate({operation:"toggle_provider",provider:provider.id,enabled:!provider.enabled})}/><button className="button outline" disabled={!!busy} onClick={()=>setEditor({provider,credential:null})}><Plus size={15}/>Ajouter une clé</button></div>
   {provider.id==="flashproxy"&&<FlashProxyPlans value={provider.plan_ids||(provider.plan_id?[provider.plan_id]:[])} busy={!!busy} onSave={mutate}/>}
   {!provider.enabled&&<p className="ops-provider-warning">Fournisseur désactivé : ses clés ne seront pas utilisées pour les prochaines collectes.</p>}
   {provider.id==="firecrawl"&&<p className="ops-provider-info">Les clés d’une même équipe partagent leurs crédits. Les soldes ci-dessous ne s’additionnent pas.</p>}
   {provider.credentials.length===0&&<div className="ops-no-keys"><KeyRound size={26}/><h3>Aucune clé enregistrée</h3><p>Ajoutez une clé pour activer le suivi des crédits de ce fournisseur.</p><button className="text-button" onClick={()=>setEditor({provider,credential:null})}><Plus size={15}/>Connecter ma première clé</button></div>}
   <div className="ops-key-list">{provider.credentials.map(key=>{
    const quota=key.quota,known=quota.remaining!==null&&quota.total!==null&&quota.total>0,percent=known?Math.max(0,Math.min(100,100*quota.remaining!/quota.total!)):null;
    return <section className={`ops-key ${!key.enabled?"is-disabled":""}`} key={key.id}><div className="ops-key-heading"><div><h3>{key.label}</h3><code>{key.masked_key}</code><span className={`badge ${key.status==="ok"?"good":["invalid","exhausted"].includes(key.status)?"danger":"warn"}`}>{!key.enabled?"Désactivée":statuses[key.status]||"À vérifier"}</span></div><Toggle label={`Utiliser ${key.label}`} enabled={key.enabled} disabled={!!busy} onClick={()=>void mutate({provider:provider.id,operation:"save",key_id:key.id,enabled:!key.enabled})}/></div>
     <p className="ops-key-detail">{key.detail}</p>
     <div className="ops-quota"><div><span>Solde disponible</span><strong className={quota.remaining===0?"exhausted":""}>{quantity(quota.remaining,quota.unit)}</strong></div><dl><div><dt>Utilisés</dt><dd>{quantity(quota.used,quota.unit)}</dd></div><div><dt>Allocation</dt><dd>{quantity(quota.total,quota.unit)}</dd></div></dl></div>
     {key.plan_quotas&&<div className="ops-plan-quotas" aria-label="Quotas par plan">{key.plan_quotas.map((plan,index)=><section className="ops-plan-quota" key={plan.plan_id}><div><h4>Plan {index+1}</h4><code>{plan.plan_id}</code><span className={`badge ${plan.status==='ok'?'good':plan.status==='exhausted'?'danger':'warn'}`}>{plan.status==='ok'?'Disponible':plan.status==='exhausted'?'Quota épuisé':'À vérifier'}</span></div><dl><div><dt>Disponibles</dt><dd>{quantity(plan.quota.remaining,plan.quota.unit)}</dd></div><div><dt>Utilisés</dt><dd>{quantity(plan.quota.used,plan.quota.unit)}</dd></div><div><dt>Allocation</dt><dd>{quantity(plan.quota.total,plan.quota.unit)}</dd></div></dl><p className="fine-print">{plan.detail}</p></section>)}</div>}
     {provider.id==='flashproxy'&&provider.plan_ids?.length===2&&!key.plan_quotas&&<p className="fine-print">Les quotas par plan apparaîtront après la prochaine vérification.</p>}
     {percent!==null?<div className={`ops-quota-track ${percent<15?"low":""}`} role="progressbar" aria-label={`Crédits disponibles pour ${key.label}`} aria-valuenow={Math.round(percent)} aria-valuemin={0} aria-valuemax={100}><i style={{transform:`scaleX(${percent/100})`}}/></div>:<p className="ops-quota-unknown">{quota.remaining===null?"Le fournisseur n’a pas communiqué de solde vérifiable.":"Le fournisseur ne communique pas d’allocation totale."}</p>}
     <div className="ops-key-times"><span>Vérification<strong>{date(key.checked_at)}</strong></span><span>Dernier appel<strong>{date(key.last_used_at)}</strong></span>{quota.reset_at&&<span>Renouvellement annoncé<strong>{date(quota.reset_at)}</strong></span>}</div>
     <div className="ops-key-actions"><button className="button outline" disabled={!!busy} onClick={()=>void mutate({provider:provider.id,operation:"refresh",key_id:key.id})}>{busy===key.id?<Loader2 className="spin" size={14}/>:<RefreshCw size={14}/>}Vérifier le quota</button><button className="text-button" disabled={!!busy} onClick={()=>setEditor({provider,credential:key})}><Pencil size={14}/>Modifier</button><button className="text-button ops-delete" disabled={!!busy} onClick={()=>setConfirm(key.id)}><Trash2 size={14}/>Supprimer</button></div>
     {confirm===key.id&&<div className="ops-delete-confirm" role="alert"><p>Supprimer « {key.label} » ? Cette clé ne sera plus utilisée.</p><div><button className="button outline" disabled={!!busy} onClick={()=>setConfirm("")}>Conserver</button><button className="button ops-danger-button" disabled={!!busy} onClick={()=>void mutate({provider:provider.id,operation:"delete",key_id:key.id})}>Supprimer la clé</button></div></div>}
    </section>;
   })}</div>
  </article>)}
  {editorPanel.value&&<div className={`presence-overlay ${editorPanel.exiting?"is-exiting":""}`}><KeyEditor key={editorPanel.value.credential?.id||editorPanel.value.provider.id} provider={editorPanel.value.provider} credential={editorPanel.value.credential} busy={!!busy} error={error} onClose={()=>setEditor(null)} onSave={mutate}/></div>}
 </section>;
}
