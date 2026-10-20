import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import type {Workflow} from '../src/TaskWorkflow.tsx';
import type {Task} from '../src/types.ts';
import {defaultFilters,type Route} from '../src/navigation.ts';
import type {SocialRun} from '../src/SocialDiscovery.tsx';

const stage=(key:string,title:string)=>({key,title,total:4,processed:4,pending:0,running:0,succeeded:4,partial:0,failed:0,blocked:0});
const platform=(name:string,state='pending',extra={})=>({
 platform:name,state,total:4,processed:1,pending:3,running:0,succeeded:1,no_results:1,failed:0,blocked:0,
 reason:'',next_attempt_at:null,manual_required:false,...extra,
});
function workflow(extra={}):Workflow {
 return {run_id:'market-round',status:'completed',created_at:'2026-09-25T01:00:00Z',updated_at:'2026-09-25T02:00:00Z',
  elapsed_seconds:3600,current:'正在读取官网：Supplier；正在检索社媒：Other Supplier',companies_found:4,
  website_pages:6,contacts_found:2,website_links_remaining:1,auto_enrich:true,maps_wait:null,
  stages:[stage('planning','规划检索范围'),stage('discovery','Google Maps 检索'),stage('refresh','官网资料补全'),
   {...stage('social','社媒检索与补全'),processed:1,succeeded:1,pending:3,no_results:1}],
  social_only:false,social:{run_id:'social-child',current:'等待 Facebook 登录',accounts_found:7,posts_found:2,read_posts:false,
   platforms:[platform('facebook','waiting_login',{manual_required:true,reason:'请先登录专用会话'}),
    platform('instagram','completed',{total:4,processed:4,succeeded:4,pending:0}),
    platform('x','waiting_recovery',{reason:'请求频率受限',next_attempt_at:'2099-01-01T00:00:00Z'})]},
  ...extra} as Workflow;
}
const task:Task={id:'market',name:'Fixture market',country:'US',state:'Fixture',city:'Example',keywords:[],
 company_types:['wholesale'],auto_enrich:true,created_at:'2026-09-25T00:00:00Z',latest_run:null};
const route:Route={view:'tasks',task:'market',run:'historical',selected:null,filters:defaultFilters(),page:2};
const button=(text:string,root:ParentNode=document)=>{
 const found=[...root.querySelectorAll<HTMLButtonElement>('button')].find(b=>b.textContent?.includes(text));
 assert.ok(found,`Missing button: ${text}`);return found;
};
async function fixture(options:{flow?:Workflow|null;task?:Task;failMutation?:boolean;failReport?:boolean}={}) {
 const h=await setupDOM();
 const requests:{path:string;method:string;body:unknown;rawBody:unknown}[]=[],navigations:Route[]=[];
 let refreshed=0;
 globalThis.fetch=async(input,init)=>{
  const path=String(input),method=init?.method||'GET';
  requests.push({path,method,body:init?.body?JSON.parse(String(init.body)):undefined,rawBody:init?.body});
  const response=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status});
  if(path==='/api/session')return response({token:'fixture'});
  if(method!=='GET'){
   if(options.failMutation)return response({detail:'Fixture operation failed'},409);
   if(path==='/api/tasks/market/social-settings')return response({...(options.task||task),...JSON.parse(String(init?.body))});
   if(path==='/api/tasks/market/social')return response({id:'new-social-round',status:'queued'});
   if(path==='/api/social/platforms/facebook/resume')return response({state:'pending'});
   throw new Error(`Unexpected mutation ${method} ${path}`);
  }
  if(path.startsWith('/api/tasks/market/activity?'))return response({items:[],total:0,page:1,page_size:50,rounds:[],workflow:options.flow===null?null:options.flow||workflow()});
  if(path.startsWith('/api/schedules'))return response([]);
  if(path==='/api/social/runs/social-child'){
   if(options.failReport)return response({detail:'Fixture report unavailable'},503);
   return response({id:'social-child',status:'completed',created_at:'2026-09-25T01:00:00Z',
    summary:{total:16,pending:5,running:0,succeeded:11},items:[{id:'item-1',company_id:'company-1',company_name:'Report Supplier',stage:'platform_search',target:'Fixture query',state:'pending',result:{reason:'Please log in'}}]});
  }
  throw new Error(`Unexpected read ${path}`);
 };
 const {TaskActivityPage}=await import('../src/pages/TaskActivityPage.tsx');
 const props={route,tasks:[options.task||task],version:0,worker:{alive:true,last_seen:null},busy:false,
  act:async(work:()=>Promise<unknown>)=>{await work();},refresh:()=>{refreshed++;},
  navigate:(next:Route)=>{navigations.push(next);},openCompany:()=>{},openMarket:()=>{}};
 await h.act(async()=>h.root.render(createElement(TaskActivityPage,props)));
 return {...h,requests,navigations,props,refreshed:()=>refreshed,
  mutations:()=>requests.filter(r=>r.method!=='GET'),
  async render(extra={}){await h.act(async()=>h.root.render(createElement(TaskActivityPage,{...props,...extra})));}};
}

