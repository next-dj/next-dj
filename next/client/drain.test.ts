import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Applier } from "./apply";
import type { AssetBridge, BeforeApply, Envelope } from "./apply";
import { createAssets } from "./assets";
import { drain } from "./drain";
import type { DrainHost, Fill } from "./drain";
import { isBusy, markBusy, stamp, stamped } from "./protocol";
import { fill, manualFrames } from "./test-doubles";
import { Wire } from "./wire";

interface Dispatched {
  event: string;
  detail: Record<string, unknown>;
}

let painter: ReturnType<typeof manualFrames>;
let listening = new AbortController();
let open = 0;

beforeEach(() => {
  listening = new AbortController();
  open = 0;
  vi.spyOn(document, "readyState", "get").mockReturnValue("loading");
  painter = manualFrames();
  delete window.$nx;
  document.body.innerHTML = "";
  document.title = "";
});

afterEach(() => {
  listening.abort();
  vi.unstubAllGlobals();
});

function frame(): void {
  painter.paint();
}

function wrapperHtml(id: string): string {
  return (
    `<div id="${id}" data-next-boundary="${id}" data-next-boundary-key="k-${id}" ` +
    `data-next-state="loading" aria-busy="true"><p>loading ${id}</p></div>`
  );
}

function shell(...ids: string[]): void {
  document.body.innerHTML = ids.map(wrapperHtml).join("");
}

function push(...entries: (Fill | 0)[]): void {
  (window.$nx ??= []).push(...entries);
}

function text(id: string): string | null {
  return document.getElementById(id)!.textContent;
}

function marks(id: string): (string | null | boolean)[] {
  const el = document.getElementById(id)!;
  return [
    el.getAttribute("data-next-state"),
    el.getAttribute("data-next-error"),
    el.getAttribute("aria-busy"),
    el.hasAttribute("data-next-busy"),
  ];
}

const READY = ["ready", null, null, false];
const LOADING = ["loading", null, "true", false];

function harness(options: { assets?: AssetBridge } = {}) {
  const dispatched: Dispatched[] = [];
  const dispatch = (event: string, detail: Record<string, unknown>): void => {
    dispatched.push({ event, detail });
  };
  const applier = new Applier({
    dispatch,
    mergeContext: () => undefined,
    document,
    ...(options.assets === undefined ? {} : { assets: () => options.assets! }),
  });
  const host = {
    partial: { apply: (raw, applying) => applier.apply(raw, applying) },
    mark: markBusy,
    busy: isBusy,
    stamped,
    fire: dispatch,
    fallbacks: new WeakMap(),
  } as DrainHost;
  const boundaries = {
    drain: () => drain(host, () => open > 0, { signal: listening.signal }),
  };
  const events = (name: string): Record<string, unknown>[] =>
    dispatched.filter((d) => d.event === name).map((d) => d.detail);
  return { boundaries, events, host };
}

describe("draining $nx", () => {
  it("applies the chunks pushed before the runtime in their push order", () => {
    shell("b-1", "b-2");
    push(fill("b-1", "<i>one</i>"), fill("b-2", "<i>two</i>"));
    const { boundaries, events } = harness();
    boundaries.drain();
    expect(text("b-1")).toBe("loading b-1");
    expect(painter.queued).toHaveLength(1);
    frame();
    expect([text("b-1"), text("b-2")]).toEqual(["one", "two"]);
    const [before] = events("partial:before-apply");
    const ops = (before as unknown as BeforeApply).envelope.ops;
    expect(ops.map((op) => (op as { target: { boundary: string } }).target)).toEqual([
      { boundary: "b-1" },
      { boundary: "b-2" },
    ]);
  });

  it("applies a chunk pushed after the runtime as it arrives", () => {
    shell("b-1", "b-2");
    const { boundaries } = harness();
    boundaries.drain();
    push(fill("b-1", "one"));
    frame();
    expect(text("b-1")).toBe("one");
    push(fill("b-2", "two"));
    expect(text("b-2")).toBe("loading b-2");
    frame();
    expect(text("b-2")).toBe("two");
    expect(window.$nx).toHaveLength(0);
  });

  it("takes the chunks on either side of the runtime", () => {
    shell("b-1", "b-2");
    push(fill("b-1", "one"));
    const { boundaries, events } = harness();
    boundaries.drain();
    push(fill("b-2", "two"));
    frame();
    expect([text("b-1"), text("b-2")]).toEqual(["one", "two"]);
    expect(events("partial:before-apply")).toHaveLength(1);
  });

  it("settles a stream whose end marker came before the runtime", () => {
    shell("b-1", "b-2");
    push(fill("b-1", "one"), 0);
    const { boundaries } = harness();
    boundaries.drain();
    expect(marks("b-2")).toEqual(LOADING);
    frame();
    expect(marks("b-1")).toEqual(READY);
    expect(marks("b-2")).toEqual(["error", "incomplete", null, false]);
  });
});

