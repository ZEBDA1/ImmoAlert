import { apiFetch } from "./api";
import { useEffect, useState } from "react";
import { ArrowUpRight, ChevronLeft, ChevronRight, Expand, Heart, Loader2, MapPin, RefreshCw, X } from "lucide-react";
import type { Listing } from "./dashboard";

const names:Record<string,string>={leboncoin:"Leboncoin",seloger:"SeLoger",pap:"PAP",paruvendu:"ParuVendu",entreparticuliers:"Entreparticuliers",logicimmo:"Logic-Immo",rentola:"Rentola",ouestfranceimmo:"OuestFrance-Immo"};
const money=new Intl.NumberFormat("fr-FR",{style:"currency",currency:"EUR",maximumFractionDigits:0});
const safeUrl=(value:string)=>/^https?:\/\//i.test(value||"")?value:"#";
const date=(value:string)=>new Date(value).toLocaleString("fr-FR",{timeZone:"Europe/Paris",dateStyle:"medium",timeStyle:"short"});

function Gallery({listing}:{listing:Listing}){
 const [index,setIndex]=useState(0),[expanded,setExpanded]=useState(false),[failed,setFailed]=useState(false);
 const photos=listing.images||[],photo=photos[index];
 useEffect(()=>{setFailed(false)},[photo]);
 useEffect(()=>{if(!expanded)return;const escape=(event:KeyboardEvent)=>{if(event.key==="Escape"){event.stopImmediatePropagation();setExpanded(false)}};window.addEventListener("keydown",escape,true);return()=>window.removeEventListener("keydown",escape,true)},[expanded]);
 function move(delta:number){setIndex(i=>(i+delta+photos.length)%photos.length)}
 return <div className={`detail-gallery ${expanded?"expanded":""}`} onKeyDown={e=>{if(photos.length>1&&["ArrowLeft","ArrowRight"].includes(e.key)){e.preventDefault();move(e.key==="ArrowLeft"?-1:1)}}}>
  <div className="gallery-stage">
   {photo&&!failed?<img src={safeUrl(photo)} alt={`${listing.title} — photo ${index+1} sur ${photos.length}`} decoding="async" onError={()=>setFailed(true)}/>:<div className="property-placeholder"><span className="placeholder-cross">+</span><span>{photo?"CETTE PHOTO N’EST PLUS ACCESSIBLE":"AUCUNE PHOTO DISPONIBLE"}</span></div>}
   {photos.length>0&&<span className="gallery-counter" aria-live="polite">{index+1} / {photos.length}</span>}
   {photos.length>1&&<><button className="gallery-arrow previous" aria-label="Photo précédente" onClick={()=>move(-1)}><ChevronLeft/></button><button className="gallery-arrow next" aria-label="Photo suivante" onClick={()=>move(1)}><ChevronRight/></button></>}
   {photo&&<button className="gallery-expand" aria-label={expanded?"Réduire la galerie":"Agrandir la galerie"} aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>{expanded?<X size={19}/>:<Expand size={19}/>}</button>}
  </div>
  {photos.length>1&&<div className="gallery-thumbnails" aria-label="Toutes les photos">{photos.map((url,i)=><button key={url} aria-label={`Afficher la photo ${i+1}`} aria-pressed={index===i} className={index===i?"selected":""} onClick={()=>setIndex(i)}><img src={safeUrl(url)} alt={`Photo ${i+1}`} loading="lazy"/><span>{i+1}</span></button>)}</div>}
 </div>
}

function PropertyMap({listing}:{listing:Listing}){
 const lat=listing.latitude,lon=listing.longitude;
 const valid=typeof lat==="number"&&typeof lon==="number"&&Number.isFinite(lat)&&Number.isFinite(lon)&&Math.abs(lat)<=90&&Math.abs(lon)<=180;
 const approximate=listing.geo_precision!=="portal";
 if(!valid)return <section className="detail-map"><h3>LOCALISATION.</h3><p className="fine-print">{listing.location||"Adresse non précisée"}. Les coordonnées de ce bien ne sont pas disponibles.</p>{listing.location&&<a className="text-button" href={`https://www.openstreetmap.org/search?query=${encodeURIComponent(listing.location)}`} target="_blank" rel="noopener noreferrer">Rechercher cette zone <ArrowUpRight size={14}/></a>}</section>;
 const delta=approximate?.025:.008,bbox=[Math.max(-180,lon-delta),Math.max(-90,lat-delta),Math.min(180,lon+delta),Math.min(90,lat+delta)].join(",");
 const map=`https://www.openstreetmap.org/export/embed.html?bbox=${encodeURIComponent(bbox)}&layer=mapnik&marker=${lat},${lon}`;
 return <section className="detail-map"><h3>LOCALISATION.</h3><p className="map-precision"><MapPin size={14}/>{approximate?"Zone approximative · centre de la commune":"Position fournie par le portail · précision à confirmer"}</p><iframe title={`Carte de ${listing.location}`} src={map} loading="lazy" referrerPolicy="no-referrer"/><p className="fine-print">{approximate?"Le repère situe la commune. Il ne représente pas l’adresse exacte du bien.":"Le portail peut décaler le repère pour préserver l’adresse du bien."}</p><a className="text-button" href={`https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=${approximate?13:16}/${lat}/${lon}`} target="_blank" rel="noopener noreferrer">Ouvrir la carte <ArrowUpRight size={14}/></a></section>
}

