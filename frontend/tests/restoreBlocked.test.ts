import "./support/registerTsx.ts";
import { setupDOM } from "./support/dom.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";

// P1-16 前端（design §6.2 / §10）：恢复被拒面板的标题、6 类条目、按钮路由、未列出数量和畸形 payload。
const T = "2026-10-20T00:00:00Z";
const RUNS = [
  { id: "m1", kind: "discovery", status: "queued", created_at: T, task_id: "hou", task_name: "Houston 市场" },
  { id: "b1", kind: "refresh", status: "running", created_at: T, task_id: null, batch_market_id: "la", batch_market_name: "LA 市场", company_count: 7 },
  { id: "c1", kind: "refresh", status: "queued", created_at: T, task_id: null, company_id: "co1", company_name: "Solo Co" },
  { id: "x1", kind: "weird", status: "running", created_at: T, task_id: null },
];
const SOCIAL = [
  { id: "s1", status: "running", created_at: T, task_id: "la", task_name: "LA 市场", parent_run_id: "p1" },
  { id: "s2", status: "queued", created_at: T, task_id: null, company_id: "co2", company_name: "Social Co", company_count: 3 },
  { id: "s3", status: "queued", created_at: T, task_id: null, company_id: null, company_name: null, company_count: 0 },
];

test("six entry kinds: text and routes", async () => {
  const { blockerEntries, parseBlockers } = await import("../src/RestoreBlocked.tsx");
  const e = blockerEntries(parseBlockers({ runs: RUNS, runs_total: 4, social_runs: SOCIAL, social_runs_total: 3 }));
  assert.deepEqual(e.map((x) => [x.first, x.second.replace(/ · [^·]* 开始/, " · {时间} 开始"), x.action ?? null, x.route ?? null]), [
    ["Houston 市场", "市场更新 · 排队中 · {时间} 开始", "前往暂停", { view: "tasks", task: "hou", run: "m1", page: 1 }],
    ["LA 市场 · 7 家", "批量复核官网 · 运行中 · {时间} 开始", "前往暂停", { view: "tasks", task: "la", run: "b1", page: 1 }],
    ["Solo Co", "单家官网复核 · 排队中 · {时间} 开始", "前往暂停", { view: "tasks", task: "standalone", run: "c1", page: 1 }],
    ["轮次", "weird · 运行中 · {时间} 开始 · 可在目标市场查看", null, null],
    ["LA 市场", "社媒补充 · 运行中 · 跟随所属轮次，等待完成即可", "前往查看", { view: "tasks", task: "la", run: "p1", page: 1 }],
    ["Social Co 等 3 家", "社媒补充 · 排队中 · 不能暂停，等待完成即可", "前往查看", { view: "detail", selected: "co2", page: 1, section: "contacts" }],
    ["关联企业信息缺失", "社媒补充 · 排队中 · 不能暂停，等待完成即可", null, null],
  ]);
  assert.match(e[0].second, /^市场更新 · 排队中 · .+ 开始$/);
});

test("three titles, the fallback title and the unlisted count", async () => {
  const { blockerTitle, parseBlockers, unlisted } = await import("../src/RestoreBlocked.tsx");
  assert.equal(blockerTitle(parseBlockers({ runs: RUNS.slice(0, 1), runs_total: 2, social_runs: SOCIAL.slice(0, 1), social_runs_total: 1 })),
    "有 2 个轮次正在排队或运行，另有 1 项社媒补充，暂时不能恢复");
  assert.equal(blockerTitle(parseBlockers({ runs: RUNS.slice(0, 1), runs_total: 1, social_runs: [], social_runs_total: 0 })),
    "有 1 个轮次正在排队或运行，暂时不能恢复");
  assert.equal(blockerTitle(parseBlockers({ runs: [], runs_total: 0, social_runs: SOCIAL.slice(1, 2), social_runs_total: 1 })),
    "有 1 项社媒补充正在排队或运行，暂时不能恢复");
  assert.equal(blockerTitle(parseBlockers({ detail: "x" })), "有排队中或运行中的轮次，暂时不能恢复");
  assert.equal(unlisted(parseBlockers({ runs: RUNS.slice(0, 2), runs_total: 12, social_runs: SOCIAL.slice(0, 1), social_runs_total: 3 })), 12);
});

test("malformed payloads are dropped without throwing", async () => {
  const { parseBlockers, blockerEntries } = await import("../src/RestoreBlocked.tsx");
  const b = parseBlockers({ runs: "nope", runs_total: -3, social_runs: [null, 1, { id: 5 }, { id: "" }, { id: "ok", status: "queued" }], social_runs_total: "x" });
  assert.deepEqual([b.runs, b.runs_total, b.social_runs.map((s) => s.id), b.social_runs_total, b.complete], [[], 0, ["ok"], 1, true]);
  assert.equal(blockerEntries(b).length, 1);
  const none = parseBlockers({ runs: [{}], social_runs: [{ id: 3 }] });
  assert.equal(none.complete, false);
});

test("panel renders entries with labelled buttons, takes focus and never says 取消活动任务", async () => {
  const h = await setupDOM();
  try {
    const { RestoreBlocked } = await import("../src/RestoreBlocked.tsx");
    const went: unknown[] = [];
    await h.act(async () => h.root.render(createElement(RestoreBlocked, {
      body: { detail: "有排队中或运行中的轮次，暂停或等待结束后即可恢复", code: "restore_blocked_runs", runs: RUNS.slice(0, 1), runs_total: 3, social_runs: [], social_runs_total: 0 },
      onGo: (r: unknown) => went.push(r) })));
    const panel = document.querySelector(".restore-blocked")!;
    assert.equal(panel.getAttribute("role"), "alert");
    assert.equal(document.activeElement, panel.querySelector("h3"));
    assert.match(panel.textContent!, /暂停或等待这些轮次结束后，再点“确认恢复”即可，不需要取消。/);
    assert.match(panel.textContent!, /另有 2 项未列出，可在目标市场查看。/);
    const button = panel.querySelector("button")!;
    assert.equal(button.getAttribute("aria-label"), "前往暂停：Houston 市场");
    await h.act(async () => button.click());
    assert.deepEqual(went, [{ view: "tasks", task: "hou", run: "m1", page: 1 }]);
    assert.doesNotMatch(panel.textContent!, /取消活动任务|资料已变化/);
    await h.act(async () => h.root.render(createElement(RestoreBlocked, { body: {}, onGo: (r: unknown) => went.push(r) })));
    const link = [...document.querySelectorAll("button")].find((b) => b.textContent === "前往目标市场")!;
    await h.act(async () => link.click());
    assert.deepEqual(went.at(-1), { view: "tasks", page: 1 });
  } finally { await h.cleanup(); }
});
