import { beforeEach, describe, expect, it, vi } from "vitest";
import { createScripts } from "./scripts";
import type { Scripts } from "./scripts";

interface Dispatched {
  event: string;
  detail: Record<string, unknown>;
}

function inserted(name: string): HTMLScriptElement[] {
  return Array.from(
    document.head.querySelectorAll<HTMLScriptElement>(
      `script[data-next-script="${name}"]`,
    ),
  );
}

// The vendor file never loads under jsdom, so a test answers for the network.
function answer(name: string, event: "load" | "error"): void {
  const el = inserted(name).find((script) => script.src !== "");
  el!.dispatchEvent(new Event(event));
}

describe("the scripts manifest", () => {
  let dispatched: Dispatched[];
  let granted: Set<string>;
  let idle: (() => void)[];
  let scripts: Scripts;
  // A target of its own per case, so an armed interaction listener cannot outlive it.
  let events: EventTarget;

  beforeEach(() => {
    events = new EventTarget();
    document.head.innerHTML = "";
    dispatched = [];
    granted = new Set(["necessary"]);
    idle = [];
    scripts = createScripts({
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      allows: (category) => granted.has(category),
      idle: (run) => void idle.push(run),
      nonce: "boot",
      events,
    });
  });

  it("inserts an async entry at once, init before src, both carrying the nonce", () => {
    scripts._configure([
      {
        name: "gtag",
        init: "window.dataLayer=[];",
        src: "https://www.googletagmanager.com/gtag/js?id=G-1",
        strategy: "async",
        category: "necessary",
        attrs: { crossorigin: "anonymous", "data-x": 1 },
      },
    ]);
    const [init, src] = inserted("gtag");
    expect(init!.textContent).toBe("window.dataLayer=[];");
    expect(init!.hasAttribute("src")).toBe(false);
    expect(src!.src).toBe("https://www.googletagmanager.com/gtag/js?id=G-1");
    expect(src!.async).toBe(true);
    expect(src!.getAttribute("crossorigin")).toBe("anonymous");
    expect(src!.hasAttribute("data-x")).toBe(false);
    expect(init!.nonce).toBe("boot");
    expect(src!.nonce).toBe("boot");
    expect(scripts.status("gtag")).toBe("loading");
  });

  it("prefers the entry's own nonce over the bootstrap one", () => {
    scripts._configure([{ name: "a", init: "/* a */", nonce: "own" }]);
    expect(inserted("a")[0]!.nonce).toBe("own");
  });

  it("keeps a deferred head script in order with the others", () => {
    scripts._configure([{ name: "d", src: "/d.js", strategy: "defer" }]);
    expect(inserted("d")[0]!.async).toBe(false);
  });

  it("inserts in manifest order", () => {
    scripts._configure([
      { name: "late", init: "/* late */" },
      { name: "first", init: "/* first */" },
    ]);
    const names = Array.from(document.head.querySelectorAll("script")).map((el) =>
      el.getAttribute("data-next-script"),
    );
    expect(names).toEqual(["late", "first"]);
  });

  it("marks an init-only entry loaded at once and announces it", () => {
    scripts._configure([{ name: "stub", init: "window.q=[]" }]);
    expect(scripts.status("stub")).toBe("loaded");
    expect(dispatched).toEqual([
      { event: "next:script-loaded", detail: { name: "stub" } },
    ]);
  });

  it("announces a src load and a src error on the document too", () => {
    const loaded = vi.fn();
    const failed = vi.fn();
    document.addEventListener("next:script-loaded", loaded);
    document.addEventListener("next:script-error", failed);
    scripts._configure([
      { name: "ok", src: "/ok.js" },
      { name: "bad", src: "/bad.js" },
    ]);
    answer("ok", "load");
    answer("bad", "error");
    document.removeEventListener("next:script-loaded", loaded);
    document.removeEventListener("next:script-error", failed);
    expect(scripts.status("ok")).toBe("loaded");
    expect(scripts.status("bad")).toBe("error");
    expect(loaded).toHaveBeenCalledOnce();
    expect((failed.mock.calls[0]![0] as CustomEvent).detail).toEqual({
      name: "bad",
      url: "/bad.js",
    });
  });

  it("waits for the idle slot", () => {
    scripts._configure([{ name: "chat", src: "/chat.js", strategy: "idle" }]);
    expect(scripts.status("chat")).toBe("pending");
    expect(inserted("chat")).toEqual([]);
    idle[0]!();
    expect(inserted("chat")).toHaveLength(1);
  });

  it("waits for the first interaction, then stops listening", () => {
    scripts._configure([
      { name: "widget", src: "/widget.js", strategy: "interaction" },
      { name: "help", init: "/* help */", strategy: "interaction" },
    ]);
    expect(inserted("widget")).toEqual([]);
    events.dispatchEvent(new Event("keydown"));
    expect(inserted("widget")).toHaveLength(1);
    expect(scripts.status("help")).toBe("loaded");
    scripts._configure([{ name: "later", init: "/* x */", strategy: "interaction" }]);
    events.dispatchEvent(new Event("scroll"));
    expect(scripts.status("later")).toBe("loaded");
  });

  it("holds a manual entry until load, which resolves once it lands", async () => {
    scripts._configure([{ name: "video", src: "/video.js", strategy: "manual" }]);
    expect(scripts.status("video")).toBe("pending");
    const first = scripts.load("video");
    const second = scripts.load("video");
    expect(inserted("video")).toHaveLength(1);
    answer("video", "load");
    await expect(first).resolves.toBeUndefined();
    await expect(second).resolves.toBeUndefined();
    await expect(scripts.load("video")).resolves.toBeUndefined();
  });

  it("rejects load for a script that failed", async () => {
    scripts._configure([{ name: "video", src: "/video.js", strategy: "manual" }]);
    const pending = scripts.load("video");
    answer("video", "error");
    await expect(pending).rejects.toThrow("video failed to load");
    await expect(scripts.load("video")).rejects.toThrow("video is error");
  });

  it("rejects load for an unknown or blocked script", async () => {
    scripts._configure([{ name: "pixel", src: "/p.js", category: "marketing" }]);
    await expect(scripts.load("nope")).rejects.toThrow("nope is unknown");
    await expect(scripts.load("pixel")).rejects.toThrow("pixel is blocked");
  });

  it("keeps a gated entry blocked until consent grants its category", () => {
    scripts._configure([
      { name: "pixel", init: "/* fbq */", src: "/p.js", category: "marketing" },
      { name: "hotjar", src: "/h.js", category: "analytics" },
    ]);
    expect(scripts.status("pixel")).toBe("blocked");
    expect(inserted("pixel")).toEqual([]);
    granted.add("marketing");
    scripts._refresh();
    expect(inserted("pixel")).toHaveLength(2);
    expect(scripts.status("hotjar")).toBe("blocked");
  });

  it("leaves a script the server rendered alone and reports it rendered", async () => {
    document.head.innerHTML =
      '<script data-next-script="gtm" src="/gtm.js"></script>' +
      '<script data-next-script="served"></script>';
    scripts._configure([{ name: "gtm", src: "/gtm.js" }]);
    expect(document.head.querySelectorAll("script")).toHaveLength(2);
    expect(scripts.status("gtm")).toBe("rendered");
    expect(scripts.status("served")).toBe("rendered");
    expect(scripts.status("missing")).toBeUndefined();
    await expect(scripts.load("gtm")).resolves.toBeUndefined();
    await expect(scripts.load("served")).resolves.toBeUndefined();
  });

  it("drops a malformed entry and a repeated name", () => {
    scripts._configure([
      null,
      { name: "bare" },
      { src: "/nameless.js" },
      { name: "once", init: "/* 1 */", attrs: "nope" },
      { name: "once", init: "/* 2 */" },
    ]);
    scripts._configure("not a list");
    expect(scripts.status("bare")).toBeUndefined();
    expect(inserted("once").map((el) => el.textContent)).toEqual(["/* 1 */"]);
  });

  it("blocks a waiting entry whose category is revoked before its moment", async () => {
    granted.add("analytics");
    scripts._configure([
      { name: "chat", src: "/chat.js", strategy: "idle", category: "analytics" },
      { name: "widget", src: "/w.js", strategy: "interaction", category: "analytics" },
      { name: "video", src: "/v.js", strategy: "manual", category: "analytics" },
    ]);
    granted.delete("analytics");
    idle[0]!();
    events.dispatchEvent(new Event("pointerdown"));
    await expect(scripts.load("video")).rejects.toThrow("video is blocked");
    expect(document.head.querySelectorAll("script")).toHaveLength(0);
    expect(scripts.status("chat")).toBe("blocked");
    expect(scripts.status("widget")).toBe("blocked");
  });

  it("moves pending entries of a revoked category to blocked on refresh", async () => {
    granted.add("analytics");
    scripts._configure([
      { name: "video", src: "/v.js", strategy: "manual", category: "analytics" },
      { name: "base", src: "/b.js", strategy: "manual" },
    ]);
    granted.delete("analytics");
    scripts._refresh();
    expect(scripts.status("video")).toBe("blocked");
    expect(scripts.status("base")).toBe("pending");
    await expect(scripts.load("video")).rejects.toThrow("video is blocked");
    granted.add("analytics");
    scripts._refresh();
    expect(scripts.status("video")).toBe("pending");
  });
});

