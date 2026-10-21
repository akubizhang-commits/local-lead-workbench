import {useCallback,useEffect,useRef,useState,type ReactNode} from 'react';
import {ApiError,SERVER_OFFLINE,getSession,logout,setOnUnauthorized,setServerMode} from './api';
import {LoginPage} from './LoginPage';
import {SessionContext,type SessionContextValue} from './session';
import type {SessionInfo} from './types';
export const SERVER_TITLE='寻迹 · 线索工作台';
export const SESSION_EXPIRED='登录已失效，请重新登录';
export const SIGNED_OUT='已退出登录';
type State={kind:'loading'}|{kind:'app';info:SessionInfo}|{kind:'login';notice?:string;unavailable?:string;error?:string}|{kind:'busy';detail:string};
/** Shows the login page in login mode (LEAD_AUTH=on); local mode renders the app unchanged (AWS trial §2.9). */
export function AuthGate({children}:{children:ReactNode}) {
 const [state,setState]=useState<State>({kind:'loading'});
 const auto=useRef(false);
 const mode=useRef<'local'|'server'>('server');
 const timer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined);
 const load=useCallback(async ()=>{
  try {
   const info=await getSession();
   if(!info.auth) {setServerMode(false);setOnUnauthorized(undefined);setState({kind:'app',info});return;}
   mode.current=info.mode==='server'?'server':'local';
   setServerMode(mode.current==='server');
   setOnUnauthorized(()=>setState({kind:'login',notice:SESSION_EXPIRED}));
   if(info.unavailable) setState({kind:'login',unavailable:info.unavailable.detail});
   else if(info.authenticated) setState({kind:'app',info});
   else setState({kind:'login'});
  } catch(error) {
   if(error instanceof ApiError&&(error.code==='maintenance'||error.code==='db_busy')) {
    setState({kind:'busy',detail:error.message});
    // One automatic retry after 2 s (e.g. a backup restore finishing); then the button.
    if(!auto.current) {auto.current=true;timer.current=setTimeout(()=>{void load();},2000);}
    return;
   }
   setState({kind:'app',info:{}});  // unknown mode: the app shows its own connection error
  }
 },[]);
 useEffect(()=>{void load();return ()=>{clearTimeout(timer.current);setOnUnauthorized(undefined);};},[load]);
 const signOut=useCallback(async ()=>{
  try {await logout();setState({kind:'login',notice:SIGNED_OUT});}
  catch {setState({kind:'login',error:SERVER_OFFLINE});}
 },[]);
 if(state.kind==='loading') return null;
 if(state.kind==='busy') return <main className="login-page"><div className="login-card" role="alert"><h1>请稍候</h1><p className="login-sub">{state.detail}</p><button type="button" className="primary" onClick={()=>{void load();}}>重试</button></div></main>;
 if(state.kind==='login') return <LoginPage key={`${state.notice}|${state.error}|${state.unavailable}`} notice={state.notice} unavailable={state.unavailable} initialError={state.error} onLogin={result=>setState({kind:'app',info:{auth:true,authenticated:true,mode:mode.current,token:result.token,user:result.user}})}/>;
 const info=state.info;
 const value:SessionContextValue=info.auth?{mode:info.mode==='server'?'server':'local',auth:true,username:info.user?.username,logout:()=>{void signOut();}}:{mode:'local',auth:false};
 if(value.mode==='server') document.title=SERVER_TITLE;
 return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
