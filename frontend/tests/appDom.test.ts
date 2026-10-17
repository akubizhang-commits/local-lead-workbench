import "./support/registerTsx.ts";
import { setupDOM } from "./support/dom.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import type { Company } from "../src/types.ts";

const company: Company = {
  id: "a/b",
  name: "示例企业",
  address: "地址",
  phone: "",
  website: "",
  city: "上海",
  country: "中国",
  business_types: ["retail"],
  status: "candidate",
  notes: "",
  revision: 3,
  created_at: "2026-09-23",
  last_checked_at: null,
  contact_count: 0,
};
const account = {
  id: "account1",
  platform: "facebook",
  kind: "account",
  url: "https://example.com/account",
  label: "品牌账号",
  status: "candidate",
  scope: "unknown",
  revision: 2,
  linked_company_count: 1,
  last_observed_at: null,
  evidence: [],
};
const social = {
  accounts: [account],
  posts: [],
  excluded: [],
  ignored_links: [],
  outreach: [],
  do_not_contact: false,
  search_queries: [],
};

async function harness() {
  const env = await setupDOM(
    "http://localhost/?view=companies&q=服装&market_id=m1&sort=email&page=2",
  );
  const calls: { path: string; method: string; body: any }[] = [];
  let revision = 3;
  globalThis.fetch = async (input, init) => {
    const path = String(input),
      method = init?.method || "GET",
      body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path, method, body });
    let result: unknown;
    if (path === "/api/session") result = { token: "test-token" };
    else if (path === "/api/overview")
      result = {
        companies: 101,
        contacts: 0,
        tasks: 0,
        pending_reviews: 0,
        running_runs: 0,
        source_status: {
          maps: { available: false, message: "" },
          website: { available: true, message: "" },
        },
      };
    else if (
      path === "/api/tasks" ||
      path === "/api/schedules" ||
      path.startsWith("/api/social/runs?")
    )
      result = [];
    else if (path.startsWith("/api/company-page?"))
      result = {
        items: [{ ...company, revision }],
        total: 101,
        page: Number(
          new URL(path, "http://localhost").searchParams.get("page"),
        ),
        page_size: 50,
      };
    else if (path === "/api/companies/a%2Fb")
      result = {
        company,
        contacts: [],
        observations: [],
        changes: [],
        social_links: [],
      };
    else if (path === "/api/companies/a%2Fb/social") result = social;
    else if (path === "/api/companies/batch-status") {
      revision++;
      return new Response(JSON.stringify({ detail: "修订冲突" }), {
        status: 409,
      });
    } else assert.fail(`Unexpected request ${method} ${path}`);
    return new Response(JSON.stringify(result));
  };
  const { default: App } = await import("../src/App.tsx");
  const flush = () =>
    env.act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  const button = (text: string) => {
    const result = Array.from(document.querySelectorAll("button")).find(
      (b) => b.textContent === text,
    );
    assert.ok(result, text);
    return result;
  };
  const click = async (text: string) => {
    await env.act(async () => button(text).click());
    await flush();
  };
  await env.act(async () => env.root.render(createElement(App)));
  await flush();
  return { ...env, calls, flush, button, click };
}

test("real DOM: company/account navigation and browser back retain filters and page; pagination clears selection", async () => {
  const h = await harness();
  try {
    await h.act(async () =>
      document
        .querySelector<HTMLInputElement>('input[aria-label="选择 示例企业"]')!
        .click(),
    );
    assert.match(document.body.textContent || "", /已选 1 家/);
    await h.click("下一页");
    assert.equal(new URL(window.location.href).searchParams.get("page"), "3");
    assert.equal(document.querySelector('.batch-toolbar'), null);
    assert.ok([...document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].every(input=>!input.checked));
    await h.act(async () =>
      document
        .querySelector<HTMLButtonElement>('button[aria-label="查看 示例企业"]')!
        .click(),
    );
    await h.flush();
    assert.equal(
      new URL(window.location.href).searchParams.get("company"),
      "a/b",
    );
    await h.click("品牌账号");
    assert.equal(
      new URL(window.location.href).searchParams.get("account"),
      "account1",
    );
    assert.ok(document.body.textContent?.includes("账号简介"));
    await h.act(async () => {
      window.history.back();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    await h.flush();
    assert.equal(
      new URL(window.location.href).searchParams.get("account"),
      null,
    );
    assert.ok(document.body.textContent?.includes("联系人 / 账号"));
    await h.click("← 返回企业库");
    const params = new URL(window.location.href).searchParams;
    for (const [key, value] of Object.entries({
      view: "companies",
      q: "服装",
      market_id: "m1",
      sort: "email",
      page: "3",
    }))
      assert.equal(params.get(key), value);
    assert.equal(
      document.querySelector<HTMLInputElement>('input[aria-label="搜索企业"]')
        ?.value,
      "服装",
    );
    assert.ok(
      h.calls.some(
        (c) =>
          c.path.startsWith("/api/company-page?") && c.path.includes("page=3"),
      ),
    );
  } finally {
    await h.cleanup();
  }
});

test("real DOM: batch conflict is not retried, clears selected revisions and leaves a visible error", async () => {
  const h = await harness();
  try {
    await h.act(async () =>
      document
        .querySelector<HTMLInputElement>('input[aria-label="选择 示例企业"]')!
        .click(),
    );
    await h.click("应用到所选企业");
    const writes = h.calls.filter(
      (c) => c.path === "/api/companies/batch-status",
    );
    assert.equal(writes.length, 1);
    assert.deepEqual(writes[0].body, {
      companies: [{ id: "a/b", revision: 3 }],
      status: "candidate",
    });
    assert.match(document.body.textContent || "", /本次操作未重试/);
    assert.equal(document.querySelector('.batch-toolbar'), null);
    assert.ok([...document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].every(input=>!input.checked));
    assert.equal([...document.querySelectorAll("button")].some(button => button.textContent === "应用到所选企业"), false);
  } finally {
    await h.cleanup();
  }
});
