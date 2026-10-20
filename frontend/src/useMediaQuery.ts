import {useEffect,useState} from 'react';
/** `matchMedia(query).matches`, kept in sync; false when matchMedia is unavailable (jsdom, old browsers). */
export function useMediaQuery(query:string):boolean {
 const get=()=>typeof window!=='undefined'&&typeof window.matchMedia==='function'?window.matchMedia(query).matches:false;
 const [matches,setMatches]=useState(get);
 useEffect(()=>{
  if(typeof window.matchMedia!=='function')return;
  const list=window.matchMedia(query);
  const update=()=>setMatches(list.matches);
  update();
  list.addEventListener?.('change',update);
  return()=>list.removeEventListener?.('change',update);
 },[query]);
 return matches;
}
