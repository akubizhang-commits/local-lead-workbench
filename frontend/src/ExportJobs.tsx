import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, download } from "./api";
import type { Filters } from "./helpers";
import { actionMessage } from "./errors";

/** Background export job as returned by /api/export-jobs (P1-2, design §8.3). */
export interface ExportJob {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  phase: "queued" | "reading" | "building" | "writing" | "done";
  format: "csv" | "xlsx";
  layout: "entries" | "companies";
  filters: Record<string, unknown>;
  label: string;
  processed: number;
  total: number | null;
  rows: number;
  file_name: string;
  size_bytes: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: { code: string; message: string } | null;
}

export const EXPORT_BUSY = "已有导出正在进行，请等待完成";
export const EXPORT_DISABLED = "正在生成导出文件";
const live = (job: ExportJob) => job.status === "queued" || job.status === "running";

/** Body for POST /export-jobs: the same non-empty filters as the list query string. */
export function exportBody(filters: Filters | Record<string, unknown>, format: "csv" | "xlsx", layout: "entries" | "companies") {
  const body: Record<string, unknown> = { format, layout };
  for (const [key, value] of Object.entries(filters)) {
    if (Array.isArray(value)) {
      const items = value.filter((v) => typeof v === "string" && v.trim() !== "");
      if (items.length) body[key] = items;
    } else if (typeof value === "string" && value.trim() !== "") body[key] = value;
  }
  return body;
}

const number = (value: number) => value.toLocaleString("en-US");
export function formatBytes(bytes: number | null) {
  return size(bytes);
}
function size(bytes: number | null) {
  if (bytes === null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
function seconds(job: ExportJob) {
  if (!job.started_at || !job.finished_at) return 0;
  return Math.max(0, Math.round((Date.parse(job.finished_at) - Date.parse(job.started_at)) / 1000));
}

/** One line of status text per job (copy from design §12). */
export function jobText(job: ExportJob, cancelling = false): string {
  if (job.status === "succeeded") return `导出完成：${job.label} · ${size(job.size_bytes)} · 用时 ${seconds(job)} 秒`;
  if (job.status === "cancelled") return "已取消导出";
  if (job.status === "failed") {
    if (job.error?.code === "interrupted") return job.error.message;
    return `导出失败：${job.error?.message || "请重试"}`;
  }
  if (cancelling) return "正在取消…";
  if (job.phase === "reading") return "正在读取数据…";
  if (job.phase === "building") return `已处理 ${number(job.processed)} / ${number(job.total ?? 0)} 家企业`;
  if (job.phase === "writing") return `正在写入文件（${number(job.rows)} 行）…`;
  return "正在准备导出…";
}

/** Phase-level announcement for the polite live region (not every progress tick). */
function phaseText(job: ExportJob) {
  if (job.status !== "running" && job.status !== "queued") return jobText(job);
  return { queued: "正在准备导出…", reading: "正在读取数据…", building: "正在处理企业…",
           writing: "正在写入文件…", done: "" }[job.phase];
}

export function useExportJobs(pollMs = 1000) {
  const [jobs, setJobs] = useState<ExportJob[]>([]);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [cancelling, setCancelling] = useState<Record<string, boolean>>({});
  const mounted = useRef(true);
  const merge = useCallback((job: ExportJob) => {
    setJobs((current) => [job, ...current.filter((j) => j.id !== job.id)].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 5));
  }, []);
  useEffect(() => {
    mounted.current = true;
    api<ExportJob[]>("/export-jobs?limit=5").then((list) => mounted.current && setJobs(list)).catch(() => {});
    return () => { mounted.current = false; };
  }, []);
  const active = jobs.find(live);
  // Bumped after a failed poll so the effect schedules the next one even though `jobs` did not change.
  const [pollFailures, setPollFailures] = useState(0);
  useEffect(() => {
    if (!active) return;
    const timer = setTimeout(async () => {
      try {
        const job = await api<ExportJob>(`/export-jobs/${active.id}`);
        if (mounted.current) {
          merge(job);
          if (pollFailures) {
            setPollFailures(0);
            setError("");
          }
        }
      } catch (e) {
        if (mounted.current) {
          setError(e instanceof Error ? actionMessage(e) : String(e));
          setPollFailures((n) => n + 1);
        }
      }
    }, pollMs);
    return () => clearTimeout(timer);
  }, [active, pollMs, merge, pollFailures]);
  async function start(body: Record<string, unknown>) {
    setNotice("");
    setError("");
    try {
      merge(await api<ExportJob>("/export-jobs", "POST", body));
      return true;
    } catch (e) {
      const busy = e instanceof ApiError && e.code === "export_busy";
      if (!busy) throw e;
      setNotice(EXPORT_BUSY);
      const jobId = typeof e.body?.job_id === "string" ? e.body.job_id : undefined;
      const list = await api<ExportJob[]>("/export-jobs?limit=5");
      setJobs(list);
      if (jobId && !list.some((j) => j.id === jobId)) merge(await api<ExportJob>(`/export-jobs/${jobId}`));
      return false;
    }
  }
  /** "重新导出": like start(), but other errors are shown instead of being left unhandled. */
  async function restart(body: Record<string, unknown>) {
    try {
      await start(body);
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? actionMessage(e) : String(e));
    }
  }
  async function cancel(job: ExportJob) {
    setCancelling((c) => ({ ...c, [job.id]: true }));
    try {
      merge(await api<ExportJob>(`/export-jobs/${job.id}/cancel`, "POST"));
    } catch (e) {
      setError(e instanceof Error ? actionMessage(e) : String(e));
    }
  }
  async function fetchFile(job: ExportJob) {
    setError("");
    try {
      await download(`/export-jobs/${job.id}/file`, job.file_name);
      setNotice("导出文件已下载");
    } catch (e) {
      setError(e instanceof Error ? actionMessage(e) : String(e));
    }
  }
  return { jobs, active, notice, error, cancelling, start, restart, cancel, fetchFile };
}

