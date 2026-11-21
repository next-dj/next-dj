import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  onTestFinished,
  vi,
} from "vitest";
import { createHistory } from "./history";
import { createPartial } from "./partial";
import type { PartialSurface } from "./partial";
import { CONTENT_TYPE } from "./protocol";
import { createRouter } from "./router";
import type { Entry, RouterHost } from "./router";
import { chunkModules } from "./test-doubles";

type Detail = Record<string, unknown>;

interface Options {
  page?: string;
  router?: Record<string, unknown>;
  layouts?: unknown;
  state?: unknown;
  context?: Detail;
}

const SETTINGS = {
  scope: "all",
  snapshots: 10,
  intercept: "opt-in",
};

const SLOTTED =
  '<nav><a id="home" href="/">Home</a><a id="nav-b" href="/b/">B</a></nav>' +
  '<!--nx:s L1 S1--><main><h1>Home</h1><a id="b" href="/b/">B</a></main>' +
  "<!--/nx:s L1 S1-->";

const REGIONS =
  '<div data-next-scroll-region id="r1"></div>' +
  '<div data-next-scroll-region id="r2"></div>';

let listening: AbortController;
let surface: PartialSurface;
let calls: Headers[];
let scrolled: ReturnType<typeof vi.fn>;

beforeEach(() => {
  listening = new AbortController();
  calls = [];
  scrolled = vi.fn();
  history.replaceState(null, "", "/");
  document.title = "Home";
  sessionStorage.clear();
  vi.stubGlobal("scrollTo", scrolled);
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  listening.abort();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

function envelope(html = "<main><h1>B</h1></main>", href = "/b/", extra: Detail = {}) {
  return {
    version: "",
    ops: [
      { op: "morph", target: { slot: "L1" }, html },
      { op: "url", action: "push", href },
      { op: "meta", title: `Page ${href}` },
    ],
    layouts: [["L1", "D1", null]],
    ...extra,
  };
}

function answer(body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    headers: { "content-type": CONTENT_TYPE, ...headers },
  });
}

function serve(...replies: Response[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn((_url: string, init: RequestInit) => {
      calls.push(new Headers(init.headers));
      return Promise.resolve(replies.shift() ?? answer(envelope()));
    }),
  );
}

function own(created: PartialSurface): PartialSurface {
  onTestFinished(() => created._reset());
  return created;
}

function setup(options: Options = {}) {
  document.body.innerHTML = options.page ?? SLOTTED;
  if (options.state !== undefined) history.replaceState(options.state, "", "/");
  const context: Detail = {
    $router: options.router ?? SETTINGS,
    $layouts: options.layouts ?? [["L1", "D1", "S1"]],
    ...options.context,
  };
  surface = own(
    createPartial({
      ...chunkModules,
      dispatch: () => undefined,
      mergeContext: (data) => Object.assign(context, data),
      context: () => context,
    }),
  );
  surface._configure({ reload: () => undefined });
  const host: RouterHost = {
    listen: () => () => undefined,
    state: () => history.state as unknown,
    replace: (entry, href) => history.replaceState(entry, "", href),
    assign: () => undefined,
  };
  const on = (_event: "ready", listener: () => void): (() => void) => {
    listener();
    return () => undefined;
  };
  const router = createRouter({ partial: surface, context, on }, host, {
    signal: listening.signal,
  });
  router._install((seams) => createHistory(seams, { signal: listening.signal }));
  return { router, context };
}

function settled(): Promise<void> {
  return new Promise((resolve) =>
    document.addEventListener("router:end", () => resolve(), { once: true }),
  );
}

function step(state: unknown, at: string): void {
  history.replaceState(state, "", at);
  window.dispatchEvent(new PopStateEvent("popstate", { state }));
}

async function pop(state: unknown, at = "/"): Promise<void> {
  step(state, at);
  await Promise.resolve();
}

function back(state: unknown, at = "/"): Promise<void> {
  const done = settled();
  step(state, at);
  return done;
}

