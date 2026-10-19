import {TIERS, date, label} from './helpers';
import type {Filters} from './helpers';
import type {Task} from './types';

const TIER_TITLE = Object.fromEntries(TIERS);
const ACTIVE = ['queued', 'running', 'paused'];

/** "本轮新增摘要" card under the market summary (design §7.4). */
export function RoundSummary({task, openMarket}: {task: Task; openMarket?: (id: string, filters?: Partial<Filters>) => void}) {
  const s = task.round_summary;
  if (!s) return null;
  const previous = !!task.latest_run && ACTIVE.includes(task.latest_run.status) && task.latest_run.id !== s.run_id;
  const delta = Object.entries(s.tier_delta || {}).filter(([, n]) => n)
    .map(([code, n]) => `${TIER_TITLE[code] || code} ${n > 0 ? '+' : '−'}${Math.abs(n)}`);
  return <section className="panel round-summary" aria-label="本轮新增摘要">
    <h3>本轮新增摘要</h3>
    <p className="muted">第 {s.round} 轮 · {label(s.status)} · 结束于 {date(s.finished_at)}{previous ? '（当前一轮尚在进行，以下为上一轮）' : ''}</p>
    <p>新增 {s.new} 家 · 存量 {s.existing} 家 · 资料变化 {s.changed} 家 · 未再次找到 {s.not_seen} 家 · 新增联系入口 {s.entries_added} 个（去重后）</p>
    <p>{s.tiers_available
      ? `入口等级：提升 ${s.tier_improved ?? 0} 家 · 下降 ${s.tier_worsened ?? 0} 家${delta.length ? `；${delta.join(' · ')}` : ''}`
      : '入口等级变化：这一轮缺少开始时的等级记录（例如开始于升级之前），无法比较。'}</p>
    {['cancelled', 'partial'].includes(s.status) && <p className="muted">本轮未全部完成，数字只反映已执行的查询。</p>}
    {openMarket && <button onClick={() => openMarket(task.id, {run_id: s.run_id, segment: 'new'})}>查看本轮新增企业</button>}
  </section>;
}
