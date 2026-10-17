import { api } from "../api";
import { date, label, runActions } from "../helpers";
import { RunWarnings } from "../RunWarnings";
import type { Overview, Run } from "../types";
import { Badge } from "../ui";
import { queueReason } from "../workerStatus";

import type { Action } from "./pageTypes";
export function RunCard({
  run,
  worker,
  busy,
  act,
  openCompany,
}: {
  run: Run;
  worker: Overview["worker"];
  busy: boolean;
  act: Action;
  openCompany?: (id: string) => void;
}) {
  const progress = run.progress;
  const total =
      progress?.enrichment_total ??
      (run.kind === "refresh" ? progress?.total : undefined),
    done =
      progress?.enrichment_done ??
      (run.kind === "refresh" ? progress?.done : undefined);
  const discoveryTotal = progress?.discovery_total,
    discoveryDone = progress?.discovery_done;
  return (
    <div className="run-card" key={run.id}>
      <div className="row">
        <strong>
          {label(run.kind)}
          {run.kind === "refresh" && <> · {run.company_id && run.company_name ? (
            <a href={`/?view=detail&page=1&company=${encodeURIComponent(run.company_id)}`}
              onClick={(event) => {
                if (openCompany && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
                  event.preventDefault();
                  openCompany(run.company_id!);
                }
              }}>{run.company_name}</a>
          ) : <span className="muted">关联企业信息缺失</span>}</>}
        </strong>
        <Badge value={run.status} />
        <small className="muted">{date(run.created_at)}</small>
      </div>
      <p>{run.message || "等待运行状态更新"}</p>
      {run.coverage?.capability_exhausted &&
        run.coverage.retryable === false && (
          <p className="queue-reason coverage-exhausted">
            当前策略已达到覆盖能力边界，重复继续无法补齐；可定期更新已知市场资料
          </p>
        )}
      {queueReason(run.status, worker) && (
        <p className="queue-reason">{queueReason(run.status, worker)}</p>
      )}
      {run.kind!=="social"&&progress?.website_pages_checked !== undefined && (
        <p className="muted">
          累计读取 {progress.website_pages_checked} 页 · 待扫描 {progress.website_links_remaining ?? 0} 个链接
          {(run.status === "running" || run.status === "queued") && (progress.website_links_remaining ?? 0) > 0
            ? "；后台分批继续，无需手动操作" : ""}
        </p>
      )}
      <div className="run-progress-grid">
        {(
          [
            ["已处理查询", discoveryDone, discoveryTotal],
            ["企业官网处理进度", done, total],
            ["社媒企业处理进度", progress?.social_done, progress?.social_total],
          ] as const
        )
          .filter(
            ([title]) => run.kind==="social"?title==="社媒企业处理进度":
              title==="社媒企业处理进度"?progress?.social_total!==undefined:
              run.kind !== "refresh" || title === "企业官网处理进度",
          )
          .map(([title, finished, planned]) => (
            <div key={title}>
              <small className="muted">
                {title} · {finished ?? "—"} / {planned ?? "待确定"}
              </small>
              <div
                className="progress"
                role="progressbar"
                aria-label={title}
                aria-valuenow={
                  planned
                    ? Math.min(
                        100,
                        Math.round(((finished ?? 0) / planned) * 100),
                      )
                    : undefined
                }
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <span
                  style={{
                    width: planned
                      ? `${Math.min(100, ((finished ?? 0) / planned) * 100)}%`
                      : "0%",
                  }}
                />
              </div>
            </div>
          ))}
      </div>
      <div className="row between">
        {run.kind === "discovery" && (
          <div className="run-market-counts">
            <span>
              新增 <strong>{progress?.new ?? "—"}</strong>
            </span>
            <span>
              存量 <strong>{progress?.existing ?? "—"}</strong>
            </span>
            <span>
              变化 <strong>{progress?.changed ?? "—"}</strong>
            </span>
            <span>
              市场累计 <strong>{progress?.market_total ?? "—"}</strong>
            </span>
          </div>
        )}
        <div className="actions">
          {runActions(run.status, run.coverage).map((action) => (
            <button
              disabled={busy}
              key={action}
              onClick={() =>
                act(
                  () =>
                    api(
                      `/runs/${encodeURIComponent(run.id)}/${action}`,
                      "POST",
                    ),
                  action === "cancel"
                    ? "已请求取消，已完成结果保留"
                    : "运行控制已提交",
                )
              }
            >
              {
                (
                  {
                    pause: "暂停",
                    resume: "继续",
                    cancel: "取消运行",
                  } as Record<string, string>
                )[action]
              }
            </button>
          ))}
        </div>
      </div>
      {(run.error || !!run.coverage?.unresolved.length) && (
        <RunWarnings
          error={[run.error, ...(run.coverage?.unresolved || [])]
            .filter(Boolean)
            .join("；")}
        />
      )}
      <small className="muted">
        运行编号：{run.id}
        {run.finished_at ? ` · 结束于 ${date(run.finished_at)}` : ""}
      </small>
    </div>
  );
}
