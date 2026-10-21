import type { ReactNode } from "react";
import { Icon } from "./ui";
import { useSession } from "./session";
import { CLIENT_PREFIX, errorTitle, isPlainClientError, type ErrorInfo } from "./errors";
export function Loading() {
  const server = useSession().mode === "server";
  return (
    <div className="loading" role="status">
      <span />
      {server ? "正在读取数据…" : "正在读取本地数据…"}
    </div>
  );
}
export type NotFoundAction = { label: string; onClick: () => void };
function RequestId({ id }: { id?: string }) {
  return id ? (
    <p className="error-request-id">
      错误编号：<code>{id}</code>
    </p>
  ) : null;
}
export function Resource({
  loading,
  error,
  children,
  retry,
  errorInfo,
  notFoundAction,
}: {
  loading: boolean;
  error: string;
  children: ReactNode;
  retry: () => void;
  errorInfo?: ErrorInfo;
  notFoundAction?: NotFoundAction;
}) {
  const server = useSession().mode === "server";
  if (loading) return <Loading />;
  if (!error) return <>{children}</>;
  const kind = errorInfo?.kind;
  if (errorInfo && kind === "not_found") {
    const action = notFoundAction || { label: "刷新", onClick: retry };
    return (
      <div className="error-state error-not-found" role="alert">
        <Icon name="search" size={28} />
        <h3>{errorTitle(errorInfo)}</h3>
        <p>{CLIENT_PREFIX}{error}</p>
        <p>这条记录可能已被合并、被恢复的备份替换，或链接已失效。</p>
        <button onClick={action.onClick}>{action.label}</button>
      </div>
    );
  }
  if (errorInfo && (kind === "server" || kind === "storage" || kind === "busy")) {
    return (
      <div className={`error-state error-${kind}`} role="alert">
        <Icon name="shield" size={28} />
        <h3>{errorTitle(errorInfo)}</h3>
        <p>{kind === "busy" ? "请稍后重试。" : error}</p>
        <RequestId id={errorInfo.requestId} />
        <button onClick={retry}>重试</button>
      </div>
    );
  }
  if (errorInfo && kind === "client") {
    const plain = isPlainClientError(errorInfo);
    return (
      <div className="error-state error-client" role="alert">
        <Icon name="shield" size={28} />
        <h3>{plain ? "请求未完成" : "暂时无法读取数据"}</h3>
        <p>{plain ? `${CLIENT_PREFIX}${error}` : error}</p>
        <RequestId id={errorInfo.requestId} />
        <button onClick={retry}>重试</button>
      </div>
    );
  }
  return (
    <div className="error-state">
      <Icon name="globe" size={28} />
      <h3>暂时无法读取数据</h3>
      <p>{error}</p>
      <p className="muted">
        {server ? "请检查网络后重新连接。" : "请确认本地服务已启动，然后重新连接。"}此状态不代表没有业务记录。
      </p>
      <button onClick={retry}>重新连接</button>
    </div>
  );
}
export type ResourceState<T> = { data?: T; loading: boolean; error: string; errorInfo?: ErrorInfo };