describe("applying a chunk", () => {
  it("swaps a ready chunk in and marks its wrapper ready", () => {
    shell("b-1");
    const { boundaries } = harness();
    const mounted: Element[] = [];
    const onMount = (event: Event): void => void mounted.push(event.target as Element);
    document.addEventListener("next:mounted", onMount);
    boundaries.drain();
    push(fill("b-1", "<section>done</section>"));
    frame();
    document.removeEventListener("next:mounted", onMount);
    expect(document.getElementById("b-1")!.innerHTML).toBe("<section>done</section>");
    expect(marks("b-1")).toEqual(READY);
    expect(mounted).toEqual([document.getElementById("b-1")]);
  });

  it("keeps the loading content its first chunk replaces on the wrapper", () => {
    shell("b-1");
    const { boundaries, host } = harness();
    boundaries.drain();
    push(fill("b-1", "first"));
    frame();
    const wrapper = document.getElementById("b-1")!;
    expect(host.fallbacks.get(wrapper)).toBe("<p>loading b-1</p>");
    push(fill("b-1", "second"));
    frame();
    expect(host.fallbacks.get(wrapper)).toBe("<p>loading b-1</p>");
  });

  it("marks an error chunk with its kind", () => {
    shell("b-1");
    const { boundaries } = harness();
    boundaries.drain();
    push(fill("b-1", "<p>failed</p>", { s: "error", k: "timeout" }));
    frame();
    expect(text("b-1")).toBe("failed");
    expect(marks("b-1")).toEqual(["error", "timeout", null, false]);
  });

  it("writes the error digest of an error chunk and clears one it lacks", () => {
    shell("b-1", "b-2");
    document.getElementById("b-2")!.setAttribute("data-next-error-digest", "old");
    const { boundaries } = harness();
    boundaries.drain();
    push(
      fill("b-1", "<p>failed</p>", { s: "error", g: "c0ffee" }),
      fill("b-2", "<p>failed</p>", { s: "error" }),
    );
    frame();
    const digest = (id: string): string | null =>
      document.getElementById(id)!.getAttribute("data-next-error-digest");
    expect([digest("b-1"), digest("b-2")]).toEqual(["c0ffee", null]);
  });

  it("clears an error digest once a ready chunk fills the wrapper", () => {
    shell("b-1");
    document.getElementById("b-1")!.setAttribute("data-next-error-digest", "old");
    const { boundaries } = harness();
    boundaries.drain();
    push(fill("b-1", "fine", { g: "ignored" }));
    frame();
    expect(document.getElementById("b-1")!.hasAttribute("data-next-error-digest")).toBe(
      false,
    );
  });

  it("keeps a loading copy the wrapper already carries", () => {
    shell("b-1");
    const wrapper = document.getElementById("b-1")!;
    const { boundaries, host } = harness();
    host.fallbacks.set(wrapper, "<p>kept</p>");
    boundaries.drain();
    push(fill("b-1", "first"));
    frame();
    expect(host.fallbacks.get(wrapper)).toBe("<p>kept</p>");
  });

  it("applies without waiting for a paint while the tab is hidden", async () => {
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    shell("b-1");
    const { boundaries } = harness();
    boundaries.drain();
    push(fill("b-1", "one"));
    expect(text("b-1")).toBe("loading b-1");
    await Promise.resolve();
    expect(painter.queued).toHaveLength(0);
    expect(text("b-1")).toBe("one");
  });

  it("reads an error chunk without a kind as a plain error", () => {
    shell("b-1");
    const { boundaries } = harness();
    boundaries.drain();
    push(fill("b-1", "<p>failed</p>", { s: "error" }));
    frame();
    expect(marks("b-1")).toEqual(["error", "error", null, false]);
  });

  it("replaces an adopted root with the element the chunk carries", () => {
    document.body.innerHTML =
      '<template id="b-1" data-next-boundary="b-1" data-next-state="loading" ' +
      'aria-busy="true"></template>';
    const { boundaries } = harness();
    boundaries.drain();
    push({
      z: "b-1",
      s: "ready",
      e: {
        version: "9f2c",
        ops: [
          {
            op: "replace",
            target: { boundary: "b-1" },
            html: '<article id="b-1" data-next-boundary="b-1" data-next-state="ready">x</article>',
          },
        ],
        assets: [],
      },
    });
    frame();
    const root = document.getElementById("b-1")!;
    expect(root.tagName).toBe("ARTICLE");
    expect(marks("b-1")).toEqual(READY);
    expect(document.querySelector("template")).toBeNull();
  });

  it("applies the meta op of a page boundary", () => {
    shell("b-1");
    const { boundaries } = harness();
    boundaries.drain();
    const page = fill("b-1", "page");
    const ops = [...(page.e.ops as unknown[]), { op: "meta", title: "Found" }];
    push({ ...page, e: { ...page.e, ops } });
    frame();
    expect(document.title).toBe("Found");
  });

  it("applies chunks in whatever order the server finished them", () => {
    shell("b-1", "b-2", "b-3");
    const { boundaries } = harness();
    boundaries.drain();
    push(fill("b-3", "three"));
    frame();
    push(fill("b-1", "one"));
    frame();
    expect([text("b-1"), text("b-2"), text("b-3")]).toEqual([
      "one",
      "loading b-2",
      "three",
    ]);
    push(fill("b-2", "two"));
    frame();
    expect(text("b-2")).toBe("two");
  });

  it("drops a chunk whose target is not in the document without a word", () => {
    shell("b-1");
    const { boundaries, events } = harness();
    boundaries.drain();
    push(fill("b-9", "lost", { p: "k-b-8" }), fill("b-8", "lost"));
    frame();
    expect(events("partial:before-apply")).toEqual([]);
    expect(events("partial:settled")).toEqual([]);
    expect(marks("b-1")).toEqual(LOADING);
  });

  it("applies a chunk at once when its target exists even if it names a parent", () => {
    shell("b-1");
    const { boundaries } = harness();
    boundaries.drain();
    push(fill("b-1", "nested", { p: "k-b-0" }));
    frame();
    expect(text("b-1")).toBe("nested");
  });

  it("runs the chunk assets while the streamed document is still parsing", () => {
    shell("b-1");
    document.documentElement.setAttribute("data-next-streamed", "");
    vi.spyOn(document, "readyState", "get").mockReturnValue("loading");
    const loaded: string[] = [];
    const assets = createAssets({
      dispatch: () => undefined,
      document,
      loadLink: (url, _nonce, done) => {
        loaded.push(url);
        done(true);
      },
      navigate: () => undefined,
    });
    assets.seed();
    const { boundaries } = harness({ assets });
    boundaries.drain();
    const streamed = fill("b-1", "styled");
    push({
      ...streamed,
      e: {
        ...streamed.e,
        assets: [
          { kind: "css", url: "/static/chunk.css", load: "link" },
          { kind: "js", url: "/static/chunk.js", load: "script" },
        ],
      },
    });
    frame();
    expect(loaded).toEqual(["/static/chunk.css"]);
    expect(document.querySelectorAll('script[src="/static/chunk.js"]')).toHaveLength(1);
    expect(text("b-1")).toBe("styled");
    document.documentElement.removeAttribute("data-next-streamed");
    assets._reset();
    document.head.innerHTML = "";
  });
});

