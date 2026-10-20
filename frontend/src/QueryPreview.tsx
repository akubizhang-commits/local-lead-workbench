import {useEffect, useState} from 'react';
import {api} from './api';
import {actionMessage} from './errors';
import type {IndustryPreset, QueryEstimate, QueryPreviewResult} from './types';

const TYPE_ORDER = ['wholesale', 'retail', 'import'] as const;

/** Terms of a preset for the selected company types; wholesale when none (same rule as the server). */
export function presetTerms(preset: IndustryPreset | undefined, companyTypes: string[]): string[] {
  if (!preset) return [];
  const types = TYPE_ORDER.filter(type => companyTypes.includes(type));
  return (types.length ? types : ['wholesale' as const]).map(type => preset.terms[type]);
}

/** "{n} 次" text of an estimate's first round; a range for the geocoder case. */
function initialText(estimate: QueryEstimate) {
  return estimate.initial_min === estimate.initial_max
    ? `${estimate.initial_min}` : `${estimate.initial_min}–${estimate.initial_max}`;
}

/** Markets without a catalogue place: the worker locates the city first, the built-in range is its fallback. */
export const FALLBACK_NOTE = '（上限按内置城市范围估算，实际以定位结果为准）';
const fallbackNote = (estimate: QueryEstimate) => estimate.bounds_source === 'catalogue_fallback' ? FALLBACK_NOTE : '';

/** Confirmation before a round starts (D9, design §8.4). */
export function startConfirmText(estimate: QueryEstimate | undefined) {
  if (!estimate) return '本轮将执行地图检索，按低速模式逐个执行，可随时暂停。开始吗？';
  const most = estimate.max != null && estimate.max !== estimate.initial_max ? `（结果密集时最多约 ${estimate.max} 次）${fallbackNote(estimate)}` : '';
  return `本轮预计执行 ${initialText(estimate)} 次地图检索${most}，按低速模式逐个执行，可随时暂停。开始吗？`;
}

export function estimateLines(estimate: QueryEstimate): string {
  if (estimate.geometry === 'catalogue') {
    return `预计地图检索：首轮 ${estimate.initial_min} 次（每个检索词 1 次城市检索 + 16 个分区）。结果密集的分区会自动细分，最多约 ${estimate.max} 次${fallbackNote(estimate)}；按低速模式逐个执行，可随时暂停。`;
  }
  if (estimate.geometry === 'geocoder') {
    return `预计地图检索：首轮 ${estimate.initial_min}–${estimate.initial_max} 次（城市范围定位成功时 ${estimate.initial_max} 次，失败时只做 ${estimate.initial_min} 次文字检索）。`;
  }
  return `预计地图检索：${estimate.initial_min} 次文字检索。`;
}

export function existingText(preview: QueryPreviewResult, mode: 'create' | 'edit') {
  const market = preview.existing_market;
  if (!market) return '';
  if (mode === 'edit') return `已有相同地区和关键词的目标市场「${market.name}」，保存时会被拒绝。请修改关键词，或直接打开该市场。`;
  return market.archived
    ? `已有相同的目标市场「${market.name}」，目前已归档。可在“已归档市场”中恢复后继续使用。`
    : `已有相同的目标市场「${market.name}」，提交后会直接打开该市场，不会重复创建。`;
}

/**
 * Debounced POST /tasks/preview (400 ms). `body` null means nothing to preview yet.
 * onReady(key) reports the request key the preview succeeded for, or null.
 */
export function QueryPreview({body, mode = 'create', marketId, onReady, onOpenMarket}: {
  body: Record<string, unknown> | null;
  mode?: 'create' | 'edit';
  marketId?: string;
  onReady?: (key: string | null) => void;
  onOpenMarket?: (id: string) => void;
}) {
  const request = body && (mode === 'edit' ? {...body, market_id: marketId} : body);
  const key = request ? JSON.stringify(request) : '';
  const [state, setState] = useState<{key: string; result?: QueryPreviewResult; error?: string}>({key: ''});
  useEffect(() => {
    onReady?.(null);
    if (!key) {setState({key: ''}); return;}
    let active = true;
    const timer = setTimeout(() => {
      api<QueryPreviewResult>('/tasks/preview', 'POST', JSON.parse(key)).then(result => {
        if (!active) return;
        setState({key, result});
        onReady?.(key);
      }).catch(error => {
        if (active) setState({key, error: actionMessage(error)});
      });
    }, 400);
    return () => {active = false; clearTimeout(timer);};
  }, [key]);
  const current = state.key === key ? state : {key};
  const existing = current.result ? existingText(current.result, mode) : '';
  return <section className="query-preview wide" aria-label="检索预览" aria-live="polite">
    <h4>检索预览</h4>
    {!key ? <p className="muted">填写关键词或选择预设行业后显示检索预览</p>
      : current.error ? <p className="alert" role="alert">暂时无法生成预览：{current.error}</p>
      : !current.result ? <p className="muted" role="status">正在生成检索预览…</p>
      : <>
        <p>将执行的检索词：</p>
        <ul className="query-terms">{current.result.query_terms.map(term => <li key={term}>{term}</li>)}</ul>
        <p>{estimateLines(current.result)}</p>
        {existing && <p className="hint">{existing}{current.result.existing_market && onOpenMarket &&
          <> <button type="button" className="text-button" onClick={() => onOpenMarket(current.result!.existing_market!.id)}>打开该市场</button></>}</p>}
      </>}
  </section>;
}
