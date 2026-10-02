import { expect, it, vi } from "vitest";
import "./next";
// Evaluates the dev chunk's entry, as the runtime does once it fetched it.
import "./dev";
import { warnCsrf } from "./diagnostics";

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
  // The channel joins once the landed chunk resolves, catching up over the page.
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

it("warns about a malformed $csrf payload the runtime ignored", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  warnCsrf({});
  warnCsrf({ $csrf: { header: "X-CSRFToken", token: "ok" } });
  expect(warn).not.toHaveBeenCalled();
  warnCsrf({ $csrf: { header: "X-CSRFToken" } });
  expect(warn).toHaveBeenCalledWith(expect.stringContaining("$csrf"));
});