export type ExportJobsState = ReturnType<typeof useExportJobs>;

export function ExportJobsPanel({ state, exportDays = 7 }: { state: ExportJobsState; exportDays?: number }) {
  const { jobs, notice, error, cancelling } = state;
  if (!jobs.length && !notice && !error) return null;
  const announce = jobs[0] ? phaseText(jobs[0]) : "";
  return (
    <section className="panel export-jobs" aria-labelledby="export-jobs-title">
      <div className="section-title">
        <h3 id="export-jobs-title">导出任务</h3>
      </div>
      <p className="muted">导出在后台生成，期间可以继续使用其他页面；文件在本机保留 {exportDays} 天。</p>
      <p className="sr-only" aria-live="polite">{announce}</p>
      {notice && <p className="export-notice" role="status">{notice}</p>}
      {error && <p className="alert" role="alert">{error}</p>}
      <ul className="export-job-list">
        {jobs.map((job) => {
          const text = jobText(job, !!cancelling[job.id]);
          return (
            <li key={job.id} className={`export-job export-${job.status}`}>
              <div>
                <strong>{job.label}</strong>
                <span className="muted">{text}</span>
              </div>
              {live(job) && (
                <progress max={job.total || 1} value={job.phase === "building" ? job.processed : job.phase === "writing" ? job.total || 1 : 0}
                          aria-label={`${job.label} 导出进度`} aria-valuetext={text} />
              )}
              <div className="actions">
                {live(job) && <button disabled={!!cancelling[job.id]} onClick={() => void state.cancel(job)}>取消</button>}
                {job.status === "succeeded" && <button className="primary" onClick={() => void state.fetchFile(job)}>下载文件</button>}
                {(job.status === "failed" || job.status === "cancelled") && (
                  <button disabled={!!state.active} onClick={() => void state.restart({ ...job.filters, format: job.format, layout: job.layout })}>重新导出</button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
