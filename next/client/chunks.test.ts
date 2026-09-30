import { beforeEach, describe, expect, it, vi } from "vitest";
import { chunkLoader, createExtras } from "./chunks";
import type { Extras, ExtrasHost, ScriptsChunk } from "./chunks";

interface Dispatched {
  event: string;
  detail: Record<string, unknown>;
}

function runtimeScript(src: string): HTMLScriptElement {
  const el = document.createElement("script");
  if (src !== "") el.src = src;
  return el;
}

function chunkTags(): HTMLScriptElement[] {
  return Array.from(document.head.querySelectorAll("script"));
}

// A chunk double that logs every configure it receives, in order.
function fakeChunk() {
  const calls: unknown[][] = [];
  const hosts: ExtrasHost[] = [];
  const factory = (host: ExtrasHost): Extras => {
    hosts.push(host);
    return {
      consent: {
        get: () => ({ necessary: true }),
        decided: () => false,
        update: () => undefined,
        acceptAll: () => undefined,
        rejectAll: () => undefined,
        _configure: () => undefined,
        _announce: () => undefined,
        _reveal: () => undefined,
      },
      scripts: {
        load: async () => undefined,
        status: () => undefined,
        _configure: () => undefined,
        _refresh: () => undefined,
      },
      configure: (context) => calls.push(["configure", context]),
    };
  };
  return { factory, calls, hosts };
}

function makeLoader(
  runtime: Element | null = runtimeScript("/static/next/next.min.js"),
) {
  const dispatched: Dispatched[] = [];
  const installed: ScriptsChunk[] = [];
  const mount = vi.fn();
  const loader = createExtras({
    dispatch: (event, detail) => dispatched.push({ event, detail }),
    runtime,
    nonce: "boot",
    mount,
    install: (chunk) => installed.push(chunk),
  });
  return { loader, dispatched, installed, mount };
}

describe("fetching the scripts chunk", () => {
  beforeEach(() => {
    document.head.innerHTML = "";
  });

  it("leaves a payload that needs no chunk without a request", () => {
    const { loader } = makeLoader();
    loader.init({ page: "home" });
    expect(chunkTags()).toEqual([]);
  });

  it.each(["$scripts", "$consent"])(
    "fetches the runtime's sibling once when the payload carries %s",
    (key) => {
      const { loader } = makeLoader();
      loader.init({ [key]: [] });
      loader.init({ [key]: [] });
      const [tag] = chunkTags();
      expect(chunkTags()).toHaveLength(1);
      expect(tag!.src).toBe(`${location.origin}/static/next/next.scripts.min.js`);
      expect(tag!.nonce).toBe("boot");
    },
  );

  it("a page without a CSP nonce fetches the chunk without one", () => {
    const loader = createExtras({
      dispatch: () => undefined,
      runtime: runtimeScript("/static/next/next.min.js"),
      nonce: undefined,
      mount: () => undefined,
      install: () => undefined,
    });
    loader.init({ $scripts: [] });
    expect(chunkTags()[0]!.hasAttribute("nonce")).toBe(false);
  });

  it("prefers the $chunks payload key over the sibling", () => {
    const { loader } = makeLoader();
    loader.init({
      $consent: {},
      $chunks: { scripts: "/static/next/next.scripts.abc.js" },
    });
    expect(chunkTags()[0]!.getAttribute("src")).toBe(
      "/static/next/next.scripts.abc.js",
    );
  });

  it("falls back to the sibling when $chunks names no scripts chunk", () => {
    const { loader } = makeLoader();
    loader.init({ $consent: {}, $chunks: { other: "/x.js" } });
    expect(chunkTags()[0]!.src).toBe(
      `${location.origin}/static/next/next.scripts.min.js`,
    );
  });

  it("an inline bootstrap without $chunks has nowhere to fetch from", async () => {
    for (const runtime of [null, runtimeScript(""), document.createElement("div")]) {
      const { loader } = makeLoader(runtime);
      const ready = loader.ready();
      loader.init({ $scripts: [] });
      await expect(ready).rejects.toThrow("unavailable");
    }
    expect(chunkTags()).toEqual([]);
  });
});

