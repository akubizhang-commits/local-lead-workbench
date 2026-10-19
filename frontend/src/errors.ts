/** Error kinds for the UI: missing record, service error, storage, busy, offline (P1-6, design §5.7). */
export type ErrorKind = 'offline' | 'not_found' | 'storage' | 'busy' | 'server' | 'client';
export interface ErrorInfo {kind: ErrorKind; code?: string; requestId?: string; status: number}

const STORAGE = new Set(['readonly_storage', 'disk_full', 'storage_unavailable', 'db_corrupt']);
const BUSY = new Set(['db_busy', 'maintenance']);

type Coded = {status?: unknown; code?: unknown; requestId?: unknown};

export function errorKind(e: unknown): ErrorKind {
  const {status, code} = (e ?? {}) as Coded;
  if (status === 0 || code === 'offline') return 'offline';
  if (code === 'not_found') return 'not_found';
  if (typeof code === 'string' && STORAGE.has(code)) return 'storage';
  if (typeof code === 'string' && BUSY.has(code)) return 'busy';
  if (typeof status === 'number' && status >= 500) return 'server';
  return 'client';
}

export function errorInfo(e: unknown): ErrorInfo | undefined {
  if (!e || typeof e !== 'object') return undefined;
  const {status, code, requestId} = e as Coded;
  return {
    kind: errorKind(e),
    code: typeof code === 'string' ? code : undefined,
    requestId: typeof requestId === 'string' ? requestId : undefined,
    status: typeof status === 'number' ? status : 0,
  };
}

/** Suffix appended to action errors for 5xx responses: "（错误编号 req_…）". */
export function requestIdSuffix(e: unknown): string {
  const {status, requestId} = (e ?? {}) as Coded;
  return typeof status === 'number' && status >= 500 && typeof requestId === 'string' && requestId
    ? `（错误编号 ${requestId}）` : '';
}

export function errorTitle(info: ErrorInfo): string {
  if (info.kind === 'not_found') return '记录已更新或移除';
  if (info.kind === 'server') return '服务出错';
  if (info.kind === 'busy') return info.code === 'maintenance' ? '正在恢复备份' : '本地数据库正忙';
  if (info.kind === 'storage') {
    if (info.code === 'readonly_storage') return '数据目录不可写';
    if (info.code === 'disk_full') return '磁盘空间不足';
    if (info.code === 'db_corrupt') return '本地数据库文件损坏';
    return '本地数据库不可用';
  }
  return '暂时无法读取数据';
}
