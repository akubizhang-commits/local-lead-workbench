import {useEffect, useRef, useState} from 'react';
import {api} from './api';
import {useResource} from './hooks';
import {date} from './helpers';
import {Alert, External, Field} from './ui';
import {actionMessage} from './errors';

type PlatformGate = {state:string;reason:string;manual_required:boolean;next_attempt_at?:string|null};
export type SocialRun = {
  id:string; status:string; created_at:string;parent_run_id?:string;task_id?:string;
  summary:Partial<Record<'total'|'pending'|'running'|'succeeded'|'no_results'|'blocked'|'failed'|'cancelled'|'skipped'|'new_links',number>>;
  items?:{id:string;company_id:string;company_name:string;stage:string;target:string;state:string;result:Record<string,unknown>}[];
  platform_gates?:Record<string,PlatformGate>;
};
const statusNames:Record<string,string> = {
  pending:'等待处理', queued:'排队中', running:'进行中', cancelling:'正在取消',
  succeeded:'成功', success:'成功', completed:'已结束', finished:'已结束',
  partial:'部分完成', partial_success:'部分完成', no_results:'未找到结果',
  blocked:'访问受阻', failed:'失败', error:'失败', cancelled:'已取消',
  ok:'连接可用', available:'可用', accessible:'可访问', readable:'可读取预览',
  unconfigured:'未配置', not_configured:'未配置', disabled:'未启用',
  timeout:'请求超时', rate_limited:'请求受限', login_required:'需要登录',
  skipped:'已跳过', unsupported:'暂不支持', not_found:'页面未找到',
  preview_read:'已读取公开预览', unreadable:'未读到有效预览',
  auth_required:'需要登录', challenge:'需要平台验证',
  paused:'已暂停', waiting_login:'等待登录', waiting_session:'等待会话释放',
  waiting_recovery:'等待恢复', waiting_configuration:'等待配置连接', needs_attention:'需要处理',
};
export function socialStatus(status:string) {return statusNames[status] || `未知状态（${status || '未提供'}）`;}
export function socialActive(status:string) {return ['pending','queued','running','cancelling'].includes(status);}
const stageNames:Record<string,string> = {
  platform_search:'平台站内搜索', website:'官网发现', website_discovery:'官网发现', search:'搜索候选', search_discovery:'搜索候选',
  read:'公开页面预览', page:'公开页面预览', profile:'账号基础资料', public_page:'公开页面预览',
  fetch:'读取公开页面', platform:'平台页面预览', platform_read:'平台页面预览',
  discovery:'账号发现', attribution:'归属核对', extract:'字段提取',
};
const metricNames:Record<string,string> = {total:'总项数',pending:'待处理',running:'进行中',succeeded:'成功',no_results:'未找到结果',blocked:'访问受阻',failed:'失败',cancelled:'已取消',skipped:'未发起请求',new_links:'新增候选链接'};
const resultNames:Record<string,string> = {message:'说明',reason:'原因',error:'错误',query:'搜索词',url:'链接',source_url:'来源',title:'页面标题',description:'页面描述',new_links:'新增候选链接',count:'结果数',status:'状态',evidence:'原文证据',results:'搜索候选',links:'发现链接',fields:'待确认字段',engine_failures:'未响应的搜索引擎'};
const reasonNames:Record<string,string> = {missing_website:'企业尚无官网，未发起网站请求',not_configured:'未配置搜索服务',provider_unavailable:'搜索服务不可用',unsupported:'暂不支持该平台',robots_denied:'来源读取规则不允许访问',rate_limited:'平台限制请求频率',access_denied:'平台拒绝访问',http_error:'来源 HTTP 错误',unsafe_redirect:'来源重定向地址不可访问',network_error:'网络连接失败',read_error:'页面读取失败',source_paused:'同平台读取已暂缓'};

const readablePlatformNames:Record<string,string> = {
  facebook:'Facebook',instagram:'Instagram',x:'X',tiktok:'TikTok',youtube:'YouTube',pinterest:'Pinterest',bluesky:'Bluesky',searxng:'搜索服务',
};

