import "./registerTsx.ts";
import assert from "node:assert/strict";

// Set up DOM before importing react-dom/client so React selects its browser event implementation.
// Node runs test files in separate processes; tests within a file use this helper serially.
export async function setupDOM(url = "http://localhost/") {
  const { JSDOM } = await import(process.env.SOCIAL_TEST_JSDOM || "jsdom");
  const dom = new JSDOM('<div id="test-root"></div>', { url });
  const keys = [
    "window",
    "document",
    "IS_REACT_ACT_ENVIRONMENT",
    "fetch",
  ] as const;
  const saved = new Map(
    keys.map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]),
  );
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  dom.window.scrollTo = () => {};
  dom.window.HTMLElement.prototype.scrollIntoView = () => {};
  // Never fall through to a real backend or external source.
  globalThis.fetch = async (input) => {
    throw new Error(`Unmocked request: ${String(input)}`);
  };
  const { act } = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { resetSession } = await import("../../src/api.ts");
  resetSession();
  const root = createRoot(document.getElementById("test-root")!);
  const errors: unknown[][] = [];
  const originalError = console.error;
  console.error = (...args) => {
    errors.push(args);
    originalError(...args);
  };
  return {
    dom,
    root,
    act,
    async cleanup() {
      try {
        await act(async () => root.unmount());
      } finally {
        console.error = originalError;
        dom.window.close();
        resetSession();
        for (const key of keys) {
          const descriptor = saved.get(key);
          if (descriptor) Object.defineProperty(globalThis, key, descriptor);
          else Reflect.deleteProperty(globalThis, key);
        }
        assert.equal(errors.length, 0, "Unexpected React/DOM errors during test");
      }
    },
  };
}
