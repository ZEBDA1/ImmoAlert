/** One request at a time; hidden tabs stop both timers and active requests. */
export function startVisiblePolling(task:()=>Promise<unknown>,interval:number,cancel?:()=>void){
 let stopped=false,running=false,repeat=false;
 let timer:ReturnType<typeof setTimeout>|undefined;
 const tick=async()=>{
  if(stopped||document.hidden)return;
  if(running){repeat=true;return}
  running=true;clearTimeout(timer);
  try{await task()}finally{
   running=false;
   if(!stopped&&!document.hidden){const delay=repeat?0:interval;repeat=false;timer=setTimeout(()=>void tick(),delay)}
  }
 };
 const visibility=()=>{clearTimeout(timer);if(document.hidden){repeat=false;cancel?.()}else void tick()};
 document.addEventListener('visibilitychange',visibility);void tick();
 return()=>{stopped=true;clearTimeout(timer);document.removeEventListener('visibilitychange',visibility);cancel?.()};
}
