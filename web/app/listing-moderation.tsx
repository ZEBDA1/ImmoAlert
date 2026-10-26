import { useEffect, useRef, useState } from "react";
import { Flag, Loader2, Trash2 } from "lucide-react";
import { AnimatePresence, m } from "motion/react";
import { useMotionSettings } from "./motion";

export function ConfirmListingRemoval({title,onCancel,onConfirm}:{title:string;onCancel:()=>void;onConfirm:()=>Promise<void>}){
 const dialog=useRef<HTMLDialogElement>(null),cancel=useRef<HTMLButtonElement>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState("");
 const motion=useMotionSettings();
 useEffect(()=>{
  const previous=document.activeElement as HTMLElement|null,node=dialog.current;
  const ownsScrollLock=!node?.closest('.detail-drawer'),before=document.body.style.overflow;
  if(ownsScrollLock)document.body.style.overflow="hidden";
  node?.showModal();cancel.current?.focus();
  return()=>{node?.close();if(ownsScrollLock)document.body.style.overflow=before;if(previous?.isConnected)previous.focus({preventScroll:true})};
 },[]);
 return <dialog ref={dialog} className="moderation-confirm" aria-labelledby="removal-title" aria-describedby="removal-description" onCancel={event=>{event.preventDefault();if(!busy)onCancel()}} onKeyDown={event=>event.stopPropagation()}>
  <m.div initial={{opacity:0,y:motion.reduced?0:8}} animate={{opacity:1,y:0}} transition={{duration:motion.duration}}>
   <span className="moderation-symbol"><Trash2 size={22}/></span><h2 id="removal-title">Supprimer cette annonce ?</h2><p className="moderation-property-title">{title}</p><p id="removal-description">La fiche et ses publications seront retirées du site et des favoris. Les alertes en attente seront annulées et les collectes ne la réafficheront pas.</p>
   {error&&<p className="moderation-error" role="alert">{error}</p>}
   <div className="modal-actions"><button ref={cancel} className="button outline" disabled={busy} onClick={onCancel}>Conserver l’annonce</button><button className="button danger-button" disabled={busy} onClick={async()=>{setBusy(true);setError("");try{await onConfirm()}catch(error){setError((error as Error).message);setBusy(false)}}}>{busy?<Loader2 size={16} className="spin"/>:<Trash2 size={16}/>}Supprimer du site</button></div>
  </m.div>
 </dialog>;
}

export default function ListingModeration({isAdmin,status,title,onReport,onRemove}:{isAdmin:boolean;status?:string;title:string;onReport:(note:string)=>Promise<void>;onRemove:()=>Promise<void>}){
 const motion=useMotionSettings();
 const [open,setOpen]=useState(false),[note,setNote]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState(""),[confirm,setConfirm]=useState(false);
 const trigger=useRef<HTMLButtonElement>(null),field=useRef<HTMLTextAreaElement>(null);
 useEffect(()=>{if(open)field.current?.focus()},[open]);
 return <section className="detail-report"><h3>L’annonce a disparu du portail ?</h3><p>Signalez-la pour qu’un administrateur vérifie sa disponibilité.</p>
  {status?<p className="report-success" role="status"><Flag size={16}/>{status==="open"?"Signalement envoyé. Un administrateur va le vérifier.":"Votre signalement a été traité."}</p>:<>
   {!open&&<button ref={trigger} className="text-button" onClick={()=>setOpen(true)}><Flag size={16}/>Signaler une annonce supprimée</button>}
   <AnimatePresence mode="wait" initial={false}>{open&&<m.form className="moderation-form" key="report-form" initial={{opacity:0,y:motion.reduced?0:6}} animate={{opacity:1,y:0}} exit={{opacity:0}} transition={{duration:motion.duration}} onSubmit={async event=>{event.preventDefault();setBusy(true);setError("");try{await onReport(note);setOpen(false)}catch(error){setError((error as Error).message)}finally{setBusy(false)}}}>
    <label>Précisions (facultatif)<textarea ref={field} rows={3} maxLength={500} value={note} onChange={event=>setNote(event.target.value)} placeholder="Ex. le lien indique que le bien n’est plus disponible."/></label><span className="fine-print">500 caractères maximum. Le signalement ne supprime pas automatiquement le bien.</span>
    {error&&<p className="moderation-error" role="alert">{error}</p>}
    <div className="modal-actions"><button className="button outline" type="button" disabled={busy} onClick={()=>{setOpen(false);requestAnimationFrame(()=>trigger.current?.focus())}}>Annuler</button><button className="button primary" disabled={busy}>{busy?<Loader2 className="spin" size={16}/>:<Flag size={16}/>}Envoyer le signalement</button></div>
   </m.form>}</AnimatePresence>
  </>}
  {isAdmin&&<div className="detail-admin-removal"><button className="text-button moderation-remove" onClick={()=>setConfirm(true)}><Trash2 size={16}/>Supprimer cette annonce du site</button></div>}
  {confirm&&<ConfirmListingRemoval title={title} onCancel={()=>setConfirm(false)} onConfirm={onRemove}/>}
 </section>;
}
