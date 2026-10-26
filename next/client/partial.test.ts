import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPartial } from "./partial";
import { createDiagnostics } from "./diagnostics";
import type { PartialAdapters, PartialDeps, PartialSurface } from "./partial";
import type { DialogAdapter } from "./layers";
import { createSse } from "./sse";
import type { EventSourceAdapter, SourceControl, SseFactory, StreamMeta } from "./sse";
import type { WebSocketAdapter, WsTransportFactory } from "./ws";
import {
  manualPollClock,
  manualVisibility,
  chunkModules,
  landed,
  unloaded,
} from "./test-doubles";

// A patches response the fetch returns so the wire resolves without a server.
function patchesResponse(body = '{"version":"v1","ops":[],"assets":[],"form":null}') {
  return new Response(body, {
    status: 200,
    headers: { "content-type": "application/vnd.next.patches+json" },
  });
}

const ENVELOPE_OK = JSON.stringify({ version: "v1", ops: [] });

// A dialog adapter the harness drives without jsdom's missing showModal.
function mockDialog(): DialogAdapter {
  return { open: () => () => undefined };
}

// A controllable EventSource adapter, since jsdom models no EventSource.
function mockSource(): {
  adapter: EventSourceAdapter;
  opened: {
    url: string;
    message(data: string): void;
    control(name: string, data: string): void;
    closed: boolean;
  }[];
} {
  const opened: {
    url: string;
    message(data: string): void;
    control(name: string, data: string): void;
    closed: boolean;
  }[] = [];
  const adapter: EventSourceAdapter = {
    open(url, onMessage, _onError, onControl): SourceControl {
      const connection = {
        url,
        message: onMessage,
        control: (name: string, data: string) => onControl?.(name, data),
        closed: false,
      };
      opened.push(connection);
      return {
        close() {
          connection.closed = true;
        },
      };
    },
  };
  return { adapter, opened };
}

