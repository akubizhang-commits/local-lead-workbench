import "./support/registerTsx.ts";
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

// P1-15：styles.css 的字号下限、文字颜色白名单和装饰色 --decor 的使用范围（design §5）。
const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const rootBlocks = [...css.matchAll(/:root\s*\{([^}]*)\}/g)].map((m) => m[1]);
const rootDecls = rootBlocks.flatMap((b) => [...b.matchAll(/(--[\w-]+)\s*:\s*([^;]+)/g)].map((m) => [m[1], m[2].trim()] as const));
const tokens = new Map(rootDecls);
const FONT_TOKENS = ["--fs-xs", "--fs-sm", "--fs-md", "--fs-lg", "--fs-xl", "--fs-h1", "--fs-h1-detail"];
// §5.2 第 2 条列出的文字 token，加上为已达标颜色定义的同值 token（值不变）。
const TEXT_TOKENS = [
  "--ink", "--text-2", "--muted", "--teal", "--link", "--placeholder", "--ok-fg", "--warn-fg", "--info-fg", "--danger-fg",
  "--neutral-fg", "--hint-fg", "--alert-fg", "--toast-fg", "--quote-fg", "--disabled-fg", "--navy", "--workflow-muted", "--workflow-amber",
  "--button-fg", "--input-fg", "--on-navy", "--nav-fg", "--nav-active-fg", "--nav-count-fg", "--local-note-fg", "--avatar-fg",
  "--avatar-letter-fg", "--online-fg", "--offline-fg", "--source-fg", "--check-fg", "--evidence-summary-fg", "--other-contacts-fg",
  "--run-warnings-fg", "--schedule-waiting-fg", "--social-ok-fg", "--social-warn-fg", "--social-error-fg", "--social-info-fg",
  "--social-platform-fg", "--workflow-current-fg", "--workflow-status-fg", "--workflow-hint-fg", "--workflow-hint-hover-fg",
  "--tooltip-sub-fg", "--badge-hint-fg", "--badge-excluded-fg", "--badge-manual-fg", "--field-error-fg",
  "--tier-top-fg", "--tier-mid-fg", "--tier-low-fg", "--tier-none-fg",
];
const DECOR_SELECTOR = /(>svg|\ssvg|\.empty-icon|\.task-icon|\.search-box|\.slash)$/;
function rules(): { selector: string; body: string }[] {
  const out: { selector: string; body: string }[] = [];
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) out.push({ selector: m[1].trim(), body: m[2] });
  return out;
}

test("font sizes never go below 12px and every --fs-* token is defined", () => {
  for (const m of css.matchAll(/font-size\s*:\s*(\d+(?:\.\d+)?)px/g)) assert.ok(Number(m[1]) >= 12, `font-size ${m[1]}px`);
  for (const m of css.matchAll(/font-size\s*:\s*var\((--[\w-]+)\)/g)) assert.ok(FONT_TOKENS.includes(m[1]) && tokens.has(m[1]), m[1]);
  for (const t of FONT_TOKENS) assert.ok(tokens.has(t), `missing ${t}`);
  assert.equal(tokens.get("--fs-xs"), "12px");
});

test("every text color is a whitelisted token or its value", () => {
  const values = new Set(["white", "#fff", "inherit", "currentcolor"]);
  for (const t of TEXT_TOKENS) {
    assert.ok(tokens.has(t), `missing token ${t}`);
    values.add(`var(${t})`);
    const v = tokens.get(t)!.toLowerCase();
    if (v.startsWith("#")) values.add(v);
  }
  const bad: string[] = [];
  for (const { selector, body } of rules()) {
    if (selector.startsWith(":root")) continue;
    for (const m of body.matchAll(/(?<![-\w])color\s*:\s*([^;]+)/g)) {
      const v = m[1].trim().toLowerCase();
      const decor = v === "var(--decor)" && DECOR_SELECTOR.test(selector.split(",").pop()!.trim());
      if (!values.has(v) && !decor) bad.push(`${selector} { color: ${v} }`);
    }
  }
  assert.deepEqual(bad, []);
});

test("--decor is used only on aria-hidden decoration selectors", () => {
  const bad: string[] = [];
  for (const { selector, body } of rules()) {
    if (selector.startsWith(":root") || !body.includes("var(--decor)")) continue;
    for (const part of selector.split(",")) if (!DECOR_SELECTOR.test(part.trim())) bad.push(part.trim());
  }
  assert.deepEqual(bad, []);
});

test(":root defines each custom property once and no --topbar-h", () => {
  const names = rootDecls.map(([n]) => n);
  assert.deepEqual(names.filter((n, i) => names.indexOf(n) !== i), []);
  assert.equal(tokens.has("--topbar-h"), false);
  assert.equal(tokens.get("--muted"), "var(--text-2)");
});

test("Icon renders an aria-hidden svg", async () => {
  const { Icon } = await import("../src/ui.tsx");
  const html = renderToStaticMarkup(createElement(Icon, { name: "search" }));
  assert.match(html, /^<svg[^>]*aria-hidden="true"/);
});
