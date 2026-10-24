// The global `Next` facade. It owns the context store, the event bus and the plugin
// hook, mounts Next.partial, and exposes the surfaces of the lazy scripts chunk.

import { createPartial } from "./partial";
import type { PartialSurface } from "./partial";
import type { Envelope } from "./apply";
import { createScriptsLoader, lazyChunk } from "./chunks";
import type { ChunkDeps, Lazy, ScriptsChunk, ScriptsFactory } from "./chunks";
import type { ConsentChange } from "./consent";
import { readCsrf } from "./csrf";
import type { CsrfMint } from "./csrf";
import type { SseFactory } from "./sse";
import type { PollFactory } from "./poll";
import type { WsTransportFactory } from "./ws";
import type { NavigatedDetail, NavigationState } from "./navigation";
import { scriptNonce } from "./protocol";
import type { Diagnostics, PartialError } from "./protocol";

/** The client context store, shared by _init, the context op, and csrf meta. */
export type NextContext = Readonly<Record<string, unknown>>;

export type { PartialError, PartialErrorKind } from "./protocol";

export type { NavigatedDetail, NavigationAction, NavigationState } from "./navigation";
export type { Asset, AssetLoad, Envelope, FormMeta, Patch } from "./apply";
export type { ConsentChange } from "./consent";
export type { ScriptsChunk } from "./chunks";
export type { ScriptStatus } from "./scripts";
export type { StreamMeta } from "./sse";

/** The module each single-module chunk registers through Next._land, by chunk name. */
interface NextModules {
  scripts: ScriptsFactory;
  stream: SseFactory;
  ws: WsTransportFactory;
  csrf: CsrfMint;
  poll: PollFactory;
  dev: Diagnostics;
}

/** The lazily fetched chunks Next.ready awaits, keyed by name. */
export interface NextChunks {
  scripts: ScriptsChunk;
}

/**
 * Payloads of the runtime events on the Next.on bus, keyed by event name.
 * The next:* events fire on the document as well.
 */
export interface NextEventMap {
  ready: NextContext;
  // changed lists only the keys that changed, so an island can skip an unrelated one.
  "context-updated": { context: NextContext; changed: string[] };
  "partial:before-request": {
    url: string;
    method: string;
    intent: { zone?: string; uid?: string };
  };
  "partial:before-apply": { envelope: Envelope };
  // ok is false when any op threw or named an unknown verb. nodes are the roots the
  // ops inserted or morphed, which the mount pass ran over.
  "partial:applied": { envelope: Envelope; ok: boolean; nodes: readonly Element[] };
  "partial:error": PartialError;
  "partial:layer-opened": { opener: HTMLElement | null };
  "partial:layer-accepted": { result: unknown };
  "partial:layer-dismissed": { reason: string };
  "next:stream-gap": { topic: string; from: number; to: number };
  "next:stream-state": {
    url: string;
    transport: "sse" | "websocket";
    state: "connecting" | "open" | "closed";
    code?: string;
  };
  "next:toast": { text: string; variant: string };
  "next:navigated": NavigatedDetail;
  "next:consent": ConsentChange;
  "next:script-loaded": { name: string };
  "next:script-error": { name: string; url: string };
}

type NextListener = (payload: Record<string, unknown>) => void;
type NextPlugin<T> = (next: typeof Next) => T;

// The bus is an EventTarget, which snapshots its listeners per dispatch and handles
// removal. deliver logs a listener's exception instead of passing it to the global
// error handler, and the ready replay, which calls a listener directly, uses it too.
function deliver(listener: NextListener, payload: Record<string, unknown>): void {
  try {
    listener(payload);
  } catch (e) {
    console.error("[next] listener threw", e);
  }
}

class NextBus extends EventTarget {
  emit(event: string, payload: Record<string, unknown>): void {
    this.dispatchEvent(new CustomEvent(event, { detail: payload }));
  }

  subscribe(event: string, listener: NextListener): () => void {
    const controller = new AbortController();
    this.addEventListener(
      event,
      (received) =>
        deliver(listener, (received as CustomEvent<Record<string, unknown>>).detail),
      { signal: controller.signal },
    );
    return () => controller.abort();
  }
}

// Read during module evaluation, the only time document.currentScript is set.
const NONCE = scriptNonce(document);

// The outcome of each script tag that settles from here on, so Next.scripts.load of a
// server-rendered tag that loaded before the scripts chunk arrived resolves at once.
const SETTLED = new WeakMap<EventTarget, boolean>();
const record = ({ target, type }: Event): void => {
  if (target instanceof HTMLScriptElement) SETTLED.set(target, type === "load");
};
document.addEventListener("load", record, true);
document.addEventListener("error", record, true);

/** The window-exposed runtime facade, a static class since there is one per page. */
class Next {
  static #context: Record<string, unknown> = {};
  static #bus = new NextBus();
  static #ready = false;

