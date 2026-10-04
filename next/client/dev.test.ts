import { expect, it, vi } from "vitest";
import "./next";
// Evaluates the dev chunk's entry, as the runtime does once it fetched it.
import "./dev";
import { createDiagnostics } from "./diagnostics";

const win = globalThis as unknown as {
  Next: {
    _init(context: Record<string, unknown>): void;
    _land(key: "dev", diagnostics: unknown): void;
  };
};

it("hands the dev channel to the runtime as it evaluates", async () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  document.body.innerHTML = '<div data-next-zone="z" data-next-lazy="soon"></div>';
  win.Next._init({ $dev: true });
  // The diagnostics are attached once the loaded chunk resolves, then check the page.
  await Promise.resolve();
  expect(warn).toHaveBeenCalledWith(expect.stringContaining('data-next-lazy="soon"'));
});

it("keeps the first dev channel when a second copy of the chunk hands one over", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const second = { attrs: vi.fn() };
  win.Next._land("dev", second);
  document.body.innerHTML = '<div data-next-zone="z" data-next-lazy="soon"></div>';
  win.Next._init({ $dev: true });
  await Promise.resolve();
  expect(second.attrs).not.toHaveBeenCalled();
});

it("warns about a malformed $csrf payload the runtime ignored, once", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  document.body.innerHTML = "";
  createDiagnostics().attrs(document);
  createDiagnostics({ $csrf: { header: "X-CSRFToken", token: "ok" } }).attrs(document);
  expect(warn).not.toHaveBeenCalled();
  const diagnostics = createDiagnostics({ $csrf: { header: "X-CSRFToken" } });
  diagnostics.attrs(document);
  diagnostics.attrs(document);
  expect(warn).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("$csrf"));
});

// Last in the file, since it replaces the runtime the cases above used.
it("warns once when a stalled copy of the chunk evaluates after its retry", async () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  document.body.innerHTML = "";
  vi.resetModules();
  await import("./next");
  win.Next._init({
    $dev: true,
    $csrf: { header: "X-CSRFToken" },
    $chunks: { dev: "/static/next/next.dev.min.js" },
  });
  await import("./dev");
  vi.resetModules();
  await import("./dev");
  await Promise.resolve();
  const csrf = warn.mock.calls.filter(([text]) => String(text).includes("$csrf"));
  expect(csrf).toHaveLength(1);
});
