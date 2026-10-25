/** The Plesk build reaches the Python API through its same-origin PHP gateway. */
export function apiFetch(path:string,options?:RequestInit){
 const php=(import.meta as ImportMeta & {env:Record<string,string>}).env.VITE_API_TRANSPORT==="php";
 return fetch(php?`/api/index.php?route=${encodeURIComponent(path)}`:path,options);
}
