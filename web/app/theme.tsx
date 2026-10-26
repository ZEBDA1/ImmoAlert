import { useEffect, useState } from "react";
import { AnimatePresence, m, useReducedMotion } from "motion/react";
import { Moon, Sun } from "lucide-react";

type Theme="light"|"dark";
export function useTheme(){
 const [theme,setTheme]=useState<Theme>(()=>document.documentElement.dataset.theme==="light"?"light":"dark");
 useEffect(()=>{
  document.documentElement.dataset.theme=theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content",theme==="dark"?"#101418":"#f2f4f7");
  try{localStorage.setItem("immoalert.theme",theme)}catch{/* Theme still works when storage is unavailable. */}
 },[theme]);
 useEffect(()=>{const sync=(event:StorageEvent)=>{if(event.key==="immoalert.theme"&&(event.newValue==="light"||event.newValue==="dark"))setTheme(event.newValue)};window.addEventListener("storage",sync);return()=>window.removeEventListener("storage",sync)},[]);
 return {theme,toggleTheme:()=>setTheme(value=>value==="dark"?"light":"dark")};
}
export function ThemeToggle({theme,onToggle}:{theme:Theme;onToggle:()=>void}){
 const reduce=useReducedMotion();
 return <button className="icon-button theme-toggle" aria-label={theme==="dark"?"Passer en mode clair":"Passer en mode sombre"} title={theme==="dark"?"Mode clair":"Mode sombre"} onClick={onToggle}><AnimatePresence mode="wait" initial={false}><m.span key={theme} initial={{opacity:0,rotate:reduce?0:-20,scale:.85}} animate={{opacity:1,rotate:0,scale:1}} exit={{opacity:0,rotate:reduce?0:20,scale:.85}} transition={{duration:reduce?0:.15}}>{theme==="dark"?<Sun size={19}/>:<Moon size={19}/>}</m.span></AnimatePresence></button>;
}
