import { setupDOM } from "./support/dom.ts";
import "./support/registerTsx.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

test("run report preserves mixed outcomes instead of treating completion as success", async () => {
  const { SocialRunReport } = await import("../src/SocialDiscovery.tsx");
  const html = renderToStaticMarkup(
    createElement(SocialRunReport, {
      run: {
        id: "r1",
        status: "completed",
        created_at: "2026-09-22",
        summary: {
          total: 4,
          pending: 0,
          running: 0,
          succeeded: 1,
          no_results: 1,
          blocked: 1,
          failed: 1,
          cancelled: 0,
          new_links: 2,
        },
        items: ["succeeded", "no_results", "blocked", "failed"].map(
          (state, i) => ({
            id: String(i),
            company_id: "c1",
            company_name: "示例企业",
            stage: "search",
            target: "instagram",
            state,
            result: { message: "原因 " + state },
          }),
        ),
      },
    }),
  );
  for (const text of [
    "已结束",
    "成功",
    "未找到结果",
    "访问受阻",
    "失败",
    "新增候选链接",
    "示例企业",
    "原因 blocked",
  ])
    assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /<details[^>]*\bopen/);
});

test("page preview retains access failure and treats extracted contacts as unconfirmed", async () => {
  const { SocialPagePreview } = await import("../src/SocialPanel.tsx");
  const html = renderToStaticMarkup(
    createElement(SocialPagePreview, {
      page: {
        entity_id: "e1",
        status: "blocked",
        message: "需要登录",
        title: "示例主页",
        description: "公开简介",
        fields: [
          {
            key: "email",
            value: "hello@example.com",
            evidence: "Email hello@example.com",
          },
        ],
        observed_at: "2026-09-22",
        source_url: "https://example.com/profile",
      },
    }),
  );
  for (const text of [
    "访问受阻",
    "需要登录",
    "示例主页",
    "公开简介",
    "hello@example.com",
    "归属待确认",
    "预览",
    "https://example.com/profile",
  ])
    assert.ok(html.includes(text), text);
  assert.ok(!html.includes("已验证联系人"));
});

test("unknown statuses and absent metrics are not presented as successful zero results", async () => {
  const { SocialRunReport } = await import("../src/SocialDiscovery.tsx");
  const html = renderToStaticMarkup(
    createElement(SocialRunReport, {
      run: {
        id: "r2",
        status: "new_server_state",
        created_at: "2026-09-22",
        summary: {},
        items: [],
      },
    }),
  );
  assert.ok(html.includes("未知状态（new_server_state）"));
  assert.ok(html.includes("<dd>—</dd>"));
  assert.ok(html.includes("尚无处理明细"));
});

test("preview escapes source content and rejects executable source URLs", async () => {
  const { SocialPagePreview } = await import("../src/SocialPanel.tsx");
  const html = renderToStaticMarkup(
    createElement(SocialPagePreview, {
      page: {
        entity_id: "x",
        status: "succeeded",
        message: "已读取",
        title: "<script>alert(1)</script>",
        description: "简介",
        fields: [],
        observed_at: "2026-09-22",
        source_url: "javascript:alert(1)",
      },
    }),
  );
  assert.ok(!html.includes("<script>"));
  assert.ok(!html.includes('href="javascript:'));
  assert.ok(html.includes("未采集完整帖子"));
});

