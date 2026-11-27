// The runtime side of the lazy chunks. Every chunk registers through Next._extend as it
// evaluates, whether the runtime fetched it or the server declared its tag. The runtime
// takes the URL from the `$chunks` payload entry, or from the caller for a bundle the
// router names, and Next.ready resolves the scripts chunk once it has loaded.

import type { Consent } from "./consent";
import { PAYLOAD_CONSENT, PAYLOAD_SCRIPTS, asString, fire, isRecord } from "./protocol";
import type { PartialError } from "./protocol";
import type { Scripts } from "./scripts";

// The payload keys that need the chunk on this page.
const CHUNK_KEYS = [PAYLOAD_SCRIPTS, PAYLOAD_CONSENT];

// How long a chunk fetch may run before it counts as failed, so a stalled request
// cannot block Next.ready, a deferred CSRF token or a stream indefinitely.
export const CHUNK_TIMEOUT = 15e3;

// The attempts a chunk gets before its loader stops appending tags for it.
export const CHUNK_ATTEMPTS = 3;

const NO_PAYLOAD = "[next] the page has no init payload";

/** The runtime functions the scripts chunk factory receives, and nothing more. */
export interface ScriptsHost {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  nonce: string | undefined;
  // The post-morph mount pass, run over revealed consent markup like inserted markup.
  mount: (nodes: readonly Element[]) => void;
  // Whether a server-rendered script tag has loaded, undefined while it has not settled.
  settled: (el: Element) => boolean | undefined;
}

/** The public surfaces of the scripts chunk, resolved by Next.ready("scripts"). */
export interface ScriptsChunk {
  consent: Pick<Consent, "get" | "decided" | "update" | "acceptAll" | "rejectAll">;
  scripts: Pick<Scripts, "load" | "status">;
}

/** The surfaces the scripts chunk factory returns. */
export interface ScriptsModule extends ScriptsChunk {
  consent: Consent;
  scripts: Scripts;
  /** Configure from an init payload and announce the initial consent state. */
  configure(context: Record<string, unknown>): void;
}

export type ScriptsFactory = (host: ScriptsHost) => ScriptsModule;

/** The runtime's loader of the scripts chunk. */
export interface ScriptsLoader {
  /** Resolve once the chunk has loaded and been configured, fetching it if needed. */
  ready(): Promise<ScriptsChunk>;
  /** Store an init payload, fetching the chunk when the payload requires it. */
  init(context: Record<string, unknown>): void;
}

/** The runtime dependencies of a chunk loader. */
export interface ChunkDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  document?: Document;
  nonce: string | undefined;
  // The seeded init payload, read at fetch time for its $chunks map.
  context: () => Record<string, unknown>;
}

export interface ScriptsLoaderDeps extends ChunkDeps {
  // What the chunk factory receives, kept apart from the loader's own seams.
  host: ScriptsHost;
  // Publishes the chunk surfaces before they are configured, so a listener of the
  // initial next:consent can already call them.
  install: (chunk: ScriptsChunk) => void;
}

/** Read access to the module a lazy chunk exports. */
export interface LazyModule<T> {
  /** The module, undefined until its chunk has evaluated. */
  get(): T | undefined;
  /**
   * Resolve with the module, fetching its chunk if needed, or undefined on failure.
   *
   * src names a bundle the payload lists elsewhere, the `$chunks` entry otherwise.
   */
  load(src?: string): Promise<T | undefined>;
  /** Run fn with the module on its first landing, even one after a failed attempt. */
  whenLoaded(fn: (value: T) => void): void;
}

/** A lazy module plus the registration call its chunk makes. */
export interface Lazy<T> extends LazyModule<T> {
  /** Whether the chunk has evaluated, so a second copy of its bundle installs nothing. */
  landed(): boolean;
  /** Store the module the chunk exports. The first copy to evaluate is kept. */
  land(value: T): void;
}

/** Append a chunk's script, calling done once on load, error or timeout, unless cancelled. */
export function appendScript(
  doc: Document,
  src: string,
  nonce: string | undefined,
  done: (loaded: boolean, el: HTMLScriptElement) => void,
): () => void {
  const el = doc.createElement("script");
  const cancel = (): void => {
    clearTimeout(timer);
    el.onload = el.onerror = null;
  };
  const end = (loaded: boolean): void => {
    cancel();
    done(loaded, el);
  };
  el.onload = () => end(true);
  el.onerror = () => end(false);
  if (nonce !== undefined) el.nonce = nonce;
  el.src = src;
  doc.head.append(el);
  const timer = setTimeout(end, CHUNK_TIMEOUT, false);
  return cancel;
}

