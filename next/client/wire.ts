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
  newId,
  pageKey,
  sameOrigin,
} from "./protocol";
import type { PartialError } from "./protocol";
import { defaultFetch, defaultNavigate, defaultSession } from "./adapters";
import type { SessionStore } from "./assets";

const SAFE_METHODS = new Set(["GET", "HEAD"]);

// Kept apart from the version guard's flag in assets.ts, so neither clears the other.
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

/**
 * The CSRF header name with its token, or with the endpoint that mints one.
 *
 * A shared page is cached for everyone, so it ships the endpoint instead of a token.
 */
export interface CsrfPayload {
  header: string;
  token?: string;
  url?: string;
}

/** The token a mutation stamps, ensure fetching a deferred one on first need. */
export interface CsrfSource {
  current(): CsrfPayload | undefined;
  ensure(): Promise<CsrfPayload | undefined>;
}

/** A single wire request with its queueing and locking intent. */
export interface WireRequest {
  url: string;
  method?: string;
  // A mutation locks on the form uid, and a safe GET queues on its named queue or on
  // url and zone. Absent both, the request runs unqueued and unlocked.
  uid?: string;
  // The X-Next-Zone value, absent when the answer addresses the whole page.
  zone?: string;
  // The queue and abort identity, defaulting to the zone. An inline validation names
  // its own key, and the zone it declares is still sent as the header.
  queue?: string;
  headers?: Record<string, string>;
  body?: BodyInit;
  // An inline validation uses a POST to send the body but mutates nothing, so it
  // joins the abortable queue and skips the mutation lock.
  abortable?: boolean;
  // The initiating form's data-next-key, threaded to apply for a repeated form.
  key?: string;
  // The page a mutation was submitted from, which receives the meta op of the reply.
  owner?: string;
}

/**
 * Receiver of a recognised envelope. The snapshot is the dirty counter captured at
 * fetch time so a field touched after the request is protected from its own
 * response. The page is the URL a safe zone GET fetched, absent on mutations. The
 * owner is the page a mutation was submitted from.
 */
