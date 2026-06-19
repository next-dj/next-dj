// The shared wire vocabulary: the content-type and Accept markers, the X-Next-*
// header names, and the data-next-* attributes more than one module addresses.
// These are the contract with the server and the only constants several runtime
// modules must agree on, so they live in one place rather than being duplicated
// or imported in two hops. A constant local to a single module stays in that
// module.

// The wire content-type marker (invariant 9) and the Accept that doubles the
// partial switch on content negotiation. Both must match the server exactly.
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
