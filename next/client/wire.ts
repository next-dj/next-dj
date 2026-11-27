// The fetch layer: intent headers, CSRF, response classification, per-target
// GET queues with latest-wins aborts, and the per-uid mutation lock.

import {
  ACCEPT,
  ATTR_BOUNDARY,
  CONTENT_TYPE,
  EVENT_STREAM,
  HEADER_ACCEPT,
  HEADER_PROTOCOL,
  HEADER_REQUEST_ID,
  HEADER_VERSION,
  HEADER_ZONE,
  NAVIGATED_FLAG,
  PROTOCOL,
  REFUSAL_HTTP_ONLY,
  REQUEST_FLAG,
  REQUEST_TIMEOUT_MS,
  STREAM_ACCEPT,
  bounded,
  markBusy,
  newId,
  now,
  pageKey,
  sameOrigin,
  stamp,
  stamped,
} from "./protocol";
import type { PartialError, WireTransport } from "./protocol";
import {
  defaultClock,
  defaultFetch,
  defaultNavigate,
  defaultReload,
  defaultSession,
} from "./adapters";
import type { Applied, BeforeApply, Envelope } from "./apply";
import type { SessionStore } from "./assets";

const SAFE_METHODS = new Set(["GET", "HEAD"]);

/** How one request ended, the value Next.partial.submit and Next.partial.fetch resolve. */
export interface Outcome {
  status: "ok" | "invalid" | "error" | "dropped";
  applied: boolean;
  errors?: Record<string, string[]> | undefined;
  envelope?: Envelope;
  error?: PartialError;
  location?: string;
  boundaries?: string[];
}

export const none = (status: Outcome["status"]): Outcome => ({
  status,
  applied: false,
});

export interface Settled {
  zones: string[];
  boundaries: string[];
  status: Outcome["status"];
}

function faultOf({ status, error }: Outcome): string | null | undefined {
  if (status === "dropped") return undefined;
  if (status !== "error") return null;
  return error?.kind === "timeout" || error?.kind === "network" ? error.kind : "error";
}

const zonesOf = (request: WireRequest): string[] => [
  ...new Set(
    request.zone
      ?.split(",")
      .map((zone) => zone.trim())
      .filter(Boolean),
  ),
];

export const zoneKey = (url: string, zone: string): string =>
  `${url.split("?")[0]} ${zone}`;

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
  // The CSRF cookie of an endpoint payload, named only while scripts may read it.
  cookie?: string;
}

/** The token a mutation stamps, ensure fetching a deferred one on first need. */
export interface CsrfSource {
  current(): CsrfPayload | undefined;
  // null when the csrf chunk failed to load, a failure its loader already reported.
  // An abort ends only this caller's wait, a mint another caller waits on goes on.
  ensure(signal?: AbortSignal): Promise<CsrfPayload | undefined | null>;
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
  owner?: string | undefined;
  transport?: WireTransport;
  wait?: boolean | "latest";
  navigate?: boolean;
  background?: boolean;
  boundaries?: Element[];
  // Sends the request in place of the fetch seam, a prefetched answer standing in.
  send?: FetchAdapter | undefined;
}

/** What a recognised envelope arrives with, read by name like ApplyOptions. */
export interface EnvelopeOptions {
  response: Response;
  // The dirty counter at fetch time, so a field touched later survives its response.
  snapshot: number;
  key: string | undefined;
  // The URL a safe zone GET fetched, absent on mutations.
  page: string | undefined;
  // The page a mutation was submitted from.
  owner: string | undefined;
  zones: string[];
  isCurrent: (zone?: string) => boolean;
  inspect?: ((detail: BeforeApply) => void) | undefined;
}

export type EnvelopeHandler = (
  raw: unknown,
  options: EnvelopeOptions,
) => Promise<Applied>;

export interface StreamTake {
  signal: AbortSignal;
  open: () => void;
  // inspect reads the before-apply detail of the envelope the body carries.
  land: (
    body: string,
    parse: () => unknown,
    inspect?: (detail: BeforeApply) => void,
  ) => Promise<Outcome>;
}

