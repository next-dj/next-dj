// The global `Next` facade every page reaches. Owns the context store, the event bus,
// the plugin hook, mounts Next.partial, and publishes the optional scripts chunk.

import { createPartial } from "./partial";
import type { PartialSurface } from "./partial";
import type { Envelope } from "./apply";
import { chunkLoader, createExtras } from "./chunks";
import type { ExtrasFactory, ScriptsChunk } from "./chunks";
import type { ConsentChange } from "./consent";
import { readCsrf } from "./csrf";
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
  // changed lists only the delta keys, so an island can skip a foreign re-render.
  "context-updated": { context: NextContext; changed: string[] };
  "partial:before-request": {
    url: string;
    method: string;
    intent: { zone?: string; uid?: string };
  };
  "partial:before-apply": { envelope: Envelope };
  // ok is false when any op threw or was an unknown verb, so a listener can
  // tell a clean apply from a degraded one that still mounted what changed.
  // nodes are the roots the ops inserted or morphed, the ones the mount pass ran over.
  "partial:applied": { envelope: Envelope; ok: boolean; nodes: readonly Element[] };
  "partial:error": PartialError;
  "partial:layer-opened": { opener: HTMLElement | null };
  "partial:layer-accepted": { result: unknown };
  "partial:layer-dismissed": { reason: string };
  "next:toast": { text: string; variant: string };
  "next:navigated": NavigatedDetail;
  "next:consent": ConsentChange;
  "next:script-loaded": { name: string };
  "next:script-error": { name: string; url: string };
}

type NextListener = (payload: Record<string, unknown>) => void;
type NextPlugin<T> = (next: typeof Next) => T;

// The bus rides an EventTarget, so the fan-out snapshot and the removal are the
// platform's. The try is ours: an EventTarget hands a throw to the global handler.
// The ready replay reaches a listener outside the bus, so it gets the same containment.
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

// Read while the runtime script still executes, the base its chunks resolve against.
const RUNTIME = document.currentScript;
const NONCE = scriptNonce(document);

/** The window-exposed runtime facade, a static class since there is one per page. */
class Next {
  static #context: Record<string, unknown> = {};
  static #bus = new NextBus();
  static #ready = false;

  static partial: PartialSurface = createPartial({
    dispatch: (event, payload) => Next.#bus.emit(event, payload),
    mergeContext: (data) => Next.#mergeContext(data),
  });

  static #diagnosed = false;

  /** Consent per category, set once the scripts chunk lands. */
  static consent: ScriptsChunk["consent"] | undefined;

  /** Third-party scripts, set once the scripts chunk lands. */
  static scripts: ScriptsChunk["scripts"] | undefined;

  static #extras = createExtras({
    dispatch: (event, payload) => Next.#bus.emit(event, payload),
    runtime: RUNTIME,
    nonce: NONCE,
    mount: (nodes) => Next.partial.mount(nodes),
    install: (chunk) => {
      Next.consent = chunk.consent;
      Next.scripts = chunk.scripts;
    },
  });

  static #chunks: { [K in keyof NextChunks]: () => Promise<NextChunks[K]> } = {
    scripts: () => Next.#extras.ready(),
  };

  // The dev chunk, fetched only for a page rendered under $dev.
  static #devtools = chunkLoader(
    {
      dispatch: (event, payload) => Next.#bus.emit(event, payload),
      runtime: RUNTIME,
      nonce: NONCE,
    },
    "dev",
    "next.dev.min.js",
    () => undefined,
  );

  static navigation = {
    current: (): NavigationState => Next.partial._current(),
  };

  static get context(): Readonly<Record<string, unknown>> {
    return Object.freeze({ ...Next.#context });
  }

  /** Bootstrap called once per page, seeding context and mounting before ready runs. */
  static _init(context: Record<string, unknown>): void {
    // Only the literal true opens the dev channel, so a stray "true" string
    // leaves production quiet. Runs before the initial trigger scan.
    const dev = context.$dev === true;
    if (dev) {
      Next.partial._configure({ dev: true });
      Next.#devtools(context);
    }
    // A page without a mintable token carries no $csrf, so an absent key keeps
    // whatever an envelope already rotated in rather than clearing it.
    const csrf = readCsrf(context.$csrf);
    if (csrf !== undefined) {
      Next.partial.setCsrf(csrf);
    } else if (dev && context.$csrf !== undefined) {
      // Otherwise the only symptom is a 403 on every programmatic mutation.
      console.warn(
        "[next] ignored a malformed $csrf payload, unsafe requests send no header",
      );
    }
    Next.#context = context;
    Next.#ready = true;
    // The initial seed is one big delta, so every seeded key is changed.
    Next.#bus.emit("context-updated", { context, changed: Object.keys(context) });
    // Mount before ready listeners run, so a ready handler sees a mounted document.
    Next.partial.ready();
    Next.#bus.emit("ready", context);
    // After ready, so a next:consent listener a ready handler binds hears the start.
    Next.#extras.init(context);
  }

  /**
   * Resolve with a lazy chunk's surfaces once it has landed and taken the payload.
   *
   * Fetches the chunk if the page did not, and rejects when it cannot load.
   */
  static ready<K extends keyof NextChunks>(chunk: K): Promise<NextChunks[K]> {
    return Next.#chunks[chunk]();
  }

  /** The dev chunk's handshake, opening the diagnostics it carries. */
  static _diagnostics(diagnostics: Diagnostics): void {
    // First wins, a second copy of the chunk would report everything twice.
    if (Next.#diagnosed) return;
    Next.#diagnosed = true;
    Next.partial._configure({ dev: true, diagnostics });
  }

  /** The scripts chunk's handshake, called once as it evaluates. */
  static _register(factory: ExtrasFactory): void {
    Next.#extras.register(factory);
  }

  /** Subscribe to a runtime event, returning an unsubscribe function. */
  // The overloads are a type-only narrowing, every listener lands on one bus.
  static on<K extends keyof NextEventMap>(
    event: K,
    listener: (payload: NextEventMap[K]) => void,
  ): () => void;
  static on(event: string, listener: (payload: unknown) => void): () => void;
  static on(event: string, listener: NextListener): () => void {
    const off = Next.#bus.subscribe(event, listener);
    // ready alone describes a state, not a moment, so a late subscriber gets a replay.
    if (event === "ready" && Next.#ready) deliver(listener, { ...Next.#context });
    return off;
  }

  static use<T>(plugin: NextPlugin<T>): T {
    return plugin(Next);
  }

  // The context op and csrf meta merge into the store _init owns, one snapshot.
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