describe("the generation guard", () => {
  function wire(fetch: () => Promise<Response>, touched: Element[] = []): Wire {
    return new Wire({
      fetch,
      dispatch: () => undefined,
      onEnvelope: (raw) =>
        Promise.resolve({ envelope: raw as Envelope, ok: true, nodes: touched }),
      csrf: { current: () => undefined, ensure: () => Promise.resolve(undefined) },
      timeout: () => 0,
      resolveZone: (name, root) => root.querySelector(`[data-next-zone="${name}"]`),
    });
  }

  const answered = (): Promise<Response> =>
    Promise.resolve(
      new Response('{"version":"","ops":[]}', {
        headers: { "content-type": "application/vnd.next.patches+json" },
      }),
    );

  it("drops a chunk inside a zone a failed reload claimed", async () => {
    document.body.innerHTML = `<div data-next-zone="z">${wrapperHtml("b-1")}</div>`;
    const { boundaries, events } = harness();
    boundaries.drain();
    const failing = wire(() => Promise.reject(new TypeError("offline")));
    await failing.fetch({ url: "/p/", zone: "z" });
    push(fill("b-1", "stale"));
    frame();
    expect(text("b-1")).toBe("loading b-1");
    expect(events("partial:settled")).toEqual([
      { zones: [], boundaries: ["b-1"], status: "dropped" },
    ]);
  });

  it("applies a chunk inside a zone whose poll is in flight", () => {
    document.body.innerHTML = `<div data-next-zone="z">${wrapperHtml("b-1")}</div>`;
    const { boundaries } = harness();
    boundaries.drain();
    const pending = wire(() => new Promise<Response>(() => undefined));
    void pending.fetch({ url: "/p/", zone: "z", background: true });
    push(fill("b-1", "fresh"));
    frame();
    expect(text("b-1")).toBe("fresh");
  });

  it("drops a chunk under a zone a poll reply touched", async () => {
    document.body.innerHTML = `<div data-next-zone="z">${wrapperHtml("b-1")}</div>`;
    const { boundaries } = harness();
    boundaries.drain();
    const zone = document.querySelector("[data-next-zone]")!;
    await wire(answered, [zone]).fetch({ url: "/p/", zone: "z", background: true });
    push(fill("b-1", "stale"));
    frame();
    expect(text("b-1")).toBe("loading b-1");
  });

  it("drops a chunk for a wrapper a zone reload put in place of the first", async () => {
    document.body.innerHTML = `<div data-next-zone="z">${wrapperHtml("b-1")}</div>`;
    const { boundaries, events } = harness();
    boundaries.drain();
    await wire(answered).fetch({ url: "/p/", zone: "z" });
    const first = document.getElementById("b-1")!;
    first.outerHTML = wrapperHtml("b-1");
    expect(document.getElementById("b-1")).not.toBe(first);
    push(fill("b-1", "stale"));
    frame();
    expect(text("b-1")).toBe("loading b-1");
    expect(events("partial:settled")).toEqual([
      { zones: [], boundaries: ["b-1"], status: "dropped" },
    ]);
  });

  it("drops a chunk for a boundary the client reloaded before it landed", async () => {
    shell("b-1");
    const { boundaries, events } = harness();
    boundaries.drain();
    const reloaded = wire(answered);
    await reloaded.fetch({ url: "/p/", boundaries: [document.getElementById("b-1")!] });
    push(fill("b-1", "stale"));
    frame();
    expect(text("b-1")).toBe("loading b-1");
    expect(events("partial:before-apply")).toEqual([]);
    expect(events("partial:settled")).toEqual([
      { zones: [], boundaries: ["b-1"], status: "dropped" },
    ]);
  });

  it("drops a chunk inside a zone whose reload is in flight", () => {
    document.body.innerHTML = `<div data-next-zone="z">${wrapperHtml("b-1")}</div>`;
    const { boundaries, events } = harness();
    boundaries.drain();
    const pending = wire(() => new Promise<Response>(() => undefined));
    void pending.fetch({ url: "/p/", zone: "z" });
    push(fill("b-1", "stale"));
    frame();
    expect(text("b-1")).toBe("loading b-1");
    expect(events("partial:settled")).toEqual([
      { zones: [], boundaries: ["b-1"], status: "dropped" },
    ]);
  });

  it("marks a boundary incomplete once a reload holding it at the end failed", async () => {
    document.body.innerHTML = `<div data-next-zone="z">${wrapperHtml("b-1")}</div>`;
    const { boundaries } = harness();
    boundaries.drain();
    let fail: (error: Error) => void = () => undefined;
    const pending = wire(() => new Promise<Response>((_, reject) => (fail = reject)));
    const sent = pending.fetch({ url: "/p/", zone: "z" });
    push(0);
    expect(marks("b-1")).toEqual(LOADING);
    for (let i = 0; i < 10; i++) await Promise.resolve();
    fail(new TypeError("offline"));
    await sent;
    expect(marks("b-1")).toEqual(LOADING);
    document.dispatchEvent(
      new CustomEvent("partial:settled", {
        detail: { zones: ["z"], boundaries: [], status: "error" },
      }),
    );
    expect(marks("b-1")).toEqual(["error", "incomplete", null, false]);
  });

  it("drops a second chunk for a boundary already in the batch", () => {
    shell("b-1");
    const { boundaries, events } = harness();
    boundaries.drain();
    push(fill("b-1", "first"), fill("b-1", "second"));
    frame();
    expect(text("b-1")).toBe("first");
    expect(events("partial:settled")).toEqual([
      { zones: [], boundaries: ["b-1"], status: "dropped" },
      { zones: [], boundaries: ["b-1"], status: "ok" },
    ]);
  });
});

