import {useEffect,useRef} from 'react';
import {api} from './api';
import {useResource} from './hooks';
import {date, label, runActions, runMessage} from './helpers';
import {CANCEL_ROUND_CONFIRM, ROUND_ACTION_NOTICE} from './pages/RunCard';
import type {Action} from './pages/pageTypes';
import type {Run} from './types';

const TONE:Record<string,string>={queued:'info',paused:'info',running:'teal',completed:'teal',partial:'warn',blocked:'warn',failed:'warn',cancelled:'muted'};
const ACTION_LABEL:Record<string,string>={pause:'暂停本轮',resume:'继续本轮',cancel:'取消本轮…'};
function duration(from:string,to?:string|null){
 const seconds=Math.max(0,Math.round(((to?Date.parse(to):Date.now())-Date.parse(from))/1000));
 const mins=Math.floor(seconds/60);
 return mins>=60?`${Math.floor(mins/60)}小时${mins%60}分钟`:`${mins}分钟`;
}
/** 控制条标题（design §4.1 第 2 条）：本轮（第 n 轮）/ 批量复核官网 / 单家官网复核。 */
export function roundTitle(run:Pick<Run,'id'|'kind'|'task_id'>,{rounds,standalone}:{rounds:Pick<Run,'id'>[];standalone:boolean}):string {
 if(standalone)return '单家官网复核';
 if(run.kind==='refresh'&&!run.task_id)return '批量复核官网';
 const index=rounds.findIndex(r=>r.id===run.id);
 return index<0?'本轮':`本轮（第 ${rounds.length-index} 轮）`;
}
/** 本轮控制条：状态、历时、说明和暂停 / 继续 / 取消本轮（P1-16 “前往暂停”的落点）。 */
export function RoundControls({runId,rounds,standalone=false,version,busy,act,retryPending=0,onViewNew,focusOnLoad=false,onFocused}:{
 focusOnLoad?:boolean;onFocused?:()=>void;
 runId:string;rounds:Pick<Run,'id'>[];standalone?:boolean;version:number;busy:boolean;act:Action;retryPending?:number;
 onViewNew?:(run:Run)=>void;
}) {
 const res=useResource<Run>(`/runs/${encodeURIComponent(runId)}`,version,true,{keepStale:true});
 const heading=useRef<HTMLHeadingElement>(null);
 const loaded=!!res.data;
 // Arriving from the restore panel (“前往暂停”): focus the control bar title once it has loaded.
 useEffect(()=>{if(focusOnLoad&&loaded&&heading.current){heading.current.focus();onFocused?.();}},[focusOnLoad,loaded,onFocused]);
 if(res.errorInfo?.kind==='not_found'||res.errorInfo?.status===404)return <p className="muted round-missing">这一轮已不存在，可能已被备份恢复替换。</p>;
 const run=res.data;
 if(!run)return null;
 const finished=['completed','cancelled'].includes(run.status);
 const actions=finished?[]:runActions(run.status,run.coverage);
 const message=runMessage(run.message);
 const title=roundTitle(run,{rounds,standalone});
 return <section className="panel round-controls" aria-labelledby={`round-${run.id}`}>
  <div className="round-controls-main">
   <div>
    <h2 ref={heading} id={`round-${run.id}`} tabIndex={-1} data-focus-fallback="">{title}{title.endsWith('）')?'':' '}· <span className={`round-status tone-${TONE[run.status]||'muted'}`}>{label(run.status)}</span></h2>
    <p className="muted">{date(run.created_at)} 开始 · 已历时 {duration(run.created_at,run.finished_at)}（含等待和暂停）{retryPending>0&&` · 待重试 ${retryPending} 项`}</p>
    {message&&<p className="round-message">{message}</p>}
   </div>
   <div className="actions">
    {actions.map(action=><button key={action} type="button" className={action==='resume'?'primary':undefined} disabled={busy}
     onClick={()=>{
      if(action==='cancel'&&!window.confirm(CANCEL_ROUND_CONFIRM))return;
      void act(()=>api(`/runs/${encodeURIComponent(run.id)}/${action}`,'POST'),ROUND_ACTION_NOTICE[action]);
     }}>{ACTION_LABEL[action]}</button>)}
    {finished&&onViewNew&&<button type="button" className="text-button" onClick={()=>onViewNew(run)}>查看本轮新增企业 →</button>}
   </div>
  </div>
 </section>;
}
