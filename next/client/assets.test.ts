import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createAssets } from "./assets";
import type { Assets, LinkLoader, SessionStore } from "./assets";
import type { Asset } from "./apply";
import type { Clock } from "./wire";

interface Dispatched {
  event: string;
  detail: Record<string, unknown>;
}

function fakeClock(): Clock {
  return { now: () => 0, setTimeout: () => 1, clearTimeout: () => undefined };
}

function memorySession(): SessionStore {
  const store = new Map<string, string>();
  return {
    get: (key) => store.get(key) ?? null,
    set: (key, value) => void store.set(key, value),
    remove: (key) => void store.delete(key),
  };
}

function css(url: string): Asset {
  return { kind: "css", url };
}

function makeAssets(over: Partial<Parameters<typeof createAssets>[0]> = {}) {
  const dispatched: Dispatched[] = [];
  const loaded: string[] = [];
  const loadLink: LinkLoader = (url, _nonce, done) => {
    loaded.push(url);
    done(true);
  };
  const assets = createAssets({
    dispatch: (event, detail) => dispatched.push({ event, detail }),
    document,
    clock: fakeClock(),
    loadLink,
    navigate: () => undefined,
    session: memorySession(),
    cssTimeoutMs: 10,
    ...over,
  });
  return { assets, dispatched, loaded };
}

