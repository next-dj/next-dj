import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Asset, Envelope, MorphPatch } from "./apply";
import type { ScriptsChunk } from "./chunks";
import type { Bounds, Move } from "./morph";
import type { HistoryHost, Settings, Turn } from "./router";
import { createSwap, persist, retire, sheets, slice } from "./swap";
import { historyHost } from "./test-doubles";

let settings: Settings;

beforeEach(() => {
  settings = {};
  document.head.innerHTML = "";
  document.body.innerHTML = "<!--s--><main><p>old</p></main><!--e-->";
});

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(document, "startViewTransition");
  document.documentElement.removeAttribute("data-next-transition");
  delete document.body.dataset.ran;
});

function host(over: Partial<HistoryHost> = {}): HistoryHost {
  return historyHost({ settings: () => settings, ...over });
}

function bounds(): Bounds {
  const [start, , end] = [...document.body.childNodes];
  return [document.body, start!, end!];
}

function swapIn(html: string): void {
  const [, main] = [...document.body.childNodes];
  (main as Element).outerHTML = html;
}

function envelope(assets: Asset[] = []): Envelope {
  return { version: "", ops: [], assets, form: null };
}

function turn(
  record: Record<string, unknown> = {},
  op: MorphPatch = { op: "morph" },
  over: Partial<HistoryHost> = {},
): Turn {
  return createSwap(host(over)).swap(record, op, bounds(), "forward");
}

describe("page stylesheets", () => {
  it("disables the page sheets a manifest drops and enables the ones it lists", () => {
    document.head.innerHTML =
      '<link rel="stylesheet" href="/a.css" data-next-asset="page">' +
      '<link rel="stylesheet" href="/b.css" data-next-asset="page">' +
      '<link rel="stylesheet" href="/c.css">' +
      '<style data-next-asset="page">.x{}</style>';
    const [a, b, c] = [...document.querySelectorAll("link")];
    const style = document.querySelector("style")!;
    retire([a!.href, ".x{}"]);
    expect([a!.disabled, b!.disabled, style.disabled]).toEqual([false, true, false]);
    expect(c!.disabled).toBeFalsy();
    expect(sheets()).toEqual([a!.href, ".x{}"]);
    retire([]);
    expect(sheets()).toEqual([]);
  });

  it("toggles the loaded sheet of a link instead of the link itself", () => {
    document.head.innerHTML =
      '<link rel="stylesheet" href="/a.css" data-next-asset="page">';
    const link = document.querySelector("link")!;
    const sheet = { disabled: false };
    Object.defineProperty(link, "sheet", { value: sheet });
    retire([]);
    expect(sheet.disabled).toBe(true);
    expect(link.disabled).toBeFalsy();
    expect(link.hasAttribute("disabled")).toBe(false);
    expect(sheets()).toEqual([]);
    retire([link.href]);
    expect(sheet.disabled).toBe(false);
    expect(sheets()).toEqual([link.href]);
  });
});

describe("slices", () => {
  it("holds detached copies of the nodes between two markers or a whole parent", () => {
    const held = slice(bounds());
    const main = document.querySelector("main")!;
    main.firstElementChild!.textContent = "new";
    expect(held.content.firstElementChild).not.toBe(main);
    expect(held.innerHTML).toBe("<main><p>old</p></main>");
    expect(slice([main, null, null]).innerHTML).toBe("<p>new</p>");
  });
});

