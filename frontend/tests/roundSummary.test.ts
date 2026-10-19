import {setupDOM} from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';

const summary={run_id:'r3',round:3,status:'completed',finished_at:'2026-10-18T08:00:00+00:00',new:1,existing:19,changed:2,
 not_seen:1,total:21,entries_added:4,queries_done:17,queries_planned:17,tiers_available:true,tier_improved:2,tier_worsened:0,
 tier_delta:{named_procurement:1,general_email:-1}};
const task={id:'m1',name:'Market',country:'US',state:'California',city:'San Jose',keywords:['x'],company_types:[],
 auto_enrich:true,created_at:'2026-10-01T00:00:00Z',latest_run:null};

async function render(props:Record<string,unknown>){
 const h=await setupDOM();
 const {RoundSummary}=await import('../src/RoundSummary.tsx');
 await h.act(async()=>h.root.render(createElement(RoundSummary,props as never)));
 return h;
}

test('summary card lines and the new-companies button',async()=>{
 const opened:unknown[]=[];
 const h=await render({task:{...task,round_summary:summary},openMarket:(id:string,filters:unknown)=>opened.push([id,filters])});
 try {
  const text=document.body.textContent!;
  assert.match(text,/本轮新增摘要/);
  assert.match(text,/第 3 轮 · 已完成 · 结束于/);
  assert.match(text,/新增 1 家 · 存量 19 家 · 资料变化 2 家 · 未再次找到 1 家 · 新增联系入口 4 个（去重后）/);
  assert.match(text,/入口等级：提升 2 家 · 下降 0 家；具名采购 \+1 · 通用邮箱 −1/);
  assert.doesNotMatch(text,/本轮未全部完成/);
  const button=[...document.querySelectorAll('button')].find(b=>b.textContent==='查看本轮新增企业')!;
  await h.act(async()=>button.click());
  assert.deepEqual(opened,[['m1',{run_id:'r3',segment:'new'}]]);
 } finally {await h.cleanup();}
});

test('missing tiers, unfinished rounds and a round still running',async()=>{
 const h=await render({task:{...task,latest_run:{id:'r4',status:'running'},
  round_summary:{...summary,status:'cancelled',tiers_available:false,tier_improved:null,tier_worsened:null,tier_delta:null}}});
 try {
  const text=document.body.textContent!;
  assert.match(text,/入口等级变化：这一轮缺少开始时的等级记录（例如开始于升级之前），无法比较。/);
  assert.match(text,/本轮未全部完成，数字只反映已执行的查询。/);
  assert.match(text,/第 3 轮 · 已取消/);
  assert.match(text,/当前一轮尚在进行，以下为上一轮/);
 } finally {await h.cleanup();}
 const partial=await render({task:{...task,round_summary:{...summary,status:'partial'}}});
 try {assert.match(document.body.textContent!,/本轮未全部完成/);} finally {await partial.cleanup();}
});

test('no finished round renders nothing',async()=>{
 const h=await render({task:{...task,round_summary:null}});
 try {assert.equal(document.querySelector('.round-summary'),null);} finally {await h.cleanup();}
});