describe("assets registry and delta", () => {
  beforeEach(() => {
    document.head.innerHTML = "";
    document.body.innerHTML = "";
  });

  it("seeds from the DOM and only loads the delta", () => {
    document.head.innerHTML = '<link rel="stylesheet" href="http://x/a.css">';
    const { assets, loaded } = makeAssets();
    assets.seed();
    assets.loadCss([css("http://x/a.css"), css("http://x/b.css")], () => undefined);
    expect(loaded).toEqual(["http://x/b.css"]);
  });

  it("empties the registry on reset and loads a seeded url again", () => {
    document.head.innerHTML = '<link rel="stylesheet" href="http://x/kept.css">';
    const { assets, loaded } = makeAssets();
    assets.seed();
    assets._reset();
    // Reset drops the registry but keeps the seeded flag, so no rescan restores
    // the key and the sheet still on the page counts as missing again.
    assets.loadCss([css("http://x/kept.css")], () => undefined);
    expect(loaded).toEqual(["http://x/kept.css"]);
  });

  it("seeds script srcs so the js delta skips an already-loaded script", () => {
    document.head.innerHTML = '<script src="http://x/seeded.js"></script>';
    const { assets } = makeAssets();
    assets.seed();
    assets.loadJs([{ kind: "js", url: "http://x/seeded.js" }]);
    expect(
      document.head.querySelectorAll('script[src="http://x/seeded.js"]'),
    ).toHaveLength(1);
  });

  it("does not insert a module the server already rendered into the page", () => {
    // A server-rendered module is a <script type="module" src>, so the src scan
    // seeds it and a second element would mount its island twice.
    document.head.innerHTML =
      '<script type="module" src="http://x/island.mjs"></script>';
    const { assets } = makeAssets();
    assets.seed();
    assets.loadJs([{ kind: "module", url: "http://x/island.mjs", load: "module" }]);
    expect(
      document.head.querySelectorAll('script[src="http://x/island.mjs"]'),
    ).toHaveLength(1);
  });

  it("scans on the first js delta when nobody seeded the registry", () => {
    // _configure builds a fresh registry and does not re-seed it, so an apply
    // can reach the delta before seed() ever ran.
    document.head.innerHTML =
      '<script type="module" src="http://x/island.mjs"></script>';
    const { assets } = makeAssets();
    assets.loadJs([{ kind: "module", url: "http://x/island.mjs", load: "module" }]);
    expect(
      document.head.querySelectorAll('script[src="http://x/island.mjs"]'),
    ).toHaveLength(1);
  });

  it("scans on the first css delta when nobody seeded the registry", () => {
    document.head.innerHTML = '<link rel="stylesheet" href="http://x/a.css">';
    const { assets, loaded } = makeAssets();
    assets.loadCss([css("http://x/a.css")], () => undefined);
    expect(loaded).toEqual([]);
  });

  it("takes the lazy first scan once, not on every delta", () => {
    const { assets } = makeAssets();
    assets.loadJs([{ kind: "js", url: "http://x/first.js" }]);
    const scan = vi.spyOn(document, "querySelectorAll");
    assets.loadJs([{ kind: "js", url: "http://x/second.js" }]);
    assets.loadCss([css("http://x/second.css")], () => undefined);
    expect(scan).not.toHaveBeenCalled();
  });

  it("seeds an inline style so the inline css delta skips it", () => {
    document.head.innerHTML = "<style>.z{}</style>";
    const { assets } = makeAssets();
    assets.seed();
    assets.loadCss([{ kind: "css", url: "", inline: ".z{}" }], () => undefined);
    expect(document.head.querySelectorAll("style")).toHaveLength(1);
  });

  it("seeds an inline script so the inline js delta does not re-execute it", () => {
    document.head.innerHTML = "<script>void 0</script>";
    const { assets } = makeAssets();
    assets.seed();
    assets.loadJs([{ kind: "js", url: "", inline: "void 0" }]);
    expect(document.head.querySelectorAll("script:not([src])")).toHaveLength(1);
  });

  it("seeds inline bodies from the body, not just the head", () => {
    document.body.innerHTML = "<style>.b{}</style><script>void 1</script>";
    const { assets } = makeAssets();
    assets.seed();
    assets.loadCss([{ kind: "css", url: "", inline: ".b{}" }], () => undefined);
    assets.loadJs([{ kind: "js", url: "", inline: "void 1" }]);
    expect(document.head.querySelectorAll("style")).toHaveLength(0);
    expect(document.head.querySelectorAll("script:not([src])")).toHaveLength(0);
  });

  it("inserts an inline asset that omits url entirely", () => {
    const { assets, loaded } = makeAssets();
    const done = vi.fn();
    assets.loadCss([{ kind: "css", inline: ".n{}" } as Asset], done);
    assets.loadJs([{ kind: "js", inline: "void 2" } as Asset]);
    expect(document.head.querySelector("style")!.textContent).toBe(".n{}");
    expect(document.head.querySelector("script:not([src])")!.textContent).toBe(
      "void 2",
    );
    expect(loaded).toEqual([]);
    expect(done).toHaveBeenCalledTimes(1);
  });

  it("still inserts an inline body that differs from the seeded one", () => {
    document.head.innerHTML = "<style>.z{}</style>";
    const { assets } = makeAssets();
    assets.seed();
    assets.loadCss([{ kind: "css", url: "", inline: ".y{}" }], () => undefined);
    expect(document.head.querySelectorAll("style")).toHaveLength(2);
  });

  it("gates the done callback until the last of several sheets settles", () => {
    const settlers: ((ok: boolean) => void)[] = [];
    const loadLink: LinkLoader = (_url, _nonce, done) => settlers.push(done);
    const { assets } = makeAssets({ loadLink });
    const done = vi.fn();
    assets.loadCss([css("http://x/one.css"), css("http://x/two.css")], done);
    expect(done).not.toHaveBeenCalled();
    settlers[0]!(true);
    expect(done).not.toHaveBeenCalled();
    settlers[1]!(true);
    expect(done).toHaveBeenCalledTimes(1);
  });

  it("fires one partial:error even when several sheets fail", () => {
    const settlers: ((ok: boolean) => void)[] = [];
    const loadLink: LinkLoader = (_url, _nonce, done) => settlers.push(done);
    const { assets, dispatched } = makeAssets({ loadLink });
    const done = vi.fn();
    assets.loadCss([css("http://x/a.css"), css("http://x/b.css")], done);
    settlers[0]!(false);
    settlers[1]!(false);
    expect(done).toHaveBeenCalledTimes(1);
    const errors = dispatched.filter((d) => d.event === "partial:error");
    expect(errors).toHaveLength(1);
    expect(errors[0]!.detail.kind).toBe("asset");
  });

  it("calls done synchronously when nothing is missing", () => {
    const { assets } = makeAssets();
    const done = vi.fn();
    assets.loadCss([], done);
    expect(done).toHaveBeenCalledTimes(1);
  });

  it("applies ops anyway and fires partial:error on a css error", () => {
    const loadLink: LinkLoader = (_url, _nonce, done) => done(false);
    const { assets, dispatched } = makeAssets({ loadLink });
    const done = vi.fn();
    assets.loadCss([css("http://x/late.css")], done);
    expect(done).toHaveBeenCalledTimes(1);
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(true);
  });

  it("inserts js once per url after the ops", () => {
    const { assets } = makeAssets();
    assets.loadJs([{ kind: "js", url: "http://x/w.js" }]);
    assets.loadJs([{ kind: "js", url: "http://x/w.js" }]);
    const scripts = document.head.querySelectorAll('script[src="http://x/w.js"]');
    expect(scripts.length).toBe(1);
  });

  it("ignores a malformed manifest entry", () => {
    const { assets, loaded } = makeAssets();
    assets.loadCss([{ kind: "css" } as never, css("http://x/ok.css")], () => undefined);
    expect(loaded).toEqual(["http://x/ok.css"]);
  });

  it("only loads the css kind from a mixed manifest", () => {
    const { assets, loaded } = makeAssets();
    assets.loadCss(
      [{ kind: "js", url: "http://x/j.js" }, css("http://x/s.css")],
      () => undefined,
    );
    expect(loaded).toEqual(["http://x/s.css"]);
  });

  it("injects an inline style with the body and skips the link loader", () => {
    const { assets, loaded } = makeAssets();
    const done = vi.fn();
    assets.loadCss([{ kind: "css", url: "", inline: ".z{color:red}" }], done);
    const style = document.head.querySelector("style")!;
    expect(style.textContent).toBe(".z{color:red}");
    expect(loaded).toEqual([]);
    expect(done).toHaveBeenCalledTimes(1);
  });

  it("dedupes an inline style by body across applies", () => {
    const { assets } = makeAssets();
    const inline = { kind: "css", url: "", inline: ".z{color:red}" };
    assets.loadCss([inline], () => undefined);
    assets.loadCss([inline], () => undefined);
    expect(document.head.querySelectorAll("style")).toHaveLength(1);
  });

  it("injects an inline script with its body as textContent", () => {
    const { assets } = makeAssets();
    assets.loadJs([{ kind: "js", url: "", inline: "globalThis.__x = 1" }]);
    const script = document.head.querySelector("script:not([src])")!;
    expect(script.textContent).toBe("globalThis.__x = 1");
  });

  it("dedupes an inline script by body across applies", () => {
    const { assets } = makeAssets();
    const inline = { kind: "js", url: "", inline: "globalThis.__x = 1" };
    assets.loadJs([inline]);
    assets.loadJs([inline]);
    expect(document.head.querySelectorAll("script:not([src])")).toHaveLength(1);
  });

  it("keeps inline and url assets of the same kind apart", () => {
    const { assets, loaded } = makeAssets();
    assets.loadCss(
      [{ kind: "css", url: "", inline: ".z{color:red}" }, css("http://x/u.css")],
      () => undefined,
    );
    expect(document.head.querySelector("style")!.textContent).toBe(".z{color:red}");
    expect(loaded).toEqual(["http://x/u.css"]);
  });

  it("inserts a module as a type=module script kept in order", () => {
    const { assets } = makeAssets();
    assets.loadJs([{ kind: "module", url: "http://x/island.mjs" }]);
    const script = document.head.querySelector<HTMLScriptElement>(
      'script[src="http://x/island.mjs"]',
    )!;
    expect(script.type).toBe("module");
    expect(script.async).toBe(false);
  });

  it("inserts a classic script without the module type", () => {
    const { assets } = makeAssets();
    assets.loadJs([{ kind: "js", url: "http://x/plain.js" }]);
    const script = document.head.querySelector<HTMLScriptElement>(
      'script[src="http://x/plain.js"]',
    )!;
    expect(script.type).toBe("");
    expect(script.async).toBe(false);
  });

  it("inserts scripts and modules in manifest order", () => {
    const { assets } = makeAssets();
    assets.loadJs([
      { kind: "module", url: "http://x/first.mjs" },
      { kind: "js", url: "http://x/second.js" },
      { kind: "module", url: "http://x/third.mjs" },
    ]);
    const srcs = Array.from(
      document.head.querySelectorAll<HTMLScriptElement>("script[src]"),
    ).map((script) => script.src);
    expect(srcs).toEqual([
      "http://x/first.mjs",
      "http://x/second.js",
      "http://x/third.mjs",
    ]);
  });

  it("loads a custom kind by the verb the server derived for it", () => {
    const { assets, loaded } = makeAssets();
    assets.loadCss(
      [{ kind: "theme", url: "http://x/theme.css", load: "link" }],
      () => undefined,
    );
    assets.loadJs([
      { kind: "island", url: "http://x/island.js", load: "script" },
      { kind: "widget", url: "http://x/widget.js", load: "module" },
    ]);
    expect(loaded).toEqual(["http://x/theme.css"]);
    expect(
      document.head.querySelector<HTMLScriptElement>(
        'script[src="http://x/island.js"]',
      )!.type,
    ).toBe("");
    expect(
      document.head.querySelector<HTMLScriptElement>(
        'script[src="http://x/widget.js"]',
      )!.type,
    ).toBe("module");
  });

  it("skips a kind whose renderer left no verb on the wire", () => {
    const { assets, loaded } = makeAssets();
    const done = vi.fn();
    assets.loadCss([{ kind: "wasm", url: "http://x/lib.wasm" } as Asset], done);
    assets.loadJs([{ kind: "wasm", url: "http://x/lib.wasm" } as Asset]);
    expect(loaded).toEqual([]);
    expect(document.head.querySelectorAll("script")).toHaveLength(0);
    expect(done).toHaveBeenCalledTimes(1);
  });

  it("prefers the server verb over the legacy meaning of the kind", () => {
    const { assets, loaded } = makeAssets();
    // A project that named its stylesheet kind "js" still gets a <link>, since
    // the server verb wins over the legacy meaning of the kind.
    assets.loadCss(
      [{ kind: "js", url: "http://x/odd.css", load: "link" }],
      () => undefined,
    );
    // A url of its own, so only the verb guard can keep it out of the head. The
    // sheet above already holds its key and would be skipped as a duplicate.
    assets.loadJs([{ kind: "js", url: "http://x/other.css", load: "link" }]);
    expect(loaded).toEqual(["http://x/odd.css"]);
    expect(document.head.querySelectorAll("script")).toHaveLength(0);
  });

  it("injects an inline module as a type=module script", () => {
    const { assets } = makeAssets();
    assets.loadJs([{ kind: "module", url: "", inline: "mount()" }]);
    const script = document.head.querySelector<HTMLScriptElement>("script:not([src])")!;
    expect(script.type).toBe("module");
    expect(script.textContent).toBe("mount()");
  });

  it("seeds an inline module so the inline module delta does not re-run it", () => {
    document.head.innerHTML = '<script type="module">mount()</script>';
    const { assets } = makeAssets();
    assets.seed();
    assets.loadJs([{ kind: "module", url: "", inline: "mount()" }]);
    expect(document.head.querySelectorAll("script:not([src])")).toHaveLength(1);
  });

  it("keeps an inline module and an inline script with the same body apart", () => {
    document.head.innerHTML = "<script>mount()</script>";
    const { assets } = makeAssets();
    assets.seed();
    // The seeded body ran as a classic script, so the module form of the same
    // source has not run yet and still has to be inserted.
    assets.loadJs([{ kind: "module", url: "", inline: "mount()" }]);
    const scripts =
      document.head.querySelectorAll<HTMLScriptElement>("script:not([src])");
    expect(scripts).toHaveLength(2);
    expect(scripts[1]!.type).toBe("module");
  });

  it("dedupes an inline module by body across applies", () => {
    const { assets } = makeAssets();
    const inline: Asset = { kind: "module", url: "", inline: "mount()" };
    assets.loadJs([inline]);
    assets.loadJs([inline]);
    expect(document.head.querySelectorAll("script:not([src])")).toHaveLength(1);
  });

  it("copies the bootstrap nonce onto inserted scripts", () => {
    const boot = document.createElement("script");
    boot.nonce = "nonce-7a3f";
    Object.defineProperty(document, "currentScript", {
      value: boot,
      configurable: true,
    });
    const { assets } = makeAssets();
    Object.defineProperty(document, "currentScript", {
      value: null,
      configurable: true,
    });
    assets.loadJs([{ kind: "js", url: "http://x/n.js" }]);
    const script = document.head.querySelector<HTMLScriptElement>(
      'script[src="http://x/n.js"]',
    )!;
    expect(script.nonce).toBe("nonce-7a3f");
  });

  it("copies the bootstrap nonce onto inline assets", () => {
    const boot = document.createElement("script");
    boot.nonce = "nonce-7a3f";
    Object.defineProperty(document, "currentScript", {
      value: boot,
      configurable: true,
    });
    const { assets } = makeAssets();
    Object.defineProperty(document, "currentScript", {
      value: null,
      configurable: true,
    });
    assets.loadCss([{ kind: "css", url: "", inline: ".z{}" }], () => undefined);
    assets.loadJs([{ kind: "js", url: "", inline: "void 0" }]);
    expect(document.head.querySelector<HTMLStyleElement>("style")!.nonce).toBe(
      "nonce-7a3f",
    );
    expect(
      document.head.querySelector<HTMLScriptElement>("script:not([src])")!.nonce,
    ).toBe("nonce-7a3f");
  });
});