test('legacy markets stay opted out and mounting a workflow never starts social work or loads reports',async()=>{
 const h=await fixture();
 try{
  const toggle=document.querySelector<HTMLInputElement>('input[name="auto_social"]');
  assert.ok(toggle);assert.equal(toggle.checked,false);
  assert.equal(h.mutations().length,0);
  assert.ok(button('补充社媒资料'));
  assert.equal(h.requests.some(r=>r.path.includes('/social/runs/')),false);
 }finally{await h.cleanup();}
});

test('future settings and explicit supplementation use separate payloads and select the returned market round',async()=>{
 const h=await fixture();
 try{
  const input=(selector:string)=>{const node=document.querySelector<HTMLInputElement>(selector);assert.ok(node);return node;};
  await h.act(async()=>{
   input('input[name="auto_social"]').click();
   input('input[name="social_platforms"][value="instagram"]').click();
   input('input[name="social_read_posts"]').click();
  });
  assert.equal(h.mutations().length,0,'Editing settings has no side effects');
  await h.act(async()=>button('保存后续设置').click());
  assert.deepEqual(h.mutations().map(({path,method,body})=>({path,method,body})),[{
   path:'/api/tasks/market/social-settings',method:'PATCH',
   body:{auto_social:true,social_platforms:['facebook','x'],social_read_posts:true},
  }]);
  assert.match(document.body.textContent!,/后续.*生效|仅影响后续/);
  await h.act(async()=>button('补充社媒资料').click());
  assert.deepEqual(h.mutations()[1].body,{social_platforms:['facebook','x'],social_read_posts:true});
  assert.equal(h.mutations()[1].path,'/api/tasks/market/social');
  assert.equal(h.navigations.at(-1)?.run,'new-social-round');
  assert.equal(h.navigations.at(-1)?.page,1);
  assert.ok(h.refreshed()>0);
 }finally{await h.cleanup();}
});

test('social actions surface API errors and an empty selection cannot submit',async()=>{
 const h=await fixture({flow:null,failMutation:true});
 try{
  await h.act(async()=>button('补充社媒资料').click());
  assert.match(document.querySelector('[role="alert"]')!.textContent!,/Fixture operation failed/);
  assert.equal(h.navigations.length,0);
  await h.act(async()=>document.querySelectorAll<HTMLInputElement>('input[name="social_platforms"]').forEach(input=>input.click()));
  await h.act(async()=>button('补充社媒资料').click());
  assert.equal(h.mutations().length,1);
  assert.match(document.body.textContent!,/至少选择一个社媒平台/);
 }finally{await h.cleanup();}
});

test('parallel stages use company counts and pending social prevents a terminal summary',async()=>{
 const h=await fixture();
 try{
  const rows=[...document.querySelectorAll('.workflow-stage-row')];
  assert.deepEqual(rows.map(row=>row.querySelector('.workflow-step')!.textContent),['1','2','3A','3B']);
  assert.match(document.querySelector('.workflow-guide')!.textContent!,/并行/);
  assert.match(rows[3].textContent!,/1 \/ 4 家企业/);
  assert.equal(rows[3].querySelector('progress')!.value,1);
  assert.doesNotMatch(document.querySelector('.workflow-summary-row')!.textContent!,/本轮已结束|已完成/);
  assert.equal(document.querySelector('.workflow-summary-row .workflow-step')!.textContent,'4');
  assert.match(document.querySelector('.workflow-current')!.textContent!,/正在读取官网：Supplier；正在检索社媒：Other Supplier/);
  assert.match(document.body.textContent!,/社媒账号.*7|7.*个账号/);
  assert.doesNotMatch(document.body.textContent!,/社媒搜索与帖子采集是独立任务，不计入这里/);
  await h.act(async()=>button('平台明细',rows[3]).click());
  const detail=document.getElementById(button('平台明细',rows[3]).getAttribute('aria-controls')!)!;
  assert.equal(detail.hidden,false);
  assert.match(detail.textContent!,/正常无结果 1/);
  assert.match(detail.textContent!,/1 \/ 4 家企业/,'Each platform also counts companies');
  assert.match(detail.textContent!,/不累加各平台数量/);
  assert.match(detail.textContent!,/按处理项计数/);
 }finally{await h.cleanup();}
});

