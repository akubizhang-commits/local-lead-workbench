import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "./api";
import { formatBytes } from "./ExportJobs";
import { date } from "./helpers";
import type { Overview, RetentionPolicy, RetentionView } from "./types";
import { Alert, Modal } from "./ui";
import { actionMessage } from "./errors";

/** Storage clean-up settings, first-run confirmation and history (P1-8, design §10.6). */
export const RETENTION_CHANGED = "待清理文件已变化，请重新查看后确认";
export const RETENTION_NO_WORKER = "后台未运行时不会自动清理";
export const RETENTION_STRICTER = "新策略会删除更多文件，需要你重新确认后才会自动清理。";
export const RETENTION_PENDING_LABEL = "存储清理待确认";
const CATEGORY: Record<string, string> = { maps_raw: "地图原始结果", auto_backup: "自动备份", export: "导出文件" };
const FIELDS: [keyof RetentionPolicy, string, number, number][] = [
  ["maps_raw_days", "地图原始结果保留天数（7–365）", 7, 365],
  ["auto_backup_keep", "自动备份保留份数（1–100）", 1, 100],
  ["export_days", "导出文件保留天数（1–90）", 1, 90],
];
type Draft = Record<keyof RetentionPolicy, string>;

export function policyErrors(draft: Draft): Partial<Record<keyof RetentionPolicy, string>> {
  const errors: Partial<Record<keyof RetentionPolicy, string>> = {};
  for (const [key, , min, max] of FIELDS) {
    const value = Number(draft[key]);
    if (!/^\d+$/.test(draft[key].trim()) || value < min || value > max) errors[key] = `请输入 ${min}–${max} 之间的整数`;
  }
  return errors;
}

function stricter(policy: RetentionPolicy, confirmed: RetentionPolicy | null) {
  return !!confirmed && (Object.keys(policy) as (keyof RetentionPolicy)[]).some((key) => policy[key] < confirmed[key]);
}

/** Sidebar marker: awaiting confirmation with files to delete. */
export function retentionPending(overview?: Overview | null) {
  const value = overview?.retention;
  return value?.state === "awaiting_confirmation" && (value.pending_files ?? 0) > 0;
}

export function RetentionMarker({ overview }: { overview?: Overview | null }) {
  return retentionPending(overview) ? (
    <em aria-label={RETENTION_PENDING_LABEL} title={RETENTION_PENDING_LABEL}>!</em>
  ) : null;
}

