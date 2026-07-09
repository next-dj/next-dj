// The shared wire vocabulary: the content-type and Accept markers, the X-Next-*
// header names, and the data-next-* attributes more than one module addresses.
// These are the contract with the server and the only constants several runtime
// modules must agree on, so they live in one place rather than being duplicated
// or imported in two hops. A constant local to a single module stays in that
// module.

// A non-envelope content-type is navigation, never a patch, so the wire markers
// here are the one place several modules read the same content-type. Both must
// match the server exactly.
export const CONTENT_TYPE = "application/vnd.next.patches+json";
export const ACCEPT = "application/vnd.next.patches+json, text/html;q=0.9";

// The intent and negotiation headers. Every partial request flags itself, names
// the zone or batch it targets, asserts the asset version it knows, and stamps a
// ring id so the SSE bridge drops its own echo. X-Next-Origin carries the host
// page of a layer request, X-Next-Merge the append or prepend intent.
export const REQUEST_FLAG = "X-Next-Request";
export const HEADER_ACCEPT = "Accept";
export const HEADER_ZONE = "X-Next-Zone";
export const HEADER_MERGE = "X-Next-Merge";
export const HEADER_VERSION = "X-Next-Version";
export const HEADER_REQUEST_ID = "X-Next-Request-Id";
export const HEADER_ORIGIN = "X-Next-Origin";

// The data-next-poll bounds. Both must match _MIN_POLL_MS and _MAX_POLL_MS in
// next/partial/zone.py exactly. The ceiling is the browser's signed-32-bit
// setTimeout coercion, above which a timer fires immediately.
export const MIN_POLL_MS = 1000;
export const MAX_POLL_MS = 2147483647;

// The data-next-* attributes the runtime resolves across module boundaries: a
// zone container, a form keyed by its action uid, and a list-row dedup key. The
// applier, the layer stack, the triggers, and the morph engine must spell these
// the same way.
export const ATTR_ZONE = "data-next-zone";
export const ATTR_ACTION = "data-next-action";
export const ATTR_KEY = "data-next-key";

// The wire-body keys the envelope parser reads mirror next/partial/keys.py, the
// server-side source of truth, so a rename on either side is a wire break and the
// two move in lockstep. They stay inlined as string literals here rather than
// shared constants until the 0.9 sync step.

// A partial:error as a discriminated union on kind, so a listener branches on
// the cause and reads only the fields that cause carries. network is a fetch
// reject or a dropped SSE connection, with no status or body to report. http is
// a 5xx or a mutating reply that is not an envelope, carrying the status and
// body. parse is a malformed JSON body. op is a thrown or unknown verb mid-apply,
// naming the verb. asset is a stylesheet that failed to load or a version
// mismatch surviving a reload, optionally naming the url. It lives with the wire
// vocabulary so every emitting module stamps its payload against one shape.
export type PartialError =
  | { kind: "network"; error: unknown }
  | { kind: "http"; status: number; body: string }
  | { kind: "parse"; body: string; error: unknown }
  | { kind: "op"; op: string; error: unknown }
  | { kind: "asset"; url?: string; error: unknown };

// The discriminant of PartialError, kept as a named alias for listeners that
// switch on the kind before reading the cause-specific fields.
export type PartialErrorKind = PartialError["kind"];

// The boundary predicates the wire parsers share. Several modules narrow an
// unknown JSON value the same way, so the checks live here next to the wire
// vocabulary rather than being copied per module.
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

// CSS.escape is unavailable in jsdom, so a quoted attribute value is escaped by
// hand. Server-authored uids and zone names are ASCII slugs, this only guards
// the rare embedded quote or backslash.
export function cssEscape(value: string): string {
  return value.replace(/["\\]/g, "\\$&");
}

// The one canon for "the URL the page is on": pathname plus search. Refresh
// re-GETs, SSE resume revalidation, and the layer history seam all key off this,
// so a query filter survives a re-GET instead of collapsing to the bare path.
export function currentUrl(doc: Document): string {
  return doc.location.pathname + doc.location.search;
}

// querySelectorAll never matches its own root, but a replace patch scans the
// new wrapper element itself, so scans fold a matching root into the result.
export function matching(root: ParentNode, selector: string): Element[] {
  const found = Array.from(root.querySelectorAll(selector));
  if (root instanceof Element && root.matches(selector)) found.push(root);
  return found;
}
