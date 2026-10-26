import {useEffect,useRef,useState} from 'react';
import {ArrowRight,CalendarDays,ChevronLeft,ChevronRight,Loader2,MapPin,NotebookPen,RefreshCw,Search} from 'lucide-react';
import {apiFetch} from './api';
import {formatDateTime} from './dates';
import {startVisiblePolling} from './polling';
import ProgressiveImage from './progressive-image';
import type {Tracking} from './dashboard';

const stages={to_contact:'À contacter',contacted:'Contacté',visit:'Visite prévue',rejected:'Écarté'};
type Item={listing:{id:number;title:string;location:string;price:number|null;images:string[];surface_m2:number|null;rooms:number|null};tracking:Tracking;is_favorite:boolean;available:boolean;archived:boolean};
type Page={items:Item[];total:number;counts:Record<string,number>;upcoming_count:number;page:number;limit:number;next_page:number|null};
const money=new Intl.NumberFormat('fr-FR',{style:'currency',currency:'EUR',maximumFractionDigits:0});

export default function TrackingPanel({revision,onOpen,onFavorites}:{revision:number;onOpen:(id:number,title:string,restore:boolean)=>Promise<void>;onFavorites:()=>void}){
 const [query,setQuery]=useState(''),[debounced,setDebounced]=useState(''),[stage,setStage]=useState(''),[scope,setScope]=useState('all'),[upcoming,setUpcoming]=useState(false),[sort,setSort]=useState('upcoming'),[page,setPage]=useState(1);
 const [data,setData]=useState<Page|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState(''),[retry,setRetry]=useState(0),[opening,setOpening]=useState<number|null>(null);
 const alive=useRef(true);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 useEffect(()=>{const timer=setTimeout(()=>{setDebounced(query);setPage(1)},250);return()=>clearTimeout(timer)},[query]);
 useEffect(()=>{
  let current:AbortController|undefined,stopped=false;
  setLoading(true);setData(null);setError('');
  const stop=startVisiblePolling(async()=>{
   current=new AbortController();
   try{
    const params=new URLSearchParams({q:debounced,stage,scope,upcoming:upcoming?'1':'0',sort,page:String(page),limit:'20'});
    const response=await apiFetch(`/api/tracking?${params}`,{signal:current.signal,cache:'no-store'}),next=await response.json();
    if(!response.ok)throw new Error(next.error||'Vos suivis sont indisponibles.');
    if(!stopped&&!current.signal.aborted){setData(next);setError('')}
   }catch(reason){if(!stopped&&!current.signal.aborted)setError((reason as Error).message)}
   finally{if(!stopped&&!current.signal.aborted)setLoading(false)}
  },15000,()=>current?.abort());
  return()=>{stopped=true;stop()};
 },[debounced,stage,scope,upcoming,sort,page,revision,retry]);
 async function open(item:Item){setOpening(item.listing.id);try{await onOpen(item.listing.id,item.listing.title,!item.is_favorite)}catch(reason){if(alive.current)setError((reason as Error).message)}finally{if(alive.current)setOpening(null)}}
 function reset(){setQuery('');setDebounced('');setStage('');setScope('all');setUpcoming(false);setPage(1)}
 return <section className="tracking-overview" aria-labelledby="tracking-title">
  <div className="tracking-overview-heading"><div><h1 id="tracking-title">Mes suivis</h1><p>Vos notes, contacts et visites, réunis par logement.</p></div><button className="button outline" onClick={onFavorites}>Voir mes favoris<ArrowRight size={16}/></button></div>
  <div className="tracking-overview-filters">
   <label className="tracking-search"><Search size={18}/><span className="sr-only">Rechercher dans mes suivis</span><input type="search" value={query} maxLength={120} placeholder="Bien, ville ou note…" onChange={event=>setQuery(event.target.value)}/></label>
   <label>Afficher<select value={scope} onChange={event=>{setScope(event.target.value);setPage(1)}}><option value="all">Tous mes suivis</option><option value="active">Dans mes favoris</option><option value="archived">Archivés</option></select></label>
   <label>Trier par<select value={sort} onChange={event=>{setSort(event.target.value);setPage(1)}}><option value="upcoming">Visites à venir</option><option value="recent">Dernière modification</option></select></label>
  </div>
  <div className="tracking-overview-stages" role="group" aria-label="Étape du suivi">
   <button className={`button outline ${!stage?'selected':''}`} aria-pressed={!stage} onClick={()=>{setStage('');setPage(1)}}>Toutes les étapes{data&&<span>{Object.values(data.counts).reduce((a,b)=>a+b,0)}</span>}</button>
   {Object.entries(stages).map(([key,label])=><button key={key} className={`button outline ${stage===key?'selected':''}`} aria-pressed={stage===key} onClick={()=>{setStage(key);setPage(1)}}>{label}{data&&<span>{data.counts[key]||0}</span>}</button>)}
  </div>
  <div className="tracking-overview-summary"><p role="status">{data?`${data.total} logement${data.total!==1?'s':''}`:loading?'Chargement de vos suivis…':'Suivis indisponibles'}</p><label><input type="checkbox" checked={upcoming} onChange={event=>{setUpcoming(event.target.checked);setPage(1)}}/>Visites à venir{data?` (${data.upcoming_count})`:''}</label></div>
  {error&&<div className="member-error" role="alert">{error}<button className="text-button" onClick={()=>setRetry(value=>value+1)}><RefreshCw size={15}/>Réessayer</button></div>}
  {loading&&!data&&<div className="tracking-overview-loading" role="status"><Loader2 className="spin" size={20}/>Chargement des logements suivis…</div>}
  {data&&data.items.length===0&&<div className="tracking-overview-empty"><NotebookPen size={30}/><h2>{query||stage||scope!=='all'||upcoming?'Aucun suivi pour ces filtres':'Vos suivis commenceront ici'}</h2><p>{query||stage||scope!=='all'||upcoming?'Modifiez votre sélection pour retrouver un logement.':'Ajoutez un bien à vos favoris, puis notez vos échanges et vos visites depuis sa fiche.'}</p><button className="button outline" onClick={query||stage||scope!=='all'||upcoming?reset:onFavorites}>{query||stage||scope!=='all'||upcoming?'Effacer les filtres':'Voir mes favoris'}</button></div>}
  <div className="tracking-overview-list">{data?.items.map(item=>{
   const {listing,tracking}=item,visit=tracking.visit_at,overdue=visit&&new Date(visit).getTime()<Date.now();
   return <article className={`tracking-overview-row ${item.archived?'is-archived':''}`} key={listing.id}>
    <div className="tracking-overview-photo">{listing.images?.[0]&&item.available?<ProgressiveImage src={listing.images[0]} alt="" loading="lazy"/>:<NotebookPen size={26}/>}</div>
    <div className="tracking-overview-copy"><div className="tracking-overview-meta"><span className={`tracking-stage stage-${tracking.stage}`}>{stages[tracking.stage]}</span>{item.archived&&<span className="fine-print">Archivé{!item.available?' · annonce retirée':' · hors favoris'}</span>}</div>
     <h2>{listing.title}</h2>{item.available&&<p className="tracking-overview-location"><MapPin size={14}/>{listing.location||'Localisation non précisée'}{listing.price!==null&&<strong>{money.format(listing.price)}</strong>}</p>}
     {tracking.note&&<p className="tracking-overview-note">{tracking.note.slice(0,180)}{tracking.note.length>180?'…':''}</p>}
     {tracking.note.length>180&&<details className="tracking-full-note"><summary>Lire la note complète</summary><p>{tracking.note}</p></details>}
     {visit&&<p className="tracking-overview-date"><CalendarDays size={15}/>{overdue?'Visite passée':'Visite'} : <time dateTime={visit}>{formatDateTime(visit)}</time></p>}
     {tracking.reminder_at&&<p className="fine-print">Rappel : <time dateTime={tracking.reminder_at}>{formatDateTime(tracking.reminder_at)}</time></p>}
     <p className="fine-print">Mis à jour le {formatDateTime(tracking.updated_at)}</p>
    </div>
    <div className="tracking-overview-actions">{item.available?<><button className="button outline" disabled={opening!==null} onClick={()=>void open(item)}>{opening===listing.id?<Loader2 className="spin" size={15}/>:<ArrowRight size={15}/>} {item.is_favorite?'Ouvrir le suivi':'Reprendre le suivi'}</button>{!item.is_favorite&&<small>Ce bien sera remis dans vos favoris.</small>}</>:<p className="fine-print">Votre note reste conservée.</p>}</div>
   </article>;
  })}</div>
  {data&&(data.total>data.limit||data.page>1)&&<nav className="inventory-pagination" aria-label="Pages des suivis"><button className="button outline" disabled={data.page<=1||loading} onClick={()=>setPage(data.page-1)}><ChevronLeft size={15}/>Précédent</button><span>Page {data.page} sur {Math.max(1,Math.ceil(data.total/data.limit))}</span><button className="button outline" disabled={!data.next_page||loading} onClick={()=>setPage(data.page+1)}>Suivant<ChevronRight size={15}/></button></nav>}
 </section>;
}
