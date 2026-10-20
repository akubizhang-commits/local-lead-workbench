import { useState } from "react";
import {api} from '../api';
import {TaskWorkflow,type Workflow} from "../TaskWorkflow";
import { useResource } from '../hooks';
import { date, label, runMessage, safeUrl } from '../helpers';
import type { Overview, Run, Task } from '../types';
import {backFromRefreshDetail,openRefreshDetail,type Route} from '../navigation';
import type {Filters} from '../helpers';
import { Resource } from '../Resource';
import { Badge, Modal } from '../ui';
import { MarketSchedule } from '../MarketSchedule';
import { RoundSummary } from '../RoundSummary';
import { MarketEditDialog } from '../MarketEditDialog';
import { MarketRecheck } from '../MarketRecheck';
export const ARCHIVE_CONFIRM='归档后，这个市场不再出现在市场列表中，也不会按计划自动更新。企业、联系人和历史记录都会保留，可随时在“已归档市场”中恢复。继续吗？';
export const RESTORED='市场已恢复。定时计划保持原设置，下次到期后才会运行。';
import { MarketSummary } from '../MarketSummary';
import { ExclusionRules } from '../ExclusionRules';
import { MarketSocialSettings } from '../MarketSocialSettings';
import { RunCard } from './RunCard';
import { RunWarnings } from '../RunWarnings';
import type { Action } from './pageTypes';

export interface RefreshItem {
 id:string;run_id:string;company_id:string;company_name:string|null;state:string;run_status:string;
 task_id:string|null;created_at:string;finished_at:string|null;message:string;source:string;website?:string;
 summary:{pages_checked?:number;contacts_found?:number;entries_added?:number;remaining?:number;files_unparsed?:number};
}
interface Activity {items:RefreshItem[];total:number;page:number;page_size:number;rounds?:Run[];workflow?:Workflow|null}
interface RefreshDetail extends RefreshItem {run:Run;logs:{url:string;status:string;reason?:string;final_url?:string}[];warnings:string[];files_unparsed:{url:string}[]}
export const itemStatus = (item:RefreshItem) => item.state==='succeeded'?'已完成':item.state==='pending'?
 (['paused','cancelled','blocked'].includes(item.run_status)?label(item.run_status):'排队中'):label(item.state);
