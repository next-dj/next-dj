import { afterEach, describe, expect, it, vi } from "vitest";
import "./next";
import "./entries/boundaries";
import "./entries/more";
import "./entries/reload";
import "./entries/router";
import type { Router } from "./router";

interface NextStatic {
  _init(context: Record<string, unknown>): void;
  _extend(name: string, install: (kernel: unknown) => unknown, build: string): void;
  router?: Router | undefined;
  partial: {
    fetch(request: Element | { url: string }): Promise<{ status: string }>;
    mount(nodes: readonly Element[]): void;
    onMount(selector: string, callback: (el: Element) => void): () => void;
  };
}

const win = globalThis as unknown as { Next: NextStatic };

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
  ["boundaries", () => import("./entries/boundaries")],
  ["more", () => import("./entries/more")],
  ["reload", () => import("./entries/reload")],
  ["router", () => import("./entries/router")],
])("the %s chunk entry", (_name, load) => {
  it("installs nothing more when its bundle evaluates twice", async () => {
    win.Next._init({ $router: { scope: "all" } });
    const router = win.Next.router;
    const spies = [
      vi.spyOn(document, "addEventListener"),
      vi.spyOn(window, "addEventListener"),
      vi.spyOn(win.Next.partial, "onMount"),
    ];
    vi.resetModules();
    await load();
    expect(spies.map((spy) => spy.mock.calls.length)).toEqual([0, 0, 0]);
    expect(win.Next.router).toBe(router);
  });
});

describe.each([
  ["csrf", () => import("./entries/csrf")],
  ["dev", () => import("./entries/dev")],
  ["forms", () => import("./entries/forms")],
  ["intent", () => import("./entries/intent")],
  ["layers", () => import("./entries/layers")],
  ["poll", () => import("./entries/poll")],
  ["stream", () => import("./entries/stream")],
  ["ws", () => import("./entries/ws")],
])("the %s chunk entry", (name, load) => {
  it("hands the runtime what the chunk exports under its own name", async () => {
    const extend = vi.spyOn(win.Next, "_extend");
    vi.resetModules();
    await load();
    expect(extend).toHaveBeenCalledWith(name, expect.any(Function), "test");
    const install = extend.mock.calls[0]![1];
    expect(install({ context: () => ({}) })).toBeDefined();
  });
});

describe("the intent chunk", () => {
  it("marks the links no render marked as the router builds its prefetch", async () => {
    const extend = vi.spyOn(win.Next, "_extend");
    vi.resetModules();
    await import("./entries/intent");
    const factory = extend.mock.calls[0]![1]({}) as (host: unknown) => unknown;
    document.body.innerHTML = '<nav><a id="l" href="/">x</a></nav>';
    const hook = vi.fn();
    const prefetch = factory({
      settings: () => ({ links: ["nav"], prefetch: "off" }),
      hook,
      serve: vi.fn(),
      partial: { layers: { urlFor: () => "/" } },
    });
    expect(prefetch).toEqual(expect.objectContaining({ take: expect.any(Function) }));
    expect(document.getElementById("l")!.getAttribute("aria-current")).toBe("page");
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
    win.Next._init({ $router: { scope: "all" } });
    document.removeEventListener("router:end", ended);
    expect(ended).toHaveBeenCalledTimes(1);
    expect(history.state).toEqual({ next: 1, kind: "nav", key: expect.any(String) });
    const keys = Object.keys(win.Next.router!).sort();
    expect(keys).toEqual(["configure", "flush", "prefetch", "visit"]);
  });
});
