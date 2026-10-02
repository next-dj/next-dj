import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Applier } from "./apply";
import type { HistoryAdapter } from "./apply";
import { createLayers } from "./layers";
import type { LayerStack, PopStateAdapter } from "./layers";
import { createNavigation } from "./navigation";
import type { Navigation } from "./navigation";
import { pageKey } from "./protocol";
import { createTriggers } from "./triggers";
import { chunkModules } from "./test-doubles";

interface Dispatched {
  event: string;
  detail: Record<string, unknown>;
}

function envelope(ops: unknown[]): Record<string, unknown> {
  return { version: "v1", ops, assets: [], form: null };
}

// window.history underneath, with the title each write happened under.
function recordingHistory() {
  const writes: { action: string; href: string; title: string }[] = [];
  const history: HistoryAdapter = {
    push(href) {
      writes.push({ action: "push", href, title: document.title });
      window.history.pushState(null, "", href);
    },
    replace(href) {
      writes.push({ action: "replace", href, title: document.title });
      window.history.replaceState(null, "", href);
    },
  };
  return { history, writes };
}

function manualPopstate() {
  let handler: (() => void) | undefined;
  const popstate: PopStateAdapter = {
    listen(h) {
      handler = h;
      return () => {
        handler = undefined;
      };
    },
  };
  return { popstate, back: () => handler?.() };
}

