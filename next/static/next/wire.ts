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
}

export type EnvelopeHandler = (raw: unknown, response: Response) => void;

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
}

interface QueueEntry {
  controller: AbortController;
  seq: number;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

export class Wire {
  readonly #fetch: FetchAdapter;
  readonly #navigate: Navigate;
  readonly #dispatch: (event: string, detail: Record<string, unknown>) => void;
  readonly #onEnvelope: EnvelopeHandler;
  readonly #version: () => string;
  readonly #csrf: () => CsrfPayload | undefined;

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

  async fetch(request: WireRequest): Promise<void> {
    const method = (request.method ?? "GET").toUpperCase();
    const safe = SAFE_METHODS.has(method);
    if (!safe && request.uid !== undefined) {
      // Per-uid mutation lock: a second submit drops while busy, a double
      // click yields exactly one fetch (invariant 8).
      if (this.#busy.has(request.uid)) return;
      this.#busy.add(request.uid);
    }
    const queueKey = safe ? request.zone : undefined;
    const entry = queueKey !== undefined ? this.#enqueue(queueKey) : undefined;
    try {
      await this.#run(request, method, entry);
    } finally {
      if (!safe && request.uid !== undefined) {
        this.#busy.delete(request.uid);
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
    await this.#classify(request, method, response);
  }

  async #classify(
    request: WireRequest,
    method: string,
    response: Response,
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
      this.#onEnvelope(hook(response, body), response);
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
    this.#onEnvelope(raw, response);
  }

  #text(response: Response): Promise<string> {
    return response.text();
  }

  #headers(request: WireRequest, method: string): Record<string, string> {
    const headers: Record<string, string> = {
      [REQUEST_FLAG]: "1",
      [HEADER_ACCEPT]: ACCEPT,
      [HEADER_VERSION]: this.#version(),
      ...request.headers,
    };
    if (request.zone !== undefined) headers[HEADER_ZONE] = request.zone;
    if (!SAFE_METHODS.has(method)) {
      const csrf = this.#csrf();
      if (csrf !== undefined) headers[csrf.header] = csrf.token;
    }
    return headers;
  }
}
