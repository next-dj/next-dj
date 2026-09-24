// The fetch layer: intent headers, CSRF, response classification, per-target
// GET queues with latest-wins aborts, and the per-uid mutation lock.

import {
  ACCEPT,
  CONTENT_TYPE,
  HEADER_ACCEPT,
  HEADER_REQUEST_ID,
  HEADER_VERSION,
  HEADER_ZONE,
  REQUEST_FLAG,
  sameOrigin,
} from "./protocol";
import type { PartialError } from "./protocol";
import { defaultFetch, defaultNavigate, defaultSession } from "./adapters";
import type { SessionStore } from "./assets";

const SAFE_METHODS = new Set(["GET", "HEAD"]);

// Kept apart from the version guard's flag in assets.ts, one must not clear the other.
const NAVIGATED_FLAG = "next:partial:navigated";

/** Fetch stand-in so vitest can drive requests deterministically. */
export type FetchAdapter = (input: string, init: RequestInit) => Promise<Response>;

/** Clock stand-in so vitest can drive time deterministically. */
export interface Clock {
  now(): number;
  setTimeout(handler: () => void, ms: number): number;
  clearTimeout(handle: number): void;
}

/** Navigation stand-in so jsdom's missing navigation hook is mockable. */
export type Navigate = (url: string) => void;

/** The CSRF header name and token carried by mutating requests. */
export interface CsrfPayload {
  header: string;
  token: string;
}

/** A single wire request with its queueing and locking intent. */
export interface WireRequest {
  url: string;
  method?: string;
  // A mutation locks on the form uid, a safe GET queues on url plus queue key.
  // Absent both, the request runs unqueued and unlocked.
  uid?: string;
  // The X-Next-Zone value, absent when the answer addresses the whole page.
  zone?: string;
  // The queue and abort identity, defaulting to the zone. An inline validation names
  // its own key, so the zone it declares still travels as the header.
  queue?: string;
  headers?: Record<string, string>;
  body?: BodyInit;
  // An inline validation rides a POST to carry the body but mutates nothing, so it
  // joins the abortable queue and skips the mutation lock.
  abortable?: boolean;
  // The initiating form's data-next-key, threaded to apply for a repeated form.
  key?: string;
}

/**
 * Sink for a recognised envelope. The snapshot is the dirty counter captured at
 * fetch time so a field touched after the request is protected from its own
 * response. The page is the URL a safe zone GET fetched, absent on mutations.
 */
export type EnvelopeHandler = (
  raw: unknown,
  response: Response,
  snapshot: number,
  key: string | undefined,
  page: string | undefined,
) => void;

/** Turns a foreign content-type body into a JSON-ish envelope before apply. */
export type ParseHook = (response: Response, body: string) => unknown;

/** Injected collaborators for a Wire, all defaulted for the browser. */
export interface WireDeps {
  fetch?: FetchAdapter;
  document?: Document;
  navigate?: Navigate;
  // The navigate-once store of the non-envelope fallback. Absent, the default
  // wraps sessionStorage, the same store the version guard writes to.
  session?: SessionStore;
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  onEnvelope: EnvelopeHandler;
  version?: () => string;
  csrf?: () => CsrfPayload | undefined;
  // The dirty counter read at fetch time, threaded to apply with the response.
  dirtySnapshot?: () => number;
  // Every mutating request stamps X-Next-Request-Id and reports it here so the
  // SSE echo ring drops the matching stream event. Absent, no id is stamped.
  rememberRequestId?: (id: string) => void;
}

