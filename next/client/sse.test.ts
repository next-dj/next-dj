import { beforeEach, describe, expect, it, vi } from "vitest";
import { createSse } from "./sse";
import type { EventSourceAdapter, SourceControl, VisibilityAdapter } from "./sse";
import { Wire } from "./wire";
import { CONTENT_TYPE, HEADER_REQUEST_ID } from "./protocol";

interface MockSource {
  url: string;
  message(data: string): void;
  error(fatal: boolean): void;
  closed: boolean;
}

// A mock EventSource adapter: each open records a controllable handle the test
// drives directly, since jsdom does not implement EventSource.
function mockSource(): { adapter: EventSourceAdapter; opened: MockSource[] } {
  const opened: MockSource[] = [];
  const adapter: EventSourceAdapter = {
    open(url, onMessage, onError): SourceControl {
      const handle: MockSource = {
        url,
        message: onMessage,
        error: onError,
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

// A mock visibility adapter the test flips and fires by hand.
function mockVisibility(): {
  adapter: VisibilityAdapter;
  set(hidden: boolean): void;
} {
  let hidden = false;
  let listener: (() => void) | null = null;
  const adapter: VisibilityAdapter = {
    hidden: () => hidden,
    onChange(l) {
      listener = l;
      return () => {
        listener = null;
      };
    },
  };
  return {
    adapter,
    set(value) {
      hidden = value;
      listener?.();
    },
  };
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

describe("createSse", () => {
  let applied: unknown[];
  let fetched: { url: string; zone: string }[];
  let dispatched: { event: string; detail: Record<string, unknown> }[];

  beforeEach(() => {
    document.body.innerHTML = "";
    applied = [];
    fetched = [];
    dispatched = [];
  });

  function makeSse(
    source: EventSourceAdapter,
    visibility: VisibilityAdapter,
    now?: () => number,
  ) {
    return createSse({
      apply: (raw) => applied.push(raw),
      fetch: (request) => fetched.push({ url: request.url, zone: request.zone }),
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      document,
      source,
      visibility,
      ...(now !== undefined ? { now } : {}),
    });
  }

  it("scan opens one stream per data-next-sse container and applies events", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, mockVisibility().adapter);
    sse.scan(document);
    expect(opened).toHaveLength(1);
    expect(opened[0]!.url).toBe("/stream/");
    opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }]));
    expect(applied).toHaveLength(1);
  });

  it("scan opens a stream only once per url", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, mockVisibility().adapter);
    sse.scan(document);
    sse.scan(document);
    expect(opened).toHaveLength(1);
    expect(sse.size()).toBe(1);
  });

  it("drops an event whose request_id is the client's own echo", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, mockVisibility().adapter);
    sse.scan(document);
    sse.remember("r1");
    opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }], "r1"));
    expect(applied).toHaveLength(0);
    opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }], "r2"));
    expect(applied).toHaveLength(1);
  });

  it("keeps only the last 25 request ids in the echo ring", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, mockVisibility().adapter);
    sse.scan(document);
    for (let i = 0; i < 26; i += 1) sse.remember(`r${i}`);
    opened[0]!.message(envelope([], "r0"));
    expect(applied).toHaveLength(1);
    opened[0]!.message(envelope([], "r25"));
    expect(applied).toHaveLength(1);
  });

  it("drops the echo of a mutation whose id the wire fed into the ring", async () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, mockVisibility().adapter);
    sse.scan(document);
    const envelopeBody = '{"version":"v1","ops":[],"assets":[],"form":null}';
    const sent: { headers: Record<string, string> }[] = [];
    const wire = new Wire({
      fetch: async (_url, init) => {
        sent.push({ headers: init.headers as Record<string, string> });
        return new Response(envelopeBody, {
          status: 200,
          headers: { "content-type": CONTENT_TYPE },
        });
      },
      navigate: () => undefined,
      dispatch: () => undefined,
      onEnvelope: () => undefined,
      rememberRequestId: (id) => sse.remember(id),
    });
    await wire.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    const echoed = sent[0]!.headers[HEADER_REQUEST_ID];
    opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }], echoed));
    expect(applied).toHaveLength(0);
    opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }], "other"));
    expect(applied).toHaveLength(1);
  });

  it("fires partial:error on a malformed event and keeps the stream alive", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, mockVisibility().adapter);
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
    const sse = makeSse(adapter, mockVisibility().adapter);
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
    const sse = makeSse(adapter, mockVisibility().adapter);
    sse.scan(document);
    opened[0]!.error(false);
    expect(opened[0]!.closed).toBe(false);
    expect(sse.size()).toBe(1);
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(false);
  });

  it("does not reopen a fatally evicted url on resume", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = mockVisibility();
    const sse = makeSse(adapter, visibility.adapter);
    sse.scan(document);
    opened[0]!.error(true);
    expect(sse.size()).toBe(0);
    visibility.set(true);
    visibility.set(false);
    expect(opened).toHaveLength(1);
    expect(sse.size()).toBe(0);
  });

  it("pauses in a background tab and reconnects with a re-GET of bound zones", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = mockVisibility();
    let clock = 0;
    const sse = makeSse(adapter, visibility.adapter, () => clock);
    sse.scan(document);
    opened[0]!.message(
      envelope([
        { op: "refresh", zone: "poll" },
        { op: "morph", target: { zone: "list" } },
      ]),
    );
    visibility.set(true);
    expect(opened[0]!.closed).toBe(true);
    // A pause longer than the revalidate threshold may have missed events, so
    // resume re-GETs every bound zone.
    clock = 5000;
    visibility.set(false);
    expect(opened).toHaveLength(2);
    expect(opened[1]!.closed).toBe(false);
    expect(sse.size()).toBe(1);
    expect(fetched.map((f) => f.zone).sort()).toEqual(["list", "poll"]);
  });

  it("re-GETs bound zones against the path and query open captured", () => {
    window.history.replaceState(null, "", "/catalog/?q=novel");
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = mockVisibility();
    let clock = 0;
    const sse = makeSse(adapter, visibility.adapter, () => clock);
    sse.scan(document);
    opened[0]!.message(envelope([{ op: "refresh", zone: "poll" }]));
    visibility.set(true);
    // The address bar moved while the tab was hidden, so resume must still
    // target the page the stream subscribed from, query filters and all.
    window.history.replaceState(null, "", "/catalog/?q=other");
    clock = 5000;
    visibility.set(false);
    expect(fetched.find((f) => f.zone === "poll")?.url).toBe("/catalog/?q=novel");
    window.history.replaceState(null, "", "/");
  });

  it("reconnects without a re-GET when the tab flickers briefly", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = mockVisibility();
    let clock = 0;
    const sse = makeSse(adapter, visibility.adapter, () => clock);
    sse.scan(document);
    opened[0]!.message(
      envelope([
        { op: "refresh", zone: "poll" },
        { op: "morph", target: { zone: "list" } },
      ]),
    );
    visibility.set(true);
    // A momentary alt-tab reconnects the stream but skips the zone re-GET, so
    // flicking between tabs does not storm the server.
    clock = 500;
    visibility.set(false);
    expect(opened).toHaveLength(2);
    expect(opened[1]!.closed).toBe(false);
    expect(sse.size()).toBe(1);
    expect(fetched).toHaveLength(0);
  });

  it("applies a non-record event body without binding any zone", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, mockVisibility().adapter);
    sse.scan(document);
    opened[0]!.message("[1,2,3]");
    expect(applied).toEqual([[1, 2, 3]]);
  });

  it("binds no zone when the event ops field is not an array", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = mockVisibility();
    let clock = 0;
    const sse = makeSse(adapter, visibility.adapter, () => clock);
    sse.scan(document);
    opened[0]!.message('{"ops":"nope"}');
    visibility.set(true);
    clock = 5000;
    visibility.set(false);
    expect(fetched).toHaveLength(0);
  });

  it("skips a non-record op and an op without a zone when binding", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const visibility = mockVisibility();
    let clock = 0;
    const sse = makeSse(adapter, visibility.adapter, () => clock);
    sse.scan(document);
    opened[0]!.message(
      JSON.stringify({
        version: "v1",
        ops: [42, { op: "event", name: "ping" }, { op: "refresh", zone: "poll" }],
        assets: [],
        form: null,
      }),
    );
    visibility.set(true);
    clock = 5000;
    visibility.set(false);
    expect(fetched.map((f) => f.zone)).toEqual(["poll"]);
  });

  it("ignores a data-next-sse container with an empty url", () => {
    document.body.innerHTML = '<div data-next-sse=""></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, mockVisibility().adapter);
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
      fetch: () => undefined,
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
});
