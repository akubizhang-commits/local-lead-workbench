import { setupDOM } from "./support/dom.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";

test("diagnostics button downloads the bundle and reports success", async () => {
  const h = await setupDOM();
  const requests: string[] = [];
  globalThis.fetch = async (input) => {
    const url = String(input);
    requests.push(url);
    if (url === "/api/session") return new Response(JSON.stringify({ token: "t" }), { headers: { "Content-Type": "application/json" } });
    if (url === "/api/diagnostics/bundle") return new Response(new Uint8Array([80, 75, 5, 6]), { headers: { "Content-Type": "application/zip" } });
    throw new Error("unexpected " + url);
  };
  const saved: string[] = [];
  const messages: string[] = [];
  const URLs = window.URL as unknown as { createObjectURL: (b: Blob) => string; revokeObjectURL: (u: string) => void };
  URLs.createObjectURL = () => "blob:diag";
  URLs.revokeObjectURL = () => {};
  globalThis.URL.createObjectURL = URLs.createObjectURL;
  globalThis.URL.revokeObjectURL = URLs.revokeObjectURL;
  window.HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) { saved.push(this.download); };
  try {
    const { DiagnosticsPanel, diagnosticsFileName } = await import("../src/pages/SettingsPage.tsx");
    const act = async (work: () => Promise<unknown>, message: string | ((r: unknown) => string)) => {
      await work();
      messages.push(typeof message === "string" ? message : message(undefined));
    };
    await h.act(async () => { h.root.render(createElement(DiagnosticsPanel, { busy: false, act })); });
    assert.match(document.body.textContent || "", /诊断信息/);
    assert.match(document.body.textContent || "", /不包含企业、联系人等业务数据，也不包含令牌、密钥和登录信息/);
    const button = [...document.querySelectorAll("button")].find((b) => b.textContent?.includes("导出诊断包"))!;
    await h.act(async () => { button.click(); await new Promise((r) => setTimeout(r, 20)); });
    assert.ok(requests.includes("/api/diagnostics/bundle"));
    assert.deepEqual(saved, [diagnosticsFileName()]);
    assert.match(saved[0], /^诊断包-\d{4}-\d{2}-\d{2}\.zip$/);
    assert.deepEqual(messages, ["诊断包已下载"]);
  } finally {
    await h.cleanup();
  }
});