describe("frame batching", () => {
  it("applies the chunks of one task in one frame with one before-apply", () => {
    shell("b-1", "b-2", "b-3");
    const { boundaries, events } = harness();
    boundaries.drain();
    push(fill("b-1", "one"));
    push(fill("b-2", "two"));
    push(fill("b-3", "three"));
    expect(painter.queued).toHaveLength(1);
    frame();
    const before = events("partial:before-apply");
    expect(before).toHaveLength(1);
    expect(before[0]!.boundaries).toEqual(
      ["b-1", "b-2", "b-3"].map((id) => document.getElementById(id)),
    );
    expect(events("partial:applied")).toHaveLength(1);
    expect(events("partial:settled")).toEqual([
      { zones: [], boundaries: ["b-1", "b-2", "b-3"], status: "ok" },
    ]);
  });

  it("sizes each batch to the time the last one took and yields past the budget", () => {
    const ids = Array.from({ length: 20 }, (_, index) => `b-${index}`);
    shell(...ids);
    let now = 0;
    vi.spyOn(performance, "now").mockImplementation(() => now);
    const { boundaries, events } = harness();
    // Each fill costs two milliseconds to apply.
    document.addEventListener(
      "partial:applied",
      (event) =>
        (now += 2 * (event as CustomEvent<{ nodes: Element[] }>).detail.nodes.length),
      { signal: listening.signal },
    );
    boundaries.drain();
    push(...ids.map((id) => fill(id, id)), 0);
    const sizes: number[][] = [];
    while (painter.queued.length > 0) {
      const before = events("partial:settled").length;
      frame();
      sizes.push(
        events("partial:settled")
          .slice(before)
          .map((detail) => (detail.boundaries as string[]).length),
      );
    }
    expect(sizes).toEqual([[8], [4], [4], [4]]);
    expect(ids.map(text)).toEqual(ids);
    expect(ids.map(marks).every((mark) => mark[0] === "ready")).toBe(true);
  });

  it("holds the whole batch for a listener that calls hold", () => {
    shell("b-1", "b-2");
    const { boundaries, events } = harness();
    let resume: () => void = () => undefined;
    document.addEventListener(
      "partial:before-apply",
      (event) => (resume = (event as CustomEvent<BeforeApply>).detail.hold()),
      { once: true },
    );
    boundaries.drain();
    push(fill("b-1", "one"), fill("b-2", "two"));
    frame();
    expect(text("b-1")).toBe("loading b-1");
    expect(marks("b-1")).toEqual(["loading", null, "true", true]);
    expect(events("partial:settled")).toEqual([]);
    resume();
    expect([text("b-1"), text("b-2")]).toEqual(["one", "two"]);
    expect([marks("b-1"), marks("b-2")]).toEqual([READY, READY]);
    expect(events("partial:before-apply")).toHaveLength(1);
  });

  it("keeps a held batch out of the incomplete sweep", () => {
    shell("b-1", "b-2");
    const { boundaries } = harness();
    let resume: () => void = () => undefined;
    document.addEventListener(
      "partial:before-apply",
      (event) => (resume = (event as CustomEvent<BeforeApply>).detail.hold()),
      { once: true },
    );
    boundaries.drain();
    push(fill("b-1", "one"), 0);
    frame();
    expect(marks("b-1")).toEqual(["loading", null, "true", true]);
    expect(marks("b-2")).toEqual(["error", "incomplete", null, false]);
    resume();
    expect(marks("b-1")).toEqual(READY);
  });
});

