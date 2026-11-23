import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";
import { createPartial } from "./partial";
import type { PartialSurface } from "./partial";
import { createPrefetch } from "./prefetch";
import { CONTENT_TYPE } from "./protocol";
import type { Layout } from "./layout";
import type { HistoryHost, Keeper, Prefetched, Settings } from "./router";
import { chunkModules, historyHost } from "./test-doubles";
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
let plains: string[];
let going: string;

beforeEach(() => {
  listening = new AbortController();
  asked = [];
  replies = [];
  plains = [];
  going = "";
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
  vi.useRealTimers();
  Reflect.deleteProperty(navigator, "connection");
  Reflect.deleteProperty(document, "elementFromPoint");
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
  return historyHost({
    partial: surface,
    settings: () => settings,
    chain: () => chain,
    ask: (href, signal, extra) => {
      asked.push({ href, signal, extra });
      const reply = replies.shift() ?? shell();
      return reply instanceof Response ? Promise.resolve(reply) : reply(signal);
    },
    owned: (url) => url.origin === location.origin && !url.pathname.startsWith("/x/"),
    plain: (path) => plains.includes(path),
    going: () => going,
    flush: cleared,
  });
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
    vi.useFakeTimers({ toFake: ["Date"], now: 1_000_000 });
    replies = [
      new Response(null, { status: 429, headers: { "Retry-After": "5" } }),
      new Response(null, { status: 503 }),
      new Response(null, { status: 500 }),
      shell(),
      new Response(null, { status: 500 }),
    ];
    const prefetch = make();
    const step = async (path: string, advance = 0): Promise<void> => {
      vi.advanceTimersByTime(advance);
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
    vi.useFakeTimers({ toFake: ["Date"], now: 1_000_000 });
    settings = { prefetch_entries: 2, prefetch_shell_ttl: 1 };
    const prefetch = make();
    for (const path of ["/a/", "/b/", "/c/"]) await prefetch.prefetch(at(path));
    expect(prefetch.take(at("/a/"))).toBeUndefined();
    expect(prefetch.take(at("/b/"))).toBeDefined();
    vi.advanceTimersByTime(1_000);
    expect(prefetch.take(at("/c/"))).toBeUndefined();
  });

  it.each([
    ["the configured ones", { prefetch_ttl: 1, prefetch_shell_ttl: 3 }, 1_000, 3_000],
    ["thirty seconds and five minutes by default", {}, 30_000, 300_000],
  ])(
    "keeps a complete page and a shell for their lifetimes, %s",
    async (_name, config, page, life) => {
      vi.useFakeTimers({ toFake: ["Date"], now: 1_000_000 });
      settings = config;
      const whole = { ...SHELL, complete: true };
      replies = [shell(), shell(), shell(whole), shell(whole)];
      const prefetch = make();
      for (const path of ["/s/", "/t/", "/c/", "/d/"])
        await prefetch.prefetch(at(path));
      vi.advanceTimersByTime(page - 1);
      expect(prefetch.take(at("/c/"))).toBeDefined();
      vi.advanceTimersByTime(1);
      expect(prefetch.take(at("/d/"))).toBeUndefined();
      vi.advanceTimersByTime(life - page - 1);
      expect(prefetch.take(at("/s/"))).toBeDefined();
      vi.advanceTimersByTime(1);
      expect(prefetch.take(at("/t/"))).toBeUndefined();
    },
  );

  it("evicts the least recently stored answer when one is stored again", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: 1_000_000 });
    settings = { prefetch_entries: 2, prefetch_shell_ttl: 1 };
    const prefetch = make();
    await prefetch.prefetch(at("/a/"));
    vi.advanceTimersByTime(600);
    await prefetch.prefetch(at("/b/"));
    vi.advanceTimersByTime(400);
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

  it("asks the router about no link a region keeps out of the viewport mode", () => {
    settings = { prefetch: "viewport" };
    document.body.innerHTML =
      '<a id="a" href="/a/">a</a>' +
      '<div data-next-prefetch="intent"><a href="/i/">i</a><a href="/j/">j</a></div>';
    const seams = host();
    const owned = vi.spyOn(seams, "owned");
    createPrefetch(seams, { signal: listening.signal }, observer());
    expect(owned.mock.calls.map(([url]) => url.pathname)).toEqual(["/a/"]);
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
    vi.useFakeTimers({ toFake: ["Date"], now: 1_000_000 });
    settings = { prefetch: "viewport", prefetch_entries: 1, prefetch_ttl: 1 };
    document.body.innerHTML = '<a id="a" href="/a/">a</a><a id="b" href="/b/">b</a>';
    const seen = observer();
    make(seen);
    seen.show(byId("a"));
    await wait(0);
    seen.show(byId("b"));
    await wait(0);
    expect(hrefs()).toEqual(["/a/"]);
    vi.advanceTimersByTime(1_000);
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
    vi.useFakeTimers({ toFake: ["Date"], now: 1_000_000 });
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
    vi.advanceTimersByTime(1_000);
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

describe("the press", () => {
  const FULL = { "X-Next-Prefetch": "full" };
  const SHALLOW = { "X-Next-Prefetch": "1" };
  const PLAIN = '<a id="l" href="/b/">B</a>';

  afterEach(() => {
    document.head.innerHTML = "";
  });

  function press(el: Element, init: PointerEventInit = {}): void {
    el.dispatchEvent(
      new PointerEvent("pointerdown", { bubbles: true, pointerType: "mouse", ...init }),
    );
  }

  function link(markup = '<a id="l" href="/b/"><span id="s">B</span></a>'): Element {
    document.body.innerHTML = `${markup}<p id="p">p</p>`;
    return document.getElementById("l")!;
  }

  function stalled(): void {
    replies.push(
      (signal) =>
        new Promise<Response>((_done, fail) =>
          signal.addEventListener("abort", () => fail(signal.reason as Error)),
        ),
    );
  }

  function point(id: string): Mock<(x: number, y: number) => Element | null> {
    const found = vi.fn<(x: number, y: number) => Element | null>(() =>
      document.getElementById(id),
    );
    Object.defineProperty(document, "elementFromPoint", {
      value: found,
      configurable: true,
    });
    return found;
  }

  const extras = (): unknown[] => asked.map((call) => call.extra);
  const aborted = (): boolean[] => asked.map((call) => call.signal.aborted);
  const taken = (prefetch: Prefetch, path: string): Prefetched =>
    prefetch.take(at(path)) as Prefetched;

  it("sends one full request at a mouse press and no shell prefetch for the link", async () => {
    const el = link();
    make();
    fire(el, "pointerover");
    press(el);
    press(el);
    el.dispatchEvent(new Event("touchstart", { bubbles: true }));
    el.dispatchEvent(new FocusEvent("focusin", { bubbles: true }));
    await wait(40);
    expect(asked).toEqual([
      { href: at("/b/"), signal: expect.any(AbortSignal), extra: FULL },
    ]);
  });

  it.each([
    ["the default mode", {}, PLAIN, "mouse", FULL],
    ["the default mode", {}, PLAIN, "touch", SHALLOW],
    ["the default mode", {}, PLAIN, "pen", SHALLOW],
    ["the mouse mode", { press: "mouse" }, PLAIN, "mouse", FULL],
    ["the mouse mode", { press: "mouse" }, PLAIN, "touch", SHALLOW],
    ["the all mode", { press: "all" }, PLAIN, "touch", FULL],
    ["the all mode", { press: "all" }, PLAIN, "pen", FULL],
    ["the off mode", { press: "off" }, PLAIN, "mouse", SHALLOW],
    [
      "a link that turns it off",
      { press: "all" },
      '<a id="l" href="/b/" data-next-press="off">B</a>',
      "mouse",
      SHALLOW,
    ],
    [
      "a link that turns it on",
      { press: "off" },
      '<a id="l" href="/b/" data-next-press="mouse">B</a>',
      "mouse",
      FULL,
    ],
    [
      "an ancestor that turns it on for every pointer",
      { press: "off" },
      '<nav data-next-press="all"><a id="l" href="/b/">B</a></nav>',
      "touch",
      FULL,
    ],
    [
      "an ancestor that turns it off",
      {},
      '<nav data-next-press="off"><a id="l" href="/b/">B</a></nav>',
      "mouse",
      SHALLOW,
    ],
    [
      "a link that overrides its ancestor",
      {},
      '<nav data-next-press="off"><a id="l" href="/b/" data-next-press="all">B</a></nav>',
      "touch",
      FULL,
    ],
  ])(
    "under %s answers a %s press with %o",
    async (_name, mode, markup, type, extra) => {
      settings = { prefetch: "intent", ...mode };
      const el = link(markup);
      make();
      press(el, { pointerType: type });
      await wait(0);
      expect(extras()).toEqual([extra]);
    },
  );

  it.each([
    ["a modifier key", (el: Element) => press(el, { ctrlKey: true })],
    ["a button other than the main one", (el: Element) => press(el, { button: 1 })],
    [
      "a touchstart",
      (el: Element) => el.dispatchEvent(new Event("touchstart", { bubbles: true })),
    ],
    [
      "a path the router fetches as a plain document",
      (el: Element) => {
        plains = ["/b/"];
        press(el);
      },
    ],
  ])("keeps a shell prefetch for %s", async (_name, act) => {
    settings = { prefetch: "intent", press: "all" };
    const el = link();
    make();
    act(el);
    await wait(0);
    expect(extras()).toEqual([SHALLOW]);
  });

  it.each([
    ["a data saver", { saveData: true }, false, "intent"],
    ["a hidden page", {}, true, "intent"],
    ["prefetching off", {}, false, "off"],
  ])("sends nothing at a press on %s", async (_name, connection, hidden, mode) => {
    Object.defineProperty(navigator, "connection", {
      value: connection,
      configurable: true,
    });
    vi.spyOn(document, "hidden", "get").mockReturnValue(hidden);
    settings = { prefetch: mode, press: "all" };
    const el = link();
    make();
    press(el);
    await wait(0);
    expect(asked).toEqual([]);
  });

  it.each([
    ["a fresh complete answer is cached", () => shell({ ...SHELL, complete: true })],
    ["the page refused prefetches", () => new Response(null, { status: 204 })],
    ["prefetches are paused", () => new Response(null, { status: 429 })],
  ])("sends nothing at a press while %s", async (_name, reply) => {
    replies = [reply()];
    const el = link();
    const prefetch = make();
    await prefetch.prefetch(at("/b/"));
    press(el);
    await wait(0);
    expect(extras()).toEqual([SHALLOW]);
  });

  it("takes a waiting shell prefetch of the link off the queue at the press", async () => {
    const answers = [held(), held(), held()];
    const el = link();
    const prefetch = make();
    const done = ["/a/", "/c/", "/d/"].map((path) => prefetch.prefetch(at(path)));
    const waiting = prefetch.prefetch(at("/b/"));
    press(el);
    await waiting;
    for (const answer of answers) answer();
    await Promise.all(done);
    await wait(0);
    expect(hrefs()).toEqual(["/a/", "/c/", "/d/", "/b/"]);
    expect(extras().at(-1)).toEqual(FULL);
  });

  it("leaves a shell prefetch of the link in flight and hands a visit both", async () => {
    const answer = held();
    const reply = shell();
    replies.push(reply);
    const el = link();
    const prefetch = make();
    const flying = prefetch.prefetch(at("/b/"));
    press(el);
    expect(extras()).toEqual([SHALLOW, FULL]);
    const joined = prefetch.take(at("/b/"));
    expect(joined).toBeInstanceOf(Promise);
    fire(document, "pointercancel");
    const response = shell();
    answer(response);
    const found = (await joined)!;
    expect(found).toMatchObject({ raw: SHELL, response });
    expect(await found.flight?.answer).toBe(reply);
    expect(aborted()).toEqual([false, false]);
    await flying;
  });

  it.each([
    ["a cancelled pointer", () => fire(document, "pointercancel")],
    ["a drag", () => fire(document, "dragstart")],
    ["a context menu", () => fire(document, "contextmenu")],
    [
      "a navigation that starts",
      () => document.dispatchEvent(new CustomEvent("router:start")),
    ],
    [
      "a release outside the link",
      () => {
        point("p");
        fire(document.getElementById("p")!, "pointerup");
      },
    ],
  ])("aborts the pressed request on %s", (_name, end) => {
    stalled();
    const el = link();
    const prefetch = make();
    press(el);
    end();
    expect(aborted()).toEqual([true]);
    expect(prefetch.take(at("/b/"))).toBeUndefined();
  });

  it("keeps the pressed request when the pointer comes up on the link", () => {
    stalled();
    const el = link();
    const prefetch = make();
    const found = point("s");
    fire(el, "pointerup");
    expect(found).not.toHaveBeenCalled();
    press(el);
    fire(document.getElementById("s")!, "pointerup", { clientX: 3, clientY: 4 });
    expect(found).toHaveBeenCalledWith(3, 4);
    expect(aborted()).toEqual([false]);
    expect(taken(prefetch, "/b/").flight?.answer).toBeInstanceOf(Promise);
  });

  it("aborts the pressed request once the press outlasts its window", () => {
    vi.useFakeTimers();
    stalled();
    const el = link();
    make();
    press(el);
    vi.advanceTimersByTime(999);
    expect(aborted()).toEqual([false]);
    vi.advanceTimersByTime(1);
    expect(aborted()).toEqual([true]);
  });

  it("keeps a flight a visit claimed past the window", () => {
    vi.useFakeTimers();
    stalled();
    const el = link();
    const prefetch = make();
    press(el);
    expect(taken(prefetch, "/b/").flight?.answer).toBeInstanceOf(Promise);
    vi.advanceTimersByTime(1000);
    fire(document, "pointercancel");
    expect(aborted()).toEqual([false]);
  });

  it("aborts the press on one link once another is pressed", () => {
    stalled();
    stalled();
    document.body.innerHTML = '<a id="a" href="/a/">A</a><a id="b" href="/b/">B</a>';
    make();
    press(document.getElementById("a")!);
    press(document.getElementById("b")!);
    expect(hrefs()).toEqual(["/a/", "/b/"]);
    expect(aborted()).toEqual([true, false]);
  });

  it("keeps the envelope a released press already received for the next visit", async () => {
    const raw = { ...SHELL, complete: true, assets: [{ kind: "css", url: "/b.css" }] };
    const response = shell(raw);
    replies = [response];
    const el = link();
    const prefetch = make();
    press(el);
    await wait(0);
    fire(document, "pointercancel");
    await wait(0);
    expect(aborted()).toEqual([false]);
    expect(prefetch.take(at("/b/"))).toEqual({ raw, response });
    expect(document.head.querySelector("link")?.rel).toBe("preload");
  });

  it.each([
    [
      "a stream",
      () =>
        new Response("event: end\ndata: 1\n\n", {
          headers: { "content-type": "text/event-stream" },
        }),
    ],
    [
      "a redirected answer",
      () => {
        const response = shell();
        Object.defineProperty(response, "redirected", { value: true });
        return response;
      },
    ],
    ["an answer without layouts", () => shell({ version: "v1", ops: [] })],
    [
      "a body that does not parse",
      () => new Response("{", { headers: { "content-type": CONTENT_TYPE } }),
    ],
    ["a refusal", () => shell(SHELL, { status: 404 })],
  ])("aborts %s a released press received and keeps nothing", async (_name, reply) => {
    replies = [reply()];
    const el = link();
    const prefetch = make();
    press(el);
    await wait(0);
    fire(document, "pointercancel");
    await wait(0);
    expect(aborted()).toEqual([true]);
    expect(prefetch.take(at("/b/"))).toBeUndefined();
  });

  it("keeps the envelope a press received once the press outlasts its window", async () => {
    vi.useFakeTimers();
    const response = shell();
    replies = [response];
    const el = link();
    const prefetch = make();
    press(el);
    await vi.advanceTimersByTimeAsync(999);
    expect(aborted()).toEqual([false]);
    await vi.advanceTimersByTimeAsync(1);
    expect(aborted()).toEqual([false]);
    expect(prefetch.take(at("/b/"))).toEqual({ raw: SHELL, response });
  });

  it("hands a visit the refusal a press received rather than asking a third time", async () => {
    const refusal = shell(SHELL, { status: 404 });
    replies = [refusal];
    const el = link();
    const prefetch = make();
    press(el);
    expect(await taken(prefetch, "/b/").flight?.answer).toBe(refusal);
    expect(hrefs()).toEqual(["/b/"]);
  });

  it("presses past a complete answer kept for an asset version the page no longer runs", async () => {
    replies = [shell({ ...SHELL, complete: true })];
    const el = link();
    const prefetch = make();
    await prefetch.prefetch(at("/b/"));
    vi.spyOn(surface, "_headers").mockReturnValue(
      new Headers({ "X-Next-Version": "v2" }),
    );
    press(el);
    expect(extras()).toEqual([SHALLOW, FULL]);
  });

  it("aborts a pressed request on a drop and keeps nothing it received", async () => {
    const el = link();
    const prefetch = make();
    press(el);
    await wait(0);
    prefetch.drop();
    await wait(0);
    expect(aborted()).toEqual([true]);
    expect(prefetch.take(at("/b/"))).toBeUndefined();
  });

  it("marks a page that refuses the press and hands a visit an empty flight", async () => {
    replies = [new Response(null, { status: 204 })];
    const el = link();
    const prefetch = make();
    press(el);
    const found = taken(prefetch, "/b/");
    expect(found.raw).toBeUndefined();
    expect(await found.flight?.answer).toBeUndefined();
    await prefetch.prefetch(at("/b/"));
    expect(hrefs()).toEqual(["/b/"]);
  });

  it.each([
    [
      "a busy answer with Retry-After",
      { status: 429, headers: { "Retry-After": "5" } },
      5_000,
    ],
    ["a busy answer", { status: 429 }, 30_000],
    ["a server error", { status: 503 }, 30_000],
  ])(
    "pauses prefetches and drops the waiting ones on %s to a press",
    async (_name, init, pause) => {
      vi.useFakeTimers({ toFake: ["Date"], now: 1_000_000 });
      const answers = [held(), held(), held()];
      replies.push(new Response(null, init));
      const el = link();
      const prefetch = make();
      const done = ["/a/", "/c/", "/d/"].map((path) => prefetch.prefetch(at(path)));
      const waiting = prefetch.prefetch(at("/e/"));
      press(el);
      expect(await taken(prefetch, "/b/").flight?.answer).toBeUndefined();
      await waiting;
      for (const answer of answers) answer();
      await Promise.all(done);
      vi.advanceTimersByTime(pause - 1);
      await prefetch.prefetch(at("/f/"));
      vi.advanceTimersByTime(2);
      await prefetch.prefetch(at("/f/"));
      expect(hrefs()).toEqual(["/a/", "/c/", "/d/", "/b/", "/f/"]);
    },
  );

  it("hands a visit an empty flight when the network fails the press", async () => {
    replies = [() => Promise.reject(new TypeError("offline"))];
    const el = link();
    const prefetch = make();
    press(el);
    expect(await taken(prefetch, "/b/").flight?.answer).toBeUndefined();
    await prefetch.prefetch(at("/b/"));
    expect(hrefs()).toEqual(["/b/", "/b/"]);
  });

  it("hands a visit the pressed flight beside a kept shell and stops it on demand", async () => {
    const response = shell();
    replies = [response];
    stalled();
    const el = link();
    const prefetch = make();
    await prefetch.prefetch(at("/b/"));
    press(el);
    const found = taken(prefetch, "/b/");
    expect(found).toMatchObject({
      raw: SHELL,
      response,
      flight: { answer: expect.any(Promise) },
    });
    found.flight!.stop();
    expect(aborted()).toEqual([false, true]);
    expect(await found.flight?.answer).toBeUndefined();
  });

  it("hands a visit the pressed flight alone when nothing is kept", async () => {
    const reply = shell();
    replies = [reply];
    const el = link();
    const prefetch = make();
    press(el);
    const found = taken(prefetch, "/b/#part");
    expect(found.raw).toBeUndefined();
    expect(found.response).toBeUndefined();
    expect(await found.flight?.answer).toBe(reply);
    expect(prefetch.take(at("/b/"))).toBeUndefined();
  });

  it("leaves a pressed flight in place for a visit to another URL", () => {
    stalled();
    const el = link();
    const prefetch = make();
    press(el);
    expect(prefetch.take(at("/c/"))).toBeUndefined();
    expect(aborted()).toEqual([false]);
    expect(taken(prefetch, "/b/").flight?.answer).toBeInstanceOf(Promise);
  });

  it("asks nothing for the URL the router is loading", async () => {
    going = at("/b/");
    const prefetch = make();
    await prefetch.prefetch(at("/b/#part"));
    await prefetch.prefetch(at("/c/"));
    expect(hrefs()).toEqual(["/c/"]);
  });
});

describe("layer links", () => {
  const LAYER =
    '<a id="l" href="/m/" data-next-layer="sheet"><span id="s">M</span></a>';

  interface Sent {
    url: string;
    headers: Headers;
    signal: AbortSignal;
  }

  let sent: Sent[];
  let answers: Reply[];

  beforeEach(() => {
    sent = [];
    answers = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init = {}) => {
      const signal = init.signal!;
      sent.push({ url: input as string, headers: new Headers(init.headers), signal });
      const reply = answers.shift() ?? zone();
      return reply instanceof Response ? Promise.resolve(reply) : reply(signal);
    });
  });

  function zone(
    init: { status?: number; headers?: Record<string, string> } = {},
  ): Response {
    return new Response('{"ops":[]}', {
      ...init,
      headers: {
        "content-type": CONTENT_TYPE,
        "X-Next-Version": "v1",
        ...init.headers,
      },
    });
  }

  function opener(markup = LAYER): Element {
    document.body.innerHTML = markup;
    return document.getElementById("l")!;
  }

  function press(el: Element, pointerType = "mouse"): void {
    el.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, pointerType }));
  }

  function hover(el: Element): Promise<void> {
    fire(el, "pointerover");
    return wait(40);
  }

  const send = (
    href = "/m/",
    name = "sheet",
  ): ReturnType<NonNullable<PartialSurface["_take"]>> => surface._take?.(href, name);

  const request = (): [string, RequestInit] => [
    at("/m/"),
    { signal: new AbortController().signal },
  ];

  it("prefetches a hovered layer link as a full zone request for the page it opens over", async () => {
    make();
    await hover(opener());
    expect(asked).toEqual([]);
    expect(sent).toHaveLength(1);
    expect(sent[0]!.url).toBe(at("/m/"));
    expect(Object.fromEntries(sent[0]!.headers)).toMatchObject({
      accept: expect.stringContaining(CONTENT_TYPE),
      "x-next-request": "1",
      "x-next-zone": "sheet",
      "x-next-origin": location.pathname + location.search,
      "x-next-prefetch": "full",
    });
    expect(sent[0]!.headers.get("accept")).not.toContain("text/event-stream");
  });

  it("hands the kept answer to the layer once, and never to a navigation", async () => {
    const reply = zone();
    answers = [reply];
    const prefetch = make();
    await hover(opener());
    expect(prefetch.take(at("/m/"))).toBeUndefined();
    const taken = send();
    expect(taken).toBeDefined();
    expect(await taken!(...request())).toBe(reply);
    expect(send()).toBeUndefined();
  });

  it("keeps a layer answer and a navigation answer of one URL apart", async () => {
    const prefetch = make();
    await prefetch.prefetch(at("/m/"));
    expect(send()).toBeUndefined();
    await hover(opener());
    expect(send("/m/", "other")).toBeUndefined();
    expect(send()).toBeDefined();
    expect(prefetch.take(at("/m/"))).toBeDefined();
  });

  it("forgets a layer answer prefetched over another page", async () => {
    make();
    await hover(opener());
    history.replaceState(null, "", "/elsewhere/");
    try {
      expect(send()).toBeUndefined();
    } finally {
      history.replaceState(null, "", "/");
    }
  });

  it.each([
    ["a failed answer", () => zone({ status: 500 })],
    ["a refused prefetch", () => new Response(null, { status: 204 })],
    [
      "an answer of another type",
      () => new Response("<p>x</p>", { headers: { "content-type": "text/html" } }),
    ],
    [
      "a redirected answer",
      () => {
        const response = zone();
        Object.defineProperty(response, "redirected", { value: true });
        return response;
      },
    ],
  ])("keeps no layer answer for %s", async (_name, reply) => {
    answers = [reply()];
    make();
    await hover(opener());
    expect(send()).toBeUndefined();
  });

  it("asks a refused layer link no more", async () => {
    answers = [new Response(null, { status: 204 })];
    make();
    const el = opener();
    await hover(el);
    fire(el, "pointerout");
    await hover(el);
    expect(sent).toHaveLength(1);
  });

  it("drops a kept layer answer of an asset version the page no longer runs", async () => {
    make();
    await hover(opener());
    vi.spyOn(surface, "_headers").mockReturnValue(
      new Headers({ "X-Next-Version": "v2" }),
    );
    expect(send()).toBeUndefined();
  });

  it("leaves a layer link with its prefetch turned off alone", async () => {
    make();
    await hover(
      opener(
        '<a id="l" href="/m/" data-next-layer="sheet" data-next-prefetch="off">M</a>',
      ),
    );
    expect(sent).toEqual([]);
    expect(asked).toEqual([]);
  });

  it("owns a layer link by its URL alone, a plain link by the element", async () => {
    const owned = vi.fn<HistoryHost["owned"]>().mockReturnValue(true);
    createPrefetch({ ...host(), owned }, { signal: listening.signal });
    await hover(opener());
    expect(owned).toHaveBeenCalledWith(new URL(at("/m/")), undefined);
  });

  it("sends the press of a layer link at once and hands its flight to the layer", async () => {
    let answer: (response: Response) => void = () => undefined;
    answers = [() => new Promise<Response>((done) => (answer = done))];
    make();
    press(opener());
    expect(sent).toHaveLength(1);
    expect(sent[0]!.headers.get("x-next-prefetch")).toBe("full");
    const taken = send();
    const reply = zone();
    const landing = taken!(...request());
    answer(reply);
    expect(await landing).toBe(reply);
  });

  it("waits for a layer prefetch still in flight", async () => {
    let answer: (response: Response) => void = () => undefined;
    answers = [() => new Promise<Response>((done) => (answer = done))];
    make();
    fire(opener(), "pointerover");
    await wait(40);
    const taken = send();
    const reply = zone();
    const landing = taken!(...request());
    answer(reply);
    expect(await landing).toBe(reply);
  });

  it("fetches itself once a pressed layer flight comes back empty", async () => {
    answers = [new Response(null, { status: 429 }), zone()];
    make();
    press(opener());
    const [url, init] = request();
    const reply = await send()!(url, init);
    expect(sent).toHaveLength(2);
    expect(sent[1]!.url).toBe(url);
    expect(reply.status).toBe(200);
  });

  it("stops a pressed layer flight once the layer drops its request", () => {
    answers = [
      (signal) =>
        new Promise<Response>((_done, fail) =>
          signal.addEventListener("abort", () => fail(signal.reason as Error)),
        ),
    ];
    make();
    press(opener());
    const controller = new AbortController();
    void send()!(at("/m/"), { signal: controller.signal }).catch(() => undefined);
    return wait(0).then(() => {
      controller.abort();
      expect(sent[0]!.signal.aborted).toBe(true);
    });
  });

  it("stops a pressed flight a kept answer makes needless", async () => {
    let answer: (response: Response) => void = () => undefined;
    const reply = zone();
    answers = [
      () => new Promise<Response>((done) => (answer = done)),
      (signal) =>
        new Promise<Response>((_done, fail) =>
          signal.addEventListener("abort", () => fail(signal.reason as Error)),
        ),
    ];
    make();
    const el = opener();
    await hover(el);
    press(el);
    expect(sent).toHaveLength(2);
    const landing = send()!(...request());
    answer(reply);
    expect(await landing).toBe(reply);
    expect(sent[1]!.signal.aborted).toBe(true);
  });
});
