import { setupDOM } from "./support/dom.ts";
import "./support/registerTsx.ts";
import test from "node:test";
import assert from "node:assert/strict";

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>;

let pendingHandler: Handler | undefined;
let lastCalls: { url: string; method: string; token: string | null }[] = [];
function stub(handler: Handler) {
  pendingHandler = handler;
  lastCalls = [];
  return lastCalls;
}
function install() {
  const handler = pendingHandler!;
  const calls = lastCalls;
  globalThis.fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    calls.push({ url: String(input), method: init.method || "GET", token: new Headers(init.headers).get("X-Local-Token") });
    return handler(String(input), init);
  }) as typeof fetch;
}

async function mount(url = "http://localhost/") {
  const env = await setupDOM(url);
  install();
  const { createElement } = await import("react");
  const { AuthGate } = await import("../src/AuthGate.tsx");
  const { useSession } = await import("../src/session.ts");
  const api = await import("../src/api.ts");
  function Child() {
    const session = useSession();
    return createElement("div", { id: "app" },
      createElement("span", { id: "who" }, `${session.mode}:${session.username ?? ""}`),
      createElement("button", { id: "load", onClick: () => { api.api("/overview").catch(() => {}); } }, "load"),
      session.logout ? createElement("button", { id: "logout", onClick: session.logout }, "退出登录") : null);
  }
  await env.act(async () => { env.root.render(createElement(AuthGate, null, createElement(Child))); await tick(); });
  return env;
}

async function settle(env: Awaited<ReturnType<typeof mount>>) {
  await env.act(async () => { await tick(); await tick(); });
}

async function click(env: Awaited<ReturnType<typeof mount>>, selector: string) {
  await env.act(async () => { (document.querySelector(selector) as HTMLElement).click(); await tick(); await tick(); });
}

async function fill(env: Awaited<ReturnType<typeof mount>>, id: string, value: string) {
  const element = document.getElementById(id) as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!;
  await env.act(async () => { setter.call(element, value); element.dispatchEvent(new window.Event("input", { bubbles: true })); });
}

test("local mode renders the app without a login page", async () => {
  stub(() => json({ token: "local", mode: "local", auth: false }));
  const env = await mount();
  try {
    assert.ok(document.getElementById("app"));
    assert.equal(document.getElementById("who")?.textContent, "local:");
    assert.equal(document.getElementById("logout"), null);
    assert.equal(document.getElementById("login-username"), null);
  } finally {
    await env.cleanup();
  }
});

test("signed out: login page, then the app with the user; logout returns to the login page", async () => {
  let signedIn = false;
  const calls = stub((url) => {
    if (url === "/api/session") return json(signedIn ? { authenticated: true, mode: "server", auth: true, token: "t1", user: { username: "alice" } } : { authenticated: false, mode: "server", auth: true });
    if (url === "/api/auth/login") { signedIn = true; return json({ user: { username: "alice" }, token: "t1" }); }
    if (url === "/api/auth/logout") { signedIn = false; return new Response(null, { status: 204 }); }
    return json({ ok: true });
  });
  const env = await mount("http://localhost/?view=companies");
  try {
    assert.ok(document.getElementById("login-username"));
    assert.equal(document.getElementById("app"), null);
    await fill(env, "login-username", "alice");
    await fill(env, "login-password", "correct-password");
    await env.act(async () => { document.querySelector("form")!.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })); await tick(); });
    await settle(env);
    assert.equal(document.getElementById("who")?.textContent, "server:alice");
    assert.equal(document.title, "寻迹 · 线索工作台");
    assert.equal(window.location.search, "?view=companies");  // back to the original address
    assert.deepEqual(calls.filter((c) => c.url !== "/api/session").map((c) => c.url), ["/api/auth/login"]);
    await click(env, "#logout");
    await settle(env);
    assert.ok(document.getElementById("login-username"));
    assert.equal(document.querySelector('[role="status"]')?.textContent, "已退出登录");
    assert.equal(calls.find((c) => c.url === "/api/auth/logout")?.token, "t1");
  } finally {
    await env.cleanup();
  }
});

test("a 401 from any request returns to the login page with the expiry notice", async () => {
  stub((url) => url === "/api/session"
    ? json({ authenticated: true, mode: "server", auth: true, token: "t", user: { username: "alice" } })
    : json({ detail: "请先登录", code: "unauthorized" }, 401));
  const env = await mount();
  try {
    assert.ok(document.getElementById("app"));
    await click(env, "#load");
    await settle(env);
    assert.ok(document.getElementById("login-username"));
    assert.equal(document.querySelector('[role="status"]')?.textContent, "登录已失效，请重新登录");
  } finally {
    await env.cleanup();
  }
});

test("csrf_failed is retried once with a fresh token", async () => {
  let n = 0;
  const calls = stub((url) => {
    if (url === "/api/session") { n += 1; return json({ authenticated: true, mode: "server", auth: true, token: `t${n}`, user: { username: "alice" } }); }
    const call = calls.filter((c) => c.url === url).length;
    return call === 1 ? json({ detail: "页面已过期，请刷新后重试", code: "csrf_failed" }, 403) : json({ ok: true });
  });
  const env = await mount();
  try {
    await click(env, "#load");
    await settle(env);
    assert.deepEqual(calls.filter((c) => c.url === "/api/overview").map((c) => c.token), ["t2", "t3"]);
    assert.ok(document.getElementById("app"));
  } finally {
    await env.cleanup();
  }
});

test("maintenance shows the detail and a retry button, retries once after 2 s", async () => {
  let sessions = 0;
  stub(() => {
    sessions += 1;
    return sessions <= 2
      ? json({ detail: "正在恢复备份，请稍后重试。", code: "maintenance" }, 503, { "Retry-After": "2" })
      : json({ authenticated: false, mode: "server", auth: true });
  });
  const env = await mount();
  try {
    assert.equal(document.getElementById("login-username"), null);
    assert.ok((document.body.textContent || "").includes("正在恢复备份，请稍后重试。"));
    assert.ok([...document.querySelectorAll("button")].some((b) => b.textContent === "重试"));
    assert.equal(sessions, 1);
    await env.act(async () => { await new Promise((r) => setTimeout(r, 2100)); });
    assert.equal(sessions, 2);  // exactly one automatic retry
    await env.act(async () => { await new Promise((r) => setTimeout(r, 2100)); });
    assert.equal(sessions, 2);
    await click(env, "button");
    await settle(env);
    assert.equal(sessions, 3);
    assert.ok(document.getElementById("login-username"));
  } finally {
    await env.cleanup();
  }
});

test("unavailable service shows the detail on the login page", async () => {
  stub(() => json({ authenticated: false, mode: "server", auth: true, unavailable: { code: "disk_full", detail: "服务器磁盘空间不足，服务暂不可用，请联系管理员。" } }));
  const env = await mount();
  try {
    assert.equal(document.querySelector('[role="alert"]')?.textContent, "服务器磁盘空间不足，服务暂不可用，请联系管理员。");
    assert.ok((document.querySelector('button[type="submit"]') as HTMLButtonElement).disabled);
  } finally {
    await env.cleanup();
  }
});
