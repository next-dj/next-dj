import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Applier } from "./apply";
import type { Applied, AssetBridge } from "./apply";
import type { Fill } from "./drain";
import { createPartial } from "./partial";
import {
  ACCEPT,
  CONTENT_TYPE,
  STREAM_ACCEPT,
  fire,
  isBusy,
  markBusy,
  now,
  stamped,
} from "./protocol";
import { createReader, frames } from "./reader";
import type { ReaderHost } from "./reader";
import { chunkModules, fill, manualFrames, manualPollClock } from "./test-doubles";
import { Wire, zoneKey } from "./wire";
import type { Outcome, StreamReader, WireRequest } from "./wire";

interface Dispatched {
  event: string;
  detail: Record<string, unknown>;
}

interface Body {
  stream: ReadableStream<Uint8Array>;
  cancel: ReturnType<typeof vi.fn>;
  send(text: string, size?: number): void;
  close(): void;
  fail(error: Error): void;
}

let painter: ReturnType<typeof manualFrames>;
let listening = new AbortController();

beforeEach(() => {
  listening = new AbortController();
  painter = manualFrames();
  vi.spyOn(document, "readyState", "get").mockReturnValue("loading");
  delete window.$nx;
  document.body.innerHTML = '<section data-next-zone="z">old</section>';
});

