import { setupDOM } from "./support/dom.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";

const policy = { maps_raw_days: 30, auto_backup_keep: 10, export_days: 7 };
const items = [
  { category: "maps_raw", path: "runs/r1/i1/maps-" + "a".repeat(32), size_bytes: 2048, reason: "已超过 30 天" },
  { category: "auto_backup", path: "backups/auto-pre-restore-1.sqlite3", size_bytes: 1024 * 1024, reason: "超出最近 10 份" },
];
const awaiting = {
  policy, state: "awaiting_confirmation", confirmed_at: null, confirmed_policy: null, history: [],
  usage: { runs_bytes: 4096, backups_bytes: 2048, exports_bytes: 0, logs_bytes: 100 },
  preview: { plan_id: "p".repeat(64), total_files: 2, total_bytes: 1024 * 1024 + 2048, items, truncated: false },
};
const active = {
  ...awaiting, state: "active", confirmed_at: "2026-10-19T00:00:00Z", confirmed_policy: policy,
  history: [{ at: "2026-10-19T00:00:00Z", trigger: "confirm", deleted: 2, freed_bytes: 1050624, errors: 0 }],
  preview: { ...awaiting.preview, total_files: 0, total_bytes: 0, items: [] },
};
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const text = () => document.body.textContent || "";
const button = (label: string) => [...document.querySelectorAll("button")].find((b) => b.textContent === label);

async function mount(h: Awaited<ReturnType<typeof setupDOM>>, props: Record<string, unknown> = {}) {
  window.HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
  const { RetentionPanel } = await import("../src/RetentionPanel.tsx");
  await h.act(async () => {
    h.root.render(createElement(RetentionPanel, { version: 1, workerAlive: true, ...props }));
    await new Promise((r) => setTimeout(r, 30));
  });
}
const settle = (h: Awaited<ReturnType<typeof setupDOM>>) => h.act(async () => { await new Promise((r) => setTimeout(r, 30)); });

test("awaiting confirmation lists files, confirms through the dialog and shows the active state", async () => {
  const h = await setupDOM();
  const posts: unknown[] = [];
  let changed = 0;
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url === "/api/session") return json({ token: "t" });
    if (url === "/api/retention") return json(awaiting);
    if (url === "/api/retention/confirm" && init?.method === "POST") {
      posts.push(JSON.parse(String(init.body)));
      return json({ deleted: 2, freed_bytes: 1050624, errors: [], ...active });
    }
    throw new Error("unexpected " + url);
  };
  try {
    await mount(h, { onChanged: () => changed++ });
    assert.match(text(), /首次清理前需要你确认。确认后将删除下列文件，之后按策略自动清理并记录。/);
    assert.match(text(), /将删除 2 个文件，共 1\.0 MB/);
    assert.equal(document.querySelectorAll("tbody tr").length, 2);
    assert.match(text(), /地图原始结果.*runs\/r1\/i1\/maps-/);
    assert.match(text(), /手动备份不会被自动删除；企业、联系人等数据库记录不受影响。/);
    await h.act(async () => button("确认清理")!.click());
    assert.match(text(), /确认删除 2 个文件（共 1\.0 MB）？删除后无法恢复。手动备份和企业、联系人等数据库记录不受影响。/);
    assert.equal(posts.length, 0);
    await h.act(async () => button("确认删除")!.click());
    await settle(h);
    assert.deepEqual(posts, [{ plan_id: "p".repeat(64) }]);
    assert.match(text(), /已启用自动清理 · 上次清理：.*删除 2 个文件，释放 1\.0 MB/);
    assert.equal(button("确认删除"), undefined);
    assert.equal(changed, 1);
  } finally {
    await h.cleanup();
  }
});

test("a changed plan shows the notice and reloads the list", async () => {
  const h = await setupDOM();
  let loads = 0;
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url === "/api/session") return json({ token: "t" });
    if (url === "/api/retention") { loads++; return json(awaiting); }
    if (url === "/api/retention/confirm" && init?.method === "POST")
      return json({ detail: "待清理文件已变化，请重新查看后确认", code: "retention_plan_changed", request_id: "req_1", preview: awaiting.preview }, 409);
    throw new Error("unexpected " + url);
  };
  try {
    await mount(h);
    await h.act(async () => button("确认清理")!.click());
    await h.act(async () => button("确认删除")!.click());
    await settle(h);
    assert.match(text(), /待清理文件已变化，请重新查看后确认/);
    assert.equal(loads, 2);
  } finally {
    await h.cleanup();
  }
});

test("empty list enables directly; no worker hint; form range validation", async () => {
  const h = await setupDOM();
  const puts: unknown[] = [];
  const empty = { ...awaiting, preview: { ...awaiting.preview, total_files: 0, total_bytes: 0, items: [] } };
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url === "/api/session") return json({ token: "t" });
    if (url === "/api/retention") return json(empty);
    if (url === "/api/retention/policy" && init?.method === "PUT") { puts.push(JSON.parse(String(init.body))); return json(empty); }
    throw new Error("unexpected " + url);
  };
  try {
    await mount(h, { workerAlive: false });
    assert.match(text(), /目前没有需要清理的文件。/);
    assert.ok(button("启用自动清理"));
    assert.match(text(), /后台未运行时不会自动清理/);
    const input = document.querySelector<HTMLInputElement>('input[min="7"]')!;
    assert.equal(input.getAttribute("max"), "365");
    assert.equal(input.closest("label")!.textContent!.startsWith("地图原始结果保留天数（7–365）"), true);
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!;
    await h.act(async () => {
      setter.call(input, "3");
      input.dispatchEvent(new window.Event("input", { bubbles: true }));
    });
    const error = document.getElementById(input.getAttribute("aria-describedby")!)!;
    assert.equal(error.textContent, "请输入 7–365 之间的整数");
    assert.equal(input.getAttribute("aria-invalid"), "true");
    assert.equal(button("保存策略")!.disabled, true);
    await h.act(async () => {
      setter.call(input, "60");
      input.dispatchEvent(new window.Event("input", { bubbles: true }));
    });
    assert.equal(button("保存策略")!.disabled, false);
    await h.act(async () => button("保存策略")!.click());
    await settle(h);
    assert.deepEqual(puts, [{ maps_raw_days: 60, auto_backup_keep: 10, export_days: 7 }]);
  } finally {
    await h.cleanup();
  }
});

test("sidebar marker shows only while awaiting with pending files", async () => {
  const h = await setupDOM();
  try {
    const { RetentionMarker } = await import("../src/RetentionPanel.tsx");
    const render = (retention: unknown) =>
      h.act(async () => { h.root.render(createElement(RetentionMarker, { overview: { retention } as never })); });
    await render({ state: "awaiting_confirmation", pending_files: 3, pending_bytes: 10 });
    const marker = document.querySelector("em")!;
    assert.equal(marker.textContent, "!");
    assert.equal(marker.getAttribute("aria-label"), "存储清理待确认");
    assert.equal(marker.getAttribute("title"), "存储清理待确认");
    await render({ state: "active", pending_files: 3, pending_bytes: 10 });
    assert.equal(document.querySelector("em"), null);
    await render({ state: "awaiting_confirmation", pending_files: 0, pending_bytes: 0 });
    assert.equal(document.querySelector("em"), null);
    await render({ state: "awaiting_confirmation", pending_files: null, pending_bytes: null });
    assert.equal(document.querySelector("em"), null);
  } finally {
    await h.cleanup();
  }
});
