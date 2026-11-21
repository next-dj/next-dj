import { afterEach, describe, expect, it, vi } from "vitest";
import "./next";
import "./entries/router";

interface NextStatic {
  _init(context: Record<string, unknown>): void;
  router?: object;
}

const win = globalThis as unknown as { Next: NextStatic };

afterEach(() => {
  document.body.innerHTML = "";
});

describe("the router chunk entry", () => {
  it("boots on the runtime's ready and stamps the entry it booted on", () => {
    const ended = vi.fn();
    document.addEventListener("router:end", ended);
    win.Next._init({ $router: { scope: "all" }, $layouts: [] });
    document.removeEventListener("router:end", ended);
    expect(ended).toHaveBeenCalledTimes(1);
    expect(history.state).toEqual({ next: 1, kind: "nav", key: expect.any(String) });
    const keys = Object.keys(win.Next.router!).sort();
    expect(keys).toEqual(["_install", "configure", "flush", "prefetch", "visit"]);
  });

  it("installs one router when the bundle evaluates twice", async () => {
    const router = win.Next.router;
    vi.resetModules();
    await import("./entries/router");
    expect(router).toBeDefined();
    expect(win.Next.router).toBe(router);
  });
});
