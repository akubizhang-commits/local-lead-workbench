import { setupDOM } from "./support/dom.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { createElement, type ReactElement } from "react";
// AWS trial follow-up: server deployments (LEAD_MODE=server) use server wording; local mode keeps the original text.
type Mode = "local" | "server";
const LOCAL_ONLY = ["本地服务已连接", "本地运行环境", "管理本地数据", "打开登录浏览器", "电脑唤醒", "本机保留", "本地线索工作台"];
const NOTE = "服务器部署版暂不支持登录社媒账号读取，可使用公开资料读取。";
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const connections = (mode: Mode) => ({
  reader: "http", instagram_reader: "browser", proxy_url: "", max_posts: 3, youtube_key_configured: false,
  dependencies: { browser: true, instaloader: false },
  sessions: { instagram: { state: "closed" }, facebook: { state: "closed" }, x: { state: "closed" } },
  login_supported: mode === "local",
});
const schedule = { id: "s1", task_id: "m1", company_id: null, kind: "discovery", interval_months: 1, timezone: "UTC",
  next_due_at: "2026-11-15T00:00:00+00:00", enabled: true, anchor: "2026-10-15T00:00:00+00:00", task_name: "Market" };
const exportJob = { id: "a".repeat(32), status: "succeeded", phase: "done", format: "xlsx", layout: "entries",
  filters: { format: "xlsx", layout: "entries", q: "" }, label: "联系入口 XLSX", processed: 10, total: 10, rows: 10,
  file_name: "联系入口.xlsx", size_bytes: 2048, created_at: "2026-10-18T00:00:00Z", started_at: "2026-10-18T00:00:00Z",
  finished_at: "2026-10-18T00:00:05Z", error: null };
