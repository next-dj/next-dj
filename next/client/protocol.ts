// The shared wire vocabulary, the contract with the server that several runtime
// modules must agree on. Module-local constants stay in their module.

/** The envelope content-type. Must match the server exactly. */
export const CONTENT_TYPE = "application/vnd.next.patches+json";
export const ACCEPT = "application/vnd.next.patches+json, text/html;q=0.9";

/** The intent and negotiation headers a partial request stamps. */
export const REQUEST_FLAG = "X-Next-Request";
export const HEADER_ACCEPT = "Accept";
export const HEADER_ZONE = "X-Next-Zone";
export const HEADER_MERGE = "X-Next-Merge";
export const HEADER_VERSION = "X-Next-Version";
export const HEADER_REQUEST_ID = "X-Next-Request-Id";
export const HEADER_ORIGIN = "X-Next-Origin";

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
  /** Warn on the hand-written trigger attributes the runtime ignores. */
  attrs(root: ParentNode): void;
}

/** The data-next-* attributes the runtime resolves across module boundaries. */
export const ATTR_ZONE = "data-next-zone";
export const ATTR_ACTION = "data-next-action";
export const ATTR_KEY = "data-next-key";
export const ATTR_SSE = "data-next-sse";
export const ATTR_POLL = "data-next-poll";

/** A partial:error, a discriminated union so a listener branches on kind. */
export type PartialError =
  | { kind: "network"; url?: string; error: unknown }
  | { kind: "http"; status: number; body: string }
  | { kind: "parse"; body: string; error: unknown }
  | { kind: "op"; op: string; target?: string; error: unknown }
  | { kind: "asset"; url?: string; error: unknown }
  | { kind: "csrf"; url?: string; error: unknown };

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
): void {
  doc.dispatchEvent(new CustomEvent(event, { detail }));
  dispatch(event, detail);
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

// A new request id. crypto.randomUUID is missing on a plain-HTTP origin although the
// lib type declares it, so a timestamp id is the fallback.
export function newId(): string {
  const impl = globalThis.crypto as { randomUUID?: () => string } | undefined;
  return impl?.randomUUID
    ? impl.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
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
