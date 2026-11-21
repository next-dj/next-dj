import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";
import { createPartial } from "./partial";
import type { PartialSurface } from "./partial";
import { createPrefetch } from "./prefetch";
import { CONTENT_TYPE } from "./protocol";
import type { HistoryHost, Keeper, Layout, Settings } from "./router";
import { chunkModules } from "./test-doubles";
import type { IntersectionAdapter } from "./triggers";

interface Asked {
  href: string;
  signal: AbortSignal;
  extra: Record<string, string> | undefined;
}

type Reply = Response | ((signal: AbortSignal) => Promise<Response>);

type Prefetch = Pick<Keeper, "take" | "prefetch" | "drop">;

const SHELL = { version: "v1", ops: [], layouts: [["L1", "D1", null]] };

let listening: AbortController;
let surface: PartialSurface;
let asked: Asked[];
let replies: Reply[];
let chain: Layout[];
let settings: Settings;
let cleared: Mock<() => void>;

beforeEach(() => {
  listening = new AbortController();
  asked = [];
  replies = [];
  chain = [["L1", "D1", "S1"]];
  settings = { prefetch: "intent", prefetch_delay: 0.02 };
  cleared = vi.fn<() => void>();
  surface = createPartial({
    ...chunkModules,
    dispatch: () => undefined,
    mergeContext: () => undefined,
  });
});

afterEach(() => {
  listening.abort();
  surface._reset();
  vi.restoreAllMocks();
  Reflect.deleteProperty(navigator, "connection");
  document.body.innerHTML = "";
});

function shell(
  body: unknown = SHELL,
  init: { status?: number; headers?: Record<string, string> } = {},
): Response {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: { "content-type": CONTENT_TYPE, ...init.headers },
  });
}

function host(): HistoryHost {
  return {
    partial: surface,
    settings: () => settings,
    context: () => ({}),
    region: () => undefined,
    chain: () => chain,
    key: () => "",
    ask: (href, signal, extra) => {
      asked.push({ href, signal, extra });
      const reply = replies.shift() ?? shell();
      return reply instanceof Response ? Promise.resolve(reply) : reply(signal);
    },
    owned: (url) => url.origin === location.origin && !url.pathname.startsWith("/x/"),
    flush: cleared,
  };
}

function make(observer?: IntersectionAdapter): Prefetch {
  return createPrefetch(host(), { signal: listening.signal }, observer);
}

function hrefs(): string[] {
  return asked.map((call) => new URL(call.href).pathname);
}

