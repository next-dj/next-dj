// The fetch layer: intent headers, CSRF from the payload, response
// classification (invariant 9 "a non-envelope is navigation"), per-target GET
// queues with latest-wins aborts, and the per-uid mutation lock.

import { CONTENT_TYPE, ACCEPT } from "./apply";
import { defaultFetch, defaultNavigate } from "./adapters";

export { CONTENT_TYPE, ACCEPT } from "./apply";

export const REQUEST_FLAG = "X-Next-Request";
export const HEADER_ACCEPT = "Accept";
export const HEADER_ZONE = "X-Next-Zone";
export const HEADER_MERGE = "X-Next-Merge";
export const HEADER_VERSION = "X-Next-Version";
export const HEADER_REQUEST_ID = "X-Next-Request-Id";
export const HEADER_ORIGIN = "X-Next-Origin";

const SAFE_METHODS = new Set(["GET", "HEAD"]);

// Stand-ins for the platform globals so vitest drives fetch and the clock
// deterministically and jsdom's missing navigation hook is mockable.
export type FetchAdapter = (input: string, init: RequestInit) => Promise<Response>;

export interface Clock {
  now(): number;
  setTimeout(handler: () => void, ms: number): number;
  clearTimeout(handle: number): void;
}

export type Navigate = (url: string) => void;

export interface CsrfPayload {
  header: string;
  token: string;
}

export interface WireRequest {
  url: string;
  method?: string;
  // The lock key for mutations is the form uid, the queue key for safe GETs is
  // the target zone. Absent, the request runs unqueued and unlocked.
  uid?: string;
  zone?: string;
  headers?: Record<string, string>;
  body?: BodyInit;
  // An inline validation rides a POST to carry the body and reach the validate
  // branch, but it mutates nothing, so it joins the abortable zone queue and
  // skips the mutation lock: a fresh blur or a submit aborts it (latest-wins).
  abortable?: boolean;
}

// The snapshot is the dirty counter captured at fetch time, threaded to apply so
// a field touched after this request is protected from its own response.
export type EnvelopeHandler = (
  raw: unknown,
  response: Response,
  snapshot: number,
) => void;

// A parse-hook turns a non-default content-type body into a JSON-ish envelope.
// Plugins register a foreign wire format here, before the apply pipeline.
export type ParseHook = (response: Response, body: string) => unknown;

export interface WireDeps {
  fetch?: FetchAdapter;
  navigate?: Navigate;
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  onEnvelope: EnvelopeHandler;
  version?: () => string;
  csrf?: () => CsrfPayload | undefined;
  // The dirty counter read at fetch time, threaded to apply with the response.
  dirtySnapshot?: () => number;
  // The mutation request id sink: every mutating request stamps X-Next-Request-Id
  // and reports it here so the SSE echo ring drops the matching stream event,
  // whose POST already brought the fresh zone. Absent, no id is stamped.
  rememberRequestId?: (id: string) => void;
}

interface QueueEntry {
  controller: AbortController;
  seq: number;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

// A ring id for a mutation, unique enough to suppress the SSE echo of this
// client's own change. crypto.randomUUID is present in every secure context and
// in jsdom, the timestamp fallback covers a plain-HTTP origin.
function newRequestId(): string {
  return globalThis.crypto?.randomUUID
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export class Wire {
  readonly #fetch: FetchAdapter;
  readonly #navigate: Navigate;
  readonly #dispatch: (event: string, detail: Record<string, unknown>) => void;
  readonly #onEnvelope: EnvelopeHandler;
  readonly #version: () => string;
  readonly #csrf: () => CsrfPayload | undefined;
  readonly #dirtySnapshot: () => number;
  readonly #rememberRequestId: (id: string) => void;

  // Latest-wins per-target GET queues and the per-uid mutation lock. Both are
  // wiped by `_reset` so vitest files start from a clean slate.
  readonly #queues: Map<string, QueueEntry> = new Map();
  readonly #busy: Set<string> = new Set();
  readonly #parseHooks: Map<string, ParseHook> = new Map();

  constructor(deps: WireDeps) {
    this.#fetch = deps.fetch ?? defaultFetch();
    this.#navigate = deps.navigate ?? defaultNavigate();
    this.#dispatch = deps.dispatch;
    this.#onEnvelope = deps.onEnvelope;
    this.#version = deps.version ?? (() => "");
    this.#csrf = deps.csrf ?? (() => undefined);
    this.#dirtySnapshot = deps.dirtySnapshot ?? (() => 0);
    this.#rememberRequestId = deps.rememberRequestId ?? (() => undefined);
  }

  _reset(): void {
    for (const entry of this.#queues.values()) {
      entry.controller.abort();
    }
    this.#queues.clear();
    this.#busy.clear();
    this.#parseHooks.clear();
  }

  // A plugin registers a parse-hook per content-type. The hook owns the body
  // before classification, so a foreign wire format never reaches navigation.
  parseHook(contentType: string, hook: ParseHook): void {
    this.#parseHooks.set(contentType, hook);
  }

  // Abort the in-flight request on a zone queue without starting a new one. A
  // form submit calls this to cancel its own inline validation: the bumped seq
  // also makes any answer already on the wire discard itself.
  abort(zone: string): void {
    const entry = this.#queues.get(zone);
    if (entry === undefined) return;
    entry.controller.abort();
    // Bump the seq so a response that resolves before the abort is observed is
    // still dropped as stale.
    this.#queues.set(zone, {
      controller: new AbortController(),
      seq: entry.seq + 1,
    });
  }

