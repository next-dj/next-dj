import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";
import { createPartial } from "./partial";
import type { PartialSurface } from "./partial";
import { CHUNK_TIMEOUT } from "./chunks";
import { createDefer } from "./defer";
import { createHistory } from "./history";
import type { DialogAdapter } from "./layers";
import { ACCEPT, CONTENT_TYPE, STREAM_ACCEPT } from "./protocol";
import { createReader } from "./reader";
import { createRouter } from "./router";
import type {
  Entry,
  HistoryHost,
  Keeper,
  Prefetched,
  Router,
  RouterHost,
  Turn,
} from "./router";
import { submitUrl } from "./submit";
import { createSwap } from "./swap";
import { chunkModules } from "./test-doubles";

interface Host extends RouterHost {
  states: Entry[];
  assigned: string[];
  listens: number;
  stops: number;
}

interface Call {
  url: string;
  headers: Headers;
  signal: AbortSignal | null;
}

interface Options {
  page?: string;
  router?: Record<string, unknown>;
  layouts?: unknown;
  context?: Record<string, unknown>;
  state?: unknown;
  dialog?: DialogAdapter;
}

type Reply = Response | ((signal: AbortSignal | null) => Promise<Response>);

type Detail = Record<string, unknown>;

const SETTINGS = {
  scope: "all",
  exclude: ["/static/"],
  snapshots: 10,
  progress_delay: 0.15,
  intercept: "opt-in",
  max_layouts: 32,
  current: ["nav", "[role=navigation]", "[data-next-nav]"],
  homes: ["/"],
};

const PAGE =
  '<nav><a id="home" href="/">Home</a><a id="nav-b" href="/b/">B</a></nav>' +
  '<div data-next-zone="flash">old</div><!--note-->' +
  '<!--nx:s L1 S1--><main><h1>Home</h1><a id="b" href="/b/">B</a></main>' +
  "<!--/nx:s L1 S1-->";

let listening: AbortController;
let surface: PartialSurface;
let calls: Call[];
let scrolled: ReturnType<typeof vi.fn>;
let intoView: Mock<Element["scrollIntoView"]>;
let reload: Mock<() => void>;

beforeEach(() => {
  listening = new AbortController();
  calls = [];
  scrolled = vi.fn();
  reload = vi.fn<() => void>();
  history.replaceState(null, "", "/");
  document.head.innerHTML = "";
  document.title = "Home";
  sessionStorage.clear();
  vi.stubGlobal("scrollTo", scrolled);
  intoView = vi.fn<Element["scrollIntoView"]>();
  Element.prototype.scrollIntoView = intoView;
});

afterEach(() => {
  listening.abort();
  surface._reset();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.body.innerHTML = "";
  document.documentElement.removeAttribute("data-next-navigating");
});

function envelope(html = "<main><h1>B</h1></main>", href = "/b/", extra: Detail = {}) {
  return {
    version: "v1",
    ops: [
      { op: "morph", target: { slot: "L1" }, html },
      { op: "url", action: "push", href },
      { op: "meta", title: `Page ${href}` },
    ],
    layouts: [["L1", "D1", null]],
    root: "L1",
    nonce: null,
    ...extra,
  };
}

function bodied(html: string, href: string): Detail {
  return {
    version: "",
    ops: [
      { op: "morph", target: { body: true }, html },
      { op: "url", action: "push", href },
    ],
    layouts: [["L2", "D2", "S2"]],
    root: "L1",
  };
}

function answer(body: unknown, headers: Record<string, string> = {}, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": `${CONTENT_TYPE}; charset=utf-8`, ...headers },
  });
}

function serve(...replies: Reply[]) {
  const fetch = vi.fn((url: string, init: RequestInit) => {
    const signal = init.signal ?? null;
    calls.push({ url, headers: new Headers(init.headers), signal });
    const reply = replies.shift() ?? answer(envelope());
    return reply instanceof Response ? Promise.resolve(reply) : reply(signal);
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function fakeHost(): Host {
  const host: Host = {
    states: [],
    assigned: [],
    listens: 0,
    stops: 0,
    listen(handler) {
      host.listens += 1;
      document.addEventListener("click", handler);
      document.addEventListener("submit", handler);
      return () => {
        host.stops += 1;
        document.removeEventListener("click", handler);
        document.removeEventListener("submit", handler);
      };
    },
    state: () => history.state as unknown,
    replace(entry, href) {
      host.states.push(entry);
      history.replaceState(entry, "", href);
    },
    assign(href) {
      host.assigned.push(href);
    },
  };
  return host;
}

function stubKeeper(restored = 0, hit?: Prefetched) {
  const turn = {
    hold: vi.fn<Turn["hold"]>(),
    before: vi.fn<Turn["before"]>(),
    after: vi.fn<Turn["after"]>(),
  };
  return {
    turn,
    leave: vi.fn<Keeper["leave"]>(),
    pop: vi.fn<Keeper["pop"]>(() => restored),
    place: vi.fn<Keeper["place"]>(() => true),
    flush: vi.fn<Keeper["flush"]>(),
    swap: vi.fn<Keeper["swap"]>(() => turn),
    extract: vi.fn<Keeper["extract"]>(() => envelope("<main><h1>X</h1></main>")),
    take: vi.fn<Keeper["take"]>(() => hit),
    prefetch: vi.fn<Keeper["prefetch"]>(() => Promise.resolve()),
    drop: vi.fn<Keeper["drop"]>(),
  };
}

function setup(options: Options = {}) {
  document.body.innerHTML = options.page ?? PAGE;
  if (options.state !== undefined) history.replaceState(options.state, "", "/");
  const context: Detail = {
    $router: options.router ?? SETTINGS,
    $layouts: options.layouts ?? [["L1", "D1", "S1"]],
    ...options.context,
  };
  surface = createPartial({
    ...chunkModules,
    dispatch: () => undefined,
    mergeContext: (data) => Object.assign(context, data),
    context: () => context,
  });
  surface._configure({ reload, ...(options.dialog ? { dialog: options.dialog } : {}) });
  const host = fakeHost();
  const events: string[] = [];
  const ends: Detail[] = [];
  for (const name of ["before", "start", "before-swap", "commit", "end", "error"])
    document.addEventListener(
      `router:${name}`,
      (event) => {
        events.push(name);
        if (name === "end") ends.push((event as CustomEvent<Detail>).detail);
      },
      { signal: listening.signal },
    );
  const router = createRouter(
    {
      partial: surface,
      get context() {
        return context;
      },
      on: (_event, listener) => {
        listener();
        return () => undefined;
      },
    },
    host,
    { signal: listening.signal },
  );
  return { router, host, context, events, ends };
}

function seamsOf(router: Router): HistoryHost {
  let seams: HistoryHost | undefined;
  router._install((host) => {
    seams = host;
    return stubKeeper();
  });
  return seams!;
}

function loaded(router: Router): Router {
  router._install((seams) => ({
    ...createHistory(seams, { signal: listening.signal }),
    form: submitUrl,
  }));
  return router;
}

function settled(): Promise<Detail> {
  return new Promise((resolve) =>
    document.addEventListener(
      "router:end",
      (event) => resolve((event as CustomEvent<Detail>).detail),
      { once: true },
    ),
  );
}

function click(target: Element, init: MouseEventInit = {}): boolean {
  let taken = false;
  const after = (event: Event): void => {
    taken = event.defaultPrevented;
    event.preventDefault();
  };
  window.addEventListener("click", after);
  target.dispatchEvent(
    new MouseEvent("click", { bubbles: true, cancelable: true, ...init }),
  );
  window.removeEventListener("click", after);
  return taken;
}

function submit(form: Element, submitter: HTMLElement | null = null): boolean {
  const event = new SubmitEvent("submit", {
    bubbles: true,
    cancelable: true,
    submitter,
  });
  form.dispatchEvent(event);
  return event.defaultPrevented;
}

function heading(): string | null {
  return document.querySelector("main h1")?.textContent ?? null;
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function pop(state: unknown, at = "/"): void {
  history.replaceState(state, "", at);
  window.dispatchEvent(new PopStateEvent("popstate", { state }));
}

function back(state: unknown, at = "/"): Promise<Detail> {
  const done = settled();
  pop(state, at);
  return done;
}

describe("link ownership", () => {
  it.each([
    ["a foreign target", '<a id="l" href="/b/" target="_blank">x</a>', {}],
    ["a download", '<a id="l" href="/b/" download>x</a>', {}],
    ["an external rel", '<a id="l" href="/b/" rel="nofollow external">x</a>', {}],
    ["a layer opener", '<a id="l" href="/b/" data-next-layer="">x</a>', {}],
    ["a merge link", '<a id="l" href="/b/" data-next-merge>x</a>', {}],
    ["a reload ancestor", '<div data-next-reload><a id="l" href="/b/">x</a></div>', {}],
    ["a router opt-out", '<a id="l" href="/b/" data-next-router="off">x</a>', {}],
    [
      "an opted-out ancestor",
      '<div data-next-router="off"><a id="l" href="/b/">x</a></div>',
      {},
    ],
    ["a fragment of this page", '<a id="l" href="#top">x</a>', {}],
    ["an excluded prefix", '<a id="l" href="/static/app.css">x</a>', {}],
    ["another scheme", '<a id="l" href="mailto:a@b.c">x</a>', {}],
    ["another origin", '<a id="l" href="https://elsewhere.example/b/">x</a>', {}],
    ["a modified click", '<a id="l" href="/b/">x</a>', { ctrlKey: true }],
    ["a middle click", '<a id="l" href="/b/">x</a>', { button: 1 }],
    ["an svg link", '<svg><a id="l" href="/b/"><text>x</text></a></svg>', {}],
  ])("leaves %s to the browser", (_name, markup, init) => {
    setup({ page: markup });
    const fetch = serve();
    expect(click(document.getElementById("l")!, init)).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("ignores a click that reaches no element", () => {
    setup();
    const fetch = serve();
    document.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true }),
    );
    expect(fetch).not.toHaveBeenCalled();
  });

  it("fetches a path that looks like a host from this origin", async () => {
    setup({ page: '<a id="l" href="/.//evil.example/x">x</a>' });
    serve(answer(envelope()));
    const done = settled();
    expect(click(document.getElementById("l")!)).toBe(true);
    await done;
    const requested = new URL(calls[0]!.url);
    expect(requested.origin).toBe(location.origin);
    expect(requested.pathname).toBe("//evil.example/x");
  });

  it("owns a link inside a zone that reloads to its loading content", async () => {
    setup({
      page:
        '<section data-next-zone="z" data-next-reload="loading">' +
        '<a id="l" href="/b/">x</a></section>',
    });
    serve();
    const done = settled();
    expect(click(document.getElementById("l")!)).toBe(true);
    await done;
  });

  it("owns only the links inside a router region in the opt-in scope", async () => {
    setup({
      router: { ...SETTINGS, scope: "opt-in" },
      page:
        '<a id="out" href="/b/">x</a>' +
        '<div data-next-router><a id="in" href="/c/">y</a></div>',
    });
    serve();
    expect(click(document.getElementById("out")!)).toBe(false);
    const done = settled();
    expect(click(document.getElementById("in")!)).toBe(true);
    await done;
    expect(calls).toHaveLength(1);
  });

  it("loads a URL that answered no envelope hard", async () => {
    const { host } = setup();
    serve(new Response("<html></html>", { headers: { "content-type": "text/html" } }));
    const done = settled();
    expect(click(document.getElementById("b")!)).toBe(true);
    await done;
    expect(host.assigned).toEqual([`${location.origin}/b/`]);
  });
});

describe("links the partial runtime handles", () => {
  const ORDERS = [
    ["bound before the router", false],
    ["bound after the router", true],
  ] as const;

  const dialog: DialogAdapter = { open: () => () => undefined };

  function own(page: string, late: boolean): string[] {
    const { events } = setup({ page, dialog });
    if (late) surface._configure({ reload, dialog });
    return events;
  }

  function routed(): boolean {
    return calls.some((call) => call.headers.has("X-Next-Navigate"));
  }

  function link(): HTMLElement {
    return document.getElementById("l")!;
  }

  it.each(ORDERS)("paginates a merge link %s", async (_name, late) => {
    const events = own(
      '<div data-next-zone="z"></div>' +
        '<a id="l" href="/b/?page=2" data-next-target="z" data-next-merge="append">m</a>',
      late,
    );
    serve(answer({ version: "v1", ops: [] }));
    expect(click(link())).toBe(true);
    await vi.waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]!.headers.get("X-Next-Merge")).toBe("append");
    expect(routed()).toBe(false);
    expect(events).not.toContain("start");
  });

  it.each(ORDERS)("opens a layer link %s", async (_name, late) => {
    const events = own('<a id="l" href="/b/" data-next-layer="z">x</a>', late);
    serve(answer({ version: "v1", ops: [] }));
    expect(click(link())).toBe(true);
    await vi.waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]!.headers.get("X-Next-Zone")).toBe("z");
    expect(routed()).toBe(false);
    expect(events).not.toContain("start");
  });

  it.each([
    ["a retry link", '<a id="l" href="/b/" data-next-retry>x</a>', 1],
    ["a retry ancestor", '<div data-next-retry><a id="l" href="/b/">x</a></div>', 1],
    [
      "a fetch link",
      '<div data-next-boundary="b" data-next-token="t" data-next-state="idle"' +
        ' data-next-defer="manual"><a id="l" href="/b/" data-next-fetch>x</a></div>',
      0,
    ],
  ])(
    "leaves %s to the boundary bundle bound after it",
    async (_name, page, reloads) => {
      const events = own(page, false);
      const reloaded = vi.fn<() => void>();
      createDefer(
        { partial: surface },
        { signal: listening.signal },
        reloaded,
      )(() => undefined);
      serve(answer({ version: "v1", ops: [] }));
      expect(click(link())).toBe(true);
      await wait(10);
      expect(reloaded).toHaveBeenCalledTimes(reloads);
      expect(routed()).toBe(false);
      expect(events).not.toContain("start");
    },
  );

  it.each(ORDERS)("leaves a refused confirm link alone %s", async (_name, late) => {
    vi.stubGlobal(
      "confirm",
      vi.fn(() => false),
    );
    const events = own('<a id="l" href="/b/" data-next-confirm="Sure?">x</a>', late);
    const fetch = serve();
    const event = new MouseEvent("click", { bubbles: true, cancelable: true });
    link().dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    await wait(10);
    expect(fetch).not.toHaveBeenCalled();
    expect(events).not.toContain("start");
  });

  it.each(ORDERS)("navigates an accepted confirm link %s", async (_name, late) => {
    const prompt = vi.fn(() => true);
    vi.stubGlobal("confirm", prompt);
    own('<a id="l" href="/b/" data-next-confirm="Sure?">x</a>', late);
    serve();
    const done = settled();
    expect(click(link())).toBe(true);
    await done;
    expect(prompt).toHaveBeenCalledWith("Sure?");
    expect(routed()).toBe(true);
  });

  it.each([
    [
      "a target link without a merge",
      '<a id="l" href="/b/" data-next-target="z">x</a>',
    ],
    [
      "a link in a polled zone",
      '<section data-next-zone="z" data-next-poll="5000"><a id="l" href="/b/">x</a></section>',
    ],
  ])("navigates %s as the plain link it stays without the router", async (_n, page) => {
    own(page, false);
    serve();
    const done = settled();
    expect(click(link())).toBe(true);
    await done;
    expect(calls).toHaveLength(1);
    expect(routed()).toBe(true);
    expect(calls[0]!.headers.has("X-Next-Merge")).toBe(false);
  });
});

