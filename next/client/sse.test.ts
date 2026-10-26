import { beforeEach, describe, expect, it, vi } from "vitest";
import { createSse } from "./sse";
import { createStream } from "./stream";
import type { Cursor, Transport, TransportSink } from "./stream";
import type {
  EventSourceAdapter,
  SourceControl,
  StreamMeta,
  VisibilityAdapter,
} from "./sse";
import { manualVisibility, chunkModules } from "./test-doubles";
import { Wire } from "./wire";
import { createCsrf } from "./csrf";
import type { Envelope } from "./apply";
import { CONTENT_TYPE, HEADER_REQUEST_ID } from "./protocol";

interface MockSource {
  url: string;
  message(data: string): void;
  error(fatal: boolean): void;
  open(): void;
  control(name: string, data: string): void;
  closed: boolean;
}

// A mock EventSource adapter the test drives directly, since jsdom lacks one.
function mockSource(): { adapter: EventSourceAdapter; opened: MockSource[] } {
  const opened: MockSource[] = [];
  const adapter: EventSourceAdapter = {
    open(url, onMessage, onError, onControl, onOpen): SourceControl {
      const handle: MockSource = {
        url,
        message: onMessage,
        error: onError,
        open: () => onOpen?.(),
        control: (name, data) => onControl?.(name, data),
        closed: false,
      };
      opened.push(handle);
      return {
        close() {
          handle.closed = true;
        },
      };
    },
  };
  return { adapter, opened };
}

function envelope(ops: unknown[], requestId?: string): string {
  const body: Record<string, unknown> = {
    version: "v1",
    ops,
    assets: [],
    form: null,
  };
  if (requestId !== undefined) body.request_id = requestId;
  return JSON.stringify(body);
}

function topicEnvelope(
  ops: unknown[],
  topic: string,
  seq: number,
  serverTime = 1000,
  epoch = "e",
): string {
  return JSON.stringify({
    version: "v1",
    ops,
    assets: [],
    form: null,
    topic,
    epoch,
    seq,
    server_time: serverTime,
  });
}

it("falls back to the platform seams and resets twice harmlessly", () => {
  const sse = createSse({
    apply: () => undefined,
    fetch: () => Promise.resolve(),
    dispatch: () => undefined,
  });
  expect(sse.size()).toBe(0);
  sse._reset();
  sse._reset();
  expect(sse.size()).toBe(0);
});