describe("persisted elements", () => {
  const moves: Node[] = [];
  const move: Move = (parent, node, before) => {
    moves.push(node);
    parent.insertBefore(node, before);
  };

  beforeEach(() => {
    moves.length = 0;
  });

  it("carries a live element into the place of its incoming twin", () => {
    swapIn('<main><video data-next-persist="v" id="live"></video></main>');
    const live = document.getElementById("live")!;
    const [rescue, place] = persist(bounds(), move);
    rescue();
    expect(live.parentElement!.parentElement).toBe(document.documentElement);
    swapIn('<section><video data-next-persist="v" id="twin"></video></section>');
    const twin = document.getElementById("twin")!;
    const removed = vi.fn();
    twin.addEventListener("next:removed", removed);
    place();
    expect(document.querySelector("section video")).toBe(live);
    expect(twin.isConnected).toBe(false);
    expect(removed).toHaveBeenCalledTimes(1);
    expect(moves).toEqual([live, live]);
    expect(document.documentElement.children).toHaveLength(2);
  });

  it("drops a live element the incoming content no longer holds", () => {
    swapIn('<main><video data-next-persist="v" id="live"></video></main>');
    const live = document.getElementById("live")!;
    const removed = vi.fn();
    live.addEventListener("next:removed", removed);
    const [rescue, place] = persist(bounds(), move);
    rescue();
    swapIn("<main></main>");
    place();
    expect(live.isConnected).toBe(false);
    expect(removed).toHaveBeenCalledTimes(1);
  });

  it("keeps the first element of a name and leaves a second to the swap", () => {
    swapIn(
      '<main><b data-next-persist="v" id="one"></b>' +
        '<b data-next-persist="v" id="two"></b></main>',
    );
    const [one, two] = ["one", "two"].map((id) => document.getElementById(id)!);
    const [rescue, place] = persist(bounds(), move);
    rescue();
    expect(two!.isConnected).toBe(true);
    swapIn('<main><b data-next-persist="v" id="new"></b></main>');
    place();
    expect(document.querySelector("main b")).toBe(one);
    expect(moves).toEqual([one, one]);
  });

  it("carries a nested persisted element inside its persisted ancestor", () => {
    swapIn(
      '<main><div data-next-persist="o" id="outer">' +
        '<b data-next-persist="i" id="inner"></b></div></main>',
    );
    const [outer, inner] = ["outer", "inner"].map((id) => document.getElementById(id)!);
    const [rescue, place] = persist(bounds(), move);
    rescue();
    swapIn(
      '<section><div data-next-persist="o">' +
        '<b data-next-persist="i"></b></div></section>',
    );
    place();
    expect(document.querySelector("section div")).toBe(outer);
    expect(outer!.firstElementChild).toBe(inner);
    expect(moves).toEqual([outer, outer]);
  });

  it("adds nothing to the document when no element persists", () => {
    const [rescue, place] = persist(bounds());
    rescue();
    expect(document.documentElement.children).toHaveLength(2);
    place();
    expect(moves).toEqual([]);
  });
});

describe("inline scripts of a swap", () => {
  const scripts =
    '<script nonce="n1">document.body.dataset.ran = (document.body.dataset.ran ?? "") + "a"</script>' +
    '<script nonce="evil">document.body.dataset.ran = "evil"</script>' +
    '<script nonce="n1" src="/x.js"></script>' +
    '<script nonce="n1" type="application/ld+json">{}</script>' +
    '<script nonce="n1" type="module">void 0</script>';

  it("runs again only the scripts carrying the nonce of the answer", () => {
    const op: MorphPatch = { op: "morph", html: `<main>${scripts}</main>` };
    const swapped = turn({ nonce: "n1" }, op, { nonce: "page" });
    const html = op.html!;
    expect(html.match(/data-next-run/g)).toHaveLength(2);
    expect(html).toContain('nonce="evil"');
    const parsed = document.createElement("template");
    parsed.innerHTML = html;
    parsed.content.querySelectorAll("script").forEach((el) => el.remove());
    swapIn(parsed.innerHTML);
    swapped.before();
    swapped.after(envelope(), [], true);
    expect(document.body.dataset.ran).toBe("a");
    const fresh = [...document.querySelectorAll<HTMLScriptElement>("main script")];
    expect(fresh.map((el) => [el.type, el.getAttribute("nonce")])).toEqual([
      ["", "page"],
      ["module", "page"],
    ]);
    expect(document.querySelector("template")).toBeNull();
  });

  it("runs nothing for an answer without a nonce", () => {
    for (const nonce of [null, ""]) {
      const op: MorphPatch = { op: "morph", html: `<main>${scripts}</main>` };
      turn({ nonce }, op);
      expect(op.html).toBe(`<main>${scripts}</main>`);
    }
    const bare: MorphPatch = { op: "morph" };
    turn({ nonce: "n1" }, bare);
    expect(bare).toEqual({ op: "morph" });
  });

  it("inserts a script without a nonce on a page without one", () => {
    const op: MorphPatch = {
      op: "morph",
      html: '<main><script nonce="n1">document.body.dataset.ran = "5"</script></main>',
    };
    const swapped = turn({ nonce: "n1" }, op);
    swapIn(op.html!);
    swapped.after(envelope(), [], true);
    expect(document.querySelector("main script")!.hasAttribute("nonce")).toBe(false);
    expect(document.body.dataset.ran).toBe("5");
  });

  it("skips a script the swap left out and runs none of a failed swap", () => {
    const html =
      '<main><script nonce="n1">document.body.dataset.ran = "7"</script></main>';
    turn({ nonce: "n1" }, { op: "morph", html }).after(envelope(), [], true);
    expect(document.body.dataset.ran).toBeUndefined();
    const op: MorphPatch = { op: "morph", html };
    const failed = turn({ nonce: "n1" }, op);
    swapIn(op.html!);
    failed.after(envelope(), [], false);
    expect(document.body.dataset.ran).toBeUndefined();
    expect(document.querySelector("main template")).not.toBeNull();
  });
});

