/** Turn API error bodies (including 422 detail arrays) into Chinese, per-field text. */
export const FIELD_LABELS: Record<string, string> = {
  name: '企业名称', website: '官方网站', city: '城市', country: '国家代码', state: '州/省',
  keywords: '关键词', company_types: '目标企业类型', social_platforms: '社媒平台', phone: '联系电话',
  address: '地址', notes: '跟进备注', status: '客户匹配状态', business_types: '业务类型',
  interval_months: '更新周期', timezone: '时区', fields: '资料字段',
};

export interface FieldError {field: string; label: string; message: string}
export interface ParsedError {message: string; fields: FieldError[]}

const LOCATIONS = new Set(['body', 'query', 'path']);
const CHINESE = /[\u4e00-\u9fff]/;

function itemMessage(raw: unknown): string {
  const text = String(raw ?? '').replace(/^Value error,\s*/, '').trim();
  return CHINESE.test(text) ? text : `输入不符合要求（${text.slice(0, 120) || '未知原因'}）`;
}

export function parseErrorDetail(status: number, body: unknown): ParsedError {
  const detail = (body as {detail?: unknown} | null)?.detail;
  if (typeof detail === 'string') return {message: detail, fields: []};
  if (Array.isArray(detail) && detail.length) {
    const fields: FieldError[] = [];
    const parts: string[] = [];
    for (const item of detail) {
      const loc = Array.isArray((item as {loc?: unknown})?.loc) ? (item as {loc: unknown[]}).loc : [];
      const field = [...loc].reverse().find((part): part is string => typeof part === 'string' && !LOCATIONS.has(part));
      const message = itemMessage((item as {msg?: unknown})?.msg);
      if (field) {
        const label = FIELD_LABELS[field] || field;
        fields.push({field, label, message});
        parts.push(`「${label}」：${message}`);
      } else {
        parts.push(message);
      }
    }
    return {message: [...new Set(parts)].join('；'), fields};
  }
  return {message: `请求失败（${status}）`, fields: []};
}
