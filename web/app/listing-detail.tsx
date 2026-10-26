import {Component,lazy,Suspense,useEffect,useRef,useState,type ReactNode} from "react";
import { ArrowUpRight, Building2, Check, ChevronLeft, ChevronRight, Expand, Heart, MapPin, RefreshCw, X } from "lucide-react";
import { AnimatePresence, m } from "motion/react";
import { useCompactLayout, useMotionSettings } from "./motion";
import ProgressiveImage from "./progressive-image";
import ListingModeration from "./listing-moderation";
import type { Listing } from "./dashboard";
import {useListing} from './use-listing';
const ListingExtras=lazy(()=>import('./listing-extras'));

const names:Record<string,string>={leboncoin:"Leboncoin",seloger:"SeLoger",pap:"PAP",paruvendu:"ParuVendu",entreparticuliers:"Entreparticuliers",logicimmo:"Logic-Immo",rentola:"Rentola",ouestfranceimmo:"OuestFrance-Immo"};
const money=new Intl.NumberFormat("fr-FR",{style:"currency",currency:"EUR",maximumFractionDigits:0});
const safeUrl=(value:string)=>/^https?:\/\//i.test(value||"")?value:"#";
import {formatDateTime as date} from "./dates";

function Gallery({listing,pending}:{listing:Listing;pending:boolean}){
 const motion=useMotionSettings();
 const [direction,setDirection]=useState(1),[selected,setSelected]=useState<string|null>(null),[expanded,setExpanded]=useState(false);
 const photos=(listing.images||[]).filter(url=>safeUrl(url)!=="#");
 // Keep the selected photo when enrichment adds images, and recover if it disappears.
 const index=Math.max(0,photos.indexOf(selected||"")),photo=photos[index];
 const neighbor=photos.length>1?photos[(index+1)%photos.length]:undefined;
 useEffect(()=>{
  const connection=(navigator as Navigator&{connection?:{saveData?:boolean;effectiveType?:string}}).connection;
  if(!neighbor||connection?.saveData||['slow-2g','2g'].includes(connection?.effectiveType||''))return;
  const image=new Image();image.decoding='async';image.fetchPriority='low';image.src=neighbor;
  void image.decode().catch(()=>{});
  return()=>image.removeAttribute('src');
 },[neighbor]);
 const gallery=useRef<HTMLDivElement>(null),touch=useRef<{x:number;y:number}|null>(null);
 useEffect(()=>{
  if(!expanded)return;
  const previous=document.activeElement as HTMLElement|null;
  const buttons=()=>Array.from(gallery.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])")||[]).filter(el=>el.offsetParent!==null);
  buttons()[0]?.focus();
  const keyboard=(event:KeyboardEvent)=>{
   if(event.key==="Escape"){event.stopImmediatePropagation();setExpanded(false)}
   if(event.key==="Tab"){const items=buttons(),first=items[0],last=items.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus()}}
  };
  window.addEventListener("keydown",keyboard,true);return()=>{window.removeEventListener("keydown",keyboard,true);previous?.focus()};
 },[expanded]);
 function choose(next:number){if(!photos.length)return;const value=(next+photos.length)%photos.length;setDirection(next>=index?1:-1);setSelected(photos[value])}
 return <div ref={gallery} className={`detail-gallery ${expanded?"expanded":""}`} onKeyDown={event=>{if(photos.length>1&&["ArrowLeft","ArrowRight"].includes(event.key)){event.preventDefault();choose(index+(event.key==="ArrowLeft"?-1:1))}}}>
  <div className="gallery-stage" data-gallery-index={index}>
   <AnimatePresence mode="wait" initial={false}><m.div className="gallery-frame" key={photo||"empty"}
    initial={{opacity:0,x:motion.reduced?0:direction*12}} animate={{opacity:1,x:0}}
    exit={{opacity:0,x:motion.reduced?0:direction*-8}} transition={{duration:motion.reduced?0:.16}}
    onPointerDown={event=>{if(event.pointerType!=="mouse"){touch.current={x:event.clientX,y:event.clientY};event.currentTarget.setPointerCapture(event.pointerId)}}}
    onPointerCancel={()=>{touch.current=null}}
    onPointerUp={event=>{const start=touch.current;touch.current=null;if(!start||photos.length<2)return;const dx=event.clientX-start.x,dy=event.clientY-start.y;if(Math.abs(dx)>48&&Math.abs(dx)>Math.abs(dy)*1.4)choose(index+(dx<0?1:-1))}}>
    {photo?<ProgressiveImage src={photo} alt={`${listing.title} · photo ${index+1} sur ${photos.length}`} loading="eager" announce/>:pending?<div className="gallery-pending" aria-label="Chargement des photos" aria-busy="true"><span className="photo-skeleton" aria-hidden="true"/><Building2 size={38} strokeWidth={1.3} aria-hidden="true"/><span>Les photos arrivent…</span></div>:<div className="property-placeholder"><Building2 size={42} strokeWidth={1.3}/><span>Aucune photo disponible</span></div>}
   </m.div></AnimatePresence>
   {photos.length>0&&<span className="gallery-counter" aria-live="polite">{index+1} / {photos.length}</span>}
   {photos.length>1&&<><button className="gallery-arrow previous" aria-label="Photo précédente" onClick={()=>choose(index-1)}><ChevronLeft/></button><button className="gallery-arrow next" aria-label="Photo suivante" onClick={()=>choose(index+1)}><ChevronRight/></button></>}
   {photo&&<button className="gallery-expand" aria-label={expanded?"Réduire la galerie":"Agrandir la galerie"} aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>{expanded?<X size={19}/>:<Expand size={19}/>}</button>}
  </div>
  {photos.length>1&&<div className="gallery-thumbnails" aria-label="Toutes les photos">{photos.map((url,i)=><button key={url} aria-label={`Afficher la photo ${i+1}`} aria-pressed={index===i} className={index===i?"selected":""} onClick={()=>choose(i)}><img src={url} alt="" loading="lazy" decoding="async"/><span>{i+1}</span></button>)}</div>}
 </div>
}

