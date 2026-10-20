import "./support/registerTsx.ts";
import { setupDOM } from "./support/dom.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";

// U-15：禁用按钮通过 aria-describedby 指向原因文字（design §9）。
const described = (button: HTMLButtonElement) => document.getElementById(button.getAttribute("aria-describedby") || "")?.textContent;
test("start / update market explains why it is disabled", async () => {
  const h = await setupDOM();
  try {
    const { MarketsPage, startBlockedReason } = await import("../src/pages/MarketsPage.tsx");
    const task = { id: "m1", name: "Market", country: "US", state: "CA", city: "San Jose", keywords: ["k"], company_types: [], auto_enrich: true,
      created_at: "2026-10-15T00:00:00Z", latest_run: { id: "r", status: "paused", created_at: "2026-10-15T00:00:00Z" } };
    await h.act(async () => h.root.render(createElement(MarketsPage, { overview: { loading: false, error: "" }, tasks: { data: [task], loading: false, error: "" },
      busy: false, act: async () => {}, refresh: () => {}, setTaskDetail: () => {}, openMarket: () => {}, openStandalone: () => {}, setModal: () => {} } as never)));
    const button = [...document.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent === "更新市场")!;
    assert.equal(button.disabled, true);
    assert.equal(described(button), "本轮还在已暂停，结束或取消后才能开始新一轮。");
    assert.equal(startBlockedReason("running"), "本轮还在运行中，结束或取消后才能开始新一轮。");
  } finally { await h.cleanup(); }
});
test("复核官网 without a website explains why it is disabled", async () => {
  const h = await setupDOM("http://localhost/?view=detail&company=c1&page=1");
  (window as any).HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
  const company = { id: "c1", name: "No Site Co", address: "", phone: "", website: "", city: "", country: "US", business_types: [], status: "candidate",
    notes: "", revision: 1, created_at: "2026-10-01", last_checked_at: null, contact_count: 0 };
  globalThis.fetch = async (input) => {
    const path = String(input);
    const body = path === "/api/session" ? { token: "t" } : path === "/api/overview" ? { worker: { alive: true }, source_status: {} }
      : path === "/api/companies/c1" ? { company, contacts: [], observations: [], changes: [], social_links: [] }
      : path === "/api/companies/c1/social" ? { accounts: [], posts: [], excluded: [], ignored_links: [], outreach: [], do_not_contact: false, search_queries: [], pages: [] }
      : path === "/api/backups" ? [{ name: "b1.sqlite3", created_at: "2026-10-01T00:00:00Z", kind: "manual" }]
      : path === "/api/social/settings" ? { configured: false, platforms: [], message: "" }
      : path === "/api/settings" ? { data_dir: "/tmp/x", version: "0.1.0" }
      : path.startsWith("/api/social/connections") ? { reader: "http", instagram_reader: "browser", proxy_url: "", max_posts: 3, sessions: {}, dependencies: { browser: true, instaloader: false }, youtube_key_configured: false }
      : path.startsWith("/api/retention") ? { maps_days: 30, backup_count: 10, export_days: 7, usage: {}, pending: { total_files: 0, total_bytes: 0, items: [] }, history: [] }
      : [];
    return new Response(JSON.stringify(body));
  };
  try {
    const { default: App } = await import("../src/App.tsx");
    await h.act(async () => { h.root.render(createElement(App)); await new Promise((r) => setTimeout(r, 40)); });
    const refresh = [...document.querySelectorAll<HTMLButtonElement>(".page-heading button")].find((b) => b.textContent!.includes("复核官网"))!;
    assert.equal(refresh.disabled, true);
    assert.equal(described(refresh), "没有官网地址，无法复核。可先在“编辑资料”里补充官网。");
  } finally { await h.cleanup(); }
});
test("U-16: fallbacks never show raw ids", async () => {
  const h = await setupDOM();
  try {
    const { ChangeList } = await import("../src/pages/ChangeList.tsx");
    await h.act(async () => h.root.render(createElement(ChangeList, { items: [{ id: "x", company_id: "raw-company-id", company_name: null, field: "phone", old_value: "", new_value: "1", reason: "", created_at: "2026-10-01" }] as never, onOpen: () => {} })));
    assert.match(document.body.textContent!, /关联企业信息缺失/);
    assert.doesNotMatch(document.body.textContent!, /raw-company-id/);
  } finally { await h.cleanup(); }
});
