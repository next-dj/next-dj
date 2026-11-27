import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";
import { createPartial } from "./partial";
import type { PartialSurface } from "./partial";
import { createPrefetch } from "./prefetch";
import { createDefer } from "./defer";
import { createHistory } from "./history";
import type { DialogAdapter } from "./layers";
import { ACCEPT, CONTENT_TYPE, MAX_LAYOUTS, STREAM_ACCEPT } from "./protocol";
import { createReader } from "./reader";
import { PROGRESS, createRouter } from "./router";
import type {
  Entry,
  HistoryHost,
  Prefetched,
  Prefetcher,
  Router,
  RouterHost,
  RouterKernel,
  Turn,
} from "./router";
import type { History } from "./history";
import type { Kernel } from "./kernel";
import type { StreamReader } from "./wire";
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
  keeper?: Partial<Keeper & History>;
  kernel?: Omit<Partial<RouterKernel>, "load"> & { load?: Load };
}

// The chunk loader of the kernel, untyped so a test answers any chunk name.
type Load = (name: string, src?: string) => Promise<unknown>;

type Reply = Response | ((signal: AbortSignal | null) => Promise<Response>);

type Detail = Record<string, unknown>;

const SETTINGS = {
  scope: "all",
  exclude: ["/static/"],
  snapshots: 10,
  progress_delay: 0.15,
  intercept: "opt_in",
  links: ["nav", "[role=navigation]", "[data-next-nav]"],
  homes: ["/"],
};

const PAGE =
  '<nav><a id="home" href="/">Home</a><a id="nav-b" href="/b/">B</a></nav>' +
  '<div data-next-zone="flash">old</div><!--note-->' +
  '<!--nx:s L1 S1--><main><h1>Home</h1><a id="b" href="/b/">B</a></main>' +
  "<!--/nx:s L1 S1-->";

let listening: AbortController;
let surface: PartialSurface;
let kernel: Kernel;
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
    if (signal?.aborted) return Promise.reject(signal.reason as Error);
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

function stubKeeper(hit?: Prefetched, restored?: number) {
  const turn = {
    hold: vi.fn<Turn["hold"]>(),
    before: vi.fn<Turn["before"]>(),
    after: vi.fn<Turn["after"]>(),
  };
  return {
    turn,
    leave: vi.fn<History["leave"]>(),
    pop: vi.fn<History["pop"]>(() => restored),
    place: vi.fn<History["place"]>(() => true),
    flush: vi.fn<History["flush"]>(),
    swap: vi.fn<History["swap"]>(() => turn),
    extract: vi.fn<History["extract"]>(() => envelope("<main><h1>X</h1></main>")),
    take: vi.fn<Prefetcher["take"]>(() => hit),
    prefetch: vi.fn<Prefetcher["prefetch"]>(() => Promise.resolve()),
    drop: vi.fn<Prefetcher["drop"]>(),
  };
}

type Keeper = ReturnType<typeof stubKeeper>;

// The layouts block the router reads its chain from, none for a `null` chain.
function chained(layouts: unknown): void {
  document.head.querySelector("script[data-next-layouts]")?.remove();
  if (layouts === null) return;
  const block = document.createElement("script");
  block.type = "application/json";
  block.setAttribute("data-next-layouts", "");
  block.textContent = JSON.stringify(layouts);
  document.head.append(block);
}

function setup(options: Options = {}) {
  chained(options.layouts === undefined ? [["L1", "D1", "S1"]] : options.layouts);
  document.body.innerHTML = options.page ?? PAGE;
  if (options.state !== undefined) history.replaceState(options.state, "", "/");
  const context: Detail = {
    $router: options.router ?? SETTINGS,
    ...options.context,
  };
  const runtime = createPartial({
    ...chunkModules,
    dispatch: () => undefined,
    mergeContext: (data) => Object.assign(context, data),
    context: () => context,
  });
  surface = runtime.partial;
  kernel = runtime.kernel;
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
  const { keeper } = options;
  let seams: HistoryHost | undefined;
  // A stub keeper stands in for the history it names and for the prefetch alike.
  const intent = keeper?.take && ((): Prefetcher => keeper as Prefetcher);
  const router = createRouter(
    {
      ...kernel,
      onReady: (listener) => listener(),
      module: (name) => (name === "intent" ? intent : undefined) as never,
      ...(options.kernel as Partial<RouterKernel> | undefined),
    },
    host,
    { signal: listening.signal },
    (given, signal) => {
      seams = given;
      return { ...createHistory(given, signal), ...keeper };
    },
  );
  return { router, host, context, events, ends, seams: seams! };
}