describe("the manifest and the scripts of a swap", () => {
  it("retires the page sheets the new manifest drops", () => {
    document.head.innerHTML =
      '<link rel="stylesheet" href="/a.css" data-next-asset="page">' +
      '<link rel="stylesheet" href="/b.css" data-next-asset="page">' +
      '<style data-next-asset="page">.x{}</style>';
    turn().after(
      envelope([
        { kind: "css", url: "/a.css" },
        { kind: "css", url: "", inline: ".x{}" },
      ]),
      [],
      true,
    );
    const [a, b] = [...document.querySelectorAll("link")];
    expect([a!.disabled, b!.disabled]).toEqual([false, true]);
    expect(document.querySelector("style")!.disabled).toBe(false);
  });

  it("marks the sheets the manifest scopes to the page before it retires", () => {
    document.head.innerHTML =
      '<link rel="stylesheet" href="/a.css" data-next-asset="page">' +
      '<link rel="stylesheet" href="/b.css"><style>.x{}</style><style>.y{}</style>';
    turn().after(
      envelope([
        { kind: "css", url: "/b.css", scope: "page" },
        { kind: "css", url: "", inline: ".x{}", scope: "page" },
        { kind: "css", url: "", inline: ".y{}" },
      ]),
      [],
      true,
    );
    const [a, b] = [...document.querySelectorAll("link")];
    const [x, y] = [...document.querySelectorAll("style")];
    const marks = [a, b, x, y].map((el) => el!.getAttribute("data-next-asset"));
    expect(marks).toEqual(["page", "page", "page", null]);
    expect([a!.disabled, b!.disabled]).toEqual([true, false]);
  });

  it("hands the scripts and the consent of the new page to the scripts chunk", async () => {
    const chunk = {
      consent: { _configure: vi.fn(), _reveal: vi.fn() },
      scripts: { _configure: vi.fn() },
    };
    const ready = vi.fn(() => Promise.resolve(chunk as unknown as ScriptsChunk));
    const nodes = [document.querySelector("main")!];
    const list = [{ name: "a", src: "/a.js" }];
    const swapped = turn({ scripts: list, consent: null }, undefined, {
      scripts: ready,
    });
    swapped.after(envelope(), nodes, true);
    await vi.waitFor(() => expect(chunk.consent._reveal).toHaveBeenCalledWith(nodes));
    expect(ready).toHaveBeenCalledTimes(1);
    expect(chunk.consent._configure).toHaveBeenCalledWith(null);
    expect(chunk.scripts._configure).toHaveBeenCalledWith(list);
  });

  it("loads no scripts chunk for a page without scripts or consent", () => {
    const ready = vi.fn(() => Promise.reject(new Error("unused")));
    turn({ scripts: null, consent: null }, undefined, { scripts: ready }).after(
      envelope(),
      [],
      true,
    );
    turn({ consent: { categories: [] } }).after(envelope(), [], true);
    expect(ready).not.toHaveBeenCalled();
  });

  it("swallows a scripts chunk that cannot load", async () => {
    const ready = vi.fn(() => Promise.reject(new Error("offline")));
    turn({ consent: {} }, undefined, { scripts: ready }).after(envelope(), [], true);
    await Promise.resolve();
    expect(ready).toHaveBeenCalledTimes(1);
  });
});