const AreaMap=lazy(()=>import('./location-map'));
class MapBoundary extends Component<{children:ReactNode;location:string},{failed:boolean}>{
 state={failed:false};
 static getDerivedStateFromError(){return {failed:true}}
 render(){return this.state.failed?<section className="detail-map"><h3>Le quartier et ses alentours</h3><p className="fine-print" role="alert">La carte ne peut pas s’afficher sur cet appareil.</p><a className="text-button" href={`https://www.openstreetmap.org/search?query=${encodeURIComponent(this.props.location)}`} target="_blank" rel="noopener noreferrer">Ouvrir la zone sur OpenStreetMap<ArrowUpRight size={14}/></a></section>:this.props.children}
}

function PropertyMap({listing,pending}:{listing:Listing;pending:boolean}){
 const anchor=useRef<HTMLDivElement>(null),[visible,setVisible]=useState(false);
 useEffect(()=>{if(!anchor.current)return;if(!('IntersectionObserver' in window)){setVisible(true);return}const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){setVisible(true);observer.disconnect()}},{rootMargin:'200px'});observer.observe(anchor.current);return()=>observer.disconnect()},[]);
 const placeholder=<section className="detail-map"><h3>Le quartier et ses alentours</h3><div className="location-map-placeholder" aria-busy="true"><span className="skeleton" aria-hidden="true"/><p className="fine-print">{pending?'Recherche de la localisation…':'La zone se charge à l’approche de la carte.'}</p></div></section>;
 return <div ref={anchor} className="location-map-slot">{visible?<MapBoundary key={listing.id} location={listing.location}><Suspense fallback={placeholder}><AreaMap listing={listing}/></Suspense></MapBoundary>:placeholder}</div>;
}

function DescriptionSkeleton(){return <div className="description-skeleton" aria-hidden="true">{[0,1,2,3,4].map(line=><span className="skeleton" key={line}/>)}</div>}

function DetailLoading({enriching}:{enriching:boolean}){
 const [slow,setSlow]=useState(false);
 useEffect(()=>{const timer=setTimeout(()=>setSlow(true),8000);return()=>clearTimeout(timer)},[]);
 return <div className="detail-loading-content">
  <span className="detail-loading-mark" aria-hidden="true"><i/><i/><i/></span>
  <div className="detail-loading-copy"><strong>{enriching?"Récupération des compléments…":"Ouverture de la fiche…"}</strong><span>{slow?"Le portail met un peu plus de temps à répondre.":"Les informations disponibles restent consultables."}</span></div>
  <span className="detail-loading-track" aria-hidden="true"><i/></span>
 </div>
}

