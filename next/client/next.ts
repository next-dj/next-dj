// The global `Next` facade every page reaches. Owns the client context store, the
// event bus, the plugin hook, and mounts Next.partial for morph and fetch.

import { createPartial } from "./partial";
import type { PartialSurface } from "./partial";
import type { Envelope } from "./apply";
import type { CsrfPayload } from "./wire";
import { asString, isRecord } from "./protocol";
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
  "partial:applied": { envelope: Envelope; ok: boolean };
  "partial:error": PartialError;
  "partial:layer-opened": { opener: HTMLElement | null };
  "partial:layer-accepted": { result: unknown };
  "partial:layer-dismissed": { reason: string };
  "next:toast": { text: string; variant: string };
}

type NextListener = (payload: Record<string, unknown>) => void;
type NextPlugin<T> = (next: typeof Next) => T;

// The bus rides an EventTarget, so the fan-out snapshot and the removal are the
// platform's. The try is ours: an EventTarget hands a throw to the global handler.
class NextBus extends EventTarget {
  emit(event: string, payload: Record<string, unknown>): void {
    this.dispatchEvent(new CustomEvent(event, { detail: payload }));
  }

  subscribe(event: string, listener: NextListener): () => void {
    const controller = new AbortController();
    this.addEventListener(
      event,
      (received) => {
        try {
          listener((received as CustomEvent<Record<string, unknown>>).detail);
        } catch (e) {
          console.error("[next] listener threw", e);
        }
      },
      { signal: controller.signal },
    );
    return () => controller.abort();
  }
}

// The init payload is untyped JSON, so the seed is taken only when both halves
// are strings. A half-filled pair would stamp a broken header on every mutation.
function readCsrf(value: unknown): CsrfPayload | undefined {
  if (!isRecord(value)) return undefined;
  const header = asString(value.header);
  const token = asString(value.token);
  if (header === undefined || token === undefined) return undefined;
  return { header, token };
}

/** The window-exposed runtime facade, a static class since there is one per page. */
class Next {
  static #context: Record<string, unknown> = {};
  static #bus = new NextBus();
  static #ready = false;

  static partial: PartialSurface = createPartial({
    dispatch: (event, payload) => Next.#bus.emit(event, payload),
    mergeContext: (data) => Next.#mergeContext(data),
  });

  static get context(): Readonly<Record<string, unknown>> {
    return Object.freeze({ ...Next.#context });
  }

  /** Bootstrap called once per page, seeding context and mounting before ready runs. */
  static _init(context: Record<string, unknown>): void {
    // Only the literal true opens the dev channel, so a stray "true" string
    // leaves production quiet. Runs before the initial trigger scan.
    const dev = context.$dev === true;
    if (dev) Next.partial._configure({ dev: true });
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
    if (event === "ready" && Next.#ready) {
      listener({ ...Next.#context });
    }
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
