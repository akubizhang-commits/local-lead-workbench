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
