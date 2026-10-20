import {useRef,useState} from 'react';
import {api} from './api';
import {date,runMessage} from './helpers';
import {useResource} from './hooks';
import {Alert} from './ui';
import {platformTitle} from './MarketSocialSettings';
import {SocialRunReport,type SocialRun} from './SocialDiscovery';
import type {SocialPlatform} from './types';
import {actionMessage} from './errors';

export interface SocialPlatformProgress {
 platform:SocialPlatform;
 state:'running'|'waiting_login'|'waiting_recovery'|'waiting_session'|'waiting_configuration'|'pending'|'completed'|'needs_attention'|'paused'|'cancelled';
 total:number;processed:number;pending:number;running:number;succeeded:number;no_results:number;partial?:number;failed:number;blocked:number;
 reason:string;next_attempt_at:string|null;manual_required:boolean;
}
export interface WorkflowSocial {
 run_id:string;current:string;accounts_found:number;posts_found:number;read_posts:boolean;platforms:SocialPlatformProgress[];
}
const stateNames:Record<SocialPlatformProgress['state'],string>={
 running:'处理中',waiting_login:'等待登录',waiting_recovery:'等待恢复',waiting_session:'等待会话释放',
 waiting_configuration:'等待配置连接',pending:'排队中',completed:'已处理完',needs_attention:'需要处理',
 paused:'已暂停',cancelled:'已取消',
};
export const platformHasWork=(p:SocialPlatformProgress)=>p.pending+p.running>0||['running','pending','waiting_login','waiting_recovery','waiting_session','waiting_configuration'].includes(p.state);
export const socialHasWork=(social?:WorkflowSocial|null)=>!!social?.platforms.some(platformHasWork);
const gated=(p:SocialPlatformProgress)=>platformHasWork(p)&&!['paused','cancelled','completed'].includes(p.state)&&
 (p.manual_required||p.state.startsWith('waiting_'));
export const socialWaiting=(social?:WorkflowSocial|null)=>!!social?.platforms.some(gated);
function platformStatus(p:SocialPlatformProgress){
 if(p.state==='completed'&&platformHasWork(p))return '仍有待处理项';
 return stateNames[p.state]||'状态待确认';
}

export function SocialPlatformGates({social,disabled,onOpenSettings,onChange}:{
 social:WorkflowSocial;disabled:boolean;onOpenSettings?:()=>void;onChange?:()=>void;
}) {
 const [pending,setPending]=useState<SocialPlatform|null>(null);
 const [error,setError]=useState(''),[notice,setNotice]=useState('');
 const inFlight=useRef(false);
 async function resume(platform:SocialPlatformProgress) {
  if(disabled||inFlight.current||(platform.next_attempt_at&&Date.parse(platform.next_attempt_at)>Date.now()))return;
  inFlight.current=true;setPending(platform.platform);setError('');setNotice('');
  try {
   await api(`/social/platforms/${encodeURIComponent(platform.platform)}/resume`,'POST');
   setNotice(`${platformTitle[platform.platform]} 已请求恢复；按最新状态与队列安排继续。`);
   onChange?.();
  }catch(error){setError(error instanceof Error ? actionMessage(error) :'恢复失败，请重试');}
  finally{inFlight.current=false;setPending(null);}
 }
 const platforms=social.platforms.filter(gated);
 if(!platforms.length)return null;
 return <div className="workflow-social-gates">
  {platforms.map(platform=>{
   const cooling=!!platform.next_attempt_at&&Date.parse(platform.next_attempt_at)>Date.now();
   return <div className="workflow-social-gate" key={platform.platform}>
    <div><strong>{platformTitle[platform.platform]} · {platformStatus(platform)}</strong>
     {platform.reason&&<p>{runMessage(platform.reason)}</p>}
     {platform.next_attempt_at&&<p>下次尝试：{date(platform.next_attempt_at)}{cooling?' · 冷却结束前不能恢复':' · 等待调度'}</p>}
    </div>
    <div className="actions">
     {onOpenSettings&&<button type="button" onClick={onOpenSettings}>{platform.state==='waiting_login'?'去登录':'处理连接'}</button>}
     <button type="button" disabled={disabled||!!pending||cooling} onClick={()=>void resume(platform)}>
      {pending===platform.platform?'正在恢复…':`恢复 ${platformTitle[platform.platform]}`}
     </button>
    </div>
   </div>;
  })}
  {error&&<Alert>{error}</Alert>}{notice&&<p role="status">{notice}</p>}
 </div>;
}

export function SocialWorkflowDetails({social,expanded,offline}:{social?:WorkflowSocial|null;expanded:boolean;offline:boolean}) {
 const [reportOpen,setReportOpen]=useState(false),[revision,setRevision]=useState(0);
 const report=useResource<SocialRun>(expanded&&reportOpen&&social?.run_id?`/social/runs/${encodeURIComponent(social.run_id)}`:null,revision,true);
 if(!social)return <p className="muted">尚未提供平台明细，请等待数据刷新。</p>;
 return <>
  {social.current&&<p>{offline?'后台离线，社媒进度已保留':runMessage(social.current)}</p>}
  <p>已找到 {social.accounts_found} 个账号 · 已读取 {social.posts_found} 条帖子 · {social.read_posts?'本轮读取近期帖子':'本轮未开启帖子读取'}</p>
  <p className="muted">平台明细按企业计数，同一企业可出现在多个平台，不累加各平台数量。完整报告按处理项计数，同一企业可有多项。</p>
  <div className="workflow-social-platforms">
   {social.platforms.map(platform=><article key={platform.platform}>
    <div className="row between"><strong>{platformTitle[platform.platform]}</strong><span className={gated(platform)?'workflow-warning':''}>{offline&&platformHasWork(platform)?'后台离线':platformStatus(platform)}</span></div>
    <p>已处理 {platform.processed} / {platform.total} 家企业</p>
    <div className="workflow-breakdown">
     <span>成功执行 <strong>{platform.succeeded}</strong></span><span>其中无结果 <strong>{platform.no_results??0}</strong></span>
     <span>待处理 <strong>{platform.pending}</strong></span><span>处理中 <strong>{platform.running}</strong></span>
     {!!platform.partial&&<span>部分完成 <strong>{platform.partial}</strong></span>}
     <span>失败 <strong>{platform.failed}</strong></span><span>受阻 <strong>{platform.blocked}</strong></span>
    </div>
    {platform.reason&&<p>原因：{runMessage(platform.reason)}</p>}
    {platform.next_attempt_at&&<p>下次尝试：{date(platform.next_attempt_at)}<small>后台在线、平台可用且轮次允许继续时，按队列处理。</small></p>}
    {platform.manual_required&&<p className="workflow-warning">需要手动处理后恢复。</p>}
   </article>)}
  </div>
  {social.run_id&&<details className="workflow-social-report" onToggle={event=>setReportOpen(event.currentTarget.open)}>
   <summary>完整社媒运行报告</summary>
   {report.loading&&<p role="status">正在读取报告…</p>}
   {report.error&&<Alert>报告读取失败：{report.error}<button type="button" onClick={()=>setRevision(value=>value+1)}>重新读取报告</button></Alert>}
   {report.data&&<SocialRunReport run={report.data} pendingWork={socialHasWork(social)}/>}
  </details>}
 </>;
}