  // The dependencies shared by every chunk loader.
  static #chunk: ChunkDeps = {
    dispatch: (event, payload) => Next.#bus.emit(event, payload),
    nonce: NONCE,
    context: () => Next.#context,
  };

  // One loader per chunk. The dev chunk is fetched only when the payload has $dev.
  static #modules = Object.fromEntries(
    ["scripts", "stream", "ws", "csrf", "poll", "dev"].map((key) => [
      key,
      lazyChunk(Next.#chunk, key),
    ]),
  ) as { [K in keyof NextModules]: Lazy<NextModules[K]> };

  static partial: PartialSurface = createPartial({
    dispatch: Next.#chunk.dispatch,
    mergeContext: (data) => Next.#mergeContext(data),
    context: Next.#chunk.context,
    sse: Next.#modules.stream,
    ws: Next.#modules.ws,
    csrf: Next.#modules.csrf,
    poll: Next.#modules.poll,
  });

  /** Consent per category, set once the scripts chunk has loaded. */
  static consent: ScriptsChunk["consent"] | undefined;

  /** Third-party scripts, set once the scripts chunk has loaded. */
  static scripts: ScriptsChunk["scripts"] | undefined;

  static #scripts = createScriptsLoader(
    {
      ...Next.#chunk,
      host: {
        dispatch: Next.#chunk.dispatch,
        nonce: NONCE,
        mount: (nodes) => Next.partial.mount(nodes),
        settled: (el) => SETTLED.get(el),
      },
      install: (chunk) => {
        Next.consent = chunk.consent;
        Next.scripts = chunk.scripts;
      },
    },
    Next.#modules.scripts,
  );

  static #chunks: { [K in keyof NextChunks]: () => Promise<NextChunks[K]> } = {
    scripts: () => Next.#scripts.ready(),
  };

  static navigation = {
    current: (): NavigationState => Next.partial._current(),
  };

  static get context(): Readonly<Record<string, unknown>> {
    return Object.freeze({ ...Next.#context });
  }

  /** Bootstrap called once per page, seeding context and mounting before ready runs. */
  static _init(context: Record<string, unknown>): void {
    // Stored first, since the chunk loaders below read $chunks from it.
    Next.#context = context;
    // Only the boolean true enables the dev channel, a "true" string does not. It is
    // enabled before the initial trigger scan, and the diagnostics are attached once
    // the dev chunk has loaded, unless a later payload turned the channel off.
    if (context.$dev === true) {
      Next.partial._configure({ dev: true });
      Next.#modules.dev.whenLoaded((diagnostics) => {
        if (Next.#context.$dev === true)
          Next.partial._configure({ dev: true, diagnostics });
      });
    }
    // A page that cannot mint a token carries no $csrf, so an absent key keeps a
    // token an envelope already rotated in. The dev chunk warns about a malformed one.
    const csrf = readCsrf(context.$csrf);
    if (csrf !== undefined) Next.partial.setCsrf(csrf);
    Next.#ready = true;
    // Every seeded key counts as changed on the initial seed.
    Next.#bus.emit("context-updated", { context, changed: Object.keys(context) });
    // Mount before ready listeners run, so a ready handler sees a mounted document.
    Next.partial.ready();
    Next.#bus.emit("ready", context);
    // Last, so a next:consent listener bound in a ready handler gets the first event.
    Next.#scripts.init(context);
  }

  /**
   * Resolve with the surfaces of a lazy chunk once it has loaded and been configured.
   *
   * Fetches the chunk if needed. Rejects when it cannot load or the page had no init.
   */
  static ready<K extends keyof NextChunks>(chunk: K): Promise<NextChunks[K]> {
    return Next.#chunks[chunk]();
  }

  /** Register the module a lazy chunk exports, called by the chunk as it evaluates. */
  static _land<K extends keyof NextModules>(key: K, value: NextModules[K]): void;
  static _land(key: "sse", value: SseFactory): void;
  static _land(
    key: keyof NextModules | "sse",
    value: NextModules[keyof NextModules] | SseFactory,
  ): void {
    // A chunk from another release may name a module this runtime does not know.
    const moduleKey = key === "sse" ? "stream" : key;
    (Next.#modules[moduleKey] as Lazy<SseFactory> | undefined)?.land(
      value as SseFactory,
    );
  }

  /** Subscribe to a runtime event, returning an unsubscribe function. */
  // The overloads only narrow the payload type. Every listener is added to one bus.
  static on<K extends keyof NextEventMap>(
    event: K,
    listener: (payload: NextEventMap[K]) => void,
  ): () => void;
  static on(event: string, listener: (payload: unknown) => void): () => void;
  static on(event: string, listener: NextListener): () => void {
    const off = Next.#bus.subscribe(event, listener);
    // ready describes a state, not a moment, so a late subscriber is called at once.
    if (event === "ready" && Next.#ready) deliver(listener, { ...Next.#context });
    return off;
  }

  static use<T>(plugin: NextPlugin<T>): T {
    return plugin(Next);
  }

  // A merge replaces the store with a new object, leaving earlier snapshots intact.
  static #mergeContext(data: Record<string, unknown>): void {
    const changed = Object.keys(data);
    Next.#context = { ...Next.#context, ...data };
    Next.#bus.emit("context-updated", { context: Next.#context, changed });
  }
}

declare global {
  interface Window {
    Next: typeof Next;
  }
}

window.Next = Next;
