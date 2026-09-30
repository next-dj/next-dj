import { expect, it, vi } from "vitest";
import "./next";
// Evaluates the dev chunk's entry, as the runtime does once it fetched it.
import "./dev";

const win = globalThis as unknown as {
  Next: {
    _init(context: Record<string, unknown>): void;
    _diagnostics(diagnostics: unknown): void;
  };
};

it("hands the dev channel to the runtime as it evaluates", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  document.body.innerHTML = '<div data-next-zone="z" data-next-lazy="soon"></div>';
  win.Next._init({ $dev: true });
  expect(warn).toHaveBeenCalledWith(expect.stringContaining('data-next-lazy="soon"'));
});

it("keeps the first dev channel when a second copy of the chunk hands one over", () => {
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const second = { attrs: vi.fn() };
  win.Next._diagnostics(second);
  document.body.innerHTML = '<div data-next-zone="z" data-next-lazy="soon"></div>';
  win.Next._init({ $dev: true });
  expect(second.attrs).not.toHaveBeenCalled();
});