  async fetch(request: WireRequest): Promise<void> {
    const method = (request.method ?? "GET").toUpperCase();
    const safe = SAFE_METHODS.has(method);
    // An abortable request (inline validation) is queue-managed like a safe GET
    // even though it is a POST: it never takes the mutation lock.
    const locked = !safe && !request.abortable && request.uid !== undefined;
    if (locked) {
      // Per-uid mutation lock: a second submit drops while busy, a double
      // click yields exactly one fetch (invariant 8).
      if (this.#busy.has(request.uid!)) return;
      this.#busy.add(request.uid!);
    }
    const queueKey = safe || request.abortable === true ? request.zone : undefined;
    const entry = queueKey !== undefined ? this.#enqueue(queueKey) : undefined;
    try {
      await this.#run(request, method, entry);
    } finally {
      if (locked) {
        this.#busy.delete(request.uid!);
      }
    }
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
    method: string,
    entry: QueueEntry | undefined,
  ): Promise<void> {
    const headers = this.#headers(request, method);
    const init: RequestInit = { method, headers };
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
      response = await this.#fetch(request.url, init);
    } catch (error) {
      // AbortError is never an error: the user moved on, no toast, no event.
      if (isAbortError(error)) return;
      this.#dispatch("partial:error", { status: 0, body: "", error });
      return;
    }
    // A stale safe-GET response that lost its race is dropped silently.
    if (entry !== undefined && this.#queues.get(request.zone!)?.seq !== entry.seq) {
      return;
    }
    await this.#classify(request, method, response, snapshot);
  }

  async #classify(
    request: WireRequest,
    method: string,
    response: Response,
    snapshot: number,
  ): Promise<void> {
    // 409 on a safe method means an asset version mismatch with an empty body:
    // the runtime does a full visit of the current URL, nothing else.
    if (response.status === 409 && SAFE_METHODS.has(method)) {
      this.#navigate(response.url || request.url);
      return;
    }
    if (response.status >= 500) {
      const body = await this.#text(response);
      this.#dispatch("partial:error", { status: response.status, body, error: null });
      return;
    }
    const contentType = response.headers.get("content-type") ?? "";
    const baseType = contentType.split(";")[0].trim();
    const hook = this.#parseHooks.get(baseType);
    if (hook !== undefined) {
      const body = await this.#text(response);
      this.#onEnvelope(hook(response, body), response, snapshot);
      return;
    }
    // Invariant 9: any non-envelope content-type, or a redirected response, is
    // a full navigation to the final URL. No attempt to parse the body.
    if (baseType !== CONTENT_TYPE || response.redirected) {
      this.#navigate(response.url || request.url);
      return;
    }
    const body = await this.#text(response);
    let raw: unknown;
    try {
      raw = JSON.parse(body);
    } catch (error) {
      this.#dispatch("partial:error", { status: response.status, body, error });
      return;
    }
    this.#onEnvelope(raw, response, snapshot);
  }

  #text(response: Response): Promise<string> {
    return response.text();
  }

  #headers(request: WireRequest, method: string): Record<string, string> {
    const headers: Record<string, string> = {
      [REQUEST_FLAG]: "1",
      [HEADER_ACCEPT]: ACCEPT,
      ...request.headers,
    };
    // The version travels only once the client has learned one from an
    // envelope, so the first request of a page asserts no stale version.
    const version = this.#version();
    if (version) headers[HEADER_VERSION] = version;
    if (request.zone !== undefined) headers[HEADER_ZONE] = request.zone;
    if (!SAFE_METHODS.has(method)) {
      const csrf = this.#csrf();
      if (csrf !== undefined) headers[csrf.header] = csrf.token;
      // A true mutation (not an abortable validate POST) carries a ring id so
      // the SSE bridge suppresses its own echo. The id is reported to the ring
      // before it leaves, the fresh zone the POST returns arrives anyway.
      if (request.abortable !== true && headers[HEADER_REQUEST_ID] === undefined) {
        const id = newRequestId();
        headers[HEADER_REQUEST_ID] = id;
        this.#rememberRequestId(id);
      }
    }
    return headers;
  }
}
