import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import type {Workflow,WorkflowStage} from '../src/TaskWorkflow.tsx';
test('workflow shows failure breakdown, current work and offline override',async()=>{
 const h=await setupDOM();
 try {
  const {TaskWorkflow}=await import('../src/TaskWorkflow.tsx');
  const flow={run_id:'fixture',status:'running',created_at:'2026-09-25T01:00:00Z',elapsed_seconds:125,
   current:'正在检索：clothing in fixture',updated_at:'2026-09-25T01:02:05Z',companies_found:8,website_pages:4,
   contacts_found:2,website_links_remaining:3,auto_enrich:true,maps_wait:null,
   stages:[{key:'discovery',title:'Google Maps 检索',total:4,processed:4,pending:0,running:0,succeeded:2,partial:1,failed:1,blocked:0}]};
  await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow,worker:{alive:true,last_seen:null}})));
  assert.equal(document.querySelector('.workflow-stage-status')!.textContent,'部分完成');
  assert.doesNotMatch(document.body.textContent!,/有未完成项/);
  assert.match(document.body.textContent!,/已完成 2/);
  assert.match(document.body.textContent!,/失败 1/);
  assert.equal(document.querySelector('progress')?.value,4);
  await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow,worker:{alive:false,last_seen:null}})));
  assert.match(document.body.textContent!,/后台离线，本轮暂时无法推进/);
 }finally{await h.cleanup();}
});
test('disabled website stage is not described as waiting for work',async()=>{
 const {stageText}=await import('../src/TaskWorkflow.tsx');
 assert.equal(stageText({key:'refresh',total:0,partial:0,failed:0,blocked:0} as any,{auto_enrich:false} as any).text,'未开启');
});

test('planning fallback explains the limited result instead of implying full success',async()=>{
 const h=await setupDOM();
 try {
  const {TaskWorkflow,stageText}=await import('../src/TaskWorkflow.tsx');
  const stage={key:'planning',title:'规划检索范围',total:1,processed:1,pending:0,running:0,succeeded:1,partial:0,failed:0,blocked:0,mode:'text_only',details:['未取得地区范围坐标，无法进行坐标分区检索；已改用地区名称文字检索。']};
  assert.equal(stageText(stage,{auto_enrich:true} as any).text,'已生成文字检索方案');
  const flow={status:'running',created_at:'2026-09-25T01:00:00Z',elapsed_seconds:125,current:'正在检索',updated_at:'2026-09-25T01:02:05Z',companies_found:0,website_pages:0,contacts_found:0,website_links_remaining:0,auto_enrich:true,maps_wait:null,stages:[stage]};
  await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow:flow as any,worker:{alive:true,last_seen:null}})));
  assert.match(document.body.textContent!,/无法进行坐标分区检索/);
  assert.match(document.querySelector('progress')!.getAttribute('aria-valuetext')!,/已处理 1 \/ 1 项/);
  assert.equal(document.querySelector('li.has-issues progress'),null);
  assert.doesNotMatch(document.body.textContent!,/部分完成 1/);
  assert.match(document.body.textContent!,/成功 1/);
 }finally{await h.cleanup();}
});

test('reasons preview on focus, dismiss on Escape, and expand without being lost during polling',async()=>{
 const h=await setupDOM();
 try {
  const {TaskWorkflow}=await import('../src/TaskWorkflow.tsx');
  const flow={run_id:'first',status:'running',created_at:'2026-09-25T01:00:00Z',elapsed_seconds:125,
   current:'正在检索',updated_at:'2026-09-25T01:02:05Z',companies_found:8,website_pages:4,
   contacts_found:2,website_links_remaining:3,auto_enrich:true,maps_wait:null,
   stages:[{key:'discovery',title:'Google Maps 检索',total:4,processed:4,pending:0,running:0,succeeded:2,partial:1,failed:1,blocked:0}]};
  const worker={alive:true,last_seen:null};
  await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow,worker})));
  const button=document.querySelector<HTMLButtonElement>('.workflow-hint button')!;
  const detail=document.getElementById(button.getAttribute('aria-controls')!)!;
  assert.equal(detail.hidden,true,'long reasons start collapsed');
  await h.act(async()=>button.focus());
  const tooltip=document.querySelector<HTMLElement>('[role="tooltip"]')!;
  assert.equal(tooltip.hidden,false);
  assert.match(tooltip.textContent!,/失败 1/);
  assert.equal(button.getAttribute('aria-describedby'),tooltip.id);
  await h.act(async()=>button.dispatchEvent(new h.dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
  assert.equal(tooltip.hidden,true);
  assert.equal(document.activeElement,button);
  await h.act(async()=>button.click());
  assert.equal(detail.hidden,false);
  assert.equal(button.getAttribute('aria-expanded'),'true');
  assert.equal(tooltip.hidden,true,'expanded reasons do not also show a tooltip');
  await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow:{...flow,website_pages:5},worker})));
  assert.equal(document.getElementById(button.getAttribute('aria-controls')!)!.hidden,false,'polling preserves disclosure state');
  await h.act(async()=>button.dispatchEvent(new h.dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
  assert.equal(detail.hidden,true);
  await h.act(async()=>button.click());
  await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow:{...flow,run_id:'second'},worker})));
  assert.equal(document.querySelector<HTMLElement>('.workflow-stage-details')!.hidden,true,'another round starts collapsed');
 }finally{await h.cleanup();}
});