// The walks over comments, the slot marker search, apart from the walks jsdom runs.
function commentWalks(): () => number {
  const walks = vi.spyOn(document, "createTreeWalker");
  return () =>
    walks.mock.calls.filter(([, shown]) => shown === NodeFilter.SHOW_COMMENT).length;
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
      router: { ...SETTINGS, scope: "opt_in" },
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

describe("the pages scope", () => {
  const ROUTES = "/_next/routes/abc/";
  const PAGES = { ...SETTINGS, scope: undefined, routes: ROUTES };
  const TABLE = { base: "/", slash: true, routes: ["-b/feed/$", "+b/$", "+[^/]+/$"] };
  const LINKS =
    '<a id="page" href="/b/">B</a><a id="plain" href="/accounts/logout/">Out</a>' +
    '<a id="feed" href="/b/feed/">Feed</a><a id="bare" href="/c">C</a>' +
    '<div data-next-router><a id="in" href="/accounts/x/">X</a></div>';

  const table = (body: unknown = TABLE) =>
    new Response(JSON.stringify(body), {
      headers: { "content-type": "application/json" },
    });

  async function loaded(options: Options = {}) {
    serve(table());
    const made = setup({ router: PAGES, page: LINKS, ...options });
    await vi.waitFor(() => expect(made.seams.owned(new URL(at("/b/")))).toBe(true));
    return made;
  }

  const at = (path: string): string => `${location.origin}${path}`;

  it("fetches the route table once at boot", async () => {
    await loaded();
    expect(calls.map((call) => call.url)).toEqual([ROUTES]);
  });

  it("leaves every link to the browser until the table loads", () => {
    const [reply] = held();
    serve(reply);
    setup({ router: PAGES, page: LINKS });
    expect(click(document.getElementById("page")!)).toBe(false);
    expect(calls).toHaveLength(1);
  });

  it("visits a page of the table soft", async () => {
    await loaded();
    calls.length = 0;
    serve(answer(envelope()));
    const done = settled();
    expect(click(document.getElementById("page")!)).toBe(true);
    await done;
    expect(calls.map((call) => call.url)).toEqual([at("/b/")]);
  });

  it("leaves a route that is no page to the browser without a request", async () => {
    await loaded();
    const fetch = serve();
    expect(click(document.getElementById("plain")!)).toBe(false);
    expect(click(document.getElementById("feed")!)).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("owns a path its slash makes a page", async () => {
    const { seams } = await loaded();
    expect(seams.owned(new URL(at("/c")))).toBe(true);
    expect(seams.owned(new URL(at("/c/d")))).toBe(false);
  });

  it("owns any link inside a router region", async () => {
    const { seams } = await loaded();
    const inside = document.getElementById("in")!;
    expect(seams.owned(new URL(at("/accounts/x/")), inside)).toBe(true);
    expect(seams.owned(new URL(at("/accounts/x/")))).toBe(false);
  });

  it("loads a visit to a route that is no page hard", async () => {
    const { router, host } = await loaded();
    const fetch = serve();
    await router.visit("/accounts/logout/");
    expect(fetch).not.toHaveBeenCalled();
    expect(host.assigned).toEqual([at("/accounts/logout/")]);
  });

  it("prefetches no route that is no page", async () => {
    const { seams } = await loaded();
    vi.useFakeTimers();
    const fetch = serve();
    createPrefetch(seams, { signal: listening.signal });
    for (const id of ["plain", "feed"])
      document
        .getElementById(id)!
        .dispatchEvent(
          new PointerEvent("pointerdown", { bubbles: true, pointerType: "mouse" }),
        );
    await vi.runAllTimersAsync();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("owns nothing while the table does not load", async () => {
    serve(new Response("nope", { status: 500 }));
    const { seams } = setup({ router: PAGES, page: LINKS });
    await wait(0);
    expect(seams.owned(new URL(at("/b/")))).toBe(false);
  });

  it("owns nothing and asks for nothing without a table to read", () => {
    const fetch = serve();
    const { seams } = setup({ router: { ...PAGES, routes: null }, page: LINKS });
    expect(seams.owned(new URL(at("/b/")))).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("reads the table its own document names", async () => {
    serve(table({ base: "/", slash: false, routes: ["+accounts/logout/$"] }));
    const { seams } = setup({
      router: { ...PAGES, routes: "/_next/routes/def/" },
      page: LINKS,
    });
    await vi.waitFor(() =>
      expect(seams.owned(new URL(at("/accounts/logout/")))).toBe(true),
    );
    expect(seams.owned(new URL(at("/b/")))).toBe(false);
    expect(calls.map((call) => call.url)).toEqual(["/_next/routes/def/"]);
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
      createDefer(kernel, { signal: listening.signal }, reloaded);
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
    setup({ page });
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
  it("walks the document for the slot markers once per navigation", async () => {
    const { router } = setup();
    serve(answer(envelope()), answer(envelope("<main><h1>C</h1></main>", "/c/")));
    const walks = commentWalks();
    await router.visit("/b/");
    expect(heading()).toBe("B");
    expect(walks()).toBe(1);
    await router.visit("/c/");
    expect(heading()).toBe("C");
    expect(walks()).toBe(1);
  });

  it("walks again once the markers it found no longer bound the slot", async () => {
    const { router } = setup();
    serve(answer(envelope()));
    const walks = commentWalks();
    const [open] = [...document.body.childNodes].filter(
      (node) =>
        node.nodeType === Node.COMMENT_NODE && (node as Comment).data === "nx:s L1 S1",
    );
    await router.visit("/b/");
    expect(walks()).toBe(1);
    (open as Comment).replaceWith(document.createComment("nx:s L1 S1"));
    serve(answer(envelope("<main><h1>C</h1></main>", "/c/")));
    await router.visit("/c/");
    expect(walks()).toBe(2);
  });

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
      '<!--nx:s L1 S1--><main><h1 tabindex="-1">B</h1></main><!--/nx:s L1 S1-->',
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
    expect(scrolled.mock.calls).toEqual([[0, 0]]);
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
    const keeper = stubKeeper();
    const { router, host, ends } = setup({ keeper });
    const visit = { version: "", ops: [{ op: "visit", href }] };
    serve(answer(visit, { "X-Next-Router": "clear" }));
    await router.visit("/b/");
    expect(keeper.flush).toHaveBeenCalledTimes(1);
    expect(host.assigned).toEqual(landed === undefined ? [] : [landed]);
    expect(ends.at(-1)!.status).toBe("hard");
  });

  it("names the script a DEBUG server loads the page for in the console", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { router, host } = setup();
    const dropped = { script: "<script>", template: "pages/b/template.djx" };
    const visit = { version: "", ops: [{ op: "visit", href: "/b/" }], dropped };
    serve(answer(visit));
    await router.visit("/b/");
    expect(host.assigned).toEqual([`${location.origin}/b/`]);
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("could not run"),
      dropped,
    );
  });

  it("keeps every request of a visit on this origin", async () => {
    const { router } = setup();
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
    createReader({ ...kernel, onReady: () => undefined });
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
    createReader({ ...kernel, onReady: () => undefined });
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

  it("replaces the response id of each layout the answer does not keep", async () => {
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
      '<!--nx:s L3 S9--><p tabindex="-1">y</p><!--/nx:s L3 S9-->',
    );
  });

  it("declares no more layouts than the server reads", async () => {
    const layouts = Array.from({ length: MAX_LAYOUTS + 3 }, (_, index) => [
      `L${index}`,
      "D",
      "S",
    ]);
    const { router } = setup({ layouts });
    serve(answer(envelope()));
    await router.visit("/b/");
    expect(calls[0]!.headers.get("X-Next-Layouts")!.split(",")).toHaveLength(
      MAX_LAYOUTS,
    );
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

  it("asserts the version and the protocol of its payload from the first visit", async () => {
    const { router } = setup({ context: { $version: "v1" } });
    surface.ready();
    serve(answer(envelope()));
    await router.visit("/b/");
    expect(calls[0]!.headers.get("X-Next-Version")).toBe("v1");
    expect(calls[0]!.headers.get("X-Next-Protocol")).toBe("2");
  });

  it("lands the first frame of a streamed navigation through the reader", async () => {
    const { router, events } = setup();
    createReader({ ...kernel, onReady: () => undefined });
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
    createReader({ ...kernel, onReady: () => undefined });
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
    const load = vi.fn<Load>((name) => {
      if (name === "boundaries") createReader({ ...kernel, onReady: () => undefined });
      return Promise.resolve(undefined);
    });
    const { router } = setup({
      router: { ...SETTINGS, boundaries: "/r.js" },
      kernel: { load },
    });
    const frame = JSON.stringify({ e: envelope() });
    serve(
      new Response(`event: patch\ndata: ${frame}\n\n`, {
        headers: { "content-type": "text/event-stream" },
      }),
    );
    await router.visit("/b/");
    expect(calls[0]!.headers.get("Accept")).toBe(STREAM_ACCEPT);
    expect(load).toHaveBeenCalledWith("boundaries", "/r.js");
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
    ["opt_in", [{ op: "layer.open", href: "/b/", zone: "z" }]],
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
    const keeper = stubKeeper();
    const { router } = setup({ keeper });
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
    const keeper = stubKeeper();
    const { router } = setup({ state: { app: 1 }, keeper });
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

describe("the progress bar", () => {
  it("draws the bar once with the nonce of the runtime", () => {
    const boots: (() => void)[] = [];
    setup({
      router: { ...SETTINGS, progress: true },
      kernel: {
        nonce: "n0",
        onReady: (listener) => {
          boots.push(listener);
          listener();
        },
      },
    });
    boots[0]!();
    const sheets = document.head.querySelectorAll("style");
    expect(sheets).toHaveLength(1);
    expect(sheets[0]!.nonce).toBe("n0");
    expect(sheets[0]!.textContent).toBe(PROGRESS);
  });

  it("draws none when the setting is off", () => {
    setup({ router: { ...SETTINGS, progress: false } });
    expect(document.head.querySelector("style")).toBeNull();
  });

  it("draws one without a nonce on a page that has none", () => {
    setup({ router: { ...SETTINGS, progress: true } });
    expect(document.head.querySelector("style")!.hasAttribute("nonce")).toBe(false);
  });
});

describe("the history seam", () => {
  it("snapshots the slot it leaves unless the page refuses snapshots", async () => {
    const keeper = stubKeeper();
    const { router } = setup({ keeper });
    const key = (history.state as Entry).key;
    serve(
      answer(envelope(), { "X-Next-Router": "no_snapshot" }),
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
    const keeper = stubKeeper();
    const { router } = setup({ router: { ...SETTINGS, snapshot: false }, keeper });
    serve(answer(envelope()));
    await router.visit("/b/");
    expect(keeper.leave).toHaveBeenCalledWith(expect.any(String), undefined);
  });

  it("flushes and keeps no snapshot of the page an answer clears", async () => {
    const keeper = stubKeeper();
    const { router } = setup({ keeper });
    serve(answer(envelope(), { "X-Next-Router": "clear" }));
    await router.visit("/b/");
    expect(keeper.flush).toHaveBeenCalledTimes(1);
    expect(keeper.leave).toHaveBeenCalledWith(expect.any(String), undefined);
  });

  it("flushes on a request that may change state and on a clearing response", () => {
    const keeper = stubKeeper();
    const { router } = setup({ keeper });
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

  it("loads the intent chunk and the reader on idle through the runtime's loader", () => {
    vi.useFakeTimers();
    const load = vi.fn<Load>(() => Promise.resolve(undefined));
    setup({
      router: { ...SETTINGS, links: null, intent: "/i.js", boundaries: "/r.js" },
      kernel: { load },
    });
    expect(load).not.toHaveBeenCalled();
    vi.runOnlyPendingTimers();
    expect(load.mock.calls).toEqual([
      ["intent", "/i.js"],
      ["boundaries", "/r.js"],
    ]);
  });

  it("asks for an idle callback with a deadline where the browser has one", () => {
    const idle = vi.fn<(run: () => void, options: IdleRequestOptions) => number>(
      () => 1,
    );
    vi.stubGlobal("requestIdleCallback", idle);
    const load = vi.fn<Load>(() => Promise.resolve(undefined));
    setup({ router: { ...SETTINGS, links: null }, kernel: { load } });
    expect(idle).toHaveBeenCalledTimes(1);
    expect(idle.mock.calls[0]![1]).toEqual({ timeout: 2000 });
    expect(load).not.toHaveBeenCalled();
    idle.mock.calls[0]![0]();
    expect(load).toHaveBeenCalledTimes(2);
  });
});

describe("the reader on idle", () => {
  it("loads the reader once idle and reuses it for a streamed navigation", async () => {
    const idle = vi.fn<(run: () => void) => number>(() => 1);
    vi.stubGlobal("requestIdleCallback", idle);
    let land: () => void = () => undefined;
    const landing = new Promise<never>((done) => {
      land = () => {
        createReader({ ...kernel, onReady: () => undefined });
        done(true as never);
      };
    });
    const load = vi.fn<Load>((name) =>
      name === "boundaries" ? landing : Promise.resolve(undefined),
    );
    const { router } = setup({
      router: { ...SETTINGS, boundaries: "/r.js" },
      kernel: { load },
    });
    idle.mock.calls[0]![0]();
    const frame = JSON.stringify({ e: envelope() });
    serve(
      new Response(`event: patch\ndata: ${frame}\n\n`, {
        headers: { "content-type": "text/event-stream" },
      }),
    );
    const visit = router.visit("/b/");
    await Promise.resolve();
    land();
    await visit;
    expect(load.mock.calls.filter(([name]) => name === "boundaries")).toHaveLength(2);
    expect(heading()).toBe("B");
  });

  it("asks for no reader on idle once one has landed", () => {
    const idle = vi.fn<(run: () => void) => number>(() => 1);
    vi.stubGlobal("requestIdleCallback", idle);
    const load = vi.fn<Load>(() => Promise.resolve(undefined));
    setup({
      router: { ...SETTINGS, links: null, boundaries: "/r.js" },
      kernel: { load },
    });
    createReader({ ...kernel, onReady: () => undefined });
    idle.mock.calls[0]![0]();
    expect(load.mock.calls.map(([name]) => name)).toEqual(["intent"]);
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
    const { router } = setup({ context: { $router: undefined }, layouts: null });
    serve(answer(bodied("<main><h1>B</h1></main>", "/b/")));
    await router.visit("/b/");
    expect(calls[0]!.headers.get("X-Next-Layouts")).toBe("");
    expect(heading()).toBe("B");
  });

  it("revalidates a popped entry the history holds no snapshot of", async () => {
    const { router } = setup({ keeper: { pop: vi.fn(() => undefined) } });
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
    expect(scrolled).toHaveBeenCalledWith(0, 0);
  });

  it("hands a pop to the history chunk and places the scroll it did not restore", async () => {
    const keeper = stubKeeper();
    const { router } = setup({ keeper });
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
    const keeper = stubKeeper(undefined, Date.now() - 4_000);
    const { router } = setup({ router: { ...SETTINGS, prefetch_ttl: 5 }, keeper });
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
    const keeper = stubKeeper(undefined, Date.now() - 30_000);
    const { router } = setup({ keeper });
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
    setup({ page: `${PAGE}<p id="frag">f</p>` });
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
    const { seams } = setup({ page: `${PAGE}<p id="frag">f</p>` });
    pop({ next: 1, kind: "nav", key: "k5" }, "/#frag");
    expect(seams.key()).toBe("k5");
    expect(intoView).toHaveBeenCalledTimes(1);
  });

  it("stamps a fragment entry the browser made so a pop back to it stays soft", async () => {
    const { router, host } = setup({ keeper: { pop: vi.fn(() => undefined) } });
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
    serve(answer(envelope()));
    await router.visit("/b/");
    expect(scrolled.mock.calls).toEqual([[0, 0]]);
  });

  it("scrolls to the top and once more at the end when the fragment is missing", async () => {
    const { router } = setup();
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
    const { router, events } = setup({ keeper: { pop: vi.fn(() => undefined) } });
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
    const { router, events } = setup({ keeper: { pop: vi.fn(() => undefined) } });
    createReader({ ...kernel, onReady: () => undefined });
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
    const { router, events } = setup({ keeper: { pop: vi.fn(() => undefined) } });
    serve(answer(envelope()), answer(envelope("<main><h1>Home</h1></main>", "/home/")));
    const home = await left(router, events);
    const details = recorded(events);
    await back(home);
    expect(details).toEqual([{ path: "/home/", title: "Page /home/", action: "pop" }]);
  });

  it("leaves a pop the next visit supersedes to that visit", async () => {
    const { router, events } = setup({ keeper: { pop: vi.fn(() => undefined) } });
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
    await vi.waitFor(() => expect(calls).toHaveLength(1));
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
    createReader({ ...kernel, onReady: () => undefined });
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

  it("hands the history and the intent chunk their seams", () => {
    const { router, seams } = setup();
    router.flush();
    expect(seams.settings()).toBe(SETTINGS);
    expect(seams.key()).toBe((history.state as Entry).key);
    expect(seams.region({ body: true })).toEqual([document.body, null, null]);
    expect(seams.region({ slot: "L1" })![0]).toBe(document.body);
    expect(seams.region({ zone: "flash" })).toBeUndefined();
    expect(seams.nonce).toBeUndefined();
    expect(seams.chain()).toEqual([["L1", "D1", "S1"]]);
    seams.chain([["L2", "D2", "S2"]]);
    expect(seams.chain()).toEqual([["L2", "D2", "S2"]]);
    expect(seams.context()).toEqual(expect.objectContaining({ $router: SETTINGS }));
    expect(seams.going()).toBe("");
    expect(seams.plain("/b/")).toBe(false);
    seams.flush();
    expect(seams.owned(new URL("/b/", location.href))).toBe(true);
    expect(seams.owned(new URL("/static/a.css", location.href))).toBe(false);
  });

  it("asks with the navigation headers a prefetch adds to", async () => {
    const { seams } = setup();
    serve();
    const signal = new AbortController().signal;
    await seams.ask("/c/", signal, { "X-Next-Prefetch": "1" });
    expect(calls[0]!.headers.get("X-Next-Navigate")).toBe("1");
    expect(calls[0]!.headers.get("X-Next-Layouts")).toBe("L1.D1");
    expect(calls[0]!.headers.get("X-Next-Prefetch")).toBe("1");
    expect(calls[0]!.signal).toBe(signal);
  });

  it("prefetches through the intent chunk once it loads", async () => {
    const keeper = stubKeeper();
    let loaded = false;
    const { router } = setup({
      kernel: {
        load: (name) => {
          loaded ||= name === "intent";
          return Promise.resolve(undefined);
        },
        module: (name) =>
          (name === "intent" && loaded ? () => keeper : undefined) as never,
      },
    });
    await router.prefetch("/c/#top");
    expect(keeper.prefetch.mock.calls).toEqual([[`${location.origin}/c/#top`]]);
  });

  it("prefetches nothing while the intent chunk cannot load", async () => {
    const { router } = setup();
    await expect(router.prefetch("/c/")).resolves.toBeUndefined();
  });

  it("builds the prefetch of the intent chunk once", async () => {
    const factory = vi.fn(() => stubKeeper());
    const { router } = setup({ kernel: { module: () => factory as never } });
    await router.prefetch("/c/");
    await router.prefetch("/d/");
    expect(factory).toHaveBeenCalledTimes(1);
  });

  it("flushes the history and drops the prefetch alike", () => {
    const keeper = stubKeeper();
    const { router } = setup({ keeper });
    router.flush();
    expect(keeper.flush).toHaveBeenCalledTimes(1);
    expect(keeper.drop).toHaveBeenCalledTimes(1);
  });
});

describe("prefetched shells", () => {
  it("applies a prefetched shell at once and revalidates it behind", async () => {
    const shell = envelope('<main><h1>B</h1><p id="w">loading</p></main>');
    const keeper = stubKeeper({ raw: shell, response: answer({}) });
    const { router, events, seams } = setup({ keeper });
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
    expect((history.state as Entry).key).toBe(seams.key());
  });

  it("commits a prefetch that skipped no deferred job without a request", async () => {
    const page = envelope('<main><h1>B</h1><p id="w">ready</p></main>', "/b/", {
      complete: true,
    });
    const keeper = stubKeeper({ raw: page, response: answer({}) });
    const { router, events, ends } = setup({ keeper });
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
    const keeper = stubKeeper({ raw: envelope(loading), response: answer({}) });
    const { router, host, ends } = setup({ keeper });
    createReader({ ...kernel, onReady: () => undefined });
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
    const keeper = stubKeeper({ raw: shell, response: answer({}) });
    const { router, host, ends } = setup({ keeper });
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
    const { router } = setup({ keeper });
    serve(answer(envelope()), answer(envelope("<main><h1>Home</h1></main>", "/")));
    const home = history.state as Entry;
    await router.visit("/b/", { replace: true });
    await back(home);
    expect(keeper.swap.mock.calls.map((call) => call[3])).toEqual(["replace", "back"]);
  });

  it("hands a layer answer no swap and snapshots nothing", async () => {
    const keeper = stubKeeper();
    const { router } = setup({ router: { ...SETTINGS, intercept: "links" }, keeper });
    vi.spyOn(surface.layers, "open").mockResolvedValue();
    serve(answer({ version: "", ops: [{ op: "layer.open", href: "/b/", zone: "z" }] }));
    await router.visit("/b/");
    expect(keeper.swap).not.toHaveBeenCalled();
    expect(keeper.leave).toHaveBeenCalledWith(expect.any(String), undefined);
  });

  it("lands a navigation without waiting for the intent chunk", async () => {
    const load = vi.fn<Load>(() => new Promise(() => undefined));
    const { router } = setup({
      router: { ...SETTINGS, intent: "/i.js" },
      kernel: { load },
    });
    void router.prefetch("/c/");
    serve(answer(envelope()));
    await router.visit("/b/");
    expect(heading()).toBe("B");
    expect(load).toHaveBeenCalledWith("intent", "/i.js");
  });

  it("visits hard once a navigation outlives the request deadline", async () => {
    const { router, host, ends } = setup({ context: { $timeout: 20 } });
    let aborted: AbortSignal | null = null;
    serve((signal) => {
      aborted = signal;
      return new Promise<Response>((_resolve, reject) =>
        signal!.addEventListener("abort", () => reject(signal!.reason as Error)),
      );
    });
    await router.visit("/b/");
    expect(host.assigned).toEqual([`${location.origin}/b/`]);
    expect(aborted!.aborted).toBe(true);
    expect(document.documentElement.hasAttribute("data-next-navigating")).toBe(false);
    expect(ends.at(-1)).toMatchObject({ status: "hard" });
  });

  it("leaves a navigation without a deadline when the payload turns it off", async () => {
    vi.useFakeTimers();
    const { router, host } = setup({ context: { $timeout: 0 } });
    serve(() => new Promise<Response>(() => undefined));
    void router.visit("/b/");
    await vi.advanceTimersByTimeAsync(60_000);
    expect(host.assigned).toEqual([]);
  });

  function flying(keeper: ReturnType<typeof stubKeeper>): (hit: Prefetched) => void {
    let arrive: (hit: Prefetched) => void = () => undefined;
    keeper.take.mockReturnValue(
      new Promise<Prefetched | undefined>((done) => (arrive = done)),
    );
    return (hit) => arrive(hit);
  }

  it("loads the page hard once when the prefetched shell throws as it lands", async () => {
    const keeper = stubKeeper({ raw: envelope(), response: answer({}) });
    keeper.swap.mockImplementation(() => {
      throw new Error("broken keeper");
    });
    const { router, host, ends, events } = setup({ keeper });
    const [reply] = held();
    serve(reply);
    await expect(router.visit("/b/")).resolves.toBeUndefined();
    expect(calls[0]!.signal!.aborted).toBe(true);
    expect(host.assigned).toEqual([`${location.origin}/b/`]);
    expect(events.filter((name) => name === "error")).toHaveLength(1);
    expect(ends.at(-1)!.status).toBe("hard");
  });

  it("asks for the page at once while it lands a prefetched shell", async () => {
    const shell = envelope('<main><h1>B</h1><p id="w">loading</p></main>');
    const keeper = stubKeeper({ raw: shell, response: answer({}) });
    const { router } = setup({ keeper });
    const [reply] = held();
    serve(reply);
    void router.visit("/b/");
    await Promise.resolve();
    expect(calls).toHaveLength(1);
    expect(calls[0]!.headers.has("X-Next-Prefetch")).toBe(false);
  });

  it("waits for a prefetch in flight and asks for the page once its shell lands", async () => {
    const shell = envelope('<main><h1>B</h1><p id="w">loading</p></main>');
    const keeper = stubKeeper();
    const arrive = flying(keeper);
    const { router } = setup({ keeper });
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
    const { router } = setup({ keeper });
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
    const { router, ends, host } = setup({ keeper });
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
    const { router } = setup({ keeper });
    serve(answer(envelope()));
    await router.visit("/b/");
    expect(heading()).toBe("B");
  });
});

describe("pressed flights", () => {
  function pressed(extra: Prefetched = {}) {
    let arrive: (response?: Response) => void = () => undefined;
    const flight = new Promise<Response | undefined>((done) => (arrive = done));
    const stop = vi.fn(() => arrive(undefined));
    return {
      hit: { ...extra, flight: { answer: flight, stop } },
      stop,
      arrive: (r?: Response) => arrive(r),
    };
  }

  it("adopts a streamed flight and hands its response to the reader", async () => {
    const press = pressed();
    const keeper = stubKeeper(press.hit);
    const { router } = setup({ keeper });
    let spy: Mock<StreamReader> | undefined;
    createReader({
      ...kernel,
      onReady: () => undefined,
      serve: (name, handler) =>
        kernel.serve(name, (spy = vi.fn(handler as StreamReader)) as never),
    });
    const reader = spy!;
    const fetch = serve();
    const frame = JSON.stringify({ e: envelope() });
    const response = new Response(`event: patch\ndata: ${frame}\n\n`, {
      headers: { "content-type": "text/event-stream" },
    });
    const visit = router.visit("/b/");
    press.arrive(response);
    await visit;
    expect(fetch).not.toHaveBeenCalled();
    expect(reader.mock.calls[0]![0]).toBe(response);
    expect(heading()).toBe("B");
    expect(press.stop).not.toHaveBeenCalled();
  });

  it("adopts a flight that answers an envelope and lands it", async () => {
    const press = pressed();
    const keeper = stubKeeper(press.hit);
    const { router, ends } = setup({ keeper });
    const fetch = serve();
    const visit = router.visit("/b/");
    press.arrive(answer(envelope()));
    await visit;
    expect(fetch).not.toHaveBeenCalled();
    expect(heading()).toBe("B");
    expect(location.pathname).toBe("/b/");
    expect(ends.at(-1)!.status).toBe("complete");
  });

  it("asks for the page itself once the flight brings nothing", async () => {
    const press = pressed();
    const keeper = stubKeeper(press.hit);
    const { router } = setup({ keeper });
    serve(answer(envelope()));
    const visit = router.visit("/b/");
    await wait(10);
    expect(calls).toHaveLength(0);
    press.arrive(undefined);
    await visit;
    expect(calls).toHaveLength(1);
    expect(calls[0]!.headers.get("X-Next-Navigate")).toBe("1");
    expect(calls[0]!.headers.has("X-Next-Prefetch")).toBe(false);
    expect(heading()).toBe("B");
  });

  it("lands a kept shell first and fills it from the flight", async () => {
    const shell = envelope('<main><h1>B</h1><p id="w">loading</p></main>');
    const press = pressed({ raw: shell, response: answer({}) });
    const keeper = stubKeeper(press.hit);
    const { router } = setup({ keeper });
    const fetch = serve();
    const shown = commits();
    const visit = router.visit("/b/");
    await wait(10);
    expect(document.getElementById("w")!.textContent).toBe("loading");
    press.arrive(answer(envelope('<main><h1>B</h1><p id="w">ready</p></main>')));
    await visit;
    expect(fetch).not.toHaveBeenCalled();
    expect(shown).toEqual(["loading"]);
    expect(document.getElementById("w")!.textContent).toBe("ready");
  });

  it("lands a kept complete answer and stops the flight unread", async () => {
    const page = envelope('<main><h1>B</h1><p id="w">whole</p></main>', "/b/", {
      complete: true,
    });
    const press = pressed({ raw: page, response: answer({}) });
    const keeper = stubKeeper(press.hit);
    const { router } = setup({ keeper });
    const fetch = serve();
    const late = answer(envelope('<main><h1>B</h1><p id="w">late</p></main>'));
    const visit = router.visit("/b/");
    press.arrive(late);
    await visit;
    expect(press.stop).toHaveBeenCalledTimes(1);
    expect(late.bodyUsed).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
    expect(document.getElementById("w")!.textContent).toBe("whole");
  });

  it.each([
    ["returned at once", (hit: Prefetched) => hit],
    ["still in flight", (hit: Prefetched) => Promise.resolve(hit)],
  ])(
    "stops a flight a router:before listener vetoes when it is %s",
    async (_n, wrap) => {
      const press = pressed();
      const keeper = stubKeeper();
      keeper.take.mockReturnValue(wrap(press.hit));
      const { router } = setup({ keeper });
      const fetch = serve();
      document.addEventListener("router:before", (event) => event.preventDefault(), {
        signal: listening.signal,
      });
      await router.visit("/b/");
      await wait(0);
      expect(press.stop).toHaveBeenCalledTimes(1);
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  it("stops the adopted flight once a newer visit aborts the one it serves", async () => {
    const press = pressed();
    const keeper = stubKeeper();
    keeper.take.mockReturnValueOnce(press.hit);
    const { router, ends } = setup({ keeper });
    const page = envelope("<main><h1>C</h1></main>", "/c/");
    serve(answer(page), answer(page));
    const first = router.visit("/b/");
    const second = router.visit("/c/");
    await Promise.all([first, second]);
    expect(press.stop).toHaveBeenCalledTimes(1);
    expect(heading()).toBe("C");
    expect(
      ends
        .slice(1)
        .map((detail) => detail.status)
        .sort(),
    ).toEqual(["complete", "superseded"]);
  });

  it("stops a flight still on its way once a newer visit overtakes the one it serves", async () => {
    const press = pressed();
    const keeper = stubKeeper();
    let arrive: (hit: Prefetched) => void = () => undefined;
    keeper.take.mockReturnValueOnce(new Promise((done) => (arrive = done)));
    const { router, ends } = setup({ keeper });
    serve(answer(envelope("<main><h1>C</h1></main>", "/c/")));
    const first = router.visit("/b/");
    const second = router.visit("/c/");
    await second;
    arrive(press.hit);
    await first;
    expect(press.stop).toHaveBeenCalledTimes(1);
    expect(heading()).toBe("C");
    expect(ends.at(-1)!.status).toBe("superseded");
  });

  it("stops the flight of a path it fetches as a plain document", async () => {
    const press = pressed();
    const keeper = stubKeeper(press.hit);
    const { router } = setup({
      keeper: { ...keeper, plain: (path: string) => path === "/b/" },
    });
    serve(new Response("<p>doc</p>", { headers: { "content-type": "text/html" } }));
    await router.visit("/b/");
    expect(press.stop).toHaveBeenCalledTimes(1);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.headers.has("X-Next-Navigate")).toBe(false);
    expect(keeper.extract).toHaveBeenCalledWith("<p>doc</p>", "/b/");
    expect(heading()).toBe("X");
  });

  it("names the bare URL it loads while a visit is in flight", async () => {
    const { router, seams } = setup();
    const [reply, release] = held();
    serve(reply);
    expect(seams.going()).toBe("");
    const visit = router.visit("/b/#part");
    expect(seams.going()).toBe(`${location.origin}/b/`);
    await vi.waitFor(() => expect(calls).toHaveLength(1));
    release(answer(envelope()));
    await visit;
    expect(seams.going()).toBe("");
  });

  it("names the newer visit while the one it superseded settles", async () => {
    const { router, seams } = setup();
    const [stalled] = held();
    const [reply, release] = held();
    serve(stalled, reply);
    const first = router.visit("/b/");
    const second = router.visit("/c/");
    await first;
    expect(seams.going()).toBe(`${location.origin}/c/`);
    await vi.waitFor(() => expect(calls).toHaveLength(2));
    release(answer(envelope("<main><h1>C</h1></main>", "/c/")));
    await second;
    expect(seams.going()).toBe("");
  });

  it("tells the intent chunk which paths the history fetches plain", () => {
    const { seams } = setup({ keeper: { plain: (path: string) => path === "/b/" } });
    expect(seams.plain("/b/")).toBe(true);
    expect(seams.plain("/c/")).toBe(false);
  });
});

describe("a press inside the router", () => {
  // The router with the prefetch of the intent chunk built at boot.
  function pressing(): { router: Router } {
    const module = (name: string) =>
      name === "intent"
        ? (seams: HistoryHost) => createPrefetch(seams, { signal: listening.signal })
        : undefined;
    return setup({ kernel: { module: module as RouterKernel["module"] } });
  }

  function press(el: Element): void {
    el.dispatchEvent(
      new PointerEvent("pointerdown", { bubbles: true, pointerType: "mouse" }),
    );
  }

  it("answers the click with the request its press sent", async () => {
    pressing();
    serve(answer(envelope()));
    const link = document.getElementById("b")!;
    press(link);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.headers.get("X-Next-Prefetch")).toBe("full");
    const done = settled();
    expect(click(link)).toBe(true);
    expect((await done).status).toBe("complete");
    expect(calls).toHaveLength(1);
    expect(heading()).toBe("B");
  });

  it("keeps a press to its own end when Back restores a snapshot meanwhile", async () => {
    const { router } = pressing();
    const whole = envelope("<main><h1>C</h1></main>", "/c/", { complete: true });
    serve(
      answer(envelope('<main><h1>B</h1><a id="c" href="/c/">C</a></main>')),
      answer(whole),
    );
    const home = history.state as Entry;
    await router.visit("/b/");
    vi.useFakeTimers();
    press(document.getElementById("c")!);
    pop(home);
    await vi.advanceTimersByTimeAsync(0);
    expect(heading()).toBe("Home");
    expect(calls).toHaveLength(2);
    expect(calls[1]!.signal!.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1000);
    vi.useRealTimers();
    expect(calls[1]!.signal!.aborted).toBe(false);
    await router.visit("/c/");
    expect(calls).toHaveLength(2);
    expect(heading()).toBe("C");
  });
});

describe("foreign answers", () => {
  const PAGE_HTML = "<html><body><main><h1>F</h1></main></body></html>";

  function html(body = PAGE_HTML): Response {
    return new Response(body, { headers: { "content-type": "text/html" } });
  }

  it("remembers a URL that answered a document and loads it hard from then on", async () => {
    const { router, host } = setup();
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
    sessionStorage.setItem("nx:foreign", "{");
    serve(answer(envelope()));
    await router.visit("/b/");
    expect(heading()).toBe("B");
  });

  it("reads the memo once, however many links it checks", () => {
    sessionStorage.setItem("nx:foreign", '["/b/"]');
    setup();
    const read = vi.spyOn(Storage.prototype, "getItem");
    const reads = read.mock.calls.length;
    for (let index = 0; index < 5; index += 1)
      expect(click(document.getElementById("b")!)).toBe(false);
    expect(read).toHaveBeenCalledTimes(reads);
  });

  it("keeps the fifty URLs remembered last", async () => {
    const paths = Array.from({ length: 51 }, (_, index) => `/f${index}/`);
    sessionStorage.setItem("nx:foreign", JSON.stringify(paths.slice(0, 50)));
    const { router } = setup();
    serve(html(), html());
    await router.visit("/f0/");
    expect(calls).toHaveLength(0);
    await router.visit("/f50/");
    const kept = JSON.parse(sessionStorage.getItem("nx:foreign")!) as string[];
    expect(kept).toHaveLength(50);
    expect(kept[0]).toBe("/f1/");
    expect(kept.at(-1)).toBe("/f50/");
  });

  it("remembers a URL for the page when the session store refuses to keep it", async () => {
    const { router, host } = setup();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    serve(html());
    await router.visit("/b/");
    await router.visit("/b/");
    expect(calls).toHaveLength(1);
    expect(host.assigned).toHaveLength(2);
  });

  it("remembers no stream it could not read", async () => {
    const { router } = setup();
    serve(new Response("", { headers: { "content-type": "text/event-stream" } }));
    await router.visit("/b/");
    expect(sessionStorage.getItem("nx:foreign")).toBeNull();
  });

  it("carves a foreign document into the page and fetches it plain from then on", async () => {
    const keeper = stubKeeper();
    const { router, host } = setup({
      router: { ...SETTINGS, foreign: "extract" },
      keeper: { extract: keeper.extract },
    });
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

  it("fetches a page a shared cache may hold as a plain document", async () => {
    const keeper = stubKeeper();
    const { router, host } = setup({ keeper: { extract: keeper.extract } });
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
    const { seams } = setup();
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