export function RefreshTable({items,onOpen}:{items:RefreshItem[];onOpen:(item:RefreshItem)=>void}) {
 return <div className="table-wrap activity-table-wrap" tabIndex={0} role="region" aria-label="官网复核明细"><table className="activity-table"><thead><tr><th>企业 / 来源</th><th>复核状态</th><th>网页 / 联系入口</th><th>所属轮次开始时间</th><th className="table-action">操作</th></tr></thead>
 <tbody>{items.map(item=><tr key={item.id}><td><button className="text-button" onClick={()=>onOpen(item)}>{item.company_name||'关联企业信息缺失'}</button><small className="muted row-secondary">{item.source==='market'?'市场更新自动补全':'单家官网复核'}</small></td>
 <td>{itemStatus(item)}</td>
 <td>读取 {item.summary.pages_checked??'—'} 页<small className="row-secondary">{item.summary.entries_added!=null?`新增联系入口 ${item.summary.entries_added} 个（去重后）`:`原始提取 ${item.summary.contacts_found??'—'} 条（未去重）`}</small></td><td>{date(item.created_at)}</td>
 <td className="table-action"><button className="text-button" onClick={()=>onOpen(item)}>查看详情</button></td></tr>)}</tbody></table>{!items.length&&<p className="muted">暂无符合条件的官网复核。</p>}</div>;
}
export const roundNumber=(rounds:Run[],id:string)=>{const i=rounds.findIndex(r=>r.id===id);return i<0?null:rounds.length-i;};
/** 市场详情页头副行（design §4.1 第 1 条）。 */
export function marketSubline(task:Task) {
 const place=[task.country,task.state,task.county,task.city].filter(Boolean).join(' / ');
 return `目标市场 · ${place}${task.keywords.length?` · 关键词：${task.keywords.join('、')}`:''} · 官网自动补全：${task.auto_enrich?'已开启':'已关闭'}`;
}
/** 市场详情页头右侧操作：查看市场企业 / 编辑市场 / 归档市场（归档后为恢复市场）。 */
export function MarketHeadingActions({task,version,busy,act,openMarket,onOpenMarket}:{task:Task;version:number;busy:boolean;act:Action;
 openMarket:(id:string)=>void;onOpenMarket:(id:string)=>void}) {
 const [editing,setEditing]=useState(false);
 return <>
  <button onClick={()=>openMarket(task.id)}>查看市场企业</button>
  {task.archived_at?<button disabled={busy} onClick={()=>act(()=>api(`/tasks/${encodeURIComponent(task.id)}/restore`,'POST'),RESTORED)}>恢复市场</button>:<>
   <button disabled={busy} onClick={()=>setEditing(true)}>编辑市场</button>
   <button disabled={busy} onClick={()=>{if(window.confirm(ARCHIVE_CONFIRM))act(()=>api(`/tasks/${encodeURIComponent(task.id)}/archive`,'POST'),'市场已归档，可在“已归档市场”中恢复');}}>归档市场</button></>}
  {editing&&<Modal title="编辑目标市场" onClose={()=>setEditing(false)}><MarketEditDialog task={task} version={version} busy={busy} act={act} onCancel={()=>setEditing(false)}
   onSaved={()=>{setEditing(false);act(async()=>undefined,'市场关键词已更新，下一轮起生效');}} onOpenMarket={id=>{setEditing(false);onOpenMarket(id);}}/></Modal>}
 </>;
}
export function TaskActivityPage({route,navigate,tasks,version,worker,busy,act,refresh,openCompany,openMarket}: {
 route:Route;navigate:(route:Route)=>void;tasks:Task[];version:number;worker:Overview['worker'];busy:boolean;act:Action;refresh:()=>void;
 openCompany:(id:string)=>void;openMarket:(id:string,filters?:Partial<Filters>)=>void;
}) {
 const [roundId,setRoundId]=useState<string|null>(null);
 const task=tasks.find(t=>t.id===route.task);
 const standalone=route.task==='standalone';
 const query=new URLSearchParams({page:String(route.page),status:route.activityStatus||'',run_id:route.run||''});
 const list=useResource<Activity>(!route.item?(standalone?'/refresh-activity':`/tasks/${encodeURIComponent(route.task||'')}/activity`)+`?${query}`:null,version,true);
 const detail=useResource<RefreshDetail>(route.item&&route.run?`/runs/${encodeURIComponent(route.run)}/items/${encodeURIComponent(route.item)}`:null,version,true);
 const change=(patch:Partial<Route>)=>navigate({...route,...patch});
 const rounds=list.data?.rounds||[];
 // 批量复核官网的轮次不在 rounds 里：轮次下拉框补一个只读选项，避免显示为空白（design §6.2 落点）。
 const batchRun=route.run&&!rounds.some(r=>r.id===route.run)?list.data?.items.find(i=>i.run_id===route.run):undefined;
 const batchRunOption=batchRun?{id:batchRun.run_id,created_at:batchRun.created_at}:undefined;
 if(route.item) return <Resource {...detail} retry={refresh}>{detail.data&&<>
   <section className="panel"><div className="panel-heading"><h2>{detail.data.company_name||'关联企业信息缺失'}</h2><button onClick={()=>openCompany(detail.data!.company_id)}>查看企业资料</button></div><p>复核状态：{itemStatus(detail.data)}</p>
   <p>读取 {detail.data.summary.pages_checked??'—'} 页{detail.data.summary.entries_added!=null&&` · 新增联系入口 ${detail.data.summary.entries_added} 个（去重后）`} · 原始提取 {detail.data.summary.contacts_found??'—'} 条（未去重） · 待扫描 {detail.data.summary.remaining??'—'} 个链接</p>
   <p>{runMessage(detail.data.message)||(detail.data.state==='succeeded'?'本次复核已完成，已取得的资料已保存。':'暂无复核说明')}</p>
   {safeUrl(detail.data.website)&&<a href={safeUrl(detail.data.website)} target="_blank" rel="noreferrer">企业官网 ↗</a>}
   {!!detail.data.warnings.length&&<RunWarnings error={detail.data.warnings.join('；')}/>}
   <h3>网页处理明细</h3><p className="muted">以下保留历次尝试，包括重试前的失败；当前结果以顶部复核状态为准。</p>
   {!detail.data.logs.length?<p className="muted">这次复核没有保存逐页日志，可以查看下方所属轮次的说明。</p>:<div className="table-wrap refresh-log-table"><table><thead><tr><th>网页</th><th>结果</th><th>说明</th></tr></thead><tbody>{detail.data.logs.map((log,i)=><tr key={i}><td>{safeUrl(log.url)?<a href={safeUrl(log.url)} target="_blank" rel="noreferrer">{log.url}</a>:log.url}</td><td>{{parsed:'已读取',file_unparsed:'文件未解析',duplicate:'重复链接',excluded:'按规则跳过',blocked:'访问受限',failed:'读取失败',deferred:'下批继续'}[log.status]||log.status}</td><td>{log.reason||'—'}</td></tr>)}</tbody></table></div>}
   </section><h3>所属轮次</h3><p className="muted">以下进度和控制针对整个轮次；一次市场更新可能包含多家企业。</p>
   <RunCard run={detail.data.run} worker={worker} busy={busy} act={act} openCompany={openCompany}/>
 </>}</Resource>;
 return <>
  {task?.archived_at&&<p className="alert" role="status">此市场已归档：不会开始新一轮，也不会按计划自动更新。企业和历史记录都已保留。</p>}
  {task&&!task.archived_at&&task.needs_keywords&&<p className="hint">这个市场创建时没有填写关键词，目前按旧规则使用服装行业默认检索词（{(task.legacy_terms||[]).join('、')}）。建议点“编辑市场”补充关键词或选择预设行业。</p>}
  {task&&<section className="panel activity-overview" aria-label="市场概况">
   {(task.geography?.county_ids.length||0)>1&&<p className="muted">跨县城市 / 居民点：关联 {task.geography?.county_ids.length} 个县或同级地区，地图检索范围仍为完整城市 / 居民点。</p>}
   <MarketSummary market={task} busy={busy} act={act} onTier={tier=>openMarket(task.id,{best_tier:tier})} onNearby={()=>openMarket(task.id,{nearby:'only'})}/><RoundSummary task={task} openMarket={openMarket}/>
  </section>}
  <Resource {...list} retry={refresh}>
   {list.data?.workflow&&<TaskWorkflow flow={list.data.workflow} worker={worker} busy={busy} onViewCompanies={task?()=>openMarket(task.id):undefined}
     onOpenSettings={()=>navigate({view:'settings',selected:null,filters:route.filters,page:1})} onSocialChange={refresh}
     onResume={()=>act(()=>api('/maps/resume','POST',{}),'已解除地图暂停，排队中和运行中的轮次将逐个继续')}/>}
  </Resource>
  {task&&<section className="panel market-settings" aria-labelledby="market-settings-title"><div className="panel-heading"><h2 id="market-settings-title">市场设置</h2></div>
   <MarketSchedule task={task} version={version} archived={!!task.archived_at}/>
   <details className="market-exclusions"><summary>本市场排除规则</summary><ExclusionRules marketId={task.id} version={version} busy={busy} act={act}/></details>
   <MarketSocialSettings key={task.id} task={task} busy={busy} onChange={refresh} onRun={run=>change({run:run.id,item:undefined,listRun:undefined,activityStatus:undefined,page:1})}/>
   <MarketRecheck marketId={task.id} archived={!!task.archived_at} version={version}/></section>}
  {list.data&&<>
   {!!rounds.length&&<section className="panel activity-rounds"><details><summary>历次更新（{rounds.length}）</summary><div className="table-wrap" role="region" aria-label="历次更新" tabIndex={0}><table className="rounds-table"><thead><tr><th>开始时间</th><th>类型</th><th>状态</th><th>结果说明</th><th>操作</th></tr></thead><tbody>{rounds.map(run=><tr key={run.id}><td>{date(run.created_at)}</td><td>{label(run.kind)}</td><td><Badge value={run.status}/></td><td>{runMessage(run.message)}</td><td><button className="text-button" onClick={()=>setRoundId(run.id)}>查看详情</button></td></tr>)}</tbody></table></div></details></section>}
   {roundId&&rounds.find(r=>r.id===roundId)&&<Modal title={`第 ${roundNumber(rounds,roundId)} 轮详情`} onClose={()=>setRoundId(null)}><div className="form-body"><RunCard run={rounds.find(r=>r.id===roundId)!} worker={worker} busy={busy} act={act}/></div><footer><button onClick={()=>setRoundId(null)}>关闭</button><button className="primary" onClick={()=>{change({run:roundId,page:1});setRoundId(null);}}>查看该轮进度</button></footer></Modal>}
   <section className="panel activity-results"><div className="panel-heading"><h2>官网复核明细</h2><span className="muted">共 {list.data.total} 条</span></div><p className="muted">每行是一家企业的一次官网复核，点企业名可以查看读取了哪些网页。</p>
   <div className="list-toolbar" role="group" aria-label="官网复核筛选"><label className="field"><span>复核状态</span><select value={route.activityStatus||''} onChange={e=>change({activityStatus:e.target.value||undefined,page:1})}><option value="">全部状态</option>{['pending','running','succeeded','partial','failed','blocked'].map(s=><option key={s} value={s}>{s==='pending'?'排队中':s==='succeeded'?'已完成':label(s)}</option>)}</select></label>
   {!!rounds.length&&<label className="field"><span>轮次</span><select value={route.run||''} onChange={e=>change({run:e.target.value||undefined,page:1})}><option value="">全部轮次（含单家复核）</option>{rounds.map((r,i)=><option key={r.id} value={r.id}>{date(r.created_at)} · 第{rounds.length-i}轮</option>)}{batchRunOption&&<option value={batchRunOption.id} disabled>批量复核官网 · {date(batchRunOption.created_at)}</option>}</select></label>}
   {(route.run||route.activityStatus)&&<button className="text-button toolbar-reset" onClick={()=>change({run:undefined,activityStatus:undefined,page:1})}>清除筛选</button>}</div>
   <RefreshTable items={list.data.items} onOpen={item=>navigate(openRefreshDetail(route,item))}/>
   <div className="pagination"><span>第 {route.page} 页 · 每页50条</span><div className="actions"><button disabled={route.page<=1} onClick={()=>change({page:route.page-1})}>上一页</button><button disabled={route.page*50>=list.data.total} onClick={()=>change({page:route.page+1})}>下一页</button></div></div></section>
  </>}
 </>;
}
