import "./support/registerTsx.ts";
import { setupDOM } from "./support/dom.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";

// P1-14 企业库窄屏（design §4.4，review NIT9/NIT11）：卡片、筛选抽屉计数、导出菜单、跳转链接。
const company = { id: "c1", name: "Long Name Supply", address: "", phone: "", website: "", city: "Los Angeles", country: "US",
  business_types: [], status: "candidate", notes: "", revision: 1, created_at: "2026-10-01", last_checked_at: null, contact_count: 2,
  best_tier_label: "通用邮箱", best_tier_rank: 5, gap: { reason: "没有具名联系人", next_step: "查看官网" },
  actual_city: "Vernon", actual_city_basis: "address", nearby: true, market_segment: "new" };
async function mount({ narrow = true, slow = false, matchMedia = true } = {}) {
  const h = await setupDOM("http://localhost/?view=companies");
  const proto = (window as any).HTMLDialogElement.prototype;
  proto.showModal ??= function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
  if (matchMedia) (window as any).matchMedia = (q: string) => ({ matches: narrow && q.includes("max-width"), addEventListener() {}, removeEventListener() {} });
  const gates: (() => void)[] = [];
  let total = 61;
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url === "/api/session") return new Response(JSON.stringify({ token: "t" }));
    if (url.startsWith("/api/company-page")) {
      if (slow) await new Promise<void>((r) => gates.push(r));
      return new Response(JSON.stringify({ items: total ? [company] : [], total, page: 1, page_size: 50, excluded_total: 0 }));
    }
    return new Response("[]");
  };
  const { CompaniesPage } = await import("../src/pages/CompaniesPage.tsx");
  const { defaultFilters } = await import("../src/navigation.ts");
  const { useState } = await import("react");
  const updates: unknown[] = [];
  let setter: (f: any) => void = () => {};
  function Wrap() {
    const [filters, setFilters] = useState(defaultFilters());
    setter = setFilters;
    return createElement(CompaniesPage, { version: 0, filters, page: 1, busy: false, act: async () => {}, refresh: () => {},
      updateFilters: (p: object) => { updates.push(p); setFilters((f: object) => ({ ...f, ...p })); }, setPage: () => {}, openCompany: () => {}, quickStatus: () => {} } as never);
  }
  const wait = () => h.act(async () => { await new Promise((r) => setTimeout(r, 30)); });
  await h.act(async () => h.root.render(createElement(Wrap)));
  await wait();
  return { ...h, wait, updates, gates, setTotal: (n: number) => { total = n; }, setFilters: (f: any) => h.act(async () => setter(f)) };
}

test("narrow renders cards, not the table; card buttons expose their text, not an aria-label", async () => {
  const h = await mount();
  try {
    assert.equal(document.querySelector("table"), null);
    const card = document.querySelector<HTMLButtonElement>(".company-card")!;
    assert.equal(card.hasAttribute("aria-label"), false);
    assert.equal(card.title, "Long Name Supply");
    assert.match(card.textContent!, /^查看 Long Name Supply/);
    assert.match(card.textContent!, /Vernon \/ US/);
    assert.match(card.textContent!, /邻近城市/);
    assert.match(card.textContent!, /通用邮箱 联系入口 2 个/);
    assert.match(card.textContent!, /缺口：没有具名联系人/);
    assert.equal(card.querySelectorAll("div,p").length, 0);
    assert.equal(document.querySelector('input[type="checkbox"]'), null, "no batch selection on narrow screens (D4)");
  } finally { await h.cleanup(); }
});

test("filter count follows NIT11: q and sort excluded, category and excluded count once", async () => {
  const { activeFilterCount } = await import("../src/pages/CompaniesPage.tsx");
  const { defaultFilters } = await import("../src/navigation.ts");
  assert.equal(activeFilterCount({ ...defaultFilters(), q: "x", sort: "tier" }), 0);
  assert.equal(activeFilterCount({ ...defaultFilters(), status: "verified", market_id: "m", category: ["a", "b"], excluded: "only", nearby: "hide" }), 5);
  const h = await mount();
  try {
    await h.setFilters((f: object) => ({ ...f, status: "verified", category: ["a", "b"] }));
    await h.wait();
    assert.ok([...document.querySelectorAll("button")].some((b) => b.textContent === "筛选（2）"));
  } finally { await h.cleanup(); }
});

test("drawer count is aria-live and keeps the previous total while loading", async () => {
  const h = await mount({ slow: true });
  try {
    h.gates.splice(0).forEach((g) => g());
    await h.wait();
    await h.act(async () => ([...document.querySelectorAll("button")].find((b) => b.textContent === "筛选")!).click());
    const count = document.querySelector(".sheet-count")!;
    assert.equal(count.getAttribute("aria-live"), "polite");
    assert.equal(count.textContent, "当前 61 家");
    h.setTotal(12);
    const select = document.querySelector<HTMLSelectElement>('dialog select[aria-label="客户匹配状态筛选"]')!;
    await h.act(async () => { select.value = "verified"; select.dispatchEvent(new window.Event("change", { bubbles: true })); });
    await h.wait();
    assert.equal(document.querySelector(".sheet-count")!.textContent, "当前 61 家", "old value while loading");
    assert.ok(document.querySelector("dialog"), "drawer stays mounted while the list reloads");
    h.gates.splice(0).forEach((g) => g());
    await h.wait();
    assert.equal(document.querySelector(".sheet-count")!.textContent, "当前 12 家");
    assert.ok([...document.querySelectorAll("dialog footer button")].some((b) => b.textContent === "查看 12 家"));
  } finally { await h.cleanup(); }
});

test("export menu closes on Escape and returns focus to its summary", async () => {
  const h = await mount();
  try {
    const menu = document.querySelector<HTMLDetailsElement>("details.export-menu")!;
    menu.open = true;
    const item = menu.querySelector<HTMLButtonElement>("button")!;
    item.focus();
    await h.act(async () => item.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    assert.equal(menu.open, false);
    assert.equal(document.activeElement, menu.querySelector("summary"));
  } finally { await h.cleanup(); }
});

for (const narrow of [true, false]) {
  test(`skip link focuses #company-list (${narrow ? "narrow" : "desktop"})`, async () => {
    const h = await mount({ narrow });
    try {
      const list = document.getElementById("company-list")!;
      assert.equal(list.getAttribute("tabindex"), "-1");
      assert.equal(list.tagName, narrow ? "UL" : "SECTION");
      await h.act(async () => ([...document.querySelectorAll("a")].find((a) => a.textContent === "跳到企业列表")!).click());
      assert.equal(document.activeElement, list);
      assert.equal(document.querySelectorAll("#company-list").length, 1);
    } finally { await h.cleanup(); }
  });
}

test("without matchMedia the desktop table renders", async () => {
  const h = await mount({ matchMedia: false });
  try {
    assert.ok(document.querySelector("table"));
    assert.equal(document.querySelector(".company-card"), null);
  } finally { await h.cleanup(); }
});
