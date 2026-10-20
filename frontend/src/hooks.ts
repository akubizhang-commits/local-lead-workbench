import {useEffect,useRef,useState} from 'react';
import {api} from './api';
import {errorInfo,type ErrorInfo} from './errors';
/**
 * Load `path` (re-read when `version` changes; poll every 5 s when `poll`).
 * keepStale: when only `version` changed (same path), keep the previous data while re-reading so the
 * subtree is not unmounted (open <details>, focus); a new path still clears it (design §4.2, D17).
 */
export function useResource<T>(path:string|null,version=0,poll=false,options?:{keepStale?:boolean}) {const [data,setData]=useState<T>();const [error,setError]=useState('');const [info,setInfo]=useState<ErrorInfo>();const [loading,setLoading]=useState(!!path);const lastPath=useRef<string|null>(null);const keepStale=!!options?.keepStale;useEffect(()=>{let active=true;let controller:ReturnType<typeof setTimeout>;const samePath=lastPath.current===path;lastPath.current=path;if(!(keepStale&&samePath&&path))setData(undefined);setError('');setInfo(undefined);setLoading(!!path);async function load(){if(!path)return;try {const result=await api<T>(path);if(active){setData(result);setError('');setInfo(undefined);}}catch(e){if(active){setError(e instanceof Error?e.message:'无法连接本地服务');setInfo(errorInfo(e));}}finally{if(active){setLoading(false);if(poll)controller=setTimeout(load,5000);}}}void load();return()=>{active=false;clearTimeout(controller);};},[path,version,poll,keepStale]);return {data,error,loading,errorInfo:info};}
/** requestAnimationFrame with a timer fallback (jsdom has no rAF unless pretendToBeVisual). */
export function nextFrame(callback:()=>void){if(typeof window.requestAnimationFrame==='function')window.requestAnimationFrame(()=>callback());else window.setTimeout(callback,16);}
