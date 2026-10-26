import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createSse } from "./sse";
import type { EventSourceAdapter, SseDeps } from "./sse";
import { manualVisibility } from "./test-doubles";
import { createWebSocketTransport } from "./ws";
import type { WebSocketAdapter } from "./ws";

interface Harness {
  deps: Partial<SseDeps>;
  connect(): Promise<void>;
  drop(): Promise<void>;
  send(frame: Record<string, unknown>): void;
  hello(topics: Record<string, unknown>): void;
  subs(): unknown[];
}

async function flush(): Promise<void> {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

function sseHarness(): Harness {
  const handles: {
    message(data: string): void;
    error(fatal: boolean): void;
    open(): void;
    control(name: string, data: string): void;
  }[] = [];
  const source: EventSourceAdapter = {
    open(_url, onMessage, onError, onControl, onOpen) {
      handles.push({
        message: onMessage,
        error: onError,
        open: () => onOpen?.(),
        control: (name, data) => onControl?.(name, data),
      });
      return { close: () => undefined };
    },
  };
  const last = () => handles[handles.length - 1]!;
  return {
    deps: { source },
    connect: () => Promise.resolve(last().open()),
    drop: () => Promise.resolve(last().error(false)),
    send: (frame) => last().message(JSON.stringify(frame)),
    hello: (topics) => last().control("next-hello", JSON.stringify({ topics })),
    subs: () => [],
  };
}

function wsHarness(): Harness {
  const sockets: {
    sent: string[];
    open(): void;
    message(body: unknown): void;
    close(code: number): void;
  }[] = [];
  const socket: WebSocketAdapter = {
    open(_url, onOpen, onMessage, onClose) {
      const item = {
        sent: [] as string[],
        open: () => onOpen((body) => item.sent.push(body)),
        message: onMessage,
        close: onClose,
      };
      sockets.push(item);
      return { close: () => undefined };
    },
  };
  const websocketFetch = () =>
    Promise.resolve(
      Response.json({
        protocol: 2,
        transport: "ws",
        ticket: "ticket",
        url: "/_next/ws/",
      }),
    );
  const last = () => sockets[sockets.length - 1]!;
  return {
    deps: {
      useWebSocket: true,
      websocket: createWebSocketTransport,
      socket,
      websocketFetch,
    },
    async connect() {
      await vi.advanceTimersByTimeAsync(1000);
      await flush();
      last().open();
    },
    async drop() {
      last().close(1006);
      await flush();
    },
    send: (frame) => last().message(JSON.stringify({ type: "patches", data: frame })),
    hello: (topics) =>
      last().message(JSON.stringify({ type: "hello", heartbeat: 0, topics })),
    subs: () => sockets.map((item) => JSON.parse(item.sent[0]!) as unknown),
  };
}

function frame(seq: number, epoch = "e"): Record<string, unknown> {
  return {
    version: "v1",
    ops: [{ op: "refresh", zone: "results" }],
    assets: [],
    form: null,
    topic: "room",
    epoch,
    seq,
  };
}

const position = (seq: number, epoch = "e") => ({ room: { epoch, seq } });

let applied: unknown[];
let fetched: { url: string; zone: string }[];
let gaps: Record<string, unknown>[];

function start(harness: Harness, visibility = manualVisibility()) {
  return createSse({
    apply: (raw) => applied.push((raw as { seq: unknown }).seq),
    fetch: (request) => {
      fetched.push(request);
      return Promise.resolve();
    },
    dispatch: (event, detail) => {
      if (event === "next:stream-gap") gaps.push(detail);
    },
    document,
    visibility,
    ...harness.deps,
  });
}

async function live(harness: Harness, visibility = manualVisibility()) {
  const sse = start(harness, visibility);
  sse.scan(document);
  await harness.connect();
  harness.hello(position(0));
  for (const seq of [1, 2, 3]) harness.send(frame(seq));
  await harness.drop();
  await harness.connect();
  return sse;
}

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML =
    '<div data-next-stream="/stream/"></div><div data-next-zone="results"></div>';
  applied = [];
  fetched = [];
  gaps = [];
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe.each([
  ["sse", sseHarness],
  ["ws", wsHarness],
])("message replay over %s", (_name, make) => {
  it("applies replayed frames and advances the cursor past them", async () => {
    const harness = make();
    const sse = await live(harness);
    harness.send(frame(4));
    harness.send(frame(5));
    harness.send(frame(6));
    expect(applied).toEqual([1, 2, 3, 4, 5, 6]);
    expect(fetched).toEqual([]);
    expect(gaps).toEqual([]);
    sse._reset();
  });

  it("resyncs nothing when the hello after replay matches the cursor", async () => {
    const harness = make();
    const sse = await live(harness);
    harness.send(frame(4));
    harness.send(frame(5));
    harness.hello(position(5));
    expect(applied).toEqual([1, 2, 3, 4, 5]);
    expect(fetched).toEqual([]);
    expect(gaps).toEqual([]);
    sse._reset();
  });

  it("drops live frames that duplicate replayed ones", async () => {
    const harness = make();
    const sse = await live(harness);
    harness.send(frame(4));
    harness.send(frame(4));
    harness.send(frame(5));
    harness.hello(position(5));
    harness.send(frame(4));
    harness.send(frame(5));
    harness.send(frame(6));
    expect(applied).toEqual([1, 2, 3, 4, 5, 6]);
    expect(fetched).toEqual([]);
    expect(gaps).toEqual([]);
    sse._reset();
  });

  it("resyncs when the hello is ahead of a partial replay", async () => {
    const harness = make();
    const sse = await live(harness);
    harness.send(frame(4));
    harness.hello(position(6));
    harness.send(frame(6));
    harness.send(frame(7));
    expect(applied).toEqual([1, 2, 3, 4, 7]);
    expect(fetched).toEqual([{ url: "/", zone: "results" }]);
    expect(gaps).toEqual([{ topic: "room", from: 4, to: 6 }]);
    sse._reset();
  });

  it("resyncs when a replay starts past the cursor", async () => {
    const harness = make();
    const sse = await live(harness);
    harness.send(frame(5));
    harness.hello(position(5));
    expect(applied).toEqual([1, 2, 3, 5]);
    expect(fetched).toEqual([{ url: "/", zone: "results" }]);
    expect(gaps).toEqual([{ topic: "room", from: 3, to: 5 }]);
    sse._reset();
  });
});

function presence(count: number): Record<string, unknown> {
  return {
    version: "v1",
    ops: [
      { op: "event", name: "next:presence", detail: { count } },
      { op: "refresh", zone: "results" },
    ],
    assets: [],
    form: null,
    topic: "room",
  };
}

describe.each([
  ["sse", sseHarness],
  ["ws", wsHarness],
])("ephemeral frames over %s", (_name, make) => {
  async function watched(harness: Harness) {
    const sse = start(harness);
    const seen: unknown[] = [];
    sse.on("room", "next:presence", (detail, meta) =>
      seen.push({ detail, meta, current: sse.meta() }),
    );
    sse.scan(document);
    await harness.connect();
    return { sse, seen };
  }

  it("applies a snapshot right after hello and leaves the cursor alone", async () => {
    const harness = make();
    const { sse, seen } = await watched(harness);
    harness.hello(position(2, "e"));
    fetched.length = 0;
    gaps.length = 0;
    harness.send(presence(1));
    harness.send(frame(3));
    expect(applied).toEqual([undefined, 3]);
    expect(seen).toEqual([
      {
        detail: { count: 1 },
        meta: {
          topic: "room",
          epoch: "",
          seq: undefined,
          serverTime: expect.any(Number),
        },
        current: {
          topic: "room",
          epoch: "",
          seq: undefined,
          serverTime: expect.any(Number),
        },
      },
    ]);
    expect(fetched).toEqual([]);
    expect(gaps).toEqual([]);
    sse._reset();
  });

  it("neither gaps nor dedupes between sequenced frames", async () => {
    const harness = make();
    const { sse, seen } = await watched(harness);
    harness.hello(position(0));
    harness.send(frame(1));
    harness.send(presence(2));
    harness.send(presence(2));
    harness.send(frame(2));
    harness.send(presence(3));
    harness.send(frame(2));
    harness.send(frame(3));
    expect(applied).toEqual([1, undefined, undefined, 2, undefined, 3]);
    expect(seen.map((item) => (item as { detail: unknown }).detail)).toEqual([
      { count: 2 },
      { count: 2 },
      { count: 3 },
    ]);
    expect(fetched).toEqual([]);
    expect(gaps).toEqual([]);
    sse._reset();
  });

  it("still detects a real gap after an ephemeral frame", async () => {
    const harness = make();
    const { sse } = await watched(harness);
    harness.hello(position(0));
    harness.send(frame(1));
    harness.send(presence(1));
    harness.send(frame(3));
    expect(applied).toEqual([1, undefined, 3]);
    expect(gaps).toEqual([{ topic: "room", from: 1, to: 3 }]);
    sse._reset();
  });
});

describe("websocket cursor across ephemeral frames", () => {
  it("subscribes again with the last sequenced position", async () => {
    const harness = wsHarness();
    const sse = start(harness);
    sse.scan(document);
    await harness.connect();
    harness.hello(position(0));
    harness.send(presence(1));
    harness.send(frame(1));
    harness.send(presence(2));
    await harness.drop();
    await harness.connect();
    expect(
      harness.subs().map((sub) => (sub as { last_seq: unknown }).last_seq),
    ).toEqual([{}, position(1)]);
    sse._reset();
  });
});

describe("websocket replay cursor", () => {
  it("subscribes again with the cursor that replayed frames advanced", async () => {
    const harness = wsHarness();
    const sse = await live(harness);
    harness.send(frame(4));
    harness.send(frame(5));
    harness.hello(position(5));
    await harness.drop();
    await harness.connect();
    expect(
      harness.subs().map((sub) => (sub as { last_seq: unknown }).last_seq),
    ).toEqual([{}, position(3), position(5)]);
    sse._reset();
  });

  it("subscribes with the replay-advanced cursor after a visibility resume", async () => {
    const harness = wsHarness();
    const visibility = manualVisibility();
    const sse = await live(harness, visibility);
    harness.send(frame(4));
    harness.hello(position(4));
    visibility.setHidden(true);
    visibility.setHidden(false);
    await harness.connect();
    expect(harness.subs().at(-1)).toEqual({
      type: "sub",
      ticket: "ticket",
      last_seq: position(4),
    });
    sse._reset();
  });
});

describe("sse event ids", () => {
  class FakeEventSource extends EventTarget {
    static readonly CLOSED = 2;
    static last: FakeEventSource | undefined;
    readyState = 1;
    onopen: (() => void) | null = null;
    onerror: (() => void) | null = null;
    constructor(readonly url: string) {
      super();
      FakeEventSource.last = this;
    }
    close(): void {
      this.readyState = FakeEventSource.CLOSED;
    }
    emit(type: string, data: unknown, lastEventId?: string): void {
      const init: MessageEventInit<string> = { data: JSON.stringify(data) };
      if (lastEventId !== undefined) init.lastEventId = lastEventId;
      this.dispatchEvent(new MessageEvent(type, init));
    }
  }

  function run(ids: boolean) {
    applied = [];
    fetched = [];
    gaps = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    const sse = start({ ...sseHarness(), deps: {} });
    sse.scan(document);
    const source = FakeEventSource.last!;
    const id = (seq: number) => (ids ? `e:${seq}` : undefined);
    source.onopen?.();
    source.emit("next-hello", { topics: position(0) });
    for (const seq of [1, 2, 3]) source.emit("next-patches", frame(seq), id(seq));
    source.onerror?.();
    source.onopen?.();
    source.emit("next-patches", frame(4), id(4));
    source.emit("next-hello", { topics: position(4) });
    source.emit("next-patches", frame(4), id(4));
    source.emit("next-patches", frame(4), ids ? "e:9" : undefined);
    source.emit("next-patches", frame(6), id(6));
    sse._reset();
    return { applied, fetched, gaps };
  }

  it("handles a message event with an id exactly like one without", () => {
    const plain = run(false);
    const tagged = run(true);
    expect(plain.applied).toEqual([1, 2, 3, 4, 6]);
    expect(plain.gaps).toEqual([{ topic: "room", from: 4, to: 6 }]);
    expect(tagged).toEqual(plain);
  });

  it("applies an ephemeral event without an id off the cursor", () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const sse = start({ ...sseHarness(), deps: {} });
    const counts: unknown[] = [];
    sse.on("room", "next:presence", (detail) => counts.push(detail.count));
    sse.scan(document);
    const source = FakeEventSource.last!;
    source.onopen?.();
    source.emit("next-hello", { topics: position(0) });
    source.emit("next-patches", presence(1));
    source.emit("next-patches", frame(1), "e:1");
    source.emit("next-patches", presence(2));
    source.emit("next-patches", frame(2), "e:2");
    expect(counts).toEqual([1, 2]);
    expect(applied).toEqual([undefined, 1, undefined, 2]);
    expect(fetched).toEqual([]);
    expect(gaps).toEqual([]);
    sse._reset();
  });
});
