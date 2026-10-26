import { apiFetch } from "./api";
import { useState } from "react";
import { ArrowRight, ArrowUpRight, Check, Eye, EyeOff, Loader2, LockKeyhole, MessageCircle, ShieldCheck, X } from "lucide-react";

export default function LoginDialog({onClose,onSuccess,onTelegram,pairUrl,busy,linking=false}:{onClose:()=>void;onSuccess:()=>Promise<void>;onTelegram:()=>void;pairUrl:string|null;busy:boolean;linking?:boolean}) {
  const [login,setLogin]=useState(""),[password,setPassword]=useState(""),[loading,setLoading]=useState(false),[error,setError]=useState(""),[visible,setVisible]=useState(false);
  async function submit(event:React.FormEvent) {
    event.preventDefault();setLoading(true);setError("");
    try {
      const response=await apiFetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({login,password})}),data=await response.json();
      if(!response.ok)throw new Error(data.error||"La connexion a échoué. Vérifiez vos identifiants.");
      setPassword("");await onSuccess();onClose();
    } catch(e) {setError((e as Error).message);} finally {setLoading(false);}
  }
  return <div className="modal-backdrop" onClick={onClose}><section className="modal login-modal" role="dialog" aria-modal="true" aria-labelledby="login-title" onClick={e=>e.stopPropagation()}>
    <button className="modal-close icon-button" aria-label="Fermer" onClick={onClose}><X size={19}/></button>
    <span className="modal-icon">{linking?<MessageCircle size={24}/>:<LockKeyhole size={23}/>}</span>
    <span className="eyebrow">{linking?"Vos alertes, où vous voulez":"Votre espace personnel"}</span>
    <h2 id="login-title">{linking?"Un lien avec Telegram":"Votre espace, au calme."}</h2>
    {!linking?<><p>Retrouvez vos recherches, vos annonces et vos favoris.</p><form onSubmit={submit} className="login-form">
      <label>Identifiant<input autoFocus autoComplete="username" required maxLength={40} placeholder="Votre identifiant" value={login} onChange={e=>setLogin(e.target.value)} spellCheck={false} autoCapitalize="none"/></label>
      <label>Mot de passe<span className="password-input"><input type={visible?"text":"password"} autoComplete="current-password" required maxLength={256} placeholder="Votre mot de passe" value={password} onChange={e=>setPassword(e.target.value)}/><button type="button" aria-label={visible?"Masquer le mot de passe":"Afficher le mot de passe"} onClick={()=>setVisible(!visible)}>{visible?<EyeOff size={17}/>:<Eye size={17}/>}</button></span></label>
      {error&&<p className="login-error" role="alert">{error}</p>}
      <button className="button primary wide" disabled={loading}>{loading?<Loader2 size={17} className="spin"/>:null}Se connecter {!loading&&<ArrowRight size={17}/>}</button>
    </form><p className="fine-print"><ShieldCheck size={13}/><span>Votre administrateur vous fournit vos identifiants. Contactez-le pour réinitialiser votre mot de passe.</span></p><div className="login-divider">Déjà autorisé sur Telegram ?</div></>:<p>Recevez les annonces qui vous correspondent dans Telegram. Vos recherches et favoris restent accessibles sur le site.</p>}
    <div className="login-telegram">{pairUrl?<><a className={`button ${linking?"primary":"outline"} wide`} href={pairUrl} target="_blank" rel="noopener noreferrer"><MessageCircle size={17}/> Ouvrir Telegram <ArrowUpRight size={16}/></a><div className="telegram-pending" role="status"><Loader2 size={16} className="spin"/><span>Dans le bot, appuyez sur « Démarrer », puis sur « Autoriser cette connexion ». Revenez ici : la liaison se termine automatiquement.</span></div></>:<button className={`button ${linking?"primary":"outline"} wide`} disabled={busy} onClick={onTelegram}>{busy?<Loader2 className="spin" size={16}/>:<MessageCircle size={16}/>} {linking?"Relier mon compte Telegram":"Continuer avec Telegram"}{!busy&&<ArrowUpRight size={15}/>}</button>}{linking&&<p><Check size={11}/> Cette liaison est facultative. Vous choisissez d’activer vos alertes.</p>}</div>
  </section></div>;
}
