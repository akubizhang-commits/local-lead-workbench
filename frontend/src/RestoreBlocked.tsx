import {useEffect, useRef} from 'react';
import {date, label} from './helpers';
import type {Route} from './navigation';

/** One blocking round from a 409 `restore_blocked_runs` body (design §6.1). */
export interface BlockingRun {id:string;kind?:string;status?:string;created_at?:string;task_id?:string|null;task_name?:string|null;
 company_id?:string|null;company_name?:string|null;batch_market_id?:string|null;batch_market_name?:string|null;company_count?:number|null}
export interface BlockingSocialRun {id:string;status?:string;created_at?:string;parent_run_id?:string|null;task_id?:string|null;task_name?:string|null;
 company_count?:number|null;company_id?:string|null;company_name?:string|null}
export interface RestoreBlockers {runs:BlockingRun[];runs_total:number;social_runs:BlockingSocialRun[];social_runs_total:number;complete:boolean}
export interface BlockerEntry {key:string;first:string;second:string;action?:'前往暂停'|'前往查看';route?:Partial<Route>}

const str=(v:unknown)=>typeof v==='string'&&v?v:null;
const count=(v:unknown)=>typeof v==='number'&&Number.isInteger(v)&&v>=0?v:null;
function items<T extends {id:string}>(value:unknown):T[]|null {
 if(value===undefined)return null;
 if(!Array.isArray(value))return [];
 return value.filter((v):v is T=>!!v&&typeof v==='object'&&typeof (v as {id?:unknown}).id==='string'&&!!(v as {id:string}).id);
}
/** Validate the payload (design §10): malformed entries are dropped, never thrown. */
export function parseBlockers(body:Record<string,unknown>|undefined):RestoreBlockers {
 const runs=items<BlockingRun>(body?.runs), social=items<BlockingSocialRun>(body?.social_runs);
 const total=(v:unknown,list:unknown[])=>Math.max(count(v)??list.length,list.length);
 return {runs:runs||[],social_runs:social||[],runs_total:total(body?.runs_total,runs||[]),social_runs_total:total(body?.social_runs_total,social||[]),
  complete:(runs?.length||0)+(social?.length||0)>0};
}
const when=(item:{status?:string;created_at?:string})=>`${label(item.status)} · ${date(item.created_at)} 开始`;
/** Six entry kinds and their button routes (design §6.2 table). */
export function blockerEntries(b:RestoreBlockers):BlockerEntry[] {
 const out:BlockerEntry[]=[];
 for(const r of b.runs){
  const task=str(r.task_id), batch=str(r.batch_market_id);
  if(task)out.push({key:r.id,first:str(r.task_name)||'目标市场',second:`${r.kind==='social'?'社媒补充轮次':r.kind==='refresh'?'官网复核':'市场更新'} · ${when(r)}`,
   action:'前往暂停',route:{view:'tasks',task,run:r.id,page:1}});
  else if(batch){const n=count(r.company_count);
   out.push({key:r.id,first:`${str(r.batch_market_name)||'目标市场'}${n!==null?` · ${n} 家`:''}`,second:`批量复核官网 · ${when(r)}`,
    action:'前往暂停',route:{view:'tasks',task:batch,run:r.id,page:1}});}
  else if(r.kind==='refresh')out.push({key:r.id,first:str(r.company_name)||'关联企业信息缺失',second:`单家官网复核 · ${when(r)}`,
   action:'前往暂停',route:{view:'tasks',task:'standalone',run:r.id,page:1}});
  else out.push({key:r.id,first:'轮次',second:`${label(r.kind)} · ${when(r)} · 可在目标市场查看`});
 }
 for(const s of b.social_runs){
  const task=str(s.task_id);
  if(task)out.push({key:`s:${s.id}`,first:str(s.task_name)||'目标市场',second:`社媒补充 · ${label(s.status)} · 跟随所属轮次，等待完成即可`,
   action:'前往查看',route:{view:'tasks',task,run:str(s.parent_run_id)||undefined,page:1}});
  else {const n=count(s.company_count), company=str(s.company_id);
   out.push({key:`s:${s.id}`,first:`${str(s.company_name)||'关联企业信息缺失'}${n!==null&&n>1?` 等 ${n} 家`:''}`,
    second:`社媒补充 · ${label(s.status)} · 不能暂停，等待完成即可`,
    ...(company?{action:'前往查看' as const,route:{view:'detail',selected:company,page:1,section:'contacts'}}:{})});}
 }
 return out;
}
export function blockerTitle(b:RestoreBlockers):string {
 const n=b.runs_total,s=b.social_runs_total;
 if(!b.complete||(!n&&!s))return '有排队中或运行中的轮次，暂时不能恢复';
 if(n&&s)return `有 ${n} 个轮次正在排队或运行，另有 ${s} 项社媒补充，暂时不能恢复`;
 if(n)return `有 ${n} 个轮次正在排队或运行，暂时不能恢复`;
 return `有 ${s} 项社媒补充正在排队或运行，暂时不能恢复`;
}
export const unlisted=(b:RestoreBlockers)=>Math.max(0,b.runs_total-b.runs.length)+Math.max(0,b.social_runs_total-b.social_runs.length);

/** Restore refusal panel (P1-16): which rounds block the restore and where to pause them. */
export function RestoreBlocked({body,onGo}:{body:Record<string,unknown>|undefined;onGo:(route:Partial<Route>)=>void}) {
 const b=parseBlockers(body);
 const entries=b.complete?blockerEntries(b):[];
 const more=b.complete?unlisted(b):0;
 const heading=useRef<HTMLHeadingElement>(null);
 useEffect(()=>{heading.current?.focus();},[]);
 return <div className="restore-blocked" role="alert">
  <h3 ref={heading} tabIndex={-1}>{blockerTitle(b)}</h3>
  <p>暂停或等待这些轮次结束后，再点“确认恢复”即可，不需要取消。</p>
  <p className="muted">已暂停的轮次不影响恢复，但其中的进度会被备份内容覆盖。</p>
  {!!entries.length&&<ul>{entries.map(e=><li key={e.key}>
   <div><strong>{e.first}</strong><small>{e.second}</small></div>
   {e.action&&e.route&&<button type="button" aria-label={`${e.action}：${e.first}`} onClick={()=>onGo(e.route!)}>{e.action}</button>}
  </li>)}</ul>}
  {more>0&&<p className="muted">另有 {more} 项未列出，可在目标市场查看。</p>}
  {!b.complete&&<button type="button" className="text-button" onClick={()=>onGo({view:'tasks',page:1})}>前往目标市场</button>}
 </div>;
}