test('manual recovery action remains visible and respects cooling, busy and offline states',async()=>{
 const h=await setupDOM();
 try {
  const {TaskWorkflow}=await import('../src/TaskWorkflow.tsx');
  let resumes=0;
  const flow={run_id:'manual',status:'running',created_at:'2026-09-25T01:00:00Z',elapsed_seconds:125,
   current:'等待地图恢复',updated_at:'2026-09-25T01:02:05Z',companies_found:8,website_pages:4,
   contacts_found:2,website_links_remaining:3,auto_enrich:true,maps_wait:null,
   maps_recovery:{phase:'manual',manual_required:true,retry_after_seconds:0,message:'等待确认',reason:'请先确认地图访问正常',next_attempt_at:null,retries_completed:3,max_auto_retries:3},
   stages:[{key:'discovery',title:'Google Maps 检索',total:4,processed:2,pending:2,running:0,succeeded:2,partial:0,failed:0,blocked:0}]};
  const props={flow,worker:{alive:true,last_seen:null},onResume:()=>{resumes++;}};
  await h.act(async()=>h.root.render(createElement(TaskWorkflow,props)));
  let button=document.querySelector<HTMLButtonElement>('.workflow-manual button')!;
  assert.equal(button.closest('[hidden]'),null);
  await h.act(async()=>button.click());
  assert.equal(resumes,1);
  for(const extra of [{busy:true},{worker:{alive:false,last_seen:null}},{flow:{...flow,maps_recovery:{...flow.maps_recovery,retry_after_seconds:61}}}]){
   await h.act(async()=>h.root.render(createElement(TaskWorkflow,{...props,...extra})));
   button=document.querySelector<HTMLButtonElement>('.workflow-manual button')!;
   assert.equal(button.disabled,true);
   await h.act(async()=>button.click());
   assert.equal(resumes,1);
  }
 }finally{await h.cleanup();}
});

test('zero tasks have no completion bar and cooldown uses minutes in the stage summary',async()=>{
 const h=await setupDOM();
 try {
  const {TaskWorkflow}=await import('../src/TaskWorkflow.tsx');
  const flow={run_id:'cooldown',status:'running',created_at:'2026-09-25T01:00:00Z',elapsed_seconds:125,
   current:'等待检索',updated_at:'2026-09-25T01:02:05Z',companies_found:0,website_pages:0,
   contacts_found:0,website_links_remaining:0,auto_enrich:true,maps_wait:null,
   maps_recovery:{phase:'cooldown',manual_required:false,retry_after_seconds:6906,message:'等待恢复',reason:'查询超时',next_attempt_at:'2026-09-25T03:00:00Z',retries_completed:1,max_auto_retries:3},
   stages:[{key:'discovery',title:'Google Maps 检索',total:102,processed:31,pending:71,running:0,succeeded:31,partial:0,failed:0,blocked:0,retry_pending:5,unattempted:66},
    {key:'refresh',title:'官网资料补全',total:0,processed:0,pending:0,running:0,succeeded:0,partial:0,failed:0,blocked:0}]};
  await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow,worker:{alive:true,last_seen:null}})));
  const summaries=[...document.querySelectorAll('.workflow-stage-row')].map(el=>el.textContent);
  assert.match(summaries[0]!,/约116分钟后尝试/);
  assert.doesNotMatch(summaries[0]!,/6906秒|原因：/);
  assert.match(summaries[1]!,/总数待确定/);
  assert.doesNotMatch(summaries[1]!,/已完成|0 \/ 0/);
  assert.equal(document.querySelectorAll('progress').length,1);
  assert.match(document.querySelector('progress')!.getAttribute('aria-valuetext')!,/成功 31，需关注 0，剩余 71/);
 }finally{await h.cleanup();}
});

