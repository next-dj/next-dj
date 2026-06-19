import { beforeEach, describe, expect, it, vi } from "vitest";
import { createAssets } from "./assets";
import type { Assets, LinkLoader, SessionStore } from "./assets";
import type { Asset } from "./apply";
import type { Clock } from "./wire";

type Dispatched = { event: string; detail: Record<string, unknown> };

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

  it("seeds script srcs so the js delta skips an already-loaded script", () => {
    document.head.innerHTML = '<script src="http://x/seeded.js"></script>';
    const { assets } = makeAssets();
    assets.seed();
    assets.loadJs([{ kind: "js", url: "http://x/seeded.js" }]);
    expect(
      document.head.querySelectorAll('script[src="http://x/seeded.js"]'),
    ).toHaveLength(1);
  });

  it("gates the done callback until the last of several sheets settles", () => {
    const settlers: ((ok: boolean) => void)[] = [];
    const loadLink: LinkLoader = (_url, _nonce, done) => settlers.push(done);
    const { assets } = makeAssets({ loadLink });
    const done = vi.fn();
    assets.loadCss([css("http://x/one.css"), css("http://x/two.css")], done);
    expect(done).not.toHaveBeenCalled();
    settlers[0](true);
    expect(done).not.toHaveBeenCalled();
    settlers[1](true);
    expect(done).toHaveBeenCalledTimes(1);
  });

  it("fires one partial:error even when several sheets fail", () => {
    const settlers: ((ok: boolean) => void)[] = [];
    const loadLink: LinkLoader = (_url, _nonce, done) => settlers.push(done);
    const { assets, dispatched } = makeAssets({ loadLink });
    const done = vi.fn();
    assets.loadCss([css("http://x/a.css"), css("http://x/b.css")], done);
    settlers[0](false);
    settlers[1](false);
    expect(done).toHaveBeenCalledTimes(1);
    expect(dispatched.filter((d) => d.event === "partial:error")).toHaveLength(1);
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
    expect(second.dispatched.some((d) => d.event === "partial:error")).toBe(true);
    expect(navigate).toHaveBeenCalledTimes(1);
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