describe("plain GET forms", () => {
  function formPage(page: string): void {
    loaded(setup({ page }).router);
  }

  it("navigates a plain GET form to its serialised URL", async () => {
    formPage(
      '<form id="f" action="/search/"><input name="q" value="a b">' +
        '<input type="file" name="upload"></form>',
    );
    serve();
    const done = settled();
    expect(submit(document.getElementById("f")!)).toBe(true);
    await done;
    expect(calls[0]!.url).toBe(`${location.origin}/search/?q=a+b`);
  });

  it("navigates a GET form without an action to this page", async () => {
    formPage('<form id="f"><input name="q" value="x"></form>');
    serve();
    const done = settled();
    expect(submit(document.getElementById("f")!)).toBe(true);
    await done;
    expect(calls[0]!.url).toBe(`${location.origin}/?q=x`);
  });

  it("honours the action and the value of the button that submitted", async () => {
    formPage(
      '<form id="f" action="/search/" method="post"><input name="q" value="x">' +
        '<button id="go" name="sort" value="new" formmethod="GET" ' +
        'formaction="/list/" formtarget="_self">go</button></form>',
    );
    serve();
    const done = settled();
    const form = document.getElementById("f")!;
    expect(submit(form, document.getElementById("go"))).toBe(true);
    await done;
    expect(calls[0]!.url).toBe(`${location.origin}/list/?q=x&sort=new`);
  });

  it("leaves a GET form to the browser until the intent chunk loads", () => {
    setup({ page: '<form id="f" action="/b/"></form>' });
    const fetch = serve();
    expect(submit(document.getElementById("f")!)).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("leaves a GET form to an excluded prefix to the browser", () => {
    formPage('<form id="f" action="/static/find/"></form>');
    const fetch = serve();
    expect(submit(document.getElementById("f")!)).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each([
    ["a POST form", '<form id="f" method="post" action="/b/"></form>', null],
    [
      "a form with a runtime attribute",
      '<form id="f" data-next-target="z"></form>',
      null,
    ],
    [
      "a form aimed elsewhere",
      '<form id="f" action="/b/" target="_blank"></form>',
      null,
    ],
    ["a submit outside a form", '<div id="f"></div>', null],
    [
      "a button that posts",
      '<form id="f" action="/b/"><button id="s" formmethod="post">x</button></form>',
      "s",
    ],
    [
      "a button aimed elsewhere",
      '<form id="f" action="/b/"><button id="s" formtarget="_blank">x</button></form>',
      "s",
    ],
  ])("leaves %s alone", (_name, markup, button) => {
    formPage(markup);
    const fetch = serve();
    const submitter = button === null ? null : document.getElementById(button);
    expect(submit(document.getElementById("f")!, submitter)).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("leaves a submit another handler already took", () => {
    formPage('<form id="f" action="/b/"></form>');
    const fetch = serve();
    const form = document.getElementById("f")!;
    form.addEventListener("submit", (event) => event.preventDefault());
    submit(form);
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("the visit pipeline", () => {
  it("morphs the slot between its markers and keeps the chrome", async () => {
    const { host, events, ends } = setup();
    serve(
      answer(
        envelope(undefined, "/b/", {
          ops: [
            { op: "morph", target: { slot: "L1" }, html: "<main><h1>B</h1></main>" },
            {
              op: "morph",
              target: { zone: "flash" },
              html: '<div data-next-zone="flash">saved</div>',
            },
            { op: "url", action: "push", href: "/b/" },
            { op: "meta", title: "Page /b/" },
            { op: "context", data: { merged: 1 } },
            null,
          ],
        }),
      ),
    );
    const nav = document.querySelector("nav");
    const main = document.querySelector("main");
    const done = settled();
    expect(click(document.getElementById("b")!)).toBe(true);
    expect(await done).toEqual({ url: `${location.origin}/b/`, status: "complete" });
    expect(heading()).toBe("B");
    expect(document.querySelector("nav")).toBe(nav);
    expect(document.querySelector("main")).toBe(main);
    expect(document.querySelector("[data-next-zone=flash]")!.textContent).toBe("saved");
    expect(document.body.innerHTML).toContain(
      "<!--nx:s L1 S1--><main><h1>B</h1></main><!--/nx:s L1 S1-->",
    );
    expect(location.pathname).toBe("/b/");
    expect(document.title).toBe("Page /b/");
    expect(events).toEqual(["end", "before", "start", "before-swap", "commit", "end"]);
    expect(ends[0]).toEqual({
      url: `${location.origin}/`,
      status: "complete",
      initial: true,
    });
    expect(host.states).toHaveLength(1);
    expect(history.state).toEqual({ next: 1, kind: "nav", key: expect.any(String) });
    expect((history.state as Entry).key).not.toBe(host.states[0]!.key);
    expect(scrolled).not.toHaveBeenCalled();
    const headers = calls[0]!.headers;
    expect(calls[0]!.url).toBe(`${location.origin}/b/`);
    expect(headers.get("X-Next-Request")).toBe("1");
    expect(headers.get("X-Next-Navigate")).toBe("1");
    expect(headers.get("X-Next-Layouts")).toBe("L1.D1");
    expect(headers.get("X-Next-Origin")).toBe("/");
    expect(headers.get("Accept")).toBe(ACCEPT);
    expect(headers.has("X-Next-Version")).toBe(false);
    expect(headers.has("X-Next-Prefetch")).toBe(false);
  });

  it("names the nodes the swap replaces before it runs", async () => {
    const { router } = setup();
    serve(answer(envelope()));
    const main = document.querySelector("main");
    const seen: unknown[] = [];
    document.addEventListener(
      "router:before-swap",
      (event) => seen.push((event as CustomEvent<Detail>).detail.nodes),
      { signal: listening.signal },
    );
    await router.visit("/b/");
    expect(seen).toEqual([[main]]);
  });

  it("lets a router:before listener keep the page", async () => {
    const { router, events } = setup();
    const fetch = serve();
    document.addEventListener("router:before", (event) => event.preventDefault(), {
      signal: listening.signal,
    });
    await router.visit("/b/");
    expect(fetch).not.toHaveBeenCalled();
    expect(events).toEqual(["end", "before"]);
  });

  it("aborts the earlier visit, which settles superseded", async () => {
    const { router, ends, events } = setup();
    serve(answer(envelope()), answer(envelope("<main><h1>C</h1></main>", "/c/")));
    const first = router.visit("/b/");
    const second = router.visit("/c/");
    await Promise.all([first, second]);
    expect(calls[0]!.signal!.aborted).toBe(true);
    expect(
      ends
        .slice(1)
        .map((detail) => detail.status)
        .sort(),
    ).toEqual(["complete", "superseded"]);
    expect(heading()).toBe("C");
    expect(location.pathname).toBe("/c/");
    expect(events).not.toContain("error");
  });

  it("settles a visit whose request the abort rejected as superseded", async () => {
    const { router, ends, events } = setup();
    serve(
      (signal) =>
        new Promise<Response>((_resolve, reject) =>
          signal!.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          ),
        ),
      answer(envelope()),
    );
    const first = router.visit("/x/");
    const second = router.visit("/b/");
    await Promise.all([first, second]);
    expect(
      ends
        .slice(1)
        .map((detail) => detail.status)
        .sort(),
    ).toEqual(["complete", "superseded"]);
    expect(events).not.toContain("error");
  });

  it("waits for a before-swap hold and drops a swap a newer visit superseded", async () => {
    const { router, ends } = setup();
    serve(answer(envelope()), answer(envelope("<main><h1>C</h1></main>", "/c/")));
    let release = (): void => undefined;
    let held = (): void => undefined;
    const holding = new Promise<void>((resolve) => (held = resolve));
    document.addEventListener(
      "router:before-swap",
      (event) => {
        release = (event as CustomEvent<{ hold: () => () => void }>).detail.hold();
        held();
      },
      { once: true },
    );
    const first = router.visit("/b/");
    await holding;
    expect(heading()).toBe("Home");
    const second = router.visit("/c/");
    release();
    await Promise.all([first, second]);
    expect(
      ends
        .slice(1)
        .map((detail) => detail.status)
        .sort(),
    ).toEqual(["complete", "superseded"]);
    expect(heading()).toBe("C");
  });

  it("commits the final URL of a same-origin redirect with the clicked fragment", async () => {
    const { router } = setup();
    loaded(router);
    const response = answer(envelope('<main><h2 id="part">P</h2></main>', "/c/"));
    Object.defineProperty(response, "url", { value: `${location.origin}/c/` });
    serve(response);
    const done = settled();
    await router.visit("/b/#part");
    expect((await done).url).toBe(`${location.origin}/c/#part`);
    expect(location.pathname).toBe("/c/");
    expect(location.hash).toBe("#part");
    expect(intoView).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["a stale asset version", () => answer({}, {}, 409)],
    ["a server error", () => answer(envelope(), {}, 500)],
    ["an empty answer", () => new Response(null, { status: 204 })],
    [
      "a document",
      () => new Response("<html></html>", { headers: { "content-type": "text/html" } }),
    ],
    [
      "a visit op",
      () =>
        answer({
          version: "",
          ops: [{ op: "visit", href: "/b/" }],
          layouts: [["R2", "D2", "S2"]],
          root: "R2",
        }),
    ],
    [
      "a visit op without a target",
      () => answer({ version: "", ops: [{ op: "visit" }], layouts: [] }),
    ],
    ["an answer without layouts", () => answer({ version: "", ops: [] })],
    ["a body that is no envelope", () => answer([1])],
    ["an envelope without a version", () => answer({ ops: [], layouts: [] })],
    [
      "a malformed body",
      () => new Response("{", { headers: { "content-type": CONTENT_TYPE } }),
    ],
    [
      "a slot the chain does not know",
      () =>
        answer({
          ...envelope(),
          ops: [{ op: "morph", target: { slot: "L7" }, html: "" }],
        }),
    ],
    [
      "a stream with no reader to read it",
      () => new Response("", { headers: { "content-type": "text/event-stream" } }),
    ],
  ])("loads the page hard on %s", async (_name, reply) => {
    const { router, host, ends } = setup();
    serve(reply());
    await router.visit("/b/");
    expect(host.assigned).toEqual([`${location.origin}/b/`]);
    expect(ends.at(-1)!.status).toBe("hard");
  });

  it.each([
    ["/login/?next=/b/", `${location.origin}/login/?next=/b/`],
    ["https://id.example/out", "https://id.example/out"],
    ["javascript:alert(1)", undefined],
  ])("follows the visit op of a clearing answer to %s", async (href, landed) => {
    const { router, host, ends } = setup();
    const keeper = stubKeeper();
    router._install(() => keeper);
    const visit = { version: "", ops: [{ op: "visit", href }] };
    serve(answer(visit, { "X-Next-Router": "clear" }));
    await router.visit("/b/");
    expect(keeper.flush).toHaveBeenCalledTimes(1);
    expect(host.assigned).toEqual(landed === undefined ? [] : [landed]);
    expect(ends.at(-1)!.status).toBe("hard");
  });

  it("keeps every request of a visit on this origin", async () => {
    const { router } = setup();
    loaded(router);
    const fetch = serve(
      answer(envelope(undefined, "/b/", { shared: true })),
      answer(envelope("<main><h1>C</h1></main>", "/c/")),
      new Response("<p></p>", { headers: { "content-type": "text/html" } }),
    );
    await router.visit("/b/");
    await router.visit("/c/");
    await router.visit("/b/");
    expect(calls[2]!.headers.has("X-Next-Navigate")).toBe(false);
    expect(fetch.mock.calls.map(([, init]) => init.mode)).toEqual([
      "same-origin",
      "same-origin",
      "same-origin",
    ]);
  });

  it("drops a malformed frame after the first one landed", async () => {
    const { router, host, ends } = setup();
    createReader({ partial: surface, on: () => () => undefined });
    const frame = JSON.stringify({ e: envelope() });
    serve(
      new Response(`event: patch\ndata: ${frame}\n\nevent: patch\ndata: {\n\n`, {
        headers: { "content-type": "text/event-stream" },
      }),
    );
    await router.visit("/b/");
    expect(heading()).toBe("B");
    expect(host.assigned).toEqual([]);
    expect(ends.at(-1)!.status).toBe("complete");
  });

  it("loads the page hard on a malformed first frame", async () => {
    const { router, host } = setup();
    createReader({ partial: surface, on: () => () => undefined });
    serve(
      new Response("event: patch\ndata: {\n\n", {
        headers: { "content-type": "text/event-stream" },
      }),
    );
    await router.visit("/b/");
    expect(heading()).toBe("Home");
    expect(host.assigned).toEqual([`${location.origin}/b/`]);
  });

  it("reports a refused request and loads the requested href hard", async () => {
    const { router, host, events } = setup();
    serve(() => Promise.reject(new TypeError("Failed to fetch")));
    await router.visit("/b/");
    expect(events).toContain("error");
    expect(host.assigned).toEqual([`${location.origin}/b/`]);
  });

  it.each([
    ["a page without the slot", "<main><h1>Home</h1></main>"],
    [
      "markers under two parents",
      "<div><!--nx:s L1 S1--><main><h1>Home</h1></main></div><!--/nx:s L1 S1-->",
    ],
  ])("loads the page hard on %s", async (_name, page) => {
    const { router, host } = setup({ page });
    serve(answer(envelope()));
    await router.visit("/b/");
    expect(host.assigned).toEqual([`${location.origin}/b/`]);
    expect(heading()).toBe("Home");
  });

  it("reports an envelope the applier refused and loads the page hard", async () => {
    const { router, host, events } = setup();
    serve(answer(envelope()));
    document.addEventListener(
      "partial:before-apply",
      (event) => event.preventDefault(),
      { signal: listening.signal },
    );
    await router.visit("/b/");
    expect(events).toContain("error");
    expect(host.assigned).toEqual([`${location.origin}/b/`]);
  });

  it("morphs the children of the body when the answer targets it", async () => {
    const { router } = setup({
      router: {},
      layouts: [],
      page: '<header id="top">Old</header><main><p>one</p></main>',
    });
    serve(
      answer(bodied('<header id="top">New</header><main><p>two</p></main>', "/b/")),
      answer(envelope()),
    );
    const header = document.getElementById("top");
    await router.visit("/b/");
    expect(document.getElementById("top")).toBe(header);
    expect(header!.textContent).toBe("New");
    expect(document.querySelector("main p")!.textContent).toBe("two");
    await router.visit("/c/");
    expect(calls[1]!.headers.get("X-Next-Layouts")).toBe("L2.D2");
  });

  it("replaces the seal of each layout the answer does not keep", async () => {
    const { router } = setup();
    serve(
      answer({
        ...envelope("<main><!--nx:s L3 S9--><p>x</p><!--/nx:s L3 S9--></main>"),
        layouts: [
          ["L1", "D1", null],
          ["L3", "D3", "S9"],
          ["L4", "D4", null],
        ],
      }),
      answer({
        ...envelope(),
        ops: [
          { op: "morph", target: { slot: "L3" }, html: "<p>y</p>" },
          { op: "url", action: "push", href: "/c/" },
        ],
        layouts: [
          ["L1", "D1", null],
          ["L3", "D3", null],
        ],
      }),
    );
    await router.visit("/b/");
    await router.visit("/c/");
    expect(calls[1]!.headers.get("X-Next-Layouts")).toBe("L1.D1,L3.D3,L4.D4");
    expect(document.body.innerHTML).toContain(
      "<!--nx:s L3 S9--><p>y</p><!--/nx:s L3 S9-->",
    );
  });

  it("declares no more layouts than the payload allows", async () => {
    const { router } = setup({
      router: { ...SETTINGS, max_layouts: 1 },
      layouts: [
        ["L1", "D1", "S1"],
        ["L2", "D2", "S2"],
      ],
    });
    serve(answer(envelope()));
    await router.visit("/b/");
    expect(calls[0]!.headers.get("X-Next-Layouts")).toBe("L1.D1");
  });

  it("declares no more bytes than the server reads", async () => {
    const layouts = Array.from({ length: 40 }, (_, index) => [
      `${index}`.padStart(16, "0"),
      "d".repeat(32),
      "s",
    ]);
    const { router } = setup({ layouts });
    serve(answer(envelope()));
    await router.visit("/b/");
    const declared = calls[0]!.headers.get("X-Next-Layouts")!;
    expect(declared.split(",")).toHaveLength(20);
    expect(declared.length).toBeLessThanOrEqual(1024);
  });

  it("asserts the asset version an applied envelope taught", async () => {
    const { router } = setup();
    serve(answer(envelope()), answer(envelope("<main><h1>C</h1></main>", "/c/")));
    await router.visit("/b/");
    await router.visit("/c/");
    expect(calls[0]!.headers.has("X-Next-Version")).toBe(false);
    expect(calls[1]!.headers.get("X-Next-Version")).toBe("v1");
  });

  it("lands the first frame of a streamed navigation through the reader", async () => {
    const { router, events } = setup();
    createReader({ partial: surface, on: () => () => undefined });
    const frame = JSON.stringify({ e: envelope() });
    serve(
      new Response(`event: patch\ndata: ${frame}\n\n`, {
        headers: { "content-type": "text/event-stream" },
      }),
    );
    await router.visit("/b/");
    expect(calls[0]!.headers.get("Accept")).toBe(STREAM_ACCEPT);
    expect(heading()).toBe("B");
    expect(events).toEqual(["end", "before", "start", "before-swap", "commit", "end"]);
  });

  it("marks a boundary of the new slot the stream never filled as incomplete", async () => {
    const { router } = setup();
    createReader({ partial: surface, on: () => () => undefined });
    const html =
      '<main><h1>B</h1><div id="w" data-next-boundary="w" ' +
      'data-next-state="loading"></div></main>';
    const frame = JSON.stringify({ e: envelope(html) });
    serve(
      new Response(`event: patch\ndata: ${frame}\n\nevent: end\ndata: 1\n\n`, {
        headers: { "content-type": "text/event-stream" },
      }),
    );
    await router.visit("/b/");
    const boundary = document.getElementById("w")!;
    expect(boundary.getAttribute("data-next-state")).toBe("error");
    expect(boundary.getAttribute("data-next-error")).toBe("incomplete");
  });

  it("loads the reader the payload names when a navigation streams", async () => {
    const reader = "/static/next/next.reader.min.js";
    const { router } = setup({ router: { ...SETTINGS, reader } });
    const frame = JSON.stringify({ e: envelope() });
    serve(
      new Response(`event: patch\ndata: ${frame}\n\n`, {
        headers: { "content-type": "text/event-stream" },
      }),
    );
    const visit = router.visit("/b/");
    const tag = await vi.waitFor(() => {
      const found = document.head.querySelector(`script[src="${reader}"]`);
      expect(found).not.toBeNull();
      return found!;
    });
    expect(calls[0]!.headers.get("Accept")).toBe(STREAM_ACCEPT);
    createReader({ partial: surface, on: () => () => undefined });
    tag.dispatchEvent(new Event("load"));
    await visit;
    expect(heading()).toBe("B");
  });

  it("reads the content type of a custom partial backend", async () => {
    const type = "application/x-custom+json";
    const { router } = setup({ router: { ...SETTINGS, type } });
    serve(
      new Response(JSON.stringify(envelope()), { headers: { "content-type": type } }),
    );
    await router.visit("/b/");
    expect(heading()).toBe("B");
  });

  it("applies a layer answer without layouts when plain links may open layers", async () => {
    const { router, host, events } = setup({
      router: { ...SETTINGS, intercept: "links" },
    });
    const open = vi.spyOn(surface.layers, "open").mockResolvedValue();
    serve(answer({ version: "", ops: [{ op: "layer.open", href: "/b/", zone: "z" }] }));
    await router.visit("/b/");
    expect(open).toHaveBeenCalledWith(null, "/b/", "z");
    expect(host.assigned).toEqual([]);
    expect(host.states).toHaveLength(1);
    expect(events).not.toContain("commit");
  });

  it.each([
    ["links", [{ op: "event", name: "opened" }]],
    ["opt-in", [{ op: "layer.open", href: "/b/", zone: "z" }]],
  ])("loads hard an answer without layouts under %s", async (intercept, ops) => {
    const { router, host } = setup({ router: { ...SETTINGS, intercept } });
    const open = vi.spyOn(surface.layers, "open").mockResolvedValue();
    serve(answer({ version: "", ops }));
    await router.visit("/b/");
    expect(open).not.toHaveBeenCalled();
    expect(host.assigned).toEqual([`${location.origin}/b/`]);
  });

  it("drops the page-owned context keys the new page does not set", async () => {
    const { router, context } = setup({ context: { page: "a", kept: 1 } });
    serve(
      answer(
        envelope(undefined, "/b/", {
          ops: [
            { op: "morph", target: { slot: "L1" }, html: "<main></main>" },
            { op: "context", data: { kept: 2 }, replace: true },
            { op: "context", data: { other: 3 } },
            { op: "context", data: "nope", replace: true },
            { op: "url", action: "push" },
          ],
        }),
      ),
    );
    await router.visit("/b/");
    expect(context.kept).toBe(2);
    expect(context.other).toBe(3);
    expect(context.page).toBeUndefined();
    expect(context.$router).toBe(SETTINGS);
    expect(location.pathname).toBe("/");
  });

  it("rotates the csrf token and flushes the history", async () => {
    const { router } = setup();
    const keeper = stubKeeper();
    router._install(() => keeper);
    const setCsrf = vi.spyOn(surface, "setCsrf");
    const csrf = { header: "X-CSRFToken", token: "t2" };
    serve(answer(envelope(undefined, "/b/", { csrf })));
    await router.visit("/b/");
    expect(setCsrf).toHaveBeenCalledWith(csrf);
    expect(keeper.flush).toHaveBeenCalledTimes(1);
  });

  it("strips the scripts of the new slot and keeps its data blocks", async () => {
    const { router } = setup();
    const html =
      '<main><script id="s">0</script><script type="application/ld+json">{}</script>' +
      "<template><script>0</script></template></main>";
    serve(answer(envelope(html)));
    await router.visit("/b/");
    expect(document.querySelectorAll("main script")).toHaveLength(1);
    expect(document.querySelector("main script")!.getAttribute("type")).toBe(
      "application/ld+json",
    );
    expect(document.querySelector("main template")!.innerHTML).toBe("");
  });

  it("closes the open layers and replaces their entry instead of pushing", async () => {
    const { router } = setup();
    serve(answer(envelope()));
    vi.spyOn(surface.layers, "size")
      .mockReturnValueOnce(1)
      .mockReturnValueOnce(1)
      .mockReturnValue(0);
    const close = vi.spyOn(surface.layers, "close").mockImplementation(() => undefined);
    const length = history.length;
    await router.visit("/b/");
    expect(close).toHaveBeenCalledTimes(1);
    expect(close).toHaveBeenCalledWith({ dismiss: true, reason: "navigate" });
    expect(history.length).toBe(length);
    expect(location.pathname).toBe("/b/");
  });

  it("leaves the entry key empty while the page owns the entry", async () => {
    const { router } = setup({ state: { app: 1 } });
    const keeper = stubKeeper();
    router._install(() => keeper);
    serve(
      answer({
        ...envelope(),
        ops: [{ op: "morph", target: { slot: "L1" }, html: "" }],
      }),
      answer(envelope()),
    );
    await router.visit("/b/");
    await router.visit("/b/");
    expect(keeper.leave.mock.calls.map(([key]) => key)[1]).toBe("");
  });
});

describe("the history seam", () => {
  it("snapshots the slot it leaves unless the page refuses snapshots", async () => {
    const { router } = setup();
    const keeper = stubKeeper();
    router._install(() => keeper);
    const key = (history.state as Entry).key;
    serve(
      answer(envelope(), { "X-Next-Router": "no-snapshot" }),
      answer(envelope("<main><h1>C</h1></main>", "/c/")),
    );
    await router.visit("/b/");
    await router.visit("/c/");
    expect(keeper.leave.mock.calls).toEqual([
      [key, { slot: "L1" }],
      [expect.any(String), undefined],
    ]);
  });

  it("keeps no snapshot of a document that refuses snapshots", async () => {
    const { router } = setup({ router: { ...SETTINGS, snapshot: false } });
    const keeper = stubKeeper();
    router._install(() => keeper);
    serve(answer(envelope()));
    await router.visit("/b/");
    expect(keeper.leave).toHaveBeenCalledWith(expect.any(String), undefined);
  });

  it("flushes and keeps no snapshot of the page an answer clears", async () => {
    const { router } = setup();
    const keeper = stubKeeper();
    router._install(() => keeper);
    serve(answer(envelope(), { "X-Next-Router": "clear" }));
    await router.visit("/b/");
    expect(keeper.flush).toHaveBeenCalledTimes(1);
    expect(keeper.leave).toHaveBeenCalledWith(expect.any(String), undefined);
  });

  it("flushes on a request that may change state and on a clearing response", () => {
    const { router } = setup();
    const keeper = stubKeeper();
    router._install(() => keeper);
    const request = (method: string): boolean =>
      document.dispatchEvent(
        new CustomEvent("partial:before-request", { detail: { method } }),
      );
    request("GET");
    expect(keeper.flush).not.toHaveBeenCalled();
    request("POST");
    expect(keeper.flush).toHaveBeenCalledTimes(1);
    const cleared = new Response("", { headers: { "X-Next-Router": "clear" } });
    surface.apply({ version: "", ops: [] }, { response: new Response("") });
    surface.apply({ version: "", ops: [] });
    expect(keeper.flush).toHaveBeenCalledTimes(1);
    surface.apply({ version: "", ops: [] }, { response: cleared });
    expect(keeper.flush).toHaveBeenCalledTimes(2);
    router.flush();
    expect(keeper.flush).toHaveBeenCalledTimes(3);
  });

  it("loads the history chunk on idle with the nonce of the router script", () => {
    vi.useFakeTimers();
    const script = document.createElement("script");
    script.nonce = "n0";
    Object.defineProperty(document, "currentScript", {
      configurable: true,
      get: () => script,
    });
    const chunk = "/static/next/next.history.min.js";
    try {
      setup({ router: { ...SETTINGS, history: chunk } });
    } finally {
      delete (document as { currentScript?: unknown }).currentScript;
    }
    expect(document.head.querySelector("script")).toBeNull();
    vi.runOnlyPendingTimers();
    const tag = document.head.querySelector<HTMLScriptElement>("script")!;
    expect(tag.getAttribute("src")).toBe(chunk);
    expect(tag.nonce).toBe("n0");
  });

  it("asks for an idle callback with a deadline where the browser has one", () => {
    const idle = vi.fn<(run: () => void, options: IdleRequestOptions) => number>(
      () => 1,
    );
    vi.stubGlobal("requestIdleCallback", idle);
    const { router } = setup();
    expect(idle).toHaveBeenCalledTimes(1);
    expect(idle.mock.calls[0]![1]).toEqual({ timeout: 2000 });
    const keeper = stubKeeper();
    router._install(() => keeper);
    idle.mock.calls[0]![0]();
    expect(document.head.querySelector("script")).toBeNull();
  });
});

describe("the reader on idle", () => {
  it("loads the reader once idle and reuses it for a streamed navigation", async () => {
    const idle = vi.fn<(run: () => void) => number>(() => 1);
    vi.stubGlobal("requestIdleCallback", idle);
    const reader = "/static/next/next.reader.min.js";
    const { router } = setup({ router: { ...SETTINGS, reader } });
    idle.mock.calls[0]![0]();
    const tags = (): NodeListOf<Element> =>
      document.head.querySelectorAll(`script[src="${reader}"]`);
    expect(tags()).toHaveLength(1);
    const frame = JSON.stringify({ e: envelope() });
    serve(
      new Response(`event: patch\ndata: ${frame}\n\n`, {
        headers: { "content-type": "text/event-stream" },
      }),
    );
    const visit = router.visit("/b/");
    createReader({ partial: surface, on: () => () => undefined });
    tags()[0]!.dispatchEvent(new Event("load"));
    await visit;
    expect(tags()).toHaveLength(1);
    expect(heading()).toBe("B");
  });

  it("asks for no reader on idle once one has landed", () => {
    const idle = vi.fn<(run: () => void) => number>(() => 1);
    vi.stubGlobal("requestIdleCallback", idle);
    const reader = "/static/next/next.reader.min.js";
    setup({ router: { ...SETTINGS, reader } });
    createReader({ partial: surface, on: () => () => undefined });
    idle.mock.calls[0]![0]();
    expect(document.head.querySelector("script")).toBeNull();
  });
});

describe("history and the pop answer", () => {
  it("stamps the entry it booted on and keeps the key of one it wrote", () => {
    const { host } = setup({ state: { next: 1, kind: "nav", key: "k9" } });
    expect(host.states).toEqual([{ next: 1, kind: "nav", key: "k9" }]);
  });

  it("mints a key for an entry of its own that lost it", () => {
    const { host } = setup({ state: { next: 1, kind: "nav" } });
    expect(host.states[0]!.key).toEqual(expect.any(String));
  });

  it("leaves a history state of the page alone", () => {
    const { host } = setup({ state: { app: 1 } });
    expect(host.states).toEqual([]);
  });

  it("reads no settings from a payload without them", async () => {
    const { router } = setup({ context: { $router: undefined, $layouts: undefined } });
    serve(answer(bodied("<main><h1>B</h1></main>", "/b/")));
    await router.visit("/b/");
    expect(calls[0]!.headers.get("X-Next-Layouts")).toBe("");
    expect(heading()).toBe("B");
  });

  it("revalidates a popped entry with a request when no history chunk loaded", async () => {
    const { router } = setup();
    serve(answer(envelope()), answer(envelope("<main><h1>Fresh</h1></main>", "/")));
    const home = history.state as Entry;
    await router.visit("/b/");
    const length = history.length;
    await back(home);
    expect(heading()).toBe("Fresh");
    expect(history.length).toBe(length);
    expect(reload).not.toHaveBeenCalled();
    expect(calls[1]!.headers.get("X-Next-Origin")).toBe("/");
    expect(calls[1]!.headers.get("X-Next-Layouts")).toBe("L1.D1");
    expect(scrolled).not.toHaveBeenCalled();
  });

  it("hands a pop to the history chunk and places the scroll it did not restore", async () => {
    const { router } = setup();
    const keeper = stubKeeper();
    router._install(() => keeper);
    serve(answer(envelope()), answer(envelope("<main><h1>Home</h1></main>", "/")));
    const home = history.state as Entry;
    await router.visit("/b/");
    const left = (history.state as Entry).key;
    await back(home);
    expect(keeper.pop).toHaveBeenCalledWith(left, home.key, true, "/");
    expect(keeper.place).toHaveBeenCalledWith(home.key);
    expect(keeper.leave).toHaveBeenCalledTimes(1);
  });

  it("asks nothing for a pop the history chunk restored within the prefetch lifetime", async () => {
    const { router } = setup({ router: { ...SETTINGS, prefetch_ttl: 5 } });
    const keeper = stubKeeper(Date.now() - 4_000);
    router._install(() => keeper);
    serve(answer(envelope()));
    const home = history.state as Entry;
    await router.visit("/b/");
    const ended = vi.fn();
    document.addEventListener("router:end", ended, { signal: listening.signal });
    pop(home);
    await wait(10);
    expect(calls).toHaveLength(1);
    expect(ended).not.toHaveBeenCalled();
    expect(keeper.pop).toHaveBeenCalledTimes(1);
  });

  it("revalidates a pop it restored once the snapshot is older than the default lifetime", async () => {
    const { router } = setup();
    const keeper = stubKeeper(Date.now() - 30_000);
    router._install(() => keeper);
    serve(answer(envelope()), answer(envelope("<main><h1>Home</h1></main>", "/")));
    const home = history.state as Entry;
    await router.visit("/b/");
    scrolled.mockClear();
    await back(home);
    expect(calls).toHaveLength(2);
    expect(keeper.place).not.toHaveBeenCalled();
    expect(scrolled).not.toHaveBeenCalled();
  });

  it("requests a zone-pushed entry after a router visit instead of reloading", async () => {
    const { router } = setup();
    serve(answer(envelope()), answer(envelope("<main><h1>Q</h1></main>", "/?q=1")));
    surface.apply({ version: "", ops: [{ op: "url", action: "push", href: "/?q=1" }] });
    const zone = history.state as Entry;
    expect(zone).toEqual({ next: 1, kind: "nav", key: expect.any(String) });
    await router.visit("/b/");
    await back(zone, "/?q=1");
    expect(reload).not.toHaveBeenCalled();
    expect(calls).toHaveLength(2);
    expect(heading()).toBe("Q");
  });

  it("pops to an entry of its own without a key with a request", async () => {
    setup();
    serve(answer(envelope("<main><h1>X</h1></main>", "/x/")));
    await back({ next: 1, kind: "nav" }, "/x/");
    expect(heading()).toBe("X");
    expect((history.state as Entry).key).toEqual(expect.any(String));
  });

  it("leaves a pop to an entry it never wrote to the runtime", async () => {
    const { router } = setup();
    const fetch = serve(answer(envelope()));
    await router.visit("/b/");
    pop(null, "/x/");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("scrolls to the fragment of a same-path pop and nowhere without one", () => {
    loaded(setup({ page: `${PAGE}<p id="frag">f</p>` }).router);
    const fetch = serve();
    pop(null, "/#frag");
    expect(intoView).toHaveBeenCalledTimes(1);
    pop(null, "/");
    expect(intoView).toHaveBeenCalledTimes(1);
    expect(scrolled).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
    expect(reload).not.toHaveBeenCalled();
  });

  it("follows the key of a same-page entry it pops to", () => {
    const { router } = setup({ page: `${PAGE}<p id="frag">f</p>` });
    const seams = seamsOf(loaded(router));
    pop({ next: 1, kind: "nav", key: "k5" }, "/#frag");
    expect(seams.key()).toBe("k5");
    expect(intoView).toHaveBeenCalledTimes(1);
  });

  it("stamps a fragment entry the browser made so a pop back to it stays soft", async () => {
    const { router, host } = setup();
    serve(answer(envelope()), answer(envelope("<main><h1>Home</h1></main>", "/")));
    const booted = (history.state as Entry).key;
    history.pushState(null, "", "/#part");
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    const entry = history.state as Entry;
    expect(entry).toEqual({ next: 1, kind: "nav", key: expect.any(String) });
    expect(entry.key).not.toBe(booted);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    expect(host.states).toHaveLength(2);
    await router.visit("/b/");
    await back(entry, "/#part");
    expect(reload).not.toHaveBeenCalled();
    expect(heading()).toBe("Home");
  });

  it("scrolls a pushed page to the top once the history chunk loaded", async () => {
    const { router } = setup();
    loaded(router);
    serve(answer(envelope()));
    await router.visit("/b/");
    expect(scrolled.mock.calls).toEqual([[0, 0]]);
  });

  it("scrolls to the top and once more at the end when the fragment is missing", async () => {
    const { router } = setup();
    loaded(router);
    serve(answer(envelope()));
    await router.visit("/b/#gone");
    expect(scrolled.mock.calls).toEqual([
      [0, 0],
      [0, 0],
    ]);
  });
});

describe("the navigated event of a pop", () => {
  const HOME = envelope("<main><h1>Home</h1></main>", "/");

  function recorded(events: string[]): Detail[] {
    const details: Detail[] = [];
    document.addEventListener(
      "next:navigated",
      (event) => {
        const { path, title, action } = (event as CustomEvent<Detail>).detail;
        events.push("navigated");
        details.push({ path, title, action });
      },
      { signal: listening.signal },
    );
    return details;
  }

  async function left(router: Router, events: string[]): Promise<Entry> {
    const home = history.state as Entry;
    await router.visit("/b/");
    events.length = 0;
    return home;
  }

  it("fires once with the title of the revalidated page", async () => {
    const { router, events } = setup();
    serve(answer(envelope()), answer(HOME));
    const home = await left(router, events);
    const details = recorded(events);
    const done = back(home);
    expect(details).toEqual([]);
    await done;
    expect(details).toEqual([{ path: "/", title: "Page /", action: "pop" }]);
    expect(events).toEqual(["start", "before-swap", "navigated", "commit", "end"]);
  });

  it("fires once with the title of a restored snapshot", async () => {
    const { router, events } = setup();
    loaded(router);
    serve(answer(envelope()));
    const home = await left(router, events);
    const details = recorded(events);
    pop(home);
    await wait(0);
    expect(heading()).toBe("Home");
    expect(details).toEqual([{ path: "/", title: "Home", action: "pop" }]);
    expect(events).toEqual(["navigated"]);
  });

  it("fires once for a streamed revalidation", async () => {
    const { router, events } = setup();
    createReader({ partial: surface, on: () => () => undefined });
    const frame = JSON.stringify({ e: HOME });
    serve(
      answer(envelope()),
      new Response(`event: patch\ndata: ${frame}\n\nevent: end\ndata: 1\n\n`, {
        headers: { "content-type": "text/event-stream" },
      }),
    );
    const home = await left(router, events);
    const details = recorded(events);
    await back(home);
    expect(details).toEqual([{ path: "/", title: "Page /", action: "pop" }]);
    expect(events).toEqual(["start", "before-swap", "navigated", "commit", "end"]);
  });

  it("stays a pop when the revalidation redirects", async () => {
    const { router, events } = setup();
    serve(answer(envelope()), answer(envelope("<main><h1>Home</h1></main>", "/home/")));
    const home = await left(router, events);
    const details = recorded(events);
    await back(home);
    expect(details).toEqual([{ path: "/home/", title: "Page /home/", action: "pop" }]);
  });

  it("leaves a pop the next visit supersedes to that visit", async () => {
    const { router, events } = setup();
    let release = (): void => undefined;
    serve(
      answer(envelope()),
      (signal) =>
        new Promise((resolve, reject) => {
          release = () => resolve(answer(HOME));
          signal?.addEventListener("abort", () => reject(signal.reason as Error));
        }),
      answer(envelope("<main><h1>C</h1></main>", "/c/")),
    );
    const home = await left(router, events);
    const details = recorded(events);
    pop(home);
    await wait(0);
    expect(calls).toHaveLength(2);
    await router.visit("/c/");
    release();
    expect(details).toEqual([{ path: "/c/", title: "Page /c/", action: "push" }]);
  });

  it("drops a pop a visit of the same task overtakes", async () => {
    const { router, events } = setup();
    serve(answer(envelope()), answer(envelope("<main><h1>C</h1></main>", "/c/")));
    const home = await left(router, events);
    const details = recorded(events);
    pop(home);
    await router.visit("/c/");
    await wait(0);
    expect(calls).toHaveLength(2);
    expect(details).toEqual([{ path: "/c/", title: "Page /c/", action: "push" }]);
  });
});

describe("focus, announcement and progress", () => {
  it.each([
    ['<main><h1 id="t">B</h1></main>', "-1"],
    ['<section id="t" data-next-focus tabindex="0">S</section>', "0"],
    ['<main><h1>B</h1><div id="t" data-next-focus>F</div></main>', "-1"],
    ['<p id="t">plain</p>', "-1"],
  ])("moves focus into the new slot of %s", async (html, tabindex) => {
    const { router } = setup();
    router._install(createSwap);
    serve(answer(envelope(html)));
    await router.visit("/b/");
    const target = document.getElementById("t")!;
    expect(document.activeElement).toBe(target);
    expect(target.getAttribute("tabindex")).toBe(tabindex);
    target.blur();
    expect(target.getAttribute("tabindex")).toBe(tabindex === "-1" ? null : "0");
  });

  it("announces the title when focus has nowhere to go", async () => {
    const { router } = setup();
    router._install(createSwap);
    const empty = (href: string): Detail =>
      envelope(undefined, href, {
        ops: [
          { op: "morph", target: { slot: "L1" } },
          { op: "url", action: "push", href },
          { op: "meta", title: `Page ${href}` },
        ],
      });
    serve(answer(empty("/b/")), answer(empty("/c/")));
    await router.visit("/b/");
    await wait(60);
    const region = document.querySelector("[aria-live=polite]")!;
    expect(region.textContent).toBe("Page /b/");
    await router.visit("/c/");
    await wait(60);
    expect(document.querySelectorAll("[aria-live=polite]")).toHaveLength(1);
    expect(region.textContent).toBe("Page /c/");
  });

  it("keeps the scroll and focus of a link that asks to preserve them", async () => {
    setup({
      page: PAGE.replace(
        'id="b" href="/b/"',
        'id="b" href="/b/" data-next-scroll="preserve"',
      ),
    });
    serve(answer(envelope()));
    const done = settled();
    click(document.getElementById("b")!);
    await done;
    expect(scrolled).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(document.body);
  });

  it("lets custom focus and announce functions take over", async () => {
    const { router } = setup();
    const focus = vi.fn(() => false);
    const announce = vi.fn();
    router.configure({ focus, announce });
    router.configure({});
    serve(answer(envelope()));
    await router.visit("/b/");
    expect(focus).toHaveBeenCalledWith([document.querySelector("main")]);
    expect(announce).toHaveBeenCalledWith("Page /b/");
  });

  it("marks the activated link at once and the document after the progress delay", async () => {
    setup();
    let release: (response: Response) => void = () => undefined;
    serve(() => new Promise<Response>((resolve) => (release = resolve)));
    vi.useFakeTimers();
    const link = document.getElementById("b")!;
    const done = settled();
    click(link);
    expect(link.hasAttribute("data-next-pending")).toBe(true);
    vi.advanceTimersByTime(149);
    expect(document.documentElement.hasAttribute("data-next-navigating")).toBe(false);
    vi.advanceTimersByTime(1);
    expect(document.documentElement.hasAttribute("data-next-navigating")).toBe(true);
    expect(document.body.getAttribute("aria-busy")).toBe("true");
    vi.useRealTimers();
    release(answer(envelope()));
    await done;
    expect(link.hasAttribute("data-next-pending")).toBe(false);
    expect(document.documentElement.hasAttribute("data-next-navigating")).toBe(false);
    expect(document.body.hasAttribute("aria-busy")).toBe(false);
  });

  it("keeps the marks of a newer visit when the one it superseded settles", async () => {
    setup({ page: `${PAGE}<a id="c" href="/c/">C</a>` });
    let release: (response: Response) => void = () => undefined;
    serve(
      (signal) =>
        new Promise<Response>((_resolve, reject) =>
          signal!.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          ),
        ),
      () => new Promise<Response>((resolve) => (release = resolve)),
    );
    const first = document.getElementById("b")!;
    const second = document.getElementById("c")!;
    const superseded = settled();
    click(first);
    click(second);
    expect((await superseded).status).toBe("superseded");
    expect(first.hasAttribute("data-next-pending")).toBe(true);
    expect(second.hasAttribute("data-next-pending")).toBe(true);
    const done = settled();
    release(answer(envelope("<main><h1>C</h1></main>", "/c/")));
    await done;
    expect(first.hasAttribute("data-next-pending")).toBe(false);
    expect(second.hasAttribute("data-next-pending")).toBe(false);
  });

  it("clears the progress once the first frame of a stream commits", async () => {
    const { router } = setup({ router: { ...SETTINGS, progress_delay: 0.05 } });
    createReader({ partial: surface, on: () => () => undefined });
    let finish = (): void => undefined;
    const frame = JSON.stringify({ e: envelope() });
    const body = new ReadableStream<Uint8Array<ArrayBuffer>>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(`event: patch\ndata: ${frame}\n\n`),
        );
        finish = () => controller.close();
      },
    });
    serve(new Response(body, { headers: { "content-type": "text/event-stream" } }));
    const committed = new Promise<void>((resolve) =>
      document.addEventListener("router:commit", () => resolve(), { once: true }),
    );
    const ended = vi.fn();
    document.addEventListener("router:end", ended, { signal: listening.signal });
    const visit = router.visit("/b/");
    await committed;
    await wait(80);
    expect(ended).not.toHaveBeenCalled();
    expect(document.documentElement.hasAttribute("data-next-navigating")).toBe(false);
    finish();
    await visit;
    expect(ended).toHaveBeenCalledTimes(1);
  });
});

describe("the router API", () => {
  it("visits an owned URL through the router and loads any other hard", async () => {
    const { router, host } = setup();
    serve(answer(envelope("<main><h1>O</h1></main>", "/other")));
    await router.visit("/other");
    expect(calls).toHaveLength(1);
    await router.visit("/static/x.css");
    await router.visit("#top");
    await router.visit("javascript:alert(1)");
    expect(host.assigned).toEqual([
      `${location.origin}/static/x.css`,
      `${location.origin}/other#top`,
    ]);
  });

  it("replaces the entry of a visit that asks to", async () => {
    const { router } = setup();
    serve(answer(envelope()));
    const length = history.length;
    await router.visit("/b/", { replace: true });
    expect(history.length).toBe(length);
    expect(location.pathname).toBe("/b/");
  });

  it("moves its listeners to a configured host", () => {
    const { router, host } = setup();
    const other = fakeHost();
    router.configure({ host: other });
    expect(host.stops).toBe(1);
    expect(other.listens).toBe(1);
  });

  it("hands the history chunk its seams", () => {
    const { router } = setup();
    router.flush();
    let seams: HistoryHost | undefined;
    router._install((host) => {
      seams = host;
      return stubKeeper();
    });
    expect(seams!.settings()).toBe(SETTINGS);
    expect(seams!.key()).toBe((history.state as Entry).key);
    expect(seams!.region({ body: true })).toEqual([document.body, null, null]);
    expect(seams!.region({ slot: "L1" })![0]).toBe(document.body);
    expect(seams!.region({ zone: "flash" })).toBeUndefined();
    expect(seams!.nonce).toBeUndefined();
    expect(seams!.chain()).toEqual([["L1", "D1", "S1"]]);
    seams!.chain([["L2", "D2", "S2"]]);
    expect(seams!.chain()).toEqual([["L2", "D2", "S2"]]);
    expect(seams!.ready).toBeUndefined();
    seams!.flush();
    expect(seams!.owned(new URL("/b/", location.href))).toBe(true);
    expect(seams!.owned(new URL("/static/a.css", location.href))).toBe(false);
  });

  it("asks with the navigation headers a prefetch adds to", async () => {
    const { router } = setup();
    let seams: HistoryHost | undefined;
    router._install((host) => {
      seams = host;
      return stubKeeper();
    });
    serve();
    const signal = new AbortController().signal;
    await seams!.ask("/c/", signal, { "X-Next-Prefetch": "1" });
    expect(calls[0]!.headers.get("X-Next-Navigate")).toBe("1");
    expect(calls[0]!.headers.get("X-Next-Layouts")).toBe("L1.D1");
    expect(calls[0]!.headers.get("X-Next-Prefetch")).toBe("1");
    expect(calls[0]!.signal).toBe(signal);
  });

  it("prefetches through the intent chunk once it loads", async () => {
    const { router } = setup();
    await router.prefetch("/c/");
    const keeper = stubKeeper();
    router._install(() => ({ prefetch: keeper.prefetch }));
    await router.prefetch("/c/#top");
    expect(keeper.prefetch.mock.calls).toEqual([[`${location.origin}/c/#top`]]);
  });

  it("merges the parts each chunk installs and flushes them all", () => {
    const { router } = setup();
    const keeper = stubKeeper();
    router._install(() => ({ flush: keeper.flush }));
    router._install(() => ({ drop: keeper.drop }));
    router.flush();
    expect(keeper.flush).toHaveBeenCalledTimes(1);
    expect(keeper.drop).toHaveBeenCalledTimes(1);
  });

  it("loads the history and the intent chunks together", () => {
    vi.useFakeTimers();
    const historyChunk = "/static/next/next.history.min.js";
    const intentChunk = "/static/next/next.intent.min.js";
    setup({ router: { ...SETTINGS, history: historyChunk, intent: intentChunk } });
    vi.runOnlyPendingTimers();
    const scripts = document.head.querySelectorAll("script");
    expect([...scripts].map((el) => el.getAttribute("src"))).toEqual([
      historyChunk,
      intentChunk,
    ]);
  });
});

describe("prefetched shells", () => {
  it("applies a prefetched shell at once and revalidates it behind", async () => {
    const shell = envelope('<main><h1>B</h1><p id="w">loading</p></main>');
    const keeper = stubKeeper(0, { raw: shell, response: answer({}) });
    const { router, events } = setup();
    router._install(() => keeper);
    serve(answer(envelope('<main><h1>B</h1><p id="w">ready</p></main>')));
    const cached: unknown[] = [];
    document.addEventListener(
      "router:before",
      (event) => cached.push((event as CustomEvent<Detail>).detail.cached),
      { signal: listening.signal },
    );
    const length = history.length;
    await router.visit("/b/");
    expect(cached).toEqual([true]);
    expect(keeper.take).toHaveBeenCalledWith(`${location.origin}/b/`);
    expect(calls).toHaveLength(1);
    expect(document.getElementById("w")!.textContent).toBe("ready");
    expect(history.length).toBe(length + 1);
    expect(location.pathname).toBe("/b/");
    expect(events.filter((name) => name === "commit")).toHaveLength(1);
    expect(keeper.leave).toHaveBeenCalledTimes(1);
    expect(keeper.swap).toHaveBeenCalledTimes(1);
    expect(keeper.swap.mock.calls[0]![3]).toBe("forward");
    expect(keeper.turn.before).toHaveBeenCalledTimes(1);
    expect(keeper.turn.hold).toHaveBeenCalledTimes(1);
    expect(keeper.turn.after).toHaveBeenCalledWith(
      expect.objectContaining({ version: "v1" }),
      [document.querySelector("main")],
      true,
    );
    expect((history.state as Entry).key).toBe(seamsOf(router).key());
  });

  it("commits a prefetch that skipped no deferred job without a request", async () => {
    const page = envelope('<main><h1>B</h1><p id="w">ready</p></main>', "/b/", {
      complete: true,
    });
    const keeper = stubKeeper(0, { raw: page, response: answer({}) });
    const { router, events, ends } = setup();
    router._install(() => keeper);
    const fetch = serve();
    await router.visit("/b/");
    expect(fetch).not.toHaveBeenCalled();
    expect(document.getElementById("w")!.textContent).toBe("ready");
    expect(location.pathname).toBe("/b/");
    expect(events.filter((name) => name === "commit")).toHaveLength(1);
    expect(ends.at(-1)!.status).toBe("complete");
  });

  it("fills the boundaries of a stream that answers behind a prefetched shell", async () => {
    const loading =
      '<main><h1>B</h1><div id="w" data-next-boundary="w" ' +
      'data-next-state="loading">loading</div></main>';
    const keeper = stubKeeper(0, { raw: envelope(loading), response: answer({}) });
    const { router, host, ends } = setup();
    router._install(() => keeper);
    createReader({ partial: surface, on: () => () => undefined });
    const shell = JSON.stringify({ e: envelope(loading) });
    const chunk = JSON.stringify({
      z: "w",
      s: "ready",
      e: {
        ops: [{ op: "inner", target: { boundary: "w" }, html: "ready" }],
        assets: [],
      },
    });
    serve(
      new Response(
        `event: patch\ndata: ${shell}\n\nevent: patch\ndata: ${chunk}\n\n` +
          "event: end\ndata: 1\n\n",
        { headers: { "content-type": "text/event-stream" } },
      ),
    );
    await router.visit("/b/");
    const boundary = document.getElementById("w")!;
    expect(boundary.textContent).toBe("ready");
    expect(boundary.getAttribute("data-next-state")).toBe("ready");
    expect(boundary.hasAttribute("data-next-error")).toBe(false);
    expect(host.assigned).toEqual([]);
    expect(ends.at(-1)!.status).toBe("complete");
  });

  it("loads the page hard and drops its request when the shell cannot apply", async () => {
    const shell = { version: "", ops: [{ op: "visit", href: "/b/" }], layouts: [] };
    const keeper = stubKeeper(0, { raw: shell, response: answer({}) });
    const { router, host, ends } = setup();
    router._install(() => keeper);
    serve(
      (signal) =>
        new Promise((_resolve, reject) =>
          signal?.addEventListener("abort", () => reject(signal.reason as Error)),
        ),
    );
    await router.visit("/b/");
    expect(calls[0]!.signal!.aborted).toBe(true);
    expect(host.assigned).toEqual([`${location.origin}/b/`]);
    expect(ends.at(-1)!.status).toBe("hard");
  });

  it("names the transition of a replace and of a pop", async () => {
    const keeper = stubKeeper();
    const { router } = setup();
    router._install(() => keeper);
    serve(answer(envelope()), answer(envelope("<main><h1>Home</h1></main>", "/")));
    const home = history.state as Entry;
    await router.visit("/b/", { replace: true });
    await back(home);
    expect(keeper.swap.mock.calls.map((call) => call[3])).toEqual(["replace", "back"]);
  });

  it("hands a layer answer no swap and snapshots nothing", async () => {
    const keeper = stubKeeper();
    const { router } = setup({ router: { ...SETTINGS, intercept: "links" } });
    router._install(() => keeper);
    vi.spyOn(surface.layers, "open").mockResolvedValue();
    serve(answer({ version: "", ops: [{ op: "layer.open", href: "/b/", zone: "z" }] }));
    await router.visit("/b/");
    expect(keeper.swap).not.toHaveBeenCalled();
    expect(keeper.leave).toHaveBeenCalledWith(expect.any(String), undefined);
  });

  it("waits for the history chunk before it lands a navigation", async () => {
    const chunk = "/static/next/next.history.min.js";
    const { router } = setup({ router: { ...SETTINGS, history: chunk } });
    serve(answer(envelope()));
    const visit = router.visit("/b/");
    const tag = await vi.waitFor(() => {
      const found = document.head.querySelector(`script[src="${chunk}"]`);
      expect(found).not.toBeNull();
      return found!;
    });
    await wait(10);
    expect(heading()).toBe("Home");
    tag.dispatchEvent(new Event("load"));
    await visit;
    expect(heading()).toBe("B");
    expect(document.head.querySelectorAll("script")).toHaveLength(1);
  });

  it("loads the history chunk at once on an entry it wrote before a reload", () => {
    vi.useFakeTimers();
    const chunk = "/static/next/next.history.min.js";
    setup({
      router: { ...SETTINGS, history: chunk },
      state: { next: 1, kind: "nav", key: "k9" },
    });
    expect(document.head.querySelectorAll(`script[src="${chunk}"]`)).toHaveLength(1);
    vi.runOnlyPendingTimers();
    expect(document.head.querySelectorAll(`script[src="${chunk}"]`)).toHaveLength(1);
  });

  it("waits for the history chunk before it lands a prefetched shell", async () => {
    const chunk = "/static/next/next.history.min.js";
    const keeper = stubKeeper(0, { raw: envelope(), response: answer({}) });
    const { router } = setup({ router: { ...SETTINGS, history: chunk } });
    router._install(() => keeper);
    serve(answer(envelope()));
    const visit = router.visit("/b/");
    const tag = await vi.waitFor(() => {
      const found = document.head.querySelector(`script[src="${chunk}"]`);
      expect(found).not.toBeNull();
      return found!;
    });
    await wait(10);
    expect(keeper.swap).not.toHaveBeenCalled();
    tag.dispatchEvent(new Event("error"));
    await visit;
    expect(keeper.swap).toHaveBeenCalledTimes(1);
    expect(heading()).toBe("B");
  });

  it("lands without a chunk that never answers once its wait runs out", async () => {
    vi.useFakeTimers();
    const chunk = "/static/next/next.history.min.js";
    const keeper = stubKeeper(0, { raw: envelope(), response: answer({}) });
    const { router } = setup({ router: { ...SETTINGS, history: chunk } });
    router._install(() => keeper);
    serve(new Response(null, { status: 500 }));
    const visit = router.visit("/b/");
    await vi.advanceTimersByTimeAsync(CHUNK_TIMEOUT - 1);
    expect(heading()).toBe("Home");
    await vi.advanceTimersByTimeAsync(1);
    vi.useRealTimers();
    await visit;
    expect(heading()).toBe("B");
  });

  function flying(keeper: ReturnType<typeof stubKeeper>): (hit: Prefetched) => void {
    let arrive: (hit: Prefetched) => void = () => undefined;
    keeper.take.mockReturnValue(
      new Promise<Prefetched | undefined>((done) => (arrive = done)),
    );
    return (hit) => arrive(hit);
  }

  function commits(): (string | null)[] {
    const shown: (string | null)[] = [];
    document.addEventListener(
      "router:commit",
      () => shown.push(document.getElementById("w")?.textContent ?? null),
      { signal: listening.signal },
    );
    return shown;
  }

  function held(): [Reply, (response: Response) => void] {
    let release: (response: Response) => void = () => undefined;
    const reply: Reply = (signal) =>
      new Promise((resolve, reject) => {
        release = resolve;
        signal?.addEventListener("abort", () => reject(signal.reason as Error));
      });
    return [reply, (response) => release(response)];
  }

  it("loads the page hard once when the prefetched shell throws as it lands", async () => {
    const keeper = stubKeeper(0, { raw: envelope(), response: answer({}) });
    keeper.swap.mockImplementation(() => {
      throw new Error("broken keeper");
    });
    const { router, host, ends, events } = setup();
    router._install(() => keeper);
    const [reply] = held();
    serve(reply);
    await expect(router.visit("/b/")).resolves.toBeUndefined();
    expect(calls[0]!.signal!.aborted).toBe(true);
    expect(host.assigned).toEqual([`${location.origin}/b/`]);
    expect(events.filter((name) => name === "error")).toHaveLength(1);
    expect(ends.at(-1)!.status).toBe("hard");
  });

  it("asks for the page at once while it lands a prefetched shell", () => {
    const shell = envelope('<main><h1>B</h1><p id="w">loading</p></main>');
    const keeper = stubKeeper(0, { raw: shell, response: answer({}) });
    const { router } = setup();
    router._install(() => keeper);
    const [reply] = held();
    serve(reply);
    void router.visit("/b/");
    expect(calls).toHaveLength(1);
    expect(calls[0]!.headers.has("X-Next-Prefetch")).toBe(false);
  });

  it("waits for a prefetch in flight and asks for the page once its shell lands", async () => {
    const shell = envelope('<main><h1>B</h1><p id="w">loading</p></main>');
    const keeper = stubKeeper();
    const arrive = flying(keeper);
    const { router } = setup();
    router._install(() => keeper);
    serve(answer(envelope('<main><h1>B</h1><p id="w">ready</p></main>')));
    const shown = commits();
    const visit = router.visit("/b/");
    await wait(10);
    expect(calls).toHaveLength(0);
    arrive({ raw: shell, response: answer({}) });
    await visit;
    expect(calls).toHaveLength(1);
    expect(shown).toEqual(["loading"]);
    expect(document.getElementById("w")!.textContent).toBe("ready");
    expect(keeper.swap).toHaveBeenCalledTimes(1);
  });

  it("asks for the page once a prefetch in flight brings nothing", async () => {
    const keeper = stubKeeper();
    let arrive: (hit: undefined) => void = () => undefined;
    keeper.take.mockReturnValue(new Promise((done) => (arrive = done)));
    const { router } = setup();
    router._install(() => keeper);
    serve(answer(envelope()));
    const visit = router.visit("/b/");
    await wait(10);
    expect(calls).toHaveLength(0);
    arrive(undefined);
    await visit;
    expect(calls).toHaveLength(1);
    expect(heading()).toBe("B");
  });

  it("commits a prefetch in flight that arrives complete without a request", async () => {
    const page = envelope('<main><h1>B</h1><p id="w">whole</p></main>', "/b/", {
      complete: true,
    });
    const keeper = stubKeeper();
    const arrive = flying(keeper);
    const { router, ends, host } = setup();
    router._install(() => keeper);
    const fetch = serve();
    const visit = router.visit("/b/");
    arrive({ raw: page, response: answer({}) });
    await visit;
    expect(fetch).not.toHaveBeenCalled();
    expect(document.getElementById("w")!.textContent).toBe("whole");
    expect(host.assigned).toEqual([]);
    expect(ends.at(-1)!.status).toBe("complete");
  });

  it("ignores an empty prefetch in flight and lands the stream", async () => {
    const keeper = stubKeeper();
    keeper.take.mockReturnValue(Promise.resolve(undefined));
    const { router } = setup();
    router._install(() => keeper);
    serve(answer(envelope()));
    await router.visit("/b/");
    expect(heading()).toBe("B");
  });
});

describe("foreign answers", () => {
  const PAGE_HTML = "<html><body><main><h1>F</h1></main></body></html>";

  function html(body = PAGE_HTML): Response {
    return new Response(body, { headers: { "content-type": "text/html" } });
  }

  it("remembers a URL that answered a document and loads it hard from then on", async () => {
    const { router, host } = setup();
    loaded(router);
    const fetch = serve(html());
    await router.visit("/b/");
    expect(host.assigned).toEqual([`${location.origin}/b/`]);
    expect(JSON.parse(sessionStorage.getItem("nx:foreign")!)).toEqual(["/b/"]);
    expect(click(document.getElementById("b")!)).toBe(false);
    await router.visit("/b/");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(host.assigned).toHaveLength(2);
  });

  it("remembers the final URL of a document once and forgets it on a flush", async () => {
    const { router } = setup();
    loaded(router);
    const login = (): Response => {
      const response = html();
      Object.defineProperty(response, "url", { value: `${location.origin}/login/` });
      return response;
    };
    serve(login(), login());
    await router.visit("/b/");
    await router.visit("/c/");
    expect(JSON.parse(sessionStorage.getItem("nx:foreign")!)).toEqual(["/login/"]);
    router.flush();
    expect(JSON.parse(sessionStorage.getItem("nx:foreign")!)).toEqual([]);
  });

  it("navigates when the session store holds no readable memo", async () => {
    const { router } = setup();
    loaded(router);
    sessionStorage.setItem("nx:foreign", "{");
    serve(answer(envelope()));
    await router.visit("/b/");
    expect(heading()).toBe("B");
  });

  it("remembers nothing before the history chunk loads", async () => {
    const { router, host } = setup();
    serve(html(), html());
    await router.visit("/b/");
    await router.visit("/b/");
    expect(sessionStorage.getItem("nx:foreign")).toBeNull();
    expect(calls).toHaveLength(2);
    expect(host.assigned).toHaveLength(2);
  });

  it("remembers no stream it could not read", async () => {
    const { router } = setup();
    loaded(router);
    serve(new Response("", { headers: { "content-type": "text/event-stream" } }));
    await router.visit("/b/");
    expect(sessionStorage.getItem("nx:foreign")).toBeNull();
  });

  it("carves a foreign document into the page and fetches it plain from then on", async () => {
    const keeper = stubKeeper();
    const { router, host } = setup({ router: { ...SETTINGS, foreign: "extract" } });
    loaded(router)._install(() => ({ extract: keeper.extract }));
    serve(
      html(),
      answer(envelope("<main><h1>C</h1></main>", "/c/")),
      html("<p>again</p>"),
    );
    await router.visit("/b/");
    expect(keeper.extract).toHaveBeenCalledWith(PAGE_HTML, "/b/");
    expect(heading()).toBe("X");
    expect(host.assigned).toEqual([]);
    await router.visit("/c/");
    await router.visit("/b/");
    expect(calls[2]!.headers.has("X-Next-Request")).toBe(false);
    expect(keeper.extract).toHaveBeenLastCalledWith("<p>again</p>", "/b/");
  });

  it("loads a foreign document hard when the history chunk is missing", async () => {
    const { router, host } = setup({ router: { ...SETTINGS, foreign: "extract" } });
    serve(html());
    await router.visit("/b/");
    expect(host.assigned).toEqual([`${location.origin}/b/`]);
  });

  it("fetches a page a shared cache may hold as a plain document", async () => {
    const keeper = stubKeeper();
    const { router, host } = setup();
    loaded(router)._install(() => ({ extract: keeper.extract }));
    serve(
      answer(envelope(undefined, "/b/", { shared: true })),
      answer(envelope("<main><h1>C</h1></main>", "/c/")),
      html(),
    );
    await router.visit("/b/");
    await router.visit("/c/");
    await router.visit("/b/");
    expect(calls[2]!.headers.has("X-Next-Navigate")).toBe(false);
    expect(keeper.extract).toHaveBeenCalledWith(PAGE_HTML, "/b/");
    expect(host.assigned).toEqual([]);
    await router.visit("/b/");
    expect(host.assigned).toEqual([`${location.origin}/b/`]);
  });
});

describe("mount hooks after a navigation", () => {
  const WIDGET = '<main><h1>B</h1><div data-widget id="w"></div></main>';
  const START =
    '<div data-next-zone="flash">old</div>' +
    "<!--nx:s L1 S1--><main><h1>Home</h1></main><!--/nx:s L1 S1-->";

  function watch(): { roots: Element[]; widgets: Element[]; removed: Element[] } {
    const seen = {
      roots: [] as Element[],
      widgets: [] as Element[],
      removed: [] as Element[],
    };
    surface.onMount("[data-widget]", (el) => seen.widgets.push(el));
    for (const [name, list] of [
      ["next:mounted", seen.roots],
      ["next:removed", seen.removed],
    ] as const)
      document.addEventListener(name, (event) => list.push(event.target as Element), {
        signal: listening.signal,
      });
    return seen;
  }

  it("mounts a component of a swapped slot once per navigation", async () => {
    const { router } = setup({ page: START });
    loaded(router);
    const seen = watch();
    serve(answer(envelope(WIDGET)), answer(envelope(WIDGET, "/c/")));
    await router.visit("/b/");
    const main = document.querySelector("main")!;
    const widget = document.getElementById("w")!;
    expect(seen.roots).toEqual([main]);
    expect(seen.widgets).toEqual([widget]);
    await router.visit("/c/");
    expect(seen.roots).toEqual([main, main]);
    expect(seen.widgets).toEqual([widget, widget]);
    expect(seen.removed).toEqual([]);
  });

  it("tells the unmount of a component the next page drops", async () => {
    const { router } = setup({ page: START });
    loaded(router);
    const seen = watch();
    serve(answer(envelope(WIDGET)), answer(envelope("<main><h1>C</h1></main>", "/c/")));
    await router.visit("/b/");
    const widget = document.getElementById("w")!;
    await router.visit("/c/");
    expect(seen.removed).toEqual([widget]);
    expect(seen.widgets).toEqual([widget]);
  });

  it("mounts a zone of the kept chrome once on its own root", async () => {
    const { router } = setup({ page: START });
    loaded(router);
    const seen = watch();
    const fresh = '<div data-next-zone="flash"><p data-widget id="t">new</p></div>';
    serve(
      answer({
        ...envelope(),
        ops: [
          ...envelope().ops,
          { op: "morph", target: { zone: "flash" }, html: fresh },
        ],
      }),
    );
    await router.visit("/b/");
    const zone = document.querySelector("[data-next-zone=flash]")!;
    expect(zone.textContent).toBe("new");
    expect(seen.roots).toEqual([document.querySelector("main"), zone]);
    expect(seen.widgets).toEqual([document.getElementById("t")]);
  });
});

describe("the entry key after a layer closes", () => {
  it("follows the entry a layer close wrote while no layer stays open", () => {
    const { router } = setup();
    const seams = seamsOf(router);
    const booted = (history.state as Entry).key;
    const close = (): boolean =>
      document.dispatchEvent(new CustomEvent("partial:layer-dismissed"));
    history.replaceState({ next: 1, kind: "nav", key: "k2" }, "", "/");
    const size = vi.spyOn(surface.layers, "size").mockReturnValueOnce(1);
    close();
    expect(seams.key()).toBe(booted);
    size.mockRestore();
    document.dispatchEvent(new CustomEvent("partial:layer-accepted"));
    expect(seams.key()).toBe("k2");
    history.replaceState({ app: 1 }, "", "/");
    close();
    expect(seams.key()).toBe("k2");
  });
});

describe("the parse hooks of a custom backend", () => {
  it("decodes the answer through the hook its content type names", async () => {
    const type = "application/x-custom";
    const { router } = setup({ router: { ...SETTINGS, type } });
    surface.parseHook(type, (_response, body) => JSON.parse(body.slice(5)) as unknown);
    serve(
      new Response(`)]}'\n${JSON.stringify(envelope())}`, {
        headers: { "content-type": type },
      }),
    );
    await router.visit("/b/");
    expect(heading()).toBe("B");
  });
});