export type StreamReader = (response: Response, take: StreamTake) => Promise<Outcome>;

/** Turns a foreign content-type body into a JSON-ish envelope before apply. */
export type ParseHook = (response: Response, body: string) => unknown;

/** Injected collaborators for a Wire, all defaulted for the browser. */
export interface WireDeps {
  fetch?: FetchAdapter;
  document?: Document;
  navigate?: Navigate;
  clock?: Clock;
  // The full reload a reply the server keeps to HTTP falls back to.
  reload?: () => void;
  // The deadline in ms, read per request, zero waiting forever and absent the default.
  timeout?: () => number;
  // The navigate-once store of the non-envelope fallback. Absent, the default
  // wraps sessionStorage, the same store the version guard writes to.
  session?: SessionStore;
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  onEnvelope: EnvelopeHandler;
  version?: () => string;
  csrf: CsrfSource;
  // The dirty counter read at fetch time, threaded to apply with the response.
  dirtySnapshot?: () => number;
  // Invalidates in-flight work when the owning partial runtime is rebuilt.
  active?: () => boolean;
  // Every mutating request stamps X-Next-Request-Id and reports it here so the
  // SSE echo ring drops the matching stream event. Absent, no id is stamped.
  rememberRequestId?: (id: string) => void;
  // A mutation with no reply may still have committed, so its echo must refresh.
  forgetRequestId?: (id: string) => void;
  // A string is why a request the server received got no reply, undefined means fetch.
  tunnel?: (
    target: string,
    init: RequestInit,
    csrf?: string,
  ) => Promise<Response | string | undefined> | undefined;
  resolveZone?: (name: string, root: ParentNode, page?: string) => Element | null;
  reader?: () => StreamReader | undefined;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

/** Shapes requests, classifies responses, and queues them per target. */
export class Wire {
  readonly #fetch: FetchAdapter;
  readonly #document: Document;
  readonly #navigate: Navigate;
  readonly #clock: Clock;
  readonly #reload: () => void;
  readonly #timeout: () => number;
  readonly #session: SessionStore;
  readonly #dispatch: (event: string, detail: Record<string, unknown>) => void;
  readonly #onEnvelope: EnvelopeHandler;
  readonly #version: () => string;
  readonly #csrf: CsrfSource;
  readonly #dirtySnapshot: () => number;
  readonly #active: () => boolean;
  readonly #rememberRequestId: (id: string) => void;
  readonly #forgetRequestId: (id: string) => void;
  readonly #tunnel: NonNullable<WireDeps["tunnel"]>;
  readonly #resolveZone: NonNullable<WireDeps["resolveZone"]>;
  readonly #reader: NonNullable<WireDeps["reader"]>;

  // Latest-wins per-target GET queues and the per-uid mutation lock. A queue holds
  // only its in-flight request, so a key is removed once it settles or is aborted.
  readonly #queues = new Map<string, AbortController>();
  readonly #claims = new Map<string, (zone: string) => void>();
  readonly #busy = new Map<string, ((go: boolean) => void)[]>();
  readonly #parseHooks = new Map<string, ParseHook>();
  // The elements each queued request fills, so a sweep can stop the ones left orphaned.
  readonly #fills = new WeakMap<AbortController, Element[]>();
  /** Harness seam, absent from the shipped bundles: abort everything, drop all state. */
  declare _reset: () => void;

