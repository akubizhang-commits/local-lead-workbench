import { setupDOM } from "./support/dom.ts";
import "./support/registerTsx.ts";
import test from "node:test";
import assert from "node:assert/strict";

const NOTE = "服务器部署版暂不支持登录社媒账号读取，可使用公开资料读取。";
const flush = (act: (fn: () => Promise<void>) => Promise<void>) =>
  act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)); });

function connections(loginSupported: boolean) {
  return {
    reader: "http", instagram_reader: "browser", proxy_url: "", max_posts: 3, youtube_key_configured: false,
    dependencies: { browser: true, instaloader: false },
    sessions: { instagram: { state: "closed", message: "m" }, facebook: { state: "closed" }, x: { state: "closed" } },
    login_supported: loginSupported,
  };
}

async function renderConnections(loginSupported: boolean) {
  const env = await setupDOM();
  const { createElement } = await import("react");
  const { SocialConnections } = await import("../src/SocialConnections.tsx");
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const path = String(input);
    const body = path === "/api/session" ? { token: "t" } : path === "/api/social/connections" ? connections(loginSupported) : null;
    if (!body) assert.fail(`Unexpected request: ${path}`);
    return new Response(JSON.stringify(body), { status: 200 });
  }) as typeof fetch;
  await env.act(async () => env.root.render(createElement(SocialConnections)));
  await flush(env.act);
  return env;
}

const buttons = () => Array.from(document.querySelectorAll("button")).map((b) => b.textContent || "");

test("server mode: no login buttons, badge and notice", async () => {
  const env = await renderConnections(false);
  try {
    assert.ok(!buttons().some((t) => t.includes("登录浏览器") || t.includes("关闭并释放")));
    const badges = Array.from(document.querySelectorAll(".social-card .badge")).map((b) => b.textContent);
    assert.deepEqual(badges, ["不支持登录", "不支持登录", "不支持登录"]);
    const alerts = Array.from(document.querySelectorAll('[role="alert"]')).map((a) => a.textContent);
    assert.ok(alerts.includes(NOTE), JSON.stringify(alerts));
    const option = document.querySelector('option[value="instaloader"]') as HTMLOptionElement;
    assert.ok(option.disabled);
    assert.ok((document.body.textContent || "").includes("缺少可选依赖 Instaloader；请安装 instaloader 后启用此采集器。"));
  } finally {
    await env.cleanup();
  }
});

test("local mode keeps the login buttons", async () => {
  const env = await renderConnections(true);
  try {
    assert.ok(buttons().some((t) => t.includes("打开 Instagram 登录浏览器")));
    assert.ok(!(document.body.textContent || "").includes(NOTE));
    assert.ok(!(document.querySelector('option[value="instaloader"]') as HTMLOptionElement).disabled);
  } finally {
    await env.cleanup();
  }
});

async function renderDiscovery(mode: "server" | "local") {
  const env = await setupDOM();
  const { createElement } = await import("react");
  const { SocialDiscovery } = await import("../src/SocialDiscovery.tsx");
  const { SessionContext } = await import("../src/session.ts");
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const path = String(input);
    const body = path === "/api/session" ? { token: "t" } : path.startsWith("/api/social/runs") ? [] : null;
    if (!body) assert.fail(`Unexpected request: ${path}`);
    return new Response(JSON.stringify(body), { status: 200 });
  }) as typeof fetch;
  await env.act(async () => env.root.render(createElement(SessionContext.Provider, { value: { mode, auth: mode === "server" } },
    createElement(SocialDiscovery, { companyId: "c1" }))));
  await flush(env.act);
  return env;
}

test("server mode disables site search with the reason", async () => {
  const env = await renderDiscovery("server");
  try {
    const button = Array.from(document.querySelectorAll("button")).find((b) => b.textContent === "按企业名称站内搜索")!;
    assert.ok(button.disabled);
    assert.equal(button.getAttribute("aria-describedby"), "platform-search-reason");
    assert.equal(document.getElementById("platform-search-reason")?.textContent, NOTE);
  } finally {
    await env.cleanup();
  }
});

test("local mode keeps site search enabled", async () => {
  const env = await renderDiscovery("local");
  try {
    const button = Array.from(document.querySelectorAll("button")).find((b) => b.textContent === "按企业名称站内搜索")!;
    assert.ok(!button.disabled);
    assert.equal(document.getElementById("platform-search-reason"), null);
  } finally {
    await env.cleanup();
  }
});
