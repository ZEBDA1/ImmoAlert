import { useCallback, useEffect, useState } from "react";
import { ArrowUpRight, Check, Flag, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { apiFetch } from "./api";
import { ConfirmListingRemoval } from "./listing-moderation";

type Report={id:number;reporter:string;note:string;status:string;created_at:string;reviewed_at:string|null;reviewed_by:string|null};
type Item={listing:{id:number;title:string;source:string;url:string;location:string;price:number|null;removed:boolean};reports:Report[];open_count:number};
type Data={items:Item[];pending_count:number;total_count:number;has_more:boolean};
const date=(value:string)=>new Date(value).toLocaleString("fr-FR",{dateStyle:"short",timeStyle:"short"});
const safeUrl=(value:string)=>/^https?:\/\//i.test(value)?value:"#";
const labels:Record<string,string>={open:"À vérifier",dismissed:"Annonce conservée",removed:"Annonce supprimée"};

export default function ReportsPanel({paused,onChange}:{paused:boolean;onChange:()=>Promise<void>}){
 const [data,setData]=useState<Data|null>(null),[error,setError]=useState(""),[notice,setNotice]=useState(""),[filter,setFilter]=useState("open"),[busy,setBusy]=useState<number|null>(null),[confirm,setConfirm]=useState<Item|null>(null);
 const load=useCallback(async()=>{const response=await apiFetch("/api/admin/reports",{cache:"no-store"});const body=await response.json();if(!response.ok)throw new Error(body.error||"Les signalements ne peuvent pas être chargés.");setData(body);setError("")},[]);
 useEffect(()=>{let active=true,pending=false;const refresh=async()=>{if(!active||pending)return;pending=true;try{await load()}catch(error){if(active)setError((error as Error).message)}finally{pending=false}};void refresh();const timer=paused?undefined:setInterval(refresh,8000);return()=>{active=false;clearInterval(timer)}},[paused,load]);
 async function moderate(item:Item,operation:"dismiss"|"remove"){
  setBusy(item.listing.id);setError("");setNotice("");
  try{const response=await apiFetch("/api/admin/reports",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({listing_id:item.listing.id,operation,confirmed:operation==="remove"})});const body=await response.json();if(!response.ok)throw new Error(body.error||"Cette action n’a pas pu être enregistrée.");setConfirm(null);setNotice(body.message);await load();await onChange()}
  finally{setBusy(null)}
 }
 const items=(data?.items||[]).filter(item=>filter==="open"?item.open_count>0:item.open_count===0);
 return <section className="reports-panel" aria-labelledby="reports-title">
  <div className="reports-heading"><div><h2 id="reports-title">Les signalements d’annonces</h2><p>{data?`${data.pending_count} signalement${data.pending_count===1?"":"s"} à vérifier` :"Chargement des signalements…"}</p></div><button className="icon-button" aria-label="Actualiser les signalements" onClick={()=>void load().catch(error=>setError(error.message))}><RefreshCw size={18}/></button></div>
  <p className="reports-help">Vérifiez l’annonce d’origine avant de décider de la conserver ou de la supprimer du site.</p>
  {error&&<p className="notice error" role="alert">{error}</p>}{notice&&<p className="notice success" role="status"><Check size={16}/>{notice}</p>}
  <label className="reports-filter">Afficher<select aria-label="Afficher les signalements" value={filter} onChange={event=>setFilter(event.target.value)}><option value="open">À vérifier</option><option value="closed">Traités</option></select></label>
  {!data&&!error&&<p className="detail-progress" role="status"><Loader2 size={16} className="spin"/>Récupération des signalements…</p>}
  {data&&!items.length&&<div className="reports-empty"><Flag size={24}/><h3>{filter==="open"?"Aucun signalement en attente":"Aucun signalement traité"}</h3><p>Les annonces signalées par les membres apparaissent ici.</p></div>}
  {items.map(item=><article className="report-item" key={item.listing.id}>
   <div className="report-item-heading"><div><span className={`badge ${item.listing.removed?"":"warn"}`}>{item.listing.removed?"Supprimée":item.open_count?`${item.open_count} à vérifier`:"Traitée"}</span><h3>{item.listing.title}</h3><p>{item.listing.location} · {item.listing.source}{item.listing.price!==null?` · ${item.listing.price.toLocaleString("fr-FR")} € / mois`:""}</p></div><a className="text-button" href={safeUrl(item.listing.url)} target="_blank" rel="noopener noreferrer">Voir l’annonce d’origine<ArrowUpRight size={16}/></a></div>
   <ul className="report-messages">{item.reports.map(report=><li key={report.id}><div><strong>{report.reporter}</strong><time dateTime={report.created_at}>{date(report.created_at)}</time><span>{labels[report.status]||report.status}</span></div><p>{report.note||"Ce membre indique que l’annonce n’est plus disponible sur le portail."}</p>{report.reviewed_at&&<small>Traité le {date(report.reviewed_at)}{report.reviewed_by?` par ${report.reviewed_by}`:""}</small>}</li>)}</ul>
   {item.open_count>0&&!item.listing.removed&&<div className="report-item-actions"><button className="button outline" disabled={busy!==null} onClick={()=>void moderate(item,"dismiss").catch(error=>setError(error.message))}>{busy===item.listing.id?<Loader2 size={16} className="spin"/>:<Check size={16}/>}Conserver l’annonce</button><button className="button outline moderation-remove" disabled={busy!==null} onClick={()=>setConfirm(item)}><Trash2 size={16}/>Supprimer du site</button></div>}
  </article>)}
  {data?.has_more&&<p className="fine-print">Les 500 signalements les plus récents sont affichés, avec priorité aux signalements en attente.</p>}
  {confirm&&<ConfirmListingRemoval title={confirm.listing.title} onCancel={()=>setConfirm(null)} onConfirm={()=>moderate(confirm,"remove")}/>}
 </section>;
}
