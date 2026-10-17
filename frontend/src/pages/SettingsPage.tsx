import { api } from "../api";
import { date } from "../helpers";
import { useResource } from "../hooks";
import { Schedules } from "../Schedules";
import { SocialSettings } from "../SocialSettings";
import type { Backup, Overview, Settings } from "../types";
import { Alert, Empty, Icon } from "../ui";
import { WorkerHealth } from "../WorkerHealth";

import { Loading, Resource, type ResourceState } from "../Resource";
import type { Action } from "./pageTypes";
export function SettingsPage({
  version,
  overview,
  busy,
  act,
  refresh,
  setRestore,
  setConfirmRestore,
}: {
  version: number;
  overview: ResourceState<Overview>;
  busy: boolean;
  act: Action;
  refresh: () => void;
  setRestore: (backup: Backup) => void;
  setConfirmRestore: (confirmed: boolean) => void;
}) {
  const settings = useResource<Settings>("/settings", version);
  const backups = useResource<Backup[]>("/backups", version);
  return (
    <>
      <WorkerHealth
        worker={overview.error ? undefined : overview.data?.worker}
        onRefresh={refresh}
      />
      <Resource {...settings} retry={refresh}>
        {settings.data && (
          <section className="panel">
            <div className="section-title">
              <h3>
                <Icon name="shield" />
                本地运行环境
              </h3>
              <span className="badge">v{settings.data.version}</span>
            </div>
            <dl className="facts">
              <dt>数据目录</dt>
              <dd>
                <code>{settings.data.data_dir}</code>
              </dd>
              <dt>联网方式</dt>
              <dd>
                {settings.data.network_mode === "direct-public"
                  ? "直接访问公开来源"
                  : settings.data.network_mode}
              </dd>
              <dt>付费服务</dt>
              <dd>{settings.data.paid_services ? "已启用" : "未启用"}</dd>
              <dt>在线 AI</dt>
              <dd>{settings.data.ai_enabled ? "已启用" : "未启用"}</dd>
            </dl>
          </section>
        )}
      </Resource>
      <section className="panel">
        <h3>来源状态</h3>
        {overview.error ? (
          <Alert>{overview.error}</Alert>
        ) : overview.loading ? (
          <Loading />
        ) : (
          (["maps", "website"] as const).map((key) => (
            <div className="source-row" key={key}>
              <Icon name="globe" />
              <div>
                <strong>
                  {key === "maps"
                    ? "Google Maps · 企业发现"
                    : "官方网站 · 资料与联系入口"}
                </strong>
                <p>
                  {overview.data?.source_status[key]?.message ||
                    "尚未提供状态说明"}
                </p>
              </div>
              <span
                className={`badge ${overview.data?.source_status[key]?.available ? "badge-verified" : "badge-blocked"}`}
              >
                {overview.data?.source_status[key]?.available
                  ? key === "maps"
                    ? "已安装"
                    : "可用"
                  : "未就绪"}
              </span>
            </div>
          ))
        )}
      </section>
      <SocialSettings />
      <Schedules version={version} />
      <section className="panel">
        <div className="section-title">
          <h3>备份与恢复</h3>
          <button
            className="primary"
            disabled={busy}
            onClick={() =>
              act(() => api("/backups", "POST"), "一致性备份已创建")
            }
          >
            <Icon name="plus" size={16} />
            创建备份
          </button>
        </div>
        <p className="muted">
          恢复会替换当前业务数据，恢复前会自动备份当前数据。有排队中或运行中的任务时不能恢复（已暂停的任务不阻止恢复，会被备份内容覆盖）；后台 worker 空闲时可直接恢复，无需停止程序。恢复前必须再次确认。
        </p>
        <Resource {...backups} retry={refresh}>
          {backups.data?.length ? (
            <div>
              {backups.data.map((b) => (
                <div className="backup-row" key={b.name}>
                  <Icon name="detail" />
                  <div>
                    <strong>{b.name}</strong>
                    <small>{date(b.created_at)}</small>
                  </div>
                  <button
                    disabled={busy}
                    onClick={() => {
                      setRestore(b);
                      setConfirmRestore(false);
                    }}
                  >
                    恢复此备份
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <Empty title="还没有备份">
              创建一致性备份，保留当前资料、来源和备注。
            </Empty>
          )}
        </Resource>
      </section>
    </>
  );
}
