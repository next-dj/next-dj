// The SSE bridge opens an EventSource for each data-next-sse container and rides
// its events through the same apply pipeline as an HTTP response. An echo ring of
// own request ids drops self-echoes, and a background tab pauses then resumes.

import { defaultEventSource, defaultVisibility } from "./adapters";
import { asString, currentUrl, isRecord, matching } from "./protocol";
import type { PartialError } from "./protocol";

const SSE_ATTR = "data-next-sse";
// A visibility flip shorter than this reconnects the stream but skips the zone
// re-GETs, so flicking between tabs does not storm the server.
const RESUME_REVALIDATE_MS = 3000;
// The last 25 own request ids, matching the server-side echo window. Overflow is
// safe: a dropped id yields an extra refresh, not a break.
const ECHO_LIMIT = 25;
// The cap on zones a connection tracks, so a long background sleep does not make
// resume re-GET an unbounded backlog at once. A plain cap, not an LRU.
const MAX_BOUND = 64;
// The placeholder control before a real socket exists, and the lasting control of
// a connection registered while paused whose socket resume opens.
/* v8 ignore next */
const NOOP: SourceControl = { close: () => undefined };

/** The control returned from opening an EventSource, a listener and a teardown. */
export interface SourceControl {
  close(): void;
}

/** The EventSource seam over the native API, which jsdom does not model. */
export interface EventSourceAdapter {
  open(
    url: string,
    onMessage: (data: string) => void,
    // fatal on a CLOSED readyState, false while the native reconnect is in flight.
    onError: (fatal: boolean) => void,
  ): SourceControl;
}

/** The visibility seam over document.visibilityState and visibilitychange. */
export interface VisibilityAdapter {
  hidden(): boolean;
  onChange(listener: () => void): () => void;
}

/** The seams the bridge draws on, injectable so jsdom-blind pieces stay testable. */
export interface SseDeps {
  // A parsed envelope rides the same apply pipeline as an HTTP response.
  apply: (raw: unknown) => void;
  // The zone re-GET that revalidates bound zones on resume, intent not headers.
  fetch: (request: { url: string; zone: string }) => void;
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  document?: Document;
  source?: EventSourceAdapter;
  visibility?: VisibilityAdapter;
  // The owning page of a container, answered by the layer stack. Absent, reads
  // the address bar.
  pageUrl?: (el: Element) => string;
  // The monotonic clock the resume gate reads to measure the hidden span.
  now?: () => number;
}

/** The bridge handle, scanned per apply and fed own request ids by the wire. */
export interface Sse {
  // Open a stream for every data-next-sse container in a freshly inserted subtree.
  scan(root: ParentNode): void;
  // Feed an own request id into the echo ring so the matching event is dropped.
  remember(id: string): void;
  size(): number;
  _reset(): void;
}

interface Connection {
  url: string;
  control: SourceControl;
  // The page the stream subscribed from, captured at open so resume re-GETs the
  // bound zones against it, not the location at resume time.
  pageUrl: string;
  // The zones this stream addressed since subscribing, re-GET on resume.
  bound: Set<string>;
}

/** Build the SSE bridge over the given seams. */
export function createSse(deps: SseDeps): Sse {
  const doc = deps.document ?? document;
  const source = deps.source ?? defaultEventSource();
  const visibility = deps.visibility ?? defaultVisibility();
  const now = deps.now ?? (() => Date.now());
  const pageUrl = deps.pageUrl ?? (() => currentUrl(doc));
  // Keyed by url so a re-scan of a re-inserted container opens no second stream.
  const connections = new Map<string, Connection>();
  // A ring of the last ECHO_LIMIT own ids. A Map answers membership in constant
  // time and keeps insertion order, so its first key is the oldest.
  const echo = new Map<string, true>();
  let paused = false;
  // The clock at the last pause, so resume measures the hidden span.
  let pausedAt = 0;
  let detachVisibility: (() => void) | null = null;

  function remember(id: string): void {
    // Re-inserting renews the id, so its newest use decides its age.
    echo.delete(id);
    echo.set(id, true);
    if (echo.size > ECHO_LIMIT) {
      for (const oldest of echo.keys()) {
        echo.delete(oldest);
        break;
      }
    }
  }

  function isEcho(id: string | undefined): boolean {
    return id !== undefined && echo.has(id);
  }

  // Parse one event body and apply it, unless it is the client's own echo. A
  // parse error fires partial:error and the stream lives on.
  function onMessage(connection: Connection, data: string): void {
    let raw: unknown;
    try {
      raw = JSON.parse(data);
    } catch (error) {
      deps.dispatch("partial:error", {
        kind: "parse",
        body: data,
        error,
      } satisfies PartialError);
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

  // A fatal error (CLOSED, a 4xx) evicts the dead connection and fires
  // partial:error once. A transient error is left to the native reconnect, its
  // own back-off, so no toast spins on every retry.
  function onError(connection: Connection, fatal: boolean): void {
    if (!fatal) return;
    connection.control.close();
    connections.delete(connection.url);
    deps.dispatch("partial:error", {
      kind: "network",
      error: null,
    } satisfies PartialError);
  }

  // Open a stream against the resolved owning page, carrying over a paused
  // predecessor's bound zones so resume knows what to revalidate.
  function openConnection(url: string, page: string, previous?: Connection): void {
    if (connections.has(url)) return;
    const connection: Connection = {
      url,
      control: NOOP,
      pageUrl: page,
      // A private copy, never the predecessor's Set by reference, so a mutation
      // on the resumed stream cannot leak into a paused one.
      bound: new Set(previous?.bound),
    };
    connections.set(url, connection);
    // A container scanned while hidden registers but defers its socket to resume.
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

  // A background tab pauses every stream but keeps the bound registry, so resume
  // knows which zones to revalidate.
  function pause(): void {
    paused = true;
    pausedAt = now();
    for (const connection of connections.values()) connection.control.close();
  }

  // On returning visibility every paused connection reopens, and bound zones
  // re-GET only when the tab was hidden past the threshold.
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
      echo.clear();
      paused = false;
      pausedAt = 0;
      if (detachVisibility !== null) detachVisibility();
      detachVisibility = null;
    },
  };
}