afterEach(() => {
  listening.abort();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

function frame(): void {
  painter.paint();
}

async function flush(): Promise<void> {
  for (let i = 0; i < 5; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function body(): Body {
  let controller: ReadableStreamDefaultController<Uint8Array> | undefined;
  const cancel = vi.fn();
  const stream = new ReadableStream<Uint8Array>({
    start: (opened) => {
      controller = opened;
    },
    cancel,
  });
  return {
    stream,
    cancel,
    send(text, size = Infinity) {
      const bytes = new TextEncoder().encode(text);
      for (let at = 0; at < bytes.length; at += size)
        controller!.enqueue(bytes.slice(at, at + size));
    },
    close: () => controller!.close(),
    fail: (error) => controller!.error(error),
  };
}

function streamed(source: Body, status = 200): Response {
  return new Response(source.stream, {
    status,
    headers: { "content-type": "text/event-stream; charset=utf-8" },
  });
}

const HELLO = 'event: hello\ndata: {"protocol":2,"streamed":true}\n\n';

const sse = (event: string, data: unknown): string =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

const wrapper = (id: string, inner = `<p>loading ${id}</p>`): string =>
  `<div id="${id}" data-next-boundary="${id}" data-next-state="loading">${inner}</div>`;

const opening = (...ids: string[]): string =>
  sse("patch", {
    e: {
      version: "",
      ops: [
        {
          op: "inner",
          target: { zone: "z" },
          html: ids.map((id) => wrapper(id)).join(""),
        },
      ],
      assets: [],
    },
  });

const patch = (id: string, html: string, extra: Partial<Fill> = {}): string =>
  sse("patch", fill(id, html, extra));

const end = (code: string): string => sse("end", { code });

const json = (html: string, target: Record<string, string>): Response =>
  new Response(JSON.stringify({ version: "", ops: [{ op: "inner", target, html }] }), {
    headers: { "content-type": CONTENT_TYPE },
  });

function text(id: string): string | null {
  return document.getElementById(id)!.textContent;
}

function marks(id: string): (string | null)[] {
  const el = document.getElementById(id)!;
  return [el.getAttribute("data-next-state"), el.getAttribute("data-next-error")];
}

const READY = ["ready", null];
const LOADING = ["loading", null];
const INCOMPLETE = ["error", "incomplete"];

function harness(
  options: {
    stream?: boolean;
    timeout?: number;
    assets?: AssetBridge;
    active?: () => boolean;
  } = {},
) {
  const dispatched: Dispatched[] = [];
  const record = (event: string, detail: Record<string, unknown>): void =>
    void dispatched.push({ event, detail });
  const applier = new Applier({
    dispatch: record,
    mergeContext: () => undefined,
    document,
    ...(options.assets === undefined ? {} : { assets: () => options.assets! }),
  });
  const partial: ReaderHost["partial"] = {
    apply: (raw, applying) => applier.apply(raw, applying),
    _mark: markBusy,
    _busy: isBusy,
    _stamped: stamped,
    _now: now,
    _fire: record,
  };
  const ready: (() => void)[] = [];
  createReader(
    {
      partial,
      on: (_event, listener) => {
        ready.push(listener);
        return () => undefined;
      },
    },
    { signal: listening.signal },
  );
  const clock = manualPollClock();
  const responses: (() => Promise<Response>)[] = [];
  const calls: RequestInit[] = [];
  const navigated: string[] = [];
  const store = new Map<string, string>();
  const wire = new Wire({
    fetch: (_url, init) => {
      calls.push(init);
      return responses.shift()?.() ?? new Promise<Response>(() => undefined);
    },
    clock,
    navigate: (url) => void navigated.push(url),
    session: {
      get: (key) => store.get(key) ?? null,
      set: (key, value) => void store.set(key, value),
      remove: (key) => void store.delete(key),
    },
    dispatch: (event, detail) => fire(document, record, event, detail),
    onEnvelope: (raw, seen) =>
      new Promise<Applied>((done) => applier.apply(raw, { ...seen, done })),
    csrf: { current: () => undefined, ensure: () => Promise.resolve(undefined) },
    timeout: () => options.timeout ?? 0,
    ...(options.active === undefined ? {} : { active: options.active }),
    resolveZone: (name, root) => root.querySelector(`[data-next-zone="${name}"]`),
    reader: () => (options.stream === false ? undefined : partial._reader),
  });
  const events = (name: string): Record<string, unknown>[] =>
    dispatched.filter((d) => d.event === name).map((d) => d.detail);
  const respond = (response: Response): number =>
    responses.push(() => Promise.resolve(response));
  const load = (request: Partial<WireRequest> = {}): Promise<Outcome> =>
    wire.fetch({ url: "/p/", zone: "z", ...request });
  const accept = (index: number): string | null =>
    new Headers(calls[index]!.headers).get("Accept");
  return {
    wire,
    clock,
    calls,
    navigated,
    events,
    respond,
    load,
    accept,
    ready: () => ready.forEach((listener) => listener()),
  };
}

describe("a streamed partial answer", () => {
  it("applies the first patch like a JSON envelope and the boundaries after it", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const before = vi.fn();
    document.addEventListener("partial:before-apply", before, {
      signal: listening.signal,
    });
    const sent = h.load();
    source.send(HELLO + opening("b-1", "b-2"));
    await flush();
    expect(before).toHaveBeenCalledTimes(1);
    expect(h.events("partial:applied")).toHaveLength(1);
    expect([text("b-1"), text("b-2")]).toEqual(["loading b-1", "loading b-2"]);
    expect(document.querySelector("section")!.hasAttribute("data-next-busy")).toBe(
      true,
    );
    source.send(patch("b-2", "two") + patch("b-1", "one") + end("complete"));
    await flush();
    expect(text("b-1")).toBe("loading b-1");
    frame();
    const outcome = await sent;
    expect(outcome).toMatchObject({ status: "ok", applied: true });
    expect([text("b-1"), text("b-2")]).toEqual(["one", "two"]);
    expect([marks("b-1"), marks("b-2")]).toEqual([READY, READY]);
    expect(document.querySelector("section")!.hasAttribute("data-next-busy")).toBe(
      false,
    );
    expect(h.events("partial:settled")).toEqual([
      { zones: ["z"], boundaries: ["b-2", "b-1"], status: "ok" },
    ]);
    expect(h.events("partial:error")).toEqual([]);
  });

  it("ignores heartbeats, the padding comment and the hello", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(
      HELLO +
        `:${" ".repeat(900)}\n\n` +
        ": ping\n\n" +
        opening("b-1") +
        ": ping\n\n" +
        patch("b-1", "one") +
        end("complete"),
    );
    await flush();
    frame();
    await expect(sent).resolves.toMatchObject({ status: "ok" });
    expect(text("b-1")).toBe("one");
  });

  it("reads a stream delivered one byte at a time", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(
      HELLO + opening("b-1") + patch("b-1", "naïve 日本") + end("complete"),
      1,
    );
    await flush();
    frame();
    await expect(sent).resolves.toMatchObject({ status: "ok" });
    expect(text("b-1")).toBe("naïve 日本");
  });

  it("tolerates CRLF line endings split across reads", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    const raw = HELLO + opening("b-1") + patch("b-1", "one") + end("complete");
    source.send(raw.replace(/\n/g, "\r\n"), 1);
    await flush();
    frame();
    await expect(sent).resolves.toMatchObject({ status: "ok" });
    expect(text("b-1")).toBe("one");
  });

  it("applies a child that came before its parent in the same frame", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(
      HELLO +
        opening("b-1") +
        patch("b-2", "inner", { p: "b-1" }) +
        patch("b-1", wrapper("b-2")) +
        end("complete"),
    );
    await flush();
    frame();
    await sent;
    expect(text("b-2")).toBe("inner");
    expect([marks("b-1"), marks("b-2")]).toEqual([READY, READY]);
  });

  it("applies a child frame once a frame before it inserted its wrapper", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + opening("b-1") + patch("b-1", wrapper("b-2")));
    await flush();
    frame();
    expect(marks("b-2")).toEqual(LOADING);
    source.send(patch("b-2", "inner", { p: "b-1" }) + end("complete"));
    await flush();
    frame();
    await expect(sent).resolves.toMatchObject({ status: "ok" });
    expect(text("b-2")).toBe("inner");
  });

  it("drops a second frame for a boundary already in the batch", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(
      HELLO +
        opening("b-1") +
        patch("b-1", "one") +
        patch("b-1", "two") +
        end("complete"),
    );
    await flush();
    frame();
    await sent;
    expect(text("b-1")).toBe("one");
  });

  it("marks an error frame with its kind", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(
      HELLO +
        opening("b-1") +
        patch("b-1", "late", { s: "error", k: "timeout" }) +
        end("deadline"),
    );
    await flush();
    frame();
    await expect(sent).resolves.toMatchObject({ status: "ok" });
    expect(marks("b-1")).toEqual(["error", "timeout"]);
  });

  it("keeps the zone ready and names what is left when the server ends on a failure", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + opening("b-1", "b-2") + patch("b-2", "two") + end("error"));
    await flush();
    frame();
    await expect(sent).resolves.toMatchObject({ status: "ok", applied: true });
    expect(marks("b-1")).toEqual(INCOMPLETE);
    expect(document.querySelector("section")!.getAttribute("data-next-state")).toBe(
      "ready",
    );
    expect(h.events("partial:error")).toEqual([
      { kind: "incomplete", boundaries: ["b-1"] },
    ]);
    expect(h.events("partial:settled")).toEqual([
      { zones: ["z"], boundaries: ["b-2"], status: "ok" },
    ]);
  });

  it("names the boundaries a deadline end left without a frame", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + opening("b-1") + end("deadline"));
    await expect(sent).resolves.toMatchObject({ status: "ok" });
    expect(h.events("partial:error")).toEqual([
      { kind: "incomplete", boundaries: ["b-1"] },
    ]);
  });

  it("marks what is still loading incomplete when the stream closes without end", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + opening("b-1", "b-2") + patch("b-1", "one"));
    source.close();
    await flush();
    expect(marks("b-2")).toEqual(LOADING);
    frame();
    await expect(sent).resolves.toMatchObject({ status: "ok" });
    expect(marks("b-1")).toEqual(READY);
    expect(marks("b-2")).toEqual(INCOMPLETE);
    expect(h.events("partial:error")).toEqual([
      { kind: "incomplete", boundaries: ["b-2"] },
    ]);
    expect(h.events("partial:settled")).toEqual([
      { zones: ["z"], boundaries: ["b-1"], status: "ok" },
    ]);
  });

  it("settles a connection that breaks after the first patch like a close", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + opening("b-1"));
    await flush();
    source.fail(new TypeError("connection reset"));
    const outcome = await sent;
    expect(outcome).toMatchObject({ status: "ok", applied: true });
    expect(marks("b-1")).toEqual(INCOMPLETE);
    expect(h.events("partial:error")).toEqual([
      { kind: "incomplete", boundaries: ["b-1"] },
    ]);
  });

  it("fails as a network error when the connection breaks before the first patch", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO);
    await flush();
    source.fail(new TypeError("connection reset"));
    const outcome = await sent;
    expect(outcome.error?.kind).toBe("network");
  });

  it("reads an event stream without a body as one that ended unpatched", async () => {
    const h = harness();
    h.respond(new Response(null, { headers: { "content-type": "text/event-stream" } }));
    const outcome = await h.load();
    expect(outcome.error?.kind).toBe("network");
  });

  it("fails as a network error when the stream closes before its first patch", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO);
    source.close();
    const outcome = await sent;
    expect(outcome.status).toBe("error");
    expect(outcome.error?.kind).toBe("network");
    expect(document.querySelector("section")!.textContent).toBe("old");
  });

  it("settles as a parse error and cancels the stream on a malformed first frame", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + "event: patch\ndata: {nope\n\n" + opening("b-1"));
    const outcome = await sent;
    expect(outcome.status).toBe("error");
    expect(h.events("partial:error")).toEqual([
      expect.objectContaining({ kind: "parse", body: "{nope", zones: ["z"] }),
    ]);
    expect(source.cancel).toHaveBeenCalled();
    expect(document.querySelector("section")!.textContent).toBe("old");
    expect(h.events("partial:settled")).toEqual([
      { zones: ["z"], boundaries: [], status: "error" },
    ]);
  });

  it("reports a malformed frame after the first patch and reads on", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(
      HELLO +
        opening("b-1") +
        "event: patch\ndata: {nope\n\n" +
        patch("b-1", "one") +
        end("complete"),
    );
    await flush();
    frame();
    await expect(sent).resolves.toMatchObject({ status: "ok" });
    expect(text("b-1")).toBe("one");
    expect(marks("b-1")).toEqual(READY);
    expect(h.events("partial:error")).toEqual([
      { kind: "parse", body: "{nope", error: expect.any(SyntaxError) },
    ]);
  });

  it("stops at a first patch the applier rejects", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + sse("patch", { e: { ops: [] } }) + patch("b-1", "one"));
    const outcome = await sent;
    expect(outcome.error?.kind).toBe("parse");
    expect(source.cancel).toHaveBeenCalled();
    expect(document.querySelector("section")!.textContent).toBe("old");
  });

  it("joins the data lines of one event with a line feed", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(
      HELLO +
        opening("b-1") +
        'event: patch\ndata: {"z":"b-1","s":"ready",\n' +
        'data: "e":{"ops":[{"op":"inner","target":{"boundary":"b-1"},"html":"one"}]}}\n\n' +
        end("complete"),
    );
    await flush();
    frame();
    await expect(sent).resolves.toMatchObject({ status: "ok" });
    expect(text("b-1")).toBe("one");
  });

  it("reads lines ended by a lone carriage return split across reads", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    const raw = HELLO + opening("b-1") + patch("b-1", "one") + end("complete");
    source.send(raw.replace(/\n/g, "\r"), 1);
    await flush();
    frame();
    await expect(sent).resolves.toMatchObject({ status: "ok" });
    expect(text("b-1")).toBe("one");
  });

  it("skips events it does not read without failing on their data", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    const chunk = {
      z: "b-1",
      s: "ready",
      e: { ops: [{ op: "inner", target: { boundary: "b-1" }, html: "one" }] },
    };
    source.send(
      "event: hello\ndata: not json\n\n" +
        "event: other\ndata: {oops\n\n" +
        "data: {oops\n\n" +
        "retry\n" +
        "event: end\n\n" +
        opening("b-1") +
        `event:patch\ndata:${JSON.stringify(chunk)}\n\n` +
        end("complete"),
    );
    await flush();
    frame();
    await expect(sent).resolves.toMatchObject({ status: "ok" });
    expect(text("b-1")).toBe("one");
    expect(h.events("partial:error")).toEqual([]);
  });

  it("writes the error digest of an error frame onto its wrapper", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(
      HELLO +
        opening("b-1") +
        patch("b-1", "failed", { s: "error", g: "f00d" }) +
        end("complete"),
    );
    await flush();
    frame();
    await sent;
    const wrapper = document.getElementById("b-1")!;
    expect(wrapper.getAttribute("data-next-error-digest")).toBe("f00d");
    expect(marks("b-1")).toEqual(["error", "error"]);
  });

  it("applies frames without waiting for a paint while the tab is hidden", async () => {
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + opening("b-1") + patch("b-1", "one") + end("complete"));
    await expect(sent).resolves.toMatchObject({ status: "ok" });
    expect(painter.queued).toHaveLength(0);
    expect(text("b-1")).toBe("one");
  });
});

