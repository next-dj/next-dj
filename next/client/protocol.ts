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

// The data-next-* attributes the runtime resolves across module boundaries: a
// zone container, a form keyed by its action uid, and a list-row dedup key. The
// applier, the layer stack, the triggers, and the morph engine must spell these
// the same way.
export const ATTR_ZONE = "data-next-zone";
export const ATTR_ACTION = "data-next-action";
export const ATTR_KEY = "data-next-key";

// The wire-body keys the envelope parser reads (version, ops, assets, op, target,
// html, kind, url, and the rest) mirror next/partial/keys.py, the server-side
// source of truth. They are inlined as string literals at their read sites rather
// than centralised here because moving them into shared constants is the 0.9 sync
// step, not this freeze. A rename on either side is a wire break and the two must
// move in lockstep.

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