test('Maps cooldown separates unexecuted queries and retries with the next attempt time',async()=>{
 const h=await setupDOM();
 try {
  const {TaskWorkflow}=await import('../src/TaskWorkflow.tsx');
  const flow={status:'running',created_at:'2026-09-25T01:00:00Z',elapsed_seconds:125,current:'读取官网',updated_at:'2026-09-25T01:02:05Z',companies_found:20,website_pages:1,contacts_found:0,website_links_remaining:0,auto_enrich:true,maps_wait:'地图冷却',maps_recovery:{phase:'cooldown',manual_required:false,message:'地图冷却，自动恢复',reason:'查询超时',next_attempt_at:'2026-09-25T03:00:00Z',retries_completed:1,max_auto_retries:3},stages:[{key:'discovery',title:'Google Maps 检索',total:102,processed:31,succeeded:31,pending:71,running:0,partial:0,failed:0,blocked:0,retry_pending:5,unattempted:66}]};
  await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow:flow as any,worker:{alive:true,last_seen:null}})));
  assert.match(document.body.textContent!,/待重试 5/);
  assert.match(document.body.textContent!,/等待恢复 66/);
  assert.match(document.body.textContent!,/下次自动尝试/);
  assert.doesNotMatch(document.body.textContent!,/受阻 71|部分完成 0/);
  assert.equal(document.querySelector('progress')?.value,31);
 }finally{await h.cleanup();}
});

const websiteStage=(extra:Partial<WorkflowStage>={}):WorkflowStage=>({
 key:'refresh',title:'官网资料补全',total:12,processed:12,pending:0,running:0,
 succeeded:2,partial:3,failed:0,blocked:7,...extra,
});
const websiteFlow=(stage=websiteStage(),extra:Partial<Workflow>={}):Workflow=>({
 run_id:'website-round',status:'partial',created_at:'2026-09-25T01:00:00Z',elapsed_seconds:125,
 current:'本次执行已结束，资料已保存',updated_at:'2026-09-25T01:02:05Z',companies_found:500,
 website_pages:18,contacts_found:2,website_links_remaining:3,auto_enrich:true,maps_wait:null,stages:[stage],...extra,
});
const websiteSummary={
 total:12,processed:12,with_pages:5,without_pages:7,pending:0,running:0,files_unparsed_companies:2,
 reasons:[{code:'access_denied',label:'网站拒绝访问或要求验证',companies:10,with_pages:3,without_pages:7}],
};

test('ended stage results distinguish partial results, success and failure without results',async()=>{
 const {stageText}=await import('../src/TaskWorkflow.tsx');
 for(const key of ['discovery','social']){
  for(const [counts,want] of [
   [{succeeded:2,partial:0,failed:1,blocked:0},'部分完成'],
   [{succeeded:0,partial:1,failed:1,blocked:0},'部分完成'],
   [{succeeded:0,partial:0,failed:1,blocked:2},'未取得结果'],
   [{succeeded:3,partial:0,failed:0,blocked:0},'已完成'],
  ] as const){
   const stage=websiteStage({key,total:3,processed:3,...counts});
   assert.equal(stageText(stage,websiteFlow(stage)).text,want,`${key} ${JSON.stringify(counts)}`);
  }
 }
});

test('website results prefer ended-company page counts and fall back safely for older payloads',async()=>{
 const {stageText}=await import('../src/TaskWorkflow.tsx');
 const cases=[
  {counts:{succeeded:0,partial:0,failed:0,blocked:12},summary:websiteSummary,pages:0,want:'部分完成'},
  {counts:{succeeded:2,partial:3,failed:0,blocked:7},summary:{...websiteSummary,with_pages:0,without_pages:12},pages:500,want:'未取得结果'},
  {counts:{succeeded:12,partial:0,failed:0,blocked:0},summary:websiteSummary,pages:18,want:'已完成'},
  {counts:{succeeded:1,partial:0,failed:0,blocked:11},summary:undefined,pages:0,want:'部分完成'},
  {counts:{succeeded:0,partial:1,failed:0,blocked:11},summary:undefined,pages:2,want:'部分完成'},
  {counts:{succeeded:0,partial:1,failed:0,blocked:11},summary:undefined,pages:0,want:'未取得结果'},
 ];
 for(const {counts,summary,pages,want} of cases){
  const stage=websiteStage({...counts,website_summary:summary});
  assert.equal(stageText(stage,websiteFlow(stage,{website_pages:pages})).text,want);
 }
});