  constructor(deps: WireDeps) {
    this.#fetch = deps.fetch ?? defaultFetch();
    this.#document = deps.document ?? document;
    this.#navigate = deps.navigate ?? defaultNavigate();
    this.#clock = deps.clock ?? defaultClock();
    this.#reload = deps.reload ?? defaultReload();
    this.#timeout = deps.timeout ?? (() => REQUEST_TIMEOUT_MS);
    this.#session = deps.session ?? defaultSession();
    this.#dispatch = deps.dispatch;
    this.#onEnvelope = deps.onEnvelope;
    this.#version = deps.version ?? (() => "");
    this.#csrf = deps.csrf;
    this.#dirtySnapshot = deps.dirtySnapshot ?? (() => 0);
    this.#active = deps.active ?? (() => true);
    this.#rememberRequestId = deps.rememberRequestId ?? (() => undefined);
    this.#forgetRequestId = deps.forgetRequestId ?? (() => undefined);
    this.#tunnel = deps.tunnel ?? (() => undefined);
    this.#resolveZone = deps.resolveZone ?? (() => null);
    this.#reader = deps.reader ?? (() => undefined);
    TEST: this._reset = () => {
      for (const controller of this.#queues.values()) controller.abort();
      this.#queues.clear();
      for (const waiters of this.#busy.values())
        for (const resume of waiters.splice(0)) resume(false);
      this.#busy.clear();
      this.#parseHooks.clear();
    };
  }

  /**
   * Register a parse-hook per content-type. The hook reads the body before
   * classification, so a foreign wire format never reaches navigation.
   */
  parseHook(contentType: string, hook: ParseHook): void {
    this.#parseHooks.set(contentType, hook);
  }

  /** Decode an SSE envelope with the hook registered for its protocol content type. */
  parseStream(contentType: string, body: string): unknown {
    const baseType = contentType.replace(/;.*$/, "").trim();
    const hook = this.#parseHooks.get(baseType);
    const response = new Response(body, { headers: { "content-type": baseType } });
    return hook === undefined ? JSON.parse(body) : hook(response, body);
  }

  /**
   * Abort the in-flight request on a queue without starting a new one, so a form submit
   * can cancel its own inline validation and a late reply to it is dropped.
   */
  abort(key: string): void {
    this.#queues.get(key)?.abort();
    this.#queues.delete(key);
  }

  /**
   * Abort every queued request whose targets have all left the document.
   *
   * A navigation that replaced a zone or a boundary frees the server slot its still
   * streaming answer holds, instead of reading it to the end for nothing.
   */
  sweep(): void {
    for (const [key, controller] of this.#queues) {
      const fills = this.#fills.get(controller);
      if (fills?.length && fills.every((el) => !el.isConnected)) this.abort(key);
    }
  }

  /** Shape, queue or lock, send, and classify a single request. */
  async fetch(request: WireRequest): Promise<Outcome> {
    const zones = zonesOf(request);
    const boundaries = (request.boundaries ?? []).flatMap(
      (el) => el.getAttribute(ATTR_BOUNDARY) ?? [],
    );
    let status: Outcome["status"] = "error";
    try {
      const outcome = await this.#send(request, zones);
      status = outcome.status;
      boundaries.push(...(outcome.boundaries ?? []));
      return outcome;
    } finally {
      if (this.#active())
        this.#dispatch("partial:settled", { zones, boundaries, status });
    }
  }

  async #send(request: WireRequest, zones: string[]): Promise<Outcome> {
    if (!this.#active()) return none("dropped");
    const target = sameOrigin(request.url, this.#document);
    if (target === undefined) {
      return this.#fail(zones, {
        kind: "network",
        url: request.url,
        error: new Error("cross-origin request refused"),
      });
    }
    const method = (request.method ?? "GET").toUpperCase();
    const safe = SAFE_METHODS.has(method);
    const uid = request.uid;
    // An abortable POST (inline validation) queues like a safe GET, taking no lock.
    let unlock: (() => void) | undefined;
    if (!safe && !request.abortable && uid !== undefined) {
      // A second submit is dropped while busy, so a double click sends one fetch.
      const busy = this.#busy.get(uid);
      if (busy) {
        if (!request.wait) return none("dropped");
        if (request.wait === "latest")
          for (const resume of busy.splice(0)) resume(false);
        if (!(await new Promise<boolean>((resume) => busy.push(resume))))
          return none("dropped");
      }
      if (!this.#active()) return none("dropped");
      const waiters = busy ?? [];
      if (!busy) this.#busy.set(uid, waiters);
      unlock = () => {
        const next = waiters.shift();
        if (next) next(true);
        else this.#busy.delete(uid);
      };
    }
    // Queued before a token wait, so a submit can still abort its own validation.
    const queueKey = this.#queueKey(request, safe);
    const background = request.background === true;
    if (background && queueKey !== undefined && this.#queues.has(queueKey))
      return none("dropped");
    const entry = queueKey !== undefined ? this.#enqueue(queueKey) : undefined;
    const claiming = safe && request.queue === undefined;
    const page =
      safe && request.zone !== undefined
        ? pageKey(request.url, this.#document)
        : undefined;
    const held = new Map<string, (fault?: string | null) => void>();
    const lose = (zone: string): void => {
      held.get(zone)?.();
      held.delete(zone);
      if (held.size === 0) entry?.abort();
    };
    const targets = [...(request.boundaries ?? [])];
    const release = markBusy(targets);
    const free = (fault?: string | null): void => {
      release(fault);
      for (const [zone, settle] of held) {
        settle(fault);
        const key = zoneKey(request.url, zone);
        if (this.#claims.get(key) === lose) this.#claims.delete(key);
      }
    };
    entry?.signal.addEventListener("abort", () => free(), { once: true });
    let started = 0;
    const isCurrent = (zone?: string): boolean =>
      (zone === undefined || held.has(zone)) &&
      this.#active() &&
      entry?.signal.aborted !== true &&
      !request.boundaries?.some((el) => stamped(el) > started);
    let fault: string | null | undefined;
    try {
      for (const zone of zones) {
        const key = zoneKey(request.url, zone);
        const owner = claiming ? this.#claims.get(key) : undefined;
        if (background && owner !== undefined) continue;
        owner?.(zone);
        const el = this.#resolveZone(zone, this.#document, page);
        if (el !== null) targets.push(el);
        held.set(zone, markBusy([el], background));
        if (claiming) this.#claims.set(key, lose);
      }
      if (held.size === 0 && zones.length > 0) return none("dropped");
      if (entry) this.#fills.set(entry, targets);
      if (!background) targets.forEach(stamp);
      started = now();
      let csrf = this.#csrf.current();
      let outcome: Outcome | undefined;
      // A deferred token is fetched inside the lock, so a double submit fetches once.
      if (!safe && csrf?.token === undefined && csrf?.url !== undefined) {
        const ensured = await this.#ensureCsrf();
        // Told apart by the header, as a DOMException is not an Error in every realm.
        if (ensured === null) outcome = none("error");
        else if ("header" in ensured) csrf = ensured;
        else {
          outcome = this.#fail(zones, {
            kind: "csrf",
            url: request.url,
            error: ensured,
          });
        }
      }
      outcome ??= await this.#run(
        request,
        zones,
        target,
        method,
        csrf,
        entry,
        isCurrent,
        page,
      );
      fault = faultOf(outcome);
      return outcome;
    } finally {
      unlock?.();
      free(fault);
      if (queueKey !== undefined && this.#queues.get(queueKey) === entry) {
        this.#queues.delete(queueKey);
      }
    }
  }

  // A token fetch that never answers fails on the deadline, so it frees the uid lock.
  // A missing token or the deadline answers the Error the csrf failure carries.
  async #ensureCsrf(): Promise<CsrfPayload | null | Error> {
    const controller = new AbortController();
    const disarm = this.arm(controller);
    try {
      const ensured = await bounded(
        this.#csrf.ensure(controller.signal),
        controller.signal,
      );
      return ensured === null || ensured?.token !== undefined
        ? ensured
        : new Error("no CSRF token for the mutation");
    } catch (error) {
      if (controller.signal.aborted) return controller.signal.reason as Error;
      throw error;
    } finally {
      disarm();
    }
  }

  /** Abort the controller with a TimeoutError once the deadline passes, zero never. */
  arm(controller: AbortController): () => void {
    const ms = this.#timeout();
    const timer =
      ms > 0
        ? this.#clock.setTimeout(
            () => controller.abort(new DOMException("timed out", "TimeoutError")),
            ms,
          )
        : 0;
    return () => this.#clock.clearTimeout(timer);
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
    if (safe) return zoneKey(request.url, request.zone);
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
    zones: string[],
    target: string,
    method: string,
    csrf: CsrfPayload | undefined,
    entry: AbortController | undefined,
    isCurrent: (zone?: string) => boolean,
    page: string | undefined,
  ): Promise<Outcome> {
    if (!isCurrent()) return none("dropped");
    // Snapshot the dirty counter before the request is sent. A field touched after
    // this point is dirty relative to the response it will receive.
    const snapshot = this.#dirtySnapshot();
    this.#dispatch("partial:before-request", {
      url: request.url,
      method,
      intent: { zone: request.zone, uid: request.uid },
    });
    // One signal ends the request on its deadline or a supersede, armed after a tunnel.
    const deadline = new AbortController();
    let disarm = (): void => undefined;
    // A before-request listener may already have aborted the queue.
    if (entry?.signal.aborted === true) deadline.abort();
    entry?.signal.addEventListener("abort", () => deadline.abort(), { once: true });
    // Inside the try, since a caller's header name can be invalid and an abort or a
    // dropped connection can reject the body read after the headers arrived.
    let own: string | undefined;
    try {
      const headers = this.headers(request, method, csrf);
      // A true mutation carries a ring id so the SSE bridge suppresses its own echo.
      if (
        !SAFE_METHODS.has(method) &&
        request.abortable !== true &&
        !headers.has(HEADER_REQUEST_ID)
      ) {
        own = newId();
        headers.set(HEADER_REQUEST_ID, own);
        this.#rememberRequestId(own);
      }
      const init: RequestInit = { method, headers, mode: "same-origin" };
      if (request.body !== undefined) init.body = request.body;
      init.signal = deadline.signal;
      const tunneled =
        request.transport === "any" && method === "POST"
          ? await bounded(
              Promise.resolve(this.#tunnel(target, init, csrf?.header)),
              deadline.signal,
            )
          : undefined;
      if (typeof tunneled === "string" && request.abortable !== true) {
        if (own !== undefined) this.#forgetRequestId(own);
        const failed = this.#fail(zones, {
          kind: "undelivered",
          url: request.url,
          reason: tunneled,
        });
        if (tunneled === REFUSAL_HTTP_ONLY) this.#reload();
        return failed;
      }
      disarm = this.arm(deadline);
      const response =
        tunneled instanceof Response
          ? tunneled
          : await bounded((request.send ?? this.#fetch)(target, init), deadline.signal);
      // A safe-GET response that a newer request superseded is dropped unreported.
      if (!isCurrent()) return none("dropped");
      return await this.#classify(
        request,
        zones,
        target,
        method,
        response,
        snapshot,
        isCurrent,
        page,
        deadline.signal,
        () => {
          disarm();
          disarm = this.arm(deadline);
        },
      );
    } catch (error) {
      // A request past its deadline may have committed, so its echo must refresh too.
      const reason = deadline.signal.reason as Error | undefined;
      if (reason?.name === "TimeoutError") {
        if (own !== undefined) this.#forgetRequestId(own);
        return this.#fail(zones, { kind: "timeout", url: request.url, error: reason });
      }
      // An AbortError means the request was superseded, so nothing is reported.
      if (isAbortError(error) || entry?.signal.aborted === true) return none("dropped");
      if (own !== undefined) this.#forgetRequestId(own);
      return this.#fail(zones, { kind: "network", error });
    } finally {
      disarm();
    }
  }