interface QueueEntry {
  controller: AbortController;
  seq: number;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

// The timestamp fallback covers a plain-HTTP origin, where crypto.randomUUID is
// absent and the runtime object is narrower than the lib type claims.
function newRequestId(): string {
  const impl = globalThis.crypto as { randomUUID?: () => string } | undefined;
  return impl?.randomUUID
    ? impl.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** Shapes requests, classifies responses, and queues them per target. */
export class Wire {
  readonly #fetch: FetchAdapter;
  readonly #document: Document;
  readonly #navigate: Navigate;
  readonly #session: SessionStore;
  readonly #dispatch: (event: string, detail: Record<string, unknown>) => void;
  readonly #onEnvelope: EnvelopeHandler;
  readonly #version: () => string;
  readonly #csrf: () => CsrfPayload | undefined;
  readonly #dirtySnapshot: () => number;
  readonly #rememberRequestId: (id: string) => void;

  // Latest-wins per-target GET queues and the per-uid mutation lock.
  readonly #queues = new Map<string, QueueEntry>();
  readonly #busy = new Set<string>();
  readonly #parseHooks = new Map<string, ParseHook>();

  constructor(deps: WireDeps) {
    this.#fetch = deps.fetch ?? defaultFetch();
    this.#document = deps.document ?? document;
    this.#navigate = deps.navigate ?? defaultNavigate();
    this.#session = deps.session ?? defaultSession();
    this.#dispatch = deps.dispatch;
    this.#onEnvelope = deps.onEnvelope;
    this.#version = deps.version ?? (() => "");
    this.#csrf = deps.csrf ?? (() => undefined);
    this.#dirtySnapshot = deps.dirtySnapshot ?? (() => 0);
    this.#rememberRequestId = deps.rememberRequestId ?? (() => undefined);
  }

  /** Abort every in-flight request and drop all state, for vitest isolation. */
  _reset(): void {
    for (const entry of this.#queues.values()) {
      entry.controller.abort();
    }
    this.#queues.clear();
    this.#busy.clear();
    this.#parseHooks.clear();
  }

  /**
   * Register a parse-hook per content-type. The hook owns the body before
   * classification, so a foreign wire format never reaches navigation.
   */
  parseHook(contentType: string, hook: ParseHook): void {
    this.#parseHooks.set(contentType, hook);
  }

  /**
   * Abort the in-flight request on a queue without starting a new one, so a form submit
   * can cancel its own inline validation and the bumped seq drops a late answer.
   */
  abort(key: string): void {
    const entry = this.#queues.get(key);
    if (entry === undefined) return;
    entry.controller.abort();
    // Bump the seq so a response resolving before the abort lands is still dropped.
    this.#queues.set(key, {
      controller: new AbortController(),
      seq: entry.seq + 1,
    });
  }

  /** Shape, queue or lock, send, and classify a single request. */
  async fetch(request: WireRequest): Promise<void> {
    const target = sameOrigin(request.url, this.#document);
    if (target === undefined) {
      this.#dispatch("partial:error", {
        kind: "network",
        url: request.url,
        error: new Error("cross-origin request refused"),
      } satisfies PartialError);
      return;
    }
    const method = (request.method ?? "GET").toUpperCase();
    const safe = SAFE_METHODS.has(method);
    const uid = request.uid;
    // An abortable POST (inline validation) queues like a safe GET, taking no lock.
    const locked = !safe && !request.abortable && uid !== undefined;
    if (locked) {
      // A second submit drops while busy, so a double click yields one fetch.
      if (this.#busy.has(uid)) return;
      this.#busy.add(uid);
    }
    const queueKey = this.#queueKey(request, safe);
    const entry = queueKey !== undefined ? this.#enqueue(queueKey) : undefined;
    try {
      await this.#run(request, target, method, queueKey, entry);
    } finally {
      if (locked) {
        this.#busy.delete(uid);
      }
    }
  }

