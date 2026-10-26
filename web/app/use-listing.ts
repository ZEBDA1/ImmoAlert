import {useEffect,useState} from 'react';
import {apiFetch,apiPath,phpTransport} from './api';
import type {Listing} from './dashboard';

export function useListing(initial:Listing,attempt:number){
 const [listing,setListing]=useState(initial),[loading,setLoading]=useState(true),[enriching,setEnriching]=useState(false),[error,setError]=useState('');
 useEffect(()=>{
  let disposed=false,controller:AbortController|undefined,stream:EventSource|undefined,timer:ReturnType<typeof setTimeout>|undefined,deadline:ReturnType<typeof setTimeout>|undefined;
  let inFlight:Promise<void>|null=null,polls=0,started=Date.now(),measurementStart=performance.now(),visibleMeasured=attempt>0,completionMeasured=false;
  const measure=(name:string)=>{
   if(document.hidden||disposed)return;
   requestAnimationFrame(()=>requestAnimationFrame(()=>{
    if(disposed||document.hidden)return;
    void apiFetch('/api/performance',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({samples:[{name,value_ms:Math.max(0,performance.now()-measurementStart)}]})}).catch(()=>{});
   }));
  };
  const shown=()=>{if(!visibleMeasured){visibleMeasured=true;measure('detail_visible')}};
  if(initial.source)shown();
  setListing(current=>current.id===initial.id?current:initial);setLoading(true);setEnriching(false);setError('');
  const stopWork=()=>{clearTimeout(timer);clearTimeout(deadline);stream?.close();stream=undefined;controller?.abort()};
  const fail=(message:string)=>{if(disposed||document.hidden)return;setError(message);setLoading(false);stopWork()};
  const apply=(data:{listing:Listing;enriching?:boolean})=>{
   if(disposed||document.hidden)return false;
   if(!data.listing)throw new Error('La fiche reçue est incomplète. Réessayez.');
   setListing(data.listing);setLoading(!!data.enriching);setEnriching(!!data.enriching);setError('');shown();
   if(!data.enriching){clearTimeout(deadline);if(!completionMeasured){completionMeasured=true;measure('enrichment_complete')}}
   return !!data.enriching;
  };
  const schedule=(mode:'initial'|'updates',delay:number)=>{clearTimeout(timer);if(!disposed&&!document.hidden)timer=setTimeout(()=>void fetchData(mode),delay)};
  const subscribe=()=>{
   stream?.close();
   const channel=new EventSource(apiPath(`/api/listings/${initial.id}/events`));stream=channel;
   channel.addEventListener('listing',event=>{if(stream!==channel)return;try{apply(JSON.parse((event as MessageEvent).data));channel.close();stream=undefined}catch(reason){fail((reason as Error).message)}});
   channel.addEventListener('failed',event=>{if(stream!==channel)return;let message='La mise à jour a été interrompue. Réessayez.';try{message=JSON.parse((event as MessageEvent).data).error||message}catch{}fail(message)});
   channel.onerror=()=>{if(stream!==channel||disposed||document.hidden)return;channel.close();stream=undefined;schedule('updates',500)};
  };
  async function fetchData(mode:'initial'|'updates'):Promise<void>{
   if(disposed||document.hidden)return;
   if(inFlight){await inFlight;if(disposed||document.hidden)return;return fetchData(mode)}
   const ownController=new AbortController();controller=ownController;
   const task=(async()=>{
    try{
     const suffix=mode==='updates'?'/updates':'';
     const response=await apiFetch(`/api/listings/${initial.id}${suffix}`,{signal:ownController.signal,cache:'no-store'}),data=await response.json();
     if(disposed||ownController.signal.aborted||document.hidden)return;
     if(!response.ok)throw new Error(data.error||'Les informations complémentaires sont indisponibles.');
     if(!apply(data))return;
     if(Date.now()-started>120000)throw new Error('La récupération prend plus de temps que prévu. Vous pouvez consulter les informations disponibles et réessayer.');
     if(data.updates_available){
      if(!phpTransport&&typeof EventSource!=='undefined'&&mode==='initial')subscribe();
      else schedule('updates',mode==='initial'?0:500);
     }else{
      const requested=typeof data.retry_after_ms==='number'&&Number.isFinite(data.retry_after_ms)?data.retry_after_ms:2000;
      schedule('initial',Math.min(10000,Math.max(2000,requested,Math.min(5000,2000+polls++*500))));
     }
    }catch(reason){if(!ownController.signal.aborted)fail((reason as Error).message)}
   })();
   inFlight=task;
   try{await task}finally{if(inFlight===task)inFlight=null}
  }
  const start=()=>{deadline=setTimeout(()=>fail('La récupération prend plus de temps que prévu. Vous pouvez consulter les informations disponibles et réessayer.'),120000);void fetchData('initial')};
  const visibility=()=>{if(document.hidden)stopWork();else{started=Date.now();measurementStart=performance.now();start()}};
  document.addEventListener('visibilitychange',visibility);if(!document.hidden)start();
  return()=>{disposed=true;stopWork();document.removeEventListener('visibilitychange',visibility)};
 },[initial.id,attempt]);
 return {listing,setListing,loading,enriching,error};
}