describe("createSse", () => {
  let applied: unknown[];
  let fetched: { url: string; zone: string }[];
  let cancelled: { url: string; zone: string }[];
  let dispatched: { event: string; detail: Record<string, unknown> }[];

  beforeEach(() => {
    document.body.innerHTML = "";
    applied = [];
    fetched = [];
    cancelled = [];
    dispatched = [];
  });

  function makeSse(
    source: EventSourceAdapter,
    visibility: VisibilityAdapter,
    over: {
      now?: () => number;
      pageUrl?: (el: Element) => string;
      cancel?: (request: { url: string; zone: string }) => void;
    } = {},
  ) {
    return createSse({
      apply: (raw) => applied.push(raw),
      fetch: (request) => {
        fetched.push(request);
        return Promise.resolve();
      },
      cancel: (request) => {
        cancelled.push(request);
        over.cancel?.(request);
      },
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      document,
      source,
      visibility,
      ...(over.now !== undefined ? { now: over.now } : {}),
      ...(over.pageUrl !== undefined ? { pageUrl: over.pageUrl } : {}),
    });
  }

  it("supports the websocket factory without optional platform seams", () => {
    let passed: unknown;
    const stream = createSse({
      apply: () => undefined,
      fetch: () => Promise.resolve(),
      dispatch: () => undefined,
      useWebSocket: true,
      websocket: (deps) => {
        passed = deps;
        return {
          up: vi.fn(),
          http: vi.fn(),
          renew: vi.fn(),
          tag: vi.fn(),
          name: "ws",
          open: (_url, sink) => {
            sink.opened();
            return { close: () => undefined };
          },
        };
      },
    });
    expect(passed).toEqual({ fallback: expect.any(Object), origin: undefined });
    stream._reset();
  });

  it("scan opens one stream per data-next-sse container and applies events", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    expect(opened).toHaveLength(1);
    expect(opened[0]!.url).toBe("/stream/");
    opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }]));
    expect(applied).toHaveLength(1);
  });

  it("scans data-next-stream and reports transport state", () => {
    document.body.innerHTML =
      '<div data-next-zone=""></div><div data-next-stream="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.open();
    opened[0]!.open();
    expect(opened[0]!.url).toBe("/stream/");
    expect(
      dispatched.filter((event) => event.event === "next:stream-state").slice(-2),
    ).toEqual([
      {
        event: "next:stream-state",
        detail: { url: "/stream/", transport: "sse", state: "connecting" },
      },
      {
        event: "next:stream-state",
        detail: { url: "/stream/", transport: "sse", state: "open" },
      },
    ]);
  });

  it("lets data-next-stream win on an element carrying both attributes", () => {
    document.body.innerHTML =
      '<div data-next-sse="/legacy/" data-next-stream="/stream/"></div>';
    const { adapter, opened } = mockSource();
    makeSse(adapter, manualVisibility()).scan(document);
    expect(opened.map((source) => source.url)).toEqual(["/stream/"]);
  });

  it("opens no stream for a container removed before its scan ran", () => {
    const root = document.createElement("div");
    root.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    makeSse(adapter, manualVisibility()).scan(root);
    expect(opened).toEqual([]);
  });

  it("ignores removed containers whose stream attribute disappeared before cleanup", async () => {
    const root = document.createElement("div");
    const owner = document.createElement("div");
    root.append(owner);
    document.body.append(root);
    makeSse(mockSource().adapter, manualVisibility()).scan(document);

    owner.setAttribute("data-next-stream", "/never-opened/");
    owner.remove();
    owner.removeAttribute("data-next-stream");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(owner.isConnected).toBe(false);
  });

  it("applies each event on behalf of the page the stream subscribed from", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const pages: string[] = [];
    const sse = createSse({
      apply: (_raw, page) => pages.push(page),
      fetch: () => Promise.resolve(),
      dispatch: () => undefined,
      document,
      source: adapter,
      visibility: manualVisibility(),
      pageUrl: () => "/inbox/",
    });
    sse.scan(document);
    opened[0]!.message(envelope([{ op: "meta", title: "Inbox (3)" }]));
    expect(pages).toEqual(["/inbox/"]);
    sse._reset();
  });

  it("scan opens a stream only once per url", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    sse.scan(document);
    expect(opened).toHaveLength(1);
    expect(sse.size()).toBe(1);
  });

  it("drops an event whose request_id is the client's own echo", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    sse.remember("r1");
    opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }], "r1"));
    expect(applied).toHaveLength(0);
    opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }], "r2"));
    expect(applied).toHaveLength(1);
  });

  it("tracks sequence before echo suppression and ignores duplicate frames", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ server_time: 1000, topics: { room: { epoch: "e", seq: 0 } } }),
    );
    sse.remember("own");
    opened[0]!.message(
      topicEnvelope([], "room", 1, 1100).replace(
        '"server_time":1100',
        '"server_time":1100,"request_id":"own"',
      ),
    );
    opened[0]!.message(topicEnvelope([], "room", 1, 1200));
    expect(applied).toEqual([]);
    expect(dispatched.some((event) => event.event === "next:stream-gap")).toBe(false);
  });

  it("does not resync when the next contiguous topic sequence arrives", () => {
    document.body.innerHTML =
      '<div data-next-sse="/stream/"></div><div data-next-zone="poll"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "e", seq: 0 } } }),
    );
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "poll" }], "room", 1));
    expect(fetched).toEqual([]);
    expect(dispatched.some((event) => event.event === "next:stream-gap")).toBe(false);
  });

  it("resyncs the page zones when the initial hello already has history", () => {
    document.body.innerHTML =
      '<div data-next-sse="/stream/"></div><div data-next-zone="poll"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "e", seq: 7 } } }),
    );
    expect(fetched).toEqual([{ url: "/", zone: "poll" }]);
    expect(dispatched.filter((event) => event.event === "next:stream-gap")).toEqual([
      { event: "next:stream-gap", detail: { topic: "room", from: 0, to: 7 } },
    ]);
  });

  it("limits initial revalidation to zones owned by the stream page", () => {
    document.body.innerHTML =
      '<div data-next-sse="/stream/" data-page="/"></div>' +
      '<div data-next-zone="main" data-page="/"></div>' +
      '<div data-next-zone="layer" data-page="/layer/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility(), {
      pageUrl: (el) => el.getAttribute("data-page") ?? "/",
    });
    sse.scan(document);
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "e", seq: 1 } } }),
    );
    expect(fetched).toEqual([{ url: "/", zone: "main" }]);
  });

  it("caps the zones seeded by an initial hello", () => {
    const zones = Array.from(
      { length: 100 },
      (_, index) => `<div data-next-zone="zone-${index}"></div>`,
    ).join("");
    document.body.innerHTML = `<div data-next-sse="/stream/"></div>${zones}`;
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "e", seq: 1 } } }),
    );
    expect(fetched).toHaveLength(1);
    expect(fetched[0]!.zone.split(",")).toHaveLength(64);
  });

  it("batches initial hello gaps for several topics into one page GET", () => {
    document.body.innerHTML =
      '<div data-next-sse="/stream/"></div><div data-next-zone="poll"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.control(
      "next-hello",
      JSON.stringify({
        topics: { "room:1": { epoch: "e", seq: 3 }, "room:2": { epoch: "e", seq: 7 } },
      }),
    );
    expect(fetched).toEqual([{ url: "/", zone: "poll" }]);
    expect(dispatched.filter((event) => event.event === "next:stream-gap")).toEqual([
      { event: "next:stream-gap", detail: { topic: "room:1", from: 0, to: 3 } },
      { event: "next:stream-gap", detail: { topic: "room:2", from: 0, to: 7 } },
    ]);
  });

  it("records zones from an echo before suppressing it", () => {
    document.body.innerHTML =
      '<div data-next-sse="/stream/"></div><div data-next-zone="poll"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "e", seq: 0 } } }),
    );
    sse.remember("own");
    opened[0]!.message(
      topicEnvelope([{ op: "refresh", zone: "poll" }], "room", 1).replace(
        '"server_time":1000',
        '"server_time":1000,"request_id":"own"',
      ),
    );
    opened[0]!.message(topicEnvelope([], "room", 3));
    expect(fetched).toEqual([{ url: "/", zone: "poll" }]);
    expect(applied).toHaveLength(1);
    expect((applied[0] as Record<string, unknown>).seq).toBe(3);
  });

  it("resyncs a batched page for each sequence gap and drops duplicate frames", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ server_time: 1000, topics: { room: { epoch: "e", seq: 4 } } }),
    );
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "a" }], "room", 5));
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "a" }], "room", 8));
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "a" }], "room", 8));
    opened[0]!.message(topicEnvelope([], "room", 10));
    expect(fetched).toEqual([
      { url: "/", zone: "a" },
      { url: "/", zone: "a" },
    ]);
    expect(cancelled).toEqual([{ url: "/", zone: "a" }]);
    expect(applied).toHaveLength(3);
    expect(dispatched.filter((event) => event.event === "next:stream-gap")).toEqual([
      { event: "next:stream-gap", detail: { topic: "room", from: 0, to: 4 } },
      { event: "next:stream-gap", detail: { topic: "room", from: 5, to: 8 } },
      { event: "next:stream-gap", detail: { topic: "room", from: 8, to: 10 } },
    ]);
  });

  it("cancels an in-flight resync that predates a later gap and issues a fresh one", async () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const requests: { url: string; zone: string }[] = [];
    const cancels: { url: string; zone: string }[] = [];
    const finishes: (() => void)[] = [];
    const sse = createSse({
      apply: () => undefined,
      fetch: (request) => {
        requests.push(request);
        return new Promise<void>((resolve) => finishes.push(resolve));
      },
      cancel: (request) => cancels.push(request),
      dispatch: () => undefined,
      document,
      source: adapter,
      visibility: manualVisibility(),
    });
    sse.scan(document);
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "alpha" }], "room", 1));
    opened[0]!.message(topicEnvelope([], "room", 3));
    opened[0]!.message(topicEnvelope([], "room", 5));
    expect(requests).toEqual([
      { url: "/", zone: "alpha" },
      { url: "/", zone: "alpha" },
    ]);
    expect(cancels).toEqual([requests[0]]);
    expect(cancels[0]).toBe(requests[0]);
    finishes[0]!();
    await Promise.resolve();
    document.querySelector("[data-next-sse]")!.remove();
    await Promise.resolve();
    expect(cancels).toEqual([requests[0], requests[1]]);
    expect(cancels[1]).toBe(requests[1]);
    finishes[1]!();
    sse._reset();
  });

  it("resyncs after a reconnect hello advances the last known sequence", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    const sse = makeSse(adapter, visibility);
    sse.scan(document);
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "a" }], "room", 4));
    visibility.setHidden(true);
    visibility.setHidden(false);
    opened[1]!.control(
      "next-hello",
      JSON.stringify({ server_time: 1200, topics: { room: { epoch: "e", seq: 9 } } }),
    );
    expect(fetched).toEqual([{ url: "/", zone: "a" }]);
    expect(
      dispatched.some(
        (event) => event.event === "next:stream-gap" && event.detail.to === 9,
      ),
    ).toBe(true);
  });

  it("resets the sequence cursor and resyncs when a reconnect hello has a new epoch", async () => {
    document.body.innerHTML =
      '<div data-next-sse="/stream/"></div><div data-next-zone="results"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    const sse = makeSse(adapter, visibility);
    sse.scan(document);
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "e", seq: 12 } } }),
    );
    await Promise.resolve();
    visibility.setHidden(true);
    visibility.setHidden(false);
    opened[1]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "f", seq: 2 } } }),
    );
    opened[1]!.message(
      topicEnvelope([{ op: "refresh", zone: "results" }], "room", 3, 1000, "f"),
    );
    expect(fetched).toEqual([
      { url: "/", zone: "results" },
      { url: "/", zone: "results" },
    ]);
    expect(applied).toHaveLength(1);
    expect(dispatched.filter((event) => event.event === "next:stream-gap")).toEqual([
      { event: "next:stream-gap", detail: { topic: "room", from: 0, to: 12 } },
      { event: "next:stream-gap", detail: { topic: "room", from: 12, to: 2 } },
    ]);
  });

  it("acknowledges skipped messages and resyncs only when a skip exposes a gap", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "e", seq: 0 } } }),
    );
    opened[0]!.control(
      "next-skip",
      JSON.stringify({ topic: "room", epoch: "e", seq: 1 }),
    );
    opened[0]!.control(
      "next-skip",
      JSON.stringify({ topic: "room", epoch: "e", seq: 1 }),
    );
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "results" }], "room", 2));
    opened[0]!.control(
      "next-skip",
      JSON.stringify({ topic: "room", epoch: "e", seq: 4 }),
    );
    opened[0]!.control(
      "next-skip",
      JSON.stringify({ topic: "room", epoch: "e", seq: 4 }),
    );
    expect(applied).toHaveLength(1);
    expect(fetched).toEqual([{ url: "/", zone: "results" }]);
    expect(dispatched.filter((event) => event.event === "next:stream-gap")).toEqual([
      { event: "next:stream-gap", detail: { topic: "room", from: 2, to: 4 } },
    ]);
  });

  it("reports malformed and unsupported stream control payloads", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.control("next-hello", "{");
    opened[0]!.control("next-hello", "null");
    opened[0]!.control("next-hello", JSON.stringify({ topics: [] }));
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { negative: -1, decimal: 1.5, valid: 0 } }),
    );
    opened[0]!.control("next-skip", "{");
    opened[0]!.control("next-skip", "null");
    opened[0]!.control("next-skip", JSON.stringify({ topic: 1, seq: 1 }));
    opened[0]!.control("next-skip", JSON.stringify({ topic: "valid", seq: "1" }));
    opened[0]!.control("unknown", "{}");
    opened[0]!.message(
      JSON.stringify({
        version: "v1",
        topic: "valid",
        seq: 1,
        ops: null,
        assets: [],
        form: null,
      }),
    );
    expect(dispatched.filter((event) => event.event === "partial:error")).toHaveLength(
      2,
    );
    expect(applied).toHaveLength(1);
  });

  it("does not resync a sequence jump when no zone has been bound yet", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "e", seq: 1 } } }),
    );
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "e", seq: 1 } } }),
    );
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "e", seq: 4 } } }),
    );
    expect(fetched).toEqual([]);
    expect(dispatched.filter((event) => event.event === "next:stream-gap")).toEqual([
      { event: "next:stream-gap", detail: { topic: "room", from: 0, to: 1 } },
      { event: "next:stream-gap", detail: { topic: "room", from: 1, to: 4 } },
    ]);
  });

  it("batches zones across streams for the same page and cancels on container teardown", async () => {
    document.body.innerHTML =
      '<div data-next-sse="/one/"></div><div data-next-sse="/two/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "alpha" }], "room", 1));
    opened[1]!.message(topicEnvelope([{ op: "refresh", zone: "beta" }], "room", 1));
    opened[0]!.message(topicEnvelope([], "room", 3));
    expect(fetched).toEqual([{ url: "/", zone: "alpha,beta" }]);
    document.querySelector('[data-next-sse="/one/"]')!.remove();
    document.querySelector('[data-next-sse="/two/"]')!.remove();
    await Promise.resolve();
    expect(cancelled).toContainEqual({ url: "/", zone: "alpha,beta" });
    expect(sse.size()).toBe(0);
  });

  it("transfers pending resync ownership to a later same-page connection", async () => {
    document.body.innerHTML = '<div data-next-sse="/one/"></div>';
    const { adapter, opened } = mockSource();
    const cancelled: { url: string; zone: string }[] = [];
    let finish: (() => void) | undefined;
    const sse = createSse({
      apply: () => undefined,
      fetch: () => new Promise<void>((resolve) => (finish = resolve)),
      cancel: (request) => cancelled.push(request),
      dispatch: () => undefined,
      document,
      source: adapter,
      visibility: manualVisibility(),
    });
    sse.scan(document);
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "alpha" }], "room", 1));
    opened[0]!.message(topicEnvelope([], "room", 3));
    const later = document.createElement("div");
    later.setAttribute("data-next-sse", "/two/");
    document.body.append(later);
    sse.scan(document);
    document.querySelector('[data-next-sse="/one/"]')!.remove();
    await Promise.resolve();
    expect(cancelled).toEqual([]);
    later.remove();
    await Promise.resolve();
    expect(cancelled).toEqual([{ url: "/", zone: "alpha" }]);
    finish!();
    await Promise.resolve();
  });

  it("drops superseded shared resync ownership without a cancel adapter", () => {
    document.body.innerHTML =
      '<div data-next-sse="/one/"></div><div data-next-sse="/two/"></div>';
    const { adapter, opened } = mockSource();
    const requests: { url: string; zone: string }[] = [];
    const sse = createSse({
      apply: () => undefined,
      fetch: (request) => {
        requests.push(request);
        return Promise.resolve();
      },
      dispatch: () => undefined,
      document,
      source: adapter,
      visibility: manualVisibility(),
    });
    sse.scan(document);
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "alpha" }], "room", 1));
    opened[1]!.message(topicEnvelope([{ op: "refresh", zone: "beta" }], "room", 1));
    opened[0]!.message(topicEnvelope([], "room", 3));
    opened[1]!.message(topicEnvelope([{ op: "refresh", zone: "gamma" }], "room", 2));
    opened[1]!.message(topicEnvelope([], "room", 4));
    expect(requests).toEqual([
      { url: "/", zone: "alpha,beta" },
      { url: "/", zone: "alpha,beta,gamma" },
    ]);
    sse._reset();
  });

  it("keeps a stream owner when its container moves within the document", async () => {
    document.body.innerHTML =
      '<section id="first"><div data-next-sse="/stream/"></div></section><section id="second"></section>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    const container = document.querySelector('[data-next-sse="/stream/"]')!;
    document.querySelector("#second")!.append(container);
    await Promise.resolve();
    expect(sse.size()).toBe(1);
    expect(opened[0]!.closed).toBe(false);
  });

  it("uses the stream fetch promise to clear completed resync ownership", async () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const cancelled: { url: string; zone: string }[] = [];
    const sse = createSse({
      apply: () => undefined,
      fetch: () => Promise.resolve(),
      cancel: (request) => cancelled.push(request),
      dispatch: () => undefined,
      document,
      source: adapter,
      visibility: manualVisibility(),
    });
    sse.scan(document);
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "alpha" }], "room", 1));
    opened[0]!.message(topicEnvelope([], "room", 3));
    await Promise.resolve();
    document.querySelector("[data-next-sse]")!.remove();
    await Promise.resolve();
    expect(cancelled).toEqual([]);
    expect(sse.size()).toBe(0);
  });

  it("does not let a stale resync completion release a newer page batch", async () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const requests: { url: string; zone: string }[] = [];
    const cancelled: { url: string; zone: string }[] = [];
    const releases: (() => void)[] = [];
    const sse = createSse({
      apply: () => undefined,
      fetch: (request) => {
        requests.push(request);
        return new Promise<void>((resolve) => releases.push(resolve));
      },
      cancel: (request) => cancelled.push(request),
      dispatch: () => undefined,
      document,
      source: adapter,
      visibility: manualVisibility(),
    });
    sse.scan(document);
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "alpha" }], "room", 1));
    opened[0]!.message(topicEnvelope([], "room", 3));
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "beta" }], "room", 4));
    opened[0]!.message(topicEnvelope([], "room", 6));
    releases[0]!();
    await Promise.resolve();
    document.querySelector("[data-next-sse]")!.remove();
    await Promise.resolve();
    expect(requests.map((request) => request.zone)).toEqual(["alpha", "alpha,beta"]);
    expect(cancelled.map((request) => request.zone)).toEqual(["alpha", "alpha,beta"]);
  });

  it("keeps a pending resync cancellable across a short visibility pause", async () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    const cancelled: { url: string; zone: string }[] = [];
    let finish: (() => void) | undefined;
    let currentTime = 0;
    const sse = createSse({
      apply: () => undefined,
      fetch: () => new Promise<void>((resolve) => (finish = resolve)),
      cancel: (request) => cancelled.push(request),
      dispatch: () => undefined,
      document,
      now: () => currentTime,
      source: adapter,
      visibility,
    });
    sse.scan(document);
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "alpha" }], "room", 1));
    opened[0]!.message(topicEnvelope([], "room", 3));
    visibility.setHidden(true);
    currentTime = 1000;
    visibility.setHidden(false);
    document.querySelector("[data-next-sse]")!.remove();
    await Promise.resolve();
    expect(cancelled).toEqual([{ url: "/", zone: "alpha" }]);
    finish!();
    await Promise.resolve();
  });

  it("replaces a pending resync when long-pause revalidation adds zones", () => {
    document.body.innerHTML =
      '<div data-next-sse="/stream/"></div><div data-next-zone="alpha"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    const fetchedRequests: { url: string; zone: string }[] = [];
    const cancelledRequests: { url: string; zone: string }[] = [];
    let currentTime = 0;
    const sse = createSse({
      apply: () => undefined,
      fetch: (request) => {
        fetchedRequests.push(request);
        return new Promise<void>(() => undefined);
      },
      cancel: (request) => cancelledRequests.push(request),
      dispatch: () => undefined,
      document,
      now: () => currentTime,
      pageUrl: () => "/",
      source: adapter,
      visibility,
    });
    sse.scan(document);
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "alpha" }], "room", 1));
    opened[0]!.message(topicEnvelope([], "room", 3));
    visibility.setHidden(true);
    document.body.insertAdjacentHTML(
      "beforeend",
      '<div data-next-sse="/second/"></div><div data-next-zone="beta"></div>',
    );
    sse.scan(document);
    currentTime = 5000;
    visibility.setHidden(false);
    expect(fetchedRequests).toEqual([
      { url: "/", zone: "alpha" },
      { url: "/", zone: "alpha,beta" },
    ]);
    expect(cancelledRequests).toEqual([{ url: "/", zone: "alpha" }]);
  });

  it("reuses the pending resync when long-pause revalidation has the same zones", () => {
    document.body.innerHTML =
      '<div data-next-sse="/stream/"></div><div data-next-zone="alpha"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    const fetchedRequests: { url: string; zone: string }[] = [];
    const cancelledRequests: { url: string; zone: string }[] = [];
    let currentTime = 0;
    const sse = createSse({
      apply: () => undefined,
      fetch: (request) => {
        fetchedRequests.push(request);
        return new Promise<void>(() => undefined);
      },
      cancel: (request) => cancelledRequests.push(request),
      dispatch: () => undefined,
      document,
      now: () => currentTime,
      pageUrl: () => "/",
      source: adapter,
      visibility,
    });
    sse.scan(document);
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "alpha" }], "room", 1));
    opened[0]!.message(topicEnvelope([], "room", 3));
    visibility.setHidden(true);
    currentTime = 5000;
    visibility.setHidden(false);
    expect(fetchedRequests).toEqual([{ url: "/", zone: "alpha" }]);
    expect(cancelledRequests).toEqual([]);
  });

  it("works with a document that has no browser window", () => {
    const detachedDocument = document.implementation.createHTMLDocument();
    const sse = createSse({
      apply: () => undefined,
      fetch: () => Promise.resolve(),
      dispatch: () => undefined,
      document: detachedDocument,
      source: mockSource().adapter,
      visibility: manualVisibility(),
    });
    sse._reset();
    expect(sse.size()).toBe(0);
  });

  it("keeps another page's stream resync independent", () => {
    document.body.innerHTML =
      '<div data-next-sse="/one/"></div><div data-next-sse="/two/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility(), {
      pageUrl: (el) => el.getAttribute("data-next-sse") ?? "/",
    });
    sse.scan(document);
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "alpha" }], "room", 1));
    opened[1]!.message(topicEnvelope([{ op: "refresh", zone: "beta" }], "room", 1));
    opened[0]!.message(topicEnvelope([], "room", 3));
    opened[1]!.message(topicEnvelope([], "room", 3));
    expect(fetched).toEqual([
      { url: "/one/", zone: "alpha" },
      { url: "/two/", zone: "beta" },
    ]);
  });

  it("closes one stream without resync ownership when a same-page sibling stays open", async () => {
    document.body.innerHTML =
      '<div data-next-sse="/one/"></div><div data-next-sse="/two/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    document.querySelector('[data-next-sse="/one/"]')!.remove();
    await Promise.resolve();
    expect(sse.size()).toBe(1);
    expect(opened[0]!.closed).toBe(true);
    expect(opened[1]!.closed).toBe(false);
  });

  it("samples server time and gives topic listeners isolated metadata", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const received: unknown[] = [];
    const sse = makeSse(adapter, manualVisibility(), { now: () => 1000 });
    sse.scan(document);
    const off = sse.on("room", "room:update", (detail, meta) =>
      received.push({ detail, meta, current: sse.meta() }),
    );
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ server_time: 1250, topics: { room: { epoch: "e", seq: 0 } } }),
    );
    opened[0]!.message(
      topicEnvelope(
        [{ op: "event", name: "room:update", detail: { count: 3 } }],
        "room",
        1,
        1300,
      ),
    );
    expect(sse.offset()).toBe(275);
    expect(sse.now()).toBe(1275);
    expect(received).toEqual([
      {
        detail: { count: 3 },
        meta: { topic: "room", epoch: "e", seq: 1, serverTime: 1300 },
        current: { topic: "room", epoch: "e", seq: 1, serverTime: 1300 },
      },
    ]);
    expect(sse.meta()).toBeUndefined();
    off();
  });

  it("keeps applying when a topic listener throws and normalizes non-object detail", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const received: unknown[] = [];
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    sse.on("room", "boom", () => {
      throw new Error("listener failure");
    });
    sse.on("room", "safe", (detail) => received.push(detail));
    opened[0]!.message(
      topicEnvelope(
        [
          { op: "event", name: "boom", detail: null },
          { op: "event", name: "safe", detail: "not an object" },
          { op: "event", name: "unmatched", detail: {} },
        ],
        "room",
        1,
      ),
    );
    expect(error).toHaveBeenCalledWith("[next] listener threw", expect.any(Error));
    expect(received).toEqual([{}]);
    expect(applied).toHaveLength(1);
  });

  it("keeps only five server clock samples and uses a server-time fallback", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility(), { now: () => 1000 });
    sse.scan(document);
    const received: StreamMeta[] = [];
    sse.on("room", "clock", (_detail, meta) => received.push(meta));
    for (let time = 100; time <= 700; time += 100) {
      opened[0]!.control(
        "next-hello",
        JSON.stringify({ server_time: 1000 + time, topics: {} }),
      );
    }
    opened[0]!.message(
      JSON.stringify({
        version: "v1",
        topic: "room",
        seq: 1,
        ops: [{ op: "event", name: "clock", detail: {} }],
        assets: [],
        form: null,
      }),
    );
    expect(sse.offset()).toBe(500);
    expect(sse.now()).toBe(1500);
    expect(received[0]?.serverTime).toBe(1500);
  });

  it("keeps only the last 25 request ids in the echo ring", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    for (let i = 0; i < 26; i += 1) sse.remember(`r${i}`);
    opened[0]!.message(envelope([], "r0"));
    expect(applied).toHaveLength(1);
    opened[0]!.message(envelope([], "r25"));
    expect(applied).toHaveLength(1);
  });

  it("still suppresses the oldest id while the ring sits exactly at the limit", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    for (let i = 0; i < 25; i += 1) sse.remember(`r${i}`);
    for (let i = 0; i < 25; i += 1) opened[0]!.message(envelope([], `r${i}`));
    expect(applied).toHaveLength(0);
  });

  it("evicts only the oldest id when one more overflows the ring", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    for (let i = 0; i < 25; i += 1) sse.remember(`r${i}`);
    sse.remember("r25");
    opened[0]!.message(envelope([], "r0"));
    expect(applied).toHaveLength(1);
    for (let i = 1; i < 26; i += 1) opened[0]!.message(envelope([], `r${i}`));
    expect(applied).toHaveLength(1);
  });

  it("a repeated id moves to the young end of the ring", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    for (let i = 0; i < 25; i += 1) sse.remember(`r${i}`);
    // Re-feeding the oldest id renews it, so the next overflow evicts r1 not r0.
    sse.remember("r0");
    sse.remember("r25");
    opened[0]!.message(envelope([], "r0"));
    expect(applied).toHaveLength(0);
    opened[0]!.message(envelope([], "r1"));
    expect(applied).toHaveLength(1);
  });

  it("drops the echo of a mutation whose id the wire fed into the ring", async () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    const envelopeBody = '{"version":"v1","ops":[],"assets":[],"form":null}';
    const sent: Headers[] = [];
    const wire = new Wire({
      fetch: async (_url, init) => {
        sent.push(new Headers(init.headers));
        return new Response(envelopeBody, {
          status: 200,
          headers: { "content-type": CONTENT_TYPE },
        });
      },
      navigate: () => undefined,
      dispatch: () => undefined,
      onEnvelope: async (raw) => ({ envelope: raw as Envelope, ok: true }),
      csrf: createCsrf({ mint: chunkModules.csrf }),
      rememberRequestId: (id) => sse.remember(id),
    });
    await wire.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    const echoed = sent[0]!.get(HEADER_REQUEST_ID) ?? "";
    opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }], echoed));
    expect(applied).toHaveLength(0);
    opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }], "other"));
    expect(applied).toHaveLength(1);
  });

  function tunneled(answer: (echo: () => void) => Promise<Response | string>) {
    document.body.innerHTML =
      '<div data-next-sse="/stream/"></div><div data-next-zone="poll"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    const wire = new Wire({
      fetch: () => Promise.reject(new Error("no HTTP fallback")),
      navigate: () => undefined,
      dispatch: () => undefined,
      onEnvelope: async (raw) => ({ envelope: raw as Envelope, ok: true }),
      csrf: createCsrf({ mint: chunkModules.csrf }),
      rememberRequestId: (id) => sse.remember(id),
      forgetRequestId: (id) => sse.forget(id),
      tunnel: (_target, init) => {
        const id = new Headers(init.headers).get(HEADER_REQUEST_ID) ?? "";
        return answer(() =>
          opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }], id)),
        );
      },
    });
    return wire.fetch({
      url: "/_next/form/u1/",
      method: "POST",
      uid: "u1",
      transport: "any",
    });
  }

  it("re-GETs once when a refusal follows an echo the ring dropped", async () => {
    await tunneled(async (echo) => {
      echo();
      return "too_big";
    });
    expect(applied).toHaveLength(0);
    expect(fetched).toEqual([{ url: "/", zone: "poll" }]);
  });

  it("lets an echo after a refusal apply itself without a re-GET", async () => {
    let echoed = (): void => undefined;
    await tunneled(async (echo) => {
      echoed = echo;
      return "internal";
    });
    echoed();
    expect(applied).toHaveLength(1);
    expect(fetched).toEqual([]);
  });

  it("neither re-GETs nor applies the echo of a delivered tunneled mutation", async () => {
    await tunneled(async (echo) => {
      echo();
      return new Response('{"version":"v1","ops":[],"assets":[],"form":null}', {
        status: 200,
        headers: { "content-type": CONTENT_TYPE },
      });
    });
    expect(applied).toHaveLength(0);
    expect(fetched).toEqual([]);
  });

  it("fires partial:error on a malformed event and keeps the stream alive", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.message("{not json");
    const err = dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.kind).toBe("parse");
    opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }]));
    expect(applied).toHaveLength(1);
  });

  it("evicts the connection and fires partial:error on a fatal error", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.error(true);
    expect(opened[0]!.closed).toBe(true);
    expect(sse.size()).toBe(0);
    const err = dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.kind).toBe("network");
  });

  it("leaves a transient error to the native reconnect without a toast", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.open();
    opened[0]!.error(false);
    expect(opened[0]!.closed).toBe(false);
    expect(sse.size()).toBe(1);
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(false);
    expect(
      dispatched
        .filter((d) => d.event === "next:stream-state")
        .map((d) => d.detail.state),
    ).toEqual(["connecting", "open", "connecting"]);
  });

  it("registers a container scanned while paused and opens it on resume", () => {
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    let clock = 0;
    const sse = makeSse(adapter, visibility, { now: () => clock });
    visibility.setHidden(true);
    // A background-tab mount registers a connection but opens no socket while paused.
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    sse.scan(document);
    expect(opened).toHaveLength(0);
    expect(sse.size()).toBe(1);
    clock = 5000;
    visibility.setHidden(false);
    expect(opened).toHaveLength(1);
    expect(opened[0]!.url).toBe("/stream/");
    expect(sse.size()).toBe(1);
  });

  it("does not reopen a fatally evicted url on resume", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    const sse = makeSse(adapter, visibility);
    sse.scan(document);
    opened[0]!.error(true);
    expect(sse.size()).toBe(0);
    visibility.setHidden(true);
    visibility.setHidden(false);
    expect(opened).toHaveLength(1);
    expect(sse.size()).toBe(0);
  });

  it("pauses in a background tab and reconnects with a re-GET of bound zones", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    let clock = 0;
    const sse = makeSse(adapter, visibility, { now: () => clock });
    sse.scan(document);
    opened[0]!.message(
      envelope([
        { op: "refresh", zone: "poll" },
        { op: "morph", target: { zone: "list" } },
      ]),
    );
    visibility.setHidden(true);
    expect(opened[0]!.closed).toBe(true);
    // A pause past the revalidate threshold re-GETs every bound zone.
    clock = 5000;
    visibility.setHidden(false);
    expect(opened).toHaveLength(2);
    expect(opened[1]!.closed).toBe(false);
    expect(sse.size()).toBe(1);
    expect(fetched).toEqual([{ url: "/", zone: "list,poll" }]);
  });

  it("re-GETs bound zones against the path and query open captured", () => {
    window.history.replaceState(null, "", "/catalog/?q=novel");
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    let clock = 0;
    const sse = makeSse(adapter, visibility, { now: () => clock });
    sse.scan(document);
    opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }]));
    visibility.setHidden(true);
    // The address bar moved while hidden, but resume targets the subscribed page.
    window.history.replaceState(null, "", "/catalog/?q=other");
    clock = 5000;
    visibility.setHidden(false);
    expect(fetched.find((f) => f.zone === "poll")?.url).toBe("/catalog/?q=novel");
    window.history.replaceState(null, "", "/");
  });

  it("captures the owning page through the pageUrl seam, not the address bar", () => {
    window.history.replaceState(null, "", "/modal/");
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    let clock = 0;
    const sse = makeSse(adapter, visibility, {
      now: () => clock,
      // The layer stack answers the host page while a modal holds the address bar.
      pageUrl: () => "/host/",
    });
    sse.scan(document);
    opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }]));
    visibility.setHidden(true);
    clock = 5000;
    visibility.setHidden(false);
    expect(fetched.find((f) => f.zone === "poll")?.url).toBe("/host/");
    window.history.replaceState(null, "", "/");
  });

  it("a resume re-GET passes zone intent and stamps no headers of its own", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    let clock = 0;
    const sse = makeSse(adapter, visibility, { now: () => clock });
    sse.scan(document);
    opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }]));
    visibility.setHidden(true);
    clock = 5000;
    visibility.setHidden(false);
    expect(fetched).toEqual([{ url: "/", zone: "poll" }]);
  });

  it("opens a stream when the scan root itself carries data-next-sse", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const el = document.querySelector("div")!;
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    // A replace patch scans the new wrapper element itself, not a descendant.
    sse.scan(el);
    expect(opened).toHaveLength(1);
    expect(opened[0]!.url).toBe("/stream/");
  });

  it("reconnects without a re-GET when the tab flickers briefly", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    let clock = 0;
    const sse = makeSse(adapter, visibility, { now: () => clock });
    sse.scan(document);
    opened[0]!.message(
      envelope([
        { op: "refresh", zone: "poll" },
        { op: "morph", target: { zone: "list" } },
      ]),
    );
    visibility.setHidden(true);
    clock = 500;
    visibility.setHidden(false);
    expect(opened).toHaveLength(2);
    expect(opened[1]!.closed).toBe(false);
    expect(sse.size()).toBe(1);
    expect(fetched).toHaveLength(0);
  });

  it("ignores callbacks from a source closed before resume", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    const sse = makeSse(adapter, visibility);
    sse.scan(document);
    visibility.setHidden(true);
    visibility.setHidden(false);
    opened[0]!.message(envelope([{ op: "event", name: "stale" }]));
    opened[0]!.open();
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "e", seq: 2 } } }),
    );
    opened[0]!.control("next-skip", JSON.stringify({ topic: "room", seq: 3 }));
    opened[0]!.error(true);
    expect(applied).toHaveLength(0);
    expect(sse.size()).toBe(1);
    expect(opened[1]!.closed).toBe(false);
  });

  it("applies a non-record event body without binding any zone", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.message("[1,2,3]");
    expect(applied).toEqual([[1, 2, 3]]);
  });

  it("binds no zone when the event ops field is not an array", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    let clock = 0;
    const sse = makeSse(adapter, visibility, { now: () => clock });
    sse.scan(document);
    opened[0]!.message('{"ops":"nope"}');
    visibility.setHidden(true);
    clock = 5000;
    visibility.setHidden(false);
    expect(fetched).toHaveLength(0);
  });

  it("skips a non-record op and an op without a zone when binding", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    let clock = 0;
    const sse = makeSse(adapter, visibility, { now: () => clock });
    sse.scan(document);
    opened[0]!.message(
      JSON.stringify({
        version: "v1",
        ops: [42, { op: "event", name: "ping" }, { op: "refresh", zone: "poll" }],
        assets: [],
        form: null,
      }),
    );
    visibility.setHidden(true);
    clock = 5000;
    visibility.setHidden(false);
    expect(fetched.map((f) => f.zone)).toEqual(["poll"]);
  });

  it("resume copies bound zones so a late event on the paused stream does not leak", async () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    let clock = 0;
    const sse = makeSse(adapter, visibility, { now: () => clock });
    sse.scan(document);
    opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }]));
    visibility.setHidden(true);
    clock = 5000;
    visibility.setHidden(false);
    fetched.length = 0;
    await Promise.resolve();
    // A stray event on the paused predecessor must not bind into the resumed copy.
    opened[0]!.message(envelope([{ op: "refresh", zone: "stale" }]));
    clock = 10000;
    visibility.setHidden(true);
    clock = 15000;
    visibility.setHidden(false);
    expect(fetched.map((f) => f.zone)).toEqual(["poll"]);
  });

  it("caps the bound registry so a long sleep does not re-GET an unbounded set", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    let clock = 0;
    const sse = makeSse(adapter, visibility, { now: () => clock });
    sse.scan(document);
    const ops = Array.from({ length: 100 }, (_, i) => ({
      op: "refresh",
      zone: `zone-${i}`,
    }));
    opened[0]!.message(envelope(ops));
    visibility.setHidden(true);
    clock = 5000;
    visibility.setHidden(false);
    expect(fetched).toHaveLength(1);
    expect(fetched[0]!.zone.split(",")).toHaveLength(64);
  });

  it("ignores a data-next-sse container with an empty url", () => {
    document.body.innerHTML = '<div data-next-sse=""></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    expect(opened).toHaveLength(0);
    expect(sse.size()).toBe(0);
  });

  it("_reset closes every connection and detaches visibility", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const detach = vi.fn();
    const sse = createSse({
      apply: (raw) => applied.push(raw),
      fetch: () => Promise.resolve(),
      dispatch: () => undefined,
      document,
      source: adapter,
      visibility: { hidden: () => false, onChange: () => detach },
    });
    sse.scan(document);
    sse._reset();
    expect(opened[0]!.closed).toBe(true);
    expect(sse.size()).toBe(0);
    expect(detach).toHaveBeenCalledOnce();
  });

  it("_reset empties the echo ring so a remembered id no longer suppresses", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    sse.remember("r1");
    opened[0]!.message(envelope([], "r1"));
    expect(applied).toHaveLength(0);
    sse._reset();
    sse.scan(document);
    opened[1]!.message(envelope([], "r1"));
    expect(applied).toHaveLength(1);
  });

  it("resyncs a real gap that arrives as the client's own echo", () => {
    document.body.innerHTML =
      '<div data-next-sse="/stream/"></div><div data-next-zone="poll"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "e", seq: 5 } } }),
    );
    fetched.length = 0;
    sse.remember("own");
    opened[0]!.message(
      topicEnvelope([], "room", 7).replace(
        '"server_time":1000',
        '"server_time":1000,"request_id":"own"',
      ),
    );
    expect(applied).toEqual([]);
    expect(fetched).toEqual([{ url: "/", zone: "poll" }]);
    expect(
      dispatched.filter((event) => event.event === "next:stream-gap"),
    ).toContainEqual({
      event: "next:stream-gap",
      detail: { topic: "room", from: 5, to: 7 },
    });
    opened[0]!.message(topicEnvelope([], "room", 8));
    expect(applied).toHaveLength(1);
  });

  it("resets the cursor when a live frame carries a new epoch", () => {
    document.body.innerHTML =
      '<div data-next-sse="/stream/"></div><div data-next-zone="poll"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "e", seq: 40 } } }),
    );
    fetched.length = 0;
    opened[0]!.message(topicEnvelope([], "room", 1, 1000, "f"));
    opened[0]!.message(topicEnvelope([], "room", 1, 1000, "f"));
    opened[0]!.message(topicEnvelope([], "room", 2, 1000, "f"));
    expect(applied).toHaveLength(2);
    expect(fetched).toEqual([{ url: "/", zone: "poll" }]);
    expect(dispatched.filter((event) => event.event === "next:stream-gap")).toEqual([
      { event: "next:stream-gap", detail: { topic: "room", from: 0, to: 40 } },
      { event: "next:stream-gap", detail: { topic: "room", from: 40, to: 1 } },
    ]);
  });

  function emptyHello() {
    document.body.innerHTML =
      '<div data-next-sse="/stream/"></div><div data-next-zone="poll"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "", seq: 0 } } }),
    );
    return { sse, source: opened[0]! };
  }

  const gaps = () => dispatched.filter((event) => event.event === "next:stream-gap");

  it("treats the first frame of a topic empty at hello as contiguous", () => {
    const { source } = emptyHello();
    source.message(topicEnvelope([], "room", 1, 1000, "fresh"));
    source.message(topicEnvelope([], "room", 2, 1000, "fresh"));
    expect(applied).toHaveLength(2);
    expect(fetched).toEqual([]);
    expect(gaps()).toEqual([]);
  });

  it("resyncs a topic empty at hello whose first frame skips ahead", () => {
    const { source } = emptyHello();
    source.message(topicEnvelope([], "room", 5, 1000, "fresh"));
    expect(applied).toHaveLength(1);
    expect(fetched).toEqual([{ url: "/", zone: "poll" }]);
    expect(gaps()).toEqual([
      { event: "next:stream-gap", detail: { topic: "room", from: 0, to: 5 } },
    ]);
  });

  it("suppresses the own echo that first publishes a topic without a GET", () => {
    const { sse, source } = emptyHello();
    sse.remember("own");
    source.message(
      topicEnvelope([], "room", 1, 1000, "fresh").replace(
        '"server_time":1000',
        '"server_time":1000,"request_id":"own"',
      ),
    );
    expect(applied).toEqual([]);
    expect(fetched).toEqual([]);
    expect(gaps()).toEqual([]);
  });

  it("resyncs a change between two published epochs", () => {
    const { source } = emptyHello();
    source.message(topicEnvelope([], "room", 1, 1000, "E"));
    source.message(topicEnvelope([], "room", 2, 1000, "G"));
    expect(applied).toHaveLength(2);
    expect(fetched).toEqual([{ url: "/", zone: "poll" }]);
    expect(gaps()).toEqual([
      { event: "next:stream-gap", detail: { topic: "room", from: 1, to: 2 } },
    ]);
  });

  it("resyncs a skip under a new epoch and reads a missing epoch as empty", () => {
    document.body.innerHTML =
      '<div data-next-sse="/stream/"></div><div data-next-zone="poll"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.control(
      "next-hello",
      JSON.stringify({ topics: { room: { epoch: "e", seq: 0 } } }),
    );
    opened[0]!.control(
      "next-skip",
      JSON.stringify({ topic: "room", epoch: "f", seq: 2 }),
    );
    opened[0]!.control("next-skip", JSON.stringify({ topic: "room", seq: 1 }));
    opened[0]!.control("next-skip", JSON.stringify({ topic: "room", seq: 2 }));
    expect(dispatched.filter((event) => event.event === "next:stream-gap")).toEqual([
      { event: "next:stream-gap", detail: { topic: "room", from: 0, to: 2 } },
      { event: "next:stream-gap", detail: { topic: "room", from: 2, to: 1 } },
    ]);
  });

  it("decodes only well-formed hello positions", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.control(
      "next-hello",
      JSON.stringify({
        topics: {
          bare: 3,
          nested: null,
          text: { epoch: "e", seq: "3" },
          negative: { epoch: "e", seq: -1 },
          plain: { seq: 2 },
          odd: { epoch: 7, seq: 4 },
        },
      }),
    );
    opened[0]!.message(topicEnvelope([], "plain", 3, 1000, ""));
    opened[0]!.message(topicEnvelope([], "odd", 5, 1000, ""));
    expect(dispatched.filter((event) => event.event === "next:stream-gap")).toEqual([
      { event: "next:stream-gap", detail: { topic: "plain", from: 0, to: 2 } },
      { event: "next:stream-gap", detail: { topic: "odd", from: 0, to: 4 } },
    ]);
    expect(applied).toHaveLength(2);
  });

  it("resolves the resync page at gap time, not the page captured at open", () => {
    document.body.innerHTML =
      '<div data-next-sse="/stream/"></div><div data-next-zone="list"></div>';
    const { adapter, opened } = mockSource();
    let page = "/items/?status=open";
    const sse = makeSse(adapter, manualVisibility(), { pageUrl: () => page });
    sse.scan(document);
    opened[0]!.message(topicEnvelope([], "room", 1));
    page = "/items/?status=closed";
    opened[0]!.message(topicEnvelope([{ op: "refresh", zone: "list" }], "room", 3));
    expect(fetched).toEqual([{ url: "/items/?status=closed", zone: "list" }]);
    expect(applied.length).toBe(2);
  });

  it("keeps the captured page when no stream owner is still in the document", () => {
    document.body.innerHTML =
      '<div data-next-sse="/stream/"></div><div data-next-zone="list"></div>';
    const { adapter, opened } = mockSource();
    let page = "/before/";
    const sse = makeSse(adapter, manualVisibility(), { pageUrl: () => page });
    sse.scan(document);
    opened[0]!.message(topicEnvelope([], "room", 1));
    document.querySelector("[data-next-sse]")!.remove();
    page = "/after/";
    opened[0]!.message(topicEnvelope([], "room", 3));
    expect(fetched).toEqual([{ url: "/before/", zone: "list" }]);
  });

  it("maps transport reconnects and clock pings onto the shared stream", () => {
    document.body.innerHTML = '<div data-next-stream="/stream/"></div>';
    let sink: TransportSink | undefined;
    let cursor: ReadonlyMap<string, Readonly<Cursor>> | undefined;
    const sse = createSse({
      apply: () => undefined,
      fetch: () => Promise.resolve(),
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      document,
      visibility: manualVisibility(),
      now: () => 1000,
      useWebSocket: true,
      websocket: () => ({
        up: vi.fn(),
        http: vi.fn(),
        renew: vi.fn(),
        tag: vi.fn(),
        name: "ws",
        open: (_url, opened, last) => {
          sink = opened;
          cursor = last;
          return { close: () => undefined };
        },
      }),
    });
    sse.scan(document);
    expect(cursor?.size).toBe(0);
    sink!.opened();
    sink!.closed("transient");
    sink!.opened();
    sink!.clock(1400);
    sink!.clock("late");
    expect(sse.offset()).toBe(400);
    expect(
      dispatched
        .filter((d) => d.event === "next:stream-state")
        .map((d) => d.detail.state),
    ).toEqual(["connecting", "open", "connecting", "open"]);
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(false);
    sse._reset();
  });

  it("applies a parsed websocket envelope as is and parses text through the hook", () => {
    document.body.innerHTML = '<div data-next-stream="/stream/"></div>';
    let sink: TransportSink | undefined;
    const parsed: string[] = [];
    const sse = createSse({
      apply: (raw) => applied.push(raw),
      parse: (body) => {
        parsed.push(body);
        return { version: "v1", ops: [], topic: "room", epoch: "e", seq: 2 };
      },
      fetch: () => Promise.resolve(),
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      document,
      visibility: manualVisibility(),
      useWebSocket: true,
      websocket: () => ({
        up: vi.fn(),
        http: vi.fn(),
        renew: vi.fn(),
        tag: vi.fn(),
        name: "ws",
        open: (_url, opened) => {
          sink = opened;
          return { close: () => undefined };
        },
      }),
    });
    sse.scan(document);
    const object = { version: "v1", ops: [], topic: "room", epoch: "e", seq: 1 };
    sink!.patches(object);
    expect(parsed).toEqual([]);
    expect(applied).toEqual([object]);
    sink!.patches("seq=2 custom");
    expect(parsed).toEqual(["seq=2 custom"]);
    expect(applied).toHaveLength(2);
    sse._reset();
  });

  it("closes for good on a terminal end event and reports its code", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    const sse = makeSse(adapter, visibility, { now: () => 0 });
    sse.scan(document);
    opened[0]!.open();
    opened[0]!.control("next-end", JSON.stringify({ code: "auth" }));
    expect(opened[0]!.closed).toBe(true);
    expect(sse.size()).toBe(0);
    expect(dispatched.filter((d) => d.event === "next:stream-state").at(-1)).toEqual({
      event: "next:stream-state",
      detail: { url: "/stream/", transport: "sse", state: "closed", code: "auth" },
    });
    expect(dispatched.filter((d) => d.event === "partial:error")).toEqual([
      { event: "partial:error", detail: { kind: "network", error: "auth" } },
    ]);
    visibility.setHidden(true);
    visibility.setHidden(false);
    expect(opened).toHaveLength(1);
  });

  it("still closes on an end event whose payload is malformed", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    sse.scan(document);
    opened[0]!.control("next-end", "{");
    expect(opened[0]!.closed).toBe(true);
    expect(sse.size()).toBe(0);
    expect(
      dispatched.filter((d) => d.event === "partial:error").map((d) => d.detail.kind),
    ).toEqual(["parse", "network"]);
    expect(dispatched.filter((d) => d.event === "partial:error")[1]!.detail.error).toBe(
      null,
    );
  });
});

