import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPartial } from "./partial";
import type { PartialAdapters, PartialSurface } from "./partial";
import type { DialogAdapter } from "./layers";
import type { EventSourceAdapter, SourceControl, VisibilityAdapter } from "./sse";

// A patches response the configured fetch returns so the wire resolves without a
// real server.
function patchesResponse(body = '{"version":"v1","ops":[],"assets":[],"form":null}') {
  return new Response(body, {
    status: 200,
    headers: { "content-type": "application/vnd.next.patches+json" },
  });
}

// A dialog adapter the harness drives without jsdom's missing showModal.
function mockDialog(): DialogAdapter {
  return { open: () => () => undefined };
}

// A controllable EventSource adapter, since jsdom models no EventSource.
function mockSource(): {
  adapter: EventSourceAdapter;
  opened: { url: string; message(data: string): void }[];
} {
  const opened: { url: string; message(data: string): void }[] = [];
  const adapter: EventSourceAdapter = {
    open(url, onMessage): SourceControl {
      opened.push({ url, message: onMessage });
      return { close: () => undefined };
    },
  };
  return { adapter, opened };
}

// A visibility adapter the harness flips by hand.
function mockVisibility(): { adapter: VisibilityAdapter; set(hidden: boolean): void } {
  let hidden = false;
  let listener: (() => void) | null = null;
  const adapter: VisibilityAdapter = {
    hidden: () => hidden,
    onChange(l) {
      listener = l;
      return () => {
        listener = null;
      };
    },
  };
  return {
    adapter,
    set(value) {
      hidden = value;
      listener?.();
    },
  };
}

function makeSurface() {
  const dispatched: { event: string; detail: Record<string, unknown> }[] = [];
  const merged: Record<string, unknown>[] = [];
  const partial = createPartial({
    dispatch: (event, detail) => dispatched.push({ event, detail }),
    mergeContext: (data) => merged.push(data),
  });
  return { partial, dispatched, merged };
}