describe("the end of the stream", () => {
  it("marks the boundaries left loading at the end marker as incomplete", () => {
    shell("b-1", "b-2", "b-3");
    document.getElementById("b-3")!.setAttribute("data-next-state", "idle");
    const { boundaries } = harness();
    boundaries.drain();
    push(fill("b-1", "one"), 0);
    expect(marks("b-2")).toEqual(LOADING);
    frame();
    expect(marks("b-1")).toEqual(READY);
    expect(marks("b-2")).toEqual(["error", "incomplete", null, false]);
    expect(marks("b-3")).toEqual(["idle", null, "true", false]);
  });

  it("settles on the window load when the end marker never came", () => {
    shell("b-1", "b-2");
    const { boundaries } = harness();
    boundaries.drain();
    push(fill("b-1", "one"));
    window.dispatchEvent(new Event("load"));
    expect(marks("b-2")).toEqual(LOADING);
    frame();
    expect(marks("b-1")).toEqual(READY);
    expect(marks("b-2")).toEqual(["error", "incomplete", null, false]);
  });

  it("settles at once when the window load came before the bundle", () => {
    shell("b-1", "b-2");
    vi.spyOn(document, "readyState", "get").mockReturnValue("complete");
    push(fill("b-1", "one"));
    const { boundaries } = harness();
    boundaries.drain();
    expect(marks("b-2")).toEqual(LOADING);
    frame();
    expect(marks("b-1")).toEqual(READY);
    expect(marks("b-2")).toEqual(["error", "incomplete", null, false]);
  });

  it("settles an end marker the bundle finds queued after the window load", () => {
    shell("b-1");
    vi.spyOn(document, "readyState", "get").mockReturnValue("complete");
    push(0);
    const { boundaries } = harness();
    boundaries.drain();
    expect(marks("b-1")).toEqual(["error", "incomplete", null, false]);
  });

  it("holds the sweep while a partial stream is open", () => {
    shell("b-1");
    open = 1;
    const { boundaries } = harness();
    boundaries.drain();
    push(0);
    expect(marks("b-1")).toEqual(LOADING);
    open = 0;
    document.dispatchEvent(
      new CustomEvent("partial:settled", {
        detail: { zones: ["z"], boundaries: [], status: "ok" },
      }),
    );
    expect(marks("b-1")).toEqual(["error", "incomplete", null, false]);
  });

  it("applies a late chunk and clears the incomplete mark", () => {
    shell("b-1");
    const { boundaries } = harness();
    boundaries.drain();
    push(0);
    expect(marks("b-1")).toEqual(["error", "incomplete", null, false]);
    push(fill("b-1", "late"));
    frame();
    expect(text("b-1")).toBe("late");
    expect(marks("b-1")).toEqual(READY);
  });
});

