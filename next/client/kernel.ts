// The contract between the runtime and its chunks. A chunk installs through
// Next._extend with a function of the Kernel, serves the handlers the runtime calls and
// hooks the apply phases, so no chunk writes a field of the runtime or of another chunk.

import type { Applied, BeforeApply, Target } from "./apply";
import type { ScriptsChunk, ScriptsFactory } from "./chunks";
import type { CsrfMint } from "./csrf";
import type { FormsFactory } from "./forms";
import type { LayersFactory } from "./layers";
import type { Bounds } from "./morph";
import type { PartialSurface } from "./partial";
import type { PollFactory } from "./poll";
import type { Diagnostics, isBusy, markBusy, now, stamped } from "./protocol";
import type { IntentFactory, Router } from "./router";
import type { SseFactory } from "./sse";
import type { Watch } from "./triggers";
import type { FetchAdapter, Outcome, StreamReader } from "./wire";
import type { WsTransportFactory } from "./ws";

/** What each chunk exports to the runtime as it installs, by chunk name. */
export interface Modules {
  scripts: ScriptsFactory;
  stream: SseFactory;
  ws: WsTransportFactory;
  csrf: CsrfMint;
  poll: PollFactory;
  dev: Diagnostics;
  forms: FormsFactory;
  layers: LayersFactory;
  boundaries: true;
  reload: true;
  more: true;
  router: Router;
  intent: IntentFactory;
}

/** The functions a chunk serves, each one called by the runtime once it is served. */
export interface Handlers {
  /** Read a streamed answer into the boundaries it fills, served by boundaries. */
  read: StreamReader;
  /** Fetch a boundary wrapper or a zone element, served by boundaries. */
  fetch: (el: Element) => Promise<Outcome>;
  /** Bound the slot of a navigation target, served by the router. */
  slot: (target: Target) => Bounds | undefined;
  /** Answer a popstate the router owns with the page it shows, served by the router. */
  pop: () => string | undefined;
  /** Hand a layer the answer prefetched for its link, served by the intent chunk. */
  take: (href: string, zone: string) => FetchAdapter | undefined;
}

/**
 * The apply phases a chunk hooks into, each run before the public event of its phase.
 *
 * Hooks run in ascending order, so the order never depends on which async bundle
 * evaluated first. A before hook may rewrite the ops, the public listeners see the result.
 */
export interface Hooks {
  before: (detail: BeforeApply) => void;
  applied: (applied: Applied) => void;
}

// The order of the apply hooks, lower first. An envelope opening a layer waits for the
// layer chunk, and an adopted boundary hands its attributes to the content root before
// the loading content is captured and the router clear drops its snapshots.
export const ORDER_LAYERS = 0;
export const ORDER_ADOPT = 1;
export const ORDER_CAPTURE = 2;
export const ORDER_CLEAR = 3;
// The state of a filled boundary is stamped before the active links and the viewport
// prefetch read the applied nodes.
export const ORDER_STAMP = 0;
export const ORDER_CURRENT = 1;
export const ORDER_VIEWPORT = 2;

/** What the runtime hands a chunk as it installs. */
export interface Kernel {
  /** The public `Next.partial` surface. */
  partial: PartialSurface;
  /** The seeded context, read per call so a later payload is seen. */
  context(): Readonly<Record<string, unknown>>;
  /** The CSP nonce of the runtime script, copied onto every element a chunk injects. */
  nonce: string | undefined;
  /** Run a callback on every `ready`, at once when the runtime is already ready. */
  onReady(listener: () => void): void;
  /** Resolve the scripts chunk, the surfaces Next.ready("scripts") resolves with. */
  scripts(): Promise<ScriptsChunk>;
  /** Fetch a chunk, src naming a bundle the payload lists outside `$chunks`. */
  load<K extends keyof Modules>(name: K, src?: string): Promise<Modules[K] | undefined>;
  /** What a chunk exported, undefined until it has evaluated. */
  module<K extends keyof Modules>(name: K): Modules[K] | undefined;
  /** Serve a handler the runtime calls, the first one served is kept. */
  serve<K extends keyof Handlers>(name: K, handler: Handlers[K]): void;
  /** The handler a chunk served, undefined until it has. */
  served<K extends keyof Handlers>(name: K): Handlers[K] | undefined;
  /** Hook an apply phase at the given order. */
  hook<K extends keyof Hooks>(phase: K, order: number, hook: Hooks[K]): void;
  /** Fire a runtime event on the document and the bus, false when a listener vetoed. */
  fire(event: string, detail: Record<string, unknown>, cancelable?: boolean): boolean;
  /** The busy marks, kept by the runtime since a chunk's copy would hold other state. */
  mark: typeof markBusy;
  busy: typeof isBusy;
  stamped: typeof stamped;
  now: typeof now;
  /** The headers a partial request of the runtime sends, the given ones on top. */
  headers(headers: Record<string, string>): Headers;
  /** Abort a controller once the request deadline of the payload passes. */
  arm(controller: AbortController): () => void;
  /** Parse a response body as the parse hook of its content type does. */
  parse(contentType: string, body: string): unknown;
  /** Stop the requests and observers whose elements left the document. */
  sweep(): void;
  /** Record whether the server speaks this runtime's release, reloading once if not. */
  release(current: boolean): void;
  /** Watch an element into view, sending once per entry as the sentinels do. */
  watch: Watch;
  /** The loading content each streamed boundary showed before its first fill. */
  fallbacks: WeakMap<Element, string>;
  /** The dev diagnostics, undefined outside a `$dev` page. */
  diagnostics(): Diagnostics | undefined;
}
