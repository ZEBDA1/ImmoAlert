/** The Plesk build reaches the Python API through its same-origin PHP gateway. */
export const phpTransport=(import.meta as ImportMeta & {env:Record<string,string>}).env.VITE_API_TRANSPORT==="php";
export function apiPath(path:string){return phpTransport?`/api/index.php?route=${encodeURIComponent(path)}`:path}
export function apiFetch(path:string,options?:RequestInit){return fetch(apiPath(path),options)}
