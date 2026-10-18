import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';

const rules=[{id:'r1',scope:'market',market_id:'m',kind:'category',value:'Roofing contractor',value_key:'roofing contractor',created_at:'2026',matched:8},
 {id:'r2',scope:'global',market_id:null,kind:'keyword',value:'contractor',value_key:'contractor',created_at:'2026',matched:3}];
const categories=[{category:'Roofing supply store',category_key:'roofing supply store',count:12}];

function mockApi(calls:string[]) {
 globalThis.fetch=async(input,init)=>{const url=String(input);calls.push(`${init?.method||'GET'} ${url}`);
  const body=url==='/api/session'?{token:'fixture'}:url.startsWith('/api/exclusion-rules')?rules:url.startsWith('/api/company-categories')?categories:[];
  return new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json'}});};
}

test('exclusion panel lists rules with scope and match counts and explains keyword matching',async()=>{
 const h=await setupDOM();
 const calls:string[]=[];
 mockApi(calls);
 try {
  const {ExclusionRules}=await import('../src/ExclusionRules.tsx');
  await h.act(async()=>{h.root.render(createElement(ExclusionRules,{marketId:'m',version:0,busy:false,act:async()=>{}}));await new Promise(r=>setTimeout(r,30));});
  const text=document.body.textContent!;
  assert.match(text,/命中规则的企业默认隐藏，不会删除，也不改变客户匹配状态；删除规则即可恢复显示。/);
  assert.match(text,/类别：Roofing contractor · 本市场 · 命中 8 家/);
  assert.match(text,/关键词：contractor · 全局 · 命中 3 家/);
  assert.match(text,/关键词按包含匹配，例如 roof 也会命中 Roofing supply。/);
  assert.equal((document.querySelector('input[placeholder="如 contractor、roofing contractor"]') as HTMLInputElement).closest('label')!.textContent!.trim().startsWith('排除关键词'),true);
  assert.match(document.querySelector('select')!.textContent!,/Roofing supply store（12）/);
  assert.ok(calls.includes('GET /api/exclusion-rules?market_id=m'));
 } finally {await h.cleanup();}
});

test('hidden-company notice switches between hide, only and include',async()=>{
 const h=await setupDOM();
 try {
  const {ExcludedNotice,CategoryTags}=await import('../src/pages/CompaniesPage.tsx');
  const modes:string[]=[];
  await h.act(async()=>h.root.render(createElement(ExcludedNotice,{hidden:4,mode:'',onChange:(m:string)=>modes.push(m)})));
  assert.match(document.body.textContent!,/已按排除规则隐藏 4 家 · 查看已排除 \/ 全部显示/);
  const buttons=[...document.querySelectorAll('button')];
  await h.act(async()=>{buttons[0].click();buttons[1].click();});
  assert.deepEqual(modes,['only','include']);
  await h.act(async()=>h.root.render(createElement(ExcludedNotice,{hidden:0,mode:'',onChange:()=>{}})));
  assert.equal(document.body.textContent,'');
  await h.act(async()=>h.root.render(createElement(CategoryTags,{categories:['A','B','C']})));
  assert.equal(document.body.textContent,'AB+1');
  assert.equal(document.querySelector('.tags')!.getAttribute('title'),'A、B、C');
  await h.act(async()=>h.root.render(createElement(CategoryTags,{categories:[]})));
  assert.equal(document.body.textContent,'—');
 } finally {await h.cleanup();}
});

test('type hints read as unverified',async()=>{
 const {hintLabel}=await import('../src/helpers.ts');
 assert.equal(hintLabel('wholesaler'),'批发商 · 待核实');
});