test('social-only rounds omit maps, website, and planning stages even with legacy zero placeholders',async()=>{
 const h=await fixture({flow:workflow({social_only:true})});
 try{
  assert.deepEqual([...document.querySelectorAll('.workflow-stage h3')].map(el=>el.textContent),['社媒补全','结果汇总']);
  assert.equal(document.querySelectorAll('.workflow-stages progress').length,1);
  assert.doesNotMatch(document.querySelector('.workflow-guide')!.textContent!,/并行/);
 }finally{await h.cleanup();}
});

test('platform gates have visible internal settings and bodyless restore; cooldown cannot be bypassed',async()=>{
 const h=await fixture();
 try{
  const login=button('去登录');
  assert.equal(login.closest('[hidden]'),null);
  await h.act(async()=>login.click());
  assert.equal(h.navigations.at(-1)?.view,'settings');
  assert.equal(h.mutations().length,0);
  const facebook=button('恢复 Facebook'),x=button('恢复 X');
  assert.equal(x.disabled,true);
  assert.match(x.closest('.workflow-social-gate')!.textContent!,/2099|下次/);
  await h.act(async()=>x.click());
  assert.equal(h.mutations().length,0);
  await h.act(async()=>facebook.click());
  assert.equal(h.mutations()[0].path,'/api/social/platforms/facebook/resume');
  assert.equal(h.mutations()[0].rawBody,undefined);
  assert.equal(document.querySelector('a[href*="facebook.com"]'),null);
  await h.render({worker:{alive:false,last_seen:null}});
  assert.equal(button('恢复 Facebook').disabled,true);
  await h.render({busy:true});
  assert.equal(button('恢复 Facebook').disabled,true);
 }finally{await h.cleanup();}
});

test('child report fetches only on explicit expansion and keeps pending work visible despite a terminal label',async()=>{
 const h=await fixture();
 try{
  await h.act(async()=>button('平台明细').click());
  assert.equal(h.requests.some(r=>r.path.includes('/social/runs/')),false);
  const summary=document.querySelector<HTMLElement>('.workflow-social-report summary');
  assert.ok(summary);
  await h.act(async()=>{
   summary.click();
   summary.parentElement!.dispatchEvent(new h.dom.window.Event('toggle'));
  });
  assert.equal(h.requests.filter(r=>r.path==='/api/social/runs/social-child').length,1);
  assert.match(document.querySelector('.social-run-report')!.textContent!,/Report Supplier/);
  assert.doesNotMatch(document.querySelector('.social-run-report .badge')!.textContent!,/已结束/);
 }finally{await h.cleanup();}
});

test('missing optional social state remains usable without inventing a completed social stage',async()=>{
 const h=await fixture({flow:workflow({social:null})});
 try{
  await h.act(async()=>button('平台明细').click());
  assert.match(document.body.textContent!,/平台明细.*尚未|尚未.*平台明细/);
  assert.equal(document.querySelector('.workflow-social-report'),null);
  assert.doesNotMatch(document.querySelector('.workflow-summary-row')!.textContent!,/本轮已结束/);
 }finally{await h.cleanup();}
});

test('terminal round with social failures reports attention rather than a successful completion',async()=>{
 const flow=workflow();
 flow.stages[3]={...flow.stages[3],processed:4,pending:0,succeeded:3,failed:1};
 flow.social!.platforms=flow.social!.platforms.map(p=>({...p,state:'needs_attention',processed:4,pending:0,succeeded:3,failed:1}));
 const h=await fixture({flow});
 try{
  assert.match(document.querySelector('.workflow-summary-row .workflow-stage-status')!.textContent!,/需关注/);
  assert.doesNotMatch(document.querySelector('.workflow-summary-row .workflow-stage-status')!.textContent!,/已完成/);
  assert.equal(!!document.querySelector('.workflow-social-gates'),false,'Finished platforms must not offer recovery');
 }finally{await h.cleanup();}
});