// Real-DOM checks run by default; SOCIAL_TEST_JSDOM may override the pinned dependency.
test("real DOM: single-company run, cancel, retry and refresh use the API contract", async () => {
  const { dom, root, act, cleanup } = await setupDOM();
  const { SocialDiscovery } = await import("../src/SocialDiscovery.tsx");
  const { resetSession } = await import("../src/api.ts");
  resetSession();
  const requests: { path: string; method: string; body: unknown }[] = [];
  let runs: any[] = [];
  let changed = 0;
  const fixture = (id: string, status: string) => ({
    id,
    status,
    created_at: "2026-09-22",
    summary: { total: 1, pending: status === "queued" ? 1 : 0, new_links: 0 },
    items: [
      {
        id: "i1",
        company_id: "a/b",
        company_name: "企业 A",
        stage: "search",
        target: "instagram",
        state: status,
        result: { message: "处理说明" },
      },
    ],
  });
  globalThis.fetch = async (input, init) => {
    const path = String(input),
      method = init?.method || "GET",
      body = init?.body ? JSON.parse(String(init.body)) : undefined;
    requests.push({ path, method, body });
    let result: unknown;
    if (path === "/api/session") result = { token: "test-token" };
    else if (path === "/api/social/runs?company_id=a%2Fb") result = runs;
    else if (path === "/api/social/runs" && method === "POST") {
      runs = [fixture("r1", "queued")];
      result = runs[0];
    } else if (path === "/api/social/runs/r1/cancel") {
      runs = [fixture("r1", "cancelled")];
      result = { ok: true };
    } else if (path === "/api/social/runs/r1/retry") {
      runs = [fixture("r2", "queued"), ...runs];
      result = runs[0];
    } else result = runs.find((run) => path === `/api/social/runs/${run.id}`);
    assert.ok(result, `unexpected request ${method} ${path}`);
    return new Response(JSON.stringify(result), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  const flush = () =>
    act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 15));
    });
  const click = async (text: string) => {
    const button = Array.from(document.querySelectorAll("button")).find(
      (b) => b.textContent === text,
    );
    assert.ok(button, `missing button ${text}`);
    assert.equal(button.disabled, false);
    await act(async () => {
      button.click();
    });
    await flush();
  };
  try {
    await act(async () => {
      root.render(
        createElement(SocialDiscovery, {
          companyId: "a/b",
          onChange: () => changed++,
        }),
      );
    });
    await flush();
    await click("开始单企发现");
    assert.deepEqual(
      requests.find((r) => r.path === "/api/social/runs" && r.method === "POST")
        ?.body,
      { company_ids: ["a/b"] },
    );
    assert.ok(document.body.textContent?.includes("企业 A"));
    assert.ok(changed > 0);
    await click("取消此运行");
    assert.ok(document.body.textContent?.includes("已取消"));
    const prior = changed;
    await click("新建重试运行");
    assert.ok(
      requests.some(
        (r) => r.path === "/api/social/runs/r1/retry" && r.method === "POST",
      ),
    );
    assert.ok(document.body.textContent?.includes("r2"));
    assert.ok(document.querySelector('option[value="r1"]'));
    assert.ok(changed > prior);
    const unchanged = changed;
    await click("刷新报告");
    assert.equal(
      changed,
      unchanged,
      "unchanged report must not trigger refresh loops",
    );
    assert.ok(!requests.some((r) => r.path.includes("outreach")));
  } finally {
    await cleanup();
  }
});

