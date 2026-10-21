import {createContext,useContext} from 'react';
/** Deployment mode and signed-in user, provided by AuthGate (AWS trial §2.9). */
export interface SessionContextValue {mode:'local'|'server';auth:boolean;username?:string;logout?:()=>void}
export const SessionContext=createContext<SessionContextValue>({mode:'local',auth:false});
export function useSession() {return useContext(SessionContext);}