function makeSurface(context: Record<string, unknown> = {}) {
  const dispatched: { event: string; detail: Record<string, unknown> }[] = [];
  const merged: Record<string, unknown>[] = [];
  const partial = createPartial({
    ...chunkModules,
    context: () => context,
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

  it("a user-toggled details survives an unrequested morph patch", () => {
    document.body.innerHTML =
      '<div data-next-zone="z"><details id="d" open></details></div>';
    const details = document.querySelector<HTMLDetailsElement>("#d")!;
    // Stamps the element as user-owned, so the morph keeps its open state.
    details.dispatchEvent(new Event("toggle"));
    partial.apply({
      version: "v1",
      ops: [
        {
          op: "morph",
          target: { zone: "z" },
          html: '<div data-next-zone="z"><details id="d"></details></div>',
        },
      ],
      assets: [],
      form: null,
    });
    expect(document.querySelector("#d")!.hasAttribute("open")).toBe(true);
  });

  it("reads the history and navigation seams of the latest configuration", () => {
    const first: string[] = [];
    const second: string[] = [];
    const visited: string[] = [];
    partial._configure({
      document,
      history: { push: (href) => first.push(href), replace: () => undefined },
    });
    partial._configure({
      document,
      history: { push: (href) => second.push(href), replace: () => undefined },
      navigate: (url) => visited.push(url),
    });
    partial.apply({
      version: "v1",
      ops: [
        { op: "url", href: "/second/" },
        { op: "visit", href: "/away/" },
      ],
      assets: [],
      form: null,
    });
    expect(first).toEqual([]);
    expect(second).toEqual(["/second/"]);
    expect(visited).toEqual(["/away/"]);
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

  it("fires a wire error on the document as well as the bus", async () => {
    const onDocument = vi.fn();
    document.addEventListener("partial:error", onDocument);
    await partial.fetch({ url: "https://attacker.example/x/" });
    document.removeEventListener("partial:error", onDocument);
    expect((onDocument.mock.calls[0]![0] as CustomEvent).detail).toMatchObject({
      kind: "network",
    });
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
    expect(new Headers(calls[0]!.headers).get("X-CSRFToken")).toBe("tok");
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
    expect(new Headers(calls[1]!.headers).get("X-CSRFToken")).toBe("rotated");
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

  it("parseHook decodes stream frames with the backend content type", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const { partial } = makeSurface({
      $realtime: { content_type: "text/vnd.next.stream+html; charset=utf-8" },
    });
    const source = mockSource();
    partial._configure({ document, source: source.adapter });
    partial.parseHook("text/vnd.next.stream+html", () => ({
      version: "v1",
      ops: [{ op: "inner", target: { zone: "z" }, html: "streamed" }],
      assets: [],
      form: null,
    }));
    document.body.insertAdjacentHTML(
      "beforeend",
      '<div data-next-stream="/stream/"></div>',
    );
    partial.stream.scan(document);
    source.opened[0]!.message("stream-body");
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe(
      "streamed",
    );
  });

  it("preserves stream metadata when parseHook unwraps a frame", () => {
    const { partial } = makeSurface({
      $realtime: { content_type: "text/vnd.next.stream+html" },
    });
    const source = mockSource();
    partial._configure({ document, source: source.adapter });
    partial.parseHook("text/vnd.next.stream+html", (_response, body) => {
      const wrapped = JSON.parse(body) as {
        envelope: Record<string, unknown>;
      };
      return wrapped.envelope;
    });
    document.body.insertAdjacentHTML(
      "beforeend",
      '<div data-next-stream="/stream/"></div>',
    );
    const received: unknown[] = [];
    partial.stream.on("updates", "item", (detail, meta) =>
      received.push({ detail, meta }),
    );
    partial.stream.scan(document);
    source.opened[0]!.message(
      JSON.stringify({
        envelope: {
          version: "v1",
          ops: [{ op: "event", name: "item", detail: { id: 3 } }],
          assets: [],
          form: null,
          topic: "updates",
          epoch: "a1",
          seq: 7,
          server_time: 1700000000,
        },
      }),
    );

    expect(received).toEqual([
      {
        detail: { id: 3 },
        meta: { topic: "updates", epoch: "a1", seq: 7, serverTime: 1700000000 },
      },
    ]);
  });

  it.each(["{}", "[]", '"text"'])(
    "reports a well-typed response whose JSON %s is no envelope as a parse error",
    async (body) => {
      partial._configure({ document, fetch: async () => patchesResponse(body) });
      await expect(partial.fetch({ url: "/list/", zone: "z" })).resolves.toMatchObject({
        status: "error",
        error: { kind: "parse", body },
      });
      const errors = dispatched.filter((d) => d.event === "partial:error");
      expect(errors.map((d) => d.detail)).toEqual([
        { kind: "parse", body, error: expect.any(TypeError) },
      ]);
    },
  );

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
    expect(new Headers(calls[0]!.init.headers).get("X-Next-Zone")).toBe("a,b");
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
    expect(new Headers(calls[0]!.init.headers).get("X-Next-Zone")).toBe("poll");
  });

  it("_configure stops the pollers the previous configuration armed", async () => {
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const clock = manualPollClock();
    const calls: string[] = [];
    const fetch = async (url: string) => {
      calls.push(url);
      return patchesResponse();
    };
    partial._configure({ document, clock, fetch, navigate: () => {} });
    partial.ready();
    clock.tick();
    await Promise.resolve();
    expect(calls).toHaveLength(1);
    partial._configure({ document, clock, fetch, navigate: () => {} });
    clock.tick();
    await Promise.resolve();
    expect(calls).toHaveLength(1);
  });

  it("_configure drops the asset watch the previous configuration armed", () => {
    Object.defineProperty(document, "readyState", {
      value: "loading",
      configurable: true,
    });
    const forget = vi.spyOn(document, "removeEventListener");
    partial._configure({ document });
    partial.ready();
    partial._configure({ document });
    Reflect.deleteProperty(document, "readyState");
    expect(forget.mock.calls.some(([type]) => type === "DOMContentLoaded")).toBe(true);
  });

  it("_reset drops the asset watch on the current document", () => {
    Object.defineProperty(document, "readyState", {
      value: "loading",
      configurable: true,
    });
    partial._configure({ document });
    partial.ready();
    const forget = vi.spyOn(document, "removeEventListener");
    partial._reset();
    Reflect.deleteProperty(document, "readyState");
    expect(forget.mock.calls.some(([type]) => type === "DOMContentLoaded")).toBe(true);
  });

  it("a poll tick GETs the host page while a layer holds the address bar", async () => {
    window.history.replaceState(null, "", "/host/");
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const clock = manualPollClock();
    const calls: string[] = [];
    partial._configure({
      document,
      clock,
      dialog: mockDialog(),
      fetch: async (url) => {
        calls.push(url);
        return patchesResponse();
      },
      navigate: () => {},
    });
    partial.ready();
    await partial.layers.open(null, "/modal/", "m");
    expect(window.location.pathname).toBe("/modal/");
    calls.length = 0;
    clock.tick();
    await Promise.resolve();
    expect(calls).toEqual([`${location.origin}/host/`]);
    partial.layers._reset();
    window.history.replaceState(null, "", "/");
  });

  it("keeps every zone GET of a double-slash page on its own origin", async () => {
    window.history.replaceState(null, "", `${location.origin}//attacker.example/x/`);
    document.body.innerHTML =
      '<div data-next-zone="late" data-next-lazy="load"></div>' +
      '<div data-next-zone="tick" data-next-poll="5000"></div>' +
      '<button data-next-accepted="tick">open</button>';
    const clock = manualPollClock();
    const calls: { url: string; init: RequestInit }[] = [];
    partial._configure({
      document,
      clock,
      dialog: mockDialog(),
      fetch: async (url, init) => {
        calls.push({ url, init });
        return patchesResponse();
      },
      navigate: () => {},
    });
    partial.ready();
    clock.tick();
    await partial.layers.open(document.querySelector("button"), "/modal/", "m");
    partial.layers.close({ result: null });
    await Promise.resolve();
    const page = `${location.origin}//attacker.example/x/`;
    expect(calls.map((call) => call.url)).toEqual([
      page,
      page,
      `${location.origin}/modal/`,
      page,
    ]);
    for (const call of calls) {
      expect(new URL(call.url).origin).toBe(location.origin);
      expect(call.init.mode).toBe("same-origin");
    }
    const origin = new Headers(calls[3]!.init.headers).get("X-Next-Origin");
    expect(origin).toBe("//attacker.example/x/");
    partial.layers._reset();
    window.history.replaceState(null, "", "/");
  });

  it("a host-page zone GET morphs the base zone, not an open layer's twin", async () => {
    window.history.replaceState(null, "", "/host/");
    document.body.innerHTML = '<div data-next-zone="dup" id="base">stale</div>';
    partial._configure({
      document,
      dialog: mockDialog(),
      fetch: async (url) => {
        const html =
          url === `${location.origin}/host/`
            ? '<div data-next-zone="dup" id="base">host</div>'
            : '<div data-next-zone="dup">modal</div>';
        return patchesResponse(
          JSON.stringify({
            version: "v1",
            ops: [{ op: "morph", target: { zone: "dup" }, html }],
            assets: [],
            form: null,
          }),
        );
      },
      navigate: () => {},
    });
    await partial.layers.open(null, "/modal/", "dup");
    const layerZone = document.querySelector('dialog [data-next-zone="dup"]')!;
    expect(layerZone.textContent).toBe("modal");
    await partial.fetch({ url: "/host/", zone: "dup" });
    expect(document.querySelector("#base")!.textContent).toBe("host");
    expect(layerZone.textContent).toBe("modal");
    partial.layers._reset();
    window.history.replaceState(null, "", "/");
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
    expect(calls).toContain(`${location.origin}/wizard/`);
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
    expect(calls).toEqual([`${location.origin}/c/?q=x`]);
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
    // The submit ran without throwing, so the wire's abort seam fired.
    expect(calls.length).toBeGreaterThan(0);
  });

  it("fetches the stream chunk only once a scan finds a stream, then opens it", () => {
    const stream = unloaded<SseFactory>("stream");
    const lazy = createPartial({
      ...chunkModules,
      stream,
      dispatch: () => undefined,
      mergeContext: () => undefined,
    });
    const source = mockSource();
    lazy._configure({
      document,
      source: source.adapter,
      visibility: manualVisibility(),
      websocket: { open: () => ({ close: () => undefined }) },
    });
    document.head.innerHTML = "";
    document.body.innerHTML = '<div data-next-zone="z"></div>';
    lazy.stream.scan(document);
    expect(stream.tags()).toBe(0);
    document.body.innerHTML = '<div data-next-stream="/stream/"></div>';
    lazy.stream.scan(document);
    expect(lazy.stream.size()).toBe(0);
    expect(stream.tags()).toBe(1);
    stream.land(createSse);
    expect(source.opened).toHaveLength(1);
    expect(lazy.stream.size()).toBe(1);
    lazy._reset();
  });

  it("builds the stream bridge from default seams when the page is not configured", async () => {
    vi.stubGlobal(
      "EventSource",
      class {
        onopen: ((event: Event) => void) | null = null;
        onerror: ((event: Event) => void) | null = null;
        addEventListener(): void {}
        close(): void {}
      },
    );
    const modules: PartialDeps = {
      ...chunkModules,
      dispatch: () => undefined,
      mergeContext: () => undefined,
    };
    delete modules.ws;
    const lazy = createPartial({
      ...modules,
      context: () => ({}),
      dispatch: () => undefined,
      mergeContext: () => undefined,
    });
    document.body.innerHTML = '<div data-next-stream="/stream/"></div>';
    lazy.stream.scan(document);
    await Promise.resolve();
    await Promise.resolve();
    expect(lazy.stream.size()).toBe(1);
    lazy._reset();
    vi.unstubAllGlobals();
  });

  it.each([
    ["no $realtime", {}],
    ["a $realtime without content_type", { $realtime: { transports: ["sse"] } }],
  ])("parseHook decodes stream frames with the stock content type on %s", (_, ctx) => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const { partial } = makeSurface(ctx);
    const source = mockSource();
    partial._configure({ document, source: source.adapter });
    partial.parseHook("application/vnd.next.patches+json", () => ({
      version: "v1",
      ops: [{ op: "inner", target: { zone: "z" }, html: "stock" }],
      assets: [],
      form: null,
    }));
    document.body.insertAdjacentHTML(
      "beforeend",
      '<div data-next-stream="/stream/"></div>',
    );
    partial.stream.scan(document);
    source.opened[0]!.message("stream-body");
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("stock");
  });

  it.each([
    ["no $realtime", {}],
    ["an SSE-only $realtime", { $realtime: { transports: ["sse"] } }],
  ])("opens SSE without fetching the ws chunk on %s", (_, ctx) => {
    const ws = unloaded<WsTransportFactory>("ws");
    const lazy = createPartial({
      ...chunkModules,
      context: () => ctx,
      ws,
      dispatch: () => undefined,
      mergeContext: () => undefined,
    });
    const source = mockSource();
    lazy._configure({ document, source: source.adapter });
    document.head.innerHTML = "";
    document.body.innerHTML = '<div data-next-stream="/stream/"></div>';
    lazy.stream.scan(document);
    expect(ws.tags()).toBe(0);
    expect(source.opened).toHaveLength(1);
    lazy._reset();
  });

  it("loads the stream chunk from the transport-neutral attribute", () => {
    const stream = unloaded<SseFactory>("stream");
    const lazy = createPartial({
      ...chunkModules,
      stream,
      dispatch: () => undefined,
      mergeContext: () => undefined,
    });
    const source = mockSource();
    lazy._configure({ document, source: source.adapter });
    document.head.innerHTML = "";
    document.body.innerHTML = '<div data-next-stream="/stream/"></div>';
    lazy.stream.scan(document);
    expect(stream.tags()).toBe(1);
    stream.land(createSse);
    expect(source.opened).toHaveLength(1);
    lazy._reset();
  });

  it("waits for the websocket module before loading the stream bridge", async () => {
    const source = mockSource();
    let usedWebSocket = false;
    const websocket: WsTransportFactory = (deps) => ({
      up: vi.fn(),
      http: vi.fn(),
      renew: vi.fn(),
      tag: vi.fn(),
      name: "ws",
      open(url, sink, lastSeq) {
        usedWebSocket = true;
        return deps.fallback.open(url, sink, lastSeq);
      },
    });
    const lazy = createPartial({
      ...chunkModules,
      context: () => ({ $realtime: { transports: ["ws"] } }),
      ws: landed(websocket),
      dispatch: () => undefined,
      mergeContext: () => undefined,
    });
    lazy._configure({
      document,
      source: source.adapter,
      visibility: manualVisibility(),
    });
    document.body.innerHTML = '<div data-next-stream="/stream/"></div>';
    lazy.stream.scan(document);
    await Promise.resolve();
    await Promise.resolve();
    expect(usedWebSocket).toBe(true);
    expect(source.opened).toHaveLength(1);
    lazy._reset();
  });

  it("hands the request ids sent before the bridge to it, so their echoes drop", () => {
    const stream = unloaded<SseFactory>("stream");
    const applied: Record<string, unknown>[] = [];
    const lazy = createPartial({
      ...chunkModules,
      stream,
      dispatch: (event, detail) => {
        if (event === "partial:applied") applied.push(detail);
      },
      mergeContext: () => undefined,
    });
    const source = mockSource();
    lazy._configure({
      document,
      source: source.adapter,
      visibility: manualVisibility(),
    });
    document.body.innerHTML =
      '<div data-next-stream="/stream/"></div><div data-next-zone="z">old</div>';
    lazy.stream.scan(document);
    // Only the last ECHO_LIMIT ids are kept, as the bridge's own ring keeps them.
    for (let i = 0; i < 26; i += 1) lazy.stream.remember(`r${i}`);
    stream.land(createSse);
    const event = (id: string): string =>
      JSON.stringify({
        version: "v1",
        request_id: id,
        ops: [{ op: "inner", target: { zone: "z" }, html: id }],
      });
    source.opened[0]!.message(event("r25"));
    expect(applied).toEqual([]);
    source.opened[0]!.message(event("r0"));
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("r0");
    lazy._reset();
  });

  it("keeps one bridge across scans and feeds it later ids directly", () => {
    const lazy = createPartial({
      ...chunkModules,
      dispatch: () => undefined,
      mergeContext: () => undefined,
    });
    const source = mockSource();
    lazy._configure({
      document,
      source: source.adapter,
      visibility: manualVisibility(),
    });
    document.body.innerHTML =
      '<div data-next-stream="/stream/"></div><div data-next-zone="z">old</div>';
    lazy.stream.scan(document);
    lazy.stream.scan(document);
    expect(source.opened).toHaveLength(1);
    lazy.stream.remember("own");
    source.opened[0]!.message(
      JSON.stringify({
        version: "v1",
        request_id: "own",
        ops: [{ op: "inner", target: { zone: "z" }, html: "echo" }],
      }),
    );
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("old");
    lazy._reset();
  });

  it("a stream chunk landing after a reset scans nothing for the dead pass", () => {
    const stream = unloaded<SseFactory>("stream");
    const lazy = createPartial({
      ...chunkModules,
      stream,
      dispatch: () => undefined,
      mergeContext: () => undefined,
    });
    const source = mockSource();
    lazy._configure({
      document,
      source: source.adapter,
      visibility: manualVisibility(),
    });
    document.body.innerHTML =
      '<div data-next-stream="/stream/"></div><div data-next-zone="z">old</div>';
    lazy.stream.scan(document);
    lazy.stream.remember("stale");
    lazy._reset();
    stream.land(createSse);
    expect(source.opened).toEqual([]);
    lazy.stream.scan(document);
    expect(source.opened).toHaveLength(1);
    // The reset dropped the buffered id with the rest of the bridge state.
    source.opened[0]!.message(
      JSON.stringify({
        version: "v1",
        request_id: "stale",
        ops: [{ op: "inner", target: { zone: "z" }, html: "fresh" }],
      }),
    );
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("fresh");
    lazy._reset();
  });

  it("an SSE event applies through the same pipeline as a response", () => {
    document.body.innerHTML =
      '<div data-next-stream="/stream/"></div><div data-next-zone="z">old</div>';
    const source = mockSource();
    partial._configure({
      document,
      source: source.adapter,
      visibility: manualVisibility(),
    });
    partial.stream.scan(document);
    expect(source.opened).toHaveLength(1);
    source.opened[0]!.message(
      JSON.stringify({
        version: "v1",
        ops: [{ op: "inner", target: { zone: "z" }, html: "live" }],
        assets: [],
        form: null,
      }),
    );
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("live");
  });

  it("a completed stream resync does not abort a later GET on teardown", async () => {
    window.history.replaceState(null, "", "/");
    document.body.innerHTML =
      '<div data-next-stream="/stream/"></div><div data-next-zone="z">old</div>';
    const source = mockSource();
    const signals: AbortSignal[] = [];
    let finishLatest: (() => void) | undefined;
    partial._configure({
      document,
      source: source.adapter,
      visibility: manualVisibility(),
      fetch: async (_url, init) => {
        signals.push(init.signal!);
        if (signals.length === 1) {
          return patchesResponse(
            JSON.stringify({
              version: "v1",
              ops: [{ op: "inner", target: { zone: "z" }, html: "synced" }],
              assets: [],
              form: null,
            }),
          );
        }
        return new Promise<Response>((resolve) => {
          finishLatest = () => resolve(patchesResponse());
        });
      },
      navigate: () => undefined,
    });
    partial.stream.scan(document);
    source.opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "e", seq: 1 } } }),
    );
    await vi.waitFor(() => expect(signals).toHaveLength(1));
    await vi.waitFor(() =>
      expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe(
        "synced",
      ),
    );
    await Promise.resolve();
    const newer = partial.fetch({ url: "/", zone: "z" });
    await vi.waitFor(() => expect(signals).toHaveLength(2));
    document.querySelector('[data-next-stream="/stream/"]')!.remove();
    await vi.waitFor(() => expect(source.opened[0]!.closed).toBe(true));
    expect(signals[1]!.aborted).toBe(false);
    finishLatest!();
    await newer;
  });

  it("teardown aborts a still pending stream resync", async () => {
    window.history.replaceState(null, "", "/");
    document.body.innerHTML =
      '<div data-next-stream="/stream/"></div><div data-next-zone="z">old</div>';
    const source = mockSource();
    let signal: AbortSignal | undefined;
    partial._configure({
      document,
      source: source.adapter,
      visibility: manualVisibility(),
      fetch: (_url, init) => {
        signal = init.signal!;
        return new Promise<Response>((resolve) => {
          signal!.addEventListener("abort", () => resolve(patchesResponse()), {
            once: true,
          });
        });
      },
      navigate: () => undefined,
    });
    partial.stream.scan(document);
    source.opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "e", seq: 1 } } }),
    );
    await vi.waitFor(() => expect(signal).toBeDefined());
    document.querySelector('[data-next-stream="/stream/"]')!.remove();
    await vi.waitFor(() => expect(source.opened[0]!.closed).toBe(true));
    expect(signal!.aborted).toBe(true);
  });

  it("a host meta from a stream or a zone GET under a layer survives its close", async () => {
    window.history.replaceState(null, "", "/inbox/");
    document.title = "Inbox (0)";
    document.body.innerHTML = '<div data-next-stream="/stream/"></div>';
    const source = mockSource();
    const meta = (title: string) =>
      JSON.stringify({
        version: "v1",
        ops: [{ op: "meta", title }],
        assets: [],
        form: null,
      });
    partial._configure({
      document,
      dialog: mockDialog(),
      source: source.adapter,
      visibility: manualVisibility(),
      fetch: async (url) =>
        patchesResponse(meta(url.endsWith("/inbox/7/") ? "Mail 7" : "Inbox (4)")),
    });
    partial.stream.scan(document);
    await partial.layers.open(null, "/inbox/7/", "mail");
    expect(document.title).toBe("Mail 7");
    source.opened[0]!.message(meta("Inbox (3)"));
    expect(document.title).toBe("Mail 7");
    partial.layers.close({ result: 1 });
    expect(document.title).toBe("Inbox (3)");
    await partial.layers.open(null, "/inbox/7/", "mail");
    await partial.fetch({ url: "/inbox/", zone: "list" });
    expect(document.title).toBe("Mail 7");
    partial.layers.close({ result: 1 });
    expect(document.title).toBe("Inbox (4)");
  });

  it("an SSE resume re-GETs the bound zones through the wire", async () => {
    document.body.innerHTML = '<div data-next-stream="/stream/"></div>';
    const source = mockSource();
    const visibility = manualVisibility();
    const calls: { url: string; init: RequestInit }[] = [];
    let clock = 0;
    partial._configure({
      document,
      source: source.adapter,
      visibility,
      fetch: async (url, init) => {
        calls.push({ url, init });
        return patchesResponse();
      },
      navigate: () => {},
    });
    const nowSpy = vi.spyOn(Date, "now").mockImplementation(() => clock);
    partial.stream.scan(document);
    source.opened[0]!.message(
      JSON.stringify({
        version: "v1",
        ops: [{ op: "refresh", zone: "poll" }],
        assets: [],
        form: null,
      }),
    );
    await Promise.resolve();
    // Drop the refresh op's own fetch, so the next call can only be the revalidation.
    calls.length = 0;
    visibility.setHidden(true);
    clock = 5000;
    visibility.setHidden(false);
    await Promise.resolve();
    nowSpy.mockRestore();
    expect(
      calls.some((c) => new Headers(c.init.headers).get("X-Next-Zone") === "poll"),
    ).toBe(true);
  });

  it("the dev channel reaches the applier's boundary diagnostics", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "debug").mockImplementation(() => {});
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const wire = {
      version: "v1",
      ops: [null, { op: "inner", target: { zone: "z" }, html: "new" }],
      assets: [],
      form: null,
    };
    partial._configure({ document, dev: true, diagnostics: createDiagnostics() });
    partial.apply(wire);
    expect(warn).toHaveBeenCalledWith("[next] dropped malformed ops: 1");
    warn.mockClear();
    partial._configure({ document });
    partial.apply(wire);
    expect(warn).not.toHaveBeenCalled();
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("new");
  });

  it("opening the dev channel keeps the ops registered before it", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const seen: unknown[] = [];
    // A verb registered before the dev channel opens must survive it.
    partial.defineOp("confetti", (patch) => seen.push(patch.origin));
    partial._configure({ dev: true, diagnostics: createDiagnostics() });
    partial.apply({
      version: "v1",
      ops: [null, { op: "confetti", origin: "btn" }],
      assets: [],
      form: null,
    });
    expect(seen).toEqual(["btn"]);
    // The dropped-ops warning proves the channel really opened.
    expect(warn).toHaveBeenCalledWith("[next] dropped malformed ops: 1");
  });

  it("opening the dev channel keeps the parse hooks registered before it", async () => {
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
    partial._configure({ dev: true });
    await partial.fetch({ url: "/list/", zone: "z" });
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("hooked");
  });

  it("opening the dev channel keeps the nonce taken at the bootstrap", () => {
    document.head.innerHTML = "";
    const boot = document.createElement("script");
    boot.nonce = "nonce-7a3f";
    Object.defineProperty(document, "currentScript", {
      value: boot,
      configurable: true,
    });
    const made = makeSurface();
    // The inline `_init` opens the channel carrying no nonce of its own.
    Object.defineProperty(document, "currentScript", {
      value: document.createElement("script"),
      configurable: true,
    });
    made.partial._configure({ dev: true });
    made.partial.apply({
      version: "v1",
      ops: [],
      assets: [{ kind: "css", url: "", inline: ".z{}", load: "link" }],
      form: null,
    });
    Object.defineProperty(document, "currentScript", {
      value: null,
      configurable: true,
    });
    expect(document.head.querySelector("style")!.nonce).toBe("nonce-7a3f");
    made.partial._reset();
  });

  it("the dev channel reaches the trigger attribute diagnostics", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="soon"></div>';
    partial._configure({ document, dev: true, diagnostics: createDiagnostics() });
    partial.ready();
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining('data-next-poll="soon" is not a whole number'),
    );
    warn.mockClear();
    partial._configure({ document });
    partial.ready();
    expect(warn).not.toHaveBeenCalled();
  });

  it("a dev chunk landing after ready validates the mounted page once", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    document.body.innerHTML = '<div data-next-zone="t" data-next-lazy="soon"></div>';
    partial._configure({ document, dev: true });
    partial.ready();
    expect(warn).not.toHaveBeenCalled();
    const diagnostics = createDiagnostics();
    partial._configure({ dev: true, diagnostics });
    partial._configure({ dev: true });
    expect(warn).toHaveBeenCalledOnce();
    expect(warn.mock.calls[0]![0]).toContain('data-next-lazy="soon"');
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

  it("layers and stream getters expose the live sub-surfaces", () => {
    partial._configure({
      document,
      dialog: mockDialog(),
      source: mockSource().adapter,
      visibility: manualVisibility(),
    });
    expect(typeof partial.layers.open).toBe("function");
    expect(typeof partial.stream.scan).toBe("function");
    expect(partial.stream.offset()).toBe(0);
  });

  it("exposes topic listeners, stream metadata, and clock through the lazy facade", () => {
    document.body.innerHTML = '<div data-next-stream="/stream/"></div>';
    const source = mockSource();
    partial._configure({
      document,
      source: source.adapter,
      visibility: manualVisibility(),
    });
    const received: {
      detail: Record<string, unknown>;
      meta: StreamMeta;
      current: StreamMeta | undefined;
    }[] = [];
    expect(partial.stream.now()).toBeGreaterThan(0);
    partial.stream.remember("queued-id");
    const off = partial.stream.on("room:1", "room:update", (detail, meta) => {
      received.push({ detail, meta, current: partial.stream.meta() });
    });
    partial.stream.on("room:1", "another", () => undefined);
    partial.stream.remember("live-id");
    expect(partial.stream.now()).toBeGreaterThan(0);
    partial.stream.scan(document);
    source.opened[0]!.message(
      JSON.stringify({
        version: "v1",
        ops: [{ op: "event", name: "room:update", detail: { count: 2 } }],
        assets: [],
        form: null,
        topic: "room:1",
        seq: 1,
        server_time: Date.now(),
      }),
    );
    expect(received[0]?.detail).toEqual({ count: 2 });
    expect(received[0]?.meta).toMatchObject({ topic: "room:1", seq: 1 });
    expect(received[0]?.meta.connection).toBeUndefined();
    expect(received[0]?.current).toEqual(received[0]?.meta);
    expect(partial.stream.meta()).toBeUndefined();
    off();
    partial._reset();
  });

  it("ignores a stream chunk that lands after its scan was reset", () => {
    const streamChunk = unloaded<SseFactory>("stream");
    const late = createPartial({
      ...chunkModules,
      stream: streamChunk,
      dispatch: () => undefined,
      mergeContext: () => undefined,
    });
    const source = mockSource();
    late._configure({
      document,
      source: source.adapter,
      visibility: manualVisibility(),
    });
    document.body.innerHTML = '<div data-next-stream="/stream/"></div>';
    late.stream.scan(document);
    late._reset();
    streamChunk.land(createSse);
    expect(source.opened).toEqual([]);
  });

  it("ignores a stream listener whose lazy chunk lands after reset", () => {
    const stream = unloaded<SseFactory>("stream");
    const lazy = createPartial({
      ...chunkModules,
      stream,
      dispatch: () => undefined,
      mergeContext: () => undefined,
    });
    const seen: unknown[] = [];
    lazy.stream.on("room:1", "room:update", (...args) => seen.push(args));
    lazy._reset();
    stream.land(createSse);
    expect(seen).toEqual([]);
  });

  it("_configure swaps in the injected history seam", () => {
    const history: PartialAdapters["history"] = { push: vi.fn(), replace: vi.fn() };
    partial._configure({ document, history });
    expect(() => partial._configure({ document, history })).not.toThrow();
  });

  it("_configure naming no seam at all rebuilds nothing", () => {
    const made = makeSurface();
    const seen: unknown[] = [];
    made.partial.defineOp("confetti", (patch) => seen.push(patch.origin));
    made.partial._configure({});
    made.partial.apply({
      version: "v1",
      ops: [{ op: "confetti", origin: "btn" }],
      assets: [],
      form: null,
    });
    expect(seen).toEqual(["btn"]);
    made.partial._reset();
  });

  it("a refresh op falls back to the global document path and query when none is injected", async () => {
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
    const { origin, pathname, search } = document.location;
    expect(calls[0]!.url).toBe(origin + pathname + search);
  });
});