export default function ListingDetail({initial,onClose,liked,disabled,onFavorite}:{initial:Listing;onClose:()=>void;liked:boolean;disabled:boolean;onFavorite:()=>void}){
 const [listing,setListing]=useState(initial),[loading,setLoading]=useState(true),[error,setError]=useState(""),[attempt,setAttempt]=useState(0);
 useEffect(()=>{
  const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
  setListing(initial);setLoading(true);setError("");
  async function load(){try{const response=await apiFetch(`/api/listings/${initial.id}`,{signal:controller.signal,cache:"no-store"}),data=await response.json();if(!response.ok)throw new Error(data.error||"Les informations complémentaires sont indisponibles.");setListing(data.listing);setLoading(!!data.enriching);if(data.enriching)timer=setTimeout(load,3000)}catch(e){if(!controller.signal.aborted){setError((e as Error).message);setLoading(false)}}}
  load();return()=>{controller.abort();clearTimeout(timer)}
 },[initial.id,attempt]);
 const pubs=listing.publications?.length?listing.publications:[{source:listing.source,url:listing.url}];
 return <div className="modal-backdrop detail-backdrop" onClick={onClose}><section className="detail-drawer" role="dialog" aria-modal="true" aria-labelledby="listing-title" onClick={e=>e.stopPropagation()}><button className="modal-close icon-button" aria-label="Fermer le détail" onClick={onClose}><X/></button>
  <Gallery key={listing.id} listing={listing}/>
  <div className="detail-body"><span className="eyebrow">UN BIEN / {pubs.length>1?`${pubs.length} PLATEFORMES`:names[listing.source]||listing.source}</span><h2 id="listing-title">{listing.title}</h2><p className="detail-location"><MapPin size={15}/>{listing.location}</p><div className="detail-price">{listing.price!==null?money.format(listing.price):"Prix à consulter"}<small> / mois</small></div><div className="detail-specs"><span>{listing.surface_m2?`${listing.surface_m2} M²`:"SURFACE N.C."}</span><span>{listing.rooms?`${listing.rooms} PIÈCE${listing.rooms>1?"S":""}`:"PIÈCES N.C."}</span>{listing.charges_included!==null&&<span>{listing.charges_included?"CHARGES COMPRISES":"HORS CHARGES"}</span>}</div>
  {loading&&<p className="detail-progress" role="status"><Loader2 size={16} className="spin"/> Récupération de la description, des photos et de la localisation…</p>}
  {(error||(!loading&&listing.details_status==="partial"))&&<p className="detail-progress warn" role="status">{error||"Le portail n’a pas fourni d’informations supplémentaires. Les données déjà collectées restent disponibles."}</p>}
  {!loading&&<button className="text-button detail-retry" onClick={()=>setAttempt(a=>a+1)}><RefreshCw size={13}/> Recharger les informations</button>}
  <section className="detail-copy"><h3>LE BIEN EN DÉTAIL.</h3><p className="detail-description">{listing.description||"La description n’a pas pu être récupérée sur ce portail."}</p></section>
  {Object.keys(listing.features||{}).length>0&&<section className="detail-features"><h3>CARACTÉRISTIQUES.</h3><dl>{Object.entries(listing.features||{}).map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></section>}
  <PropertyMap listing={listing}/>
  <section className="detail-publications"><h3>{pubs.length>1?"CE BIEN SUR LES PLATEFORMES.":"PUBLICATION D’ORIGINE."}</h3>{pubs.length>1&&<p className="fine-print">Ces annonces ont été regroupées comme un même bien. Tu reçois une seule alerte.</p>}<div>{pubs.map((p,i)=><a className="button outline" key={`${p.source}-${i}`} href={safeUrl(p.url)} target="_blank" rel="noopener noreferrer">{names[p.source]||p.source} <ArrowUpRight size={16}/></a>)}</div></section>
  <div className="detail-actions"><button className="button acid" disabled={disabled} onClick={onFavorite}><Heart size={16} fill={liked?"currentColor":"none"}/>{liked?"Retirer des repérages":"Repérer ce bien"}</button></div><div className="detail-dates">{listing.published_at&&<p>Publié le {date(listing.published_at)}</p>}{listing.last_seen_at&&<p>Dernière observation : {date(listing.last_seen_at)}</p>}</div><p className="fine-print">{listing.inventory_status&&listing.inventory_status!=="active"?"Annonce conservée. Présence sur le portail à revérifier. ":""}Prix et disponibilité à vérifier auprès de l’annonceur.</p>
  </div></section></div>
}