function respond(mode: Mode, path: string): unknown {
  if (path === "/api/session") return { token: "t" };
  if (path === "/api/overview")
    return { companies: 1, contacts: 0, tasks: 0, pending_reviews: 0, running_runs: 0, worker: { alive: true, last_seen: null },
      source_status: { maps: { available: true, message: "" }, website: { available: true, message: "" } } };
  if (path === "/api/settings") return { data_dir: "/data", version: "0.1.0", network_mode: "direct-public", paid_services: false, ai_enabled: false };
  if (path === "/api/backups/status") return { daily_enabled: false, time: "03:00", s3: { configured: false } };
  if (path === "/api/social/settings") return { searxng_url: "", configured: false, platforms: [], message: "" };
  if (path === "/api/social/connections") return connections(mode);
  if (path === "/api/schedules" || path.startsWith("/api/schedules?")) return [schedule];
  if (path.startsWith("/api/export-jobs?")) return [exportJob];
  if (path.startsWith("/api/retention")) return {};
  return [];
}
async function render(mode: Mode, element: () => Promise<ReactElement>, url = "http://localhost/") {
  const env = await setupDOM(url);
  globalThis.fetch = (async (input: RequestInfo | URL) => json(respond(mode, String(input)))) as typeof fetch;
  const { SessionContext } = await import("../src/session.ts");
  const { setServerMode } = await import("../src/api.ts");
  setServerMode(mode === "server");
  const child = await element();
  await env.act(async () => env.root.render(createElement(SessionContext.Provider, { value: { mode, auth: mode === "server", username: "demo" } }, child)));
  await env.act(async () => { await new Promise((r) => setTimeout(r, 40)); });
  return { env, text: () => document.body.textContent || "", done: async () => { setServerMode(false); await env.cleanup(); } };
}
const app = async () => createElement((await import("../src/App.tsx")).default);
const assertNoLocalWording = (text: string) => {
  for (const phrase of LOCAL_ONLY) assert.ok(!text.includes(phrase), `server mode shows “${phrase}”`);
};
for (const mode of ["local", "server"] as const) {
  const server = mode === "server";
  test(`${mode}: header, sidebar brand, settings description and runtime title`, async () => {
    const h = await render(mode, app, "http://localhost/?view=settings");
    try {
      const status = document.querySelector(".connection.service")!;
      assert.equal(status.getAttribute("title"), server ? "服务已连接" : "本地服务已连接");
      assert.equal(status.querySelector(".connection-text")!.textContent, server ? "服务已连接" : "本地服务已连接");
      assert.equal(document.querySelector(".brand span:not(.brand-symbol)")!.textContent, server ? "线索工作台" : "本地线索工作台");
      assert.equal(document.querySelector(".page-footer span")!.textContent, server ? "寻迹 · 线索工作台" : "寻迹 · 本地线索工作台");
      const headings = Array.from(document.querySelectorAll("h3")).map((e) => e.textContent?.trim());
      assert.ok(headings.includes(server ? "服务器运行环境" : "本地运行环境"), JSON.stringify(headings));
      assert.ok(h.text().includes(server ? "管理服务器数据、来源能力与定时维护。" : "管理本地数据、来源能力与定时维护。"));
      assert.ok(h.text().includes(server ? "由服务器按计划自动执行；服务停止期间到期的计划，恢复后最多补跑一次。" : "仅在应用运行且电脑唤醒时执行；错过的周期恢复后最多补跑一次。"));
      if (server) assertNoLocalWording(h.text());
    } finally {
      await h.done();
    }
  });
  test(`${mode}: social connections login guidance`, async () => {
    const h = await render(mode, async () => createElement((await import("../src/SocialConnections.tsx")).SocialConnections));
    try {
      const section = document.querySelector(".social-section")!;
      const first = section.querySelector(".section-title")!.nextElementSibling!;
      if (server) {
        assert.equal(first.getAttribute("role"), "alert");
        assert.equal(first.textContent, NOTE);
        assert.equal(section.querySelector("ol"), null);
        assert.equal(Array.from(section.querySelectorAll('[role="alert"]')).filter((a) => a.textContent === NOTE).length, 1);
        assertNoLocalWording(h.text());
      } else {
        assert.equal(first.tagName, "OL");
        assert.match(first.textContent || "", /打开登录浏览器，在浏览器窗口中手动登录/);
        assert.ok(!h.text().includes(NOTE));
      }
    } finally {
      await h.done();
    }
  });
  test(`${mode}: market schedule note`, async () => {
    const task = { id: "m1", name: "Market", country: "US", state: "California", city: "San Jose", keywords: ["roofing"],
      company_types: [], auto_enrich: true, created_at: "2026-10-15T00:00:00Z", latest_run: null };
    const h = await render(mode, async () => createElement((await import("../src/MarketSchedule.tsx")).MarketSchedule, { task } as never));
    try {
      assert.ok(h.text().includes(server ? "由服务器按计划自动执行；服务停止期间到期的计划，恢复后最多补跑一次。" : "仅在应用运行且电脑唤醒时执行；错过的周期恢复后最多补跑一次。"));
      if (server) assertNoLocalWording(h.text());
    } finally {
      await h.done();
    }
  });
  test(`${mode}: export jobs retention note`, async () => {
    const h = await render(mode, async () => {
      const { useExportJobs, ExportJobsPanel } = await import("../src/ExportJobs.tsx");
      function Host() { return createElement(ExportJobsPanel, { state: useExportJobs(40) }); }
      return createElement(Host);
    });
    try {
      assert.match(h.text(), /导出完成/);
      assert.ok(h.text().includes(server ? "文件在服务器保留 7 天，请及时下载。" : "文件在本机保留 7 天。"));
      if (server) assertNoLocalWording(h.text());
    } finally {
      await h.done();
    }
  });
}
test("plain helpers take the mode from their caller", async () => {
  const { queueReason } = await import("../src/workerStatus.ts");
  const { warningReasons } = await import("../src/warningSummary.ts");
  const offline = { alive: false, last_seen: null };
  assert.equal(queueReason("queued", offline), "等待后台：请启动本地后台后刷新状态，不需要重复开始。");
  assert.equal(queueReason("queued", offline, true), "等待后台：请检查服务器后台是否已启动，然后刷新状态，不需要重复开始。");
  assert.equal(warningReasons("tls handshake")[0].action, "核对网站证书及本地网络，修复后重试。");
  assert.equal(warningReasons("tls handshake", true)[0].action, "核对网站证书及服务器网络，修复后重试。");
});
