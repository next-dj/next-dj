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
import { HEADER_ZONE } from "./wire";

const SSE_ATTR = "data-next-sse";
// The ring holds the last 25 own request ids, matching the server-side echo
// window. Overflow is safe: a dropped id yields an extra refresh, not a break.
const ECHO_LIMIT = 25;
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
  // the refresh verb already uses.
  fetch: (request: {
    url: string;
    zone: string;
    headers?: Record<string, string>;
  }) => void;
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  document?: Document;
  source?: EventSourceAdapter;
  visibility?: VisibilityAdapter;
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
  // The zones this stream addressed with operations since subscribing, the
  // registry re-GET on resume so events missed while hidden converge.
  bound: Set<string>;
}

export function createSse(deps: SseDeps): Sse {
  const doc = deps.document ?? document;
  const source = deps.source ?? defaultEventSource();
  const visibility = deps.visibility ?? defaultVisibility();
  // The connections keyed by url so a re-scan of a re-inserted container does
  // not open a second stream to the same endpoint.
  const connections = new Map<string, Connection>();
  // The own request ids, a ring of the last ECHO_LIMIT values.
  const echo: string[] = [];
  let paused = false;
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
      deps.dispatch("partial:error", { status: 0, body: data, error });
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
      if (zone !== undefined) connection.bound.add(zone);
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
    deps.dispatch("partial:error", { status: 0, body: "", error: null });
  }

  // Open a stream to a url, carrying over the bound zones of a paused
  // predecessor so resume knows what to revalidate.
  function openConnection(url: string, bound?: Set<string>): void {
    if (connections.has(url) || paused) return;
    const connection: Connection = { url, control: NOOP, bound: bound ?? new Set() };
    connections.set(url, connection);
    connection.control = source.open(
      url,
      (data) => onMessage(connection, data),
      (fatal) => onError(connection, fatal),
    );
  }

  function scan(root: ParentNode): void {
    for (const el of Array.from(root.querySelectorAll(`[${SSE_ATTR}]`))) {
      const url = el.getAttribute(SSE_ATTR);
      if (url !== null && url !== "") openConnection(url);
    }
  }

  // A background tab pauses every stream by closing its connection. The bound
  // registry survives so resume knows which zones to revalidate.
  function pause(): void {
    paused = true;
    for (const connection of connections.values()) connection.control.close();
  }

  // On returning visibility every paused connection reopens and its bound zones
  // are re-GET with their own cookies, so events missed while hidden converge.
  function resume(): void {
    paused = false;
    const pending = [...connections.values()];
    connections.clear();
    for (const previous of pending) {
      openConnection(previous.url, previous.bound);
      for (const zone of previous.bound) {
        deps.fetch({
          url: doc.location.pathname,
          zone,
          headers: { [HEADER_ZONE]: zone },
        });
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
      if (detachVisibility !== null) detachVisibility();
      detachVisibility = null;
    },
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}
