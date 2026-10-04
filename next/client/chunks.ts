// The runtime side of the lazy chunks. Consent and third-party scripts ship in
// next.scripts.min.js, fetched only when the payload carries them, and Next.ready
// resolves once it has loaded. The SSE bridge, the CSRF token fetch, the poller and
// the dev diagnostics each ship in a chunk of their own, fetched on first use.

import type { Consent } from "./consent";
import { asString, fire, isRecord } from "./protocol";
import type { PartialError } from "./protocol";
import type { Scripts } from "./scripts";

// The payload keys that need the chunk on this page.
const CHUNK_KEYS = ["$scripts", "$consent"];

// How long a chunk fetch may run before it counts as failed, so a stalled request
// cannot block Next.ready, a deferred CSRF token or a stream indefinitely.
export const CHUNK_TIMEOUT = 15e3;

/** The runtime functions exposed to the scripts chunk. */
export interface ExtrasHost {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  nonce: string | undefined;
  // The post-morph mount pass, run over revealed consent markup like inserted markup.
  mount: (nodes: readonly Element[]) => void;
}

/** The public surfaces of the scripts chunk, resolved by Next.ready("scripts"). */
export interface ScriptsChunk {
  consent: Pick<Consent, "get" | "decided" | "update" | "acceptAll" | "rejectAll">;
  scripts: Pick<Scripts, "load" | "status">;
}

/** The surfaces the scripts chunk factory returns. */
export interface Extras extends ScriptsChunk {
  consent: Consent;
  scripts: Scripts;
  /** Configure from an init payload and announce the initial consent state. */
  configure(context: Record<string, unknown>): void;
}

export type ExtrasFactory = (host: ExtrasHost) => Extras;

/** The runtime's loader of the scripts chunk. */
export interface ExtrasLoader {
  /** Resolve once the chunk has loaded and been configured, fetching it if needed. */
  ready(): Promise<ScriptsChunk>;
  /** Store an init payload, fetching the chunk when the payload requires it. */
  init(context: Record<string, unknown>): void;
}

/** The runtime dependencies of a chunk loader. */
export interface ChunkDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  document?: Document;
  // The runtime's own script element, read during evaluation. Null for an inline
  // bootstrap, where $chunks is the only source of a chunk URL.
  runtime: Element | null;
  nonce: string | undefined;
  // The seeded init payload, read at fetch time for its $chunks map.
  context: () => Record<string, unknown>;
}

export interface ExtrasDeps extends ChunkDeps {
  mount: (nodes: readonly Element[]) => void;
  // Publishes the chunk surfaces before they are configured, so a listener of the
  // initial next:consent can already call them.
  install: (chunk: ScriptsChunk) => void;
}

/** Read access to the module a lazy chunk exports. */
export interface LazyModule<T> {
  /** The module, undefined until its chunk has evaluated. */
  get(): T | undefined;
  /** Resolve with the module, fetching its chunk if needed, or undefined on failure. */
  load(): Promise<T | undefined>;
}

/** A lazy module plus the registration call its chunk makes. */
export interface Lazy<T> extends LazyModule<T> {
  /** Store the module the chunk exports. The first copy to evaluate is kept. */
  land(value: T): void;
}

/**
 * A loader for a chunk carrying one module, with at most one fetch in flight.
 *
 * The URL is the chunk's `$chunks` entry in the seeded payload, or next.<key>.min.js
 * beside the runtime script when the payload has none. The entry takes precedence
 * because a hashing storage serves the chunk under a name the sibling URL cannot
 * derive. A missing URL or a failed fetch resolves undefined, and a failed fetch is
 * retried on the next call. A file that loads without registering its module counts
 * as failed, as does one that has not registered it within CHUNK_TIMEOUT.
 */
export function lazyChunk<T>(deps: ChunkDeps, key: string): Lazy<T> {
  const doc = deps.document ?? document;
  let value: T | undefined;
  let pending: Promise<T | undefined> | undefined;
  let settle: ((value: T | undefined) => void) | undefined;
  return {
    get: () => value,
    load() {
      if (value !== undefined) return Promise.resolve(value);
      if (pending !== undefined) return pending;
      const chunks = deps.context().$chunks;
      const runtime = deps.runtime;
      const url =
        (isRecord(chunks) ? asString(chunks[key]) : undefined) ??
        (runtime instanceof HTMLScriptElement && runtime.src !== ""
          ? new URL(`next.${key}.min.js`, runtime.src).href
          : undefined);
      if (url === undefined) return Promise.resolve(undefined);
      const el = doc.createElement("script");
      if (deps.nonce !== undefined) el.nonce = deps.nonce;
      const fail = (): void => {
        // Nothing to do once the module is registered or this attempt has failed.
        if (value !== undefined || !el.isConnected) return;
        // A retry appends a new tag, so the failed one is removed.
        el.remove();
        pending = undefined;
        settle?.(undefined);
        fire(doc, deps.dispatch, "partial:error", {
          kind: "asset",
          url,
          error: new Error(`${key} chunk failed`),
        } satisfies PartialError);
      };
      el.onerror = fail;
      // A file that ran without registering its module fails too, so no caller hangs.
      el.onload = fail;
      el.src = url;
      doc.head.append(el);
      setTimeout(fail, CHUNK_TIMEOUT);
      pending = new Promise((resolve) => (settle = resolve));
      return pending;
    },
    land(landed) {
      if (value !== undefined) return;
      value = landed;
      settle?.(landed);
    },
  };
}

/** Build the scripts chunk loader, which configures the chunk with each payload. */
export function createExtras(
  deps: ExtrasDeps,
  chunk: LazyModule<ExtrasFactory>,
): ExtrasLoader {
  let extras: Extras | undefined;
  // Set by the first init. From then on deps.context returns the latest payload.
  let seeded = false;
  // The ready calls made before init, resolved by init once the payload is known.
  const early: ((landing: Promise<ScriptsChunk>) => void)[] = [];

  // Called only after init stored a payload. The chunk is configured with the latest.
  async function land(): Promise<ScriptsChunk> {
    // A chunk that has already loaded is built synchronously, without an await.
    const factory = chunk.get() ?? (await chunk.load());
    if (factory === undefined)
      throw new Error("[next] the scripts chunk is unavailable");
    // Later calls reuse the surfaces built the first time.
    if (extras === undefined) {
      extras = factory(deps);
      deps.install(extras);
      // Read after the await, since init may have stored a newer payload meanwhile.
      extras.configure(deps.context());
    }
    return extras;
  }

  return {
    ready() {
      if (seeded) return land();
      return new Promise((resolve) => early.push(resolve));
    },
    init(next) {
      seeded = true;
      if (extras !== undefined) extras.configure(next);
      else if (early.length > 0 || CHUNK_KEYS.some((key) => next[key] !== undefined)) {
        const landing = land();
        // The loader already reported the failure as a partial:error.
        landing.catch(() => undefined);
        for (const resolve of early.splice(0)) resolve(landing);
      }
    },
  };
}
