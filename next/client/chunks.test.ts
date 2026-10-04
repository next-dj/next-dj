import { beforeEach, describe, expect, it, vi } from "vitest";
import { CHUNK_TIMEOUT, createExtras, lazyChunk } from "./chunks";
import type { Extras, ExtrasFactory, ExtrasHost, ScriptsChunk } from "./chunks";

interface Dispatched {
  event: string;
  detail: Record<string, unknown>;
}

// The $chunks map the server emits for a storage that holds every chunk.
const CHUNKS = {
  scripts: "/static/next/next.scripts.min.js",
  dev: "/static/next/next.dev.min.js",
};

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

function makeLoader(withNonce = true) {
  const dispatched: Dispatched[] = [];
  const installed: ScriptsChunk[] = [];
  const mount = vi.fn();
  let seeded: Record<string, unknown> = {};
  const deps = {
    dispatch: (event: string, detail: Record<string, unknown>) =>
      void dispatched.push({ event, detail }),
    nonce: withNonce ? "boot" : undefined,
    context: () => seeded,
  };
  const chunk = lazyChunk<ExtrasFactory>(deps, "scripts");
  const extras = createExtras(
    { ...deps, mount, install: (landed) => installed.push(landed) },
    chunk,
  );
  // The runtime seeds its context before init, and the chunk registers through _land.
  const loader = {
    ready: () => extras.ready(),
    init(next: Record<string, unknown>) {
      seeded = next;
      extras.init(next);
    },
    register: (factory: ExtrasFactory) => chunk.land(factory),
  };
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
    "fetches the $chunks URL once when the payload carries %s",
    (key) => {
      const { loader } = makeLoader();
      loader.init({ [key]: [], $chunks: CHUNKS });
      loader.init({ [key]: [], $chunks: CHUNKS });
      const [tag] = chunkTags();
      expect(chunkTags()).toHaveLength(1);
      expect(tag!.src).toBe(`${location.origin}/static/next/next.scripts.min.js`);
      expect(tag!.nonce).toBe("boot");
    },
  );

  it("a page without a CSP nonce fetches the chunk without one", () => {
    const { loader } = makeLoader(false);
    loader.init({ $scripts: [], $chunks: CHUNKS });
    expect(chunkTags()[0]!.hasAttribute("nonce")).toBe(false);
  });

  it("fetches the hashed name the $chunks key gives", () => {
    const { loader } = makeLoader();
    loader.init({
      $consent: {},
      $chunks: { scripts: "/static/next/next.scripts.abc.js" },
    });
    expect(chunkTags()[0]!.getAttribute("src")).toBe(
      "/static/next/next.scripts.abc.js",
    );
  });

  it.each([undefined, "/x.js", { other: "/x.js" }, { scripts: 1 }])(
    "a payload whose $chunks is %j has nowhere to fetch from",
    async ($chunks) => {
      const { loader } = makeLoader();
      const ready = loader.ready();
      loader.init({ $scripts: [], $chunks });
      await expect(ready).rejects.toThrow("unavailable");
      expect(chunkTags()).toEqual([]);
    },
  );

  it("never guesses a chunk URL beside the runtime script", async () => {
    // The runtime reads its own script element during evaluation, so it is set first.
    const runtime = document.createElement("script");
    runtime.src = "/static/next/next.min.js";
    Object.defineProperty(document, "currentScript", {
      value: runtime,
      configurable: true,
    });
    vi.resetModules();
    try {
      await import("./next");
    } finally {
      Object.defineProperty(document, "currentScript", {
        value: null,
        configurable: true,
      });
    }
    window.Next._init({ $scripts: [] });
    await expect(window.Next.ready("scripts")).rejects.toThrow("unavailable");
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
    // The chunk's events go through the runtime's own bus.
    hosts[0]!.dispatch("next:consent", { changed: [] });
    expect(dispatched).toEqual([{ event: "next:consent", detail: { changed: [] } }]);
  });

  it("a ready after init fetches a chunk the payload did not ask for", async () => {
    const { loader } = makeLoader();
    loader.init({ page: "home", $chunks: CHUNKS });
    const ready = loader.ready();
    expect(chunkTags()).toHaveLength(1);
    loader.register(fakeChunk().factory);
    await expect(ready).resolves.toHaveProperty("consent");
    await expect(loader.ready()).resolves.toHaveProperty("scripts");
    expect(chunkTags()).toHaveLength(1);
  });

  it("a chunk landing before the payload waits for init to build and configure it", async () => {
    const { loader, installed, mount } = makeLoader();
    const { factory, calls, hosts } = fakeChunk();
    loader.register(factory);
    let settled = false;
    const ready = loader.ready().then(() => (settled = true));
    await Promise.resolve();
    expect(settled).toBe(false);
    expect(installed).toEqual([]);
    expect(chunkTags()).toEqual([]);
    loader.init({ page: "home" });
    await ready;
    expect(installed).toHaveLength(1);
    expect(hosts[0]!.mount).toBe(mount);
    expect(calls).toEqual([["configure", { page: "home" }]]);
    loader.init({ page: "next" });
    expect(calls).toHaveLength(2);
  });

  it("configures a chunk with the payload init stored last, not the one it fetched for", async () => {
    const { loader } = makeLoader();
    const { factory, calls } = fakeChunk();
    loader.init({ $scripts: [], $chunks: CHUNKS, page: "first" });
    const ready = loader.ready();
    loader.init({ $scripts: [], $chunks: CHUNKS, page: "second" });
    loader.register(factory);
    await ready;
    expect(calls).toEqual([
      ["configure", { $scripts: [], $chunks: CHUNKS, page: "second" }],
    ]);
  });

  it("takes the first chunk that lands and ignores a second copy", async () => {
    const { loader, installed } = makeLoader();
    const first = fakeChunk();
    const second = fakeChunk();
    loader.init({ $consent: {}, $chunks: CHUNKS });
    loader.register(first.factory);
    loader.register(second.factory);
    await loader.ready();
    expect(first.calls).toEqual([["configure", { $consent: {}, $chunks: CHUNKS }]]);
    expect(second.hosts).toEqual([]);
    expect(installed).toHaveLength(1);
  });

  it("a failed chunk no ready awaits is reported as an asset alone", async () => {
    const { loader, dispatched } = makeLoader();
    loader.init({ $scripts: [], $chunks: CHUNKS });
    chunkTags()[0]!.dispatchEvent(new Event("error"));
    await Promise.resolve();
    expect(dispatched.map((d) => d.detail.kind)).toEqual(["asset"]);
  });

  it("a failed chunk reports the asset, rejects ready, and lets a later ready retry", async () => {
    const { loader, dispatched } = makeLoader();
    const onDocument = vi.fn();
    document.addEventListener("partial:error", onDocument);
    loader.init({ $scripts: [], $chunks: CHUNKS });
    const pending = loader.ready();
    const failed = chunkTags()[0]!;
    failed.dispatchEvent(new Event("error"));
    expect(failed.isConnected).toBe(false);
    document.removeEventListener("partial:error", onDocument);
    await expect(pending).rejects.toThrow("unavailable");
    expect(dispatched[0]).toMatchObject({
      event: "partial:error",
      detail: { kind: "asset", url: CHUNKS.scripts },
    });
    expect(onDocument).toHaveBeenCalledOnce();
    const retry = loader.ready();
    expect(chunkTags()).toHaveLength(1);
    expect(chunkTags()[0]).not.toBe(failed);
    loader.register(fakeChunk().factory);
    await expect(retry).resolves.toHaveProperty("scripts");
  });
});

