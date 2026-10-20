import "./support/registerTsx.ts";
import { setupDOM } from "./support/dom.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { createElement as h } from "react";

// U-13：弹窗打开后焦点在第一个可用字段；没有字段时在底部第一个按钮；'none' 保持现状；也可以指定选择器。
async function open(children: unknown, initialFocus?: string) {
  const d = await setupDOM();
  (window as any).HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
  const { Modal } = await import("../src/ui.tsx");
  const before = document.createElement("button");
  document.body.append(before);
  before.focus();
  await d.act(async () => d.root.render(h(Modal, { title: "T", onClose: () => {}, initialFocus, children } as never)));
  return { ...d, before };
}
test("first enabled field gets focus", async () => {
  const d = await open([h("input", { key: 1, disabled: true, name: "a" }), h("select", { key: 2, name: "b" }), h("textarea", { key: 3 })]);
  try { assert.equal((document.activeElement as HTMLSelectElement).name, "b"); } finally { await d.cleanup(); }
});
test("only buttons: the first footer button gets focus", async () => {
  const d = await open(h("footer", null, h("button", { key: 1 }, "关闭"), h("button", { key: 2 }, "确认")));
  try { assert.equal(document.activeElement!.textContent, "关闭"); } finally { await d.cleanup(); }
});
test("initialFocus='none' leaves focus where it was; a selector targets that field", async () => {
  const d = await open(h("input", { name: "x" }), "none");
  try { assert.equal(document.activeElement, d.before); } finally { await d.cleanup(); }
  const e = await open([h("input", { key: 1, name: "country" }), h("input", { key: 2, name: "state" })], "[name=state]");
  try { assert.equal((document.activeElement as HTMLInputElement).name, "state"); } finally { await e.cleanup(); }
});
