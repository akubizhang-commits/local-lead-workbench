import {useState} from 'react';
import {api, ApiError} from './api';
import {actionMessage} from './errors';
import {date} from './helpers';
import {useResource} from './hooks';
import {Alert} from './ui';
import type {Schedule, Task} from './types';
import {SCHEDULE_NOTE} from './Schedules';
import {useSession} from './session';

const PERIODS = [1, 2, 3, 6, 12];
const browserZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai';

/** The market page's always-visible "每月自动更新" switch (design §7.3). */
export function MarketSchedule({task, version = 0, archived = false}: {task: Task; version?: number; archived?: boolean}) {
  const scheduleNote = SCHEDULE_NOTE[useSession().mode];
  const [revision, setRevision] = useState(0);
  const result = useResource<Schedule[]>(`/schedules?task_id=${encodeURIComponent(task.id)}`, version + revision);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const items = result.data || [];
  // The server orders the list: enabled first, then the earliest plan.
  const current = items.find(s => s.enabled);
  const reusable = items.find(s => !s.enabled && (s.kind === 'discovery' || !s.kind));
  const months = current?.interval_months || 1;
  const on = !!current;
  async function run(work: () => Promise<Schedule>, message: (s: Schedule) => string) {
    setBusy(true); setError(''); setNotice('');
    try {
      setNotice(message(await work()));
    } catch (e) {
      if (e instanceof ApiError && e.code === 'schedule_exists') setNotice('该市场已有启用中的计划，已刷新为该计划的设置。');
      else setError(actionMessage(e));
    } finally {
      setBusy(false); setRevision(v => v + 1);
    }
  }
  const patch = (id: string, body: Record<string, unknown>) =>
    api<Schedule>(`/schedules/${encodeURIComponent(id)}`, 'PATCH', body);
  function toggle() {
    if (on) return run(() => patch(current!.id, {enabled: false}), () => '已关闭自动更新');
    return run(() => reusable ? patch(reusable.id, {enabled: true})
      : api<Schedule>('/schedules', 'POST', {task_id: task.id, kind: 'discovery', interval_months: 1, timezone: browserZone()}),
    s => `已开启自动更新，下次：${date(s.next_due_at)}`);
  }
  const initial = task.query_estimate?.initial_min;
  const title = months === 1 ? '每月自动更新' : `每 ${months} 个月自动更新`;
  return <section className="panel market-schedule" aria-label="自动更新">
    <div className="row between">
      <label className="check-line">
        <input type="checkbox" role="switch" aria-checked={on} checked={on}
          disabled={busy || archived || result.loading || !!result.error} onChange={() => void toggle()}/>
        {title}
      </label>
      <select aria-label="更新周期" value={months} disabled={!on || busy || archived}
        onChange={event => {
          const n = Number(event.target.value);
          void run(() => patch(current!.id, {interval_months: n}), s => `更新周期已改为每 ${n} 个月，下次：${date(s.next_due_at)}`);
        }}>
        {PERIODS.map(n => <option key={n} value={n}>每 {n} 个月</option>)}
      </select>
    </div>
    {archived && <p className="muted">已归档，恢复后可开启</p>}
    <p>{on ? `下次更新：${date(current!.next_due_at)}（${current!.timezone}）` : '未开启自动更新'}</p>
    <p className="muted">{scheduleNote}每次更新按当前关键词执行地图检索{initial ? `（预计首轮 ${initial} 次）` : ''}。</p>
    {notice && <p role="status">{notice}</p>}
    {(error || result.error) && <Alert>{error || result.error}</Alert>}
  </section>;
}