test('social report preserves running, paused and explicit platform waiting states',async()=>{
 const h=await setupDOM();
 try{
  const {SocialRunReport}=await import('../src/SocialDiscovery.tsx');
  const run:SocialRun={id:'child',created_at:'2026-09-25T01:00:00Z',status:'running',summary:{total:2,pending:1,running:1}};
  for(const [status,title] of [['running','进行中'],['paused','已暂停'],['waiting_login','等待登录'],
   ['waiting_session','等待会话释放'],['waiting_recovery','等待恢复'],['waiting_configuration','等待配置连接']]){
   await h.act(async()=>h.root.render(createElement(SocialRunReport,{run:{...run,status}})));
   assert.equal(document.querySelector('.social-run-report .badge')!.textContent,title);
  }
 }finally{await h.cleanup();}
});

test('every platform waiting state explains the gate while unaffected platforms continue',async()=>{
 const flow=workflow({status:'running'});
 flow.stages[3]={...flow.stages[3],pending:2,running:1};
 const h=await fixture({flow});
 try{
  const {TaskWorkflow}=await import('../src/TaskWorkflow.tsx');
  for(const [state,title] of [['waiting_login','等待登录'],['waiting_session','等待会话释放'],
   ['waiting_configuration','等待配置连接'],['waiting_recovery','等待恢复']]){
   const social={...flow.social!,platforms:[
    {...flow.social!.platforms[0],state:state as any,reason:'Fixture gate',manual_required:true},
    {...flow.social!.platforms[1],state:'running' as const,pending:1,running:1,processed:2},
   ]};
   await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow:{...flow,social},
    worker:{alive:true,last_seen:null},onOpenSettings:()=>{}})));
   assert.match(document.querySelector('.workflow-social-gate')!.textContent!,new RegExp(title));
   assert.match(document.querySelectorAll('.workflow-stage-row')[3].textContent!,/处理中/);
   await h.act(async()=>button('平台明细').click());
   assert.match(document.querySelector('.workflow-social-platforms')!.textContent!,/Instagram处理中/);
   await h.act(async()=>button('平台明细').click());
  }
 }finally{await h.cleanup();}
});

test('failed resume stays visible and retry submits once; a closed report can be retried explicitly',async()=>{
 const options={failMutation:true,failReport:true};
 const h=await fixture(options);
 try{
  await h.act(async()=>button('恢复 Facebook').click());
  assert.match(document.querySelector('.workflow-social-gates [role="alert"]')!.textContent!,/Fixture operation failed/);
  assert.equal(button('恢复 Facebook').disabled,false);
  options.failMutation=false;
  await h.act(async()=>button('恢复 Facebook').click());
  assert.equal(h.mutations().length,2);
  assert.equal(document.querySelector('.workflow-social-gates [role="alert"]'),null);
  await h.act(async()=>button('平台明细').click());
  const summary=document.querySelector<HTMLElement>('.workflow-social-report summary')!;
  await h.act(async()=>{summary.click();summary.parentElement!.dispatchEvent(new h.dom.window.Event('toggle'));});
  assert.match(document.querySelector('.workflow-social-report [role="alert"]')!.textContent!,/Fixture report unavailable/);
  options.failReport=false;
  await h.act(async()=>button('重新读取报告').click());
  assert.match(document.querySelector('.social-run-report')!.textContent!,/Report Supplier/);
  assert.equal(h.requests.filter(r=>r.path==='/api/social/runs/social-child').length,2);
  await h.act(async()=>button('平台明细').click());
  assert.equal(summary.closest<HTMLElement>('[hidden]')!.hidden,true);
 }finally{await h.cleanup();}
});

