import { describe, expect, it, vi } from "vitest";
import "./next";
import "./entries/router";
import "./entries/history";

interface NextStatic {
  _init(context: Record<string, unknown>): void;
  router?: { _install: (factory: unknown) => void } | undefined;
  $history?: true;
}

const win = globalThis as unknown as { Next: NextStatic };

describe("the history chunk entry", () => {
  it("installs its keeper on the router once", async () => {
    win.Next._init({ $router: { scope: "all" }, $layouts: [] });
    const install = vi.spyOn(win.Next.router!, "_install");
    vi.resetModules();
    await import("./entries/history");
    expect(install).not.toHaveBeenCalled();
    delete win.Next.$history;
    vi.resetModules();
    await import("./entries/history");
    expect(install).toHaveBeenCalledTimes(1);
  });

  it("does nothing on a page without the router", async () => {
    const router = win.Next.router;
    delete win.Next.router;
    delete win.Next.$history;
    vi.resetModules();
    await expect(import("./entries/history")).resolves.toBeDefined();
    win.Next.router = router;
    expect(win.Next.$history).toBe(true);
  });
});
