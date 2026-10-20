import {useId, useState, type FormEvent} from 'react';
import {api, ApiError} from './api';
import {actionMessage} from './errors';
import {ExclusionRules} from './ExclusionRules';
import {KEYWORDS_REQUIRED, KeywordFields, splitKeywords} from './forms';
import {QueryPreview} from './QueryPreview';
import {Alert} from './ui';
import type {Action} from './pages/pageTypes';
import type {Task} from './types';

const ACTIVE = ['queued', 'running', 'paused'];

/** "编辑目标市场": keywords and exclusion rules only; the region is read-only (design §6.4). */
export function MarketEditDialog({task, version, busy, act, onSaved, onCancel, onOpenMarket}: {
  task: Task; version: number; busy: boolean; act: Action;
  onSaved: (task: Task) => void; onCancel: () => void; onOpenMarket: (id: string) => void;
}) {
  const [keywords, setKeywords] = useState(task.keywords.join(', '));
  const [industry, setIndustry] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [existing, setExisting] = useState<string | null>(null);
  const formId = useId();
  const list = splitKeywords(keywords);
  const body = list.length || industry ? {keywords: list, ...(industry ? {industry} : {})} : null;
  const unfinished = !!task.latest_run && ACTIVE.includes(task.latest_run.status);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!body) {setError(KEYWORDS_REQUIRED); return;}
    setSaving(true); setError(''); setExisting(null);
    try {
      onSaved(await api<Task>(`/tasks/${encodeURIComponent(task.id)}`, 'PATCH', body));
    } catch (e) {
      setError(actionMessage(e));
      if (e instanceof ApiError && e.code === 'market_exists' && typeof e.body?.existing_id === 'string') setExisting(e.body.existing_id);
    } finally {
      setSaving(false);
    }
  }
  // ExclusionRules has its own forms, so it sits beside (not inside) the keyword form.
  return <>
    <div className="form-body">
      <form id={formId} onSubmit={submit}>
        {error && <Alert>{error}{existing && <> <button type="button" onClick={() => onOpenMarket(existing)}>打开该市场</button></>}</Alert>}
        <p>地区：{[task.country, task.state, task.county, task.city].filter(Boolean).join(' / ')}</p>
        <p className="hint">国家、州 / 省和城市创建后不能修改。要换地区，请新建市场。</p>
        <div className="form-grid">
          <KeywordFields industry={industry} onIndustry={setIndustry} companyTypes={task.company_types}
            keywords={keywords} onKeywords={setKeywords}
            invalidProps={error === KEYWORDS_REQUIRED ? {'aria-invalid': true} : {}}/>
          <QueryPreview body={body} mode="edit" marketId={task.id} onOpenMarket={onOpenMarket}/>
        </div>
        <p className="hint">修改后从下一轮开始按新关键词检索；已找到的企业、联系人和历史轮次都会保留。{unfinished && '当前未结束的一轮仍按原关键词执行。'}</p>
      </form>
      <section aria-label="排除规则"><h4>排除规则</h4>
        <ExclusionRules marketId={task.id} version={version} busy={busy} act={act}/></section>
    </div>
    <footer><button type="button" onClick={onCancel}>取消</button>
      <button className="primary" type="submit" form={formId} disabled={saving}>{saving ? '保存中…' : '保存修改'}</button></footer>
  </>;
}
