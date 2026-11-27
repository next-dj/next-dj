// The shared wire vocabulary, the contract with the server that several runtime
// modules must agree on. Module-local constants stay in their module.

// A statement labelled TEST is a harness seam. The shipped build drops every such
// statement through esbuild's dropLabels, the unit build runs it as plain code.
declare global {
  /** The id every bundle of one build shares, a digest of the client sources. */
  const __BUILD__: string;
}

import { ATTR_ERROR, ATTR_STATE, ATTR_STREAMED, HEADER_ROUTER } from "./protocol.gen";

export * from "./protocol.gen";

/** The negotiation header a partial request stamps beside the generated ones. */
export const HEADER_ACCEPT = "Accept";

// The last 25 own request ids, matching the server-side echo window. Overflow is
// safe, since a dropped id only causes an extra refresh.
export const ECHO_LIMIT = 25;

/** The data-next-poll bounds, matching _MIN_POLL_MS and _MAX_POLL_MS in
 * next/partial/zone.py. The signed-32-bit ceiling, above it a timer fires at once. */
export const MIN_POLL_MS = 1000;
export const MAX_POLL_MS = 2147483647;

// Parse a poll interval attribute. Only an all-digit value within the server bounds
// is accepted, so "5s" is rejected rather than read as 5.
export function pollInterval(raw: string | null): number | null {
  if (raw === null || !/^\d+$/.test(raw)) return null;
  const ms = Number(raw);
  return ms >= MIN_POLL_MS && ms <= MAX_POLL_MS ? ms : null;
}

/** The request deadline when the payload names none, long enough for a slow upload. */
export const REQUEST_TIMEOUT_MS = 30000;
/** The longest request deadline, the signed-32-bit timer ceiling. */
export const MAX_TIMEOUT_MS = 2147483647;

// Read the $timeout value in ms, zero turning it off and out of range the default.
export function requestTimeout(raw: unknown): number {
  return typeof raw === "number" && raw >= 0 && raw <= MAX_TIMEOUT_MS
    ? raw
    : REQUEST_TIMEOUT_MS;
}

// Settle with work or reject on abort, so even a transport ignoring the signal ends.
export function bounded<T>(work: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const stop = (): void =>
      reject(
        signal.reason instanceof Error
          ? signal.reason
          : new DOMException("aborted", "AbortError"),
      );
    if (signal.aborted) stop();
    signal.addEventListener("abort", stop, { once: true });
    work.then(resolve, reject).finally(() => signal.removeEventListener("abort", stop));
  });
}

/** The dev diagnostics, provided by next.dev.min.js once it loads for a $dev page. */
export interface Diagnostics {
  /** Report what the envelope boundary dropped from a raw wire envelope. */
  dropped(wire: Record<string, unknown>): void;
  /** Time one op under a user timing span and return the op's result. */
  timed(
    patch: { op: string; target?: unknown; zone?: unknown },
    run: () => boolean,
  ): boolean;
  /** Warn that a script was stripped from a patch aimed at the described address. */
  stripped(address: string | undefined): void;
  /** Warn that one node carries both data-next-key and id. */
  keyed: (el: Element) => void;
  /** Warn on hand-written trigger attributes and seeded values the runtime ignores. */
  attrs(root: ParentNode): void;
  /** Warn that an element fetch dropped, as the page loads no bundle that serves it. */
  unserved(el: Element): void;
  /** Show why a DEBUG server refused the token of a boundary, on it and in the console. */
  rejected(el: Element, detail: string): void;
}

/** The prefix every attribute of the protocol shares. */
export const ATTR_PREFIX = "data-next-";
/** The data-next-* attributes only the runtime reads, the shared ones generated. */
export const ATTR_STREAM = "data-next-stream";
export const ATTR_BUSY = "data-next-busy";
export const ATTR_ACCEPTED = "data-next-accepted";
export const ATTR_BATCH = "data-next-batch";
export const ATTR_RUN = "data-next-run";
export const ATTR_DIALOG = "data-next-dialog";
export const ATTR_KEEP = "data-next-keep";
export const ATTR_MORPH_EVENTS = "data-next-morph-events";
export const ATTR_PENDING = "data-next-pending";
export const ATTR_NAVIGATING = "data-next-navigating";
export const ATTR_TOAST = "data-next-toast";
export const ATTR_TRANSITION = "data-next-transition";
/** A boundary still waiting for its content, a literal so a chunk drops ATTR_STATE. */
export const LOADING = "[data-next-state=loading]";