describe("view transitions", () => {
  function transitions(start: (arg: unknown) => unknown) {
    const spy = vi.fn(start);
    Object.defineProperty(document, "startViewTransition", {
      value: spy,
      configurable: true,
    });
    return spy;
  }

  function motion(reduced: boolean): void {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: reduced && query.includes("reduce"),
    }));
  }

  it("swaps inside a view transition typed by the motion", async () => {
    settings = { transitions: true };
    motion(false);
    const start = transitions(() => ({}));
    const release = vi.fn();
    const hold = vi.fn(() => release);
    const swapped = turn();
    swapped.hold(hold);
    expect(hold).toHaveBeenCalledTimes(1);
    expect(document.documentElement.getAttribute("data-next-transition")).toBe(
      "forward",
    );
    const options = start.mock.calls[0]![0] as {
      update: () => Promise<void>;
      types: string[];
    };
    expect(options.types).toEqual(["forward"]);
    let settled = false;
    const update = options.update().then(() => (settled = true));
    expect(release).toHaveBeenCalledTimes(1);
    await Promise.resolve();
    expect(settled).toBe(false);
    swapped.after(envelope(), [], true);
    await update;
    expect(settled).toBe(true);
  });

  it("falls back to the callback form an older browser takes", () => {
    settings = { transitions: true };
    motion(false);
    const start = transitions((arg) => {
      if (typeof arg !== "function") throw new TypeError("callback expected");
      return {};
    });
    turn().hold(() => () => undefined);
    expect(start).toHaveBeenCalledTimes(2);
    expect(start.mock.calls[1]![0]).toEqual(expect.any(Function));
  });

  it.each([
    ["the setting is off", false, true, false],
    ["the browser has no transitions", true, false, false],
    ["the user asks for reduced motion", true, true, true],
  ])("swaps without a transition when %s", (_name, enabled, present, reduced) => {
    settings = { transitions: enabled };
    motion(reduced);
    if (present) transitions(() => ({}));
    const hold = vi.fn(() => () => undefined);
    turn().hold(hold);
    expect(hold).not.toHaveBeenCalled();
    expect(document.documentElement.hasAttribute("data-next-transition")).toBe(false);
  });
});