function heading(): string | null {
  return document.querySelector("main h1")?.textContent ?? null;
}

describe("snapshots", () => {
  it("restores a fresh snapshot of a popped entry without a request", async () => {
    const { router } = setup({ page: SLOTTED + REGIONS });
    serve(answer(envelope()));
    const home = history.state as Entry;
    vi.stubGlobal("scrollY", 420);
    document.getElementById("r1")!.scrollTop = 7;
    await router.visit("/b/");
    expect(heading()).toBe("B");
    expect(document.title).toBe("Page /b/");
    scrolled.mockClear();
    await pop(home);
    expect(heading()).toBe("Home");
    expect(document.title).toBe("Home");
    expect(scrolled).toHaveBeenCalledWith(0, 420);
    expect(scrolled).toHaveBeenCalledTimes(1);
    expect(calls).toHaveLength(1);
  });

  it("revalidates a restored snapshot older than the prefetch lifetime", async () => {
    let now = 1_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    const { router } = setup({ router: { ...SETTINGS, prefetch_ttl: 5 } });
    serve(answer(envelope()), answer(envelope("<main><h1>Fresh</h1></main>", "/")));
    const home = history.state as Entry;
    await router.visit("/b/");
    now += 5_000;
    const done = back(home);
    await Promise.resolve();
    expect(heading()).toBe("Home");
    await done;
    expect(heading()).toBe("Fresh");
    expect(calls).toHaveLength(2);
  });

  it("restores the context the popped page ran with", async () => {
    const { router, context } = setup({ context: { user: "ann" } });
    serve(
      answer(
        envelope(undefined, "/b/", {
          ops: [
            ...envelope().ops,
            { op: "context", replace: true, data: { team: "red" } },
          ],
        }),
      ),
    );
    const home = history.state as Entry;
    await router.visit("/b/");
    expect(context).toMatchObject({ user: undefined, team: "red" });
    await pop(home);
    expect(context).toMatchObject({ user: "ann", team: undefined });
    expect(context.$layouts).toEqual([["L1", "D1", "S1"]]);
  });

  it("restores the entry it left on a pop when going forward again", async () => {
    const { router } = setup();
    serve(answer(envelope()));
    const home = history.state as Entry;
    await router.visit("/b/");
    const page = history.state as Entry;
    await pop(home);
    expect(heading()).toBe("Home");
    await pop(page, "/b/");
    expect(heading()).toBe("B");
    expect(calls).toHaveLength(1);
  });

  it("keeps the current layouts down to the slot and the snapshot's below it", async () => {
    const nested = SLOTTED.replace(
      "<h1>Home</h1>",
      "<!--nx:s L2 S2--><h1>Home</h1><!--/nx:s L2 S2-->",
    );
    const { router } = setup({
      page: nested,
      layouts: [
        ["L1", "D1", "S1"],
        ["L2", "D2", "S2"],
      ],
    });
    serve(
      answer(
        envelope("<main><!--nx:s L3 S9--><h1>B</h1><!--/nx:s L3 S9--></main>", "/b/", {
          layouts: [
            ["L1", "D1x", null],
            ["L3", "D3", "S9"],
          ],
        }),
      ),
      answer(envelope("<main><h1>C</h1></main>", "/c/")),
    );
    const home = history.state as Entry;
    await router.visit("/b/");
    await pop(home);
    expect(document.body.innerHTML).toContain("<!--nx:s L2 S2-->");
    await router.visit("/c/");
    expect(calls[1]!.get("X-Next-Layouts")).toBe("L1.D1x,L2.D2");
  });

  it("restores a body snapshot on a pop", async () => {
    const bodied = (html: string, href: string): Detail => ({
      version: "",
      ops: [
        { op: "morph", target: { body: true }, html },
        { op: "url", action: "push", href },
      ],
      layouts: [],
    });
    const { router } = setup({
      router: { scope: "all" },
      page: "<main><h1>Home</h1></main>",
      layouts: [],
    });
    serve(answer(bodied("<main><h1>B</h1></main>", "/b/")));
    const home = history.state as Entry;
    await router.visit("/b/");
    await pop(home);
    expect(heading()).toBe("Home");
  });

  it.each([
    ["the page refused one", { "X-Next-Router": "no-snapshot" }, {}],
    ["an answer cleared them", {}, { "X-Next-Router": "clear" }],
  ])("keeps no snapshot when %s", async (_name, first, second) => {
    const { router } = setup();
    serve(
      answer(envelope(), first),
      answer(envelope("<main><h1>C</h1></main>", "/c/"), second),
      answer(envelope("<main><h1>B</h1></main>", "/b/")),
    );
    await router.visit("/b/");
    const page = history.state as Entry;
    await router.visit("/c/");
    const done = back(page, "/b/");
    expect(heading()).toBe("C");
    await done;
    expect(heading()).toBe("B");
  });

  it("restores a popped entry but keeps none of the page that refused one", async () => {
    const { router } = setup();
    serve(
      answer(envelope(), { "X-Next-Router": "no-snapshot" }),
      answer(envelope("<main><h1>B</h1></main>", "/b/")),
    );
    const home = history.state as Entry;
    await router.visit("/b/");
    const page = history.state as Entry;
    await pop(home);
    expect(heading()).toBe("Home");
    const forward = back(page, "/b/");
    expect(heading()).toBe("Home");
    await forward;
    expect(heading()).toBe("B");
  });

  it("keeps no more snapshots than the payload allows", async () => {
    const { router } = setup({ router: { ...SETTINGS, snapshots: 1 } });
    serve(
      answer(envelope()),
      answer(envelope("<main><h1>C</h1></main>", "/c/")),
      answer(envelope("<main><h1>Home</h1></main>", "/")),
    );
    const home = history.state as Entry;
    await router.visit("/b/");
    await router.visit("/c/");
    const done = back(home);
    expect(heading()).toBe("C");
    await done;
  });

  it("requests a popped entry whose snapshot slot is no longer on screen", async () => {
    const { router } = setup();
    serve(
      answer(envelope()),
      answer({
        version: "",
        ops: [
          { op: "morph", target: { body: true }, html: "<main><h1>C</h1></main>" },
          { op: "url", action: "push", href: "/c/" },
        ],
        layouts: [["L2", "D2", "S2"]],
      }),
      answer(envelope("<main><h1>Home</h1></main>", "/")),
    );
    const home = history.state as Entry;
    await router.visit("/b/");
    await router.visit("/c/");
    const done = back(home);
    expect(heading()).toBe("C");
    await done;
    expect(calls[2]!.get("X-Next-Layouts")).toBe("L2.D2");
  });

  it("restores the page stylesheets a popped entry had on", async () => {
    const { router } = setup();
    surface._configure({
      reload: () => undefined,
      loadLink: (url, _nonce, done) => {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = url;
        document.head.append(link);
        done(true);
      },
    });
    document.head.innerHTML =
      '<link rel="stylesheet" href="/home.css" data-next-asset="page">' +
      '<link rel="stylesheet" href="/site.css">';
    const page = (url: string): Detail => ({
      assets: [{ kind: "css", url, scope: "page" }],
    });
    serve(answer(envelope(undefined, "/b/", page("/b.css"))));
    const home = history.state as Entry;
    await router.visit("/b/");
    const sheet = (url: string): HTMLLinkElement =>
      document.head.querySelector(`link[href="${url}"]`)!;
    expect(sheet("/b.css").getAttribute("data-next-asset")).toBe("page");
    expect(sheet("/home.css").disabled).toBe(true);
    expect(sheet("/b.css").disabled).toBe(false);
    await pop(home);
    expect(sheet("/home.css").disabled).toBe(false);
    expect(sheet("/b.css").disabled).toBe(true);
    expect(sheet("/site.css").disabled).toBeFalsy();
    document.head.innerHTML = "";
  });

  it("keeps a persisted element across the restore of a popped entry", async () => {
    const page = SLOTTED.replace(
      "<h1>Home</h1>",
      '<h1>Home</h1><video data-next-persist="player" id="v1"></video>',
    );
    const { router } = setup({ page });
    const player = document.getElementById("v1");
    const twin = '<main><h1>B</h1><video data-next-persist="player"></video></main>';
    serve(answer(envelope(twin)));
    const home = history.state as Entry;
    await router.visit("/b/");
    expect(document.querySelector("main video")).toBe(player);
    await pop(home);
    expect(document.querySelector("main video")).toBe(player);
    expect(document.querySelectorAll("video")).toHaveLength(1);
  });

  it("drops every snapshot on a page restored from the cache", async () => {
    const { router } = setup();
    serve(answer(envelope()), answer(envelope("<main><h1>Home</h1></main>", "/")));
    const home = history.state as Entry;
    await router.visit("/b/");
    window.dispatchEvent(Object.assign(new Event("pageshow"), { persisted: false }));
    window.dispatchEvent(Object.assign(new Event("pageshow"), { persisted: true }));
    const done = back(home);
    expect(heading()).toBe("B");
    await done;
  });
});

