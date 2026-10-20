import {useState} from 'react';
import {api} from './api';
import {actionMessage} from './errors';
import {date} from './helpers';
import {useResource} from './hooks';
import {Alert} from './ui';
import type {Run} from './types';

export interface RecheckBatch {
  run_id: string; status: string; total: number; done: number; created_at: string; finished_at: string | null;
  skipped: {company_id: string; name: string; reason: string}[]; pages_checked: number; entries_added: number;
}
export interface RecheckCreated {run_id: string; queued: number; skipped: {company_id: string; name: string; reason: string}[]}

export const RECHECK_LIMIT = 20;
export const recheckConfirm = (n: number | string) =>
  `将依次读取 ${n} 家企业的公开官网（每家约 1–4 分钟，按访问节奏逐个执行），可在市场页暂停或取消。继续吗？`;
export const recheckQueued = (result: RecheckCreated) =>
  `已加入官网复核队列：${result.queued} 家${result.skipped.length ? `；跳过 ${result.skipped.length} 家（原因见市场页）` : ''}`;

/** Title of a run as RunCard shows it: market name, batch re-check, or the company name. */
export function runTitle(run: Pick<Run, 'kind' | 'company_name'> & {snapshot?: Record<string, unknown> | null}, companyName = '') {
  const snapshot = run.snapshot || {};
  if (snapshot.batch === 'website_recheck') return `批量官网复核 · ${(snapshot.company_ids as unknown[] | undefined)?.length ?? 0} 家`;
  if (run.kind === 'discovery') return String(snapshot.name || '市场检索');
  return run.company_name || companyName || '企业单独复核';
}

/** Market page panel for batch website re-checks; polls every 5 s (design §6.4). */
export function MarketRecheck({marketId, archived = false, version = 0}: {marketId: string; archived?: boolean; version?: number}) {
  const [revision, setRevision] = useState(0);
  const batch = useResource<RecheckBatch | null>(`/tasks/${encodeURIComponent(marketId)}/website-recheck`, version + revision, true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const b = batch.data;
  async function run(work: () => Promise<string>) {
    setBusy(true); setError(''); setNotice('');
    try {setNotice(await work());} catch (e) {setError(actionMessage(e));} finally {setBusy(false); setRevision(v => v + 1);}
  }
  const control = (action: 'pause' | 'resume' | 'cancel', message: string) =>
    run(async () => {await api(`/runs/${encodeURIComponent(b!.run_id)}/${action}`, 'POST'); return message;});
  const active = !!b && ['queued', 'running'].includes(b.status);
  const paused = b?.status === 'paused';
  return <section className="panel market-recheck" aria-label="批量官网复核">
    <h3>批量官网复核</h3>
    {active ? <>
      <p role="status">官网复核进行中：已完成 {b!.done} / {b!.total} 家</p>
      <div className="actions"><button disabled={busy} onClick={() => void control('pause', '已暂停批量官网复核')}>暂停</button>
        <button disabled={busy} onClick={() => void control('cancel', '已取消批量官网复核')}>取消</button></div>
    </> : paused ? <>
      <p role="status">已暂停：已完成 {b!.done} / {b!.total} 家</p>
      <div className="actions"><button disabled={busy || archived} onClick={() => void control('resume', '已继续批量官网复核')}>继续</button>
        <button disabled={busy} onClick={() => void control('cancel', '已取消批量官网复核')}>取消</button></div>
    </> : <>
      {b && <p>上次批量复核：完成 {b.done} / {b.total} 家 · 结束于 {date(b.finished_at)} · 新增联系入口 {b.entries_added} 个（去重后）{b.skipped.length ? ` · 跳过 ${b.skipped.length} 家` : ''}</p>}
      {!!b?.skipped.length && <details><summary>查看跳过的企业</summary><ul>{b.skipped.map(s => <li key={s.company_id}>{s.name || s.company_id} · {s.reason}</li>)}</ul></details>}
      <p className="muted">对市场内尚未读取过官网的企业批量复核，一次最多 {RECHECK_LIMIT} 家。读取失败或只读到部分页面的企业不会被重复选中，可在企业库勾选后单独复核。</p>
      <button disabled={busy || archived} onClick={() => {
        if (!window.confirm(recheckConfirm(`最多 ${RECHECK_LIMIT}`))) return;
        void run(async () => recheckQueued(await api<RecheckCreated>(`/tasks/${encodeURIComponent(marketId)}/website-recheck`, 'POST', {select: 'never_attempted'})));
      }}>复核尚未读取过官网的企业</button>
    </>}
    {notice && <p role="status">{notice}</p>}
    {(error || batch.error) && <Alert>{error || batch.error}</Alert>}
  </section>;
}