describe("cancelling a stream", () => {
  it("stops reading and drops the request when a newer one aborts it", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + opening("b-1") + patch("b-1", "stale"));
    await flush();
    h.wire.abort(zoneKey("/p/", "z"));
    await expect(sent).resolves.toEqual({ status: "dropped", applied: false });
    expect(source.cancel).toHaveBeenCalled();
    frame();
    expect(text("b-1")).toBe("loading b-1");
    expect(document.querySelector("section")!.hasAttribute("data-next-busy")).toBe(
      false,
    );
    expect(h.events("partial:settled")).toEqual([
      { zones: ["z"], boundaries: [], status: "dropped" },
    ]);
  });

  it("drops a request aborted after its end while its frames wait", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + opening("b-1") + patch("b-1", "stale") + end("complete"));
    await flush();
    h.wire.abort(zoneKey("/p/", "z"));
    await expect(sent).resolves.toMatchObject({ status: "dropped" });
    frame();
    expect(text("b-1")).toBe("loading b-1");
  });

  it("skips a poll tick while the zone's stream is open", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + opening("b-1"));
    await flush();
    await expect(h.load({ background: true })).resolves.toEqual({
      status: "dropped",
      applied: false,
    });
    expect(h.calls).toHaveLength(1);
    source.send(end("complete"));
    await sent;
    h.respond(new Response(null, { status: 204 }));
    void h.load({ background: true });
    await flush();
    expect(h.calls).toHaveLength(2);
  });

  it("leaves a frame waiting on its stylesheet unapplied once the request aborts", async () => {
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
    const h = harness({ assets });
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(
      HELLO +
        opening("b-1") +
        sse("patch", {
          z: "b-1",
          s: "ready",
          e: {
            ops: [{ op: "inner", target: { boundary: "b-1" }, html: "styled" }],
            assets: [{ kind: "css", url: "/static/b.css", load: "link" }],
          },
        }),
    );
    await flush();
    frame();
    h.wire.abort(zoneKey("/p/", "z"));
    await expect(sent).resolves.toMatchObject({ status: "dropped" });
    loaded();
    expect(text("b-1")).toBe("loading b-1");
    expect(marks("b-1")).toEqual(LOADING);
    expect(document.getElementById("b-1")!.hasAttribute("data-next-busy")).toBe(false);
    expect(h.events("partial:applied").at(-1)).toMatchObject({ stale: true });
  });

  it("resumes the document sweep when a reset ends an open stream", async () => {
    document.body.innerHTML =
      '<section data-next-zone="z">old</section>' + wrapper("b-9");
    let alive = true;
    const h = harness({ active: () => alive });
    h.ready();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + opening("b-1"));
    await flush();
    window.$nx!.push(0);
    expect(marks("b-9")).toEqual(LOADING);
    alive = false;
    h.wire._reset();
    await expect(sent).resolves.toMatchObject({ status: "dropped" });
    expect(source.cancel).toHaveBeenCalled();
    expect(h.events("partial:settled")).toEqual([]);
    expect(marks("b-9")).toEqual(INCOMPLETE);
  });
});