describe("scroll positions", () => {
  it("saves the scroll on pagehide under the entry on screen", () => {
    setup();
    const key = (history.state as Entry).key;
    vi.stubGlobal("scrollY", 75);
    window.dispatchEvent(new Event("pagehide"));
    expect(JSON.parse(sessionStorage.getItem("nx:scroll")!)).toEqual([[key, [75]]]);
  });

  it("restores the scroll of the entry it booted on", () => {
    sessionStorage.setItem("nx:scroll", JSON.stringify([["k9", [120, 30]]]));
    setup({ state: { next: 1, kind: "nav", key: "k9" }, page: SLOTTED + REGIONS });
    expect(scrolled).toHaveBeenCalledWith(0, 120);
    expect(document.getElementById("r1")!.scrollTop).toBe(30);
  });

  it.each([
    [2, ["b", "c"]],
    [0, []],
  ])("keeps no more positions than a snapshot bound of %i", (snapshots, kept) => {
    surface = own(
      createPartial({
        ...chunkModules,
        dispatch: () => undefined,
        mergeContext: () => undefined,
      }),
    );
    const keeper = createHistory(
      {
        partial: surface,
        settings: () => ({ snapshots }),
        context: () => ({}),
        region: () => undefined,
        chain: () => [],
        key: () => "",
        ask: () => Promise.reject(new Error("offline")),
        owned: () => true,
        flush: () => undefined,
      },
      { signal: listening.signal },
    );
    for (const key of ["a", "b", "c"]) keeper.leave(key, { slot: "L1" });
    const saved = JSON.parse(sessionStorage.getItem("nx:scroll")!) as [string][];
    expect(saved.map(([key]) => key)).toEqual(kept);
  });

  it("scrolls once more at the end when the page was too short", async () => {
    sessionStorage.setItem("nx:scroll", JSON.stringify([["k1", [500]]]));
    const { router } = setup({ router: { scope: "all" } });
    serve(answer(envelope()), answer(envelope("<main><h1>X</h1></main>", "/x/")));
    await router.visit("/b/");
    scrolled.mockClear();
    await back({ next: 1, kind: "nav", key: "k1" }, "/x/");
    expect(scrolled.mock.calls).toEqual([
      [0, 500],
      [0, 500],
    ]);
  });

  it("navigates when the session store is unreadable or refuses writes", async () => {
    sessionStorage.setItem("nx:scroll", "{");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("full");
    });
    const { router } = setup({ state: { next: 1, kind: "nav", key: "k" } });
    serve(answer(envelope()));
    await router.visit("/b/");
    expect(heading()).toBe("B");
  });
});