// Kept apart from the version guard's flag in assets.ts, so neither clears the other.
export const NAVIGATED_FLAG = "next:partial:navigated";

let tick = 0;
const stamps = new WeakMap<Element, number>();
export const stamp = (el: Element): void => void stamps.set(el, ++tick);
export const stamped = (el: Element | null): number =>
  el ? Math.max(stamps.get(el) ?? 0, stamped(el.parentElement)) : 0;
export const now = (): number => tick;

export const streaming = (doc: Document): boolean =>
  doc.querySelector(`[${ATTR_STREAMED}]`) !== null;

export function duration(raw: string | null | undefined): number | undefined {
  const match = /^(\d+)(m?s)?$/.exec(raw ?? "");
  return match ? Number(match[1]) * (match[2] === "s" ? 1000 : 1) : undefined;
}

type Hold = [count: number, state: string | null, error: string | null];

const holds = new WeakMap<Element, Hold>();

function put(el: Element, name: string, value: string | null): void {
  if (value === null) el.removeAttribute(name);
  else el.setAttribute(name, value);
}

export const isBusy = (el: Element): boolean => holds.has(el);

export function markBusy(
  elements: readonly (Element | null)[],
  quiet?: boolean,
): (fault?: string | null) => void {
  const marked = elements.flatMap((el): [Element, Hold][] => {
    if (el === null) return [];
    let held = holds.get(el);
    if (held === undefined) {
      held = [0, el.getAttribute(ATTR_STATE), el.getAttribute(ATTR_ERROR)];
      holds.set(el, held);
      el.removeAttribute(ATTR_ERROR);
      if (held[1] !== null && held[1] !== "ready")
        el.setAttribute(ATTR_STATE, "loading");
    }
    held[0] += 1;
    el.setAttribute(ATTR_BUSY, "");
    if (!quiet) el.setAttribute("aria-busy", "true");
    return [[el, held]];
  });
  return (fault) => {
    for (const [el, held] of marked.splice(0)) {
      if ((held[0] -= 1) > 0) continue;
      holds.delete(el);
      el.removeAttribute(ATTR_BUSY);
      el.removeAttribute("aria-busy");
      if (fault === undefined) {
        put(el, ATTR_STATE, held[1]);
        put(el, ATTR_ERROR, held[2]);
      } else {
        const kind = el.getAttribute(ATTR_ERROR) ?? fault;
        put(el, ATTR_STATE, kind === null ? "ready" : "error");
        put(el, ATTR_ERROR, kind);
      }
    }
  };
}

/** The transport a request may ride, `any` letting a POST go over the open socket. */
export type WireTransport = "http" | "any";

/** A partial:error, a discriminated union so a listener branches on kind. */
export type PartialError = (
  | { kind: "network"; url?: string; error: unknown }
  | { kind: "timeout"; url: string; error: unknown }
  | { kind: "http"; status: number; body: string }
  | { kind: "parse"; body: string; error: unknown }
  | { kind: "op"; op: string; target?: string; error: unknown }
  | { kind: "asset"; url?: string; error: unknown }
  | { kind: "csrf"; url?: string; error: unknown }
  | { kind: "undelivered"; url: string; reason: string }
  | { kind: "incomplete"; boundaries: string[] }
) & { zones?: string[] };

/** The discriminant of PartialError, aliased for listeners that switch on it. */
export type PartialErrorKind = PartialError["kind"];

/** The dev-diagnostics flag, a fixed value or a read of state that flips after
 * construction. The inline bootstrap opens the channel post-build, so a rebuild
 * would drop registries and re-read the CSP nonce off the wrong script. */
export type DevFlag = boolean | (() => boolean);

