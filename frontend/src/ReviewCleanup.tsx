import {useState} from 'react';
import {api} from './api';
import {actionMessage} from './errors';
import {fieldValue} from './helpers';
import {useResource} from './hooks';
import {Alert} from './ui';
import type {ReviewCleanupPlan} from './types';
export const cleanupConfirm = (total: number) => `将关闭 ${total} 条待审核项，企业资料保持不变。继续吗？`;
export const cleanupDone = (closed: number) => `已关闭 ${closed} 条明显重复的审核，企业资料未修改`;
/** One-time cleanup of obvious duplicate reviews (design §4.7); only shown when there is something to close. */
export function ReviewCleanup({version = 0, onDone}: {version?: number; onDone: () => void}) {
  const [revision, setRevision] = useState(0);
  const plan = useResource<ReviewCleanupPlan>('/review-cleanup', version + revision);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const data = plan.data;
  async function confirm() {
    if (!data || !window.confirm(cleanupConfirm(data.total))) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await api<{closed: number}>('/review-cleanup', 'POST', {plan_id: data.plan_id});
      setNotice(cleanupDone(result.closed));
      onDone();
    } catch (e) {
      setError(actionMessage(e));  // 409 plan_changed shows the server text.
    } finally {
      setBusy(false);
      setRevision(v => v + 1);  // Re-read the plan; after a 409 the user confirms the new one.
    }
  }
  return <>
    {notice && <p role="status" className="notice">{notice}</p>}
    {error && <Alert>{error}</Alert>}
    {!!data?.total && <section className="panel review-cleanup" aria-label="清理明显重复的审核">
      <h2>清理明显重复的审核</h2>
      <p className="muted">以下待审核项只是格式差异或网页噪音。清理只会把它们标为“已自动关闭”，不会修改企业资料，也不会删除记录。</p>
      <ul>{data.rules.map(rule => <li key={rule.rule}>
        <details><summary>{rule.label} · {rule.count} 条</summary>
          <ul>{rule.samples.map(s => <li key={s.review_id}>{s.company_name}：{fieldValue(s.field, s.old_value)} → {fieldValue(s.field, s.new_value)}</li>)}</ul>
        </details></li>)}</ul>
      <button className="primary" disabled={busy} onClick={() => void confirm()}>确认清理 {data.total} 条</button>
    </section>}
  </>;
}
