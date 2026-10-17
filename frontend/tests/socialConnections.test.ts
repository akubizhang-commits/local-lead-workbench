import { setupDOM } from "./support/dom.ts";
import "./support/registerTsx.ts";
import test from "node:test";
import assert from "node:assert/strict";

test("proxy accepts only the supported protocols, exact loopback hosts and port boundaries", async () => {
  const { validSocialProxy } = await import("../src/SocialConnections.tsx");
  for (const protocol of ["http", "socks5"])
    for (const host of ["localhost", "127.0.0.1", "[::1]"])
      for (const port of [1024, 7890, 65535]) {
        const url = `${protocol}://${host}:${port}`;
        assert.equal(validSocialProxy(url), true, url);
      }
  for (const value of [
    "",
    "   ",
    " http://localhost:1024 ",
    "socks5://[::1]:65535/",
  ])
    assert.equal(validSocialProxy(value), true, value);
});

test("proxy rejects unsupported protocols, alternate hosts, credentials and invalid ports", async () => {
  const { validSocialProxy } = await import("../src/SocialConnections.tsx");
  const invalid = [
    "https://localhost:1024",
    "socks4://localhost:1024",
    "socks4a://localhost:1024",
    "socks5h://localhost:1024",
    "http://127.0.0.2:1024",
    "http://127.1.2.3:1024",
    "http://127.1:1024",
    "http://2130706433:1024",
    "http://127.000.000.001:1024",
    "http://localhost.:1024",
    "http://localhost.example.com:1024",
    "http://[0:0:0:0:0:0:0:1]:1024",
    "http://[::ffff:127.0.0.1]:1024",
    "http://example.com:1024",
    "http://localhost",
    "http://localhost:0",
    "http://localhost:80",
    "http://localhost:1023",
    "http://localhost:65536",
    "http://localhost:-1024",
    "http://localhost:1e4",
    "http://localhost:1024.5",
    "http://user:secret@localhost:1024",
    "http://@localhost:1024",
    "http://localhost:1024/path",
    "http://localhost:1024?query=1",
    "http://localhost:1024#fragment",
  ];
  for (const value of invalid)
    assert.equal(validSocialProxy(value), false, value);
});

test("post limit accepts only integers from one through five", async () => {
  const { validSocialMaxPosts } = await import("../src/SocialConnections.tsx");
  for (const value of ["1", "2", "3", "4", "5"])
    assert.equal(validSocialMaxPosts(value), true, value);
  for (const value of [
    "",
    "0",
    "6",
    "100",
    "-1",
    "1.5",
    "NaN",
    "Infinity",
    "1e0",
    " 1 ",
  ])
    assert.equal(validSocialMaxPosts(value), false, value);
});

// Runs with the pinned local JSDOM dependency; an explicit override remains available.
test("real DOM: post limits, reader guidance and open sessions guard configuration saves", async () => {
  const { dom, root, act, cleanup } = await setupDOM();
  const { createElement } = await import("react");
  const { SocialConnections } = await import("../src/SocialConnections.tsx");
  const { resetSession } = await import("../src/api.ts");
  resetSession();
  let sessionOpen = true;
  const writes: unknown[] = [];
  globalThis.fetch = async (input, init) => {
    const path = String(input),
      method = init?.method || "GET";
    let result: unknown;
    if (path === "/api/session") result = { token: "test-token" };
    else if (path === "/api/social/connections" && method === "GET")
      result = {
        reader: "http",
        instagram_reader: "browser",
        proxy_url: "",
        max_posts: 3,
        youtube_key_configured: true,
        dependencies: { browser: false, instaloader: true },
        sessions: {
          instagram: { state: sessionOpen ? "open" : "closed" },
          facebook: { state: "closed" },
        },
      };
    else if (
      path === "/api/social/browser/instagram/close" &&
      method === "POST"
    ) {
      sessionOpen = false;
      result = { status: "closed", message: "已关闭" };
    } else if (path === "/api/social/connections" && method === "PUT") {
      writes.push(JSON.parse(String(init?.body)));
      result = {};
    } else assert.fail(`Unexpected request: ${method} ${path}`);
    return new Response(JSON.stringify(result), { status: 200 });
  };
  const flush = () =>
    act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 15));
    });
  const button = (text: string) => {
    const found = Array.from(document.querySelectorAll("button")).find(
      (item) => item.textContent === text,
    );
    assert.ok(found, text);
    return found;
  };
  try {
    await act(async () => root.render(createElement(SocialConnections)));
    await flush();
    const posts = document.querySelector('input[type="number"]')!;
    assert.equal(posts.getAttribute("min"), "1");
    assert.equal(posts.getAttribute("max"), "5");
    assert.equal(posts.getAttribute("step"), "1");
    assert.ok(
      document.body.textContent?.includes(
        "Facebook / Twitter（X）默认读取方式",
      ),
    );
    assert.ok(document.body.textContent?.includes("否则使用 HTTP"));
    assert.ok(
      document.body.textContent?.includes("选择 Instaloader 时独立生效"),
    );
    assert.ok(!document.body.textContent?.includes("所选读取方式的依赖不可用"));
    assert.equal(button("保存连接配置").disabled, true);
    assert.ok(document.body.textContent?.includes("未提供会话状态"));
    assert.equal(button("打开 Twitter（X） 登录浏览器").disabled, true);
    assert.equal(button("关闭并释放 Twitter（X） 会话").disabled, true);
    // A synthetic submit bypasses button disabling; the handler must guard it too.
    await act(async () => {
      document
        .querySelector("form")!
        .dispatchEvent(
          new dom.window.Event("submit", { bubbles: true, cancelable: true }),
        );
    });
    assert.equal(writes.length, 0);
    await act(async () => button("关闭并释放 Instagram 会话").click());
    await flush();
    assert.equal(button("保存连接配置").disabled, false);
    await act(async () => button("保存连接配置").click());
    await flush();
    assert.deepEqual(writes, [
      {
        reader: "http",
        instagram_reader: "browser",
        proxy_url: "",
        max_posts: 3,
      },
    ]);
  } finally {
    await cleanup();
  }
});
