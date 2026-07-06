import { beforeEach, describe, expect, it, vi } from "vitest";
import { createSse } from "./sse";
import type { EventSourceAdapter, SourceControl, VisibilityAdapter } from "./sse";
import { manualVisibility } from "./test-doubles";
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
    over: { now?: () => number; pageUrl?: (el: Element) => string } = {},
  ) {
    return createSse({
      apply: (raw) => applied.push(raw),
      // The request object is recorded as handed over, so an assertion on its
      // exact shape proves the bridge passes intent and stamps no headers.
      fetch: (request) => fetched.push(request),
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      document,
      source,
      visibility,
      ...(over.now !== undefined ? { now: over.now } : {}),
      ...(over.pageUrl !== undefined ? { pageUrl: over.pageUrl } : {}),
    });
  }

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

  it("drops the echo of a mutation whose id the wire fed into the ring", async () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
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
    opened[0]!.error(false);
    expect(opened[0]!.closed).toBe(false);
    expect(sse.size()).toBe(1);
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(false);
  });

  it("registers a container scanned while paused and opens it on resume", () => {
    const { adapter, opened } = mockSource();
    const visibility = manualVisibility();
    let clock = 0;
    const sse = makeSse(adapter, visibility, { now: () => clock });
    visibility.setHidden(true);
    // A zone mounts in the background tab: its container registers a connection
    // but opens no socket while the stream is paused.
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    sse.scan(document);
    expect(opened).toHaveLength(0);
    expect(sse.size()).toBe(1);
    clock = 5000;
    visibility.setHidden(false);
    // Resume opens the deferred socket rather than stranding the zone's feed.
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
    // A pause longer than the revalidate threshold may have missed events, so
    // resume re-GETs every bound zone.
    clock = 5000;
    visibility.setHidden(false);
    expect(opened).toHaveLength(2);
    expect(opened[1]!.closed).toBe(false);
    expect(sse.size()).toBe(1);
    expect(fetched.map((f) => f.zone).sort()).toEqual(["list", "poll"]);
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
    // The address bar moved while the tab was hidden, so resume must still
    // target the page the stream subscribed from, query filters and all.
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
      // The layer stack answers the host page while a modal holds the address
      // bar, so the stream subscribes for the page that owns its container.
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
    // The wire stamps X-Next-Zone from the zone intent, so the exact request
    // shape carries url and zone and nothing else.
    expect(fetched).toEqual([{ url: "/", zone: "poll" }]);
  });

  it("opens a stream when the scan root itself carries data-next-sse", () => {
    document.body.innerHTML = '<div data-next-sse="/stream/"></div>';
    const el = document.querySelector("div")!;
    const { adapter, opened } = mockSource();
    const sse = makeSse(adapter, manualVisibility());
    // A replace patch scans the new wrapper element itself, so the container
    // binds even when it is the subtree root and not a descendant.
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
    // A momentary alt-tab reconnects the stream but skips the zone re-GET, so
    // flicking between tabs does not storm the server.
    clock = 500;
    visibility.setHidden(false);
    expect(opened).toHaveLength(2);
    expect(opened[1]!.closed).toBe(false);
    expect(sse.size()).toBe(1);
    expect(fetched).toHaveLength(0);
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

  it("resume copies bound zones so a late event on the paused stream does not leak", () => {
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
    // The paused predecessor still holds its message closure. A stray event on
    // it must not bind into the resumed connection, which carries its own copy.
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
    expect(fetched).toHaveLength(64);
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
