import { beforeEach, describe, expect, it, vi } from "vitest";
import { createExtras, lazyChunk } from "./chunks";
import type { Extras, ExtrasFactory, ExtrasHost, ScriptsChunk } from "./chunks";

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
  withNonce = true,
) {
  const dispatched: Dispatched[] = [];
  const installed: ScriptsChunk[] = [];
  const mount = vi.fn();
  let seeded: Record<string, unknown> = {};
  const deps = {
    dispatch: (event: string, detail: Record<string, unknown>) =>
      void dispatched.push({ event, detail }),
    runtime,
    nonce: withNonce ? "boot" : undefined,
    context: () => seeded,
  };
  const chunk = lazyChunk<ExtrasFactory>(deps, "scripts");
  const extras = createExtras(
    { ...deps, mount, install: (landed) => installed.push(landed) },
    chunk,
  );
  // The runtime seeds its context before init, and the chunk lands through _land.
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
    const { loader } = makeLoader(runtimeScript("/static/next/next.min.js"), false);
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

  it("takes the first chunk that lands and ignores a second copy", async () => {
    const { loader, installed } = makeLoader();
    const first = fakeChunk();
    const second = fakeChunk();
    loader.init({ $consent: {} });
    loader.register(first.factory);
    loader.register(second.factory);
    await loader.ready();
    expect(first.calls).toEqual([["configure", { $consent: {} }]]);
    expect(second.hosts).toEqual([]);
    expect(installed).toHaveLength(1);
  });

  it("a failed chunk no ready awaits is reported as an asset alone", async () => {
    const { loader, dispatched } = makeLoader();
    loader.init({ $scripts: [] });
    chunkTags()[0]!.dispatchEvent(new Event("error"));
    await Promise.resolve();
    expect(dispatched.map((d) => d.detail.kind)).toEqual(["asset"]);
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

describe("a single-module chunk", () => {
  function makeDev(
    runtime: Element | null = runtimeScript("/static/next/next.min.js"),
  ) {
    const doc = document.implementation.createHTMLDocument("");
    const errors: Record<string, unknown>[] = [];
    let seeded: Record<string, unknown> = { $dev: true };
    const chunk = lazyChunk<string>(
      {
        dispatch: (_event, detail) => errors.push(detail),
        document: doc,
        runtime,
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

  it("fetches the runtime's sibling once, with the bootstrap nonce", async () => {
    const { chunk, tags } = makeDev();
    const first = chunk.load();
    const second = chunk.load();
    expect(tags().map((el) => [el.src, el.nonce])).toEqual([
      [`${location.origin}/static/next/next.dev.min.js`, "boot"],
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
    expect(errors[0]).toMatchObject({
      kind: "asset",
      url: `${location.origin}/static/next/next.dev.min.js`,
    });
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

  it("keeps a landed module when its file reports the load", async () => {
    const { chunk, errors, tags } = makeDev();
    const pending = chunk.load();
    chunk.land("diagnostics");
    tags()[0]!.dispatchEvent(new Event("load"));
    expect(await pending).toBe("diagnostics");
    expect(errors).toEqual([]);
    expect(tags()).toHaveLength(1);
  });

  it("answers undefined when there is nowhere to fetch from", async () => {
    const { chunk, tags } = makeDev(null);
    expect(await chunk.load()).toBeUndefined();
    expect(tags()).toEqual([]);
  });
});
