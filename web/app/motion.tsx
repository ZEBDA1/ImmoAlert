import { useEffect, useState, type ReactNode } from "react";
import { AnimatePresence, m, useReducedMotion } from "motion/react";

export function useMotionSettings(){
 const reduced=useReducedMotion();
 const device=navigator as Navigator & {deviceMemory?:number};
 const lowEnd=(device.deviceMemory!==undefined&&device.deviceMemory<=2)||(device.deviceMemory===undefined&&device.hardwareConcurrency<=4);
 return {reduced:!!reduced,duration:reduced?0:lowEnd ? .18 : .26,distance:reduced?0:10};
}
export function useCompactLayout(){
 const [compact,setCompact]=useState(()=>window.matchMedia("(max-width: 700px)").matches);
 useEffect(()=>{
  const media=window.matchMedia("(max-width: 700px)");
  const update=()=>setCompact(media.matches);
  update();media.addEventListener("change",update);
  return()=>media.removeEventListener("change",update);
 },[]);
 return compact;
}
/** Keep focus and scroll-lock ownership until the matching exit has finished. */
export function usePresence<T>(value:T|null, milliseconds=220) {
 const [previous,setPrevious]=useState<T|null>(value);
 const reduced=useReducedMotion();
 useEffect(()=>{
  if(value!==null){setPrevious(value);return}
  const timer=setTimeout(()=>setPrevious(null),reduced?0:milliseconds);
  return ()=>clearTimeout(timer);
 },[value,milliseconds,reduced]);
 return {value:value??previous,exiting:value===null&&previous!==null};
}
export function Presence({show,children}:{show:boolean;children:ReactNode}){
 const {reduced}=useMotionSettings();
 return <AnimatePresence mode="wait" initial={false}>{show&&<m.div className="presence-overlay" key="panel" initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0,transition:{duration:reduced?0:.18}}} transition={{duration:reduced?0:.2}}>{children}</m.div>}</AnimatePresence>;
}
