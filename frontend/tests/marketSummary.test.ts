import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';

const base={id:'m',name:'Market',country:'US',state:'California',city:'San Jose',keywords:[],company_types:['wholesale'],
 auto_enrich:true,created_at:'2026-10-15T00:00:00Z',latest_run:null};
const coverage={scope_label:'San Jose, California, US',planned:1,done:0,unresolved:[]};

test('coverage shows which search terms the round used, and nothing for legacy rounds',async()=>{
 const h=await setupDOM();
 try {
  const {MarketSummary}=await import('../src/MarketSummary.tsx');
  await h.act(async()=>h.root.render(createElement(MarketSummary,{market:{...base,keywords:['metal roofing supply'],
   coverage:{...coverage,query_basis:'keywords',query_terms:['metal roofing supply']}}})));
  assert.match(document.body.textContent!,/检索词（按关键词）：metal roofing supply/);
  await h.act(async()=>h.root.render(createElement(MarketSummary,{market:{...base,
   coverage:{...coverage,query_basis:'company_types',query_terms:['apparel wholesaler','clothing wholesaler']}}})));
  assert.match(document.body.textContent!,/检索词（按企业类型默认）：apparel wholesaler、clothing wholesaler/);
  await h.act(async()=>h.root.render(createElement(MarketSummary,{market:{...base,coverage}})));
  assert.doesNotMatch(document.body.textContent!,/检索词/);
 } finally {await h.cleanup();}
});
test('market summary reports companies hidden by exclusion rules',async()=>{
 const h=await setupDOM();
 try {
  const {MarketSummary}=await import('../src/MarketSummary.tsx');
  const stats={total:10,new:1,existing:9,changed:0,not_seen:0};
  await h.act(async()=>h.root.render(createElement(MarketSummary,{market:{...base,coverage,market_stats:{...stats,excluded:3}}})));
  assert.match(document.body.textContent!,/已按排除规则隐藏 3 家/);
  await h.act(async()=>h.root.render(createElement(MarketSummary,{market:{...base,coverage,market_stats:{...stats,excluded:0}}})));
  assert.doesNotMatch(document.body.textContent!,/排除规则/);
 } finally {await h.cleanup();}
});
test('market summary counts companies by best entry tier and links to the list',async()=>{
 const h=await setupDOM();
 try {
  const {MarketSummary}=await import('../src/MarketSummary.tsx');
  const picked:string[]=[];
  const tier_stats={named_procurement:2,named_role:1,named_unknown:0,department_email:3,general_email:4,form_phone:0,company_phone:5,none:1};
  await h.act(async()=>h.root.render(createElement(MarketSummary,{market:{...base,coverage,tier_stats},onTier:(t:string)=>picked.push(t)})));
  const text=document.body.textContent!;
  assert.match(text,/联系入口等级（按每家最佳入口计）：具名采购 2 家 · 具名岗位 1 · 具名·岗位未明确 0 · 部门邮箱 3 · 通用邮箱 4 · 表单 \/ 联系页面 \/ 电话 0 · 仅企业电话 5 · 无可用入口 1/);
  const button=[...document.querySelectorAll('.market-tiers button')].find(b=>b.textContent!.startsWith('仅企业电话'))!;
  await h.act(async()=>{(button as HTMLButtonElement).click();});
  assert.deepEqual(picked,['company_phone']);
 } finally {await h.cleanup();}
});
