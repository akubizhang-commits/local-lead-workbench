import type { ReactNode } from "react";
import { Icon } from "./ui";
export function Loading() {
  return (
    <div className="loading" role="status">
      <span />
      正在读取本地数据…
    </div>
  );
}
export function Resource({
  loading,
  error,
  children,
  retry,
}: {
  loading: boolean;
  error: string;
  children: ReactNode;
  retry: () => void;
}) {
  return loading ? (
    <Loading />
  ) : error ? (
    <div className="error-state">
      <Icon name="globe" size={28} />
      <h3>暂时无法读取数据</h3>
      <p>{error}</p>
      <p className="muted">
        请确认本地服务已启动，然后重新连接。此状态不代表没有业务记录。
      </p>
      <button onClick={retry}>重新连接</button>
    </div>
  ) : (
    <>{children}</>
  );
}

export type ResourceState<T> = { data?: T; loading: boolean; error: string };