function ManualPlatformGates({run,disabled,onChange}:{run:SocialRun;disabled:boolean;onChange?:()=>void}) {
  const [now,setNow]=useState(Date.now),[pending,setPending]=useState('');
  const [failure,setFailure]=useState(''),[notice,setNotice]=useState('');
  const inFlight=useRef(false);
  const entries=Object.entries(run.platform_gates??{}).filter(([,gate])=>
    gate.manual_required||gate.state.startsWith('waiting_')||!!gate.next_attempt_at||!!gate.reason);
  const hasCooldown=entries.some(([,gate])=>!!gate.next_attempt_at&&Date.parse(gate.next_attempt_at)>now);
  useEffect(()=>{
    if(!hasCooldown)return;
    const timer=setInterval(()=>setNow(Date.now()),1000);
    return ()=>clearInterval(timer);
  },[hasCooldown]);
  async function resume(platform:string,gate:PlatformGate) {
    const deadline=gate.next_attempt_at?Date.parse(gate.next_attempt_at):0;
    if(disabled||inFlight.current||!Number.isFinite(deadline)||deadline>Date.now())return;
    inFlight.current=true;setPending(platform);setFailure('');setNotice('');
    try {
      await api(`/social/platforms/${encodeURIComponent(platform)}/resume`,'POST');
      setNotice(`${readablePlatformNames[platform]||platform} 已恢复，待处理项按平台间隔继续。`);
      onChange?.();
    }catch(error){setFailure(error instanceof Error ? actionMessage(error) :'恢复失败，请重试');}
    finally{inFlight.current=false;setPending('');}
  }
  if(!entries.length)return null;
  return <section className="social-run-gates" aria-label="当前平台等待状态">
    <h5>当前平台等待状态</h5>
    <p className="muted">同一平台的手动任务和市场任务共用访问间隔；请先处理登录或连接问题，再恢复。</p>
    {entries.map(([platform,gate])=>{
      const deadline=gate.next_attempt_at?Date.parse(gate.next_attempt_at):0;
      const cooling=deadline>now;
      const name=readablePlatformNames[platform]||platform;
      return <article key={platform} className="row between">
        <div><strong>{name} · {socialStatus(gate.state)}{cooling?' · 冷却中':''}</strong>
          {gate.reason&&<p>{gate.reason}</p>}
          {gate.next_attempt_at&&<p>下次尝试：{Number.isFinite(deadline)?date(gate.next_attempt_at):'时间待确认'}{cooling?' · 冷却结束前不能恢复':' · 以后台最新状态为准'}</p>}
          {gate.manual_required&&<p>需要手动处理后恢复。</p>}
        </div>
        <div className="actions">
          <a className="button" href="/?view=settings">{gate.state==='waiting_login'?'去登录':'处理连接'}</a>
          <button type="button" disabled={disabled||!!pending||cooling||!Number.isFinite(deadline)}
            onClick={()=>void resume(platform,gate)}>{pending===platform?'正在恢复…':`恢复 ${name}`}</button>
        </div>
      </article>;
    })}
    {!socialActive(run.status)&&<p className="muted">此运行已结束；恢复平台后，可新建重试运行，历史报告会保留。</p>}
    {failure&&<Alert>{failure}</Alert>}{notice&&<p role="status">{notice}</p>}
  </section>;
}