describe("the commit phase", () => {
  let dispatched: Dispatched[];
  let writes: { action: string; href: string; title: string }[];
  let navigation: Navigation;
  let layers: LayerStack;
  let applier: Applier;
  let back: () => void;
  let order: string[];

  beforeEach(() => {
    document.body.innerHTML = "";
    document.head.innerHTML = "<title>Feed</title>";
    window.history.replaceState(null, "", "/feed/");
    dispatched = [];
    order = [];
    const recording = recordingHistory();
    writes = recording.writes;
    const dispatch = (event: string, detail: Record<string, unknown>): void => {
      dispatched.push({ event, detail });
      order.push(event);
    };
    navigation = createNavigation({ dispatch, document, history: recording.history });
    const manual = manualPopstate();
    back = manual.back;
    layers = createLayers({
      dispatch,
      fetch: async (request) =>
        void applier.apply(
          envelope([
            {
              op: "morph",
              target: { zone: request.zone },
              html: `<div data-next-zone="${request.zone}">body</div>`,
            },
            { op: "meta", title: "Photo" },
          ]),
          { page: new URL(request.url, location.href).pathname },
        ),
      document,
      dialog: { open: () => () => undefined },
      navigation,
      popstate: manual.popstate,
    });
    layers.install(document);
    applier = new Applier({
      dispatch,
      mergeContext: () => undefined,
      document,
      layers: () => layers,
      navigation: () => navigation,
    });
  });

  afterEach(() => {
    layers._reset();
    window.history.replaceState(null, "", "/");
  });

  function navigated(): Record<string, unknown>[] {
    return dispatched.filter((d) => d.event === "next:navigated").map((d) => d.detail);
  }

  it("a url op after meta writes the entry before the new title lands", () => {
    applier.apply(
      envelope([
        { op: "meta", title: "Page 2" },
        { op: "url", href: "/feed/?page=2" },
      ]),
    );
    expect(writes).toEqual([{ action: "push", href: "/feed/?page=2", title: "Feed" }]);
    expect(document.title).toBe("Page 2");
  });

  it("a url op before meta gives the same order all the same", () => {
    applier.apply(
      envelope([
        { op: "url", href: "/feed/?page=2" },
        { op: "meta", title: "Page 2" },
      ]),
    );
    expect(writes).toEqual([{ action: "push", href: "/feed/?page=2", title: "Feed" }]);
    expect(document.title).toBe("Page 2");
  });

  it("the entry left behind keeps its own title in the Back menu", () => {
    // A browser freezes the leaving entry's title at the push and titles the new
    // one from whatever the document says afterwards.
    const entries: { href: string; title: string }[] = [
      { href: "/feed/", title: document.title },
    ];
    const session = createNavigation({
      dispatch: () => undefined,
      history: {
        push(href) {
          entries[entries.length - 1]!.title = document.title;
          entries.push({ href, title: "" });
        },
        replace: () => undefined,
      },
    });
    const local = new Applier({
      dispatch: () => undefined,
      mergeContext: () => undefined,
      navigation: () => session,
    });
    local.apply(
      envelope([
        { op: "meta", title: "Page 2" },
        { op: "url", href: "/feed/?page=2" },
      ]),
    );
    entries[entries.length - 1]!.title = document.title;
    expect(entries).toEqual([
      { href: "/feed/", title: "Feed" },
      { href: "/feed/?page=2", title: "Page 2" },
    ]);
  });

  it("announces one navigation per envelope, after partial:applied", () => {
    applier.apply(
      envelope([
        { op: "url", href: "/feed/?page=2" },
        { op: "meta", title: "Page 2" },
        { op: "url", href: "/feed/?page=2&sort=new", action: "replace" },
      ]),
    );
    expect(navigated()).toEqual([
      {
        url: `${location.origin}/feed/?page=2&sort=new`,
        path: "/feed/?page=2&sort=new",
        title: "Page 2",
        action: "push",
      },
    ]);
    expect(order.indexOf("partial:applied")).toBeLessThan(
      order.indexOf("next:navigated"),
    );
  });

  it("a title-only change is announced with no history action", () => {
    applier.apply(envelope([{ op: "meta", title: "Feed (3)" }]));
    expect(navigated()).toEqual([
      {
        url: `${location.origin}/feed/`,
        path: "/feed/",
        title: "Feed (3)",
        action: "none",
      },
    ]);
  });

  it("an envelope that moves neither the URL nor the title announces nothing", () => {
    applier.apply(envelope([{ op: "meta", title: "  Feed " }, { op: "toast" }]));
    expect(navigated()).toEqual([]);
  });

  it("[meta, layer.close] lands the meta on the host after the close, one event", async () => {
    await layers.open(null, "/photos/1/", "photo");
    dispatched.length = 0;
    applier.apply(
      envelope([
        { op: "meta", title: "Feed (saved)" },
        { op: "layer.close", result: 1 },
      ]),
    );
    expect(layers.size()).toBe(0);
    expect(document.title).toBe("Feed (saved)");
    expect(navigated()).toEqual([
      expect.objectContaining({
        path: "/feed/",
        title: "Feed (saved)",
        action: "replace",
      }),
    ]);
  });

  it("[layer.close, meta] gives the same title and the same single event", async () => {
    await layers.open(null, "/photos/1/", "photo");
    dispatched.length = 0;
    applier.apply(
      envelope([
        { op: "layer.close", result: 1 },
        { op: "meta", title: "Feed (saved)" },
      ]),
    );
    expect(document.title).toBe("Feed (saved)");
    expect(navigated()).toEqual([
      expect.objectContaining({ title: "Feed (saved)", action: "replace" }),
    ]);
  });

  it("a layer body writes its URL, then shows the layer's own title", async () => {
    await layers.open(null, "/photos/1/", "photo");
    expect(writes).toEqual([{ action: "push", href: "/photos/1/", title: "Feed" }]);
    expect(document.title).toBe("Photo");
    expect(navigated()).toEqual([
      expect.objectContaining({ path: "/photos/1/", action: "push" }),
    ]);
  });

  it("a Back gesture with no layer open is announced as a pop", () => {
    applier.apply(envelope([{ op: "url", href: "/feed/?page=2" }]));
    window.history.replaceState(null, "", "/feed/");
    back();
    expect(navigated().at(-1)).toEqual(
      expect.objectContaining({
        path: "/feed/",
        action: "pop",
      }),
    );
  });

  it("a throwing push reports the url op and announces nothing", () => {
    const failing = createNavigation({
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      history: {
        push: () => {
          throw new Error("rate limited");
        },
        replace: () => undefined,
      },
    });
    const local = new Applier({
      dispatch: () => undefined,
      mergeContext: () => undefined,
      navigation: () => failing,
    });
    local.apply(envelope([{ op: "url", href: "/elsewhere/" }]));
    expect(dispatched.find((d) => d.event === "partial:error")?.detail).toMatchObject({
      kind: "op",
      op: "url",
    });
    expect(navigated()).toEqual([]);
  });

  it("an external address change inside a commit is announced as a push", () => {
    const commit = navigation.begin();
    window.history.pushState(null, "", "/feed/elsewhere/");
    commit.end();
    expect(navigated()).toEqual([
      expect.objectContaining({
        path: "/feed/elsewhere/",
        action: "push",
      }),
    ]);
  });

  it("dropping a superseded hold leaves the newer one to announce", () => {
    const committed: string[] = [];
    const drop = navigation.hold("/photos/2/", "/photos/2/", () =>
      committed.push("old"),
    );
    const again = navigation.hold("/photos/2/", "/photos/2/", () =>
      committed.push("new"),
    );
    drop();
    expect(location.pathname).toBe("/photos/2/");
    applier.apply(envelope([]), { page: "/photos/2/" });
    expect(committed).toEqual(["new"]);
    again();
  });

  it("a hold pushes at once and its drop rolls back, neither announced", () => {
    const drop = navigation.hold("/photos/2/", "/photos/2/", () => undefined);
    expect(location.pathname).toBe("/photos/2/");
    drop();
    expect(location.pathname).toBe("/feed/");
    expect(writes.map((w) => w.action)).toEqual(["push", "replace"]);
    expect(navigated()).toEqual([]);
  });

  it("a drop after the bar moved on leaves it where it is", () => {
    const drop = navigation.hold("/photos/2/", "/photos/2/", () => undefined);
    window.history.replaceState(null, "", "/elsewhere/");
    drop();
    expect(location.pathname).toBe("/elsewhere/");
  });

  it("current answers where the page stands", () => {
    expect(navigation.current()).toEqual({
      url: `${location.origin}/feed/`,
      path: "/feed/",
      title: "Feed",
    });
  });
});

