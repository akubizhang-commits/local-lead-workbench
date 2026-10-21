import { setupDOM } from "./support/dom.ts";
import "./support/registerTsx.ts";
import test from "node:test";
import assert from "node:assert/strict";

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });

async function mount(props: Record<string, unknown> = {}) {
  const env = await setupDOM();
  const { createElement } = await import("react");
  const { LoginPage } = await import("../src/LoginPage.tsx");
  const logins: unknown[] = [];
  await env.act(async () => env.root.render(createElement(LoginPage, { onLogin: (r: unknown) => logins.push(r), ...props })));
  return { ...env, logins };
}

function input(id: string) {
  return document.getElementById(id) as HTMLInputElement;
}

async function type(env: Awaited<ReturnType<typeof mount>>, id: string, value: string) {
  const element = input(id);
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!;
  await env.act(async () => {
    setter.call(element, value);
    element.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
}

async function submit(env: Awaited<ReturnType<typeof mount>>) {
  await env.act(async () => {
    document.querySelector("form")!.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  });
}

test("login page wording, labels and initial focus", async () => {
  const env = await mount();
  try {
    assert.equal(document.title, "登录 · 寻迹线索工作台");
    assert.equal(document.querySelector("h1")?.textContent, "登录寻迹线索工作台");
    const text = document.body.textContent || "";
    for (const phrase of ["请使用管理员为你创建的账号登录。", "用户名", "密码", "忘记密码请联系管理员重置。"])
      assert.ok(text.includes(phrase), phrase);
    assert.equal(document.querySelector('label[for="login-username"]')?.textContent, "用户名");
    assert.equal(input("login-username").getAttribute("autocomplete"), "username");
    assert.equal(input("login-password").type, "password");
    assert.equal(input("login-password").getAttribute("autocomplete"), "current-password");
    assert.ok(document.activeElement === input("login-username"), "focus on user name");
    assert.equal(document.querySelector("button")?.textContent, "登录");
  } finally {
    await env.cleanup();
  }
});

test("empty fields are refused without a request", async () => {
  const env = await mount();
  try {
    await submit(env);
    assert.equal(document.querySelector('[role="alert"]')?.textContent, "请输入用户名和密码");
  } finally {
    await env.cleanup();
  }
});

for (const [name, response, expected] of [
  ["401", () => json({ detail: "用户名或密码错误", code: "invalid_credentials" }, 401), "用户名或密码错误"],
  ["429", () => json({ detail: "x", code: "login_rate_limited" }, 429, { "Retry-After": "61" }), "登录尝试过多，请 2 分钟后再试"],
  ["503", () => json({ detail: "本地数据库正忙，请稍后重试。", code: "db_busy" }, 503), "本地数据库正忙，请稍后重试。"],
  ["offline", () => { throw new TypeError("fetch failed"); }, "无法连接服务，请检查网络后重试"],
] as const) {
  test(`failed login (${name}) shows the message, clears and focuses the password`, async () => {
    const env = await mount();
    try {
      let pending: (() => void) | undefined;
      globalThis.fetch = (async () => {
        await new Promise<void>((resolve) => { pending = resolve; });
        return (response as () => Response)();
      }) as typeof fetch;
      await type(env, "login-username", "alice");
      await type(env, "login-password", "wrong-password");
      await submit(env);
      const button = document.querySelector("button")!;
      assert.equal(button.textContent, "正在登录…");
      assert.ok(button.disabled);
      await env.act(async () => { pending!(); await new Promise((r) => setTimeout(r, 0)); });
      assert.equal(document.querySelector('[role="alert"]')?.textContent, expected);
      assert.equal(input("login-password").value, "");
      assert.ok(document.activeElement === input("login-password"), "focus on password");
      assert.equal(env.logins.length, 0);
    } finally {
      await env.cleanup();
    }
  });
}

test("successful login reports the user", async () => {
  const env = await mount();
  try {
    globalThis.fetch = (async () => json({ user: { username: "alice" }, token: "t" })) as typeof fetch;
    await type(env, "login-username", "alice");
    await type(env, "login-password", "correct-password");
    await submit(env);
    assert.deepEqual(env.logins, [{ user: { username: "alice" }, token: "t" }]);
  } finally {
    await env.cleanup();
  }
});

test("unavailable service disables the button and shows the detail", async () => {
  const env = await mount({ unavailable: "服务器磁盘空间不足，服务暂不可用，请联系管理员。" });
  try {
    assert.equal(document.querySelector('[role="alert"]')?.textContent, "服务器磁盘空间不足，服务暂不可用，请联系管理员。");
    assert.ok(document.querySelector("button")!.disabled);
  } finally {
    await env.cleanup();
  }
});