function fire(el: EventTarget, type: string, init: MouseEventInit = {}): void {
  el.dispatchEvent(new MouseEvent(type, { bubbles: true, ...init }));
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const at = (path: string): string => `${location.origin}${path}`;

describe("intent", () => {
  it("prefetches a hovered link once the delay passes, as a marked request", async () => {
    document.body.innerHTML = '<a id="l" href="/b/#part"><span id="s">B</span></a>';
    make();
    const link = document.getElementById("l")!;
    const inner = document.getElementById("s")!;
    fire(link, "pointerover");
    fire(link, "pointerout", { relatedTarget: inner });
    fire(inner, "pointerover");
    await wait(5);
    expect(asked).toEqual([]);
    await wait(40);
    expect(asked).toEqual([
      {
        href: at("/b/"),
        signal: expect.any(AbortSignal),
        extra: { "X-Next-Prefetch": "1" },
      },
    ]);
  });

  it("drops a hover or a focus that leaves before the delay", async () => {
    document.body.innerHTML =
      '<a id="l" href="/b/">B</a><p id="p">p</p><a id="x" href="/x/">x</a>';
    make();
    const link = document.getElementById("l")!;
    fire(document.body, "pointerout");
    fire(document.getElementById("x")!, "pointerover");
    fire(link, "pointerover");
    fire(link, "pointerout", { relatedTarget: document.getElementById("p") });
    link.dispatchEvent(new FocusEvent("focusin", { bubbles: true }));
    link.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    await wait(40);
    expect(asked).toEqual([]);
  });

  it("prefetches at once on a press or a touch, one request per URL", async () => {
    document.body.innerHTML = '<a id="l" href="/b/">B</a>';
    make();
    const link = document.getElementById("l")!;
    fire(link, "pointerdown");
    link.dispatchEvent(new Event("touchstart", { bubbles: true }));
    fire(document, "pointerdown");
    await wait(0);
    expect(hrefs()).toEqual(["/b/"]);
  });

  it.each([
    [
      "an opted-out ancestor",
      '<div data-next-prefetch="off"><a id="l" href="/b/">x</a></div>',
    ],
    ["a confirm", '<a id="l" href="/b/" data-next-confirm="Sure?">x</a>'],
    ["an action", '<a id="l" href="/b/" data-next-action="u">x</a>'],
    ["a download", '<a id="l" href="/b/" download>x</a>'],
    ["an external rel", '<a id="l" href="/b/" rel="external">x</a>'],
    ["a foreign target", '<a id="l" href="/b/" target="_blank">x</a>'],
    ["a link the router does not own", '<a id="l" href="/x/b/">x</a>'],
    ["another origin", '<a id="l" href="https://elsewhere.example/">x</a>'],
    ["an svg link", '<svg><a id="l" href="/b/"><text>x</text></a></svg>'],
    ["an anchor without href", '<a id="l">x</a>'],
  ])("leaves %s alone", async (_name, markup) => {
    document.body.innerHTML = markup;
    make();
    fire(document.getElementById("l")!, "pointerdown");
    await wait(0);
    expect(asked).toEqual([]);
  });

  it.each([
    ["a data saver", { saveData: true }, false, "intent"],
    ["a slow link", { effectiveType: "slow-2g" }, false, "intent"],
    ["a hidden page", {}, true, "intent"],
    ["prefetching off", {}, false, "off"],
  ])("holds back on %s", async (_name, connection, hidden, mode) => {
    Object.defineProperty(navigator, "connection", {
      value: connection,
      configurable: true,
    });
    vi.spyOn(document, "hidden", "get").mockReturnValue(hidden);
    settings = { prefetch: mode };
    document.body.innerHTML = '<a id="l" href="/b/">B</a>';
    make();
    fire(document.getElementById("l")!, "pointerdown");
    await wait(0);
    expect(asked).toEqual([]);
  });

  it("prefetches on a fast link with the default delay", async () => {
    Object.defineProperty(navigator, "connection", {
      value: { effectiveType: "4g" },
      configurable: true,
    });
    settings = {};
    document.body.innerHTML = '<a id="l" href="/b/">B</a>';
    make();
    fire(document.getElementById("l")!, "pointerover");
    await wait(100);
    expect(hrefs()).toEqual(["/b/"]);
  });

  it("listens no more once its signal aborts", async () => {
    document.body.innerHTML = '<a id="l" href="/b/">B</a>';
    make();
    listening.abort();
    fire(document.getElementById("l")!, "pointerdown");
    await wait(0);
    expect(asked).toEqual([]);
  });
});

describe("the queue", () => {
  function held(): (response?: Response) => void {
    let answer: (response: Response) => void = () => undefined;
    replies.push(
      (signal) =>
        new Promise<Response>((done, fail) => {
          answer = done;
          signal.addEventListener("abort", () => fail(signal.reason as Error));
        }),
    );
    return (response = shell()) => answer(response);
  }

  it("runs three prefetches at once and starts a waiting one as one ends", async () => {
    const answers = [held(), held(), held(), held()];
    const prefetch = make();
    const done = ["/a/", "/b/", "/c/", "/d/"].map((path) =>
      prefetch.prefetch(at(path)),
    );
    expect(hrefs()).toEqual(["/a/", "/b/", "/c/"]);
    answers[0]!();
    await done[0];
    expect(hrefs()).toEqual(["/a/", "/b/", "/c/", "/d/"]);
    for (const answer of answers.slice(1)) answer();
    await Promise.all(done);
    expect(prefetch.take(at("/d/"))).toBeDefined();
  });

  it("starts the latest intent first among those waiting", async () => {
    const answers = [held(), held(), held(), held(), held()];
    const prefetch = make();
    const done = ["/a/", "/b/", "/c/", "/d/", "/e/"].map((path) =>
      prefetch.prefetch(at(path)),
    );
    answers[0]!();
    await done[0];
    expect(hrefs()).toEqual(["/a/", "/b/", "/c/", "/e/"]);
    answers[1]!();
    await done[1];
    expect(hrefs()).toEqual(["/a/", "/b/", "/c/", "/e/", "/d/"]);
  });

  it("drops a waiting intent the pointer leaves before it starts", async () => {
    document.body.innerHTML = '<a id="l" href="/l/">L</a><p id="p">p</p>';
    const answers = [held(), held(), held()];
    const prefetch = make();
    const done = ["/a/", "/b/", "/c/"].map((path) => prefetch.prefetch(at(path)));
    const link = document.getElementById("l")!;
    fire(link, "pointerover");
    await wait(40);
    fire(link, "pointerout", { relatedTarget: document.getElementById("p") });
    for (const answer of answers) answer();
    await Promise.all(done);
    expect(hrefs()).toEqual(["/a/", "/b/", "/c/"]);
  });

  it("moves a waiting prefetch ahead once an intent asks for it", async () => {
    const answers = [held(), held(), held(), held()];
    const prefetch = make();
    const done = ["/a/", "/b/", "/c/"].map((path) => prefetch.prefetch(at(path)));
    void prefetch.prefetch(at("/d/"));
    void prefetch.prefetch(at("/e/"));
    void prefetch.prefetch(at("/d/"));
    answers[0]!();
    await done[0];
    expect(hrefs()).toEqual(["/a/", "/b/", "/c/", "/d/"]);
  });

  it("hands a visit nothing for a waiting URL and asks for it no more", async () => {
    const answers = [held(), held(), held()];
    const prefetch = make();
    const done = ["/a/", "/b/", "/c/"].map((path) => prefetch.prefetch(at(path)));
    const waiting = prefetch.prefetch(at("/d/"));
    expect(prefetch.take(at("/d/"))).toBeUndefined();
    await waiting;
    for (const answer of answers) answer();
    await Promise.all(done);
    expect(hrefs()).toEqual(["/a/", "/b/", "/c/"]);
  });

  it("asks nothing for a URL in flight or freshly cached", async () => {
    const prefetch = make();
    const first = prefetch.prefetch(at("/a/"));
    await prefetch.prefetch(at("/a/#top"));
    await first;
    await prefetch.prefetch(at("/a/"));
    expect(hrefs()).toEqual(["/a/"]);
  });

  it("hands a visit the answer of the prefetch still in flight", async () => {
    let answer: (response: Response) => void = () => undefined;
    replies = [() => new Promise((done) => (answer = done))];
    const prefetch = make();
    const flying = prefetch.prefetch(at("/a/"));
    const joined = prefetch.take(at("/a/#part"));
    expect(joined).toBeInstanceOf(Promise);
    expect(prefetch.take(at("/b/"))).toBeUndefined();
    const response = shell();
    answer(response);
    expect(await joined).toEqual({ raw: SHELL, response });
    await flying;
    expect(hrefs()).toEqual(["/a/"]);
  });

  it("aborts the prefetch in flight on a drop and keeps nothing it answers", async () => {
    let answer: (response: Response) => void = () => undefined;
    replies = [() => new Promise((done) => (answer = done))];
    const prefetch = make();
    const flying = prefetch.prefetch(at("/a/"));
    const joined = prefetch.take(at("/a/"));
    prefetch.drop();
    expect(asked[0]!.signal.aborted).toBe(true);
    answer(shell());
    await flying;
    expect(await joined).toBeUndefined();
    expect(prefetch.take(at("/a/"))).toBeUndefined();
  });

  it("settles a prefetch the network refused", async () => {
    replies = [() => Promise.reject(new TypeError("offline"))];
    const prefetch = make();
    await prefetch.prefetch(at("/a/"));
    await prefetch.prefetch(at("/a/"));
    expect(hrefs()).toEqual(["/a/", "/a/"]);
  });
});

describe("back-off and refusals", () => {
  it("pauses for Retry-After, then doubles its own pause, until a success resets it", async () => {
    let now = 1_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    replies = [
      new Response(null, { status: 429, headers: { "Retry-After": "5" } }),
      new Response(null, { status: 503 }),
      new Response(null, { status: 500 }),
      shell(),
      new Response(null, { status: 500 }),
    ];
    const prefetch = make();
    const step = async (path: string, advance = 0): Promise<void> => {
      now += advance;
      await prefetch.prefetch(at(path));
    };
    await step("/a/");
    await step("/b/", 4_999);
    await step("/b/", 2);
    await step("/c/", 29_999);
    await step("/c/", 2);
    await step("/d/", 59_999);
    await step("/d/", 2);
    await step("/e/");
    await step("/f/", 29_999);
    await step("/f/", 2);
    expect(hrefs()).toEqual(["/a/", "/b/", "/c/", "/d/", "/e/", "/f/"]);
  });

  it("asks no more for a page that refuses prefetches", async () => {
    replies = [new Response(null, { status: 204 })];
    const prefetch = make();
    await prefetch.prefetch(at("/a/"));
    await prefetch.prefetch(at("/a/"));
    await prefetch.prefetch(at("/b/"));
    expect(hrefs()).toEqual(["/a/", "/b/"]);
    expect(prefetch.take(at("/a/"))).toBeUndefined();
  });
});

describe("the cache", () => {
  it("hands a fresh answer to one visit only", async () => {
    const prefetch = make();
    const response = shell();
    replies = [response];
    await prefetch.prefetch(at("/a/"));
    expect(prefetch.take(at("/a/#part"))).toEqual({ raw: SHELL, response });
    expect(prefetch.take(at("/a/"))).toBeUndefined();
  });

  it("keeps no more answers than allowed and none past their lifetime", async () => {
    let now = 1_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    settings = { prefetch_entries: 2, prefetch_shell_ttl: 1 };
    const prefetch = make();
    for (const path of ["/a/", "/b/", "/c/"]) await prefetch.prefetch(at(path));
    expect(prefetch.take(at("/a/"))).toBeUndefined();
    expect(prefetch.take(at("/b/"))).toBeDefined();
    now += 1_000;
    expect(prefetch.take(at("/c/"))).toBeUndefined();
  });

  it("keeps a shell for the shell lifetime and a complete page for the prefetch one", async () => {
    let now = 1_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    settings = { prefetch_ttl: 1, prefetch_shell_ttl: 3 };
    replies = [shell(), shell({ ...SHELL, complete: true }), shell()];
    const prefetch = make();
    await prefetch.prefetch(at("/s/"));
    await prefetch.prefetch(at("/c/"));
    now += 1_000;
    expect(prefetch.take(at("/c/"))).toBeUndefined();
    expect(prefetch.take(at("/s/"))).toBeDefined();
    await prefetch.prefetch(at("/s/"));
    now += 3_000;
    expect(prefetch.take(at("/s/"))).toBeUndefined();
  });

  it("keeps a shell five minutes and a complete page thirty seconds by default", async () => {
    let now = 1_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    settings = {};
    replies = [shell(), shell({ ...SHELL, complete: true })];
    const prefetch = make();
    await prefetch.prefetch(at("/s/"));
    await prefetch.prefetch(at("/c/"));
    now += 30_000;
    expect(prefetch.take(at("/c/"))).toBeUndefined();
    now += 269_999;
    expect(prefetch.take(at("/s/"))).toBeDefined();
  });

  it("evicts the least recently stored answer when one is stored again", async () => {
    let now = 1_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    settings = { prefetch_entries: 2, prefetch_shell_ttl: 1 };
    const prefetch = make();
    await prefetch.prefetch(at("/a/"));
    now += 600;
    await prefetch.prefetch(at("/b/"));
    now += 400;
    await prefetch.prefetch(at("/a/"));
    await prefetch.prefetch(at("/c/"));
    expect(hrefs()).toEqual(["/a/", "/b/", "/a/", "/c/"]);
    expect(prefetch.take(at("/b/"))).toBeUndefined();
    expect(prefetch.take(at("/a/"))).toBeDefined();
  });

  it("forgets an answer prefetched under another layout chain", async () => {
    const prefetch = make();
    await prefetch.prefetch(at("/a/"));
    chain = [["L9", "D9", "S9"]];
    expect(prefetch.take(at("/a/"))).toBeUndefined();
  });

  it("drops an answer of an asset version the page no longer runs", async () => {
    const prefetch = make();
    await prefetch.prefetch(at("/a/"));
    await prefetch.prefetch(at("/b/"));
    const headers = vi.spyOn(surface, "_headers");
    headers.mockReturnValue(new Headers({ "X-Next-Version": "v2" }));
    expect(prefetch.take(at("/a/"))).toBeUndefined();
    headers.mockReturnValue(new Headers({ "X-Next-Version": "v1" }));
    expect(prefetch.take(at("/b/"))).toBeDefined();
  });

  it.each([
    ["a failed answer", () => shell(SHELL, { status: 404 })],
    [
      "a redirected answer",
      () => {
        const response = shell();
        Object.defineProperty(response, "redirected", { value: true });
        return response;
      },
    ],
    [
      "a document",
      () => new Response("<p></p>", { headers: { "content-type": "text/html" } }),
    ],
    ["an answer without layouts", () => shell({ version: "v1", ops: [] })],
    ["a body that is no record", () => shell([1])],
  ])("keeps no %s", async (_name, reply) => {
    replies = [reply()];
    const prefetch = make();
    await prefetch.prefetch(at("/a/"));
    expect(prefetch.take(at("/a/"))).toBeUndefined();
  });

  it("flushes the router on a clearing answer and drops its own on demand", async () => {
    replies = [shell(SHELL, { headers: { "X-Next-Router": "clear" } })];
    const prefetch = make();
    await prefetch.prefetch(at("/a/"));
    expect(cleared).toHaveBeenCalledTimes(1);
    await prefetch.prefetch(at("/b/"));
    prefetch.drop();
    expect(prefetch.take(at("/a/"))).toBeUndefined();
    expect(prefetch.take(at("/b/"))).toBeUndefined();
  });

  it("drops every answer on a page restored from the cache", async () => {
    const prefetch = make();
    await prefetch.prefetch(at("/a/"));
    await prefetch.prefetch(at("/b/"));
    window.dispatchEvent(Object.assign(new Event("pageshow"), { persisted: false }));
    expect(prefetch.take(at("/a/"))).toBeDefined();
    window.dispatchEvent(Object.assign(new Event("pageshow"), { persisted: true }));
    expect(prefetch.take(at("/b/"))).toBeUndefined();
  });

  it("asks nothing for a URL the router does not own", async () => {
    const prefetch = make();
    await prefetch.prefetch(at("/x/a/"));
    await prefetch.prefetch("https://elsewhere.example/a/");
    expect(asked).toEqual([]);
  });

  it("decodes an answer through the parse hook of a custom backend", async () => {
    const type = "application/x-custom";
    settings = { type };
    surface.parseHook(type, (_response, body) => JSON.parse(body.slice(1)) as unknown);
    replies = [
      new Response(`!${JSON.stringify(SHELL)}`, { headers: { "content-type": type } }),
    ];
    const prefetch = make();
    await prefetch.prefetch(at("/a/"));
    expect((await prefetch.take(at("/a/")))!.raw).toEqual(SHELL);
  });
});

describe("viewport", () => {
  function observer(): IntersectionAdapter & {
    watched: Element[];
    stopped: Element[];
    show(el: Element, visible?: boolean): void;
  } {
    const views = new Map<Element, (visible: boolean) => void>();
    const watched: Element[] = [];
    const stopped: Element[] = [];
    return {
      watched,
      stopped,
      observe(el, onView) {
        watched.push(el);
        views.set(el, onView);
        return () => stopped.push(el);
      },
      show(el, visible = true) {
        views.get(el)?.(visible);
      },
    };
  }

  const byId = (id: string): Element => document.getElementById(id)!;

  function held(): (response?: Response) => void {
    let answer: (response: Response) => void = () => undefined;
    replies.push(
      (signal) =>
        new Promise<Response>((done, fail) => {
          answer = done;
          signal.addEventListener("abort", () => fail(signal.reason as Error));
        }),
    );
    return (response = shell()) => answer(response);
  }

  it("watches every owned link under the viewport mode but a region that opts out", () => {
    settings = { prefetch: "viewport" };
    document.body.innerHTML =
      '<a id="a" href="/a/">a</a><a id="x" href="/x/a/">x</a>' +
      '<div data-next-prefetch="intent"><a id="i" href="/i/">i</a></div>' +
      '<div data-next-prefetch="off"><a id="o" href="/o/">o</a></div>' +
      '<a id="n">n</a>';
    const seen = observer();
    make(seen);
    expect(seen.watched).toEqual([byId("a")]);
  });

  it("watches a region that opts in under the intent mode and nothing else", () => {
    document.body.innerHTML =
      '<div data-next-prefetch="viewport"><a id="v" href="/v/">v</a></div>' +
      '<a id="w" href="/w/" data-next-prefetch="viewport">w</a><a id="o" href="/o/">o</a>';
    const seen = observer();
    make(seen);
    expect(seen.watched).toEqual([byId("v"), byId("w")]);
  });

  it("watches nothing while prefetching is off", () => {
    settings = { prefetch: "off" };
    document.body.innerHTML =
      '<div data-next-prefetch="viewport"><a id="v" href="/v/">v</a></div>';
    const seen = observer();
    make(seen);
    expect(seen.watched).toEqual([]);
  });

  it("prefetches a link in view once and stops watching it once answered", async () => {
    settings = { prefetch: "viewport" };
    document.body.innerHTML = '<a id="v" href="/v/">v</a><a id="t" href="/v/">t</a>';
    const seen = observer();
    const prefetch = make(seen);
    seen.show(byId("v"), false);
    seen.show(byId("v"));
    await wait(0);
    expect(hrefs()).toEqual(["/v/"]);
    expect(seen.stopped).toEqual([byId("v")]);
    seen.show(byId("t"));
    expect(seen.stopped).toEqual([byId("v"), byId("t")]);
    expect(prefetch.take(at("/v/"))).toBeDefined();
    expect(hrefs()).toEqual(["/v/"]);
  });

  it("runs two viewport prefetches at once and keeps the third slot for intent", async () => {
    settings = { prefetch: "viewport", prefetch_delay: 0.02 };
    document.body.innerHTML =
      '<a id="a" href="/a/">a</a><a id="b" href="/b/">b</a><a id="c" href="/c/">c</a>' +
      '<div data-next-prefetch="intent"><a id="i" href="/i/">i</a></div>';
    const answers = [held(), held(), held(), held()];
    const seen = observer();
    make(seen);
    for (const id of ["a", "b", "c"]) seen.show(byId(id));
    expect(hrefs()).toEqual(["/a/", "/b/"]);
    fire(byId("i"), "pointerdown");
    expect(hrefs()).toEqual(["/a/", "/b/", "/i/"]);
    answers[0]!();
    await wait(0);
    expect(hrefs()).toEqual(["/a/", "/b/", "/i/"]);
    answers[2]!();
    await wait(0);
    expect(hrefs()).toEqual(["/a/", "/b/", "/i/", "/c/"]);
  });

  it("drops a waiting link that leaves the view or the document", async () => {
    settings = { prefetch: "viewport" };
    document.body.innerHTML =
      '<a id="a" href="/a/">a</a><a id="b" href="/b/">b</a>' +
      '<a id="c" href="/c/">c</a><a id="d" href="/d/">d</a>';
    const answers = [held(), held()];
    const seen = observer();
    make(seen);
    const [a, b, c, d] = ["a", "b", "c", "d"].map(byId);
    for (const el of [a!, b!, c!, d!]) seen.show(el);
    seen.show(c!, false);
    d!.remove();
    answers[0]!();
    answers[1]!();
    await wait(0);
    expect(hrefs()).toEqual(["/a/", "/b/"]);
    expect(seen.stopped).toEqual([a, b, d]);
  });

  it("watches a link on after its prefetch is aborted", async () => {
    settings = { prefetch: "viewport" };
    document.body.innerHTML = '<a id="v" href="/v/">v</a>';
    held();
    const seen = observer();
    const prefetch = make(seen);
    seen.show(byId("v"));
    prefetch.drop();
    await wait(0);
    expect(seen.stopped).toEqual([]);
    seen.show(byId("v"), false);
    seen.show(byId("v"));
    await wait(0);
    expect(hrefs()).toEqual(["/v/", "/v/"]);
    expect(seen.stopped).toEqual([byId("v")]);
  });

  it("asks for no more links in view than the cache holds", async () => {
    settings = { prefetch: "viewport", prefetch_entries: 2 };
    document.body.innerHTML =
      '<a id="a" href="/a/">a</a><a id="b" href="/b/">b</a><a id="c" href="/c/">c</a>';
    const seen = observer();
    make(seen);
    for (const id of ["a", "b", "c"]) seen.show(byId(id));
    await wait(0);
    expect(hrefs()).toEqual(["/a/", "/b/"]);
  });

  it("makes room for links in view once the answers they wait behind expire", async () => {
    let now = 1_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    settings = { prefetch: "viewport", prefetch_entries: 1, prefetch_ttl: 1 };
    document.body.innerHTML = '<a id="a" href="/a/">a</a><a id="b" href="/b/">b</a>';
    const seen = observer();
    make(seen);
    seen.show(byId("a"));
    await wait(0);
    seen.show(byId("b"));
    await wait(0);
    expect(hrefs()).toEqual(["/a/"]);
    now += 1_000;
    seen.show(byId("b"), false);
    seen.show(byId("b"));
    await wait(0);
    expect(hrefs()).toEqual(["/a/", "/b/"]);
  });

  it("turns a hovered link in view back into a viewport prefetch once left", async () => {
    settings = { prefetch: "viewport", prefetch_delay: 0.01 };
    document.body.innerHTML =
      '<a id="a" href="/a/">a</a><a id="b" href="/b/">b</a>' +
      '<a id="c" href="/c/">c</a><p id="p">p</p>';
    const answers = [held(), held(), held(), held()];
    const seen = observer();
    const prefetch = make(seen);
    for (const id of ["a", "b"]) seen.show(byId(id));
    void prefetch.prefetch(at("/q/"));
    seen.show(byId("c"));
    fire(byId("c"), "pointerover");
    await wait(30);
    fire(byId("c"), "pointerout", { relatedTarget: byId("p") });
    answers[2]!();
    await wait(0);
    expect(hrefs()).toEqual(["/a/", "/b/", "/q/"]);
    answers[0]!();
    await wait(0);
    expect(hrefs()).toEqual(["/a/", "/b/", "/q/", "/c/"]);
  });

  it("keeps a link in view queued when an intent queued it first", async () => {
    settings = { prefetch: "viewport", prefetch_delay: 0.01 };
    document.body.innerHTML = '<a id="c" href="/c/">c</a><p id="p">p</p>';
    const answers = [held(), held(), held(), held()];
    const seen = observer();
    const prefetch = make(seen);
    for (const path of ["/q/", "/r/", "/s/"]) void prefetch.prefetch(at(path));
    fire(byId("c"), "pointerover");
    await wait(30);
    seen.show(byId("c"));
    fire(byId("c"), "pointerout", { relatedTarget: byId("p") });
    answers[0]!();
    answers[1]!();
    await wait(0);
    expect(hrefs()).toEqual(["/q/", "/r/", "/s/", "/c/"]);
  });

  it("lets the oldest shell past the prefetch lifetime give way to a link in view", async () => {
    let now = 1_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    settings = { prefetch: "viewport", prefetch_entries: 2, prefetch_ttl: 1 };
    document.body.innerHTML =
      '<a id="a" href="/a/">a</a><a id="b" href="/b/">b</a>' +
      '<a id="c" href="/c/">c</a><a id="d" href="/d/">d</a><a id="e" href="/e/">e</a>';
    const seen = observer();
    const prefetch = make(seen);
    for (const id of ["a", "b"]) {
      seen.show(byId(id));
      await wait(0);
    }
    seen.show(byId("c"));
    await wait(0);
    expect(hrefs()).toEqual(["/a/", "/b/"]);
    now += 1_000;
    fire(byId("a"), "pointerover");
    seen.show(byId("c"), false);
    seen.show(byId("c"));
    await wait(0);
    expect(hrefs()).toEqual(["/a/", "/b/", "/c/"]);
    seen.show(byId("d"));
    await wait(0);
    expect(hrefs()).toEqual(["/a/", "/b/", "/c/"]);
    expect(prefetch.take(at("/b/"))).toBeUndefined();
    expect(prefetch.take(at("/a/"))).toBeDefined();
  });

  it("holds back links in view on a hidden page", async () => {
    settings = { prefetch: "viewport" };
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    document.body.innerHTML = '<a id="v" href="/v/">v</a>';
    const seen = observer();
    make(seen);
    seen.show(byId("v"));
    await wait(0);
    expect(asked).toEqual([]);
  });

  it("drops the waiting links once the server asks for a pause", async () => {
    settings = { prefetch: "viewport" };
    document.body.innerHTML =
      '<a id="a" href="/a/">a</a><a id="b" href="/b/">b</a><a id="c" href="/c/">c</a>';
    const answers = [held(), held()];
    const seen = observer();
    make(seen);
    for (const id of ["a", "b", "c"]) seen.show(byId(id));
    answers[0]!(new Response(null, { status: 429, headers: { "Retry-After": "5" } }));
    answers[1]!();
    await wait(0);
    expect(hrefs()).toEqual(["/a/", "/b/"]);
  });

  it("watches the links a later apply inserts", () => {
    settings = { prefetch: "viewport" };
    document.body.innerHTML = '<a id="a" href="/a/">a</a><section id="s"></section>';
    const seen = observer();
    make(seen);
    byId("s").innerHTML = '<a id="b" href="/b/">b</a>';
    document.dispatchEvent(
      new CustomEvent("partial:applied", { detail: { nodes: [byId("s"), byId("a")] } }),
    );
    document.dispatchEvent(new CustomEvent("partial:applied", { detail: {} }));
    expect(seen.watched).toEqual([byId("a"), byId("b")]);
  });
});

describe("stylesheets of a prefetched page", () => {
  const sheets = (): string[] =>
    [...document.head.querySelectorAll("link")].map(
      (el) => `${el.rel} ${el.getAttribute("href")} ${el.nonce}`,
    );

  afterEach(() => {
    document.head.innerHTML = "";
  });

  it("preloads each stylesheet the page lists that this one lacks", async () => {
    document.head.innerHTML = '<link rel="stylesheet" href="/site.css">';
    replies = [
      shell({
        ...SHELL,
        assets: [
          { kind: "css", url: "/site.css" },
          { kind: "css", url: "/b.css" },
          { kind: "css", url: "/b.css" },
          { kind: "x", url: "/c.css", load: "link" },
          { kind: "css", url: "", inline: "p{}" },
          { kind: "css", url: "/d.css", inline: "p{}" },
          { kind: "js", url: "/b.js" },
          "junk",
        ],
      }),
    ];
    const prefetch = createPrefetch(
      { ...host(), nonce: "n1" },
      { signal: listening.signal },
    );
    await prefetch.prefetch(at("/b/"));
    expect(sheets()).toEqual([
      "stylesheet /site.css ",
      "preload /b.css n1",
      "preload /c.css n1",
    ]);
  });

  it("prefetches the stylesheets of a page asked for by the viewport alone", async () => {
    settings = { prefetch: "viewport" };
    document.body.innerHTML = '<a id="v" href="/v/">v</a>';
    replies = [shell({ ...SHELL, assets: [{ kind: "css", url: "/v.css" }] })];
    let show: (visible: boolean) => void = () => undefined;
    make({
      observe: (_el, onView) => {
        show = onView;
        return () => undefined;
      },
    });
    show(true);
    await wait(0);
    expect(sheets()).toEqual(["prefetch /v.css "]);
  });

  it("preloads nothing for a page without assets", async () => {
    const prefetch = make();
    await prefetch.prefetch(at("/b/"));
    expect(sheets()).toEqual([]);
  });
});
