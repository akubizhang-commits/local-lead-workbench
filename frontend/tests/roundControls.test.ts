import "./support/registerTsx.ts";
import { setupDOM } from "./support/dom.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";

// RoundControls (design §4.1 第 2 条, review R4): buttons per status, titles, cancel confirmation,
// polling to a finished state and focus after an action.
const RUN = { id: "r1", task_id: "t1", kind: "discovery", status: "running", progress: { done: 0, total: 0, discovered: 0 },
  message: "等待本地 worker", created_at: "2026-10-20T00:00:00Z", finished_at: null, error: null };
async function mount(run: Record<string, unknown>, { rounds = [{ id: "r0" }, { id: "r1" }], standalone = false, confirm = true } = {}) {
  const h = await setupDOM();
  const calls: string[] = [];
  const state = { run: { ...RUN, ...run } };
  globalThis.fetch = async (input, init) => {
    const path = String(input);
    if (path === "/api/session") return new Response(JSON.stringify({ token: "t" }));
    if (init?.method === "POST") { calls.push(path); return new Response("{}"); }
    if (path === "/api/runs/r1") return new Response(JSON.stringify(state.run));
    if (path === "/api/runs/gone") return new Response(JSON.stringify({ detail: "轮次不存在", code: "not_found" }), { status: 404 });
    throw new Error(`unexpected ${path}`);
  };
  const prompts: string[] = [];
  window.confirm = (text?: string) => { prompts.push(String(text)); return confirm; };
  const { RoundControls } = await import("../src/RoundControls.tsx");
  const { restoreFocus } = await import("../src/App.tsx");
  const { nextFrame } = await import("../src/hooks.ts");
  const { useState } = await import("react");
  // Mirrors App.act(): disable controls while busy, bump version, then restore focus on the next frame.
  function Harness({ runId }: { runId: string }) {
    const [busy, setBusy] = useState(false);
    const [version, setVersion] = useState(0);
    async function act(work: () => Promise<unknown>) {
      const prev = document.activeElement as HTMLElement | null;
      setBusy(true);
      try { await work(); setVersion((v) => v + 1); } finally { setBusy(false); nextFrame(() => restoreFocus(prev)); }
    }
    return createElement(RoundControls, { runId, rounds, standalone, version, busy, act: act as never, onViewNew: () => {} });
  }
  const wait = (ms = 40) => h.act(async () => { await new Promise((r) => setTimeout(r, ms)); });
  const render = async (runId = "r1") => { await h.act(async () => h.root.render(createElement(Harness, { runId }))); await wait(); };
  const buttons = () => [...document.querySelectorAll(".round-controls button")].map((b) => b.textContent);
  return { ...h, calls, prompts, state, render, wait, buttons };
}

test("buttons follow the round status", async () => {
  for (const [status, want] of [["running", ["暂停本轮", "取消本轮…"]], ["queued", ["暂停本轮", "取消本轮…"]],
    ["paused", ["继续本轮", "取消本轮…"]], ["blocked", ["继续本轮", "取消本轮…"]], ["failed", ["继续本轮"]], ["partial", ["继续本轮"]],
    ["completed", ["查看本轮新增企业 →"]], ["cancelled", ["查看本轮新增企业 →"]]] as const) {
    const h = await mount({ status });
    try {
      await h.render();
      assert.deepEqual(h.buttons(), want, status);
      if (status === "paused") assert.equal(document.querySelector(".round-controls button")!.className, "primary");
    } finally { await h.cleanup(); }
  }
});

test("titles: numbered round, batch website recheck and single-company recheck; message goes through runMessage", async () => {
  const cases: [Record<string, unknown>, boolean, RegExp][] = [
    [{}, false, /^本轮（第 1 轮）· 运行中$/],
    [{ kind: "refresh", task_id: null }, false, /^批量复核官网 · 运行中$/],
    [{ kind: "refresh", task_id: null }, true, /^单家官网复核 · 运行中$/],
  ];
  for (const [run, standalone, title] of cases) {
    const h = await mount(run, { standalone, rounds: [{ id: "r1" }] });
    try {
      await h.render();
      assert.match(document.querySelector(".round-controls h2")!.textContent!, title);
      assert.equal(document.querySelector(".round-controls h2")!.getAttribute("tabindex"), "-1");
      assert.match(document.querySelector(".round-controls")!.textContent!, /等待后台处理/);
      assert.doesNotMatch(document.querySelector(".round-controls")!.textContent!, /worker/);
    } finally { await h.cleanup(); }
  }
  const { roundTitle } = await import("../src/RoundControls.tsx");
  assert.equal(roundTitle({ id: "x", kind: "discovery", task_id: "t" }, { rounds: [{ id: "r1" }], standalone: false }), "本轮");
});

test("cancel asks first with the D13 wording; declining sends nothing", async () => {
  const h = await mount({ status: "running" }, { confirm: false });
  try {
    await h.render();
    await h.act(async () => ([...document.querySelectorAll<HTMLButtonElement>(".round-controls button")].find((b) => b.textContent === "取消本轮…")!).click());
    await h.wait();
    assert.deepEqual(h.prompts, ["取消后本轮不再继续，已取得的企业和资料都会保留。暂停也能停下本轮，之后还可以继续。确定取消本轮吗？"]);
    assert.deepEqual(h.calls, []);
  } finally { await h.cleanup(); }
});

test("a missing round shows a one-line notice instead of controls", async () => {
  const h = await mount({});
  try {
    await h.render("gone");
    assert.equal(document.querySelector(".round-controls"), null);
    assert.match(document.body.textContent!, /这一轮已不存在，可能已被备份恢复替换。/);
  } finally { await h.cleanup(); }
});

test("pause keeps focus on the control bar, and polling to completed swaps in the results link", async () => {
  const h = await mount({ status: "running" });
  try {
    await h.render();
    const pause = [...document.querySelectorAll<HTMLButtonElement>(".round-controls button")].find((b) => b.textContent === "暂停本轮")!;
    pause.focus();
    h.state.run = { ...h.state.run, status: "paused" };
    await h.act(async () => pause.click());
    await h.wait(80);
    assert.deepEqual(h.calls, ["/api/runs/r1/pause"]);
    assert.deepEqual(h.buttons(), ["继续本轮", "取消本轮…"]);
    const active = document.activeElement!;
    assert.notEqual(active, document.body);
    assert.ok(active.closest(".round-controls"), "focus stays inside the control bar");
    // Poll (5 s timer) → completed.
    h.state.run = { ...h.state.run, status: "completed", finished_at: "2026-10-20T01:00:00Z" };
    await h.wait(5200);
    assert.deepEqual(h.buttons(), ["查看本轮新增企业 →"]);
  } finally { await h.cleanup(); }
});
