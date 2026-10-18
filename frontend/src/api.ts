import {parseErrorDetail,type FieldError} from './validation';
let tokenPromise:Promise<string>|undefined;
export class ApiError extends Error { status:number; fields:FieldError[]; constructor(message:string,status:number,fields:FieldError[]=[]) {super(message);this.status=status;this.fields=fields;} }
export async function checked(response:Response) {if (!response.ok) {let parsed=parseErrorDetail(response.status,null);try {parsed=parseErrorDetail(response.status,await response.json());}catch {/* retain HTTP status */} throw new ApiError(parsed.message,response.status,parsed.fields);}return response;}
const OFFLINE='无法连接本地服务，请确认已启动';
/** fetch rejects (TypeError) only when the local service is unreachable. */
async function reach(input:string,init:RequestInit) {try {return await fetch(input,init);} catch(error) {if(error instanceof TypeError) throw new ApiError(OFFLINE,0); throw error;}}
async function token() {return tokenPromise ??= reach('/api/session',{credentials:'same-origin',cache:'no-store'}).then(checked).then(r=>r.json()).then(body=>{if(typeof body.token !== 'string' || !body.token) throw new Error('本地会话令牌无效');return body.token as string;}).catch(error=>{tokenPromise=undefined;throw error;});}
export function resetSession() {tokenPromise=undefined;}
export async function request(path:string,init:RequestInit={}) {const headers = new Headers(init.headers);headers.set('X-Local-Token',await token());if(init.body) headers.set('Content-Type','application/json');const response=await reach(`/api${path}`,{...init,headers,credentials:'same-origin',cache:'no-store'});if(response.status === 401 || response.status === 403) resetSession();return checked(response);}
export async function api<T>(path:string,method='GET',body?:unknown):Promise<T> {const response=await request(path,{method,body:body === undefined ? undefined : JSON.stringify(body)});return response.json();}
/** POST /runs/{id}/relocate: retry city geometry and add the area searches of a text-only round. */
export async function relocateRun(id:string) {return api(`/runs/${encodeURIComponent(id)}/relocate`,'POST');}
export async function download(path:string,filename:string) {const response=await request(path);const blob=await response.blob();const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=filename;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