export type EnvelopeHandler = (
  raw: unknown,
  response: Response,
  snapshot: number,
  key: string | undefined,
  page: string | undefined,
  owner: string | undefined,
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
  csrf: CsrfSource;
  // The dirty counter read at fetch time, threaded to apply with the response.
  dirtySnapshot?: () => number;
  // Every mutating request stamps X-Next-Request-Id and reports it here so the
  // SSE echo ring drops the matching stream event. Absent, no id is stamped.
  rememberRequestId?: (id: string) => void;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
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
  readonly #csrf: CsrfSource;
  readonly #dirtySnapshot: () => number;
  readonly #rememberRequestId: (id: string) => void;

  // Latest-wins per-target GET queues and the per-uid mutation lock. A queue holds
  // only its in-flight request, so a key is removed once it settles or is aborted.
  readonly #queues = new Map<string, AbortController>();
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
    this.#csrf = deps.csrf;
    this.#dirtySnapshot = deps.dirtySnapshot ?? (() => 0);
    this.#rememberRequestId = deps.rememberRequestId ?? (() => undefined);
  }

  /** Abort every in-flight request and drop all state, for vitest isolation. */
  _reset(): void {
    for (const controller of this.#queues.values()) {
      controller.abort();
    }
    this.#queues.clear();
    this.#busy.clear();
    this.#parseHooks.clear();
  }

  /**
   * Register a parse-hook per content-type. The hook reads the body before
   * classification, so a foreign wire format never reaches navigation.
   */
  parseHook(contentType: string, hook: ParseHook): void {
    this.#parseHooks.set(contentType, hook);
  }

  /**
   * Abort the in-flight request on a queue without starting a new one, so a form submit
   * can cancel its own inline validation and a late reply to it is dropped.
   */
  abort(key: string): void {
    this.#queues.get(key)?.abort();
    this.#queues.delete(key);
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
      // A second submit is dropped while busy, so a double click sends one fetch.
      if (this.#busy.has(uid)) return;
      this.#busy.add(uid);
    }
    // Queued before a token wait, so a submit can still abort its own validation.
    const queueKey = this.#queueKey(request, safe);
    const entry = queueKey !== undefined ? this.#enqueue(queueKey) : undefined;
    try {
      let csrf = this.#csrf.current();
      // A deferred token is fetched inside the lock, so a double submit fetches once.
      if (!safe && csrf?.token === undefined && csrf?.url !== undefined) {
        csrf = await this.#csrf.ensure();
        if (csrf?.token === undefined) {
          this.#dispatch("partial:error", {
            kind: "csrf",
            url: request.url,
            error: new Error("no CSRF token for the mutation"),
          } satisfies PartialError);
          return;
        }
      }
      await this.#run(request, target, method, csrf, queueKey, entry);
    } finally {
      if (locked) {
        this.#busy.delete(uid);
      }
      if (queueKey !== undefined && this.#queues.get(queueKey) === entry) {
        this.#queues.delete(queueKey);
      }
    }
  }

  // A zone GET queues per path+zone so two pages sharing a zone name run
  // independently while a re-filtered GET of the same page supersedes its
  // predecessor. The space separator cannot appear in either part. A named queue
  // stays bare, the key abort() addresses.
  #queueKey(request: WireRequest, safe: boolean): string | undefined {
    if (request.queue !== undefined) {
      return safe || request.abortable === true ? request.queue : undefined;
    }
    if (request.zone === undefined) return undefined;
    if (safe) return `${request.url.split("?")[0]} ${request.zone}`;
    return request.abortable === true ? request.zone : undefined;
  }

  // A new safe GET to a target aborts the in-flight one (latest-wins). A response
  // whose controller no longer holds the key is dropped.
  #enqueue(key: string): AbortController {
    this.#queues.get(key)?.abort();
    const controller = new AbortController();
    this.#queues.set(key, controller);
    return controller;
  }

  async #run(
    request: WireRequest,
    target: string,
    method: string,
    csrf: CsrfPayload | undefined,
    queueKey: string | undefined,
    entry: AbortController | undefined,
  ): Promise<void> {
    const headers = this.#headers(request, method, csrf);
    const init: RequestInit = { method, headers, mode: "same-origin" };
    if (request.body !== undefined) init.body = request.body;
    if (entry !== undefined) init.signal = entry.signal;
    // Snapshot the dirty counter before the request is sent. A field touched after
    // this point is dirty relative to the response it will receive.
    const snapshot = this.#dirtySnapshot();
    this.#dispatch("partial:before-request", {
      url: request.url,
      method,
      intent: { zone: request.zone, uid: request.uid },
    });
    // The body read runs inside the try too, since an abort or a dropped connection
    // can reject it after the headers arrived.
    try {
      const response = await this.#fetch(target, init);
      // A safe-GET response that a newer request superseded is dropped unreported.
      if (queueKey !== undefined && this.#queues.get(queueKey) !== entry) return;
      await this.#classify(request, target, method, response, snapshot);
    } catch (error) {
      // An AbortError means the request was superseded, so nothing is reported.
      if (isAbortError(error)) return;
      this.#dispatch("partial:error", {
        kind: "network",
        error,
      } satisfies PartialError);
    }
  }

  async #classify(
    request: WireRequest,
    target: string,
    method: string,
    response: Response,
    snapshot: number,
  ): Promise<void> {
    // A 409 on a safe method means an asset version mismatch with an empty body. The
    // runtime then does a full visit of the current URL and nothing else.
    if (response.status === 409 && SAFE_METHODS.has(method)) {
      this.#navigate(response.url || target);
      return;
    }
    if (response.status >= 500) {
      const body = await response.text();
      this.#dispatch("partial:error", {
        kind: "http",
        status: response.status,
        body,
      } satisfies PartialError);
      return;
    }
    // Only a safe zone GET names a page. Mutations keep the unscoped resolve.
    const page =
      SAFE_METHODS.has(method) && request.zone !== undefined
        ? pageKey(request.url, this.#document)
        : undefined;
    const contentType = response.headers.get("content-type") ?? "";
    const baseType = contentType.replace(/;.*$/, "").trim();
    const hook = this.#parseHooks.get(baseType);
    // A non-envelope reply or a redirect is a full navigation to the final URL. On a
    // mutation the URL is the action endpoint, so navigating there would 405. A
    // parse hook handles its content type, so its body never reaches navigation.
    if (hook === undefined && (baseType !== CONTENT_TYPE || response.redirected)) {
      if (response.redirected || SAFE_METHODS.has(method)) {
        this.#fallbackNavigate(response.url || target);
        return;
      }
      const body = await response.text();
      this.#dispatch("partial:error", {
        kind: "http",
        status: response.status,
        body,
      } satisfies PartialError);
      return;
    }
    const body = await response.text();
    // A body the hook or JSON cannot parse, or an envelope apply rejects as malformed,
    // is a parse error rather than an unhandled rejection of a fire-and-forget fetch.
    try {
      const raw: unknown = hook !== undefined ? hook(response, body) : JSON.parse(body);
      // Cleared once the body parses, so a later non-envelope reply on this page
      // navigates once more.
      this.#session.remove(NAVIGATED_FLAG);
      this.#onEnvelope(raw, response, snapshot, request.key, page, request.owner);
    } catch (error) {
      this.#dispatch("partial:error", {
        kind: "parse",
        body,
        error,
      } satisfies PartialError);
    }
  }

  // Guarded so a page that keeps replying with a non-envelope (login redirect, WAF
  // stub, maintenance) cannot loop navigation, since a `lazy="load"` zone requests
  // again on every page. The first navigates and sets the flag, and a second while it
  // is set fires a partial:error and leaves the page in place.
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

  // Headers, not a plain record, so a caller writing a name in another case still
  // matches the runtime's header of that name and each header is set exactly once.
  #headers(
    request: WireRequest,
    method: string,
    csrf: CsrfPayload | undefined,
  ): Headers {
    const headers = new Headers({
      [REQUEST_FLAG]: "1",
      [HEADER_ACCEPT]: ACCEPT,
      ...request.headers,
    });
    // The version is sent only after the client has read one from an envelope, so
    // the first request of a page asserts no stale version.
    const version = this.#version();
    if (version) headers.set(HEADER_VERSION, version);
    if (request.zone !== undefined) headers.set(HEADER_ZONE, request.zone);
    if (!SAFE_METHODS.has(method)) {
      if (csrf?.token !== undefined) headers.set(csrf.header, csrf.token);
      // A true mutation carries a ring id so the SSE bridge suppresses its own echo.
      if (request.abortable !== true && !headers.has(HEADER_REQUEST_ID)) {
        const id = newId();
        headers.set(HEADER_REQUEST_ID, id);
        this.#rememberRequestId(id);
      }
    }
    return headers;
  }
}