describe("a newer request over an open stream", () => {
  const nested = (): string =>
    sse("patch", {
      e: {
        version: "",
        ops: [
          {
            op: "inner",
            target: { zone: "z" },
            html: wrapper("p-1", wrapper("b-1")),
          },
        ],
        assets: [],
      },
    });

  it("drops a frame for a boundary a reload claimed after the stream began", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + opening("b-1", "b-2"));
    await flush();
    void h.wire.fetch({
      url: "/p/",
      queue: "b-1",
      boundaries: [document.getElementById("b-1")!],
    });
    source.send(patch("b-1", "stale") + patch("b-2", "two") + end("complete"));
    await flush();
    frame();
    await expect(sent).resolves.toMatchObject({ status: "ok" });
    expect([text("b-1"), text("b-2")]).toEqual(["loading b-1", "two"]);
    expect(marks("b-1")).toEqual(LOADING);
    expect(h.events("partial:error")).toEqual([]);
  });

  it("drops a frame for a boundary reloaded alone after the stream began", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + opening("b-1", "b-2"));
    await flush();
    h.respond(json("fresh", { boundary: "b-1" }));
    await h.wire.fetch({
      url: "/p/",
      queue: "b-1",
      boundaries: [document.getElementById("b-1")!],
    });
    expect(text("b-1")).toBe("fresh");
    source.send(patch("b-1", "stale") + patch("b-2", "two") + end("complete"));
    await flush();
    frame();
    await expect(sent).resolves.toMatchObject({ status: "ok" });
    expect([text("b-1"), text("b-2")]).toEqual(["fresh", "two"]);
    expect(h.events("partial:settled").at(-1)).toEqual({
      zones: ["z"],
      boundaries: ["b-2"],
      status: "ok",
    });
  });

  it("drops a frame for a wrapper the reload of its parent created anew", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + nested());
    await flush();
    const first = document.getElementById("b-1");
    h.respond(json(wrapper("b-1", "<p>new</p>"), { boundary: "p-1" }));
    await h.wire.fetch({
      url: "/p/",
      queue: "p-1",
      boundaries: [document.getElementById("p-1")!],
    });
    expect(document.getElementById("b-1")).not.toBe(first);
    source.send(patch("b-1", "stale") + end("complete"));
    await flush();
    frame();
    await expect(sent).resolves.toMatchObject({ status: "ok" });
    expect(text("b-1")).toBe("new");
    expect(marks("b-1")).toEqual(LOADING);
    expect(h.events("partial:error")).toEqual([]);
  });
});