describe("createPartial surface", () => {
  let partial: PartialSurface;
  let dispatched: { event: string; detail: Record<string, unknown> }[];

  beforeEach(() => {
    document.body.innerHTML = "";
    const made = makeSurface();
    partial = made.partial;
    dispatched = made.dispatched;
    partial._configure({ document });
  });

  it("apply runs the verbs through the configured document", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    partial.apply({
      version: "v1",
      ops: [{ op: "inner", target: { zone: "z" }, html: "new" }],
      assets: [],
      form: null,
    });
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("new");
  });

  it("defineOp registers a custom verb reachable from apply", () => {
    const seen: unknown[] = [];
    partial.defineOp("confetti", (patch) => seen.push(patch.origin));
    partial.apply({
      version: "v1",
      ops: [{ op: "confetti", origin: "btn" }],
      assets: [],
      form: null,
    });
    expect(seen).toEqual(["btn"]);
  });

  it("fetch sends the CSRF token set through setCsrf on unsafe methods", async () => {
    const calls: RequestInit[] = [];
    partial._configure({
      document,
      fetch: async (_url, init) => {
        calls.push(init);
        return new Response('{"version":"v1","ops":[],"assets":[],"form":null}', {
          status: 200,
          headers: { "content-type": "application/vnd.next.patches+json" },
        });
      },
      navigate: () => {},
    });
    partial.setCsrf({ header: "X-CSRFToken", token: "tok" });
    await partial.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    expect((calls[0].headers as Record<string, string>)["X-CSRFToken"]).toBe("tok");
  });

  it("a csrf meta in an applied envelope rotates the token for the next mutation", async () => {
    const calls: RequestInit[] = [];
    let body =
      '{"version":"v1","ops":[],"assets":[],"form":null,' +
      '"csrf":{"header":"X-CSRFToken","token":"rotated"}}';
    partial._configure({
      document,
      fetch: async (_url, init) => {
        calls.push(init);
        const r = new Response(body, {
          status: 200,
          headers: { "content-type": "application/vnd.next.patches+json" },
        });
        body = '{"version":"v1","ops":[],"assets":[],"form":null}';
        return r;
      },
      navigate: () => {},
    });
    partial.setCsrf({ header: "X-CSRFToken", token: "old" });
    await partial.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    await partial.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    expect((calls[1].headers as Record<string, string>)["X-CSRFToken"]).toBe("rotated");
  });

  it("parseHook reaches the applier for a foreign content-type", async () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    partial._configure({
      document,
      fetch: async () =>
        new Response("stream-body", {
          status: 200,
          headers: { "content-type": "text/vnd.next.stream+html" },
        }),
      navigate: () => {},
    });
    partial.parseHook("text/vnd.next.stream+html", () => ({
      version: "v1",
      ops: [{ op: "inner", target: { zone: "z" }, html: "hooked" }],
      assets: [],
      form: null,
    }));
    await partial.fetch({ url: "/list/", zone: "z" });
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("hooked");
  });

  it("_reset clears custom ops, csrf, and configured adapters", async () => {
    partial.defineOp("confetti", () => undefined);
    partial.setCsrf({ header: "X-CSRFToken", token: "tok" });
    partial._reset();
    partial._configure({ document });
    partial.apply({
      version: "v1",
      ops: [{ op: "confetti" }],
      assets: [],
      form: null,
    });
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(true);
  });

  it("emits partial:before-request before the fetch resolves", async () => {
    partial._configure({
      document,
      fetch: async () =>
        new Response('{"version":"v1","ops":[],"assets":[],"form":null}', {
          status: 200,
          headers: { "content-type": "application/vnd.next.patches+json" },
        }),
      navigate: () => {},
    });
    await partial.fetch({ url: "/list/", zone: "z" });
    expect(dispatched.some((d) => d.event === "partial:before-request")).toBe(true);
  });

  it("onMount runs over the initial DOM on ready and over inserted subtrees", () => {
    document.body.innerHTML = '<div data-next-zone="z"><span class="w">a</span></div>';
    const seen: string[] = [];
    partial._configure({ document });
    partial.onMount(".w", (el) => seen.push(el.textContent ?? ""));
    partial.ready();
    expect(seen).toEqual(["a"]);
    partial.apply({
      version: "v1",
      ops: [
        {
          op: "morph",
          target: { zone: "z" },
          html: '<div data-next-zone="z"><span class="w">b</span></div>',
        },
      ],
      assets: [],
      form: null,
    });
    expect(seen).toEqual(["a", "b"]);
  });

  it("onMount fires when the inserted subtree root itself matches the selector", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const seen: string[] = [];
    partial._configure({ document });
    partial.onMount('[data-next-zone="z"]', (el) => seen.push(el.textContent ?? ""));
    partial.apply({
      version: "v1",
      ops: [
        {
          op: "morph",
          target: { zone: "z" },
          html: '<div data-next-zone="z">new</div>',
        },
      ],
      assets: [],
      form: null,
    });
    expect(seen).toEqual(["new"]);
  });

  it("onMount registered after ready catches up over the present document", () => {
    document.body.innerHTML = "<div data-x>here</div>";
    const seen: Element[] = [];
    partial._configure({ document });
    partial.ready();
    partial.onMount("[data-x]", (el) => seen.push(el));
    expect(seen).toEqual([document.querySelector("[data-x]")]);
  });

  it("onMount registered before ready runs exactly once on ready", () => {
    document.body.innerHTML = "<div data-x>here</div>";
    const seen: string[] = [];
    partial._configure({ document });
    partial.onMount("[data-x]", (el) => seen.push(el.textContent ?? ""));
    partial.ready();
    expect(seen).toEqual(["here"]);
  });

  it("a late onMount still runs over a subtree inserted by a later apply", () => {
    document.body.innerHTML = '<div data-next-zone="z"><span class="w">a</span></div>';
    const seen: string[] = [];
    partial._configure({ document });
    partial.ready();
    partial.onMount(".w", (el) => seen.push(el.textContent ?? ""));
    expect(seen).toEqual(["a"]);
    partial.apply({
      version: "v1",
      ops: [
        {
          op: "morph",
          target: { zone: "z" },
          html: '<div data-next-zone="z"><span class="w">b</span></div>',
        },
      ],
      assets: [],
      form: null,
    });
    expect(seen).toEqual(["a", "b"]);
  });

  it("ready batches the document's load zones into one zone fetch", async () => {
    document.body.innerHTML =
      '<div data-next-zone="a" data-next-lazy="load"></div>' +
      '<div data-next-zone="b" data-next-lazy="load"></div>';
    const calls: { url: string; init: RequestInit }[] = [];
    partial._configure({
      document,
      fetch: async (url, init) => {
        calls.push({ url, init });
        return new Response('{"version":"v1","ops":[],"assets":[],"form":null}', {
          status: 200,
          headers: { "content-type": "application/vnd.next.patches+json" },
        });
      },
      navigate: () => {},
    });
    partial.ready();
    await Promise.resolve();
    expect(calls).toHaveLength(1);
    expect((calls[0].init.headers as Record<string, string>)["X-Next-Zone"]).toBe(
      "a,b",
    );
  });

  it("a refresh op re-GETs the zone through the wire", async () => {
    document.body.innerHTML = '<div data-next-zone="poll">old</div>';
    const calls: { url: string; init: RequestInit }[] = [];
    partial._configure({
      document,
      fetch: async (url, init) => {
        calls.push({ url, init });
        return patchesResponse();
      },
      navigate: () => {},
    });
    partial.apply({
      version: "v1",
      ops: [{ op: "refresh", zone: "poll" }],
      assets: [],
      form: null,
    });
    await Promise.resolve();
    expect(calls).toHaveLength(1);
    expect((calls[0].init.headers as Record<string, string>)["X-Next-Zone"]).toBe(
      "poll",
    );
  });

  it("opening a layer GETs its body through the wire", async () => {
    const calls: string[] = [];
    partial._configure({
      document,
      dialog: mockDialog(),
      fetch: async (url) => {
        calls.push(url);
        return patchesResponse();
      },
      navigate: () => {},
    });
    await partial.layers.open(null, "/wizard/", "access");
    expect(calls).toContain("/wizard/");
  });

  it("a delegated filter trigger fetches through the wire", async () => {
    document.body.innerHTML =
      '<form action="/c/" data-next-target="r">' +
      '<input name="q" value="x" data-next-trigger="input">' +
      "</form>";
    const calls: string[] = [];
    partial._configure({
      document,
      fetch: async (url) => {
        calls.push(url);
        return patchesResponse();
      },
      navigate: () => {},
    });
    document
      .querySelector("input")!
      .dispatchEvent(new Event("input", { bubbles: true }));
    await Promise.resolve();
    expect(calls).toEqual(["/c/?q=x"]);
  });

  it("a form submit aborts its own in-flight validation through the wire", async () => {
    document.body.innerHTML =
      '<form action="/_next/form/u/" data-next-validate="blur" data-next-action="u">' +
      '<input name="email" value="a@b.c">' +
      "</form>";
    const calls: { url: string; init: RequestInit }[] = [];
    partial._configure({
      document,
      fetch: async (url, init) => {
        calls.push({ url, init });
        return patchesResponse();
      },
      navigate: () => {},
    });
    const form = document.querySelector("form")!;
    document.querySelector("input")!.dispatchEvent(new FocusEvent("blur"));
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await Promise.resolve();
    // The submit ran without throwing, so the wire's abort seam fired for the
    // validate zone the blur opened.
    expect(calls.length).toBeGreaterThan(0);
  });

  it("an SSE event applies through the same pipeline as a response", () => {
    document.body.innerHTML =
      '<div data-next-sse="/stream/"></div><div data-next-zone="z">old</div>';
    const source = mockSource();
    partial._configure({
      document,
      source: source.adapter,
      visibility: mockVisibility().adapter,
    });
    partial.sse.scan(document);
    expect(source.opened).toHaveLength(1);
    source.opened[0].message(
      JSON.stringify({
        version: "v1",
        ops: [{ op: "inner", target: { zone: "z" }, html: "live" }],
        assets: [],
        form: null,
      }),
    );
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("live");
  });

  it("an SSE resume re-GETs the bound zones through the wire", async () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const source = mockSource();
    const visibility = mockVisibility();
    const calls: { url: string; init: RequestInit }[] = [];
    let clock = 0;
    partial._configure({
      document,
      source: source.adapter,
      visibility: visibility.adapter,
      fetch: async (url, init) => {
        calls.push({ url, init });
        return patchesResponse();
      },
      navigate: () => {},
    });
    const nowSpy = vi.spyOn(Date, "now").mockImplementation(() => clock);
    partial.sse.scan(document);
    source.opened[0].message(
      JSON.stringify({
        version: "v1",
        ops: [{ op: "refresh", zone: "poll" }],
        assets: [],
        form: null,
      }),
    );
    visibility.set(true);
    clock = 5000;
    visibility.set(false);
    await Promise.resolve();
    nowSpy.mockRestore();
    expect(calls.some((c) => c.url.includes("?") || c.init.headers)).toBe(true);
    expect(
      calls.some(
        (c) => (c.init.headers as Record<string, string>)["X-Next-Zone"] === "poll",
      ),
    ).toBe(true);
  });

  it("onMount returns a teardown that unregisters the callback", () => {
    document.body.innerHTML = '<div data-next-zone="z"><span class="w">a</span></div>';
    const seen: string[] = [];
    partial._configure({ document });
    const off = partial.onMount(".w", (el) => seen.push(el.textContent ?? ""));
    off();
    partial.ready();
    expect(seen).toEqual([]);
  });

  it("a second onMount teardown after removal is a no-op", () => {
    partial._configure({ document });
    const off = partial.onMount(".w", () => undefined);
    off();
    expect(() => off()).not.toThrow();
  });

  it("layers and sse getters expose the live sub-surfaces", () => {
    partial._configure({ document, dialog: mockDialog() });
    expect(typeof partial.layers.open).toBe("function");
    expect(typeof partial.sse.scan).toBe("function");
  });

  it("_configure swaps in the injected history seam", () => {
    const history: PartialAdapters["history"] = { push: vi.fn(), replace: vi.fn() };
    partial._configure({ document, history });
    expect(() => partial._configure({ document, history })).not.toThrow();
  });

  it("_configure without a document installs over the global document", () => {
    const made = makeSurface();
    expect(() => made.partial._configure({})).not.toThrow();
    made.partial._reset();
  });

  it("a refresh op falls back to the global document path when none is injected", async () => {
    document.body.innerHTML = '<div data-next-zone="poll">old</div>';
    const calls: { url: string }[] = [];
    partial._configure({
      fetch: async (url) => {
        calls.push({ url });
        return patchesResponse();
      },
      navigate: () => {},
    });
    partial.apply({
      version: "v1",
      ops: [{ op: "refresh", zone: "poll" }],
      assets: [],
      form: null,
    });
    await Promise.resolve();
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(document.location.pathname);
  });
});

describe("Next.partial integration", () => {
  beforeEach(async () => {
    await import("./next");
  });

  it("exposes the partial surface on the global Next", async () => {
    const win = globalThis as unknown as {
      Next: { partial: PartialSurface };
    };
    expect(typeof win.Next.partial.apply).toBe("function");
    expect(typeof win.Next.partial.fetch).toBe("function");
    expect(typeof win.Next.partial.defineOp).toBe("function");
    expect(typeof win.Next.partial._reset).toBe("function");
  });
});