/**
 * A loader for a chunk carrying one module, with at most one fetch in flight.
 *
 * The URL comes from the payload, the `$chunks` entry or one the caller read elsewhere,
 * as a hashing or signing storage serves it under a URL the client cannot derive. A
 * missing URL or a failed fetch resolves undefined, and a failed fetch is retried on the
 * next call, CHUNK_ATTEMPTS times in all. A file that loads without registering its
 * module counts as failed, as does one not registered within CHUNK_TIMEOUT.
 */
export function lazyChunk<T>(deps: ChunkDeps, key: string): Lazy<T> {
  const doc = deps.document ?? document;
  let landed = false;
  let value: T | undefined;
  let pending: Promise<T | undefined> | undefined;
  let settle: ((value: T | undefined) => void) | undefined;
  let cancel: (() => void) | undefined;
  let attempts = 0;
  // A timed-out tag may still evaluate, so its subscribers outlive the attempt.
  const waiting: ((value: T) => void)[] = [];

  function load(src?: string): Promise<T | undefined> {
    if (landed) return Promise.resolve(value);
    if (pending !== undefined) return pending;
    const chunks = deps.context().$chunks;
    const url = src ?? (isRecord(chunks) ? asString(chunks[key]) : undefined);
    if (url === undefined || attempts >= CHUNK_ATTEMPTS)
      return Promise.resolve(undefined);
    attempts += 1;
    // Any end before the module registers fails, a file that ran without it included.
    cancel = appendScript(doc, url, deps.nonce, (_loaded, el) => {
      // A retry appends a new tag, so the failed one is removed.
      el.remove();
      pending = undefined;
      settle?.(undefined);
      fire(doc, deps.dispatch, "partial:error", {
        kind: "asset",
        url,
        error: new Error(`${key} chunk failed`),
      } satisfies PartialError);
    });
    pending = new Promise((resolve) => (settle = resolve));
    return pending;
  }

  return {
    get: () => value,
    load,
    whenLoaded(fn) {
      if (landed) fn(value as T);
      else if (attempts < CHUNK_ATTEMPTS || pending !== undefined) {
        waiting.push(fn);
        void load();
      }
    },
    landed: () => landed,
    land(module) {
      if (landed) return;
      landed = true;
      value = module;
      cancel?.();
      settle?.(module);
      for (const fn of waiting.splice(0)) fn(module);
    },
  };
}

/** Build the scripts chunk loader, which configures the chunk with each payload. */
export function createScriptsLoader(
  deps: ScriptsLoaderDeps,
  chunk: LazyModule<ScriptsFactory>,
): ScriptsLoader {
  const doc = deps.document ?? document;
  let built: ScriptsModule | undefined;
  // Set by the first init. From then on deps.context returns the latest payload.
  let seeded = false;
  // The ready calls made before init, resolved by init once the payload is known.
  const early: ((landing: Promise<ScriptsChunk>) => void)[] = [];

  // Later calls reuse the surfaces built the first time.
  function build(factory: ScriptsFactory): ScriptsModule {
    if (built === undefined) {
      built = factory(deps.host);
      deps.install(built);
      // Read at build time, since init may have stored a newer payload meanwhile.
      built.configure(deps.context());
    }
    return built;
  }

  // Called only after init stored a payload.
  async function land(): Promise<ScriptsChunk> {
    // A chunk that has already loaded is built synchronously, without an await.
    const factory = chunk.get() ?? (await chunk.load());
    if (factory === undefined)
      throw new Error("[next] the scripts chunk is unavailable");
    return build(factory);
  }

  return {
    ready() {
      if (seeded) return land();
      return new Promise((resolve, reject) => {
        // The inline init runs while the document parses, so none by its end means none.
        const abandon = (): void => {
          if (!seeded) reject(new Error(NO_PAYLOAD));
        };
        if (doc.readyState !== "loading") return abandon();
        early.push(resolve);
        doc.addEventListener("DOMContentLoaded", abandon, { once: true });
      });
    },
    init(next) {
      seeded = true;
      if (built !== undefined) built.configure(next);
      else if (early.length > 0 || CHUNK_KEYS.some((key) => next[key] !== undefined)) {
        // A chunk landing after its timeout is still built and configured.
        chunk.whenLoaded(build);
        const landing = land();
        // The loader already reported the failure as a partial:error.
        landing.catch(() => undefined);
        for (const resolve of early.splice(0)) resolve(landing);
      }
    },
  };
}