test('pending, running, Maps recovery and social login never become ended result labels',async()=>{
 const {stageText}=await import('../src/TaskWorkflow.tsx');
 const stage=websiteStage({processed:10,pending:2,website_summary:{...websiteSummary,processed:10,pending:2}});
 assert.equal(stageText(stage,websiteFlow(stage,{status:'running'})).text,'需关注');
 assert.equal(stageText({...stage,running:1,pending:1},websiteFlow(stage,{status:'running'})).text,'处理中');
 const discovery={...stage,key:'discovery'};
 const recovery={phase:'cooldown',manual_required:false,retry_after_seconds:600,message:'等待',reason:'限流',
  next_attempt_at:'2099-01-01T00:00:00Z',retries_completed:1,max_auto_retries:3};
 assert.equal(stageText(discovery,websiteFlow(discovery,{maps_recovery:recovery})).text,'等待自动继续');
 assert.equal(stageText(discovery,websiteFlow(discovery,{maps_recovery:{...recovery,manual_required:true}})).text,'等待处理后恢复');
 const social={...stage,key:'social',pending:0,processed:12};
 const socialFlow=websiteFlow(social,{social:{run_id:'child',current:'等待登录',accounts_found:1,posts_found:0,read_posts:false,
  platforms:[{platform:'instagram',state:'waiting_login',manual_required:true,reason:'请先登录',next_attempt_at:null,
   total:12,processed:10,pending:2,running:0,succeeded:10,no_results:0,failed:0,blocked:0}]}});
 assert.equal(stageText(social,socialFlow).text,'等待平台继续');
});

test('interrupted unfinished website stages never show completion, even when cancelled items leave no pending counts',async()=>{
 const h=await setupDOM();
 try{
  const {TaskWorkflow,stageText}=await import('../src/TaskWorkflow.tsx');
  for(const [status,want] of [['paused','已暂停'],['cancelled','已取消']]){
   for(const counts of [{processed:4,pending:7,running:1},{processed:4,pending:0,running:0}]){
    const stage=websiteStage({...counts,succeeded:4,partial:0,blocked:0});
    const flow=websiteFlow(stage,{status});
    assert.equal(stageText(stage,flow).text,want);
    await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow,worker:{alive:true,last_seen:null}})));
    const row=document.querySelector('.workflow-stage')!;
    assert.equal(row.querySelector('.workflow-stage-status')!.textContent,want);
    assert.equal(row.querySelector('.workflow-step.is-complete'),null);
    assert.doesNotMatch(row.textContent!,/本次执行已结束|全部执行结束|本阶段执行结束/);
    assert.equal(document.querySelector('.workflow-summary-row .workflow-stage-status')!.textContent,want);
   }
  }
 }finally{await h.cleanup();}
});

test('website details explain this round, exclusive page outcomes, grouped reasons and unparsed files',async()=>{
 const h=await setupDOM();
 try{
  const {TaskWorkflow}=await import('../src/TaskWorkflow.tsx');
  const stage=websiteStage({website_summary:websiteSummary,details:['旧的官网异常项说明']});
  await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow:websiteFlow(stage),worker:{alive:true,last_seen:null}})));
  const row=document.querySelector('.workflow-stage')!;
  const button=row.querySelector<HTMLButtonElement>('.workflow-hint button')!;
  assert.match(button.textContent!,/官网检索明细/);
  await h.act(async()=>button.click());
  const details=document.getElementById(button.getAttribute('aria-controls')!)!;
  assert.equal(details.hidden,false);
  assert.match(details.textContent!,/本轮已处理 12 家/);
  assert.match(details.textContent!,/已读取网页 5 家 · 未读到网页 7 家/);
  assert.match(details.textContent!,/网站拒绝访问或要求验证 10 家（其中 3 家有部分网页结果）/);
  assert.match(details.textContent!,/未解析文件.*2 家|2 家.*未解析文件/);
  assert.match(details.textContent!,/不计为访问失败/);
  assert.match(details.textContent!,/本次执行已结束，未能读取的来源不影响已保存资料；重试不保证网站恢复/);
  assert.doesNotMatch(details.textContent!,/500|待处理 0|处理中 0|异常项|下方.*筛选|旧的官网/);
  assert.equal(document.querySelector('.workflow-summary-row .workflow-stage-status')!.textContent,'部分完成');
 }finally{await h.cleanup();}
});

