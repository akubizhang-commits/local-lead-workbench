import {setupDOM} from './support/dom.ts';
import {createElement} from 'react';
import test from 'node:test';
import assert from 'node:assert/strict';
import {warningSummary} from '../src/warningSummary.ts';

test('repeated failures become short Chinese reasons without URLs', () => {
  const error = 'https://example.org/a: Response body byte limit exceeded；'
    + 'https://example.org/b: Response body byte limit exceeded；robots disallows /shipping；'
    + 'Cross-site redirect requires manual identity review；Page limit reached';
  assert.equal(warningSummary(error), '页面过大；网站访问规则或规则文件读取受限；跳转到其他网站；旧版单次页面上限');
  assert.ok(!warningSummary('unexpected failure').includes('undefined'));
});

test('maps warnings separate scraper faults, real empty results, time limits and user pauses', async () => {
  const {warningReasons} = await import('../src/warningSummary.ts');
  const reasons = (text: string) => warningReasons(text).map(item => item.reason);
  assert.deepEqual(reasons('采集程序异常：地图页面被意外关闭，本次未完成'), ['采集程序异常：地图页面被意外关闭']);
  // Rows stored before the 2026-10-16 wording change still map to the same reason.
  assert.deepEqual(reasons('采集程序异常：地图浏览器意外关闭（本机浏览器进程退出），本次未完成'), ['采集程序异常：地图页面被意外关闭']);
  assert.deepEqual(reasons('没有找到结果：采集程序正常结束，但没有返回可用的企业；这不代表该地区一定没有此类企业'), ['确实没有结果']);
  assert.deepEqual(reasons('采集程序异常：内部查询超时，结果不完整'), ['读取超时或时间预算耗尽']);
  assert.deepEqual(reasons('No validated listings; empty output does not prove that the query has no matches'), ['确实没有结果']);
  for (const text of ['采集程序异常：Maps adapter failed: OSError', '采集程序异常：Process cleanup failed: x',
    '采集程序异常：Final output read failed: disk', 'Final output read failed: disk']) {
    assert.deepEqual(reasons(text), ['采集程序异常'], text);
  }
  const paused = warningReasons('已暂停：采集按用户操作停止，已取得的结果会保留');
  assert.deepEqual(paused.map(item => item.reason), ['已按用户操作暂停']);
  assert.ok(paused.every(item => !item.reason.includes('采集程序异常') && item.reason !== '读取未完成'));
  assert.deepEqual(reasons('采集程序异常：单次查询达到 180 秒时间上限后停止，结果不完整'), ['读取超时或时间预算耗尽']);
});
test('incomplete text-only round offers relocation with confirmation, or explains why not',async()=>{
 const h=await setupDOM();
 try {
  const {RelocateNotice,relocateConfirm}=await import('../src/MarketSummary.tsx');
  assert.equal(relocateConfirm(2),'将重新定位城市范围，成功后按范围补查，预计新增约 34 次地图检索（按访问保护逐个执行，可随时暂停）。继续吗？');
  const calls:string[]=[];
  let confirmed=false;
  window.confirm=(text?:string)=>{calls.push(String(text));return confirmed;};
  const acted:string[]=[];
  const act=async(_work:()=>Promise<unknown>,message:unknown)=>{acted.push(String(message));};
  const coverage={incomplete:true,relocatable:true,query_terms:['metal roofing supply'],geocoder_retry_at:'2026-10-18T10:00:00+00:00'};
  await h.act(async()=>h.root.render(createElement(RelocateNotice,{coverage,runId:'r',busy:false,act} as never)));
  assert.match(document.body.textContent!,/结果不完整：城市范围定位失败，本轮只取得首批结果。/);
  assert.match(document.body.textContent!,/定位服务冷却中，请在 .+ 后重试。/);
  const button=document.querySelector('button')!;
  await h.act(async()=>{button.click();});
  assert.deepEqual(calls,[relocateConfirm(1)]);assert.deepEqual(acted,[]);
  confirmed=true;
  await h.act(async()=>{button.click();});
  assert.equal(acted.length,1);
  await h.act(async()=>h.root.render(createElement(RelocateNotice,{coverage:{...coverage,relocatable:false,relocate_block_reason:'请先点继续，或取消后再补查'},runId:'r',busy:false,act} as never)));
  assert.equal(document.querySelector('button')!.disabled,true);
  assert.match(document.body.textContent!,/请先点继续，或取消后再补查/);
  await h.act(async()=>h.root.render(createElement(RelocateNotice,{coverage:{incomplete:false},runId:'r',act} as never)));
  assert.equal(document.body.textContent,'');
 } finally {await h.cleanup();}
});
