import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';

test('refresh list identifies company, origin and preserves distinct attempts',async()=>{
 const h=await setupDOM();
 const {RefreshTable,itemStatus}=await import('../src/pages/TaskActivityPage.tsx');
 const item={id:'i1',run_id:'r1',company_id:'c1',company_name:'Supplier',state:'succeeded',run_status:'completed',task_id:'t1',created_at:'2026-09-24T00:00:00Z',finished_at:null,message:'',source:'market',summary:{pages_checked:6,contacts_found:12}};
 let selected='';
 try {
  await h.act(async()=>h.root.render(createElement(RefreshTable,{items:[item,{...item,id:'i2',source:'standalone'}],onOpen:i=>{selected=i.id;}})));
  assert.equal(document.querySelectorAll('tbody tr').length,2);
  assert.match(document.body.textContent!,/市场更新自动补全/);assert.match(document.body.textContent!,/单家官网复核/);
  await h.act(async()=>([...document.querySelectorAll('button')].find(b=>b.textContent==='查看详情')!).click());
  assert.equal(selected,'i1');
  assert.equal(itemStatus({...item,state:'pending',run_status:'paused'}),'已暂停');
  assert.equal(itemStatus({...item,state:'pending',run_status:'cancelled'}),'已取消');
 }finally{await h.cleanup();}
});

test('refresh detail is main content, not a header action or duplicate market list',async()=>{
 const h=await setupDOM('http://localhost/?view=tasks&task=t1&run=r1&item=i1');
 const oldFetch=globalThis.fetch;
 const item={id:'i1',run_id:'r1',company_id:'c1',company_name:'Supplier',state:'succeeded',run_status:'completed',task_id:'t1',created_at:'2026-09-24T00:00:00Z',finished_at:null,message:'',source:'market',summary:{pages_checked:6,contacts_found:12},logs:[],warnings:[],files_unparsed:[],run:{id:'r1',task_id:'t1',kind:'discovery',status:'completed',progress:{done:1,total:1,discovered:1},message:'done',created_at:'2026-09-24T00:00:00Z',finished_at:null,error:null}};
 globalThis.fetch=async(input)=>new Response(JSON.stringify(String(input)==='/api/session'?{token:'fixture'}:String(input).includes('/items/')?item:String(input)==='/api/overview'?{worker:{alive:true},source_status:{}}:[]),{headers:{'Content-Type':'application/json'}});
 try{
  const {resetSession}=await import('../src/api.ts');resetSession();
  const {default:App}=await import('../src/App.tsx');
  await h.act(async()=>{h.root.render(createElement(App));await new Promise(resolve=>setTimeout(resolve,40));});
  assert.match(document.querySelector('main')!.textContent!,/Supplier/);
  assert.equal(document.querySelector('.page-heading .panel'),null);
  assert.equal(document.querySelector('.metrics'),null);
  assert.match(document.querySelector('h1')!.textContent!,/官网复核详情/);
 }finally{globalThis.fetch=oldFetch;await h.cleanup();}
});
test('refresh rows report deduplicated new entries, raw extraction labelled as such',async()=>{
 const h=await setupDOM();
 try {
  const {RefreshTable}=await import('../src/pages/TaskActivityPage.tsx');
  const row={id:'i',run_id:'r',company_id:'c',company_name:'Supplier',state:'succeeded',run_status:'completed',task_id:'t',created_at:'2026-09-24T00:00:00Z',finished_at:null,message:'',source:'market'};
  await h.act(async()=>h.root.render(createElement(RefreshTable,{items:[{...row,summary:{pages_checked:6,contacts_found:30,entries_added:2}}],onOpen:()=>{}})));
  assert.match(document.body.textContent!,/新增联系入口 2 个（去重后）/);
  await h.act(async()=>h.root.render(createElement(RefreshTable,{items:[{...row,summary:{pages_checked:6,contacts_found:30}}],onOpen:()=>{}})));
  assert.match(document.body.textContent!,/原始提取 30 条（未去重）/);
 } finally {await h.cleanup();}
});
