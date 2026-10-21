import {parseErrorDetail,type FieldError} from './validation';
import type {LoginResult,SessionInfo} from './types';
let tokenPromise:Promise<string>|undefined;
let serverMode=false;
let unauthorizedHandler:(()=>void)|undefined;
export class ApiError extends Error { status:number; fields:FieldError[]; code?:string; requestId?:string; body?:Record<string,unknown>; retryAfter?:number; constructor(message:string,status:number,fields:FieldError[]=[],code?:string,requestId?:string,body?:Record<string,unknown>) {super(message);this.status=status;this.fields=fields;this.code=code;this.requestId=requestId;this.body=body;} }
export async function checked(response:Response) {if (!response.ok) {let parsed=parseErrorDetail(response.status,null);let body:{code?:unknown;request_id?:unknown}|null=null;try {body=await response.json();parsed=parseErrorDetail(response.status,body);}catch {/* retain HTTP status */}
 const code=typeof body?.code==='string'?body.code:undefined;
 const requestId=typeof body?.request_id==='string'&&body.request_id?body.request_id:response.headers?.get?.('X-Request-ID')||undefined;
 const error=new ApiError(parsed.message,response.status,parsed.fields,code,requestId,body&&typeof body==='object'?body as Record<string,unknown>:undefined);
 const retry=Number(response.headers?.get?.('Retry-After'));
 if(Number.isFinite(retry)&&retry>0) error.retryAfter=retry;
 throw error;}return response;}
const OFFLINE='无法连接本地服务，请确认已启动';
export const SERVER_OFFLINE='无法连接服务，请检查网络后重试';
/** Server deployments (LEAD_MODE=server) use network wording for the offline message. */
export function setServerMode(on:boolean) {serverMode=on;}
/** For plain helpers outside React (errors, worker status): true on server deployments. */
export function isServerMode() {return serverMode;}
/** Called when any request is answered 401 (session ended); AuthGate shows the login page. */
export function setOnUnauthorized(handler?:()=>void) {unauthorizedHandler=handler;}
/** True while AuthGate handles 401 by showing the login page (login mode only). */
export function loginManaged() {return unauthorizedHandler!==undefined;}
function unauthorized() {resetSession();unauthorizedHandler?.();}
/** fetch rejects (TypeError) only when the service is unreachable. */
async function reach(input:string,init:RequestInit) {try {return await fetch(input,init);} catch(error) {if(error instanceof TypeError) throw new ApiError(serverMode?SERVER_OFFLINE:OFFLINE,0,[],'offline'); throw error;}}
export async function getSession():Promise<SessionInfo> {const response=await reach('/api/session',{credentials:'same-origin',cache:'no-store'}).then(checked);return response.json();}
async function token() {return tokenPromise ??= getSession().then(body=>{
 if(body.unavailable) throw new ApiError(body.unavailable.detail,503,[],body.unavailable.code);
 if(body.authenticated===false) throw new ApiError('请先登录',401,[],'unauthorized');
 if(typeof body.token !== 'string' || !body.token) throw new Error('本地会话令牌无效');return body.token as string;}).catch(error=>{tokenPromise=undefined;throw error;});}
export function resetSession() {tokenPromise=undefined;}
export async function request(path:string,init:RequestInit={},retried=false):Promise<Response> {
 let value:string;
 try {value=await token();} catch(error) {if(error instanceof ApiError&&error.status===401) unauthorized(); throw error;}
 const headers = new Headers(init.headers);headers.set('X-Local-Token',value);if(init.body) headers.set('Content-Type','application/json');
 const response=await reach(`/api${path}`,{...init,headers,credentials:'same-origin',cache:'no-store'});
 if(response.status === 401) {unauthorized();return checked(response);}
 if(response.status === 403) {
  resetSession();
  // A stale CSRF token (session rotated in another tab): fetch a fresh one and retry once.
  const code=await response.clone().json().then((b:{code?:unknown})=>b?.code,()=>undefined);
  if(code==='csrf_failed'&&!retried) return request(path,init,true);
 }
 return checked(response);}
/** POST /api/auth/login: not through request(): without a session token() would always fail. */
export async function login(username:string,password:string):Promise<LoginResult> {
 const response=await reach('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',cache:'no-store',body:JSON.stringify({username,password})}).then(checked);
 const body=await response.json() as LoginResult;
 tokenPromise=Promise.resolve(body.token);
 return body;}
/** POST /api/auth/logout; without a valid session the token is omitted (the server answers 204). */
export async function logout() {
 let value:string|undefined;
 try {value=await token();} catch(error) {if(!(error instanceof ApiError&&error.status===401)) {resetSession();throw error;}}
 try {await reach('/api/auth/logout',{method:'POST',headers:value?{'X-Local-Token':value}:{},credentials:'same-origin',cache:'no-store'}).then(checked);}
 finally {resetSession();}}
export async function api<T>(path:string,method='GET',body?:unknown):Promise<T> {const response=await request(path,{method,body:body === undefined ? undefined : JSON.stringify(body)});return response.json();}
/** POST /runs/{id}/relocate: retry city geometry and add the area searches of a text-only round. */
export async function relocateRun(id:string) {return api(`/runs/${encodeURIComponent(id)}/relocate`,'POST');}
export async function download(path:string,filename:string) {const response=await request(path);const blob=await response.blob();const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=filename;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
