// The SSE bridge: a container carrying data-next-sse opens an EventSource, each
// next-patches event carries the same envelope as an HTTP response and rides
// the same apply pipeline. A ring buffer of the client's own X-Next-Request-Id
// values suppresses the echo of its own mutation, whose POST already brought the
// fresh zone. In a background tab the stream pauses (the connection closes), and
// on returning visibility the runtime reconnects and re-GETs the zones the
// stream addressed since subscribing, so events missed while hidden converge.
//
// The EventSource and the visibility signal live behind injectable seams, since
// jsdom models neither. A parse error of a single event fires partial:error and
// the stream lives on.

import { defaultEventSource, defaultVisibility } from "./adapters";
import { asString, currentUrl, isRecord, matching } from "./protocol";

const SSE_ATTR = "data-next-sse";
// A visibility flip shorter than this revalidates nothing: a momentary alt-tab
// reconnects the stream but skips the zone re-GETs, so flicking between tabs
// does not storm the server.
const RESUME_REVALIDATE_MS = 3000;
// The ring holds the last 25 own request ids, matching the server-side echo
// window. Overflow is safe: a dropped id yields an extra refresh, not a break.
const ECHO_LIMIT = 25;
// The cap on bound zones a connection tracks. Without it a long background sleep
// lets the registry grow unbounded, and resume would re-GET every accumulated
// zone at once, a thundering herd on the server. Past the cap a new zone is
// dropped, so resume revalidates a bounded slice rather than the whole backlog.
// This keeps the earliest bound zones, not the freshest: it is a plain cap, not
// an LRU, since any bounded slice serves the anti-thundering-herd goal.
const MAX_BOUND = 64;
// The placeholder control before a real socket exists: the transient between
// openConnection setting it and source.open returning, and the lasting control
// of a connection registered while paused whose socket resume opens. Its close
// is never reached: resume drops the paused registration from the map without
// closing it, and nothing closes a connection between the two open statements.
/* v8 ignore next */
const NOOP: SourceControl = { close: () => undefined };

// The control returned from opening an EventSource: a listener for next-patches
// events and a teardown. The native reconnect with the server retry lives
// inside the source, the runtime only closes on pause and reopens on resume.
export interface SourceControl {
  close(): void;
}

export interface EventSourceAdapter {
  open(
    url: string,
    onMessage: (data: string) => void,
    // fatal is true on a CLOSED readyState (a 4xx or a permanent failure the
    // browser will not retry), false while CONNECTING (the native reconnect is
    // in flight), so the bridge evicts the dead connection but leaves a
    // transient one to the server's own retry.
    onError: (fatal: boolean) => void,
  ): SourceControl;
}

// The visibility seam over document.visibilityState and the visibilitychange
// event. A background tab pauses the stream, a foreground tab resumes it.
export interface VisibilityAdapter {
  hidden(): boolean;
  onChange(listener: () => void): () => void;
}

export interface SseDeps {
  // The apply entry the wire shares: a parsed envelope rides the same pipeline
  // as an HTTP response. The raw string is parsed here so a malformed event
  // fires partial:error without poisoning the connection.
  apply: (raw: unknown) => void;
  // The zone re-GET used to revalidate bound zones on resume, the same shape
  // the refresh verb already uses. The wire stamps X-Next-Zone from the zone
  // intent, so the bridge passes intent, never headers.
  fetch: (request: { url: string; zone: string }) => void;
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  document?: Document;
  source?: EventSourceAdapter;
  visibility?: VisibilityAdapter;
  // The URL of the page that owns a data-next-sse container, answered by the
  // layer stack. Absent, the capture reads the address bar.
  pageUrl?: (el: Element) => string;
  // The monotonic clock the resume gate reads to measure how long the tab was
  // hidden. Injectable so tests drive the anti-storm threshold deterministically.
  now?: () => number;
}

export interface Sse {
  // Open the connection of every data-next-sse container in a freshly inserted
  // subtree, run from the mount registry after each apply and on ready.
  scan(root: ParentNode): void;
  // Feed an own request id into the echo ring, called by the wire on every
  // mutating request so the matching stream event is dropped silently.
  remember(id: string): void;
  // The number of open connections, for tests and the resync bookkeeping.
  size(): number;
  _reset(): void;
}

interface Connection {
  url: string;
  control: SourceControl;
  // The page the stream subscribed from, captured at open so resume re-GETs
  // the bound zones against it rather than the location at resume time.
  pageUrl: string;
  // The zones this stream addressed with operations since subscribing, the
  // registry re-GET on resume so events missed while hidden converge.
  bound: Set<string>;
}