describe("the request deadline", () => {
  it("times out a stream whose first patch never came", async () => {
    const h = harness({ timeout: 1000 });
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO);
    await flush();
    h.clock.tick();
    const outcome = await sent;
    expect(outcome.error?.kind).toBe("timeout");
    expect(source.cancel).toHaveBeenCalled();
  });

  it("restarts the count on every read, so it measures silence", async () => {
    const h = harness({ timeout: 1000 });
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    await flush();
    expect(h.clock.intervals).toEqual([1000]);
    source.send(HELLO + opening("b-1"));
    await flush();
    source.send(": ping\n\n");
    await flush();
    expect(h.clock.pending()).toBe(1);
    expect(h.clock.intervals).toEqual([1000, 1000, 1000]);
    source.send(patch("b-1", "one") + end("complete"));
    await flush();
    frame();
    await expect(sent).resolves.toMatchObject({ status: "ok" });
    expect(h.clock.pending()).toBe(0);
  });

  it("settles a stream that stalls after its first patch like a close", async () => {
    const h = harness({ timeout: 1000 });
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + opening("b-1", "b-2") + patch("b-1", "one"));
    await flush();
    h.clock.tick();
    await flush();
    expect(source.cancel).toHaveBeenCalled();
    frame();
    const outcome = await sent;
    expect(outcome).toMatchObject({ status: "ok", applied: true });
    expect(text("b-1")).toBe("one");
    expect(marks("b-2")).toEqual(INCOMPLETE);
    expect(document.querySelector("section")!.getAttribute("data-next-state")).toBe(
      "ready",
    );
    expect(h.events("partial:error")).toEqual([
      { kind: "incomplete", boundaries: ["b-2"] },
    ]);
  });
});