describe("url dedup keys", () => {
  beforeEach(() => {
    document.head.innerHTML = "";
    document.body.innerHTML = "";
  });

  function absolute(path: string): string {
    return new URL(path, document.baseURI).href;
  }

  it("does not re-insert server-rendered assets a relative manifest names", () => {
    document.head.innerHTML =
      '<link rel="stylesheet" href="/static/x.css">' +
      '<script type="module" src="/static/m.js"></script>';
    const { assets, loaded } = makeAssets();
    assets.seed();
    assets.loadCss([css("/static/x.css")], () => undefined);
    assets.loadJs([{ kind: "module", url: "/static/m.js", load: "module" }]);
    expect(loaded).toEqual([]);
    expect(document.head.querySelectorAll("link")).toHaveLength(1);
    expect(document.head.querySelectorAll("script[src]")).toHaveLength(1);
  });

  it("dedupes an absolute manifest url against a relative document one", () => {
    document.head.innerHTML =
      '<link rel="stylesheet" href="/static/a.css">' +
      '<script src="/static/a.js"></script>';
    const { assets, loaded } = makeAssets();
    assets.seed();
    assets.loadCss([css(absolute("/static/a.css"))], () => undefined);
    assets.loadJs([{ kind: "js", url: absolute("/static/a.js") }]);
    expect(loaded).toEqual([]);
    expect(document.head.querySelectorAll("script[src]")).toHaveLength(1);
  });

  it("dedupes a relative manifest url against an absolute document one", () => {
    document.head.innerHTML =
      `<link rel="stylesheet" href="${absolute("/static/b.css")}">` +
      `<script src="${absolute("/static/b.js")}"></script>`;
    const { assets, loaded } = makeAssets();
    assets.seed();
    assets.loadCss([css("/static/b.css")], () => undefined);
    assets.loadJs([{ kind: "js", url: "/static/b.js" }]);
    expect(loaded).toEqual([]);
    expect(document.head.querySelectorAll("script[src]")).toHaveLength(1);
  });

  it("dedupes a manifest path with no leading slash", () => {
    document.head.innerHTML = `<link rel="stylesheet" href="${absolute("static/r.css")}">`;
    const { assets, loaded } = makeAssets();
    assets.seed();
    assets.loadCss([css("static/r.css")], () => undefined);
    expect(loaded).toEqual([]);
  });

  it("dedupes a cross-origin cdn url", () => {
    document.head.innerHTML =
      '<link rel="stylesheet" href="https://cdn.example.com/p.css">';
    const { assets, loaded } = makeAssets();
    assets.seed();
    assets.loadCss([css("https://cdn.example.com/p.css")], () => undefined);
    expect(loaded).toEqual([]);
  });

  it("dedupes a protocol-relative manifest url", () => {
    document.head.innerHTML = `<link rel="stylesheet" href="${absolute("//cdn.example.com/q.css")}">`;
    const { assets, loaded } = makeAssets();
    assets.seed();
    assets.loadCss([css("//cdn.example.com/q.css")], () => undefined);
    expect(loaded).toEqual([]);
  });

  it("resolves the key against <base href> rather than the location", () => {
    document.head.innerHTML =
      '<base href="http://cdn.example.com/build/">' +
      '<script type="module" src="http://cdn.example.com/build/app.mjs"></script>';
    const { assets } = makeAssets();
    assets.seed();
    // Keyed against the location the relative url would resolve elsewhere and a
    // second element would evaluate the module again.
    assets.loadJs([{ kind: "module", url: "app.mjs", load: "module" }]);
    expect(document.head.querySelectorAll("script[src]")).toHaveLength(1);
  });

  it("keeps a version query part of the asset identity", () => {
    const { assets, loaded } = makeAssets();
    assets.loadCss(
      [css("/static/x.css?v=1"), css("/static/x.css?v=2")],
      () => undefined,
    );
    expect(loaded).toEqual(["/static/x.css?v=1", "/static/x.css?v=2"]);
  });

  it("keeps a fragment out of the asset identity", () => {
    const { assets } = makeAssets();
    assets.loadJs([
      { kind: "module", url: "/static/m.js#zone-a", load: "module" },
      { kind: "module", url: "/static/m.js#zone-b", load: "module" },
    ]);
    expect(document.head.querySelectorAll("script[src]")).toHaveLength(1);
  });

  it("skips a stylesheet entry whose url is empty rather than keying the page", () => {
    const { assets, loaded } = makeAssets();
    // An empty url resolves to the document itself, so the entry would fetch
    // the page as a stylesheet and claim the key of a real asset addressing it.
    assets.loadCss([{ kind: "css", url: "" }, css(document.baseURI)], () => undefined);
    expect(loaded).toEqual([document.baseURI]);
  });

  it("skips a script entry whose url is empty rather than keying the page", () => {
    const { assets } = makeAssets();
    assets.loadJs([
      { kind: "js", url: "" },
      { kind: "js", url: document.baseURI },
    ]);
    const srcs = Array.from(
      document.head.querySelectorAll<HTMLScriptElement>("script[src]"),
    ).map((script) => script.getAttribute("src"));
    expect(srcs).toEqual([document.baseURI]);
  });

  it("keys an unresolvable manifest url by its raw string", () => {
    const { assets, loaded } = makeAssets();
    assets.loadCss([css("http://"), css("http://")], () => undefined);
    assets.loadJs([
      { kind: "js", url: "https://" },
      { kind: "js", url: "https://" },
    ]);
    expect(loaded).toEqual(["http://"]);
    expect(document.head.querySelectorAll("script[src]")).toHaveLength(1);
  });
});

