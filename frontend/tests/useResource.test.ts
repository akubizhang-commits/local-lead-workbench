import "./support/registerTsx.ts";
import { setupDOM } from "./support/dom.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";

// keepStale (design §4.2 / D17): a version bump on the same path keeps the old data while re-reading.
async function mount(options?: { keepStale?: boolean }) {
  const h = await setupDOM();
  const gates: (() => void)[] = [];
  let n = 0;
  globalThis.fetch = async (input) => {
    const path = String(input);
    if (path === "/api/session") return new Response(JSON.stringify({ token: "t" }));
    n++;
    const value = `${path}#${n}`;
    await new Promise<void>((resolve) => gates.push(resolve));
    return new Response(JSON.stringify({ value }));
  };
  const { useResource } = await import("../src/hooks.ts");
  const seen: (string | undefined)[] = [];
  function Probe({ path, version }: { path: string; version: number }) {
    const r = useResource<{ value: string }>(path, version, false, options);
    seen.push(r.loading ? `loading:${r.data?.value ?? "-"}` : r.data?.value);
    return null;
  }
  const render = (path: string, version: number) => h.act(async () => h.root.render(createElement(Probe, { path, version })));
  const settle = () => h.act(async () => { await new Promise((r) => setTimeout(r, 10)); gates.splice(0).forEach((g) => g()); await new Promise((r) => setTimeout(r, 10)); });
  return { ...h, seen, render, settle };
}

test("keepStale keeps data across a version change and clears it on a new path", async () => {
  const h = await mount({ keepStale: true });
  try {
    await h.render("/a", 0); await h.settle();
    assert.equal(h.seen.at(-1), "/api/a#1");
    await h.render("/a", 1);
    assert.equal(h.seen.at(-1), "loading:/api/a#1");
    await h.settle();
    assert.equal(h.seen.at(-1), "/api/a#2");
    await h.render("/b", 1);
    assert.equal(h.seen.at(-1), "loading:-");
    await h.settle();
    assert.equal(h.seen.at(-1), "/api/b#3");
  } finally { await h.cleanup(); }
});

test("without options a version change clears data as before", async () => {
  const h = await mount();
  try {
    await h.render("/a", 0); await h.settle();
    await h.render("/a", 1);
    assert.equal(h.seen.at(-1), "loading:-");
    await h.settle();
    assert.equal(h.seen.at(-1), "/api/a#2");
  } finally { await h.cleanup(); }
});