describe("negotiation", () => {
  it("asks for a stream on a safe request only while a reader is present", async () => {
    const reading = harness();
    reading.respond(new Response(null, { status: 204 }));
    await reading.load({ navigate: false });
    reading.respond(new Response(null, { status: 204 }));
    await reading.load({ method: "POST", zone: "z", abortable: true });
    reading.respond(new Response(null, { status: 204 }));
    await reading.load({ method: "HEAD", navigate: false });
    const plain = harness({ stream: false });
    plain.respond(new Response(null, { status: 204 }));
    await plain.load({ navigate: false });
    expect([
      reading.accept(0),
      reading.accept(1),
      reading.accept(2),
      plain.accept(0),
    ]).toEqual([STREAM_ACCEPT, ACCEPT, ACCEPT, ACCEPT]);
  });

  it("applies a JSON answer to a request that asked for a stream", async () => {
    const h = harness();
    h.respond(
      new Response(
        JSON.stringify({
          version: "",
          ops: [{ op: "inner", target: { zone: "z" }, html: "json" }],
        }),
        { headers: { "content-type": CONTENT_TYPE } },
      ),
    );
    await expect(h.load()).resolves.toMatchObject({ status: "ok", applied: true });
    expect(h.accept(0)).toBe(STREAM_ACCEPT);
    expect(document.querySelector("section")!.textContent).toBe("json");
  });

  it("treats an event stream as a foreign answer without a reader", async () => {
    const h = harness({ stream: false });
    const source = body();
    h.respond(streamed(source));
    await h.load();
    expect(h.navigated).toHaveLength(1);
    expect(source.cancel).not.toHaveBeenCalled();
  });

  it("reports a crashed event stream as an HTTP error", async () => {
    const h = harness();
    const source = body();
    h.respond(streamed(source, 500));
    source.send("oops");
    source.close();
    const outcome = await h.load();
    expect(outcome.error).toMatchObject({ kind: "http", status: 500, body: "oops" });
  });

  it("asks for a stream only on a payload with $stream and a reader", async () => {
    vi.spyOn(document, "readyState", "get").mockReturnValue("complete");
    let context: Record<string, unknown> = {};
    const accepts: (string | null)[] = [];
    const surface = createPartial({
      ...chunkModules,
      dispatch: () => undefined,
      mergeContext: () => undefined,
      context: () => context,
    });
    surface._configure({
      document,
      navigate: () => undefined,
      fetch: async (_url, init) => {
        accepts.push(new Headers(init.headers).get("Accept"));
        return new Response('{"version":"","ops":[]}', {
          headers: { "content-type": CONTENT_TYPE },
        });
      },
    });
    await surface.fetch({ url: "/p/", zone: "z" });
    surface._reader = vi.fn<StreamReader>();
    await surface.fetch({ url: "/p/", zone: "z" });
    context = { $stream: true };
    await surface.fetch({ url: "/p/", zone: "z" });
    surface._reader = undefined;
    await surface.fetch({ url: "/p/", zone: "z" });
    expect(accepts).toEqual([ACCEPT, ACCEPT, STREAM_ACCEPT, ACCEPT]);
    const done = vi.fn();
    surface.apply({ version: "", ops: [] }, { done });
    expect(done).toHaveBeenCalledWith(expect.objectContaining({ ok: true }));
    surface._reset();
  });
});

