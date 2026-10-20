import {useId, useState} from 'react';
import {date, label, mapsReasonText, runMessage} from './helpers';
import {WorkflowHint} from './WorkflowHint';
import {SocialPlatformGates,SocialWorkflowDetails,socialHasWork,socialWaiting,type WorkflowSocial} from './WorkflowSocial';
import {WorkflowWebsiteDetails,websiteDetailsTip,type WebsiteSummary} from './WorkflowWebsite';
import type {Overview} from './types';
export interface WorkflowStage {key:string;title:string;total:number;processed:number;pending:number;running:number;succeeded:number;partial:number;failed:number;blocked:number;no_results?:number;mode?:string|null;details?:string[];retry_pending?:number;unattempted?:number;website_summary?:WebsiteSummary}
interface MapsRecovery {phase:string;manual_required:boolean;retry_after_seconds:number;message:string;reason:string;next_attempt_at:string|null;retries_completed:number;max_auto_retries:number}
type FailureKind='scraper_error'|'no_results'|'restricted'|null;
interface MapsQuery {id:string;query:string;state:string;attempts:number|null;retries:number|null;last_attempt_at:string|null;next_attempt_at:string|null;reason:string;stop_reason?:string|null;failure_kind?:FailureKind;history:{attempt:number;finished_at:string;stop_reason:string;message:string;failure_kind?:FailureKind}[]}
/** Distinguish a scraper fault from a genuine empty result. */
export function queryFailureBadge(q:Pick<MapsQuery,'failure_kind'|'state'|'next_attempt_at'>):string|null {
 if(q.failure_kind==='scraper_error')return q.state==='pending'&&q.next_attempt_at?'采集程序异常（将自动重试）':'采集程序异常';
 if(q.failure_kind==='no_results')return '没有找到结果';
 if(q.failure_kind==='restricted')return '平台限制访问';
 return null;
}
export interface Workflow {run_id:string;status:string;created_at:string;elapsed_seconds:number;current:string;stages:WorkflowStage[];companies_found:number;website_pages:number;contacts_found:number;website_links_remaining:number;auto_enrich:boolean;maps_wait:string|null;updated_at:string;maps_recovery?:MapsRecovery|null;maps_queries?:MapsQuery[];maps_query_issue_count?:number;social_only?:boolean;social?:WorkflowSocial|null}
function stageInterrupted(stage:WorkflowStage,flow:Workflow) {
 return ['paused','cancelled'].includes(flow.status)&&
  (stage.pending+stage.running>0||stage.processed<stage.total||stage.key==='social');
}
export function stageText(stage:WorkflowStage,flow:Workflow) {
 const errors=stage.partial+stage.failed+stage.blocked;
 if(stage.key==='refresh'&&!flow.auto_enrich&&!stage.total)return '未开启';
 if(stageInterrupted(stage,flow))return label(flow.status);
 if(stage.key==='social'){
  if(stage.running)return '处理中';
  if(socialWaiting(flow.social))return '等待平台继续';
  if(socialHasWork(flow.social))return '排队中';
 }
 if(!stage.total)return stage.key==='planning'?'暂无规划记录':'未开始';
 if(stage.running)return '处理中';
 if(stage.key==='discovery'&&stage.pending&&flow.maps_recovery?.manual_required)return '等待处理后恢复';
 if(stage.key==='discovery'&&stage.pending&&['cooldown','query_wait','probe'].includes(flow.maps_recovery?.phase||''))return '等待自动继续';
 if(stage.pending)return flow.status==='paused'?'已暂停':flow.status==='cancelled'?'已取消':errors?'需关注':'排队中';
 if(stage.key==='planning'&&stage.mode==='text_only'&&!stage.failed&&!stage.blocked)return '已生成文字检索方案';
 if(!errors)return '已完成';
 const hasResults=stage.key==='refresh'?
  (stage.website_summary?stage.website_summary.with_pages>0:stage.succeeded>0||flow.website_pages>0):
  stage.succeeded>0||stage.partial>0;
 return hasResults?'部分完成':'未取得结果';
}
function duration(seconds:number){const mins=Math.floor(seconds/60);return mins>=60?`${Math.floor(mins/60)}小时${mins%60}分钟`:`${mins}分钟`;}
function recoveryTime(recovery:MapsRecovery){
 if(recovery.retry_after_seconds>0)return recovery.retry_after_seconds<60?'不到1分钟后尝试':`约${Math.ceil(recovery.retry_after_seconds/60)}分钟后尝试`;
 return '等待调度继续';
}
function StageRow({stage:s,index,flow,offline,busy,onOpenSettings,onSocialChange}:{
 stage:WorkflowStage;index:number;flow:Workflow;offline:boolean;busy?:boolean;onOpenSettings?:()=>void;onSocialChange?:()=>void;
}) {
 const [open,setOpen]=useState(false);
 const id=useId();
 const recovery=s.key==='discovery'?flow.maps_recovery:null;
 const issues=s.partial+s.failed+s.blocked;
 const remaining=s.pending+s.running;
 const fallback=s.key==='planning'&&s.mode==='text_only'&&!s.failed&&!s.blocked;
 const social=s.key==='social';
 const website=s.key==='refresh';
 const interrupted=stageInterrupted(s,flow);
 const waiting=!!recovery&&(recovery.manual_required||!!recovery.reason)||social&&socialWaiting(flow.social);
 const complete=s.total>0&&s.processed>=s.total&&!remaining&&!issues&&!interrupted&&!(social&&socialHasWork(flow.social));
 const unit=s.key==='refresh'||social?'家企业':s.key==='discovery'?'个查询':'项';
 const step=flow.social_only&&social?'3B':({planning:'1',discovery:'2',refresh:'3A',social:'3B'}[s.key]||String(index+1));
 const retry=s.retry_pending??0;
 const unattempted=s.unattempted??Math.max(0,s.pending-retry);
 const text=offline&&remaining?'后台离线':fallback&&complete?'已完成':stageText(s,flow);
 const detailLabel=website?'官网检索明细':social?(issues?`平台明细 · 需关注 ${issues} 家`:'平台明细'):issues?`需关注 ${issues} 项`:retry?`待重试 ${retry} 项`:fallback?'文字检索说明':waiting?'等待原因':'查看明细';
 const tip=website?websiteDetailsTip(s.website_summary,s.processed):[recovery?.reason,...(social?flow.social?.platforms.filter(p=>p.reason).map(p=>p.reason)||[]:[]),...(s.details||[])].filter(Boolean).map(runMessage).join('；')||
  (issues?`部分完成 ${s.partial} · 失败 ${s.failed} · 受阻 ${s.blocked}。已取得的结果已保存。`:
   `成功 ${s.succeeded} · 待处理 ${s.pending} · 处理中 ${s.running}。`);
 return <li className={`workflow-stage${s.running&&!offline&&!interrupted?' is-active':''}${issues?' has-issues':''}`}>
  <div className="workflow-stage-row">
   <div className="workflow-stage-heading">
    <span className={`workflow-step${complete?' is-complete':''}`} aria-hidden="true">
     {step}
    </span>
    <div><h3>{s.title}</h3><span className={`workflow-stage-status${issues&&!s.running||waiting||offline&&remaining?' is-warning':''}`}>{text}</span></div>
   </div>
   <div className="workflow-stage-progress">
    {s.total>0?<>
     <div className="workflow-progress-caption"><span>已处理 <strong>{s.processed}</strong> / {s.total} <small>{unit}</small></span></div>
     <div className="workflow-track">
      <progress className="sr-only" aria-label={`${s.title}已处理进度`} max={s.total} value={s.processed}
       aria-valuetext={`已处理 ${s.processed} / ${s.total} ${unit}；成功 ${s.succeeded}，需关注 ${issues}，剩余 ${remaining}`}/>
      <span className="workflow-track-success" aria-hidden="true" style={{width:`${s.succeeded/s.total*100}%`}}/>
      <span className="workflow-track-issues" aria-hidden="true" style={{width:`${issues/s.total*100}%`}}/>
     </div>
    </>:<span className="workflow-no-total">{s.key==='refresh'&&!flow.auto_enrich?'本市场未开启官网自动补全':'总数待确定'}</span>}
   </div>
   <div className="workflow-stage-next">
    <span>{interrupted?label(flow.status):offline&&remaining?'进度已保留':recovery?.manual_required?'需要手动恢复':
     recovery?.next_attempt_at?recoveryTime(recovery):remaining?`剩余 ${remaining} ${unit}`:social&&socialHasWork(flow.social)?'仍有平台待处理':s.total?'全部已处理':'—'}</span>
    <WorkflowHint label={detailLabel} tip={tip} expanded={open} controls={id} onToggle={()=>setOpen(!open)} warning={issues>0||retry>0||waiting}/>
   </div>
  </div>
  {social&&flow.social&&<SocialPlatformGates social={flow.social}
   disabled={!!busy||offline||['paused','cancelled'].includes(flow.status)} onOpenSettings={onOpenSettings} onChange={onSocialChange}/>}
  <div id={id} className="workflow-stage-details" hidden={!open}>
   {website?<WorkflowWebsiteDetails summary={s.website_summary} processed={s.processed} pending={s.pending} running={s.running}
    ended={s.total>0&&!remaining&&issues>0&&!interrupted}/>:<>
   <div className="workflow-breakdown">
    <span>{s.key==='discovery'?'已完成':'成功'} <strong>{s.succeeded}</strong></span>
    {s.key==='discovery'?<><span>待重试 <strong>{retry}</strong></span><span>{waiting?'等待恢复':'等待执行'} <strong>{unattempted}</strong></span></>:<span>待处理 <strong>{s.pending}</strong></span>}
    <span>处理中 <strong>{s.running}</strong></span>
    {social&&s.no_results!==undefined&&<span>正常无结果 <strong>{s.no_results}</strong></span>}
    {s.partial>0&&<span className="workflow-warning">部分完成 <strong>{s.partial}</strong></span>}
    {s.failed>0&&<span className="workflow-warning">失败 <strong>{s.failed}</strong></span>}
    {s.blocked>0&&<span className="workflow-warning">受阻 <strong>{s.blocked}</strong></span>}
   </div>
   {s.details?.map(detail=><p className="workflow-warning" key={detail}>{runMessage(detail)}</p>)}
   {recovery&&<>
    <p>{runMessage(recovery.message)}</p>
    {recovery.reason&&<p>原因：{runMessage(recovery.reason)}</p>}
    {recovery.next_attempt_at&&<p>下次自动尝试：{date(recovery.next_attempt_at)}<small>后台在线时，到期后按队列逐个处理。</small></p>}
    {waiting&&<p>连续恢复已尝试 {recovery.retries_completed} 次 · 最多自动恢复 {recovery.max_auto_retries} 次</p>}
   </>}
   {issues>0&&<p>异常项已结束本次执行，已取得的结果已保存。{social?'可展开社媒运行报告查看具体原因。':'可在下方异常与重试记录中查看具体查询。'}</p>}
   {social&&<SocialWorkflowDetails social={flow.social} expanded={open} offline={offline}/>}
   </>}
  </div>
 </li>;
}
export function TaskWorkflow({flow,worker,onResume,onViewCompanies,busy,onOpenSettings,onSocialChange}:{
 flow:Workflow;worker:Overview['worker'];onResume?:()=>void;onViewCompanies?:()=>void;busy?:boolean;onOpenSettings?:()=>void;onSocialChange?:()=>void;
}) {
 const stages=flow.social_only?flow.stages.filter(s=>s.key==='social'):flow.stages;
 const remaining=stages.some(s=>s.pending+s.running>0)||socialHasWork(flow.social);
 const active=!['paused','cancelled'].includes(flow.status)&&(['queued','running'].includes(flow.status)||remaining);
 const offline=active&&worker?.alive===false;
 const issue=stages.some(s=>s.partial+s.failed+s.blocked>0);
 const recovery=flow.social_only?null:flow.maps_recovery;
 const hasSocial=stages.some(s=>s.key==='social');
 const parallel=hasSocial&&!flow.social_only;
 return <section className="panel task-workflow" aria-label="本轮进度">
  <div className="panel-heading"><h2>本轮进度</h2><span className="workflow-elapsed">已历时 {duration(flow.elapsed_seconds)}<small>含等待和暂停</small></span></div>
  <dl className="workflow-results">
   <div><dt>{flow.social_only?'本轮补充企业':'已发现企业'}</dt><dd>{flow.companies_found.toLocaleString()} <small>家</small></dd></div>
   {flow.social_only?<><div><dt>社媒账号</dt><dd>{flow.social?.accounts_found.toLocaleString()??'—'} <small>个</small></dd></div>
    <div><dt>已读取帖子</dt><dd>{flow.social?.posts_found.toLocaleString()??'—'} <small>条</small></dd></div></>:<>
    <div><dt>官网已读取</dt><dd>{flow.website_pages.toLocaleString()} <small>页</small></dd></div>
    <div><dt>原始提取</dt><dd>{flow.contacts_found.toLocaleString()} <small>条</small></dd><small className="workflow-result-note">未去重，不等于联系入口数</small></div>
   </>}
  </dl>
  {!flow.social_only&&flow.social&&<p className="workflow-social-results">社媒账号 <strong>{flow.social.accounts_found.toLocaleString()}</strong> 个 · 已读取帖子 <strong>{flow.social.posts_found.toLocaleString()}</strong> 条</p>}
  <div className={`workflow-current${offline?' is-offline':''}`} role="status">
   <span className="workflow-current-label">{offline?'等待后台':active?'当前进展':'本轮结果'}</span>
   <div><strong>{offline?'后台离线，本轮暂时无法推进':runMessage(flow.current)}</strong>
   {offline&&<p>已保留进度，重新启动后台后可恢复处理。</p>}
   {!flow.social_only&&!recovery&&flow.maps_wait&&flow.maps_wait!==flow.current&&<p>{runMessage(flow.maps_wait)}</p>}
   </div>
  </div>
  <div className="workflow-guide"><span>阶段进展{parallel?' · 3A 官网与 3B 社媒并行补全':flow.social_only?' · 社媒补充轮次':''}</span><span className="workflow-legend"><span><i className="workflow-track-success"/>成功</span><span><i className="workflow-track-issues"/>需关注</span><span><i/>待执行</span></span></div>
  <ol className="workflow-stages">{stages.map((s,i)=><StageRow key={`${flow.run_id}-${s.key}`} stage={s} index={i} flow={flow} offline={offline}
   busy={busy} onOpenSettings={onOpenSettings} onSocialChange={onSocialChange}/>)}
   <li className="workflow-stage workflow-summary-row"><div className="workflow-stage-heading"><span className="workflow-step" aria-hidden="true">4</span><div><h3>结果汇总</h3><span className="workflow-stage-status">{offline?'进度已保留':active?'持续汇总中':flow.status==='completed'?(issue?'本轮已结束 · 有需关注项':'本轮已结束'):flow.status==='partial'?'部分完成':label(flow.status)}</span></div></div>
    <p><strong>{flow.companies_found.toLocaleString()}</strong> 家企业已保存<span>结果随采集保存</span></p>
    {onViewCompanies&&<button type="button" className="workflow-view-results" onClick={onViewCompanies}>查看企业 <span aria-hidden="true">→</span></button>}
   </li>
  </ol>
  {recovery?.manual_required&&<div className="workflow-manual">
   <div><strong>地图检索需要手动恢复</strong><p>{runMessage(recovery.reason||recovery.message)}{recovery.retry_after_seconds>0&&` · 冷却剩余${Math.ceil(recovery.retry_after_seconds/60)}分钟`}</p></div>
   {onResume&&<button disabled={busy||offline||recovery.retry_after_seconds>0} onClick={onResume}>确认访问恢复，继续地图检索</button>}
  </div>}
  <div className="workflow-footer">
  {!flow.social_only&&!!flow.maps_queries?.length&&<details className="workflow-query-history"><summary>地图异常与重试记录（{flow.maps_query_issue_count??flow.maps_queries.length} 项）</summary>
   <p className="muted">显示最近尝试的最多20项；未执行的查询不计为失败。历史记录未保存尝试次数时显示“未记录”。</p>
   <div className="table-wrap"><table><thead><tr><th>查询</th><th>状态</th><th>已尝试 / 已重试</th><th>说明</th></tr></thead><tbody>{flow.maps_queries.map(q=><tr key={q.id}>
    <td>{q.query}</td><td>{q.state==='pending'?'等待重试':q.state==='running'?'处理中':q.state==='succeeded'?'已恢复完成':'需要处理'}</td>
    <td>{q.attempts??'未记录'} 次 · 重试 {q.retries??'未记录'} 次</td>
    <td>{queryFailureBadge(q)&&<span className={`badge badge-${q.failure_kind}`}>{queryFailureBadge(q)}</span>}{mapsReasonText(q.reason,q.stop_reason)}{q.next_attempt_at&&<small>{q.failure_kind==='scraper_error'?'计划重试':'计划复核'}：{date(q.next_attempt_at)}</small>}
     {!!q.history.length&&<details><summary>历次尝试</summary>{q.history.map((h,i)=><p key={i}>第{h.attempt}次 · {date(h.finished_at)} · {mapsReasonText(h.message,h.stop_reason)||label(h.stop_reason)||'已结束'}</p>)}</details>}
    </td></tr>)}</tbody></table></div>
  </details>}
  <details className="workflow-method"><summary>进度与统计说明</summary>
   <p>{flow.social_only?'本轮仅补充已有企业的社媒资料。':'查询会随区域细分增加，官网补全的企业数会随新发现的企业增加。'}进度条中绿色为成功，棕色为部分完成、失败或受阻；“已处理”包含成功和需关注两部分，不代表全部成功，也不代表覆盖了市场内全部企业。</p>
   {!flow.social_only&&<p>官网还有 {flow.website_links_remaining.toLocaleString()} 个已发现链接待扫描。“原始提取”是从网页提取到的条数，不等于去重后的联系入口。</p>}
   {hasSocial&&<p>社媒主进度按企业去重，正常无结果计入成功执行；没有找到结果不代表没有账号。登录、冷却与排队不计入“已处理”，平台间的企业数不能相加。</p>}
   <p>本轮开始：{date(flow.created_at)} · 数据刷新于 {date(flow.updated_at)}</p>
  </details>
  <span className="workflow-refresh-note">每5秒刷新</span>
  </div>
 </section>;
}
