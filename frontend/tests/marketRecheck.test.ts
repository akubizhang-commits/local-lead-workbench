import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const {recheckConfirm,recheckQueued,runTitle}=await import('../src/MarketRecheck.tsx');

test('confirmation and success texts',()=>{
 assert.equal(recheckConfirm(3),'将依次读取 3 家企业的公开官网（每家约 1–4 分钟，按访问节奏逐个执行），可在市场页暂停或取消。继续吗？');
 assert.equal(recheckQueued({run_id:'r',queued:3,skipped:[]}),'已加入官网复核队列：3 家');
 assert.equal(recheckQueued({run_id:'r',queued:2,skipped:[{company_id:'c',name:'C',reason:'无官网'}]}),
  '已加入官网复核队列：2 家；跳过 1 家（原因见市场页）');
});

test('run titles for the "already queued" notice',()=>{
 assert.equal(runTitle({kind:'discovery',snapshot:{name:'San Jose · 批发商'}}),'San Jose · 批发商');
 assert.equal(runTitle({kind:'refresh',snapshot:{batch:'website_recheck',company_ids:['a','b']}}),'批量官网复核 · 2 家');
 assert.equal(runTitle({kind:'refresh',company_name:null,snapshot:{company_id:'c'}},'Omni Sheet Metal'),'Omni Sheet Metal');
});

test('RunCard titles batch re-check rounds',async()=>{
 const {RunCard}=await import('../src/pages/RunCard.tsx');
 const html=renderToStaticMarkup(createElement(RunCard,{run:{id:'r',task_id:'',kind:'refresh',status:'running',company_id:null,
  progress:{done:0,total:2,discovered:0,enrichment_done:0,enrichment_total:2},message:'',created_at:'2026-10-18T00:00:00Z',
  snapshot:{batch:'website_recheck',market_id:'m1',company_ids:['a','b']}},worker:undefined,busy:false,act:()=>{}} as never));
 assert.match(html,/批量官网复核 · 2 家/);
 assert.doesNotMatch(html,/关联企业信息缺失/);
});

async function panel(batch:unknown,respond:(path:string,method:string)=>Response|undefined=()=>undefined){
 const h=await setupDOM();
 const calls:{path:string;method:string;body:unknown}[]=[];
 globalThis.fetch=async(input,init)=>{
  const path=String(input),method=init?.method||'GET';
  if(path==='/api/session')return new Response(JSON.stringify({token:'t'}));
  calls.push({path,method,body:init?.body?JSON.parse(String(init.body)):undefined});
  return respond(path,method)||new Response(JSON.stringify(method==='GET'?batch:{run_id:'r2',queued:20,skipped:[]}));
 };
 const prompts:string[]=[];
 h.dom.window.confirm=(text?:string)=>{prompts.push(String(text));return true;};
 globalThis.window.confirm=h.dom.window.confirm;
 const {MarketRecheck}=await import('../src/MarketRecheck.tsx');
 await h.act(async()=>h.root.render(createElement(MarketRecheck,{marketId:'m1'})));
 const wait=()=>h.act(async()=>{await new Promise(r=>setTimeout(r,20));});
 await wait();
 const click=async(text:string)=>{await h.act(async()=>[...document.querySelectorAll('button')].find(b=>b.textContent===text)!.click());await wait();};
 return {...h,calls,prompts,click};
}
const base={run_id:'r1',total:20,done:7,created_at:'2026-10-18T00:00:00Z',finished_at:null,skipped:[],pages_checked:30,entries_added:4};

test('panel without a batch offers the never-attempted selection',async()=>{
 const h=await panel(null);
 try {
  assert.match(document.body.textContent!,/对市场内尚未读取过官网的企业批量复核，一次最多 20 家。/);
  await h.click('复核尚未读取过官网的企业');
  assert.equal(h.prompts[0],recheckConfirm('最多 20'));
  const post=h.calls.find(c=>c.method==='POST')!;
  assert.equal(post.path,'/api/tasks/m1/website-recheck');
  assert.deepEqual(post.body,{select:'never_attempted'});
  assert.match(document.body.textContent!,/已加入官网复核队列：20 家/);
 } finally {await h.cleanup();}
});

