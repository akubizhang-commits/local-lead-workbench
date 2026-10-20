import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';

test('decision notice reports how many same-value suggestions were closed',async()=>{
 const h=await setupDOM();
 try {
  const {decisionNotice}=await import('../src/pages/ReviewsPage.tsx');
  const notice=decisionNotice('审核决定已保存');
  assert.equal(notice({ok:true,auto_closed:2}),'审核决定已保存，已同时关闭 2 条相同建议');
  assert.equal(notice({ok:true,auto_closed:0}),'审核决定已保存');
  assert.equal(notice({ok:true}),'审核决定已保存');
 } finally {await h.cleanup();}
});

test('merged review lists how many sources support it',async()=>{
 const h=await setupDOM();
 try {
  const {ReviewList}=await import('../src/ReviewList.tsx');
  const review={id:'r',company_id:'c',company_name:'Acme Fixture Inc.',kind:'field_update',field:'name',
   old_value:'Acme Fixture Inc.',new_value:'Acme Fixture',source_url:'https://acme.example/1',evidence:'Acme Fixture',
   status:'pending',created_at:'2026-10-15T00:00:00Z',evidence_count:88,
   evidence_items:[{source_url:'https://acme.example/88',evidence:'x',observed_at:'2026-10-15T00:00:00Z'},
    {source_url:'https://acme.example/87',evidence:'x',observed_at:'2026-10-15T00:00:00Z'}]};
  await h.act(async()=>h.root.render(createElement(ReviewList,{reviews:[review],busy:false,onOpen:()=>{},onDecision:()=>{}})));
  assert.match(document.body.textContent!,/共 88 处来源支持此建议/);
  assert.equal(document.querySelectorAll('.review-evidence li a').length,2);
  await h.act(async()=>h.root.render(createElement(ReviewList,{reviews:[{...review,evidence_count:1}],busy:false,onOpen:()=>{},onDecision:()=>{}})));
  assert.doesNotMatch(document.body.textContent!,/处来源支持/);
 } finally {await h.cleanup();}
});
test('contact change reviews offer to keep the previous record',async()=>{
 const h=await setupDOM();
 try {
  const {ReviewList}=await import('../src/ReviewList.tsx');
  const contact={id:'k',company_id:'c',name:'Robert Other',role:'Sales Rep',email:'bob@alpha.example',url:'',kind:'general',
   role_status:'explicit',association_status:'same_block',source_url:'https://alpha.example/about',evidence:'x',observed_at:'',review_status:'auto_recorded',fields:[]};
  const base={id:'r',company_id:'c',company_name:'Alpha',kind:'contact',field:'contact',old_value:'{}',new_value:'k',
   source_url:'https://alpha.example/about',evidence:'x',status:'pending',created_at:'2026-10-15T00:00:00Z',
   previous_contact:{name:'Bob Buyer',role:'Buyer'},contact};
  for(const change of ['proposal','legacy'] as const){
   await h.act(async()=>h.root.render(createElement(ReviewList,{reviews:[{...base,contact_change:change}],busy:false,onOpen:()=>{},onDecision:()=>{}})));
   const buttons=[...document.querySelectorAll('.review-card .actions button')].map(b=>b.textContent);
   assert.deepEqual(buttons,['不采用 · 保留此前记录','采用新信息']);
   assert.match(document.body.textContent!,/不采用只丢弃本次采集的新信息，此前记录和采用状态不变。/);
  }
  await h.act(async()=>h.root.render(createElement(ReviewList,{reviews:[{...base,old_value:'',contact_change:'new'}],busy:false,onOpen:()=>{},onDecision:()=>{}})));
  assert.deepEqual([...document.querySelectorAll('.review-card .actions button')].map(b=>b.textContent),['标记为不采用','确认联系入口']);
 } finally {await h.cleanup();}
});
