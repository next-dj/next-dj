import { afterEach, describe, expect, it, vi } from "vitest";
import "./next";
import "./entries/defer";
import "./entries/more";
import "./entries/reader";
import "./entries/reload";
import "./entries/router";
import "./entries/history";
import "./entries/intent";
import type { Router } from "./router";

interface NextStatic {
  _init(context: Record<string, unknown>): void;
  on(event: string, listener: () => void): () => void;
  router?: Router | undefined;
  partial: {
    _reader?: unknown;
    fetch(request: Element | { url: string }): Promise<{ status: string }>;
    mount(nodes: readonly Element[]): void;
    onMount(selector: string, callback: (el: Element) => void): () => void;
  };
}

const win = globalThis as unknown as { Next: NextStatic };

const ran = (): Set<string> =>
  (window as unknown as Record<symbol, Set<string>>)[Symbol.for("next.once")]!;

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.body.innerHTML = "";
});

function reload(): void {
  document.dispatchEvent(
    new CustomEvent("partial:before-request", {
      detail: { url: "/", method: "GET", intent: { zone: "z" } },
    }),
  );
}

describe.each([
  ["defer", () => import("./entries/defer")],
  ["more", () => import("./entries/more")],
  ["reader", () => import("./entries/reader")],
  ["reload", () => import("./entries/reload")],
  ["router", () => import("./entries/router")],
  ["history", () => import("./entries/history")],
  ["intent", () => import("./entries/intent")],
])("the %s chunk entry", (_name, load) => {
  it("registers nothing more when its bundle evaluates twice", async () => {
    win.Next._init({ $router: { scope: "all" }, $layouts: [] });
    const router = win.Next.router;
    const reader = win.Next.partial._reader;
    const spies = [
      vi.spyOn(document, "addEventListener"),
      vi.spyOn(window, "addEventListener"),
      vi.spyOn(win.Next, "on"),
      vi.spyOn(win.Next.partial, "onMount"),
      vi.spyOn(router!, "_install"),
    ];
    vi.resetModules();
    await load();
    expect(spies.map((spy) => spy.mock.calls.length)).toEqual([0, 0, 0, 0, 0]);
    expect(win.Next.router).toBe(router);
    expect(win.Next.partial._reader).toBe(reader);
  });
});

describe.each([
  ["history", () => import("./entries/history")],
  ["intent", () => import("./entries/intent")],
])("the %s chunk entry on a page without the router", (name, load) => {
  it("installs nothing and evaluates", async () => {
    const router = win.Next.router;
    delete win.Next.router;
    ran().delete(name);
    vi.resetModules();
    await expect(load()).resolves.toBeDefined();
    win.Next.router = router;
    expect(ran().has(name)).toBe(true);
  });
});

describe("the chunks at work", () => {
  it("lets Next.partial.fetch take an element that is neither a wrapper nor a zone", async () => {
    await expect(win.Next.partial.fetch(document.createElement("p"))).resolves.toEqual({
      status: "dropped",
      applied: false,
    });
  });

  it("lands a defer scan the runtime hands every mounted wrapper to", async () => {
    const fetch = vi.fn(() => Promise.reject(new TypeError("offline")));
    vi.stubGlobal("fetch", fetch);
    document.body.innerHTML =
      '<div id="b-1" data-next-boundary="b-1" data-next-token="tok" ' +
      'data-next-state="idle" data-next-defer="load"></div>';
    win.Next.partial.mount([document.getElementById("b-1")!]);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(new URL(url).pathname).toBe(location.pathname);
    expect(new Headers(init.headers).get("X-Next-Boundary")).toBe("tok");
  });

  it("stamps the rows of a list as the runtime mounts its more link", () => {
    document.body.innerHTML =
      '<ul data-next-zone="list"><li>a</li></ul><div data-next-zone="list-more">' +
      '<a href="/list/?page=2" data-next-target="list,list-more" ' +
      'data-next-url="replace">More</a></div>';
    win.Next.partial.mount([document.body]);
    expect(document.querySelector("li")!.getAttribute("data-next-batch")).toBe("1");
  });

  it("drains the fills a streamed document pushes", () => {
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
      frames.push(callback),
    );
    document.body.innerHTML =
      '<div id="b-1" data-next-boundary="b-1" data-next-state="loading">wait</div>';
    window.$nx!.push({
      z: "b-1",
      s: "ready",
      e: { ops: [{ op: "inner", target: { boundary: "b-1" }, html: "one" }] },
    });
    for (const callback of frames.splice(0)) callback(0);
    expect(document.getElementById("b-1")!.textContent).toBe("one");
    expect(document.getElementById("b-1")!.getAttribute("data-next-state")).toBe(
      "ready",
    );
  });

  it("shows the loading content of a boundary in a zone the runtime reloads", () => {
    vi.useFakeTimers();
    document.body.innerHTML =
      '<section data-next-zone="z" data-next-reload="loading">' +
      '<div id="b-2" data-next-boundary="b-2" data-next-state="ready">done</div>' +
      '</section><template data-next-loading="b-2"><i>wait</i></template>';
    win.Next.partial.mount([document.body]);
    reload();
    vi.advanceTimersByTime(150);
    expect(document.getElementById("b-2")!.innerHTML).toBe("<i>wait</i>");
    document.dispatchEvent(
      new CustomEvent("partial:settled", {
        detail: { zones: ["z"], boundaries: [], status: "ok" },
      }),
    );
  });

  it("boots the router on the runtime's ready and stamps the entry it booted on", () => {
    const ended = vi.fn();
    document.addEventListener("router:end", ended);
    win.Next._init({ $router: { scope: "all" }, $layouts: [] });
    document.removeEventListener("router:end", ended);
    expect(ended).toHaveBeenCalledTimes(1);
    expect(history.state).toEqual({ next: 1, kind: "nav", key: expect.any(String) });
    const keys = Object.keys(win.Next.router!).sort();
    expect(keys).toEqual(["_install", "configure", "flush", "prefetch", "visit"]);
  });
});