test('website detail disclosure stays keyboard accessible and open through polling without network requests',async()=>{
 const h=await setupDOM();
 try{
  const {TaskWorkflow}=await import('../src/TaskWorkflow.tsx');
  const liveSummary={...websiteSummary,total:15,processed:12,pending:2,running:1};
  const stage=websiteStage({total:15,processed:12,pending:2,running:1,website_summary:liveSummary});
  const flow=websiteFlow(stage,{status:'running'});
  const worker={alive:true,last_seen:null};
  await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow,worker})));
  let button=document.querySelector<HTMLButtonElement>('.workflow-hint button')!;
  await h.act(async()=>button.focus());
  const tooltip=document.querySelector<HTMLElement>('[role="tooltip"]')!;
  assert.equal(tooltip.hidden,false);
  assert.match(tooltip.textContent!,/网站拒绝访问或要求验证 10 家/);
  await h.act(async()=>button.dispatchEvent(new h.dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
  assert.equal(tooltip.hidden,true);
  assert.equal(document.activeElement,button);
  await h.act(async()=>button.click());
  const details=document.getElementById(button.getAttribute('aria-controls')!)!;
  assert.match(details.textContent!,/待处理 2 家.*处理中 1 家/);
  assert.doesNotMatch(details.textContent!,/本次执行已结束/);
  const next={...stage,processed:13,pending:1,website_summary:{...liveSummary,processed:13,pending:1,with_pages:6}};
  await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow:{...flow,stages:[next]},worker})));
  assert.equal(details.hidden,false);
  assert.match(details.textContent!,/本轮已处理 13 家/);
  assert.match(details.textContent!,/已读取网页 6 家 · 未读到网页 7 家/);
  assert.equal(button.getAttribute('aria-expanded'),'true');
  await h.act(async()=>button.dispatchEvent(new h.dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
  assert.equal(details.hidden,true);
  await h.act(async()=>button.click());
  await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow:{...flow,run_id:'next-round'},worker})));
  button=document.querySelector<HTMLButtonElement>('.workflow-hint button')!;
  assert.equal(document.getElementById(button.getAttribute('aria-controls')!)!.hidden,true);
 }finally{await h.cleanup();}
});

test('legacy website details never invent a company count from pages or failed item totals',async()=>{
 const h=await setupDOM();
 try{
  const {TaskWorkflow}=await import('../src/TaskWorkflow.tsx');
  const flow=websiteFlow(websiteStage(),{website_pages:300});
  await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow,worker:{alive:true,last_seen:null}})));
  const button=document.querySelector<HTMLButtonElement>('.workflow-hint button')!;
  assert.match(button.textContent!,/官网检索明细/);
  await h.act(async()=>button.click());
  const details=document.getElementById(button.getAttribute('aria-controls')!)!;
  assert.match(details.textContent!,/本轮已处理 12 家/);
  assert.doesNotMatch(details.textContent!,/已读取网页 \d+ 家|未读到网页 \d+ 家|异常项|下方.*筛选/);
 }finally{await h.cleanup();}
});

