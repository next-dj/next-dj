import { describe, expect, it, vi } from "vitest";
import "./next";
import "./entries/router";
import "./entries/intent";

interface NextStatic {
  _init(context: Record<string, unknown>): void;
  router?: { _install: (factory: unknown) => void } | undefined;
  $intent?: true;
}

const win = globalThis as unknown as { Next: NextStatic };

describe("the intent chunk entry", () => {
  it("installs its prefetcher on the router once", async () => {
    win.Next._init({ $router: { scope: "all" }, $layouts: [] });
    const install = vi.spyOn(win.Next.router!, "_install");
    vi.resetModules();
    await import("./entries/intent");
    expect(install).not.toHaveBeenCalled();
    delete win.Next.$intent;
    vi.resetModules();
    await import("./entries/intent");
    expect(install).toHaveBeenCalledTimes(1);
  });

  it("does nothing on a page without the router", async () => {
    const router = win.Next.router;
    delete win.Next.router;
    delete win.Next.$intent;
    vi.resetModules();
    await expect(import("./entries/intent")).resolves.toBeDefined();
    win.Next.router = router;
    expect(win.Next.$intent).toBe(true);
  });
});
