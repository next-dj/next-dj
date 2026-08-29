// The global `Next` facade every page reaches. Owns the client context store, the
// event bus, the plugin hook, and mounts Next.partial for morph and fetch.

import { createPartial } from "./partial";
import type { PartialSurface } from "./partial";
import type { Envelope } from "./apply";
import type { PartialError } from "./protocol";

/** The client context store, shared by _init, the context op, and csrf meta. */
export type NextContext = Readonly<Record<string, unknown>>;

export type { PartialError, PartialErrorKind } from "./protocol";

/**
 * Payloads of the runtime events on the Next.on bus, keyed by event name.
 * The next:* DOM events fire on the document instead and are not in this map.
 */
export interface NextEventMap {
  ready: NextContext;
  // changed lists only the delta keys, so an island can skip a re-render its
  // own keys did not cause.
  "context-updated": { context: NextContext; changed: string[] };
  "partial:before-request": {
    url: string;
    method: string;
    intent: { zone?: string; uid?: string };
  };
  "partial:before-apply": { envelope: Envelope };
  // ok is false when any op threw or was an unknown verb, so a listener can
  // tell a clean apply from a degraded one that still mounted what changed.
  "partial:applied": { envelope: Envelope; ok: boolean };
  "partial:error": PartialError;
  "partial:layer-opened": { opener: HTMLElement | null };
  "partial:layer-accepted": { result: unknown };
  "partial:layer-dismissed": { reason: string };
  "next:toast": { text: string; variant: string };
}

type NextListener = (payload: Record<string, unknown>) => void;
type NextPlugin<T> = (next: typeof Next) => T;

/** The window-exposed runtime facade, a static class since there is one per page. */
class Next {
  static #context: Record<string, unknown> = {};
  static #listeners = new Map<string, Set<NextListener>>();
  static #ready = false;

  static partial: PartialSurface = createPartial({
    dispatch: (event, payload) => Next.#dispatch(event, payload),
    mergeContext: (data) => Next.#mergeContext(data),
  });

  static get context(): Readonly<Record<string, unknown>> {
    return Object.freeze({ ...Next.#context });
  }

  /** Bootstrap called once per page, seeding context and mounting before ready runs. */
  static _init(context: Record<string, unknown>): void {
    // Only the literal true opens the dev channel, so a stray "true" string
    // leaves production quiet. Runs before the initial trigger scan.
    if (context.$dev === true) Next.partial._configure({ dev: true });
    Next.#context = context;
    Next.#ready = true;
    // The initial seed is one big delta, so every seeded key is changed.
    Next.#dispatch("context-updated", { context, changed: Object.keys(context) });
    // Mount before ready listeners run, so a ready handler sees a mounted document.
    Next.partial.ready();
    Next.#dispatch("ready", context);
  }

  /** Subscribe to a runtime event, returning an unsubscribe function. */
  // The overloads are a type-only narrowing, every listener lands in one Map.
  static on<K extends keyof NextEventMap>(
    event: K,
    listener: (payload: NextEventMap[K]) => void,
  ): () => void;
  static on(event: string, listener: (payload: unknown) => void): () => void;
  static on(event: string, listener: NextListener): () => void {
    let bucket = Next.#listeners.get(event);
    if (bucket === undefined) {
      bucket = new Set();
      Next.#listeners.set(event, bucket);
    }
    bucket.add(listener);
    if (event === "ready" && Next.#ready) {
      listener({ ...Next.#context });
    }
    return () => {
      bucket.delete(listener);
    };
  }

  static use<T>(plugin: NextPlugin<T>): T {
    return plugin(Next);
  }

  // The context op and csrf meta merge into the store _init owns, so islands
  // see one consistent snapshot.
  static #mergeContext(data: Record<string, unknown>): void {
    const changed = Object.keys(data);
    Next.#context = { ...Next.#context, ...data };
    Next.#dispatch("context-updated", { context: Next.#context, changed });
  }

  static #dispatch(event: string, payload: Record<string, unknown>): void {
    const bucket = Next.#listeners.get(event);
    if (bucket === undefined) return;
    // Snapshot against mid-fan-out mutation, isolate each call so one throwing
    // listener does not abort delivery to the rest.
    for (const listener of [...bucket]) {
      try {
        listener(payload);
      } catch (e) {
        console.error("[next] listener threw", e);
      }
    }
  }
}

declare global {
  interface Window {
    Next: typeof Next;
  }
}

window.Next = Next;