test('maps query rows separate scraper faults from empty results and translate legacy English',async()=>{
 const h=await setupDOM();
 try {
  const {TaskWorkflow,queryFailureBadge}=await import('../src/TaskWorkflow.tsx');
  const q=(id:string,extra:Record<string,unknown>)=>({id,query:`metal roofing supply in ${id}`,state:'blocked',attempts:1,retries:0,
   last_attempt_at:'2026-10-15T00:00:00Z',next_attempt_at:null,reason:'',stop_reason:null,failure_kind:null,history:[],...extra});
  const queries=[
   q('retry',{state:'pending',next_attempt_at:'2026-10-15T00:02:00Z',failure_kind:'scraper_error',stop_reason:'browser_closed',
    reason:'Scraper browser closed before successful completion; inspect stderr.log',
    history:[{attempt:1,finished_at:'2026-10-15T00:00:00Z',stop_reason:'browser_closed',message:'Scraper browser closed before successful completion; inspect stderr.log',failure_kind:'scraper_error'}]}),
   q('crashed',{failure_kind:'scraper_error',stop_reason:'browser_closed',reason:'采集程序异常：地图页面被意外关闭，本次未完成'}),
   q('empty',{state:'pending',next_attempt_at:'2026-10-15T00:10:00Z',failure_kind:'no_results',stop_reason:'empty_output',reason:'没有找到结果：采集程序正常结束'}),
   q('paused',{state:'partial',failure_kind:null,stop_reason:'cancelled',reason:'已暂停：采集按用户操作停止，已取得的结果会保留'}),
  ];
  assert.equal(queryFailureBadge(queries[0] as any),'采集程序异常（将自动重试）');
  assert.equal(queryFailureBadge(queries[1] as any),'采集程序异常');
  assert.equal(queryFailureBadge(queries[2] as any),'没有找到结果');
  assert.equal(queryFailureBadge(queries[3] as any),null);
  const flow={run_id:'f',status:'running',created_at:'2026-10-15T00:00:00Z',elapsed_seconds:60,current:'x',updated_at:'2026-10-15T00:01:00Z',
   companies_found:0,website_pages:0,contacts_found:0,website_links_remaining:0,auto_enrich:true,maps_wait:null,
   stages:[{key:'discovery',title:'Google Maps 检索',total:4,processed:2,pending:2,running:0,succeeded:0,partial:1,failed:0,blocked:1}],
   maps_queries:queries,maps_query_issue_count:4};
  await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow:flow as any,worker:{alive:true,last_seen:null}})));
  const rows=[...document.querySelectorAll('.workflow-query-history tbody tr')];
  const text=(i:number)=>rows[i].textContent!;
  assert.match(text(0),/采集程序异常（将自动重试）/);assert.match(text(0),/计划重试：/);
  assert.match(text(1),/采集程序异常/);assert.doesNotMatch(text(1),/将自动重试/);
  assert.match(text(2),/没有找到结果/);assert.match(text(2),/计划复核：/);
  assert.equal(rows[3].querySelector('.badge'),null);
  assert.doesNotMatch(document.body.textContent!,/Scraper browser closed/);
 } finally {await h.cleanup();}
});
// P1-12：design 表 3.2 每一行一个用例——左列状态文字和颜色、右列文字、aria-valuetext；所有输出都不含“执行结束”。
test('stage status table 3.2: one status per row, "已处理 N / M" value text and right column',async()=>{
 const {stageText,stageNext,stageValueText,stageName,summaryText}=await import('../src/TaskWorkflow.tsx');
 const zero={total:0,processed:0,pending:0,running:0,succeeded:0,partial:0,failed:0,blocked:0};
 const st=(key:string,c:Record<string,unknown>={})=>({key,title:`后端 ${key}`,...zero,...c}) as any;
 const fl=(c:Record<string,unknown>={})=>({run_id:'r',status:'running',auto_enrich:true,website_pages:0,maps_recovery:null,social:null,...c}) as any;
 const gate={platform:'instagram',state:'waiting_login',total:3,processed:0,pending:3,running:0,succeeded:0,no_results:0,failed:0,blocked:0,reason:'',next_attempt_at:null,manual_required:true};
 const rows:[string,any,any,boolean,string,string,string][]=[
  ['refresh 未开启',st('refresh'),fl({auto_enrich:false}),false,'未开启','muted','—'],
  ['后台离线',st('refresh',{total:12,processed:2,pending:10}),fl(),true,'后台离线','warn','进度已保留'],
  ['已暂停',st('discovery',{total:17,processed:6,pending:11,succeeded:5,failed:1}),fl({status:'paused'}),false,'已暂停','info','剩余 11 个查询'],
  ['已取消',st('refresh',{total:12,processed:2,pending:10}),fl({status:'cancelled'}),false,'已取消','muted','剩余 10 家企业'],
  ['等待平台继续',st('social',{total:3,pending:3}),fl({social:{run_id:'s',platforms:[gate]}}),false,'等待平台继续','warn','仍有平台待处理'],
  ['暂无规划记录',st('planning'),fl(),false,'暂无规划记录','muted','—'],
  ['未开始',st('discovery'),fl(),false,'未开始','muted','—'],
  ['处理中',st('discovery',{total:17,processed:3,pending:13,running:1,succeeded:3}),fl(),false,'处理中','teal','剩余 14 个查询'],
  ['等待处理后恢复',st('discovery',{total:17,processed:5,pending:12,succeeded:5}),fl({maps_recovery:{phase:'manual',manual_required:true,retry_after_seconds:0}}),false,'等待处理后恢复','warn','需要手动恢复'],
  ['等待自动继续',st('discovery',{total:17,processed:5,pending:12,succeeded:5}),fl({maps_recovery:{phase:'cooldown',manual_required:false,retry_after_seconds:170}}),false,'等待自动继续','warn','约3分钟后尝试'],
  ['排队中',st('discovery',{total:17,pending:17}),fl(),false,'排队中','info','剩余 17 个查询'],
  ['需关注',st('refresh',{total:12,processed:3,pending:9,succeeded:2,failed:1}),fl(),false,'需关注','warn','剩余 9 家企业'],
  ['文字检索方案',st('planning',{total:1,processed:1,succeeded:1,mode:'text_only'}),fl(),false,'已生成文字检索方案','teal','—'],
  ['已完成',st('refresh',{total:12,processed:12,succeeded:12}),fl(),false,'已完成','teal','全部已处理'],
  ['部分完成',st('refresh',{total:12,processed:12,succeeded:9,failed:3}),fl({website_pages:5}),false,'部分完成','warn','全部已处理'],
  ['未取得结果',st('discovery',{total:4,processed:4,failed:4}),fl(),false,'未取得结果','warn','全部已处理'],
 ];
 for(const [name,stage,flow,offline,text,tone,next] of rows){
  assert.deepEqual(stageText(stage,flow,offline),{text,tone},name);
  assert.equal(stageNext(stage,flow,offline),next,name);
  assert.doesNotMatch(text+next,/执行结束/,name);
 }
 assert.equal(stageValueText(st('discovery',{total:17,processed:6,pending:11,succeeded:5,failed:1})),'已处理 6 / 17 个查询；成功 5，需关注 1，剩余 11');
 assert.equal(stageValueText(st('refresh',{total:12,processed:2,pending:10,succeeded:2})),'已处理 2 / 12 家企业；成功 2，需关注 0，剩余 10');
 assert.deepEqual(['planning','discovery','refresh','social','custom'].map(k=>stageName(st(k))),['规划检索范围','地图检索','官网补全','社媒补全','后端 custom']);
 const sum=(status:string,o:{offline?:boolean;active?:boolean;issue?:boolean}={})=>summaryText(fl({status}),{offline:false,active:false,issue:false,...o});
 assert.deepEqual([sum('running',{active:true}),sum('completed'),sum('completed',{issue:true}),sum('partial'),sum('paused'),sum('cancelled'),sum('running',{offline:true,active:true})],
  ['持续汇总中','本轮已结束','本轮已结束 · 有需关注项','部分完成','已暂停','已取消','进度已保留']);
});
test('paused Los Angeles discovery row reads "已暂停 · 已处理 6 / 17 个查询 · 剩余 11 个查询" with no 执行结束',async()=>{
 const h=await setupDOM();
 try {
  const {TaskWorkflow}=await import('../src/TaskWorkflow.tsx');
  const flow={run_id:'la',status:'paused',created_at:'2026-10-20T00:00:00Z',elapsed_seconds:240,current:'任务已暂停，等待手动继续',updated_at:'2026-10-20T00:04:00Z',
   companies_found:48,website_pages:0,contacts_found:23,website_links_remaining:0,auto_enrich:true,maps_wait:null,
   stages:[{key:'discovery',title:'Google Maps 检索',total:17,processed:6,pending:11,running:0,succeeded:5,partial:0,failed:1,blocked:0,retry_pending:1}]};
  await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow:flow as any,worker:{alive:true,last_seen:null}})));
  const row=document.querySelector('.workflow-stage')!;
  assert.equal(row.querySelector('h3')!.textContent,'地图检索');
  assert.equal(row.querySelector('.workflow-stage-status')!.textContent,'已暂停');
  assert.match(row.querySelector('.workflow-progress-caption')!.textContent!,/^已处理 6 \/ 17 个查询$/);
  assert.equal(row.querySelector('.workflow-stage-next>span')!.textContent,'剩余 11 个查询');
  assert.match(document.body.textContent!,/本轮已暂停，等待手动继续/);
  assert.doesNotMatch(document.body.textContent!,/执行结束|任务|Google Maps/);
 }finally{await h.cleanup();}
});
