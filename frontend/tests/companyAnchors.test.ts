import "./support/registerTsx.ts";
import { setupDOM } from "./support/dom.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";

// P1-13（design §4.2，D18 A，review R3/R4）：锚点导航、折叠保持、section=contacts、操作后焦点。
test("activeSection uses the 56px offset below the sticky anchor bar", async () => {
  const { activeSection, ANCHOR_OFFSET } = await import("../src/CompanyAnchors.tsx");
  assert.equal(ANCHOR_OFFSET, 56);
  const tops = { "company-summary": 300, "company-contacts": 600, "company-facts": 1200, "company-evidence": 1700, "company-history": 1800 };
  assert.equal(activeSection(tops, 0, 56), "company-summary");
  assert.equal(activeSection(tops, 543, 56), "company-contacts");
  assert.equal(activeSection(tops, 540, 56), "company-summary");
  assert.equal(activeSection(tops, 1150, 56), "company-facts");
  assert.equal(activeSection(tops, 5000, 56), "company-history");
  assert.equal(activeSection({}, 100, 56), "company-summary");
});

const company = { id: "c1", name: "Example Supply Co", address: "1 Main St", phone: "+1 555", website: "https://www.example.com/contact",
  city: "Los Angeles", country: "US", business_types: ["wholesale"], status: "candidate", notes: "", revision: 3,
  created_at: "2026-10-01T00:00:00Z", last_checked_at: null, contact_count: 1, best_tier: "general_email", best_tier_label: "通用邮箱", best_tier_rank: 5,
  actual_city: "Los Angeles", actual_city_basis: "address" };
const contacts = [
  { id: "k1", name: "", email: "info@example.com", url: "", kind: "general_email", tier: "general_email", review_status: "auto_recorded", adoption_status: "automatic", fields: [], observed_at: "2026-10-01T00:00:00Z" },
  { id: "k2", name: "Pending Person", email: "p@example.com", url: "", kind: "person", tier: "named_unknown", review_status: "pending", adoption_status: "automatic", fields: [], observed_at: "2026-10-01T00:00:00Z" },
];
const observations = [1, 2, 3, 4].map((i) => ({ id: `o${i}`, field: i < 4 ? "phone" : "name", value: `v${i}`, method: "website", evidence: "e", source_url: "https://example.com", observed_at: `2026-10-0${i}T00:00:00Z` }));
const changes = [{ id: "ch1", company_id: "c1", company_name: "Example Supply Co", field: "phone", old_value: "", new_value: "+1 555", reason: "r", created_at: "2026-10-02T00:00:00Z" }];
async function mountApp(url = "http://localhost/?view=detail&company=c1&page=1") {
  const h = await setupDOM(url);
  (window as any).HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
  const state = { company: { ...company }, gate: null as null | Promise<void>, release: () => {} };
  const calls: string[] = [];
  globalThis.fetch = async (input, init) => {
    const path = String(input), method = init?.method || "GET";
    calls.push(`${method} ${path}`);
    const json = (v: unknown) => new Response(JSON.stringify(v));
    if (path === "/api/session") return json({ token: "t" });
    if (path === "/api/overview") return json({ worker: { alive: true }, source_status: {} });
    if (path === "/api/companies/c1" && method === "PATCH") {
      state.company = { ...state.company, status: JSON.parse(String(init!.body)).status, revision: 4 };
      return json(state.company);
    }
    if (path === "/api/companies/c1") {
      if (state.gate) await state.gate;
      return json({ company: state.company, contacts, observations, changes, social_links: [] });
    }
    if (path === "/api/companies/c1/social")
      return json({ accounts: [], posts: [], excluded: [], ignored_links: [], outreach: [], do_not_contact: false, search_queries: [], pages: [] });
    return json([]);
  };
  const { default: App } = await import("../src/App.tsx");
  const wait = (ms = 40) => h.act(async () => { await new Promise((r) => setTimeout(r, ms)); });
  await h.act(async () => h.root.render(createElement(App)));
  await wait();
  const hold = () => { state.gate = new Promise<void>((r) => { state.release = () => { state.gate = null; r(); }; }); };
  return { ...h, wait, state, calls, hold };
}

test("anchors render without IntersectionObserver; 概要 is current; 来源证据 opens its details and takes focus", async () => {
  const h = await mountApp();
  try {
    assert.equal(typeof (globalThis as any).IntersectionObserver, "undefined");
    const nav = document.querySelector("nav.detail-anchors")!;
    assert.equal(nav.getAttribute("aria-label"), "页内导航");
    const buttons = [...nav.querySelectorAll("button")];
    assert.deepEqual(buttons.map((b) => b.textContent), ["概要", "联系入口 1", "资料", "来源证据 4", "历史 1"]);
    assert.equal(buttons[0].getAttribute("aria-current"), "true");
    assert.equal(document.querySelector("main h1")!.textContent, "Example Supply Co");
    const evidence = document.getElementById("company-evidence") as HTMLDetailsElement;
    assert.equal(evidence.open, false);
    await h.act(async () => buttons[3].click());
    assert.equal(evidence.open, true);
    assert.equal(document.activeElement, evidence.querySelector("summary"));
    assert.match(evidence.querySelector("summary")!.textContent!, /来源证据 · 4 条 联系电话 3 · 企业名称 1/);
    assert.match(evidence.textContent!, /显示其余 0 条|联系电话 · 3 条/);
  } finally { await h.cleanup(); }
});

test("an action re-read keeps open details, the heading and focus on the customer-match select", async () => {
  const h = await mountApp();
  try {
    const evidence = document.getElementById("company-evidence") as HTMLDetailsElement;
    const history = document.getElementById("company-history") as HTMLDetailsElement;
    const pending = [...document.querySelectorAll<HTMLDetailsElement>("#company-contacts details")].find((d) => d.querySelector("summary")!.textContent!.startsWith("待确认联系人"))!;
    evidence.open = true; history.open = true; pending.open = true;
    const select = document.querySelector<HTMLSelectElement>(".page-heading .quick-status select")!;
    select.focus();
    h.hold();
    await h.act(async () => { select.value = "verified"; select.dispatchEvent(new window.Event("change", { bubbles: true })); });
    await h.wait();
    assert.equal(document.querySelector("main h1")!.textContent, "Example Supply Co", "heading keeps the name while re-reading");
    h.state.release();
    await h.wait(80);
    assert.ok(h.calls.includes("PATCH /api/companies/c1"));
    assert.equal(document.getElementById("company-evidence"), evidence, "same element, not remounted");
    assert.equal(evidence.open, true);
    assert.equal(history.open, true);
    assert.equal(pending.isConnected && pending.open, true, "R3: contact-area details survive the social re-read");
    assert.equal(document.activeElement, document.querySelector(".page-heading .quick-status select"), "R4: focus returns to the select");
  } finally { await h.cleanup(); }
});

test("section=contacts jumps once, opens 查找与更新社媒资料 and clears the parameter with replace", async () => {
  const h = await mountApp("http://localhost/?view=detail&company=c1&page=1&section=contacts");
  try {
    await h.wait(60);
    const discovery = [...document.querySelectorAll<HTMLDetailsElement>("#company-contacts details")].find((d) => d.querySelector("summary")!.textContent === "查找与更新社媒资料")!;
    assert.equal(discovery.open, true);
    assert.equal(document.activeElement, document.getElementById("company-contacts-title"));
    assert.equal(new URL(window.location.href).searchParams.has("section"), false);
    const lengthAfter = window.history.length;
    await h.wait(60);
    assert.equal(window.history.length, lengthAfter, "handled once, no further navigation");
  } finally { await h.cleanup(); }
});