  // A safe GET queues per path+key so two pages sharing a zone name run
  // independently while a re-filtered GET of the same page supersedes its
  // predecessor. The space separator cannot appear in either part. An abortable
  // POST keeps the bare queue key that abort() addresses.
  #queueKey(request: WireRequest, safe: boolean): string | undefined {
    const key = request.queue ?? request.zone;
    if (key === undefined) return undefined;
    if (safe) return `${request.url.split("?")[0]} ${key}`;
    return request.abortable === true ? key : undefined;
  }

  // A new safe GET to a target aborts the in-flight one (latest-wins). The
  // monotonic seq lets the response discard itself when a fresher one started.
  #enqueue(key: string): QueueEntry {
    const previous = this.#queues.get(key);
    if (previous !== undefined) {
      previous.controller.abort();
    }
    const entry: QueueEntry = {
      controller: new AbortController(),
      seq: (previous?.seq ?? 0) + 1,
    };
    this.#queues.set(key, entry);
    return entry;
  }

  async #run(
    request: WireRequest,
    target: string,
    method: string,
    queueKey: string | undefined,
    entry: QueueEntry | undefined,
  ): Promise<void> {
    const headers = this.#headers(request, method);
    const init: RequestInit = { method, headers, mode: "same-origin" };
    if (request.body !== undefined) init.body = request.body;
    if (entry !== undefined) init.signal = entry.controller.signal;
    // Snapshot the dirty counter before the request leaves: a field touched
    // after this point is dirty relative to the response it will receive.
    const snapshot = this.#dirtySnapshot();
    this.#dispatch("partial:before-request", {
      url: request.url,
      method,
      intent: { zone: request.zone, uid: request.uid },
    });
    let response: Response;
    try {
      response = await this.#fetch(target, init);
    } catch (error) {
      // AbortError is never an error: the user moved on, no toast, no event.
      if (isAbortError(error)) return;
      this.#dispatch("partial:error", {
        kind: "network",
        error,
      } satisfies PartialError);
      return;
    }
    // A stale safe-GET response that lost its race is dropped silently.
    if (
      entry !== undefined &&
      queueKey !== undefined &&
      this.#queues.get(queueKey)?.seq !== entry.seq
    ) {
      return;
    }
    await this.#classify(request, target, method, response, snapshot);
  }

  async #classify(
    request: WireRequest,
    target: string,
    method: string,
    response: Response,
    snapshot: number,
  ): Promise<void> {
    // 409 on a safe method means an asset version mismatch with an empty body:
    // the runtime does a full visit of the current URL, nothing else.
    if (response.status === 409 && SAFE_METHODS.has(method)) {
      this.#navigate(response.url || target);
      return;
    }
    if (response.status >= 500) {
      const body = await this.#text(response);
      this.#dispatch("partial:error", {
        kind: "http",
        status: response.status,
        body,
      } satisfies PartialError);
      return;
    }
    // Only a safe zone GET names a page: mutations keep the unscoped resolve.
    const page =
      SAFE_METHODS.has(method) && request.zone !== undefined ? request.url : undefined;
    const contentType = response.headers.get("content-type") ?? "";
    const baseType = contentType.replace(/;.*$/, "").trim();
    const hook = this.#parseHooks.get(baseType);
    if (hook !== undefined) {
      const body = await this.#text(response);
      this.#deliver(hook(response, body), response, snapshot, request.key, page);
      return;
    }
    // A non-envelope reply or a redirect is a full navigation to the final URL. On a
    // mutation the URL is the action endpoint, so navigating there would 405.
    if (baseType !== CONTENT_TYPE || response.redirected) {
      if (response.redirected || SAFE_METHODS.has(method)) {
        this.#fallbackNavigate(response.url || target);
        return;
      }
      const body = await this.#text(response);
      this.#dispatch("partial:error", {
        kind: "http",
        status: response.status,
        body,
      } satisfies PartialError);
      return;
    }
    const body = await this.#text(response);
    let raw: unknown;
    try {
      raw = JSON.parse(body);
    } catch (error) {
      this.#dispatch("partial:error", {
        kind: "parse",
        body,
        error,
      } satisfies PartialError);
      return;
    }
    this.#deliver(raw, response, snapshot, request.key, page);
  }

  // Clearing the navigate-once flag on a correct classify lets the next
  // non-envelope on the same page earn its own single navigation.
  #deliver(
    raw: unknown,
    response: Response,
    snapshot: number,
    key: string | undefined,
    page: string | undefined,
  ): void {
    this.#session.remove(NAVIGATED_FLAG);
    this.#onEnvelope(raw, response, snapshot, key, page);
  }

  // Guarded so a page that keeps answering non-envelope (login redirect, WAF
  // stub, maintenance) cannot loop navigation: a `lazy="load"` zone re-asks on
  // every page. The first navigates under the flag, a second while it stands
  // degrades to a partial:error and leaves the page in place.
  #fallbackNavigate(url: string): void {
    if (this.#session.get(NAVIGATED_FLAG) === "1") {
      this.#session.remove(NAVIGATED_FLAG);
      this.#dispatch("partial:error", {
        kind: "network",
        error: new Error("zone answered non-envelope after a navigation"),
      } satisfies PartialError);
      return;
    }
    this.#session.set(NAVIGATED_FLAG, "1");
    this.#navigate(url);
  }

  #text(response: Response): Promise<string> {
    return response.text();
  }

  // Headers, not a plain record, so a caller writing a name in another case still
  // collides with the runtime's own and each header is stamped exactly once.
  #headers(request: WireRequest, method: string): Headers {
    const headers = new Headers({
      [REQUEST_FLAG]: "1",
      [HEADER_ACCEPT]: ACCEPT,
      ...request.headers,
    });
    // The version travels only once the client has learned one from an
    // envelope, so the first request of a page asserts no stale version.
    const version = this.#version();
    if (version) headers.set(HEADER_VERSION, version);
    if (request.zone !== undefined) headers.set(HEADER_ZONE, request.zone);
    if (!SAFE_METHODS.has(method)) {
      const csrf = this.#csrf();
      if (csrf !== undefined) headers.set(csrf.header, csrf.token);
      // A true mutation carries a ring id so the SSE bridge suppresses its own echo.
      if (request.abortable !== true && !headers.has(HEADER_REQUEST_ID)) {
        const id = newRequestId();
        headers.set(HEADER_REQUEST_ID, id);
        this.#rememberRequestId(id);
      }
    }
    return headers;
  }
}