describe("the scripts defaults", () => {
  it("reads the live document and idles through the platform", () => {
    vi.useFakeTimers();
    const scripts = createScripts({ dispatch: () => undefined, allows: () => true });
    document.head.innerHTML = "";
    scripts._configure([
      { name: "late", init: "window.nextLate = true", strategy: "idle" },
    ]);
    vi.runAllTimers();
    vi.useRealTimers();
    expect(scripts.status("late")).toBe("loaded");
    expect(
      document.head.querySelector<HTMLScriptElement>('script[data-next-script="late"]')!
        .nonce,
    ).toBe("");
  });

  it("hands the idle slot to requestIdleCallback, bounded by the runtime's own", () => {
    const requestIdle = vi.fn();
    vi.stubGlobal("requestIdleCallback", requestIdle);
    const scripts = createScripts({ dispatch: () => undefined, allows: () => true });
    scripts._configure([
      { name: "rich", init: "/* rich */", strategy: "idle", timeout: 500 },
    ]);
    vi.unstubAllGlobals();
    expect(requestIdle).toHaveBeenCalledWith(expect.any(Function), { timeout: 3000 });
    expect(scripts.status("rich")).toBe("pending");
  });

  it("waits for the load event on a page still loading", () => {
    vi.useFakeTimers();
    vi.spyOn(document, "readyState", "get").mockReturnValue("loading");
    const scripts = createScripts({ dispatch: () => undefined, allows: () => true });
    scripts._configure([{ name: "early", init: "/* early */", strategy: "idle" }]);
    vi.runAllTimers();
    expect(scripts.status("early")).toBe("pending");
    window.dispatchEvent(new Event("load"));
    vi.runAllTimers();
    vi.useRealTimers();
    expect(scripts.status("early")).toBe("loaded");
  });
});