export function SocialRunReport({run,pendingWork=false,disabled=false,onChange}:{
  run:SocialRun;pendingWork?:boolean;disabled?:boolean;onChange?:()=>void;
}) {
  const stillPending=pendingWork||(run.summary.pending??0)+(run.summary.running??0)>0;
  const pendingTerminal=stillPending&&['completed','finished','succeeded','success','partial','partial_success','failed','error','blocked'].includes(run.status);
  const status=pendingTerminal?'仍有待处理项':socialStatus(run.status);
  return <div className="social-run-report">
    <div className="row between"><strong>运行报告</strong><span className={`badge social-state-${pendingTerminal?'pending':run.status}`}>{status}</span></div>
    <p className="muted">创建于 {date(run.created_at)} · <span className="social-raw-url">{run.id}</span></p>
    <dl className="social-run-metrics">{Object.entries(metricNames).map(([key,title])=><div key={key}><dt>{title}</dt><dd>{run.summary[key as keyof SocialRun['summary']] ?? '—'}</dd></div>)}</dl>
    <p className="muted">统计按处理项计数，同一企业可有多项。运行结束不代表全部成功；新增链接仍需核对归属，未找到结果不代表没有账号。</p>
    {!run.parent_run_id&&!run.task_id&&<ManualPlatformGates key={run.id} run={run} disabled={disabled} onChange={onChange}/>}
    <details className="social-run-details"><summary>逐项处理明细（{run.items?.length ?? 0}）</summary>
      <div className="social-run-items" tabIndex={0} aria-label="逐项处理明细">
        {run.items?.length ? run.items.map(item=><article key={item.id}>
          <div className="row between"><strong>{item.company_name || '企业名称未提供'}</strong><span className={`badge social-state-${item.state}`}>{socialStatus(item.state)}</span></div>
          <p>{stageNames[item.stage] || `处理阶段：${item.stage}`} · {/^https?:\/\//i.test(item.target)?<External url={item.target}/>:item.target || '未指定目标'}</p>
          <dl className="social-result">{Object.entries(item.result ?? {}).map(([key,value])=><div key={key}><dt>{resultNames[key] || key}</dt><dd>{key==='status'&&typeof value==='string'?socialStatus(value):key==='reason'&&typeof value==='string'?(reasonNames[value] || value):typeof value==='string'&&/^https?:\/\//i.test(value)?<External url={value}/>:typeof value==='object'?JSON.stringify(value,null,2):String(value ?? '未提供')}</dd></div>)}</dl>
        </article>):<p className="muted">尚无处理明细。</p>}
      </div>
    </details>
  </div>;
}

export function SocialDiscovery({companyId,onChange,disabled=false}:{companyId?:string;onChange?:()=>void;disabled?:boolean}) {
  return <DiscoveryRuns key={companyId || 'pilot'} companyId={companyId} onChange={onChange} disabled={disabled}/>;
}

function DiscoveryRuns({companyId,onChange,disabled}:{companyId?:string;onChange?:()=>void;disabled:boolean}) {
  const [revision,setRevision]=useState(0),[selected,setSelected]=useState('');
  const [busy,setBusy]=useState(false),[failure,setFailure]=useState(''),[notice,setNotice]=useState('');
  const list=useResource<SocialRun[]>(`/social/runs${companyId?`?company_id=${encodeURIComponent(companyId)}`:''}`,revision,true);
  const selectedId=selected || list.data?.[0]?.id;
  useEffect(()=>{
    if(!selected&&list.data?.[0])setSelected(list.data[0].id);
  },[list.data,selected]);
  const detail=useResource<SocialRun>(selectedId?`/social/runs/${encodeURIComponent(selectedId)}`:null,revision,true);
  const changeRef=useRef(onChange);
  changeRef.current=onChange;
  const lastResult=useRef('');
  useEffect(()=>{
    if(!detail.data)return;
    const signature=JSON.stringify(detail.data);
    if(signature!==lastResult.current){lastResult.current=signature;changeRef.current?.();}
  },[detail.data]);
  const activeRun=list.data?.some(run=>!run.parent_run_id&&socialActive(run.status)) ||
    (detail.data&&!detail.data.parent_run_id&&socialActive(detail.data.status));
  async function act(action:'start'|'read'|'platform-search'|'profile'|'cancel'|'retry') {
    if(busy)return;
    setBusy(true);setFailure('');setNotice('');
    try {
      if(action==='cancel'){
        await api(`/social/runs/${encodeURIComponent(selectedId!)}/cancel`,'POST');
        setNotice('已请求取消，请以最新运行状态为准。');
      } else {
        const starting=action==='start'||action==='read'||action==='platform-search'||action==='profile';
        const run=await api<SocialRun>(starting?'/social/runs':`/social/runs/${encodeURIComponent(selectedId!)}/retry`,'POST',starting?(companyId?{company_ids:[companyId],...(action==='read'?{read_only:true}:action==='platform-search'?{platform_search:true}:action==='profile'?{profile_only:true}:{})}:{pilot:true}):undefined);
        setSelected(run.id);
        setNotice(action==='retry'?'已创建重试运行，历史报告保留。':'已提交发现任务，请查看逐项处理结果。');
      }
      setRevision(v=>v+1);
    }catch(error){setFailure(error instanceof Error ? actionMessage(error) :'操作失败，请重试');}
    finally{setBusy(false);}
  }
  return <section className="social-discovery" aria-label={companyId?'单企社媒自动发现':'社媒试点与报告'}>
    <div className="section-title"><h4>{companyId?'自动发现社交账号':'20 家企业试点'}</h4><button className="primary" disabled={disabled||busy||list.loading||!!list.error||!!activeRun} onClick={()=>void act('start')}>{busy?'正在提交…':companyId?'开始单企发现':'启动 20 家试点'}</button></div>
    {companyId&&<button disabled={disabled||busy||list.loading||!!list.error||!!activeRun} onClick={()=>void act('profile')}>补充已有账号基础资料</button>}
    {companyId&&<button disabled={disabled||busy||list.loading||!!list.error||!!activeRun} onClick={()=>void act('read')}>读取已有账号与近期帖子</button>}
    {companyId&&<><button disabled={disabled||busy||list.loading||!!list.error||!!activeRun} onClick={()=>void act('platform-search')}>按企业名称站内搜索</button><p className="muted">使用设置中保存的 Facebook / Instagram / Twitter（X）专用会话查找候选账号，并自动补充基础资料，不依赖官网或搜索引擎。请先关闭并释放登录窗口。候选不等于已确认联系人；帖子需另行读取。</p></>}
    <p className="muted">{companyId?'已有账号可直接读取，无需等待搜索。发现任务则从官网与搜索结果查找候选。':'由服务端选择最多20家企业，逐项记录发现、无结果与访问限制。'}按设置的读取方式获取简介和有限近期帖子，不代表完整历史；不会自动确认归属或发送消息。运行结束后点击页面顶部“刷新数据”查看新资料。</p>
    {activeRun&&<p role="status">已有任务待处理或进行中，每 5 秒更新；可查看报告或取消运行。</p>}
    {failure&&<Alert>{failure}</Alert>}{notice&&<p role="status">{notice}</p>}
    {list.error&&<Alert>运行列表读取失败：{list.error}<button disabled={busy} onClick={()=>setRevision(v=>v+1)}>重新读取</button></Alert>}
    {list.loading&&<p role="status">正在读取最近运行…</p>}
    {list.data?.length ? <Field label={companyId?'最近运行':'试点及社媒运行历史'}><select value={selectedId || ''} disabled={busy} onChange={e=>setSelected(e.target.value)}>
      {selected&&!list.data.some(run=>run.id===selected)&&<option value={selected}>新提交的运行 · {selected}</option>}
      {list.data.map(run=><option key={run.id} value={run.id}>{date(run.created_at)} · {socialStatus(run.status)} · {run.id}</option>)}
    </select></Field>:!list.loading&&!list.error&&<p className="muted">尚无运行记录。</p>}
    {detail.error&&<Alert>报告读取失败：{detail.error}<button disabled={busy} onClick={()=>setRevision(v=>v+1)}>重读报告</button></Alert>}
    {detail.loading&&selectedId&&<p role="status">正在读取报告…</p>}
    {detail.data&&<><SocialRunReport run={detail.data} disabled={disabled||busy||!!detail.error}
      onChange={()=>{setRevision(v=>v+1);changeRef.current?.();}}/><div className="actions social-run-actions">
      {detail.data.task_id?<a className="button" href={`/?view=tasks&page=1&task=${encodeURIComponent(detail.data.task_id)}&run=${encodeURIComponent(detail.data.parent_run_id!)}`}>查看所属市场任务</a>:
       socialActive(detail.data.status)?<button disabled={busy||!!detail.error||detail.data.status==='cancelling'} onClick={()=>void act('cancel')}>取消此运行</button>:<button disabled={disabled||busy||!!detail.error||!!activeRun} onClick={()=>void act('retry')}>新建重试运行</button>}
      <button disabled={busy} onClick={()=>setRevision(v=>v+1)}>刷新报告</button>
    </div><p className="muted">{detail.data.task_id?'此运行由市场任务统一暂停、继续或取消。':'重试会创建新运行，保留当前历史报告。访问限制请先处理原因，再手动重试。'}</p></>}
  </section>;
}