describe("seeding across the parse window", () => {
  // The bootstrap is an inline script the server places above the co-located
  // asset tags, so seed() runs while the parser is still mid-document.
  function readyState(state: DocumentReadyState): void {
    Object.defineProperty(document, "readyState", {
      value: state,
      configurable: true,
    });
  }

  function parseTag(html: string): void {
    document.head.insertAdjacentHTML("beforeend", html);
  }

  function finishParsing(): void {
    readyState("interactive");
    document.dispatchEvent(new Event("DOMContentLoaded"));
  }

  beforeEach(() => {
    document.head.innerHTML = "";
    document.body.innerHTML = "";
    readyState("loading");
  });

  afterEach(() => {
    Reflect.deleteProperty(document, "readyState");
  });

  it("seeds a module parsed after the bootstrap ran", () => {
    const { assets } = makeAssets();
    assets.seed();
    parseTag('<script type="module" src="/static/m.js"></script>');
    finishParsing();
    assets.loadJs([{ kind: "module", url: "/static/m.js", load: "module" }]);
    expect(document.querySelectorAll('script[src="/static/m.js"]')).toHaveLength(1);
  });

  it("seeds a classic script parsed after the bootstrap ran", () => {
    const { assets } = makeAssets();
    assets.seed();
    parseTag('<script src="/static/c.js"></script>');
    finishParsing();
    assets.loadJs([{ kind: "js", url: "/static/c.js" }]);
    expect(document.querySelectorAll('script[src="/static/c.js"]')).toHaveLength(1);
  });

  it("seeds a stylesheet parsed after the bootstrap ran", () => {
    const { assets, loaded } = makeAssets();
    assets.seed();
    parseTag('<link rel="stylesheet" href="/static/late.css">');
    finishParsing();
    assets.loadCss([css("/static/late.css")], () => undefined);
    expect(loaded).toEqual([]);
  });

  it("re-seeds inside the window, before DOMContentLoaded fires", () => {
    const { assets } = makeAssets();
    assets.seed();
    parseTag('<script type="module" src="/static/w.js"></script>');
    assets.loadJs([{ kind: "module", url: "/static/w.js", load: "module" }]);
    expect(document.querySelectorAll('script[src="/static/w.js"]')).toHaveLength(1);
  });

  it("re-seeds a stylesheet inside the window, before DOMContentLoaded fires", () => {
    const { assets, loaded } = makeAssets();
    // Seeded up front, so the rescan can only come from loadCss itself: a load
    // zone fires mid-parse and its response lands before the parser is done.
    assets.seed();
    parseTag('<link rel="stylesheet" href="/static/mid.css">');
    assets.loadCss([css("/static/mid.css")], () => undefined);
    expect(loaded).toEqual([]);
  });

  it("re-seeds for a deferred script running before DOMContentLoaded", () => {
    const { assets } = makeAssets();
    assets.seed();
    parseTag('<script src="/static/d.js"></script>');
    // A deferred script runs once readyState has flipped but before the event,
    // so the pending catch-up, not the readyState, has to gate the rescan.
    readyState("interactive");
    assets.loadJs([{ kind: "js", url: "/static/d.js" }]);
    expect(document.querySelectorAll('script[src="/static/d.js"]')).toHaveLength(1);
  });

  it("stops rescanning once the document has finished parsing", () => {
    const { assets } = makeAssets();
    assets.seed();
    finishParsing();
    const scan = vi.spyOn(document, "querySelectorAll");
    assets.loadJs([{ kind: "js", url: "/static/after.js" }]);
    assets.loadCss([css("/static/after.css")], () => undefined);
    expect(scan).not.toHaveBeenCalled();
  });

  it("never watches a document that is already parsed", () => {
    readyState("complete");
    const listen = vi.spyOn(document, "addEventListener");
    const { assets } = makeAssets();
    assets.seed();
    expect(listen).not.toHaveBeenCalled();
  });

  it("watches the parse window once across repeated seeds", () => {
    const listen = vi.spyOn(document, "addEventListener");
    const { assets } = makeAssets();
    assets.seed();
    assets.seed();
    expect(
      listen.mock.calls.filter(([type]) => type === "DOMContentLoaded"),
    ).toHaveLength(1);
  });

  it("drops the catch-up listener on reset", () => {
    const listen = vi.spyOn(document, "addEventListener");
    const forget = vi.spyOn(document, "removeEventListener");
    const { assets } = makeAssets();
    assets.seed();
    assets._reset();
    const handler = listen.mock.calls.find(([type]) => type === "DOMContentLoaded")![1];
    expect(forget).toHaveBeenCalledWith("DOMContentLoaded", handler);
    parseTag('<script src="/static/dead.js"></script>');
    finishParsing();
    assets.loadJs([{ kind: "js", url: "/static/dead.js" }]);
    expect(document.querySelectorAll('script[src="/static/dead.js"]')).toHaveLength(2);
  });

  it("resets a settled registry without a listener to drop", () => {
    readyState("complete");
    const forget = vi.spyOn(document, "removeEventListener");
    const { assets } = makeAssets();
    assets.seed();
    assets._reset();
    expect(forget).not.toHaveBeenCalled();
  });
});