describe("partial:settled for a batch", () => {
  it("reports an op that failed as an error", () => {
    shell("b-1");
    const { boundaries, events } = harness();
    boundaries.drain();
    const broken = fill("b-1", "one");
    push({ ...broken, e: { ...broken.e, ops: [{ op: "nope" }] } });
    frame();
    expect(events("partial:settled")).toEqual([
      { zones: [], boundaries: ["b-1"], status: "error" },
    ]);
    expect(marks("b-1")).toEqual(READY);
  });

  it("reports a cancelled batch as dropped and leaves the wrapper loading", () => {
    shell("b-1");
    const { boundaries, events } = harness();
    document.addEventListener("partial:before-apply", (e) => e.preventDefault(), {
      once: true,
    });
    boundaries.drain();
    push(fill("b-1", "one"));
    frame();
    expect(text("b-1")).toBe("loading b-1");
    expect(events("partial:settled")).toEqual([
      { zones: [], boundaries: ["b-1"], status: "dropped" },
    ]);
    expect(marks("b-1")).toEqual(["loading", null, null, false]);
  });
});

describe("nested boundaries", () => {
  const parent = (): Fill => fill("b-1", wrapperHtml("b-2"));
  const child = (extra: Partial<Fill> = {}): Fill =>
    fill("b-2", "inner", { p: "k-b-1", ...extra });

  it("applies a child pushed in the same task as its parent after the parent", () => {
    shell("b-1");
    const { boundaries, events } = harness();
    const mounted: Element[] = [];
    const onMount = (event: Event): void => void mounted.push(event.target as Element);
    document.addEventListener("next:mounted", onMount);
    boundaries.drain();
    push(parent(), child());
    frame();
    document.removeEventListener("next:mounted", onMount);
    expect(text("b-2")).toBe("inner");
    expect([marks("b-1"), marks("b-2")]).toEqual([READY, READY]);
    expect(events("partial:before-apply")).toHaveLength(1);
    expect(events("partial:applied")).toHaveLength(1);
    expect(mounted).toEqual(["b-1", "b-2"].map((id) => document.getElementById(id)));
    expect(events("partial:settled")).toEqual([
      { zones: [], boundaries: ["b-1", "b-2"], status: "ok" },
    ]);
  });

  it("finds a child pushed in a later task of the same frame", () => {
    shell("b-1");
    const { boundaries, events } = harness();
    boundaries.drain();
    push(parent());
    push(child({ s: "error", k: "timeout" }));
    expect(painter.queued).toHaveLength(1);
    frame();
    expect(text("b-2")).toBe("inner");
    expect(marks("b-1")).toEqual(READY);
    expect(marks("b-2")).toEqual(["error", "timeout", null, false]);
    expect(events("partial:before-apply")).toHaveLength(1);
    expect(events("partial:applied")).toHaveLength(1);
  });

  it("applies a child that came before its parent in the same frame", () => {
    shell("b-1");
    const { boundaries, events } = harness();
    boundaries.drain();
    push(child(), parent());
    frame();
    expect(text("b-2")).toBe("inner");
    expect([marks("b-1"), marks("b-2")]).toEqual([READY, READY]);
    expect(events("partial:settled")).toEqual([
      { zones: [], boundaries: ["b-1", "b-2"], status: "ok" },
    ]);
  });

  it("keeps a child until the parent waiting on its stylesheet has applied", () => {
    shell("b-1");
    let loaded: () => void = () => undefined;
    const assets: AssetBridge = {
      loadCss: (manifest, done) => {
        if (manifest.length > 0) loaded = done;
        else done();
      },
      loadJs: () => undefined,
      versionMismatch: () => false,
      acceptVersion: () => undefined,
    };
    const { boundaries, events } = harness({ assets });
    boundaries.drain();
    const styled = parent();
    push({
      ...styled,
      e: { ...styled.e, assets: [{ kind: "css", url: "/static/p.css", load: "link" }] },
    });
    frame();
    push(child());
    frame();
    expect(document.getElementById("b-2")).toBeNull();
    expect(painter.queued).toHaveLength(0);
    push(0);
    loaded();
    expect(text("b-2")).toBe("loading b-2");
    expect(marks("b-2")).toEqual(LOADING);
    frame();
    expect(text("b-2")).toBe("inner");
    expect([marks("b-1"), marks("b-2")]).toEqual([READY, READY]);
    expect(events("partial:settled")).toEqual([
      { zones: [], boundaries: ["b-1"], status: "ok" },
      { zones: [], boundaries: ["b-2"], status: "ok" },
    ]);
  });

  it("keeps an orphan child while a batch is still applying", () => {
    shell("b-1");
    let loaded: () => void = () => undefined;
    const assets: AssetBridge = {
      loadCss: (manifest, done) => {
        if (manifest.length > 0) loaded = done;
        else done();
      },
      loadJs: () => undefined,
      versionMismatch: () => false,
      acceptVersion: () => undefined,
    };
    const { boundaries } = harness({ assets });
    boundaries.drain();
    const styled = parent();
    push({
      ...styled,
      e: { ...styled.e, assets: [{ kind: "css", url: "/static/p.css", load: "link" }] },
    });
    frame();
    push(child());
    frame();
    push(child());
    frame();
    loaded();
    frame();
    expect(text("b-2")).toBe("inner");
  });

  it("drops a child once no parent is left to insert its target", () => {
    shell("b-1", "b-3");
    let loaded: () => void = () => undefined;
    const assets: AssetBridge = {
      loadCss: (manifest, done) => {
        if (manifest.length > 0) loaded = done;
        else done();
      },
      loadJs: () => undefined,
      versionMismatch: () => false,
      acceptVersion: () => undefined,
    };
    const { boundaries, events } = harness({ assets });
    boundaries.drain();
    const styled = fill("b-1", "no child here");
    push({
      ...styled,
      e: { ...styled.e, assets: [{ kind: "css", url: "/static/p.css", load: "link" }] },
    });
    frame();
    push(child(), fill("b-3", "three"));
    frame();
    loaded();
    frame();
    expect(painter.queued).toHaveLength(0);
    expect(document.getElementById("b-2")).toBeNull();
    expect(events("partial:settled")).toEqual([
      { zones: [], boundaries: ["b-3"], status: "ok" },
      { zones: [], boundaries: ["b-1"], status: "ok" },
    ]);
  });

  it("drops a child whose parent was superseded without a word", () => {
    shell("b-1", "b-3");
    stamp(document.getElementById("b-1")!);
    const { boundaries, events } = harness();
    boundaries.drain();
    push(parent(), child(), fill("b-3", "three"));
    frame();
    expect(text("b-1")).toBe("loading b-1");
    expect(document.getElementById("b-2")).toBeNull();
    expect(text("b-3")).toBe("three");
    expect(events("partial:settled")).toEqual([
      { zones: [], boundaries: ["b-1"], status: "dropped" },
      { zones: [], boundaries: ["b-3"], status: "ok" },
    ]);
  });

  it("holds the parent and its child together for a listener that calls hold", () => {
    shell("b-1");
    const { boundaries, events } = harness();
    let resume: () => void = () => undefined;
    document.addEventListener(
      "partial:before-apply",
      (event) => (resume = (event as CustomEvent<BeforeApply>).detail.hold()),
      { once: true },
    );
    boundaries.drain();
    push(parent(), child());
    frame();
    expect(marks("b-1")).toEqual(["loading", null, "true", true]);
    expect(document.getElementById("b-2")).toBeNull();
    expect(events("partial:settled")).toEqual([]);
    resume();
    expect(text("b-2")).toBe("inner");
    expect([marks("b-1"), marks("b-2")]).toEqual([READY, READY]);
    expect(events("partial:before-apply")).toHaveLength(1);
    expect(events("partial:settled")).toEqual([
      { zones: [], boundaries: ["b-1", "b-2"], status: "ok" },
    ]);
  });

  it("leaves a child of a cancelled batch out of the dropped report", () => {
    shell("b-1");
    const { boundaries, events } = harness();
    document.addEventListener("partial:before-apply", (e) => e.preventDefault(), {
      once: true,
    });
    boundaries.drain();
    push(parent(), child());
    frame();
    expect(document.getElementById("b-2")).toBeNull();
    expect(events("partial:settled")).toEqual([
      { zones: [], boundaries: ["b-1"], status: "dropped" },
    ]);
  });
});

