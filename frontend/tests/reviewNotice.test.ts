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