describe("waiting for the scripts chunk", () => {
  beforeEach(() => {
    document.head.innerHTML = "";
  });

  it("a ready before init waits for the payload to fetch it, then resolves", async () => {
    const { loader, installed, dispatched } = makeLoader();
    const ready = loader.ready();
    expect(chunkTags()).toEqual([]);
    loader.init({ $chunks: { scripts: "/static/next/next.scripts.abc.js" } });
    expect(chunkTags()[0]!.getAttribute("src")).toBe(
      "/static/next/next.scripts.abc.js",
    );
    const { factory, calls, hosts } = fakeChunk();
    loader.register(factory);
    const chunk = await ready;
    expect(installed).toEqual([chunk]);
    expect(calls).toEqual([
      ["configure", { $chunks: { scripts: "/static/next/next.scripts.abc.js" } }],
    ]);
    expect(hosts[0]!.nonce).toBe("boot");
    // The chunk's events ride the runtime's own bus.
    hosts[0]!.dispatch("next:consent", { changed: [] });
    expect(dispatched).toEqual([{ event: "next:consent", detail: { changed: [] } }]);
  });

  it("a ready after init fetches a chunk the payload did not ask for", async () => {
    const { loader } = makeLoader();
    loader.init({ page: "home" });
    const ready = loader.ready();
    expect(chunkTags()).toHaveLength(1);
    loader.register(fakeChunk().factory);
    await expect(ready).resolves.toHaveProperty("consent");
    await expect(loader.ready()).resolves.toHaveProperty("scripts");
    expect(chunkTags()).toHaveLength(1);
  });

  it("a chunk landing before the payload is installed at once and configured on init", async () => {
    const { loader, installed, mount } = makeLoader();
    const { factory, calls, hosts } = fakeChunk();
    loader.register(factory);
    expect(installed).toHaveLength(1);
    expect(hosts[0]!.mount).toBe(mount);
    let settled = false;
    const ready = loader.ready().then(() => (settled = true));
    await Promise.resolve();
    expect(settled).toBe(false);
    expect(chunkTags()).toEqual([]);
    loader.init({ page: "home" });
    await ready;
    expect(calls).toEqual([["configure", { page: "home" }]]);
    loader.init({ page: "next" });
    expect(calls).toHaveLength(2);
  });

  it("takes the first chunk that registers and ignores a second copy", () => {
    const { loader, installed } = makeLoader();
    const first = fakeChunk();
    const second = fakeChunk();
    loader.init({ $consent: {} });
    loader.register(first.factory);
    loader.register(second.factory);
    expect(first.calls).toEqual([["configure", { $consent: {} }]]);
    expect(second.hosts).toEqual([]);
    expect(installed).toHaveLength(1);
  });

  it("a failed chunk reports the asset, rejects ready, and lets a later ready retry", async () => {
    const { loader, dispatched } = makeLoader();
    const onDocument = vi.fn();
    document.addEventListener("partial:error", onDocument);
    loader.init({ $scripts: [] });
    const pending = loader.ready();
    const failed = chunkTags()[0]!;
    failed.dispatchEvent(new Event("error"));
    expect(failed.isConnected).toBe(false);
    document.removeEventListener("partial:error", onDocument);
    await expect(pending).rejects.toThrow("unavailable");
    expect(dispatched[0]).toMatchObject({
      event: "partial:error",
      detail: {
        kind: "asset",
        url: `${location.origin}/static/next/next.scripts.min.js`,
      },
    });
    expect(onDocument).toHaveBeenCalledOnce();
    const retry = loader.ready();
    expect(chunkTags()).toHaveLength(1);
    expect(chunkTags()[0]).not.toBe(failed);
    loader.register(fakeChunk().factory);
    await expect(retry).resolves.toHaveProperty("scripts");
  });
});

describe("the dev chunk fetch", () => {
  function makeDev(
    runtime: Element | null = runtimeScript("/static/next/next.min.js"),
  ) {
    const doc = document.implementation.createHTMLDocument("");
    const failed = vi.fn();
    const errors: Record<string, unknown>[] = [];
    const load = chunkLoader(
      {
        dispatch: (_event, detail) => errors.push(detail),
        document: doc,
        runtime,
        nonce: "boot",
      },
      "dev",
      "next.dev.min.js",
      failed,
    );
    const tags = () => Array.from(doc.head.querySelectorAll("script"));
    return { load, failed, errors, tags };
  }

  it("fetches the runtime's dev sibling once, with the bootstrap nonce", () => {
    const { load, tags } = makeDev();
    load({ $dev: true });
    load({ $dev: true });
    expect(tags().map((el) => [el.src, el.nonce])).toEqual([
      [`${location.origin}/static/next/next.dev.min.js`, "boot"],
    ]);
  });

  it("prefers the $chunks.dev key and ignores the scripts one", () => {
    const { load, tags } = makeDev();
    load({ $chunks: { scripts: "/s.js", dev: "/static/next/next.dev.abc.js" } });
    expect(tags()[0]!.getAttribute("src")).toBe("/static/next/next.dev.abc.js");
  });

  it("reports a failed fetch and tries again on the next call", () => {
    const { load, failed, errors, tags } = makeDev();
    load(undefined);
    tags()[0]!.dispatchEvent(new Event("error"));
    expect(tags()).toEqual([]);
    expect(failed).toHaveBeenCalledOnce();
    expect(errors[0]).toMatchObject({
      kind: "asset",
      url: `${location.origin}/static/next/next.dev.min.js`,
    });
    load(undefined);
    expect(tags()).toHaveLength(1);
  });

  it("calls failed when there is nowhere to fetch from", () => {
    const { load, failed, tags } = makeDev(null);
    load({ $dev: true });
    expect(failed).toHaveBeenCalledOnce();
    expect(tags()).toEqual([]);
  });
});