  #fail(zones: string[], failure: PartialError): Outcome {
    const error = { ...failure, zones };
    this.#dispatch("partial:error", error);
    return { ...none("error"), error };
  }

  async #classify(
    request: WireRequest,
    zones: string[],
    target: string,
    method: string,
    response: Response,
    snapshot: number,
    isCurrent: (zone?: string) => boolean,
    page: string | undefined,
    signal: AbortSignal,
    open: () => void,
  ): Promise<Outcome> {
    const safe = SAFE_METHODS.has(method);
    const navigates = request.boundaries === undefined && (response.redirected || safe);
    // A 409 on a safe method means an asset version mismatch with an empty body. The
    // runtime then does a full visit of the current URL and nothing else.
    if (response.status === 409 && safe && navigates)
      return this.#fallbackNavigate(zones, response.url || target);
    const crashed = response.status >= 500;
    const contentType = response.headers.get("content-type") ?? "";
    const baseType = contentType.replace(/;.*$/, "").trim();
    const hook = this.#parseHooks.get(baseType);
    const read = baseType === EVENT_STREAM ? this.#reader() : undefined;
    // A mutation never navigates to its own endpoint, that would answer 405
    const foreign =
      !crashed &&
      hook === undefined &&
      ((baseType !== CONTENT_TYPE && read === undefined) || response.redirected);
    if (foreign && (response.redirected || (response.status < 400 && navigates)))
      return request.navigate === false
        ? { ...none("ok"), location: response.url || target }
        : this.#fallbackNavigate(zones, response.url || target);
    // A body the hook or JSON cannot parse, or an envelope apply rejects as malformed,
    // is a parse error rather than an unhandled rejection of a fire-and-forget fetch.
    const land: StreamTake["land"] = async (body, parse, inspect) => {
      try {
        const raw = parse();
        // Cleared once the body parses, so a later non-envelope reply on this page
        // navigates once more.
        this.#session.remove(NAVIGATED_FLAG);
        const { envelope, ok, stale, nodes } = await this.#onEnvelope(raw, {
          response,
          snapshot,
          key: request.key,
          page,
          owner: request.owner,
          zones,
          isCurrent,
          inspect,
        });
        if (stale === true) return none("dropped");
        nodes?.forEach(stamp);
        const invalid = envelope.form?.valid === false;
        return {
          status: invalid ? "invalid" : response.status < 400 ? "ok" : "error",
          applied: ok,
          envelope,
          errors: invalid ? envelope.form.errors : undefined,
        };
      } catch (error) {
        return this.#fail(zones, { kind: "parse", body, error });
      }
    };
    if (read !== undefined && !crashed) return read(response, { signal, open, land });
    const body = await response.text();
    if (!isCurrent()) return none("dropped");
    if (crashed || foreign) {
      const failed = this.#fail(zones, { kind: "http", status: response.status, body });
      if (foreign && navigates) this.#fallbackNavigate(zones, response.url || target);
      return failed;
    }
    return land(body, () =>
      hook !== undefined ? hook(response, body) : JSON.parse(body),
    );
  }

  // Guarded so a page that keeps replying with a non-envelope (login redirect, WAF
  // stub, maintenance) cannot loop navigation, since a `defer="load"` trigger requests
  // again on every page. The first navigates and sets the flag, and a second while it
  // is set fires a partial:error and leaves the page in place.
  #fallbackNavigate(zones: string[], url: string): Outcome {
    if (this.#session.get(NAVIGATED_FLAG) === "1") {
      this.#session.remove(NAVIGATED_FLAG);
      return this.#fail(zones, {
        kind: "network",
        error: new Error("zone answered non-envelope after a navigation"),
      });
    }
    this.#session.set(NAVIGATED_FLAG, "1");
    this.#navigate(url);
    return none("ok");
  }

  // Headers, not a plain record, so a caller writing a name in another case still
  // matches the runtime's header of that name and each header is set exactly once.
  headers(request: WireRequest, method: string, csrf?: CsrfPayload): Headers {
    const headers = new Headers({
      [REQUEST_FLAG]: "1",
      [HEADER_ACCEPT]: method === "GET" && this.#reader() ? STREAM_ACCEPT : ACCEPT,
      ...request.headers,
    });
    // The version comes from the init payload, so even the first request after a
    // deploy asserts the release the page was rendered by.
    const version = this.#version();
    if (version) headers.set(HEADER_VERSION, version);
    headers.set(HEADER_PROTOCOL, `${PROTOCOL}`);
    if (request.zone !== undefined) headers.set(HEADER_ZONE, request.zone);
    if (!SAFE_METHODS.has(method) && csrf?.token !== undefined)
      headers.set(csrf.header, csrf.token);
    return headers;
  }
}