export default function ListingDetail({initial,focusTracking=false,onTrackingSaved,onClose,liked,disabled,onFavorite,onFavoriteState,isAdmin=false,reports=[],onReport,onRemove}:{initial:Listing;focusTracking?:boolean;onTrackingSaved?:()=>void;onClose:()=>void;liked:boolean;disabled:boolean;onFavorite:()=>void;onFavoriteState:(id:number,enabled:boolean)=>void;isAdmin?:boolean;reports?:{listing_id:number;status:string}[];onReport:(note:string)=>Promise<void>;onRemove:()=>Promise<void>}){
 const motion=useMotionSettings(),compact=useCompactLayout();
 const drawer=useRef<HTMLElement>(null);
 const [attempt,setAttempt]=useState(0);
 const {listing,setListing,loading,enriching,error}=useListing(initial,attempt);
 const previousLiked=useRef(liked);
 useEffect(()=>{if(typeof listing.is_favorite==='boolean'){onFavoriteState(initial.id,listing.is_favorite);if(listing.id!==initial.id)onFavoriteState(listing.id,listing.is_favorite)}},[initial.id,listing.id,listing.is_favorite,onFavoriteState]);
 useEffect(()=>{if(liked&&!previousLiked.current)setAttempt(value=>value+1);previousLiked.current=liked},[liked]);
 // Ownership follows the actual animated panel, rather than a timer in its parent.
 // This also handles closing and immediately opening a different listing.
 useEffect(()=>{
  const previous=document.activeElement as HTMLElement|null,before=document.body.style.overflow;
  const panel=drawer.current;
  const buttons=()=>Array.from(panel?.querySelectorAll<HTMLElement>('button:not([disabled]),a[href],input:not([disabled]),select:not([disabled])')||[]).filter(element=>element.offsetParent!==null);
  document.body.style.overflow="hidden";
  if(!panel?.contains(document.activeElement))buttons()[0]?.focus({preventScroll:true});
  const trap=(event:KeyboardEvent)=>{
   if(event.key!=="Tab"||panel?.querySelector('.detail-gallery.expanded,dialog[open]'))return;
   const items=buttons(),first=items[0],last=items.at(-1);
   if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}
   else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus()}
  };
  document.addEventListener("keydown",trap);
  return()=>{document.body.style.overflow=before;document.removeEventListener("keydown",trap);if(previous?.isConnected)previous.focus({preventScroll:true});else requestAnimationFrame(()=>{const heading=document.querySelector<HTMLElement>('.main h1');heading?.setAttribute('tabindex','-1');heading?.focus({preventScroll:true})})};
 },[]);
 const pubs=listing.publications?.length?listing.publications:[{source:listing.source,url:listing.url,price:listing.price}];
 const features=Object.entries(listing.features||{}),partial=!loading&&listing.details_status==="partial";
 const descriptionPending=loading&&!listing.description;
 return <div className="modal-backdrop detail-backdrop" onClick={event=>{if(event.target===event.currentTarget)onClose()}}>
  <m.section ref={drawer} className="detail-drawer" role="dialog" aria-modal="true" aria-labelledby="listing-title"
   initial={{x:motion.reduced||compact?0:32,y:motion.reduced||!compact?0:24}} animate={{x:0,y:0}}
   exit={{x:motion.reduced||compact?0:24,y:motion.reduced||!compact?0:16,transition:{duration:motion.reduced?0:.16}}}
   transition={{duration:motion.duration,ease:[.22,1,.36,1]}}>
  <header className="detail-topbar"><span>Détail du bien</span><button className="modal-close icon-button" aria-label="Fermer le détail" onClick={onClose}><X size={21}/></button></header>
  <Gallery key={listing.id} listing={listing} pending={loading}/>
  <div className="detail-body"><span className="eyebrow">Un bien, {pubs.length>1?`${pubs.length} portails`:names[listing.source]||listing.source}</span><h2 id="listing-title">{listing.title}</h2><p className="detail-location"><MapPin size={15}/>{listing.location}</p><div className="detail-price">{listing.price!==null?money.format(listing.price):"Prix à consulter"}{listing.price!==null&&<small> / mois</small>}</div><div className="detail-specs"><span>{listing.surface_m2?`${listing.surface_m2} m²`:"Surface à préciser"}</span><span>{listing.rooms?`${listing.rooms} pièce${listing.rooms>1?"s":""}`:"Pièces à préciser"}</span>{listing.charges_included!==null&&<span>{listing.charges_included?"Charges comprises":"Hors charges"}</span>}</div>
  <div className="detail-load-status" role="status" aria-live="polite" aria-atomic="true"><AnimatePresence mode="wait" initial={false}>
   <m.div key={loading?"loading":error||partial?"warning":"ready"} className={`detail-progress ${loading?"is-loading":""} ${error||partial?"warn":""} ${!loading&&!error&&!partial?"is-ready":""}`}
    initial={{opacity:0,y:motion.reduced?0:4}} animate={{opacity:1,y:0}} exit={{opacity:0}} transition={{duration:motion.reduced?0:.15}}>
    {loading?<DetailLoading enriching={enriching}/>:error||partial?<>{error||"Le portail n’a pas fourni d’informations supplémentaires. Les données déjà collectées restent disponibles."}</>:<><Check size={16} aria-hidden="true"/>Fiche chargée</>}
   </m.div>
  </AnimatePresence></div>
  {!loading&&<button className="text-button detail-retry" onClick={()=>setAttempt(a=>a+1)}><RefreshCw size={14}/> Recharger les informations</button>}
  <section className="detail-copy"><h3>La description du bien</h3><div className="detail-description-slot" aria-busy={descriptionPending}><AnimatePresence mode="wait" initial={false}>
   {descriptionPending?<m.div key="pending" exit={{opacity:0}} transition={{duration:motion.reduced?0:.12}}><DescriptionSkeleton/></m.div>:<m.p key={listing.description||"empty"} className="detail-description" initial={{opacity:0,y:motion.reduced?0:6}} animate={{opacity:1,y:0}} exit={{opacity:0}} transition={{duration:motion.reduced?0:.22,ease:[.22,1,.36,1]}}>{listing.description||"La description n’a pas pu être récupérée sur ce portail."}</m.p>}
  </AnimatePresence></div></section>
  {(features.length>0||loading)&&<section className="detail-features" aria-busy={loading&&features.length===0}><h3>Les caractéristiques</h3>{features.length?<m.dl key="features" initial={{opacity:0}} animate={{opacity:1}} transition={{duration:motion.reduced?0:.2}}>{features.map(([label,value])=><div key={label}><dt>{label}</dt><dd>{/^(?:https?):\/\//i.test(value)&&['Visite virtuelle','Vue de la rue','Barème des honoraires'].includes(label)?<a className="text-button" href={safeUrl(value)} target="_blank" rel="noopener noreferrer">{label==='Visite virtuelle'?'Voir la visite':label==='Vue de la rue'?'Voir la rue':'Consulter le barème'}<ArrowUpRight size={14}/></a>:value}</dd></div>)}</m.dl>:<div className="feature-skeleton" aria-hidden="true">{[0,1,2,3].map(i=><span key={i} className="skeleton"/>)}</div>}</section>}
  <PropertyMap listing={listing} pending={loading}/>
  <section className="detail-publications"><h3>{pubs.length>1?"Un même bien sur plusieurs portails":"Voir l’annonce d’origine"}</h3>{pubs.length>1&&<p className="fine-print">Ces annonces ont été regroupées comme un même bien. Vous recevez une seule alerte.</p>}<div>{pubs.map((p,i)=><a className="button outline" key={`${p.source}-${i}`} href={safeUrl(p.url)} target="_blank" rel="noopener noreferrer">{names[p.source]||p.source}{typeof p.price==="number"?` · ${money.format(p.price)}`:""} <ArrowUpRight size={16}/></a>)}</div></section>
  <div className="detail-actions"><m.button whileTap={motion.reduced?undefined:{scale:.98}} className="button acid" disabled={disabled} onClick={onFavorite}><Heart size={16} fill={liked?"currentColor":"none"}/>{liked?"Retirer de mes favoris":"Enregistrer ce bien"}</m.button></div><Suspense fallback={<p className="fine-print" role="status">Chargement de l’historique et du suivi…</p>}><ListingExtras listing={listing} liked={liked} pending={loading} focusTracking={focusTracking} onTracking={tracking=>{setListing(current=>({...current,tracking,tracking_stage:tracking.stage}));onTrackingSaved?.()}}/></Suspense><ListingModeration key={listing.id} title={listing.title} isAdmin={isAdmin} status={reports.find(report=>report.listing_id===listing.id)?.status} onReport={onReport} onRemove={onRemove}/><div className="detail-dates">{listing.published_at&&<p>Publié le {date(listing.published_at)}</p>}{listing.last_seen_at&&<p>Dernière observation : {date(listing.last_seen_at)}</p>}</div><p className="fine-print">{listing.inventory_status&&listing.inventory_status!=="active"?"Annonce conservée. Présence sur le portail à revérifier. ":""}Prix et disponibilité à vérifier auprès de l’annonceur.</p>
  </div></m.section></div>
}