test("real DOM: settings save, blocked probe and 20-company pilot preserve their meanings", async () => {
  const { dom, root, act, cleanup } = await setupDOM();
  const { SocialSettings } = await import("../src/SocialSettings.tsx");
  const { resetSession } = await import("../src/api.ts");
  resetSession();
  const requests: { path: string; method: string; body: unknown }[] = [];
  let settings = {
    searxng_url: "http://127.0.0.1:8080",
    configured: true,
    platforms: ["instagram"],
    message: "需要 JSON 响应",
  };
  const connections = {
    reader: "browser",
    instagram_reader: "browser",
    proxy_url: "",
    max_posts: 3,
    youtube_key_configured: false,
    dependencies: { browser: true, instaloader: false },
    sessions: { instagram: { state: "closed" }, facebook: { state: "closed" } },
  };
  const run = {
    id: "pilot-1",
    status: "completed",
    created_at: "2026-09-22",
    summary: { total: 20, no_results: 20, new_links: 0 },
    items: [],
  };
  globalThis.fetch = async (input, init) => {
    const path = String(input),
      method = init?.method || "GET",
      body = init?.body ? JSON.parse(String(init.body)) : undefined;
    requests.push({ path, method, body });
    let result: unknown;
    if (path === "/api/session") result = { token: "test-token" };
    else if (path === "/api/social/settings") {
      if (method === "PUT") settings = { ...settings, ...body };
      result = settings;
    } else if (path === "/api/social/connections" && method === "GET")
      result = connections;
    else if (
      method === "POST" &&
      /^\/api\/social\/browser\/(instagram|facebook)\/(open|close)$/.test(path)
    ) {
      const platform = path.split("/")[4] as "instagram" | "facebook";
      connections.sessions[platform].state = path.endsWith("/open")
        ? "open"
        : "closed";
      result = {
        status: connections.sessions[platform].state,
        message: "浏览器会话状态已更新",
      };
    } else if (path === "/api/social/probe")
      result = { status: "blocked", message: "JSON 响应被拒绝（403）" };
    else if (path === "/api/social/runs" && method === "GET") result = [];
    else if (path === "/api/social/runs" && method === "POST") result = run;
    else if (path === "/api/social/runs/pilot-1") result = run;
    assert.ok(result, `unexpected request ${method} ${path}`);
    return new Response(JSON.stringify(result), { status: 200 });
  };
  const flush = () =>
    act(async () => {
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
    assert.equal(button(text).disabled, false);
    await act(async () => button(text).click());
    await flush();
  };
  const taskRequests = () =>
    requests.filter(
      (r) => r.method === "POST" && r.path.startsWith("/api/social/runs"),
    );
  const assertTaskBlocked = async (text: string) => {
    assert.equal(
      button(text).disabled,
      true,
      `${text} must be disabled while a login session is open`,
    );
    const count = taskRequests().length;
    await act(async () => button(text).click());
    await flush();
    assert.equal(
      taskRequests().length,
      count,
      "disabled task buttons must not send requests",
    );
  };
  try {
    await act(async () => {
      root.render(createElement(SocialSettings));
    });
    await flush();
    await click("保存配置");
    assert.deepEqual(requests.find((r) => r.method === "PUT")?.body, {
      searxng_url: "http://127.0.0.1:8080",
    });
    await click("测试连接");
    assert.ok(document.body.textContent?.includes("连接测试：访问受阻"));
    assert.ok(document.body.textContent?.includes("JSON 响应被拒绝（403）"));
    assert.ok(
      requests.some(
        (r) => r.path === "/api/social/connections" && r.method === "GET",
      ),
    );
    assert.equal(
      button("启动 20 家试点").disabled,
      false,
      "closed sessions permit a pilot",
    );
    await click("打开 Instagram 登录浏览器");
    await assertTaskBlocked("启动 20 家试点");
    await click("打开 Facebook 登录浏览器");
    await click("关闭并释放 Instagram 会话");
    await assertTaskBlocked("启动 20 家试点");
    await click("关闭并释放 Facebook 会话");
    assert.equal(
      button("启动 20 家试点").disabled,
      false,
      "both sessions must be closed before starting",
    );
    await click("启动 20 家试点");
    assert.deepEqual(
      requests.find((r) => r.path === "/api/social/runs" && r.method === "POST")
        ?.body,
      { pilot: true },
    );
    assert.ok(document.body.textContent?.includes("未找到结果"));
    assert.ok(document.body.textContent?.includes("pilot-1"));
    assert.equal(button("新建重试运行").disabled, false);
    await click("打开 Instagram 登录浏览器");
    await assertTaskBlocked("启动 20 家试点");
    await assertTaskBlocked("新建重试运行");
    await click("关闭并释放 Instagram 会话");
    assert.equal(button("启动 20 家试点").disabled, false);
    assert.equal(
      button("新建重试运行").disabled,
      false,
      "closing the session also restores retry",
    );
  } finally {
    await cleanup();
  }
});

test("backend public-page adapter statuses and read stage are shown in Chinese", async () => {
  const { socialStatus, SocialRunReport } = await import(
    "../src/SocialDiscovery.tsx"
  );
  assert.equal(socialStatus("preview_read"), "已读取公开预览");
  assert.equal(socialStatus("unreadable"), "未读到有效预览");
  assert.equal(socialStatus("auth_required"), "需要登录");
  assert.equal(socialStatus("challenge"), "需要平台验证");
  const html = renderToStaticMarkup(
    createElement(SocialRunReport, {
      run: {
        id: "r3",
        status: "partial",
        created_at: "2026-09-22",
        summary: { blocked: 1 },
        items: [
          {
            id: "i3",
            company_id: "c",
            company_name: "测试企业",
            stage: "read",
            target: "https://example.com",
            state: "blocked",
            result: { status: "auth_required", reason: "source_paused" },
          },
        ],
      },
    }),
  );
  for (const text of ["公开页面预览", "需要登录", "同平台读取已暂缓"])
    assert.ok(html.includes(text), text);
});

test("account summary separates platform, name, identifier and discovery source", async () => {
  const { SocialAccountSummary } = await import("../src/SocialPanel.tsx");
  const entry = {
    id: "e",
    platform: "facebook",
    kind: "account",
    url: "https://www.facebook.com/profile.php?id=123",
    label: "Page 123",
    status: "candidate",
    scope: "unknown",
    revision: 1,
    linked_company_count: 1,
    last_observed_at: null,
    evidence: [
      {
        id: "v",
        original_url: "",
        source_url: "https://www.facebook.com/search/pages/?q=Vocal",
        evidence:
          "平台站内搜索；查询：Vocal；结果：Vocal Apparel。名称匹配候选，城市、电话及归属待核实。",
        method: "platform_search",
        observed_at: "2026-09-23",
        reason: "",
      },
    ],
  };
  const html = renderToStaticMarkup(
    createElement(SocialAccountSummary, { entry, pages: [] }),
  );
  for (const text of [
    "数据来源：Facebook",
    "Vocal Apparel",
    "账号编号",
    "123",
    "Facebook 站内搜索",
    "尚未采集账号简介",
  ])
    assert.ok(html.includes(text), text + " " + html);
  assert.doesNotMatch(html, /Page 123/);
});

test("saved posts render inline with platform and capture time and escaped text", async () => {
  const { SocialPosts } = await import("../src/SocialPanel.tsx");
  const html = renderToStaticMarkup(
    createElement(SocialPosts, {
      platform: "Instagram",
      page: {
        entity_id: "e",
        status: "preview_read",
        message: "",
        title: "Brand",
        description: "",
        fields: [],
        source_url: "https://instagram.com/brand/",
        observed_at: "2026-09-23",
        posts: [
          {
            url: "https://instagram.com/p/abc/",
            text: "New collection <script>x</script>",
            observed_at: "2026-09-20",
          },
        ],
      },
    }),
  );
  assert.match(html, /Instagram/);
  assert.match(html, /New collection &lt;script&gt;/);
  assert.match(html, /采集于/);
  assert.doesNotMatch(html, /<details/);
});

test("account list is concise and legacy is not called a discovery", async () => {
  const { SocialAccountList } = await import("../src/SocialPanel.tsx");
  const entry = {
    id: "a",
    platform: "facebook",
    kind: "account",
    url: "https://facebook.com/brand/",
    label: "brand",
    status: "candidate",
    scope: "unknown",
    revision: 1,
    linked_company_count: 1,
    last_observed_at: null,
    evidence: [],
  };
  const html = renderToStaticMarkup(
    createElement(SocialAccountList, {
      entries: [entry],
      pages: [],
      onOpen: () => {},
    }),
  );
  assert.match(html, /Facebook/);
  assert.match(html, /brand/);
  assert.doesNotMatch(html, /账号名称尚未采集/);
  assert.doesNotMatch(html, /确认归属/);
});

test("website discovery is explicit while legacy dates are entry dates", async () => {
  const { SocialAccountSummary } = await import("../src/SocialPanel.tsx");
  const entry = {
    id: "a",
    platform: "facebook",
    kind: "account",
    url: "https://facebook.com/brand/",
    label: "brand",
    status: "candidate",
    scope: "unknown",
    revision: 1,
    linked_company_count: 1,
    last_observed_at: null,
    evidence: [
      {
        id: "s",
        original_url: "",
        source_url: "",
        evidence: "",
        method: "legacy",
        observed_at: "2026-09-22",
        reason: "",
      },
    ],
  };
  const legacy = renderToStaticMarkup(
    createElement(SocialAccountSummary, { entry, pages: [] }),
  );
  assert.match(legacy, /原始来源未记录/);
  assert.match(legacy, /入库时间/);
  assert.doesNotMatch(legacy, /最近发现/);
  const current = {
    ...entry,
    evidence: [
      ...entry.evidence,
      {
        ...entry.evidence[0],
        id: "w",
        method: "website",
        source_url: "https://brand.example",
      },
    ],
  };
  const html = renderToStaticMarkup(
    createElement(SocialAccountSummary, { entry: current, pages: [] }),
  );
  assert.match(html, /企业官网/);
  assert.doesNotMatch(html, /原始来源未记录/);
});

test("account list has four columns and failed attempts do not advance update time", async () => {
  const { SocialAccountList } = await import("../src/SocialPanel.tsx");
  const entry = {
    id: "a",
    platform: "facebook",
    kind: "account",
    url: "https://facebook.com/brand/",
    label: "brand",
    status: "candidate",
    scope: "unknown",
    revision: 1,
    linked_company_count: 1,
    last_observed_at: null,
    evidence: [],
  };
  const page = {
    entity_id: "a",
    status: "failed",
    message: "timeout",
    title: "Brand",
    description: "",
    fields: [],
    source_url: entry.url,
    observed_at: "2026-09-23",
    content_observed_at: "2026-09-20",
  };
  const html = renderToStaticMarkup(
    createElement(SocialAccountList, {
      entries: [entry],
      pages: [page],
      onOpen: () => {},
    }),
  );
  assert.equal((html.match(/<th(?:>| )/g) || []).length, 4);
  for (const label of ["联系人 / 账号", "联系渠道", "企业关系", "更新时间"])
    assert.ok(html.includes(label));
  for (const label of ["已采集联系方式", "采集状态", "条已保存", "2026/09/23"])
    assert.ok(!html.includes(label));
  assert.match(html, /2026\/09\/20/);
  const missing = renderToStaticMarkup(
    createElement(SocialAccountList, {
      entries: [entry],
      pages: [{ ...page, content_observed_at: undefined }],
      onOpen: () => {},
    }),
  );
  assert.match(missing, /暂无更新记录/);
});

test("account details distinguish retained field and text sources from current observations and folded history", async () => {
  const { SocialAccountSummary, SocialPagePreview } = await import(
    "../src/SocialPanel.tsx"
  );
  const entry = {
    id: "a",
    platform: "facebook",
    kind: "account",
    url: "https://facebook.com/brand/",
    label: "brand",
    status: "candidate",
    scope: "unknown",
    revision: 1,
    linked_company_count: 1,
    last_observed_at: null,
    evidence: [],
  };
  const page = {
    entity_id: "a",
    status: "preview_read",
    message: "",
    title: "Saved Brand",
    description: "Saved description",
    observed_at: "2026-09-23",
    source_url: entry.url,
    fields: [
      {
        key: "email",
        value: "kept@example.com",
        evidence: "saved email",
        source_url: "https://example.com/old",
        observed_at: "2026-09-20",
        retained: true,
      },
      {
        key: "phone",
        value: "12345",
        evidence: "new phone",
        observed_at: "2026-09-23",
        retained: false,
      },
    ],
    content_sources: {
      title: {
        observed_at: "2026-09-19",
        source_url: "https://example.com/title",
        retained: true,
      },
      description: {
        observed_at: "2026-09-18",
        source_url: "https://example.com/description",
        retained: true,
      },
    },
    field_history: [
      {
        key: "email",
        value: "previous@example.com",
        evidence: "older email",
        source_url: "https://example.com/history",
        observed_at: "2026-09-17",
      },
    ],
  };
  const summary = renderToStaticMarkup(
    createElement(SocialAccountSummary, { entry, pages: [page] }),
  );
  for (const value of [
    "2026/09/20",
    "2026/09/19",
    "2026/09/18",
    "保留旧资料",
    "https://example.com/title",
    "https://example.com/description",
  ])
    assert.ok(summary.includes(value), value);
  const preview = renderToStaticMarkup(
    createElement(SocialPagePreview, { page }),
  );
  for (const value of [
    "2026/09/20",
    "2026/09/23",
    "previous@example.com",
    "2026/09/17",
    "https://example.com/history",
    "不等同于本次新值",
  ])
    assert.ok(preview.includes(value), value);
  assert.match(preview, /<details[^>]*><summary>历史字段值/);
  assert.doesNotMatch(preview, /<details[^>]*\bopen/);
});

test("account fields remain visible without title or description and cannot borrow another account source", async () => {
  const { SocialAccountSummary } = await import("../src/SocialPanel.tsx");
  const entry = {
    id: "a",
    platform: "facebook",
    kind: "account",
    url: "https://example.com/a",
    label: "Account A",
    status: "candidate",
    scope: "unknown",
    revision: 1,
    linked_company_count: 1,
    last_observed_at: null,
    evidence: [],
  };
  const page = {
    entity_id: "a",
    status: "preview_read",
    message: "",
    title: "",
    description: "",
    fields: [
      {
        key: "email",
        value: "a@example.com",
        evidence: "Email A",
        observed_at: "2026-09-18",
        retained: true,
      },
    ],
    observed_at: "2026-09-23",
    source_url: entry.url,
  };
  const html = renderToStaticMarkup(
    createElement(SocialAccountSummary, {
      entry,
      pages: [
        {
          ...page,
          entity_id: "b",
          title: "Other Account",
          description: "Other description",
        },
        page,
      ],
    }),
  );
  assert.match(html, /a@example.com/);
  assert.match(html, /2026\/09\/18/);
  assert.match(html, /保留旧资料/);
  assert.doesNotMatch(html, /Other Account|Other description/);
});