describe("version safeguard and reload-once", () => {
  beforeEach(() => {
    document.head.innerHTML = "";
  });

  it("learns the first version without a visit", () => {
    const navigate = vi.fn();
    const { assets } = makeAssets({ navigate });
    expect(assets.versionMismatch("v1", "/here/")).toBe(false);
    expect(navigate).not.toHaveBeenCalled();
    expect(assets.version()).toBe("v1");
  });

  it("visits once on a mismatch and degrades on the second", () => {
    const navigate = vi.fn();
    const session = memorySession();
    let made: Assets;
    {
      const m = makeAssets({ navigate, session });
      made = m.assets;
    }
    made.versionMismatch("v1", "/here/");
    expect(made.versionMismatch("v2", "/here/")).toBe(true);
    expect(navigate).toHaveBeenCalledWith("/here/");

    const second = makeAssets({ navigate, session });
    second.assets.versionMismatch("v1", "/here/");
    expect(second.assets.versionMismatch("v2", "/here/")).toBe(true);
    const err = second.dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.kind).toBe("asset");
    expect(err!.detail.url).toBe("/here/");
    expect(navigate).toHaveBeenCalledTimes(1);
  });

  it("ignores an empty version on accept, keeping the known one", () => {
    const session = memorySession();
    const { assets } = makeAssets({ session });
    assets.versionMismatch("v1", "/here/");
    assets.acceptVersion("");
    expect(assets.version()).toBe("v1");
  });

  it("clears the reload flag once a version matches", () => {
    const session = memorySession();
    const { assets } = makeAssets({ session });
    assets.versionMismatch("v1", "/here/");
    assets.versionMismatch("v2", "/here/");
    expect(session.get("next:partial:reloaded")).toBe("1");
    assets.acceptVersion("v2");
    expect(session.get("next:partial:reloaded")).toBeNull();
  });
});