describe("a single-module chunk", () => {
  function makeDev() {
    const doc = document.implementation.createHTMLDocument("");
    const errors: Record<string, unknown>[] = [];
    let seeded: Record<string, unknown> = { $dev: true, $chunks: CHUNKS };
    const chunk = lazyChunk<string>(
      {
        dispatch: (_event, detail) => errors.push(detail),
        document: doc,
        nonce: "boot",
        context: () => seeded,
      },
      "dev",
    );
    const tags = () => Array.from(doc.head.querySelectorAll("script"));
    return {
      chunk,
      errors,
      tags,
      seed: (next: Record<string, unknown>) => (seeded = next),
    };
  }

  it("fetches its $chunks URL once, with the bootstrap nonce", async () => {
    const { chunk, tags } = makeDev();
    const first = chunk.load();
    const second = chunk.load();
    expect(tags().map((el) => [el.getAttribute("src"), el.nonce])).toEqual([
      [CHUNKS.dev, "boot"],
    ]);
    expect(chunk.get()).toBeUndefined();
    chunk.land("diagnostics");
    expect(await first).toBe("diagnostics");
    expect(await second).toBe("diagnostics");
    expect(await chunk.load()).toBe("diagnostics");
    expect(chunk.get()).toBe("diagnostics");
    expect(tags()).toHaveLength(1);
  });

  it("keeps the first copy that lands, one landing before any need included", async () => {
    const { chunk, tags } = makeDev();
    chunk.land("first");
    chunk.land("second");
    expect(await chunk.load()).toBe("first");
    expect(tags()).toEqual([]);
  });

  it("prefers its own $chunks key and ignores the others", () => {
    const { chunk, tags, seed } = makeDev();
    seed({ $chunks: { scripts: "/s.js", dev: "/static/next/next.dev.abc.js" } });
    void chunk.load();
    expect(tags()[0]!.getAttribute("src")).toBe("/static/next/next.dev.abc.js");
  });

  it("reports a failed fetch, answers undefined, and tries again on the next need", async () => {
    const { chunk, errors, tags } = makeDev();
    const pending = chunk.load();
    tags()[0]!.dispatchEvent(new Event("error"));
    expect(tags()).toEqual([]);
    expect(await pending).toBeUndefined();
    expect(errors[0]).toMatchObject({ kind: "asset", url: CHUNKS.dev });
    void chunk.load();
    expect(tags()).toHaveLength(1);
  });

  it("fails a file that loads without landing, so no need hangs on it", async () => {
    const { chunk, errors, tags } = makeDev();
    const pending = chunk.load();
    tags()[0]!.dispatchEvent(new Event("load"));
    expect(await pending).toBeUndefined();
    expect(tags()).toEqual([]);
    expect(errors[0]).toMatchObject({ kind: "asset" });
  });

  it("fails a fetch that stalls past the timeout, then tries again on the next need", async () => {
    vi.useFakeTimers();
    try {
      const { chunk, errors, tags } = makeDev();
      const pending = chunk.load();
      const stalled = tags()[0]!;
      vi.advanceTimersByTime(CHUNK_TIMEOUT - 1);
      expect(errors).toEqual([]);
      vi.advanceTimersByTime(1);
      expect(await pending).toBeUndefined();
      expect(stalled.isConnected).toBe(false);
      expect(errors).toEqual([expect.objectContaining({ kind: "asset" })]);
      // The stalled tag reporting late leaves the retry it no longer owns alone.
      const retry = chunk.load();
      stalled.dispatchEvent(new Event("error"));
      expect(errors).toHaveLength(1);
      chunk.land("diagnostics");
      vi.advanceTimersByTime(CHUNK_TIMEOUT);
      expect(await retry).toBe("diagnostics");
      expect(errors).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("fails a tag another script removed before it reported, then retries", async () => {
    vi.useFakeTimers();
    try {
      const { chunk, errors, tags } = makeDev();
      const pending = chunk.load();
      tags()[0]!.remove();
      vi.advanceTimersByTime(CHUNK_TIMEOUT);
      expect(await pending).toBeUndefined();
      expect(errors).toEqual([expect.objectContaining({ kind: "asset" })]);
      void chunk.load();
      expect(tags()).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps a landed module when its file reports the load", async () => {
    const { chunk, errors, tags } = makeDev();
    const pending = chunk.load();
    chunk.land("diagnostics");
    tags()[0]!.dispatchEvent(new Event("load"));
    expect(await pending).toBe("diagnostics");
    expect(errors).toEqual([]);
    expect(tags()).toHaveLength(1);
  });

  it("answers undefined when $chunks names no URL for it", async () => {
    const { chunk, tags, seed } = makeDev();
    seed({ $dev: true, $chunks: { scripts: CHUNKS.scripts } });
    expect(await chunk.load()).toBeUndefined();
    expect(tags()).toEqual([]);
  });
});
