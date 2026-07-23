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
 * next/partial/zone.py. The ceiling is the signed-32-bit setTimeout coercion,
 * above which a timer fires immediately. */
export const MIN_POLL_MS = 1000;
export const MAX_POLL_MS = 2147483647;

/** The data-next-* attributes the runtime resolves across module boundaries. */
export const ATTR_ZONE = "data-next-zone";
export const ATTR_ACTION = "data-next-action";
export const ATTR_KEY = "data-next-key";

/** A partial:error, a discriminated union so a listener branches on kind. */
export type PartialError =
  | { kind: "network"; error: unknown }
  | { kind: "http"; status: number; body: string }
  | { kind: "parse"; body: string; error: unknown }
  | { kind: "op"; op: string; target?: string; error: unknown }
  | { kind: "asset"; url?: string; error: unknown };

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

/** Match a selector across a subtree, folding in the root when it matches too. */
export function matching(root: ParentNode, selector: string): Element[] {
  const found = Array.from(root.querySelectorAll(selector));
  if (root instanceof Element && root.matches(selector)) found.push(root);
  return found;
}
