import {useEffect,useRef,useState,type KeyboardEvent} from 'react';
import {ArrowUpRight,LocateFixed,Minus,Plus,RefreshCw} from 'lucide-react';
import * as L from 'leaflet';
import type {Geometry} from 'geojson';
import 'leaflet/dist/leaflet.css';
import {apiFetch} from './api';
import type {Listing} from './dashboard';

type Area={kind:'commune'|'indicative'|'unavailable';center:[number,number]|null;geometry:Geometry|null;radius_m?:number|null;label:string;detail:string};
function provisional(listing:Listing):Area|null{
 const lat=listing.latitude,lon=listing.longitude;
 if(typeof lat!=='number'||typeof lon!=='number'||!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>85.0511||Math.abs(lon)>180)return null;
 const portal=listing.geo_precision==='portal';return {kind:'indicative',center:[lat,lon],geometry:null,radius_m:portal?500:3000,label:portal?'Secteur indicatif':'Repère communal indicatif',detail:portal?'Secteur indicatif autour du point du portail, sans adresse précise.':'Repère communal indicatif. Recherche du contour officiel ; ce cercle ne représente pas les limites de la commune.'};
}
export default function LocationMap({listing}:{listing:Listing}){
 const container=useRef<HTMLDivElement>(null),map=useRef<L.Map|null>(null),layer=useRef<L.TileLayer|null>(null),bounds=useRef<L.LatLngBounds|null>(null);
 const [area,setArea]=useState<Area|null>(null),[error,setError]=useState(''),[tileError,setTileError]=useState(''),[loadingTiles,setLoadingTiles]=useState(true),[attempt,setAttempt]=useState(0);
 useEffect(()=>{const controller=new AbortController();setArea(provisional(listing));setError('');void(async()=>{try{const response=await apiFetch(`/api/listings/${listing.id}/area`,{signal:controller.signal,cache:'no-store'}),data=await response.json();if(!response.ok)throw new Error(data.error||'La zone est indisponible.');if(!controller.signal.aborted)setArea(data.area)}catch(reason){if(!controller.signal.aborted)setError((reason as Error).message)}})();return()=>controller.abort()},[listing.id,listing.latitude,listing.longitude,listing.geo_precision,attempt]);
 useEffect(()=>{
  if(!container.current||!area?.center||area.kind==='unavailable')return;
  let disposed=false,frame=0,loaded=0,failed=0;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
  const instance=L.map(container.current,{zoomControl:false,scrollWheelZoom:false,attributionControl:true,zoomAnimation:!reduced,fadeAnimation:!reduced,markerZoomAnimation:false,inertia:!reduced,maxZoom:19,minZoom:3});map.current=instance;instance.setView(area.center,13,{animate:false});
  const shape=area.geometry?L.geoJSON(area.geometry,{style:{color:'#285ec3',weight:3,fillColor:'#457bd2',fillOpacity:.16}}):L.circle(area.center,{radius:area.radius_m||500,color:'#285ec3',weight:3,fillColor:'#457bd2',fillOpacity:.16});
  shape.addTo(instance);bounds.current=shape.getBounds();instance.fitBounds(bounds.current,{padding:[24,24],maxZoom:area.kind==='commune'?14:16,animate:false});
  const tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,referrerPolicy:'strict-origin-when-cross-origin',attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>'});layer.current=tiles;
  tiles.on('loading',()=>{loaded=0;failed=0;if(!disposed){setLoadingTiles(true);setTileError('')}});
  tiles.on('tileload',()=>{loaded++;if(!disposed)setLoadingTiles(false)});
  tiles.on('tileerror',()=>{failed++});
  tiles.on('load',()=>{if(!disposed){setLoadingTiles(false);if(failed)setTileError(loaded?'Certaines parties du fond de carte sont indisponibles.':'Le fond de carte est indisponible. La zone reste affichée.')}});
  tiles.addTo(instance);
  const media=matchMedia('(prefers-reduced-motion: reduce)'),motion=()=>{instance.stop();instance.options.zoomAnimation=!media.matches;instance.options.inertia=!media.matches};media.addEventListener('change',motion);
  const observer=new ResizeObserver(()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>instance.invalidateSize({animate:false}))});observer.observe(container.current);
  return()=>{disposed=true;cancelAnimationFrame(frame);observer.disconnect();media.removeEventListener('change',motion);instance.remove();map.current=null;layer.current=null;bounds.current=null};
 },[area]);
 const recenter=()=>{if(map.current&&bounds.current)map.current.fitBounds(bounds.current,{padding:[24,24],maxZoom:area?.kind==='commune'?14:16,animate:!matchMedia('(prefers-reduced-motion: reduce)').matches})};
 const search=`https://www.openstreetmap.org/search?query=${encodeURIComponent(listing.location||'')}`;
 const external=area?.center?`https://www.openstreetmap.org/#map=${area.kind==='commune'?12:15}/${area.center[0]}/${area.center[1]}`:search;
 const keyboard=(event:KeyboardEvent<HTMLDivElement>)=>{
  const direction:Record<string,[number,number]>={ArrowLeft:[-80,0],ArrowRight:[80,0],ArrowUp:[0,-80],ArrowDown:[0,80]};
  if(direction[event.key]&&matchMedia('(prefers-reduced-motion: reduce)').matches){event.preventDefault();event.stopPropagation();map.current?.panBy(direction[event.key],{animate:false})}
 };
 return <section className="detail-map location-area"><div className="extra-heading"><h3>Le quartier et ses alentours</h3>{area&&<span>{area.label}</span>}</div>
  {!area&&!error&&<div className="location-map-placeholder" role="status"><span className="skeleton" aria-hidden="true"/><p>Préparation de la zone…</p></div>}
  {error&&<div className="member-error" role="alert"><p>{error}</p><button className="text-button" onClick={()=>setAttempt(value=>value+1)}>Réessayer la localisation</button></div>}
  {area&&area.kind!=='unavailable'&&<div className="location-map-wrap">
   <div ref={container} className="location-map" role="region" aria-label={`Carte de la zone de ${listing.location||'ce bien'}`} tabIndex={0} onKeyDownCapture={keyboard}/>
   <div className="location-map-controls" aria-label="Contrôles de la carte">
    <button className="icon-button" aria-label="Agrandir la carte" onClick={()=>map.current?.zoomIn()}><Plus size={18}/></button>
    <button className="icon-button" aria-label="Réduire la carte" onClick={()=>map.current?.zoomOut()}><Minus size={18}/></button>
    <button className="icon-button" aria-label="Recentrer sur la zone" onClick={recenter}><LocateFixed size={18}/></button>
   </div>
   {loadingTiles&&<span className="map-loading-label" role="status">Fond de carte en cours de chargement…</span>}
  </div>}

  {tileError&&<div className="map-tile-error" role="status"><p>{tileError}</p><button className="text-button" onClick={()=>layer.current?.redraw()}><RefreshCw size={14}/>Recharger le fond de carte</button></div>}
  {area&&<p className="fine-print map-area-description">{area.detail}</p>}
  <a className="text-button" href={external} target="_blank" rel="noopener noreferrer">{area?.center?'Ouvrir la carte':'Rechercher cette zone'}<ArrowUpRight size={14}/></a>
 </section>;
}
