import { setupDOM } from './support/dom.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';

test('website progress distinguishes company completion from scanned and remaining pages', async () => {
  const h = await setupDOM();
  const { RunCard } = await import('../src/pages/RunCard.tsx');
  try {
    await h.act(async () => h.root.render(createElement(RunCard, {
      run: {id:'fixture',task_id:'',kind:'refresh',status:'running',progress:{done:0,total:1,discovered:0,
        enrichment_done:0,enrichment_total:1,website_pages_checked:20,website_links_remaining:6},
        message:'分批扫描',created_at:'2026-09-24T00:00:00Z',finished_at:null,error:null},
      worker:{alive:true,last_seen:null},busy:false,act:async () => {},
    })));
    assert.match(document.body.textContent!, /累计读取 20 页 · 待扫描 6 个链接/);
    assert.match(document.body.textContent!, /后台分批继续，无需手动操作/);
    assert.match(document.body.textContent!, /企业官网处理进度 · 0 \/ 1/);
    assert.ok(![...document.querySelectorAll('button')].some(b=>b.textContent==='继续'));
  } finally {await h.cleanup();}
});

test('refresh run names its company and opens the matching detail', async () => {
  const h = await setupDOM();
  const { RunCard } = await import('../src/pages/RunCard.tsx');
  let opened = '';
  const run = {id:'old-run',task_id:'',kind:'refresh',company_id:'company-1',company_name:'Ambiance Apparel',status:'completed',
    progress:{done:1,total:1,discovered:0},message:'已完成',created_at:'2026-09-24T00:00:00Z',finished_at:null,error:null};
  try {
    await h.act(async () => h.root.render(createElement(RunCard, {run,
      worker:{alive:true,last_seen:null},busy:false,act:async () => {},openCompany:(id:string) => {opened=id;}})));
    const link = document.querySelector('a')!;
    assert.equal(link.textContent, 'Ambiance Apparel');
    assert.equal(link.getAttribute('href'), '/?view=detail&page=1&company=company-1');
    await h.act(async () => link.click());
    assert.equal(opened, 'company-1');
    await h.act(async () => h.root.render(createElement(RunCard, {run:{...run,company_name:null},
      worker:{alive:true,last_seen:null},busy:false,act:async () => {}})));
    assert.match(document.body.textContent!, /关联企业信息缺失/);
    assert.equal(document.querySelector('a'), null);
  } finally {await h.cleanup();}
});