test('saved preferences and unsaved edits survive normal polling; disabled controls never submit',async()=>{
 const savedTask={...task,auto_social:true,social_platforms:['x'] as const,social_read_posts:true};
 const h=await fixture({task:savedTask as unknown as Task});
 try{
  const input=(selector:string)=>document.querySelector<HTMLInputElement>(selector)!;
  assert.equal(input('input[name="social_read_posts"]').checked,true);
  assert.equal(input('input[value="x"]').checked,true);
  assert.equal(input('input[value="facebook"]').checked,false);
  await h.act(async()=>input('input[value="facebook"]').click());
  await h.render({tasks:[{...savedTask}],busy:true});
  assert.equal(input('input[value="facebook"]').checked,true);
  assert.equal(button('补充社媒资料').disabled,true);
  await h.act(async()=>button('补充社媒资料').click());
  assert.equal(h.mutations().length,0);
  await h.render({tasks:[{...savedTask}]});
  await h.act(async()=>button('补充社媒资料').click());
  assert.deepEqual(h.mutations()[0].body,{social_platforms:['facebook','x'],social_read_posts:true});
 }finally{await h.cleanup();}
});

test('social-only rounds with all normal no-results complete successfully without fabricated failures',async()=>{
 const flow=workflow({social_only:true});
 flow.stages[3]={...flow.stages[3],processed:4,pending:0,succeeded:4,no_results:4};
 flow.social!.platforms=flow.social!.platforms.map(p=>({...p,state:'completed',pending:0,processed:4,succeeded:4,no_results:4}));
 const h=await fixture({flow});
 try{
  assert.match(document.querySelector('.workflow-summary-row')!.textContent!,/本轮已结束/);
  assert.equal(document.querySelector('li.has-issues'),null);
  await h.act(async()=>button('平台明细').click());
  assert.match(document.querySelector('.workflow-stage-details')!.textContent!,/正常无结果 4/);
 }finally{await h.cleanup();}
});

test('paused and cancelled rounds retain pending counts and cannot resume a platform through the round',async()=>{
 const h=await fixture();
 try{
  const {TaskWorkflow}=await import('../src/TaskWorkflow.tsx');
  for(const status of ['paused','cancelled']){
   await h.act(async()=>h.root.render(createElement(TaskWorkflow,{flow:workflow({status}),worker:{alive:true,last_seen:null}})));
   assert.equal(button('恢复 Facebook').disabled,true);
   assert.match(document.querySelectorAll('.workflow-stage-row')[3].textContent!,new RegExp(status==='paused'?'已暂停':'已取消'));
   assert.doesNotMatch(document.querySelector('.workflow-summary-row')!.textContent!,/持续汇总中|本轮已结束/);
  }
 }finally{await h.cleanup();}
});

test('market-owned social reports link parent controls without disabling independent company searches',async()=>{
 const h=await setupDOM();
 const run={id:'child',parent_run_id:'parent',task_id:'market',status:'running',
  created_at:'2026-09-25T00:00:00Z',summary:{total:3,pending:3},items:[]};
 globalThis.fetch=async(input)=>{
  const path=String(input);
  if(path==='/api/session')return new Response(JSON.stringify({token:'fixture'}));
  if(path==='/api/social/runs?company_id=company')return new Response(JSON.stringify([run]));
  if(path==='/api/social/runs/child')return new Response(JSON.stringify(run));
  throw new Error(`Unexpected request ${path}`);
 };
 try{
  const {SocialDiscovery}=await import('../src/SocialDiscovery.tsx');
  await h.act(async()=>h.root.render(createElement(SocialDiscovery,{companyId:'company'})));
  assert.equal(button('按企业名称站内搜索').disabled,false);
  assert.equal(button('补充已有账号基础资料').disabled,false);
  assert.ok(document.querySelector('a[href*="task=market"][href*="run=parent"]'));
  assert.ok(![...document.querySelectorAll('button')].some(b=>b.textContent==='取消此运行'));
 }finally{await h.cleanup();}
});

test('social-only RunCard labels social progress and omits map/website counters',async()=>{
 const h=await setupDOM();
 try{
  const {RunCard}=await import('../src/pages/RunCard.tsx');
  const run={id:'parent',task_id:'market',kind:'social',status:'running',
   progress:{done:1,total:3,discovered:0,social_done:1,social_total:3,enrichment_done:0,enrichment_total:0},
   message:'补充社媒',created_at:'2026-09-25T00:00:00Z',finished_at:null,error:null};
  await h.act(async()=>h.root.render(createElement(RunCard,{run,worker:{alive:true,last_seen:null},busy:false,act:async()=>{}})));
  assert.match(document.body.textContent!,/社媒补全 · 已处理 1 \/ 3 家企业/);
  assert.doesNotMatch(document.body.textContent!,/官网补全|地图检索 ·|市场累计/);
 }finally{await h.cleanup();}
});