export function RetentionPanel({
  version,
  workerAlive,
  onChanged,
}: {
  version: number;
  workerAlive?: boolean;
  onChanged?: () => void;
}) {
  const [view, setView] = useState<RetentionView | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [working, setWorking] = useState(false);
  const apply = (next: RetentionView) => {
    setView(next);
    setDraft({
      maps_raw_days: String(next.policy.maps_raw_days),
      auto_backup_keep: String(next.policy.auto_backup_keep),
      export_days: String(next.policy.export_days),
    });
  };
  const load = useCallback(async () => {
    try {
      apply(await api<RetentionView>("/retention"));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? actionMessage(e) : String(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load, version]);
  if (!view || !draft) {
    return (
      <section className="panel retention-panel" aria-labelledby="retention-title">
        <h3 id="retention-title">存储清理</h3>
        {error ? <Alert>{error}</Alert> : <p className="muted">正在读取…</p>}
      </section>
    );
  }
  const errors = policyErrors(draft);
  const invalid = Object.keys(errors).length > 0;
  const policy = invalid ? view.policy : {
    maps_raw_days: Number(draft.maps_raw_days),
    auto_backup_keep: Number(draft.auto_backup_keep),
    export_days: Number(draft.export_days),
  };
  const changed = (Object.keys(policy) as (keyof RetentionPolicy)[]).some((k) => policy[k] !== view.policy[k]);
  const preview = view.preview;
  const last = view.history[0];
  const save = async () => {
    setWorking(true);
    setNotice("");
    try {
      const next = await api<RetentionView>("/retention/policy", "PUT", policy);
      apply(next);
      if (view.state === "active" && next.state === "awaiting_confirmation") setNotice(RETENTION_STRICTER);
      setError("");
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? actionMessage(e) : String(e));
    } finally {
      setWorking(false);
    }
  };
  const confirm = async () => {
    setConfirming(false);
    setWorking(true);
    setNotice("");
    try {
      const result = await api<RetentionView & { deleted: number; freed_bytes: number; errors: { path: string }[] }>(
        "/retention/confirm", "POST", { plan_id: preview.plan_id });
      apply(result);
      if (result.errors.length) setNotice(`${result.errors.length} 个文件未能删除，详情已写入日志`);
      setError("");
      onChanged?.();
    } catch (e) {
      if (e instanceof ApiError && e.code === "retention_plan_changed") {
        setNotice(RETENTION_CHANGED);
        await load();
      } else {
        setError(e instanceof Error ? actionMessage(e) : String(e));
      }
    } finally {
      setWorking(false);
    }
  };
  const total = `${preview.total_files} 个文件（共 ${formatBytes(preview.total_bytes)}）`;
  return (
    <section className="panel retention-panel" aria-labelledby="retention-title">
      <h3 id="retention-title">存储清理</h3>
      <p className="muted">
        地图原始结果保留 {view.policy.maps_raw_days} 天；自动备份保留最近 {view.policy.auto_backup_keep} 份；导出文件保留{" "}
        {view.policy.export_days} 天。手动备份不会被自动删除；企业、联系人等数据库记录不受影响。
      </p>
      <form
        className="retention-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!invalid && changed) void save();
        }}
      >
        {FIELDS.map(([key, label, min, max]) => (
          <label className="field" key={key}>
            <span>{label}</span>
            <input
              type="number"
              min={min}
              max={max}
              step={1}
              value={draft[key]}
              aria-invalid={!!errors[key]}
              aria-describedby={errors[key] ? `retention-${key}-error` : undefined}
              onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
            />
            {errors[key] && (
              <small className="field-error" id={`retention-${key}-error`}>{errors[key]}</small>
            )}
          </label>
        ))}
        <button type="submit" disabled={working || invalid || !changed}>保存策略</button>
      </form>
      {view.state === "active" && !invalid && stricter(policy, view.confirmed_policy) && (
        <p className="hint">{RETENTION_STRICTER}</p>
      )}
      <p className="retention-usage">
        当前占用：地图原始结果 {formatBytes(view.usage.runs_bytes)} · 备份 {formatBytes(view.usage.backups_bytes)} ·
        导出 {formatBytes(view.usage.exports_bytes)} · 日志 {formatBytes(view.usage.logs_bytes)}
      </p>
      {notice && <p className="retention-notice" role="status">{notice}</p>}
      {error && <Alert>{error}</Alert>}
      {workerAlive === false && <p className="hint">{RETENTION_NO_WORKER}</p>}
      {view.state === "active" ? (
        <div className="retention-active">
          <p>
            <strong>已启用自动清理</strong>
            {last && <> · 上次清理：{date(last.at)}，删除 {last.deleted} 个文件，释放 {formatBytes(last.freed_bytes)}</>}
          </p>
          {view.history.length > 0 && (
            <ul className="retention-history">
              {view.history.map((h) => (
                <li key={h.at + h.trigger}>
                  {date(h.at)} · {h.trigger === "confirm" ? "确认清理" : "自动清理"} · 删除 {h.deleted} 个文件 · 释放{" "}
                  {formatBytes(h.freed_bytes)}
                  {h.errors > 0 && <> · {h.errors} 个文件未能删除</>}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : preview.total_files === 0 ? (
        <div className="retention-empty">
          <p>目前没有需要清理的文件。</p>
          <button className="primary" disabled={working} onClick={() => void confirm()}>启用自动清理</button>
        </div>
      ) : (
        <div className="retention-pending">
          <p>首次清理前需要你确认。确认后将删除下列文件，之后按策略自动清理并记录。</p>
          <p>
            将删除 {preview.total_files} 个文件，共 {formatBytes(preview.total_bytes)}
          </p>
          {preview.truncated && <p className="hint">仅显示前 500 项，确认时按完整清单执行</p>}
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th scope="col">类别</th><th scope="col">文件</th><th scope="col">大小</th><th scope="col">原因</th></tr>
              </thead>
              <tbody>
                {preview.items.map((item) => (
                  <tr key={item.path}>
                    <td>{CATEGORY[item.category] || item.category}</td>
                    <td><code>{item.path}</code></td>
                    <td>{formatBytes(item.size_bytes)}</td>
                    <td>{item.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button className="primary" disabled={working} onClick={() => setConfirming(true)}>确认清理</button>
        </div>
      )}
      {confirming && (
        <Modal title="确认清理" onClose={() => setConfirming(false)}>
          <p>确认删除 {total}？删除后无法恢复。手动备份和企业、联系人等数据库记录不受影响。</p>
          <div className="actions">
            <button onClick={() => setConfirming(false)}>取消</button>
            <button className="primary danger" onClick={() => void confirm()}>确认删除</button>
          </div>
        </Modal>
      )}
    </section>
  );
}