describe("the streamed document", () => {
  it("drains the document chunks once however often the runtime is ready", () => {
    document.body.innerHTML = wrapper("b-1");
    (window.$nx ??= []).push({
      z: "b-1",
      s: "ready",
      e: { ops: [{ op: "inner", target: { boundary: "b-1" }, html: "one" }] },
    });
    const h = harness();
    expect(painter.queued).toHaveLength(0);
    h.ready();
    h.ready();
    expect(painter.queued).toHaveLength(1);
    frame();
    expect(text("b-1")).toBe("one");
    expect(h.events("partial:applied")).toHaveLength(1);
  });

  it("leaves the document sweep to the end of an open partial stream", async () => {
    document.body.innerHTML =
      '<section data-next-zone="z">old</section>' + wrapper("b-9");
    const h = harness();
    h.ready();
    const source = body();
    h.respond(streamed(source));
    const sent = h.load();
    source.send(HELLO + opening("b-1"));
    await flush();
    window.$nx!.push(0);
    expect(marks("b-9")).toEqual(LOADING);
    source.send(patch("b-1", "one") + end("complete"));
    await flush();
    frame();
    await sent;
    expect(marks("b-9")).toEqual(INCOMPLETE);
  });
});

describe("the frames of an event stream", () => {
  async function parsed(parts: (string | Uint8Array)[]): Promise<string[][]> {
    const encoder = new TextEncoder();
    const queue = parts.map((part) =>
      typeof part === "string" ? encoder.encode(part) : part,
    );
    const read = (): Promise<ReadableStreamReadResult<Uint8Array>> => {
      const value = queue.shift();
      return Promise.resolve(
        value ? { done: false, value } : { done: true, value: undefined },
      );
    };
    const found: string[][] = [];
    for await (const item of frames({ read })) found.push(item);
    return found;
  }

  const euro = new TextEncoder().encode("data: \u20ac\n\n");

  it.each([
    ["one frame", ["event: patch\ndata: 1\n\n"], [["patch", "1"]]],
    ["a CRLF split across reads", ["event: a\r", "\ndata: x\r\n\r\n"], [["a", "x"]]],
    ["a bare CR", ["data: x\r\r"], [["", "x"]]],
    ["data over several lines", ["data: a\ndata: b\n\n"], [["", "a\nb"]]],
    ["a field without its space", ["data:x\n\n"], [["", "x"]]],
    ["a frame without data", ["event: ping\n\ndata: y\n\n"], [["", "y"]]],
    ["a comment and a field it ignores", [": hi\nid: 3\ndata: y\n\n"], [["", "y"]]],
    ["a line split across reads", ["da", "ta: z", "\n\n"], [["", "z"]]],
    ["a frame the body cuts short", ["data: z\n"], []],
    [
      "a character split across reads",
      [euro.slice(0, 7), euro.slice(7)],
      [["", "\u20ac"]],
    ],
  ])("reads %s", async (_name, parts, expected) => {
    expect(await parsed(parts)).toEqual(expected);
  });
});
