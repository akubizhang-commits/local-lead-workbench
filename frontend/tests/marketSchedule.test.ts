import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';

const task={id:'m1',name:'Market',country:'US',state:'California',city:'San Jose',keywords:['metal roofing supply'],
 company_types:['wholesale'],auto_enrich:true,created_at:'2026-10-15T00:00:00Z',latest_run:null,
 query_estimate:{terms:['metal roofing supply'],query_terms:[],geometry:'catalogue',initial_min:17,initial_max:17,max:421}};
const plan=(extra:Record<string,unknown>)=>({id:'s',task_id:'m1',company_id:null,kind:'discovery',interval_months:1,
 timezone:'UTC',next_due_at:'2026-11-15T00:00:00+00:00',enabled:true,anchor:'2026-10-15T00:00:00+00:00',...extra});

async function render(lists:unknown[][],respond:(path:string,method:string,body:unknown)=>Response|undefined=()=>undefined,props={}){
 const h=await setupDOM();
 const calls:{path:string;method:string;body:unknown}[]=[];
 let read=0;
 globalThis.fetch=async(input,init)=>{
  const path=String(input),method=init?.method||'GET';
  const body=init?.body?JSON.parse(String(init.body)):undefined;
  if(path==='/api/session')return new Response(JSON.stringify({token:'t'}));
  calls.push({path,method,body});
  const custom=respond(path,method,body);
  if(custom)return custom;
  if(method==='GET')return new Response(JSON.stringify(lists[Math.min(read++,lists.length-1)]));
  return new Response(JSON.stringify(plan({id:body?.task_id?'new':path.split('/').pop(),enabled:body?.enabled??true,
   interval_months:body?.interval_months??1})));
 };
 const {MarketSchedule}=await import('../src/MarketSchedule.tsx');
 await h.act(async()=>h.root.render(createElement(MarketSchedule,{task,...props} as never)));
 const wait=()=>h.act(async()=>{await new Promise(r=>setTimeout(r,20));});
 await wait();
 const toggle=()=>document.querySelector<HTMLInputElement>('input[role="switch"]')!;
 return {...h,calls,wait,toggle};
}

test('switch defaults to one month and only reads this market\'s plans',async()=>{
 const h=await render([[]]);
 try {
  assert.match(document.body.textContent!,/每月自动更新/);
  assert.match(document.body.textContent!,/未开启自动更新/);
  assert.match(document.body.textContent!,/预计首轮 17 次/);
  assert.equal(h.toggle().checked,false);
  assert.deepEqual(h.calls.map(c=>c.path),['/api/schedules?task_id=m1']);
  await h.act(async()=>h.toggle().click());
  await h.wait();
  const post=h.calls.find(c=>c.method==='POST')!;
  assert.equal(post.path,'/api/schedules');
  assert.equal((post.body as {interval_months:number}).interval_months,1);
  assert.equal((post.body as {kind:string}).kind,'discovery');
  assert.match(document.body.textContent!,/已开启自动更新，下次：/);
 } finally {await h.cleanup();}
});

test('turning on re-enables the first disabled plan from the server list',async()=>{
 const h=await render([[plan({id:'old',enabled:false,interval_months:3}),plan({id:'older',enabled:false})]]);
 try {
  await h.act(async()=>h.toggle().click());
  await h.wait();
  const patch=h.calls.find(c=>c.method==='PATCH')!;
  assert.equal(patch.path,'/api/schedules/old');
  assert.deepEqual(patch.body,{enabled:true});
  assert.equal(h.calls.some(c=>c.method==='POST'),false);
 } finally {await h.cleanup();}
});

test('an enabled plan shows its next time; the period and off switch patch it',async()=>{
 const h=await render([[plan({id:'on',interval_months:2})]]);
 try {
  assert.equal(h.toggle().checked,true);
  assert.match(document.body.textContent!,/每 2 个月自动更新/);
  assert.match(document.body.textContent!,/下次更新：.*（UTC）/);
  const select=document.querySelector<HTMLSelectElement>('select[aria-label="更新周期"]')!;
  await h.act(async()=>{select.value='6';select.dispatchEvent(new h.dom.window.Event('change',{bubbles:true}));});
  await h.wait();
  assert.deepEqual(h.calls.find(c=>c.method==='PATCH')!.body,{interval_months:6});
  assert.match(document.body.textContent!,/更新周期已改为每 6 个月，下次：/);
  await h.act(async()=>h.toggle().click());
  await h.wait();
  assert.deepEqual(h.calls.filter(c=>c.method==='PATCH').at(-1)!.body,{enabled:false});
  assert.match(document.body.textContent!,/已关闭自动更新/);
 } finally {await h.cleanup();}
});

test('409 schedule_exists refreshes to the existing plan',async()=>{
 const h=await render([[],[plan({id:'theirs'})]],(path,method)=>method==='POST'?new Response(JSON.stringify(
  {detail:'该市场已有启用中的定时计划',code:'schedule_exists',existing_id:'theirs'}),{status:409}):undefined);
 try {
  await h.act(async()=>h.toggle().click());
  await h.wait();
  assert.match(document.body.textContent!,/该市场已有启用中的计划，已刷新为该计划的设置。/);
  assert.equal(h.calls.filter(c=>c.method==='GET').length,2);
  assert.equal(h.toggle().checked,true);
 } finally {await h.cleanup();}
});

test('archived markets cannot switch the plan on',async()=>{
 const h=await render([[]],undefined,{archived:true});
 try {
  assert.equal(h.toggle().disabled,true);
  assert.match(document.body.textContent!,/已归档，恢复后可开启/);
 } finally {await h.cleanup();}
});

test('the settings schedule form still defaults to three months and names the market',async()=>{
 const h=await setupDOM();
 try {
  globalThis.fetch=async(input)=>String(input)==='/api/session'?new Response(JSON.stringify({token:'t'})):
   new Response(JSON.stringify([plan({task_name:'San Jose 市场'})]));
  const {Schedules}=await import('../src/Schedules.tsx');
  await h.act(async()=>h.root.render(createElement(Schedules,{companyId:'c1'})));
  await h.act(async()=>{await new Promise(r=>setTimeout(r,20));});
  assert.equal(document.querySelector<HTMLInputElement>('input[aria-label="间隔月数"]')!.value,'3');
  await h.act(async()=>h.root.render(createElement(Schedules,{})));
  await h.act(async()=>{await new Promise(r=>setTimeout(r,20));});
  assert.match(document.body.textContent!,/目标：San Jose 市场/);
 } finally {await h.cleanup();}
});
