// The core side of the optional scripts chunk. Consent and third-party scripts ship in
// next.scripts.min.js, fetched only when the payload carries them, so a site without
// them never downloads it. Their surfaces exist once it lands, Next.ready awaits that.

import type { Consent } from "./consent";
import { asString, fire, isRecord } from "./protocol";
import type { PartialError } from "./protocol";
import type { Scripts } from "./scripts";

const CHUNK_FILE = "next.scripts.min.js";
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
  /** The chunk's handshake. */
  register(factory: ExtrasFactory): void;
}

/** What a chunk fetch needs from the runtime. */
export interface ChunkDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  document?: Document;
  // The running runtime's script, read while it still executes. Null for an inline
  // bootstrap, which leaves the payload key as the only way to a chunk.
  runtime: Element | null;
  nonce: string | undefined;
}

export interface ExtrasDeps extends ChunkDeps {
  mount: (nodes: readonly Element[]) => void;
  // Publishes the landed surfaces before they take the payload, so a listener of the
  // starting next:consent can already reach them.
  install: (chunk: ScriptsChunk) => void;
}

interface Waiter {
  resolve: (chunk: ScriptsChunk) => void;
  reject: (error: Error) => void;
}

/**
 * Fetch one chunk by its `$chunks` key, or as the runtime's sibling, at most once.
 *
 * The payload key wins, since a hashed storage renames the runtime but not the
 * sibling. A failed or unaddressable fetch calls failed, and a failed one may retry.
 */
export function chunkLoader(
  deps: ChunkDeps,
  key: string,
  file: string,
  failed: () => void,
): (context: Record<string, unknown> | undefined) => void {
  const doc = deps.document ?? document;
  let requested = false;
  return (context) => {
    if (requested) return;
    const chunks = context?.$chunks;
    const runtime = deps.runtime;
    const url =
      (isRecord(chunks) ? asString(chunks[key]) : undefined) ??
      (runtime instanceof HTMLScriptElement && runtime.src !== ""
        ? new URL(file, runtime.src).href
        : undefined);
    if (url === undefined) {
      failed();
      return;
    }
    requested = true;
    const el = doc.createElement("script");
    if (deps.nonce !== undefined) el.nonce = deps.nonce;
    el.onerror = () => {
      // A retry appends its own tag, so the failed one leaves rather than piling up.
      el.remove();
      requested = false;
      failed();
      fire(doc, deps.dispatch, "partial:error", {
        kind: "asset",
        url,
        error: new Error(`${key} chunk failed`),
      } satisfies PartialError);
    };
    el.src = url;
    doc.head.append(el);
  };
}

export function createExtras(deps: ExtrasDeps): ExtrasLoader {
  let extras: Extras | undefined;
  let context: Record<string, unknown> | undefined;
  const waiting: Waiter[] = [];

  const fetchChunk = chunkLoader(deps, "scripts", CHUNK_FILE, () => {
    const error = new Error("[next] the scripts chunk is unavailable");
    for (const waiter of waiting.splice(0)) waiter.reject(error);
  });

  function configure(landed: Extras, next: Record<string, unknown>): void {
    landed.configure(next);
    for (const waiter of waiting.splice(0)) waiter.resolve(landed);
  }

  return {
    ready() {
      if (extras !== undefined && context !== undefined) return Promise.resolve(extras);
      const pending = new Promise<ScriptsChunk>((resolve, reject) => {
        waiting.push({ resolve, reject });
      });
      // Before init the payload's $chunks is unknown, so init fetches for a waiter.
      if (context !== undefined) fetchChunk(context);
      return pending;
    },
    init(next) {
      context = next;
      if (extras !== undefined) configure(extras, next);
      else if (waiting.length > 0 || CHUNK_KEYS.some((key) => next[key] !== undefined))
        fetchChunk(next);
    },
    register(factory) {
      // A second copy of the chunk would seed and announce the page a second time.
      if (extras !== undefined) return;
      const landed = factory({
        dispatch: deps.dispatch,
        nonce: deps.nonce,
        mount: deps.mount,
      });
      extras = landed;
      deps.install(landed);
      if (context !== undefined) configure(landed, context);
    },
  };
}
