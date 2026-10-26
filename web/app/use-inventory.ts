import {useCallback,useEffect,useRef,useState} from 'react';
import {apiFetch} from './api';
import {startVisiblePolling} from './polling';
import type {Listing} from './dashboard';

export type InventoryResult={items:Listing[];total:number;page:number;limit:number;next_page:number|null};
const empty:InventoryResult={items:[],total:0,page:1,limit:18,next_page:null};

export function useInventory(userId:number|undefined,criteria:string,enabled:boolean){
 const [data,setData]=useState<InventoryResult>(empty),[loading,setLoading]=useState(false),[error,setError]=useState(''),[page,setPage]=useState(1);
 const [owner,setOwner]=useState<number|undefined>(undefined),[loadedKey,setLoadedKey]=useState('');
 const active=useRef<{controller:AbortController;promise:Promise<void>}|null>(null);
 const version=useRef(0),key=useRef('');
 const expected=`${userId||0}:${criteria}`;
 if(key.current!==expected){key.current=expected;version.current++}
 const fetchPage=useCallback(async(requestedPage:number)=>{
  if(!userId||!enabled||document.hidden)return;
  if(active.current){await active.current.promise;if(active.current)return}
  const ownVersion=version.current,controller=new AbortController();
  setLoading(true);
  const promise=(async()=>{
   try{
    const params=new URLSearchParams(criteria);params.set('page',String(requestedPage));params.set('limit','18');
    const response=await apiFetch('/api/inventory?'+params,{signal:controller.signal,cache:'no-store'}),result=await response.json();
    if(!response.ok)throw new Error(result.error||'Les annonces sont momentanément indisponibles.');
    if(!controller.signal.aborted&&ownVersion===version.current){setData(result);setOwner(userId);setLoadedKey(`${userId}:${criteria}`);setError('');if(!result.items.length&&requestedPage>1)setPage(Math.max(1,Math.ceil(result.total/result.limit)))}
   }catch(reason){if(!controller.signal.aborted&&ownVersion===version.current){setError((reason as Error).message);setLoadedKey(`${userId}:${criteria}`)}}
   finally{if(active.current?.controller===controller)active.current=null;if(ownVersion===version.current)setLoading(false)}
  })();
  active.current={controller,promise};await promise;
 },[userId,enabled,criteria]);
 useEffect(()=>{setPage(1)},[userId,criteria]);
 useEffect(()=>{
  if(!userId){setData(empty);return}
  if(!enabled)return;
  let stop:(()=>void)|undefined;
  const timer=setTimeout(()=>{stop=startVisiblePolling(()=>fetchPage(page),8000,()=>active.current?.controller.abort())},new URLSearchParams(criteria).get('q')?180:0);
  return()=>{clearTimeout(timer);stop?.();active.current?.controller.abort()};
 },[userId,enabled,criteria,page,fetchPage]);
 return {...(owner===userId?data:empty),loading:loading||!!userId&&enabled&&loadedKey!==expected,error,currentPage:page,setPage,refresh:()=>fetchPage(page)};
}
