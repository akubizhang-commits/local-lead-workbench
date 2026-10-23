import {useEffect,useRef,useState,type FormEvent} from 'react';
import {ApiError,SERVER_OFFLINE,login} from './api';
import type {LoginResult} from './types';
export const LOGIN_TITLE='登录 · 寻迹线索工作台';
/** Chinese message for a failed login (AWS trial §2.9). */
export function loginError(error:unknown):string {
 if(!(error instanceof ApiError)) return error instanceof Error&&error.message?error.message:'登录未完成，请重试';
 if(error.status===0||error.code==='offline') return SERVER_OFFLINE;
 if(error.status===401) return '用户名或密码错误';
 if(error.status===422) return '请输入用户名和密码';
 if(error.status===429) return `登录尝试过多，请 ${Math.max(1,Math.ceil((error.retryAfter||60)/60))} 分钟后再试`;
 return error.message||'登录未完成，请重试';
}
export function LoginPage({notice,unavailable,initialError,onLogin}:{notice?:string;unavailable?:string;initialError?:string;onLogin:(result:LoginResult)=>void}) {
 const [username,setUsername]=useState('');
 const [password,setPassword]=useState('');
 const [error,setError]=useState(initialError||'');
 const [submitting,setSubmitting]=useState(false);
 const [failures,setFailures]=useState(0);
 const userRef=useRef<HTMLInputElement>(null);
 const passwordRef=useRef<HTMLInputElement>(null);
 useEffect(()=>{document.title=LOGIN_TITLE;userRef.current?.focus();},[]);
 // After a failed attempt (inputs enabled again): focus the cleared password field.
 useEffect(()=>{if(failures) passwordRef.current?.focus();},[failures]);
 async function submit(event:FormEvent) {
  event.preventDefault();
  if(submitting||unavailable) return;
  if(!username.trim()||!password) {setError('请输入用户名和密码');(username.trim()?passwordRef:userRef).current?.focus();return;}
  setSubmitting(true);setError('');
  try {onLogin(await login(username.trim(),password));}
  catch(e) {setError(loginError(e));setPassword('');setSubmitting(false);setFailures(n=>n+1);}
 }
 const message=unavailable||error;
 return <main className="login-page">
  <form className="login-card" onSubmit={submit} noValidate>
   <h1>登录寻迹线索工作台</h1>
   <p className="login-sub">请使用管理员为你创建的账号登录。</p>
   {notice&&!message&&<p className="login-notice" role="status">{notice}</p>}
   {message&&<div className="login-error" role="alert">{message}</div>}
   <label htmlFor="login-username">用户名</label>
   <input id="login-username" ref={userRef} name="username" autoComplete="username" value={username} onChange={e=>setUsername(e.target.value)} disabled={submitting}/>
   <label htmlFor="login-password">密码</label>
   <input id="login-password" ref={passwordRef} name="password" type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} disabled={submitting}/>
   <button type="submit" className="primary" disabled={submitting||!!unavailable}>{submitting?'正在登录…':'登录'}</button>
   <p className="login-foot">忘记密码请联系管理员重置。</p>
   <p className="login-foot"><a href="/about">了解产品 · 合作伙伴介绍</a></p>
  </form>
 </main>;
}