describe("stream frame boundary", () => {
  it("reports a frame apply rejects and keeps applying later frames", () => {
    const events: { event: string; detail: Record<string, unknown> }[] = [];
    const applied: unknown[] = [];
    let sink: TransportSink | undefined;
    const transport: Transport = {
      name: "t",
      open: (_url, opened) => {
        sink = opened;
        return { close: () => undefined };
      },
    };
    document.body.innerHTML = '<div data-next-stream="/s/"></div>';
    const stream = createStream({
      apply: (raw) => {
        if (typeof (raw as Record<string, unknown>).version !== "string")
          throw new TypeError("missing version");
        applied.push(raw);
      },
      fetch: async () => undefined,
      dispatch: (event, detail) => events.push({ event, detail }),
      transport,
      visibility: manualVisibility(),
    });
    stream.scan(document);
    const bad = { topic: "a", seq: 1, epoch: "e", ops: [] };
    expect(() => sink!.patches(bad)).not.toThrow();
    expect(() => sink!.patches('{"topic":"a","seq":2,"epoch":"e"}')).not.toThrow();
    expect(events.filter((e) => e.event === "partial:error")).toEqual([
      {
        event: "partial:error",
        detail: {
          kind: "parse",
          body: JSON.stringify(bad),
          error: expect.any(TypeError),
        },
      },
      {
        event: "partial:error",
        detail: {
          kind: "parse",
          body: '{"topic":"a","seq":2,"epoch":"e"}',
          error: expect.any(TypeError),
        },
      },
    ]);
    sink!.patches({ version: "v1", topic: "a", seq: 3, epoch: "e", ops: [] });
    expect(applied).toHaveLength(1);
    stream._reset();
  });
});
