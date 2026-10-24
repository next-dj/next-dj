// The transport-neutral stream layer owns cursors, resync, events, and visibility.

import { defaultVisibility } from "./adapters";
import {
  ATTR_STREAM,
  ATTR_SSE,
  ATTR_ZONE,
  ECHO_LIMIT,
  EPOCH,
  SEQ,
  SERVER_TIME,
  TOPIC,
  asString,
  currentUrl,
  isRecord,
  matching,
} from "./protocol";
import type { PartialError } from "./protocol";

// A visibility flip shorter than this reconnects the stream but skips the zone
// re-GETs, so flicking between tabs does not storm the server.
const RESUME_REVALIDATE_MS = 3000;
// The cap on zones a connection tracks, so a long background sleep does not make
// resume re-GET an unbounded backlog at once. A plain cap, not an LRU.
const MAX_BOUND = 64;
// The placeholder control before a real socket exists, and the lasting control of
// a connection registered while paused whose socket resume opens.
/* v8 ignore next */
const NOOP: TransportControl = { close: () => undefined };
const STREAM_SELECTOR = `[${ATTR_STREAM}], [${ATTR_SSE}]`;

/** A patches frame body, raw text for the parse hook or an already decoded object. */
export type PatchesData = string | Record<string, unknown>;

/** The handle returned by a transport and closed on teardown. */
export interface TransportControl {
  close(): void;
}

/** Messages a transport delivers to the shared stream protocol. */
export interface TransportSink {
  patches(this: void, data: PatchesData): void;
  hello(this: void, data: string): void;
  skip(this: void, data: string): void;
  // A terminal frame, the server denied or revoked access and the stream stays closed.
  end(this: void, data: string): void;
  clock(this: void, serverTime: unknown): void;
  opened(this: void): void;
  closed(this: void, reason: "fatal" | "transient"): void;
}

/** A transport owns framing and connection setup, while stream state stays shared. */
export interface Transport {
  readonly name: string;
  open(
    url: string,
    sink: TransportSink,
    cursor?: ReadonlyMap<string, Readonly<Cursor>>,
  ): TransportControl;
}

/** The visibility seam over document.visibilityState and visibilitychange. */
export interface VisibilityAdapter {
  hidden(): boolean;
  onChange(listener: () => void): () => void;
}

/** The seams the bridge draws on, injectable so jsdom-blind pieces stay testable. */
export interface StreamDeps {
  // A parsed envelope goes through the same apply pipeline as an HTTP response.
  apply: (raw: unknown, page: string) => void;
  parse?: (body: string) => unknown;
  // The zone re-GET that revalidates bound zones on resume, intent not headers.
  fetch: (request: { url: string; zone: string }) => Promise<void>;
  cancel?: (request: { url: string; zone: string }) => void;
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  document?: Document;
  transport: Transport;
  visibility?: VisibilityAdapter;
  // The owning page of a container from the layer stack, else the address bar.
  pageUrl?: (el: Element) => string;
  // The monotonic clock the resume gate reads to measure the hidden span.
  now?: () => number;
}

/** The bridge handle, scanned per apply and fed own request ids by the wire. */
export interface Stream {
  // Open a stream for every stream container in a freshly inserted subtree.
  scan(root: ParentNode): void;
  // Feed an own request id into the echo ring so the matching event is dropped.
  remember(id: string): void;
  size(): number;
  /** Estimate server epoch time in milliseconds from recent stream frames. */
  now(): number;
  /** Median server clock offset in milliseconds. */
  offset(): number;
  /** Listen to one named event from one topic without changing the global event detail. */
  on(
    topic: string,
    name: string,
    callback: (detail: Record<string, unknown>, meta: StreamMeta) => void,
  ): () => void;
  /** Metadata for the event currently being delivered to a callback. */
  meta(): StreamMeta | undefined;
  _reset(): void;
}

export type StreamFactory = (deps: StreamDeps) => Stream;

// A counter that starts again gets a new epoch, so a seq is unique only per epoch.
export type Cursor = [epoch: string, seq: number];

