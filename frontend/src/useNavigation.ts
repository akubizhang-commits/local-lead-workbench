import {useEffect,useState} from 'react';
import {parseRoute,routeUrl,type Route} from './navigation';
export function useNavigation(){
 const [route,setRoute]=useState(()=>parseRoute(window.location.href));
 useEffect(()=>{const pop=()=>setRoute(parseRoute(window.location.href));window.addEventListener('popstate',pop);return()=>window.removeEventListener('popstate',pop);},[]);
 function navigate(next:Route){
  const url=routeUrl(next);
  if(url!==window.location.pathname+window.location.search)window.history.pushState(null,'',url);
  setRoute(next);
 }
 return {route,navigate};
}
