import { setupDOM } from "./support/dom.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";

test("real DOM: account decision refreshes revision and manual follow-up stays a saved draft", async () => {
  const h = await setupDOM();
  const { SocialPanel } = await import("../src/SocialPanel.tsx");
  let account = {
    id: "a",
    platform: "facebook",
    kind: "account",
    url: "https://example.com/a",
    label: "Brand",
    status: "candidate",
    scope: "unknown",
    revision: 2,
    linked_company_count: 1,
    last_observed_at: null,
    evidence: [],
  };
  const calls: { path: string; method: string; body: any }[] = [];
  globalThis.fetch = async (input, init) => {
    const path = String(input),
      method = init?.method || "GET",
      body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path, method, body });
    let result: unknown;
    if (path === "/api/session") result = { token: "test-token" };
    else if (path === "/api/companies/c%2Fd/social")
      result = {
        accounts: [account],
        posts: [],
        excluded: [],
        ignored_links: [],
        outreach: [],
        do_not_contact: false,
        search_queries: [],
        pages: [],
      };
    else if (path === "/api/companies/c%2Fd/social/a/decision") {
      account = {
        ...account,
        status: body.status,
        scope: body.scope,
        revision: 3,
      };
      result = { ok: true };
    } else if (path === "/api/companies/c%2Fd/social/outreach")
      result = { ok: true };
    else assert.fail(`Unexpected request ${method} ${path}`);
    return new Response(JSON.stringify(result));
  };
  const flush = () =>
    h.act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 15));
    });
  const button = (text: string) => {
    const found = Array.from(document.querySelectorAll("button")).find(
      (b) => b.textContent === text,
    );
    assert.ok(found, text);
    return found;
  };
  const click = async (text: string) => {
    await h.act(async () => button(text).click());
    await flush();
  };
  try {
    await h.act(async () =>
      h.root.render(
        createElement(SocialPanel, {
          companyId: "c/d",
          version: 0,
          accountId: "a",
        }),
      ),
    );
    await flush();
    assert.equal(button("确认归属").disabled, true);
    const scope = document.querySelector<HTMLSelectElement>(
      ".social-decision select",
    )!;
    await h.act(async () => {
      scope.value = "brand";
      scope.dispatchEvent(new h.dom.window.Event("change", { bubbles: true }));
    });
    await click("确认归属");
    assert.deepEqual(calls.find((c) => c.path.endsWith("/decision"))?.body, {
      status: "confirmed",
      scope: "brand",
      revision: 2,
    });
    assert.ok(document.body.textContent?.includes("人工已确认"));
    await click("记录跟进 / 草稿");
    const form = document.querySelector<HTMLFormElement>(
      "#social-followup form",
    )!;
    assert.equal(form.querySelector("select")!.value, "a");
    const note = form.querySelector("textarea")!;
    await h.act(async () => {
      Object.getOwnPropertyDescriptor(
        h.dom.window.HTMLTextAreaElement.prototype,
        "value",
      )!.set!.call(note, "待人工发送的草稿");
      note.dispatchEvent(new h.dom.window.Event("input", { bubbles: true }));
    });
    assert.equal(button("保存记录").disabled, false);
    await click("确认归属");
    assert.equal(
      document.querySelector<HTMLTextAreaElement>("#social-followup textarea")!
        .value,
      "待人工发送的草稿",
      "refresh after another decision must preserve an unsaved follow-up draft",
    );
    await click("保存记录");
    assert.deepEqual(calls.find((c) => c.path.endsWith("/outreach"))?.body, {
      entity_id: "a",
      state: "draft",
      note: "待人工发送的草稿",
    });
    assert.ok(document.body.textContent?.includes("草稿已保存，尚未发送"));
    await click("不采用");
    assert.deepEqual(
      calls.filter((c) => c.path.endsWith("/decision"))[2].body,
      { status: "excluded", scope: "brand", revision: 3 },
    );
  } finally {
    await h.cleanup();
  }
});
