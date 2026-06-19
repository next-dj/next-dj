// The public facade: the global `Next` every page reaches. It owns the client
// context store, the event bus (Next.on), the plugin hook (Next.use), and mounts
// Next.partial for the morph and fetch runtime. A static class, never
// instantiated, since there is one runtime per page.

import { createPartial } from "./partial";
import type { PartialSurface } from "./partial";
import type { Envelope } from "./apply";

// The client context store, the same shape _init seeds and the context op and
// csrf meta merge into.
export type NextContext = Readonly<Record<string, unknown>>;

// The payload of every runtime event reaching the Next.on bus, keyed by event
// name, so a known event types its listener argument. This is only the bus
// channel: the next:* DOM events (next:mounted, next:removed) fire on the
// document instead and are not in this map. A custom event op or a plugin event
// falls through to the open on() overload.
export interface NextEventMap {
  ready: NextContext;
  "context-updated": NextContext;
  "partial:before-request": {
    url: string;
    method: string;
    intent: { zone?: string; uid?: string };
  };
  "partial:before-apply": { envelope: Envelope };
  "partial:applied": { envelope: Envelope };
  "partial:error": { status: number; body: string; error: unknown };
  "partial:layer-opened": { opener: HTMLElement | null };
  "partial:layer-accepted": { result: unknown };
  "partial:layer-dismissed": { reason: string };
  "next:toast": { text: string; variant: string };
}

type NextListener = (payload: Record<string, unknown>) => void;
type NextPlugin<T> = (next: typeof Next) => T;

class Next {
  static #context: Record<string, unknown> = {};
  static #listeners: Map<string, Set<NextListener>> = new Map();
  static #ready = false;

  static partial: PartialSurface = createPartial({
    dispatch: (event, payload) => Next.#dispatch(event, payload),
    mergeContext: (data) => Next.#mergeContext(data),
  });

  static get context(): Readonly<Record<string, unknown>> {
    return Object.freeze({ ...Next.#context });
  }

  // The entry point the template's inline script calls once per page with the
  // server-seeded context. It is the runtime's true bootstrap, ahead of any
  // co-located script, so it seeds context and mounts before ready listeners run.
  static _init(context: Record<string, unknown>): void {
    Next.#context = context;
    Next.#ready = true;
    Next.#dispatch("context-updated", context);
    // Seed the asset registry, mount the initial DOM, and fire the batched load
    // zones before `ready` listeners run, so a `ready` handler sees a mounted
    // document.
    Next.partial.ready();
    Next.#dispatch("ready", context);
  }

  // A known runtime event types its listener payload through NextEventMap, a
  // custom event op or a plugin event falls through to the open overload. The
  // dispatch mechanism is unchanged: every listener lands in the same
  // Map<string, Set>, the overloads are a type-only narrowing of the argument.
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
      bucket!.delete(listener);
    };
  }

  static use<T>(plugin: NextPlugin<T>): T {
    return plugin(Next);
  }

  // The operation `context` and the `csrf` meta merge into the same store
  // that `_init` owns, so context islands see one consistent snapshot.
  static #mergeContext(data: Record<string, unknown>): void {
    Next.#context = { ...Next.#context, ...data };
    Next.#dispatch("context-updated", Next.#context);
  }

  static #dispatch(event: string, payload: Record<string, unknown>): void {
    const bucket = Next.#listeners.get(event);
    if (bucket === undefined) return;
    for (const listener of bucket) {
      listener(payload);
    }
  }
}

declare global {
  interface Window {
    Next: typeof Next;
  }
}

window.Next = Next;
