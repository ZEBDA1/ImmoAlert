import {useEffect,useRef,useState} from 'react';
import {ArrowUpRight,Download,Loader2,X} from 'lucide-react';
import {apiFetch} from './api';
import type {Listing} from './dashboard';
import ProgressiveImage from './progressive-image';

import {comparisonRows,comparisonCsv} from './comparison-data';

export default function CompareDialog({ids,onClose}:{ids:number[];onClose:()=>void}){
 const dialog=useRef<HTMLDialogElement>(null),[items,setItems]=useState<Listing[]>([]),[loading,setLoading]=useState(true),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
 const key=ids.join(',');
 useEffect(()=>{const element=dialog.current,previous=document.activeElement as HTMLElement|null;if(element&&!element.open)element.showModal();const before=document.body.style.overflow;document.body.style.overflow='hidden';return()=>{element?.close();document.body.style.overflow=before;previous?.focus({preventScroll:true})}},[]);
 useEffect(()=>{const controller=new AbortController();setLoading(true);setError('');void(async()=>{try{const response=await apiFetch('/api/comparison?ids='+encodeURIComponent(key),{signal:controller.signal,cache:'no-store'}),data=await response.json();if(!response.ok)throw new Error(data.error||'Le comparateur est indisponible.');if(!controller.signal.aborted)setItems(data.items)}catch(reason){if(!controller.signal.aborted)setError((reason as Error).message)}finally{if(!controller.signal.aborted)setLoading(false)}})();return()=>controller.abort()},[key,attempt]);
 const rows=comparisonRows;
 function download(){const url=URL.createObjectURL(new Blob([comparisonCsv(items)],{type:'text/csv;charset=utf-8'})),anchor=document.createElement('a');anchor.href=url;anchor.download='comparatif-immoalert.csv';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
 return <dialog ref={dialog} className="comparison-dialog" aria-labelledby="comparison-title" onCancel={event=>{event.preventDefault();onClose()}} onClick={event=>{if(event.target===event.currentTarget){const bounds=event.currentTarget.getBoundingClientRect();if(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom)onClose()}}}>
  <header><div><h2 id="comparison-title">Comparez vos favoris.</h2><p>{ids.length} biens, les mêmes critères.</p></div><button className="icon-button" autoFocus aria-label="Fermer le comparateur" onClick={onClose}><X size={21}/></button></header>
  {loading&&<p className="comparison-status" role="status"><Loader2 size={18} className="spin"/>Préparation du comparatif…</p>}
  {error&&<div className="comparison-status member-error" role="alert"><p>{error}</p><button className="text-button" onClick={()=>setAttempt(value=>value+1)}>Réessayer</button></div>}
  {!loading&&!error&&<><div className="comparison-tools"><button className="text-button" onClick={download}><Download size={16}/>Exporter le comparatif CSV</button><span>Vos critères personnels restent privés.</span></div><div className="comparison-scroll" tabIndex={0} aria-label="Tableau comparatif, défilement horizontal"><table className="comparison-table"><caption className="sr-only">Comparaison du loyer, des charges, de la surface, du DPE et de la localisation de vos favoris.</caption><thead><tr><th scope="col">Les critères</th>{items.map(item=><th scope="col" key={item.id}><div className="comparison-photo">{item.images[0]&&<ProgressiveImage src={item.images[0]} alt={item.title}/>}</div><span>{item.title}</span></th>)}</tr></thead><tbody>{rows.map(row=><tr key={row.label}><th scope="row">{row.label}</th>{items.map(item=><td key={item.id}>{row.value(item)}</td>)}</tr>)}<tr><th scope="row">Publications</th>{items.map(item=><td key={item.id}>{(item.publications||[{source:item.source,url:item.url}]).map((pub,index)=><a key={index} href={/^https?:\/\//.test(pub.url)?pub.url:'#'} target="_blank" rel="noopener noreferrer">{pub.source}<ArrowUpRight size={14}/></a>)}</td>)}</tr></tbody></table></div><p className="comparison-note">Les informations absentes sont indiquées explicitement. Le loyer hors charges ne permet pas de connaître le coût mensuel total.</p></>}
 </dialog>;
}