/** Normalise a DevFlag to a getter, whatever shape it arrived in. */
export function devReader(flag: DevFlag | undefined): () => boolean {
  if (typeof flag === "function") return flag;
  const fixed = flag ?? false;
  return () => fixed;
}

/** Narrow an unknown JSON value to a plain object. */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Narrow an unknown JSON value to a string, or undefined. */
export function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

/** Escape a quoted attribute value by hand, since jsdom lacks CSS.escape. */
export function cssEscape(value: string): string {
  return value.replace(/["\\]/g, "\\$&");
}

/** The URL the page is on, pathname plus search, so a query survives a re-GET. */
export function currentUrl(doc: Document): string {
  return doc.location.pathname + doc.location.search;
}

/** Resolve `url` on the page origin, a leading `//` read as a path, else undefined. */
export function sameOrigin(url: string, doc: Document): string | undefined {
  const origin = doc.location.origin;
  let target: URL;
  try {
    target = url.startsWith("/") ? new URL(origin + url) : new URL(url, doc.baseURI);
  } catch {
    return undefined;
  }
  return target.origin === origin ? target.href : undefined;
}

/** The page identity of a URL, its path and search on this origin, no fragment. */
export function pageKey(url: string, doc: Document): string {
  const href = sameOrigin(url, doc);
  if (href === undefined) return url;
  const target = new URL(href);
  return target.pathname + target.search;
}

/** Fire a runtime event on the document and on the Next.on bus alike. */
export function fire(
  doc: Document,
  dispatch: (event: string, detail: Record<string, unknown>) => void,
  event: string,
  detail: Record<string, unknown>,
  cancelable = false,
): boolean {
  const passed = doc.dispatchEvent(new CustomEvent(event, { detail, cancelable }));
  dispatch(event, detail);
  return passed;
}

/**
 * The CSP nonce of the runtime script, read at module evaluation.
 *
 * currentScript is null after evaluation and in a module script, so the nonce of the
 * first script that has one is used instead. A policy issues one nonce per response.
 */
export function scriptNonce(doc: Document): string | undefined {
  const current = doc.currentScript ?? doc.querySelector("script[nonce]");
  const value = current instanceof HTMLElement ? current.nonce : "";
  return value === "" ? undefined : value;
}

/** A short random key, unique enough to tell entries and marks of one page apart. */
export const uid = (): string => Math.random().toString(36).slice(2);

// A new request id. crypto.randomUUID is missing on a plain-HTTP origin although the
// lib type declares it, so a timestamp id is the fallback.
export function newId(): string {
  const impl = globalThis.crypto as { randomUUID?: () => string } | undefined;
  return impl?.randomUUID ? impl.randomUUID() : `${Date.now()}-${uid()}`;
}

/** Match a selector across a subtree, folding in the root when it matches too. */
export function matching(root: ParentNode, selector: string): Element[] {
  const found = Array.from(root.querySelectorAll(selector));
  if (root instanceof Element && root.matches(selector)) found.push(root);
  return found;
}

// Add a zone to the per-page batch, one comma-joined GET per owning page.
export function addZone(
  batches: Map<string, string[]>,
  url: string,
  zone: string,
): void {
  const zones = batches.get(url);
  if (zones === undefined) batches.set(url, [zone]);
  else zones.push(zone);
}

/** A URL without its fragment, the page identity a navigation and a prefetch share. */
export const bare = (href: string): string => href.replace(/#.*/s, "");

/** The media type of a response, its content-type without parameters. */
export const mime = (response: Response): string =>
  (response.headers.get("content-type") ?? "").replace(/;.*/, "").trim();

/** Whether the router header of a response lists the given directive. */
export const says = (response: Response | undefined, word: string): boolean =>
  (response?.headers.get(HEADER_ROUTER) ?? "")
    .split(",")
    .some((token) => token.trim() === word);

export function blank(
  data: Record<string, unknown>,
  context: Readonly<Record<string, unknown>>,
): Record<string, unknown> {
  for (const name of Object.keys(context))
    if (!name.startsWith("$") && !(name in data)) data[name] = undefined;
  return data;
}