test('nothing to re-check shows the server text',async()=>{
 const h=await panel(null,(path,method)=>method==='POST'?new Response(JSON.stringify({detail:'所选企业都不需要复核：没有尚未读取过官网的企业。',code:'nothing_to_recheck'}),{status:409}):undefined);
 try {
  await h.click('复核尚未读取过官网的企业');
  assert.match(document.body.textContent!,/所选企业都不需要复核：没有尚未读取过官网的企业。/);
  assert.doesNotMatch(document.body.textContent!,/请求未完成/);
 } finally {await h.cleanup();}
});

test('running, paused and finished batches',async()=>{
 const running=await panel({...base,status:'running'});
 try {
  assert.match(document.body.textContent!,/官网复核进行中：已完成 7 \/ 20 家/);
  await running.click('暂停');
  assert.ok(running.calls.some(c=>c.path==='/api/runs/r1/pause'&&c.method==='POST'));
 } finally {await running.cleanup();}
 const paused=await panel({...base,status:'paused'});
 try {
  assert.match(document.body.textContent!,/已暂停：已完成 7 \/ 20 家/);
  await paused.click('继续');
  assert.ok(paused.calls.some(c=>c.path==='/api/runs/r1/resume'));
  await paused.click('取消');
  assert.ok(paused.calls.some(c=>c.path==='/api/runs/r1/cancel'));
 } finally {await paused.cleanup();}
 const done=await panel({...base,status:'completed',done:20,finished_at:'2026-10-18T02:00:00Z',
  skipped:[{company_id:'c',name:'Kobett Metals',reason:'无官网'}]});
 try {
  const text=document.body.textContent!;
  assert.match(text,/上次批量复核：完成 20 \/ 20 家 · 结束于 .* · 新增联系入口 4 个（去重后） · 跳过 1 家/);
  assert.match(text,/Kobett Metals · 无官网/);
  assert.ok([...document.querySelectorAll('button')].some(b=>b.textContent==='复核尚未读取过官网的企业'));
 } finally {await done.cleanup();}
});

test('company library: re-checking more than 20 selected companies is disabled',async()=>{
 const h=await setupDOM('http://localhost/?view=companies&market_id=m1');
 const items=Array.from({length:21},(_,i)=>({id:`c${i}`,name:`Company ${i}`,address:'',phone:'',website:'https://x.example',
  city:'San Jose',country:'US',business_types:[],status:'candidate',notes:'',revision:1,created_at:'2026-10-01',
  last_checked_at:null,contact_count:0}));
 globalThis.fetch=async(input)=>{
  const path=String(input);
  if(path==='/api/session')return new Response(JSON.stringify({token:'t'}));
  if(path.startsWith('/api/company-page'))return new Response(JSON.stringify({items,total:21,page:1,page_size:50}));
  if(path.startsWith('/api/export-jobs'))return new Response('[]');
  return new Response('[]');
 };
 try {
  const {CompaniesPage}=await import('../src/pages/CompaniesPage.tsx');
  const {defaultFilters}=await import('../src/navigation.ts');
  await h.act(async()=>h.root.render(createElement(CompaniesPage,{version:0,filters:{...defaultFilters(),market_id:'m1'},page:1,
   busy:false,act:()=>{},refresh:()=>{},updateFilters:()=>{},setPage:()=>{},openCompany:()=>{},quickStatus:()=>{}} as never)));
  await h.act(async()=>{await new Promise(r=>setTimeout(r,30));});
  await h.act(async()=>document.querySelector<HTMLInputElement>('input[aria-label="选择当前页全部企业"]')!.click());
  const button=[...document.querySelectorAll('button')].find(b=>b.textContent==='复核所选企业官网')!;
  assert.equal(button.disabled,true);
  assert.match(document.body.textContent!,/一次最多 20 家，请减少选择/);
  await h.act(async()=>document.querySelector<HTMLInputElement>('input[aria-label="选择 Company 0"]')!.click());
  assert.equal(button.disabled,false);
 } finally {await h.cleanup();}
});