export function createSse(deps: SseDeps): Sse {
  const doc = deps.document ?? document;
  const source = deps.source ?? defaultEventSource();
  const visibility = deps.visibility ?? defaultVisibility();
  const now = deps.now ?? (() => Date.now());
  const pageUrl = deps.pageUrl ?? (() => currentUrl(doc));
  // The connections keyed by url so a re-scan of a re-inserted container does
  // not open a second stream to the same endpoint.
  const connections = new Map<string, Connection>();
  // The own request ids, a ring of the last ECHO_LIMIT values.
  const echo: string[] = [];
  let paused = false;
  // The clock reading at the last pause, so resume measures the hidden span and
  // skips revalidation for a flicker.
  let pausedAt = 0;
  let detachVisibility: (() => void) | null = null;

  function remember(id: string): void {
    echo.push(id);
    if (echo.length > ECHO_LIMIT) echo.shift();
  }

  function isEcho(id: string | undefined): boolean {
    return id !== undefined && echo.includes(id);
  }

  // Parse one event body and apply it, unless it is the client's own echo. A
  // parse error fires partial:error and the stream lives on.
  function onMessage(connection: Connection, data: string): void {
    let raw: unknown;
    try {
      raw = JSON.parse(data);
    } catch (error) {
      deps.dispatch("partial:error", { kind: "parse", body: data, error });
      return;
    }
    if (isRecord(raw)) {
      if (isEcho(asString(raw.request_id))) return;
      recordBound(connection, raw);
    }
    deps.apply(raw);
  }

  // Register every zone the stream addressed, the set re-GET on resume.
  function recordBound(connection: Connection, raw: Record<string, unknown>): void {
    if (!Array.isArray(raw.ops)) return;
    for (const op of raw.ops) {
      if (!isRecord(op)) continue;
      const target = isRecord(op.target) ? op.target : undefined;
      const zone = asString(op.zone) ?? asString(target?.zone);
      if (zone === undefined) continue;
      if (connection.bound.size >= MAX_BOUND && !connection.bound.has(zone)) {
        continue;
      }
      connection.bound.add(zone);
    }
  }

  // A stream error either evicts a dead connection or leaves a transient one to
  // the native reconnect. A fatal error (CLOSED, a 4xx the browser will not
  // retry) closes and drops the connection so resume does not reopen the dead
  // url, then fires partial:error once. A transient error (CONNECTING, the
  // browser is already retrying with the server's retry interval) is left
  // alone: firing partial:error on every retry would spin a toast, and the
  // native retry is the back-off, so no extra reconnect surface is warranted.
  function onError(connection: Connection, fatal: boolean): void {
    if (!fatal) return;
    connection.control.close();
    connections.delete(connection.url);
    deps.dispatch("partial:error", { kind: "network", error: null });
  }

  // Open a stream to a url against the resolved owning page, carrying over
  // the bound zones of a paused predecessor so resume knows what to
  // revalidate and where.
  function openConnection(url: string, page: string, previous?: Connection): void {
    if (connections.has(url)) return;
    const connection: Connection = {
      url,
      control: NOOP,
      pageUrl: page,
      // An independent copy per connection, never the predecessor's Set by
      // reference, so a mutation on the resumed stream cannot leak back into a
      // paused one that was never re-GET.
      bound: new Set(previous?.bound),
    };
    connections.set(url, connection);
    // A container scanned while the tab is hidden registers its connection but
    // defers the socket to resume: an early return before the map write would
    // strand the background-mounted zone with a feed resume never reopens.
    if (paused) return;
    connection.control = source.open(
      url,
      (data) => onMessage(connection, data),
      (fatal) => onError(connection, fatal),
    );
  }

  function scan(root: ParentNode): void {
    for (const el of matching(root, `[${SSE_ATTR}]`)) {
      const url = el.getAttribute(SSE_ATTR);
      if (url !== null && url !== "") openConnection(url, pageUrl(el));
    }
  }

  // A background tab pauses every stream by closing its connection. The bound
  // registry survives so resume knows which zones to revalidate.
  function pause(): void {
    paused = true;
    pausedAt = now();
    for (const connection of connections.values()) connection.control.close();
  }

  // On returning visibility every paused connection reopens, and bound zones
  // re-GET only when the tab was hidden past the threshold, so a flicker
  // reconnects without storming the server. A polled zone may also refetch on
  // the same flip once its own interval elapsed — the morphs are idempotent.
  function resume(): void {
    paused = false;
    const revalidate = now() - pausedAt >= RESUME_REVALIDATE_MS;
    const pending = [...connections.values()];
    connections.clear();
    for (const previous of pending) {
      openConnection(previous.url, previous.pageUrl, previous);
      if (!revalidate) continue;
      for (const zone of previous.bound) {
        deps.fetch({ url: previous.pageUrl, zone });
      }
    }
  }

  detachVisibility = visibility.onChange(() => {
    if (visibility.hidden()) pause();
    else resume();
  });

  return {
    scan,
    remember,
    size: () => connections.size,
    _reset() {
      for (const connection of connections.values()) connection.control.close();
      connections.clear();
      echo.length = 0;
      paused = false;
      pausedAt = 0;
      if (detachVisibility !== null) detachVisibility();
      detachVisibility = null;
    },
  };
}
