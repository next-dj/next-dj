// The core side of the lazy chunks. Consent and third-party scripts ship in
// next.scripts.min.js, fetched only when the payload carries them, so a site without
// them never downloads it. Their surfaces exist once it lands, Next.ready awaits that.
// The stream bridge and the CSRF mint each ship alone, fetched on their first need.

import type { Consent } from "./consent";
import { asString, fire, isRecord } from "./protocol";
import type { PartialError } from "./protocol";
import type { Scripts } from "./scripts";

// The payload keys that need the chunk on this page.
const CHUNK_KEYS = ["$scripts", "$consent"];

/** What the core lends the chunk. */
export interface ExtrasHost {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  nonce: string | undefined;
  // The post-morph mount pass, so revealed markup mounts like any inserted markup.
  mount: (nodes: readonly Element[]) => void;
}

/** The public surfaces of the scripts chunk, what Next.ready("scripts") answers. */
export interface ScriptsChunk {
  consent: Pick<Consent, "get" | "decided" | "update" | "acceptAll" | "rejectAll">;
  scripts: Pick<Scripts, "load" | "status">;
}

/** What the chunk hands back once it runs. */
export interface Extras extends ScriptsChunk {
  consent: Consent;
  scripts: Scripts;
  /** Seed from an init payload and announce the starting consent. */
  configure(context: Record<string, unknown>): void;
}

export type ExtrasFactory = (host: ExtrasHost) => Extras;

export interface ExtrasLoader {
  /** Resolve once the chunk has landed and taken the payload, fetching it as needed. */
  ready(): Promise<ScriptsChunk>;
  /** Take an init payload, fetching the chunk when the payload needs it. */
  init(context: Record<string, unknown>): void;
}

/** What a chunk fetch needs from the runtime. */
export interface ChunkDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  document?: Document;
  // The running runtime's script, read while it still executes. Null for an inline
  // bootstrap, which leaves the payload key as the only way to a chunk.
  runtime: Element | null;
  nonce: string | undefined;
  // The seeded init payload, read as a chunk is fetched for its $chunks.
  context: () => Record<string, unknown>;
}

export interface ExtrasDeps extends ChunkDeps {
  mount: (nodes: readonly Element[]) => void;
  // Publishes the landed surfaces before they take the payload, so a listener of the
  // starting next:consent can already reach them.
  install: (chunk: ScriptsChunk) => void;
}

/** A module a lazy chunk hands over, what the core awaits before it uses it. */
export interface LazyModule<T> {
  /** The landed module, undefined until its chunk evaluates. */
  get(): T | undefined;
  /** Resolve with the module, fetching its chunk as needed, undefined when it cannot. */
  load(): Promise<T | undefined>;
}

export interface Lazy<T> extends LazyModule<T> {
  /** The chunk's handshake, the first copy to evaluate wins. */
  land(value: T): void;
}

/**
 * A chunk carrying one module, fetched at most once on its first need.
 *
 * The URL is the `$chunks` key of the seeded payload, or next.<key>.min.js beside the
 * runtime. The payload key wins, since a hashed storage renames the runtime but not
 * the sibling. An unaddressable or failed fetch answers undefined, and a failed one
 * is fetched again on the next need. A file that loads without landing has failed.
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
        // A retry appends its own tag, so the failed one leaves rather than piling up.
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
      // A file that ran without handing its module over fails too, or the need hangs.
      el.onload = () => {
        if (value === undefined) fail();
      };
      el.src = url;
      doc.head.append(el);
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

export function createExtras(
  deps: ExtrasDeps,
  chunk: LazyModule<ExtrasFactory>,
): ExtrasLoader {
  let extras: Extras | undefined;
  let context: Record<string, unknown> | undefined;
  // The ready calls made before init, which fetches for them once the payload is known.
  const early: ((landing: Promise<ScriptsChunk>) => void)[] = [];

  async function land(seed: Record<string, unknown>): Promise<ScriptsChunk> {
    // A landed chunk builds at once, so its surfaces exist as the payload seeds them.
    const factory = chunk.get() ?? (await chunk.load());
    if (factory === undefined)
      throw new Error("[next] the scripts chunk is unavailable");
    // A second need of a landed chunk takes the surfaces it already built.
    if (extras === undefined) {
      extras = factory(deps);
      deps.install(extras);
      extras.configure(seed);
    }
    return extras;
  }

  return {
    ready() {
      if (context !== undefined) return land(context);
      return new Promise((resolve) => early.push(resolve));
    },
    init(next) {
      context = next;
      if (extras !== undefined) extras.configure(next);
      else if (early.length > 0 || CHUNK_KEYS.some((key) => next[key] !== undefined)) {
        const landing = land(next);
        // A failure nobody awaits is already reported as an asset error.
        landing.catch(() => undefined);
        for (const resolve of early.splice(0)) resolve(landing);
      }
    },
  };
}