describe("extracting a document", () => {
  const chain = (): ReturnType<HistoryHost["chain"]> => [
    ["L1", "D1", "S1"],
    ["L2", "D2", "S2"],
  ];

  function page(layouts: string, body: string): string {
    return (
      '<html><head><title>New</title><link rel="stylesheet" href="/p.css" ' +
      'data-next-asset="page"><link rel="stylesheet" href="/s.css">' +
      '<script type="module" src="/m.js"></script><script src="/c.js"></script>' +
      `</head><body>${body}<script data-next-init>Next._init({})</script>` +
      `<script type="application/json" data-next-layouts>${layouts}</script>` +
      "</body></html>"
    );
  }

  it("carves the slot of the deepest layout both chains share", () => {
    const html = page(
      '[["L1","D1","T1"],["L2","D9","T2"]]',
      "<header>h</header><!--nx:s L1 T1--><main>new</main><!--/nx:s L1 T1-->",
    );
    const raw = createSwap(host({ chain })).extract(html, "/n/");
    expect(raw).toEqual({
      version: "",
      ops: [
        { op: "morph", target: { slot: "L1" }, html: "<main>new</main>" },
        { op: "url", href: "/n/" },
        { op: "meta", title: "New" },
      ],
      assets: [
        { kind: "css", url: "/p.css", scope: "page" },
        { kind: "css", url: "/s.css", scope: null },
        { kind: "module", url: "/m.js" },
        { kind: "js", url: "/c.js" },
      ],
      layouts: [
        ["L1", "D1", null],
        ["L2", "D9", "T2"],
      ],
    });
  });

  it.each([
    ["a document of another site", "[]"],
    ["a slot whose markers are gone", '[["L1","D1","T1"]]'],
    ["a chain the page shares nothing with", '[["L7","D7","T7"]]'],
    [
      "a chain deeper than the one on screen",
      '[["L1","D1","T1"],["L2","D2","T2"],["L3","D3","T3"]]',
    ],
  ])("morphs the whole body for %s", (_name, layouts) => {
    const html = page(layouts, "<main>body</main>");
    const raw = createSwap(host({ chain })).extract(html, "/n/");
    const [morph] = raw.ops as Record<string, unknown>[];
    expect(morph!.target).toEqual({ body: true });
    expect(morph!.html).toContain("<main>body</main>");
    const responseIds = (raw.layouts as unknown[][]).map((layout) => layout[2]);
    expect(responseIds).toEqual(
      JSON.parse(layouts).map((layout: string[]) => layout[2]),
    );
  });

  it("keeps the sources of the runtime nonce, not noscript or data ones", () => {
    const html =
      '<html><head><link rel="stylesheet" href="/a.css" nonce="n">' +
      '<script type="application/json" src="/d.json"></script>' +
      '<script nonce="n" data-next-init>Next._init({})</script></head><body><main>x</main>' +
      '<noscript><link rel="stylesheet" href="/n.css"></noscript></body></html>';
    const raw = createSwap(host({ nonce: "n" })).extract(html, "/n/");
    expect(raw.assets).toEqual([{ kind: "css", url: "/a.css", scope: null }]);
  });

  it.each([
    [
      "a source without the runtime nonce",
      '<script src="/evil.js"></script><script nonce="n" data-next-init>Next._init({})' +
        "</script>",
      "n",
    ],
    [
      "two runtimes that disagree",
      '<script nonce="a" data-next-init>Next._init({})</script>' +
        '<script nonce="b" data-next-init>Next._init({})' +
        '</script><link rel="stylesheet" href="/a.css" nonce="a">',
      undefined,
    ],
    [
      "a document without a nonce on a page with one",
      '<link rel="stylesheet" href="/a.css">',
      "n",
    ],
  ])("hands back no envelope for %s", (_name, head, nonce) => {
    const html = `<html><head>${head}</head><body><main>x</main></body></html>`;
    expect(createSwap(host({ nonce })).extract(html, "/n/")).toEqual({});
  });

  it.each([
    ["an inline script", "<script>run()</script>"],
    ["a module", '<script type="module">run()</script>'],
  ])("hands back no envelope for a slot holding %s", (_name, script) => {
    const html = page(
      '[["L1","D1","T1"]]',
      `<!--nx:s L1 T1-->${script}<!--/nx:s L1 T1-->`,
    );
    expect(createSwap(host({ chain })).extract(html, "/n/")).toEqual({});
  });

  it("carves a slot whose scripts are data, inert or the init", () => {
    const html = page(
      '[["L1","D1","T1"]]',
      '<!--nx:s L1 T1--><script type="application/json">{}</script>' +
        "<noscript><script>run()</script></noscript><main>m</main><!--/nx:s L1 T1-->",
    );
    const raw = createSwap(host({ chain })).extract(html, "/n/");
    expect((raw.ops as Record<string, unknown>[])[0]!.target).toEqual({ slot: "L1" });
  });

  it("reads a layouts block the server never filled as no layouts", () => {
    const html =
      '<body><script type="application/json" data-next-layouts>"nx:layouts s"</script>' +
      '<script type="application/json" data-next-layouts>[</script></body>';
    expect(createSwap(host({ chain: () => [] })).extract(html, "/n/").layouts).toEqual(
      [],
    );
    const broken = html.replace('"nx:layouts s"', "[");
    expect(
      createSwap(host({ chain: () => [] })).extract(broken, "/n/").layouts,
    ).toEqual([]);
  });

  it("reads a document without any payload as one with no layouts", () => {
    const raw = createSwap(host({ chain: () => [] })).extract("<p>plain</p>", "/n/");
    expect(raw.layouts).toEqual([]);
    expect(raw.assets).toEqual([]);
  });
});