describe("yielding", () => {
  it("applies several batches in one task while they fit the budget", () => {
    const ids = Array.from({ length: 12 }, (_, index) => `b-${index}`);
    shell(...ids);
    const { boundaries, events } = harness();
    boundaries.drain();
    push(...ids.map((id) => fill(id, id)));
    frame();
    expect(
      events("partial:settled").map((d) => (d.boundaries as string[]).length),
    ).toEqual([8, 4]);
    expect(painter.queued).toHaveLength(0);
  });

  it("resumes through scheduler.yield where the browser has it", async () => {
    let now = 0;
    vi.spyOn(performance, "now").mockImplementation(() => (now += 5));
    vi.stubGlobal("scheduler", { yield: () => Promise.resolve() });
    shell("b-1", "b-2");
    const { boundaries } = harness();
    boundaries.drain();
    push(fill("b-1", "one"), fill("b-2", "two"));
    expect(painter.queued).toHaveLength(0);
    await vi.waitFor(() => expect([text("b-1"), text("b-2")]).toEqual(["one", "two"]));
    expect(painter.queued).toHaveLength(0);
  });

  it("keeps its cost guess when a batch took no measurable time", () => {
    shell("b-1", "b-2", "b-3");
    vi.spyOn(performance, "now").mockReturnValue(1);
    const { boundaries, events } = harness();
    boundaries.drain();
    push(fill("b-1", "one"), fill("b-2", "two"), fill("b-3", "three"));
    frame();
    expect(events("partial:settled")).toHaveLength(1);
  });
});