describe("Next.partial.submit", () => {
  const FORM =
    '<div data-next-zone="z">old</div>' +
    '<form action="/_next/form/u/" data-next-action="u" data-next-target="z">' +
    '<input name="title" value="x"></form>';

  function answering(...bodies: string[]) {
    const made = makeSurface();
    const calls: { url: string; init: RequestInit }[] = [];
    const replies: ((r: Response) => void)[] = [];
    made.partial._configure({
      document,
      navigate: () => undefined,
      fetch: (url, init) => {
        calls.push({ url, init });
        const body = bodies.shift();
        return body === undefined
          ? new Promise<Response>((resolve) => replies.push(resolve))
          : Promise.resolve(patchesResponse(body));
      },
    });
    const fields = (n: number): [string, FormDataEntryValue][] => [
      ...(calls[n]!.init.body as FormData),
    ];
    return { ...made, calls, replies, fields };
  }

  async function flush(): Promise<void> {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
  }

  beforeEach(() => {
    document.body.innerHTML = FORM;
  });

  it("resolves an applied form reply as ok and fires the usual events", async () => {
    const reply = JSON.stringify({
      version: "v1",
      ops: [{ op: "inner", target: { zone: "z" }, html: "new" }],
    });
    const s = answering(reply);
    const outcome = await s.partial.submit(document.querySelector("form")!);
    expect(outcome).toMatchObject({ status: "ok", applied: true, errors: undefined });
    expect(outcome.envelope?.ops).toHaveLength(1);
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("new");
    expect(s.calls[0]!.url).toBe(`${location.origin}/_next/form/u/`);
    expect(new Headers(s.calls[0]!.init.headers).get("X-Next-Zone")).toBe("z");
    expect(s.fields(0)).toEqual([["title", "x"]]);
    const events = s.dispatched.map((d) => d.event);
    expect(events).toEqual([
      "partial:before-request",
      "partial:before-apply",
      "partial:applied",
    ]);
    const applied = s.dispatched[2]!.detail;
    expect(applied).toMatchObject({ envelope: outcome.envelope, ok: true });
  });

  it("resolves an invalid form with its errors", async () => {
    const errors = { title: ["Too short."] };
    const s = answering(
      JSON.stringify({
        version: "v1",
        ops: [],
        form: { uid: "u", valid: false, errors },
      }),
    );
    await expect(
      s.partial.submit(document.querySelector("form")!),
    ).resolves.toMatchObject({ status: "invalid", applied: true, errors });
  });

  it("resolves a reply the page refused to apply as not applied", async () => {
    const s = answering(ENVELOPE_OK);
    document.addEventListener("partial:before-apply", (e) => e.preventDefault(), {
      once: true,
    });
    await expect(
      s.partial.submit(document.querySelector("form")!),
    ).resolves.toMatchObject({ status: "ok", applied: false });
  });

  it("resolves a network failure as the error it fired", async () => {
    const made = makeSurface();
    made.partial._configure({
      document,
      fetch: async () => {
        throw new TypeError("offline");
      },
    });
    const outcome = await made.partial.submit(document.querySelector("form")!);
    expect(outcome).toMatchObject({ status: "error", applied: false });
    const error = made.dispatched.find((d) => d.event === "partial:error")!.detail;
    expect(outcome.error).toBe(error);
  });

  it("posts an action with its fields, the page origin and the zone", async () => {
    const s = answering(ENVELOPE_OK);
    const file = new Blob(["x"]);
    await expect(
      s.partial.submit({
        action: "/_next/form/move/",
        data: { card: 3, done: false, note: "n", file },
        zone: "board",
      }),
    ).resolves.toMatchObject({ status: "ok" });
    expect(s.calls[0]!.url).toBe(`${location.origin}/_next/form/move/`);
    expect(s.calls[0]!.init.method).toBe("POST");
    expect(new Headers(s.calls[0]!.init.headers).get("X-Next-Zone")).toBe("board");
    const fields = s.fields(0);
    expect(fields.slice(0, 3)).toEqual([
      ["card", "3"],
      ["done", "false"],
      ["note", "n"],
    ]);
    expect(fields[3]![1]).toBeInstanceOf(Blob);
    const { pathname, search } = document.location;
    expect(fields[4]).toEqual(["_next_form_origin", pathname + search]);
  });

  it("keeps the origin and every value of a FormData it is given", async () => {
    const s = answering(ENVELOPE_OK, ENVELOPE_OK);
    const data = new FormData();
    data.append("tag", "a");
    data.append("tag", "b");
    data.append("_next_form_origin", "/board/1/");
    await s.partial.submit({ action: "/_next/form/tag/", data });
    await s.partial.submit({ action: "/_next/form/tag/" });
    expect(s.fields(0)).toEqual([
      ["tag", "a"],
      ["tag", "b"],
      ["_next_form_origin", "/board/1/"],
    ]);
    expect(s.fields(1).map(([name]) => name)).toEqual(["_next_form_origin"]);
  });

  it("locks a form without an action uid on its url", async () => {
    document.body.innerHTML =
      '<form action="/plain/"><input name="a" value="1"></form>';
    const s = answering();
    const form = document.querySelector("form")!;
    void s.partial.submit(form);
    void s.partial.submit(form);
    await flush();
    expect(s.calls).toHaveLength(1);
    expect(s.dispatched[0]!.detail).toMatchObject({
      intent: { uid: `/plain/` },
    });
  });

  it("queues submits behind the busy form, each one sent in turn", async () => {
    const s = answering();
    const form = document.querySelector<HTMLFormElement>("form")!;
    const input = form.querySelector("input")!;
    const first = s.partial.submit(form);
    input.value = "second";
    const second = s.partial.submit(form);
    input.value = "third";
    const third = s.partial.submit(form);
    s.replies.shift()!(patchesResponse());
    await expect(first).resolves.toMatchObject({ status: "ok" });
    await flush();
    expect(s.fields(1)).toEqual([["title", "second"]]);
    s.replies.shift()!(patchesResponse());
    await expect(second).resolves.toMatchObject({ status: "ok" });
    await flush();
    expect(s.fields(2)).toEqual([["title", "third"]]);
    s.replies.shift()!(patchesResponse());
    await expect(third).resolves.toMatchObject({ status: "ok" });
  });

  it("sends every keyless action submit in order, none dropped", async () => {
    const s = answering();
    const create = (title: string) =>
      s.partial.submit({
        action: "/_next/form/create/",
        data: { title },
        navigate: false,
      });
    const outcomes = [create("A"), create("B"), create("C")];
    for (let n = 0; n < 3; n += 1) {
      await flush();
      s.replies.shift()!(patchesResponse());
      await expect(outcomes[n]).resolves.toMatchObject({ status: "ok" });
    }
    expect([0, 1, 2].map((n) => s.fields(n)[0])).toEqual([
      ["title", "A"],
      ["title", "B"],
      ["title", "C"],
    ]);
  });

  it("settles a reply whose mount throws behind a new stylesheet and frees the lock", async () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const made = makeSurface();
    const links: ((ok: boolean) => void)[] = [];
    let sent = 0;
    made.partial._configure({
      document,
      navigate: () => undefined,
      loadLink: (_url, _nonce, done) => links.push(done),
      fetch: async () => {
        sent += 1;
        return patchesResponse(
          JSON.stringify({
            version: "v1",
            assets: [{ kind: "css", url: `/sheet-${sent}.css` }],
            ops: [
              { op: "inner", target: { zone: "z" }, html: '<p class="boom">n</p>' },
            ],
          }),
        );
      },
    });
    made.partial.onMount(".boom", () => {
      throw new Error("user mount");
    });
    const move = () => made.partial.submit({ action: "/_next/form/a/", key: "k" });
    const first = move();
    await flush();
    expect(() => links.shift()!(true)).toThrow("user mount");
    await expect(first).resolves.toMatchObject({ status: "ok", applied: false });
    const second = move();
    await flush();
    expect(sent).toBe(2);
    expect(() => links.shift()!(true)).toThrow("user mount");
    await expect(second).resolves.toMatchObject({ status: "ok", applied: false });
  });

  it("resolves an action whose data cannot be read as an error", async () => {
    const s = answering(ENVELOPE_OK);
    const broken = {
      toString(): string {
        throw new Error("unreadable");
      },
    } as unknown as string;
    const outcome = await s.partial.submit({
      action: "/_next/form/x/",
      data: { a: broken },
    });
    expect(outcome).toMatchObject({ status: "error", applied: false });
    expect(outcome.error).toMatchObject({
      kind: "network",
      error: { message: "unreadable" },
    });
    expect(s.calls).toEqual([]);
  });

  it("locks an action on its key, so other keys never wait or drop", async () => {
    const s = answering();
    const move = (key: string) => s.partial.submit({ action: "/_next/form/m/", key });
    const first = move("card-1");
    const second = move("card-2");
    await flush();
    expect(s.calls).toHaveLength(2);
    const queued = move("card-1");
    const latest = move("card-1");
    await expect(queued).resolves.toEqual({ status: "dropped", applied: false });
    expect(s.dispatched.map((d) => d.detail.intent)).toMatchObject([
      { uid: "card-1" },
      { uid: "card-2" },
    ]);
    s.replies.shift()!(patchesResponse());
    s.replies.shift()!(patchesResponse());
    await expect(first).resolves.toMatchObject({ status: "ok" });
    await expect(second).resolves.toMatchObject({ status: "ok" });
    await flush();
    expect(s.calls).toHaveLength(3);
    s.replies.shift()!(patchesResponse());
    await expect(latest).resolves.toMatchObject({ status: "ok" });
  });

  it("resolves a redirect with its location instead of navigating when asked", async () => {
    const made = makeSurface();
    const navigate = vi.fn();
    made.partial._configure({
      document,
      navigate,
      fetch: async () => {
        const response = new Response("<p>", {
          headers: { "content-type": "text/html" },
        });
        Object.defineProperties(response, {
          redirected: { value: true },
          url: { value: `${location.origin}/board/?created=9` },
        });
        return response;
      },
    });
    const create = (navigate = true) =>
      made.partial.submit({ action: "/_next/form/c/", navigate });
    await expect(create(false)).resolves.toEqual({
      status: "ok",
      applied: false,
      location: `${location.origin}/board/?created=9`,
    });
    expect(navigate).not.toHaveBeenCalled();
    await expect(create()).resolves.toEqual({ status: "ok", applied: false });
    expect(navigate).toHaveBeenCalledWith(`${location.origin}/board/?created=9`);
  });

  it("still drops a declarative double submit while the form is busy", async () => {
    const s = answering();
    const form = document.querySelector("form")!;
    const event = (): Event => new Event("submit", { bubbles: true, cancelable: true });
    form.dispatchEvent(event());
    form.dispatchEvent(event());
    const queued = s.partial.submit(form);
    await flush();
    expect(s.calls).toHaveLength(1);
    s.replies.shift()!(patchesResponse());
    await flush();
    expect(s.calls).toHaveLength(2);
    s.replies.shift()!(patchesResponse());
    await expect(queued).resolves.toMatchObject({ status: "ok" });
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

describe("createPartial over an open WebSocket", () => {
  const MASKED = [
    "fw062o3AibVzOUYtJtC0uP0MIDbY3wEs5fxT6MItQLn5l3tNjTQBXB3Rs6p5X4VE",
    "QcrsoFV0mQwlcQhSwkUZBjSeSzOdaxTwGVYfs3ATUqYRJZMc6K8A45VjC22k45aI",
  ] as const;
  const OTHER = "eoRDP3uSdzGc7HxAfeekm21kauG5wYsEsI9i2xEzzqDRvSXf3rLt6xvz6W853mSy";
  const FORM =
    '<div data-next-stream="/stream/"></div><div data-next-zone="z">old</div>' +
    '<form action="/_next/form/u/" data-next-action="u" data-next-transport="any">' +
    '<input name="t" value="x"></form>';
  const REPLY = JSON.stringify({
    version: "v1",
    ops: [{ op: "inner", target: { zone: "z" }, html: "new" }],
  });

  async function flush(): Promise<void> {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
  }

  function submit(): void {
    document
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  }

  function connected(transports: string[]) {
    const sent: string[] = [];
    const deliver: ((body: unknown) => void)[] = [];
    const opens: (() => void)[] = [];
    const websocket: WebSocketAdapter = {
      open(_url, onOpen, onMessage) {
        opens.push(() => onOpen((body) => sent.push(body)));
        deliver.push(onMessage);
        return { close: () => undefined };
      },
    };
    const fetch = vi.fn(async (url: string) =>
      url.endsWith("/stream/")
        ? Response.json({
            protocol: 2,
            transport: "ws",
            ticket: "t",
            url: "/_next/ws/",
          })
        : patchesResponse(REPLY),
    );
    const partial = createPartial({
      ...chunkModules,
      context: () => ({ $realtime: { transports } }),
      dispatch: () => undefined,
      mergeContext: () => undefined,
    });
    partial._configure({
      document,
      fetch,
      websocket,
      source: mockSource().adapter,
      visibility: manualVisibility(),
    });
    document.body.innerHTML = FORM;
    partial.stream.scan(document);
    const message = (frame: Record<string, unknown>, socket = 0): void =>
      deliver[socket]!(JSON.stringify(frame));
    const last = (): Record<string, unknown> =>
      JSON.parse(sent[sent.length - 1]!) as Record<string, unknown>;
    return { partial, fetch, opens, message, last, sent };
  }

  it("sends upstream events once the socket has said hello", async () => {
    const c = connected(["ws", "sse"]);
    await expect(c.partial.stream.send("room", "typing", {})).resolves.toBe(false);
    await flush();
    c.opens[0]!();
    c.message({ type: "hello", topics: { room: { epoch: "e", seq: 0 } } });
    const sent = c.partial.stream.send("room", "typing", { user: 1 });
    expect(c.last()).toMatchObject({ type: "up", topic: "room", name: "typing" });
    c.message({ type: "ack", id: c.last().id, ok: true });
    await expect(sent).resolves.toBe(true);
    c.partial._reset();
  });

  it("sends an event without waiting for an ack when asked to", async () => {
    const c = connected(["ws", "sse"]);
    await flush();
    c.opens[0]!();
    c.message({ type: "hello", topics: { room: { epoch: "e", seq: 0 } } });
    await expect(
      c.partial.stream.send("room", "typing", { user: 1 }, { ack: false }),
    ).resolves.toBe(true);
    expect(c.last()).toEqual({
      type: "up",
      topic: "room",
      name: "typing",
      data: { user: 1 },
    });
    const acked = c.partial.stream.send("room", "typing", null, { ack: true });
    expect(c.last().id).toEqual(expect.any(String));
    c.message({ type: "ack", id: c.last().id, ok: true });
    await expect(acked).resolves.toBe(true);
    c.partial._reset();
  });

  it("reconnects the socket when a reply or the page rotates the CSRF token", async () => {
    const c = connected(["ws", "sse"]);
    const negotiations = (): number =>
      c.fetch.mock.calls.filter(([url]) => url.endsWith("/stream/")).length;
    await flush();
    c.opens[0]!();
    c.message({ type: "hello", topics: { room: { epoch: "e", seq: 0 } }, tunnel: {} });
    submit();
    await flush();
    c.message({
      type: "http",
      id: c.last().id,
      status: 200,
      headers: { "content-type": "application/vnd.next.patches+json" },
      body: JSON.stringify({
        version: "v1",
        ops: [],
        csrf: { header: "X-CSRFToken", token: MASKED[0] },
      }),
    });
    await flush();
    expect(negotiations()).toBe(2);
    c.opens[1]!();
    c.message({ type: "hello", topics: {} }, 1);
    c.partial.setCsrf({ header: "X-CSRFToken", token: MASKED[1] });
    await flush();
    expect(negotiations()).toBe(2);
    c.partial.setCsrf({ header: "X-CSRFToken", token: OTHER });
    await flush();
    expect(negotiations()).toBe(3);
    c.opens[2]!();
    c.message({ type: "hello", topics: {} }, 2);
    c.partial.setCsrf({ header: "X-CSRFToken", url: "/csrf/" });
    c.partial.setCsrf(undefined);
    await flush();
    expect(negotiations()).toBe(3);
    c.partial._reset();
  });

  it("keeps the socket when a reply re-masks the token the page started with", async () => {
    const c = connected(["ws", "sse"]);
    c.partial.setCsrf({ header: "X-CSRFToken", token: MASKED[0] });
    await flush();
    c.opens[0]!();
    c.message({ type: "hello", topics: {}, tunnel: {} });
    c.partial.setCsrf({ header: "X-CSRFToken", token: MASKED[1] });
    await flush();
    expect(c.fetch.mock.calls.filter(([url]) => url.endsWith("/stream/"))).toHaveLength(
      1,
    );
    c.partial._reset();
  });

  it("resolves a submit through the socket tunnel and an undelivered one", async () => {
    const c = connected(["ws", "sse"]);
    await flush();
    c.opens[0]!();
    c.message({ type: "hello", topics: {}, tunnel: {} });
    const form = document.querySelector("form")!;
    const tunneled = c.partial.submit(form);
    await flush();
    expect(c.last()).toMatchObject({ type: "http", method: "POST", body: "t=x" });
    c.message({
      type: "http",
      id: c.last().id,
      status: 200,
      headers: { "content-type": "application/vnd.next.patches+json" },
      body: REPLY,
    });
    await expect(tunneled).resolves.toMatchObject({ status: "ok", applied: true });
    const refused = c.partial.submit({
      action: "/_next/form/u/",
      data: { t: "y" },
      transport: "any",
    });
    await flush();
    expect(c.last()).toMatchObject({ type: "http", method: "POST" });
    c.message({ type: "http", id: c.last().id, status: 0, error: "limit" });
    await expect(refused).resolves.toMatchObject({
      status: "error",
      applied: false,
      error: { kind: "undelivered", reason: "limit" },
    });
    expect(c.fetch).toHaveBeenCalledOnce();
    c.partial._reset();
  });

  it("resolves a tunneled redirect with its location when asked", async () => {
    const c = connected(["ws", "sse"]);
    await flush();
    c.opens[0]!();
    c.message({ type: "hello", topics: {}, tunnel: {} });
    const created = c.partial.submit({
      action: "/_next/form/c/",
      transport: "any",
      navigate: false,
    });
    await flush();
    c.message({
      type: "http",
      id: c.last().id,
      status: 302,
      headers: { location: "/board/?created=9" },
    });
    await expect(created).resolves.toEqual({
      status: "ok",
      applied: false,
      location: `${location.origin}/board/?created=9`,
    });
    expect(c.fetch).toHaveBeenCalledOnce();
    c.partial._reset();
  });

  it("names the socket connection in the metadata of a topic event", async () => {
    const c = connected(["ws", "sse"]);
    const seen: (string | undefined)[] = [];
    c.partial.stream.on("room", "moved", (_detail, meta) => {
      seen.push(meta.connection, c.partial.stream.meta()?.connection);
    });
    await flush();
    c.opens[0]!();
    c.message({
      type: "hello",
      topics: { room: { epoch: "e", seq: 0 } },
      connection: "c-7",
    });
    c.message({
      type: "patches",
      data: {
        version: "v1",
        topic: "room",
        seq: 1,
        epoch: "e",
        ops: [{ op: "event", name: "moved" }],
      },
    });
    expect(seen).toEqual(["c-7", "c-7"]);
    expect(c.partial.stream.meta()).toBeUndefined();
    c.partial._reset();
  });

  it("applies a form reply from the socket as the fetched reply", async () => {
    const c = connected(["ws", "sse"]);
    await flush();
    c.opens[0]!();
    c.message({ type: "hello", topics: { room: { epoch: "e", seq: 0 } }, tunnel: {} });
    submit();
    await flush();
    expect(c.last()).toMatchObject({ type: "http", method: "POST", body: "t=x" });
    c.message({
      type: "http",
      id: c.last().id,
      status: 200,
      headers: { "content-type": "application/vnd.next.patches+json" },
      body: REPLY,
    });
    await flush();
    const tunneled = document.querySelector('[data-next-zone="z"]')!.textContent;
    expect(c.fetch).toHaveBeenCalledOnce();
    c.partial._reset();

    const plain = connected(["sse"]);
    submit();
    await flush();
    expect(plain.fetch).toHaveBeenCalledOnce();
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe(tunneled);
    expect(tunneled).toBe("new");
    await expect(plain.partial.stream.send("room", "typing", {})).resolves.toBe(false);
    plain.partial._reset();
  });
});

describe("createPartial after a mutation that got no reply", () => {
  async function flush(): Promise<void> {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
  }

  const zoneGets = (fetch: { mock: { calls: unknown[][] } }): number =>
    fetch.mock.calls.filter(([, init]) =>
      new Headers((init as RequestInit).headers).has("X-Next-Zone"),
    ).length;

  const echo = (id: string | null, seq: number) => ({
    version: "v1",
    topic: "room",
    epoch: "e",
    seq,
    request_id: id,
    ops: [{ op: "refresh", zone: "z" }],
  });

  it("refreshes zones on the echo of a tunneled mutation a hidden tab cut off", async () => {
    const sent: string[][] = [];
    const deliver: ((body: unknown) => void)[] = [];
    const opens: (() => void)[] = [];
    const websocket: WebSocketAdapter = {
      open(_url, onOpen, onMessage) {
        const mine: string[] = [];
        sent.push(mine);
        opens.push(() => onOpen((body) => mine.push(body)));
        deliver.push(onMessage);
        return { close: () => undefined };
      },
    };
    const fetch = vi.fn(async (url: string) =>
      url.endsWith("/stream/")
        ? Response.json({
            protocol: 2,
            transport: "ws",
            ticket: "t",
            url: "/_next/ws/",
          })
        : patchesResponse(ENVELOPE_OK),
    );
    const visibility = manualVisibility();
    const partial = createPartial({
      ...chunkModules,
      context: () => ({ $realtime: { transports: ["ws", "sse"] } }),
      dispatch: () => undefined,
      mergeContext: () => undefined,
    });
    partial._configure({
      document,
      fetch,
      websocket,
      source: mockSource().adapter,
      visibility,
    });
    document.body.innerHTML =
      '<div data-next-stream="/stream/"></div><div data-next-zone="z">old</div>';
    partial.stream.scan(document);
    await flush();
    opens[0]!();
    const hello = (seq: number): string =>
      JSON.stringify({
        type: "hello",
        topics: { room: { epoch: "e", seq } },
        tunnel: {},
      });
    deliver[0]!(hello(0));
    const outcome = partial.submit({
      action: "/_next/form/u/",
      data: { t: "x" },
      transport: "any",
    });
    await flush();
    const frame = JSON.parse(sent[0]!.at(-1)!) as { headers: Record<string, string> };
    const id = frame.headers["x-next-request-id"]!;
    visibility.setHidden(true);
    await expect(outcome).resolves.toMatchObject({
      status: "error",
      error: { kind: "undelivered", reason: "closed" },
    });
    visibility.setHidden(false);
    await flush();
    opens[1]!();
    deliver[1]!(JSON.stringify({ type: "patches", data: echo(id, 1) }));
    deliver[1]!(hello(1));
    await flush();
    expect(zoneGets(fetch)).toBe(1);
    partial._reset();
  });

  it("refreshes zones on the echo of an HTTP mutation that failed on the network", async () => {
    const fetch = vi.fn(async (_url: string, init: RequestInit) => {
      if (init.method === "POST") throw new TypeError("network down");
      return patchesResponse(ENVELOPE_OK);
    });
    const source = mockSource();
    const partial = createPartial({
      ...chunkModules,
      dispatch: () => undefined,
      mergeContext: () => undefined,
    });
    partial._configure({ document, fetch, source: source.adapter });
    document.body.innerHTML =
      '<div data-next-stream="/stream/"></div><div data-next-zone="z">old</div>';
    partial.stream.scan(document);
    const outcome = await partial.submit({
      action: "/_next/form/u/",
      data: { t: "x" },
    });
    expect(outcome).toMatchObject({ status: "error", error: { kind: "network" } });
    const id = new Headers(fetch.mock.calls[0]![1].headers).get("X-Next-Request-Id");
    expect(id).toEqual(expect.any(String));
    source.opened[0]!.message(JSON.stringify(echo(id, 1)));
    await flush();
    expect(zoneGets(fetch)).toBe(1);
    partial._reset();
  });

  it("forgets an id sent before the bridge was built", async () => {
    const stream = unloaded<SseFactory>("stream");
    const fetch = vi.fn(async (_url: string, init: RequestInit) => {
      if (init.method === "POST") throw new TypeError("network down");
      return patchesResponse(ENVELOPE_OK);
    });
    const source = mockSource();
    const partial = createPartial({
      ...chunkModules,
      stream,
      dispatch: () => undefined,
      mergeContext: () => undefined,
    });
    partial._configure({ document, fetch, source: source.adapter });
    document.head.innerHTML = "";
    document.body.innerHTML =
      '<div data-next-stream="/stream/"></div><div data-next-zone="z">old</div>';
    partial.stream.scan(document);
    await partial.submit({ action: "/_next/form/u/", data: { t: "x" } });
    const id = new Headers(fetch.mock.calls[0]![1].headers).get("X-Next-Request-Id");
    stream.land(createSse);
    source.opened[0]!.message(JSON.stringify(echo(id, 1)));
    await flush();
    expect(zoneGets(fetch)).toBe(1);
    partial._reset();
  });

  it("fetches the ws and stream chunks together on a WebSocket page", () => {
    const ws = unloaded<WsTransportFactory>("ws");
    const stream = unloaded<SseFactory>("stream");
    const lazy = createPartial({
      ...chunkModules,
      context: () => ({ $realtime: { transports: ["ws", "sse"] } }),
      ws,
      stream,
      dispatch: () => undefined,
      mergeContext: () => undefined,
    });
    lazy._configure({ document, source: mockSource().adapter });
    document.head.innerHTML = "";
    document.body.innerHTML = '<div data-next-stream="/stream/"></div>';
    lazy.stream.scan(document);
    expect(ws.tags()).toBe(1);
    expect(stream.tags()).toBe(1);
    lazy._reset();
  });
});