describe("a filter submit replaces the URL through the navigation", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("announces a url replace for the filtered page", () => {
    window.history.replaceState(null, "", "/catalog/");
    document.body.innerHTML =
      '<form data-next-target="results"><input name="q" data-next-trigger="input"></form>';
    const dispatched: Dispatched[] = [];
    const navigation = createNavigation({
      dispatch: (event, detail) => dispatched.push({ event, detail }),
    });
    const triggers = createTriggers({
      fetch: () => undefined,
      abort: () => undefined,
      history: navigation.asHistory(),
      poll: chunkModules.poll,
    });
    const detach = triggers.install(document);
    const input = document.querySelector("input")!;
    input.value = "novel";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    detach();
    expect(dispatched.find((d) => d.event === "next:navigated")?.detail).toMatchObject({
      path: "/catalog/?q=novel",
      action: "replace",
    });
  });
});

describe("the history view of the navigation", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("announces a push written through it", () => {
    window.history.replaceState(null, "", "/a/");
    const dispatched: Dispatched[] = [];
    const navigation = createNavigation({
      dispatch: (event, detail) => dispatched.push({ event, detail }),
    });
    navigation.asHistory().push("/b/");
    expect(location.pathname).toBe("/b/");
    expect(dispatched[0]!.detail).toMatchObject({ path: "/b/", action: "push" });
  });
});

describe("pageKey", () => {
  it("keys a same-origin URL by path and search, a foreign one by itself", () => {
    expect(pageKey(`${location.origin}/a/?b=1#c`, document)).toBe("/a/?b=1");
    expect(pageKey("https://attacker.example/a/", document)).toBe(
      "https://attacker.example/a/",
    );
  });
});

describe("the meta op syncs the four head tags", () => {
  beforeEach(() => {
    document.head.innerHTML =
      "<title>Feed</title>" +
      '<meta name="description" content="old">' +
      '<meta property="og:title" content="social">';
  });

  function apply(op: Record<string, unknown>): void {
    new Applier({ dispatch: () => undefined, mergeContext: () => undefined }).apply(
      envelope([{ op: "meta", ...op }]),
    );
  }

  it("upserts description, canonical and robots and leaves social tags alone", () => {
    apply({
      title: "Board 7",
      description: "new",
      canonical: "https://example.com/board/7/",
      robots: "noindex, nofollow",
    });
    expect(document.title).toBe("Board 7");
    expect(document.querySelectorAll('meta[name="description"]')).toHaveLength(1);
    expect(
      document.querySelector('meta[name="description"]')!.getAttribute("content"),
    ).toBe("new");
    expect(document.querySelector('link[rel="canonical"]')!.getAttribute("href")).toBe(
      "https://example.com/board/7/",
    );
    expect(document.querySelector('meta[name="robots"]')!.getAttribute("content")).toBe(
      "noindex, nofollow",
    );
    expect(
      document.querySelector('meta[property="og:title"]')!.getAttribute("content"),
    ).toBe("social");
  });

  it("removes a tag named null and keeps one left out", () => {
    apply({ title: null, description: null, robots: null });
    expect(document.head.querySelector("title")).toBeNull();
    expect(document.querySelector('meta[name="description"]')).toBeNull();
    apply({ canonical: "https://example.com/" });
    expect(document.querySelector('link[rel="canonical"]')).not.toBeNull();
  });

  it("collapses whitespace in an incoming title", () => {
    apply({ title: "  Board\n\t 7  " });
    expect(document.head.querySelector("title")!.textContent).toBe("Board 7");
  });
});