describe("a chunk carried in a template", () => {
  function held(id: string, html: string, op = "inner"): Fill {
    document.body.insertAdjacentHTML(
      "beforeend",
      `<template id="nx-${id}">${html}</template>`,
    );
    return {
      z: id,
      s: "ready",
      t: `nx-${id}`,
      e: { ops: [null, { op, target: { boundary: id } }, { op: "event" }] },
    };
  }

  it("moves the parsed nodes into the boundary and drops the template", () => {
    shell("b-1");
    const { boundaries } = harness();
    boundaries.drain();
    const chunk = held("b-1", "<section>done</section>");
    const parsed = (document.getElementById("nx-b-1") as HTMLTemplateElement).content
      .firstChild;
    push(chunk);
    expect(document.getElementById("nx-b-1")).toBeNull();
    frame();
    expect(document.getElementById("b-1")!.firstChild).toBe(parsed);
    expect(text("b-1")).toBe("done");
    expect(marks("b-1")).toEqual(READY);
  });

  it.each([
    ["a row", "<table><tbody>", "</tbody></table>", "tr", "<td>cell</td>"],
    ["a cell", "<table><tbody><tr>", "</tr></tbody></table>", "td", "cell"],
    ["an option", "<select>", "</select>", "option", "cell"],
    ["an item", "<ul>", "</ul>", "li", "cell"],
  ])("replaces %s its context alone admits", (_, open, close, tag, body) => {
    const loading = `<${tag} id="b-1" data-next-state="loading" aria-busy="true">wait</${tag}>`;
    document.body.innerHTML = `${open}${loading}${close}`;
    const { boundaries } = harness();
    boundaries.drain();
    push(
      held(
        "b-1",
        `<${tag} id="b-1" data-next-state="ready">${body}</${tag}>`,
        "replace",
      ),
    );
    frame();
    const el = document.getElementById("b-1")!;
    expect(el.tagName.toLowerCase()).toBe(tag);
    expect(el.textContent).toBe("cell");
    expect(el.getAttribute("data-next-state")).toBe("ready");
  });

  it("neutralises the scripts of the fragment and of its nested templates", () => {
    shell("b-1");
    const { boundaries } = harness();
    boundaries.drain();
    push(
      held(
        "b-1",
        "<script>window.ran = 1</script><template><p>in</p><script>x()</script></template>" +
          '<script type="application/json">{}</script><p>after</p>',
      ),
    );
    frame();
    const el = document.getElementById("b-1")!;
    expect(el.querySelectorAll("script")).toHaveLength(1);
    expect(el.querySelector("script")!.type).toBe("application/json");
    const nested = el.querySelector("template")!;
    expect(nested.content.querySelector("script")).toBeNull();
    expect(nested.content.textContent).toBe("in");
    expect(text("b-1")).toContain("after");
  });

  it("drops the template of a chunk whose boundary went stale", () => {
    shell("b-1");
    const { boundaries, events } = harness();
    boundaries.drain();
    markBusy([document.getElementById("b-1")!]);
    push(held("b-1", "<p>late</p>"));
    frame();
    expect(document.querySelector("template")).toBeNull();
    expect(events("partial:settled")).toEqual([
      { zones: [], boundaries: ["b-1"], status: "dropped" },
    ]);
  });

  it("takes the templates of the chunks queued before the bundle", () => {
    shell("b-1");
    push(held("b-1", "<p>early</p>"));
    const { boundaries } = harness();
    boundaries.drain();
    expect(document.querySelector("template")).toBeNull();
    frame();
    expect(text("b-1")).toBe("early");
  });

  it("leaves the op as it came when the named template is missing or foreign", () => {
    shell("b-1", "b-2");
    document.body.insertAdjacentHTML(
      "beforeend",
      '<div id="nx-b-2">not a template</div><template id="nx-b-3">x</template>' +
        '<template id="nx-b-4">x</template>',
    );
    const { boundaries } = harness();
    boundaries.drain();
    const ops = (id: string): unknown => [
      null,
      { op: "inner", target: { boundary: id }, html: "<p>inline</p>" },
    ];
    push(
      { z: "b-1", s: "ready", t: "nx-b-1", e: { ops: ops("b-1") } },
      { z: "b-2", s: "ready", t: "nx-b-2", e: { ops: ops("b-2") } },
      { z: "b-3", t: "nx-b-3", e: {} },
      { z: "b-4", t: "nx-b-4", e: { ops: [{ op: "event" }] } },
    );
    expect(document.getElementById("nx-b-2")).toBeNull();
    expect(document.querySelector("template")).toBeNull();
    frame();
    expect(text("b-1")).toBe("inline");
    expect(text("b-2")).toBe("inline");
  });
});