interface Connection {
  url: string;
  control: TransportControl;
  state: "connecting" | "open" | "closed";
  // The owning page, captured at open and refreshed from the owners on a gap resync.
  pageUrl: string;
  // The page zones checked at first hello, plus zones addressed since subscribing.
  bound: Set<string>;
  owners: Set<Element>;
  cursor: Map<string, Cursor>;
  resync?: Resync;
}

interface Resync {
  request: { url: string; zone: string };
  owners: Set<Connection>;
}

/** Metadata carried beside a topic event, never merged into its detail. */
export interface StreamMeta {
  topic: string;
  epoch: string;
  seq: number;
  serverTime: number;
}

interface Listener {
  topic: string;
  name: string;
  callback: (detail: Record<string, unknown>, meta: StreamMeta) => void;
}

function isSeq(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

/** Build the transport-independent stream state machine. */
export function createStream(deps: StreamDeps): Stream {
  const doc = deps.document ?? document;
  const transport = deps.transport;
  const visibility = deps.visibility ?? defaultVisibility();
  const cancelRequest = deps.cancel ?? (() => undefined);
  const now = deps.now ?? (() => Date.now());
  const pageUrl = deps.pageUrl ?? (() => currentUrl(doc));
  // Keyed by url so a re-scan of a re-inserted container opens no second stream.
  const connections = new Map<string, Connection>();
  // A ring of the last ECHO_LIMIT own ids. A Set answers membership in constant
  // time and keeps insertion order, so its first value is the oldest.
  const echo = new Set<string>();
  let paused = false;
  // The clock at the last pause, so resume measures the hidden span.
  let pausedAt = 0;
  let detachVisibility: (() => void) | null = null;
  const offsets: number[] = [];
  const listeners = new Set<Listener>();
  let currentMeta: StreamMeta | undefined;
  const ElementType = doc.defaultView?.Element;
  const MutationObserverType = doc.defaultView?.MutationObserver;
  const observer =
    ElementType === undefined || MutationObserverType === undefined
      ? undefined
      : new MutationObserverType((records) => {
          for (const record of records) {
            for (const node of record.removedNodes) {
              if (!(node instanceof ElementType)) continue;
              const removed = Array.from(node.querySelectorAll(STREAM_SELECTOR));
              if (node.matches(STREAM_SELECTOR)) removed.unshift(node);
              for (const owner of removed) {
                if (owner.isConnected) continue;
                const url = streamUrl(owner);
                /* v8 ignore next */
                if (url === null) continue;
                const connection = connections.get(url);
                connection?.owners.delete(owner);
                if (connection?.owners.size === 0) {
                  detachConnection(connection);
                }
              }
            }
          }
        });
  observer?.observe(doc.documentElement, { childList: true, subtree: true });

  function offset(): number {
    if (offsets.length === 0) return 0;
    const sorted = [...offsets].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    /* v8 ignore next */
    const upper = sorted[middle] ?? 0;
    if (sorted.length % 2 !== 0) return upper;
    /* v8 ignore next */
    return ((sorted[middle - 1] ?? upper) + upper) / 2;
  }

  function sampleTime(serverTime: unknown): void {
    if (typeof serverTime !== "number" || !Number.isFinite(serverTime)) return;
    offsets.push(serverTime - now());
    if (offsets.length > 5) offsets.shift();
  }

  function cancelResync(connection: Connection): void {
    if (connection.resync === undefined) return;
    const pending = connection.resync;
    delete connection.resync;
    pending.owners.delete(connection);
    if (pending.owners.size === 0) cancelRequest(pending.request);
  }

  function setState(
    connection: Connection,
    state: Connection["state"],
    code?: string,
  ): void {
    if (connection.state === state) return;
    connection.state = state;
    deps.dispatch("next:stream-state", {
      url: connection.url,
      transport: transport.name,
      state,
      code,
    });
  }

  function isCurrent(connection: Connection): boolean {
    return connections.get(connection.url) === connection;
  }

  function peersOf(page: string): Connection[] {
    return [...connections.values()].filter((peer) => peer.pageUrl === page);
  }

  // The bound zones of a page batch, sorted so equal batches compare equal.
  function zonesOf(peers: Connection[]): string {
    return [...new Set(peers.flatMap((peer) => [...peer.bound]))].sort().join(",");
  }

  function detachConnection(connection: Connection, code?: string): void {
    connection.control.close();
    setState(connection, "closed", code);
    connections.delete(connection.url);
    const pagePeers = peersOf(connection.pageUrl);
    if (pagePeers.length === 0) {
      cancelResync(connection);
    } else if (connection.resync !== undefined) {
      const pending = connection.resync;
      pending.owners.delete(connection);
      // Existing page peers share a batch; a later peer has no owner yet.
      for (const peer of pagePeers) {
        if (peer.resync === undefined) {
          peer.resync = pending;
          pending.owners.add(peer);
        }
      }
      delete connection.resync;
    }
  }

  function trackResync(
    request: { url: string; zone: string },
    owners: Connection[],
  ): void {
    const existing = owners
      .map((owner) => owner.resync)
      .find(
        (pending) =>
          pending?.request.url === request.url && pending.request.zone === request.zone,
      );
    if (existing !== undefined) {
      for (const owner of owners) {
        owner.resync = existing;
        existing.owners.add(owner);
      }
      return;
    }
    const pendingResync: Resync = { request, owners: new Set(owners) };
    for (const owner of owners) owner.resync = pendingResync;
    const pending = deps.fetch(request);
    const clear = (): void => {
      for (const owner of pendingResync.owners) delete owner.resync;
      pendingResync.owners.clear();
    };
    void pending.then(clear, clear);
  }

  function resyncMany(
    connection: Connection,
    gaps: { topic: string; from: number; to: number }[],
  ): void {
    // A same-page navigation can change the query, so the GET follows the owners.
    for (const candidate of connections.values()) {
      const owner = [...candidate.owners].find((el) => el.isConnected);
      if (owner !== undefined) candidate.pageUrl = pageUrl(owner);
    }
    const peers = peersOf(connection.pageUrl);
    const zone = zonesOf(peers);
    if (zone !== "") {
      // A GET already in flight may predate this gap, so the latest one wins.
      for (const peer of peers) cancelResync(peer);
      trackResync({ url: connection.pageUrl, zone }, peers);
    }
    for (const gap of gaps) deps.dispatch("next:stream-gap", gap);
  }

  // False for a duplicate. An empty epoch is a never-published topic, not a restart.
  function advance(
    connection: Connection,
    topic: string,
    epoch: string,
    seq: number,
  ): boolean {
    const previous = connection.cursor.get(topic);
    if (previous?.[0] === epoch && seq <= previous[1]) return false;
    connection.cursor.set(topic, [epoch, seq]);
    if (
      previous !== undefined &&
      (seq > previous[1] + 1 || (previous[0] !== epoch && previous[0] !== ""))
    )
      resyncMany(connection, [{ topic, from: previous[1], to: seq }]);
    return true;
  }

  function control(data: string): Record<string, unknown> | undefined {
    let raw: unknown;
    try {
      raw = JSON.parse(data);
    } catch (error) {
      deps.dispatch("partial:error", {
        kind: "parse",
        body: data,
        error,
      } satisfies PartialError);
      return undefined;
    }
    return isRecord(raw) ? raw : undefined;
  }

  function onHello(connection: Connection, data: string): void {
    const raw = control(data);
    if (raw === undefined) return;
    sampleTime(raw[SERVER_TIME]);
    const topics = raw.topics;
    if (!isRecord(topics)) return;
    const gaps: { topic: string; from: number; to: number }[] = [];
    for (const [topic, position] of Object.entries(topics)) {
      if (!isRecord(position)) continue;
      const seq = position[SEQ];
      if (!isSeq(seq)) continue;
      const epoch = asString(position[EPOCH]) ?? "";
      const previous = connection.cursor.get(topic);
      if (previous?.[0] === epoch && previous[1] === seq) continue;
      connection.cursor.set(topic, [epoch, seq]);
      if (previous !== undefined || seq > 0)
        gaps.push({ topic, from: previous?.[1] ?? 0, to: seq });
    }
    if (gaps.length > 0) resyncMany(connection, gaps);
  }

  function onSkip(connection: Connection, data: string): void {
    const raw = control(data);
    const topic = asString(raw?.[TOPIC]);
    const seq = raw?.[SEQ];
    if (topic !== undefined && isSeq(seq))
      advance(connection, topic, asString(raw?.[EPOCH]) ?? "", seq);
  }

  function remember(id: string): void {
    // Re-inserting renews the id, so its newest use decides its age.
    echo.delete(id);
    echo.add(id);
    if (echo.size <= ECHO_LIMIT) return;
    // The set always holds one entry above the limit, so oldest is never undefined.
    const [oldest] = echo;
    /* v8 ignore next */
    if (oldest !== undefined) echo.delete(oldest);
  }

  function isEcho(id: string | undefined): boolean {
    return id !== undefined && echo.has(id);
  }

  // Parse a text body or take an object frame as is, then apply it unless it is the
  // client's own echo. A parse error fires partial:error and the stream lives on.
  function onMessage(connection: Connection, data: PatchesData): void {
    let raw: unknown = data;
    if (typeof data === "string")
      try {
        raw = (deps.parse ?? JSON.parse)(data);
      } catch (error) {
        deps.dispatch("partial:error", {
          kind: "parse",
          body: data,
          error,
        } satisfies PartialError);
        return;
      }
    if (isRecord(raw)) {
      sampleTime(raw[SERVER_TIME]);
      const topic = asString(raw[TOPIC]);
      const seq = raw[SEQ];
      if (topic !== undefined && isSeq(seq)) {
        const epoch = asString(raw[EPOCH]) ?? "";
        recordBound(connection, raw);
        // The gap check runs first, so an own echo cannot hide a lost frame.
        if (!advance(connection, topic, epoch, seq) || isEcho(asString(raw.request_id)))
          return;
        currentMeta = {
          topic,
          epoch,
          seq,
          serverTime:
            typeof raw[SERVER_TIME] === "number" ? raw[SERVER_TIME] : now() + offset(),
        };
        try {
          for (const op of Array.isArray(raw.ops) ? raw.ops : []) {
            if (!isRecord(op) || op.op !== "event" || typeof op.name !== "string")
              continue;
            const detail = isRecord(op.detail) ? op.detail : {};
            for (const listener of listeners) {
              if (listener.topic === topic && listener.name === op.name) {
                try {
                  listener.callback(detail, currentMeta);
                } catch (error) {
                  console.error("[next] listener threw", error);
                }
              }
            }
          }
          deps.apply(raw, connection.pageUrl);
        } finally {
          currentMeta = undefined;
        }
        return;
      } else if (isEcho(asString(raw.request_id))) {
        return;
      }
      recordBound(connection, raw);
    }
    deps.apply(raw, connection.pageUrl);
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

  // A fatal error evicts the connection and fires partial:error once, a transient one
  // is left to the native reconnect so no toast spins on every retry.
  function onError(connection: Connection, code?: string): void {
    detachConnection(connection, code);
    deps.dispatch("partial:error", {
      kind: "network",
      error: code ?? null,
    } satisfies PartialError);
  }

  // Open a stream against the resolved owning page, carrying over a paused
  // predecessor's bound zones so resume knows what to revalidate.
  function openConnection(
    url: string,
    page: string,
    previous?: Connection,
    owner?: Element,
  ): Connection {
    const connection: Connection = {
      url,
      control: NOOP,
      state: "closed",
      pageUrl: page,
      // A private copy, never the predecessor's Set by reference, so a mutation
      // on the resumed stream cannot leak into a paused one.
      bound: new Set(previous?.bound),
      owners: new Set(previous?.owners),
      cursor: new Map(previous?.cursor),
    };
    for (const zone of matching(doc, `[${ATTR_ZONE}]`)) {
      if (pageUrl(zone) !== page) continue;
      if (connection.bound.size >= MAX_BOUND) break;
      const name = zone.getAttribute(ATTR_ZONE);
      if (name) connection.bound.add(name);
    }
    if (owner !== undefined) connection.owners.add(owner);
    connections.set(url, connection);
    // A container scanned while hidden registers but defers its socket to resume.
    if (paused) return connection;
    setState(connection, "connecting");
    const live =
      <T>(handle: (value: T) => void) =>
      (value: T): void => {
        if (!paused && isCurrent(connection)) handle(value);
      };
    connection.control = transport.open(
      url,
      {
        patches: live((data: PatchesData) => onMessage(connection, data)),
        hello: live((data: string) => onHello(connection, data)),
        skip: live((data: string) => onSkip(connection, data)),
        end: live((data: string) => onError(connection, asString(control(data)?.code))),
        clock: live(sampleTime),
        opened: live<void>(() => setState(connection, "open")),
        closed: live((reason: "fatal" | "transient") => {
          if (reason === "fatal") onError(connection);
          else setState(connection, "connecting");
        }),
      },
      connection.cursor,
    );
    return connection;
  }

  function streamUrl(el: Element): string | null {
    return el.getAttribute(ATTR_STREAM) ?? el.getAttribute(ATTR_SSE);
  }

  function scan(root: ParentNode): void {
    for (const el of matching(root, STREAM_SELECTOR)) {
      // A container a patch removed while the chunk was loading opens no stream.
      if (!el.isConnected) continue;
      const url = streamUrl(el);
      if (url === null || url === "") continue;
      const existing = connections.get(url);
      if (existing !== undefined) {
        existing.owners.add(el);
        continue;
      }
      openConnection(url, pageUrl(el), undefined, el);
    }
  }

  // Pausing keeps the bound registry, so resume knows which zones to revalidate.
  function pause(): void {
    paused = true;
    pausedAt = now();
    for (const connection of connections.values()) {
      connection.control.close();
      setState(connection, "closed");
    }
  }

  // On returning visibility every paused connection reopens, and bound zones
  // re-GET only when the tab was hidden past the threshold.
  function resume(): void {
    paused = false;
    const revalidate = now() - pausedAt >= RESUME_REVALIDATE_MS;
    const pending = [...connections.values()];
    connections.clear();
    const revalidated = new Set<string>();
    for (const previous of pending) {
      const resumed = openConnection(previous.url, previous.pageUrl, previous);
      if (previous.resync !== undefined) {
        const resync = previous.resync;
        resync.owners.delete(previous);
        delete previous.resync;
        resumed.resync = resync;
        resync.owners.add(resumed);
      }
      if (!revalidate || revalidated.has(previous.pageUrl)) continue;
      revalidated.add(previous.pageUrl);
      const zone = zonesOf(
        pending.filter((connection) => connection.pageUrl === previous.pageUrl),
      );
      if (zone === "") continue;
      const request = { url: previous.pageUrl, zone };
      const owners = peersOf(previous.pageUrl);
      for (const owner of owners) {
        if (owner.resync?.request.zone !== request.zone) cancelResync(owner);
      }
      trackResync(request, owners);
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
    now: () => now() + offset(),
    offset,
    on(topic, name, callback) {
      const listener = { topic, name, callback };
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    meta: () => currentMeta,
    _reset() {
      for (const connection of connections.values()) {
        connection.control.close();
        cancelResync(connection);
      }
      connections.clear();
      observer?.disconnect();
      echo.clear();
      offsets.length = 0;
      listeners.clear();
      currentMeta = undefined;
      paused = false;
      pausedAt = 0;
      if (detachVisibility !== null) detachVisibility();
      detachVisibility = null;
    },
  };
}
