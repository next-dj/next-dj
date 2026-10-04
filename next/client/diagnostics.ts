// The dev diagnostics. They report what the envelope boundary dropped, per-op timing,
// stripped scripts, nodes with both a key and an id, and ignored trigger attributes.
// They ship in next.dev.min.js, which the runtime fetches only for a $dev payload.

import { assetLoad, isPatch, isWellFormedAsset } from "./apply";
import { readCsrf } from "./csrf";
import {
  ATTR_KEY,
  ATTR_ZONE,
  MAX_POLL_MS,
  MIN_POLL_MS,
  asString,
  isRecord,
  matching,
  pollInterval,
} from "./protocol";
import type { Diagnostics } from "./protocol";
import { LAZY_ATTR, MERGE_ATTR, POLL_ATTR } from "./triggers";

// The accepted values of the checked attributes, so a typo is reported during
// development instead of being ignored. The merge values match the server.
const LAZY_VALUES = new Set(["load", "revealed"]);
const MERGE_VALUES = new Set(["append", "prepend"]);

// A non-list field is dropped whole, so the per-entry counters cannot report it.
// An absent field is the normal terse envelope and says nothing.
function reportNonArray(field: string, value: unknown): void {
  if (value !== undefined && !Array.isArray(value)) {
    console.warn(`[next] envelope ${field} is not an array, all ${field} dropped`);
  }
}

// Reports what the two boundary filters dropped. It walks the wire arrays a second
// time, so only the dev chunk does it.
function reportDropped(wire: Record<string, unknown>): void {
  reportNonArray("ops", wire.ops);
  const rawOps = Array.isArray(wire.ops) ? wire.ops : [];
  const malformedOps = rawOps.length - rawOps.filter(isPatch).length;
  if (malformedOps > 0) {
    console.warn(`[next] dropped malformed ops: ${malformedOps}`);
  }
  reportNonArray("assets", wire.assets);
  const rawAssets = Array.isArray(wire.assets) ? wire.assets : [];
  let malformedAssets = 0;
  let skipped = 0;
  const kinds = new Set<string>();
  for (const entry of rawAssets) {
    if (!isWellFormedAsset(entry)) {
      malformedAssets += 1;
    } else if (assetLoad(entry.kind, entry.load, entry.inline) === undefined) {
      skipped += 1;
      kinds.add(entry.kind);
    }
  }
  if (malformedAssets > 0) {
    console.warn(`[next] dropped malformed assets: ${malformedAssets}`);
  }
  // A debug line, not a warning, since a custom kind without an insertion verb is a
  // valid configuration. The kinds are named since nothing else reports such an asset.
  if (skipped > 0) {
    const named = Array.from(kinds).join(", ");
    console.debug(`[next] skipped assets of unsupported kind (${skipped}): ${named}`);
  }
}

// Errors are caught so a stubbed or full user timing buffer cannot fail the op.
function openMeasure(startMark: string): void {
  try {
    performance.mark(startMark);
  } catch {
    // A timing failure must not fail the op.
  }
}

// End the timing span of one op. A user timing error is caught here, so it cannot
// replace the op's result in the caller's finally block.
function closeMeasure(name: string, startMark: string): void {
  try {
    performance.measure(name, startMark);
    // The performance panel records the span when it is created, so the mark and
    // the measure are cleared to keep the buffer of a long-lived dev tab small.
    performance.clearMarks(startMark);
    performance.clearMeasures(name);
  } catch {
    // A timing failure must not fail the op.
  }
}

// Log the timing of one op. A page may replace console.debug with a stub that
// throws, so the error is caught and cannot replace the op's result.
function reportTiming(message: string): void {
  try {
    console.debug(message);
  } catch {
    // A timing failure must not fail the op.
  }
}

// The zone an op addresses, read as each verb resolves it. refresh prefers its
// top-level zone, and layer.open has only that field.
function zoneOf(patch: {
  op: string;
  target?: unknown;
  zone?: unknown;
}): string | undefined {
  const target = patch.target;
  const inTarget = isRecord(target) ? asString(target.zone) : undefined;
  const top = asString(patch.zone);
  if (patch.op === "refresh") return top ?? inTarget;
  if (patch.op === "layer.open") return top;
  return inTarget;
}

// An element matched by an attribute selector has it, so the null arm cannot occur.
function attrOf(el: Element, name: string): string {
  /* v8 ignore next */
  return el.getAttribute(name) ?? "";
}

function warnAttr(attr: string, value: string, allowed: Set<string>): void {
  const set = Array.from(allowed).join(", ");
  console.warn(
    `[next.partial] ${attr}="${value}" is not a recognised value and is ignored. Use one of: ${set}.`,
  );
}

// The interval has no fixed set of values, so the message states the bounds.
function warnPoll(value: string): void {
  console.warn(
    `[next.partial] ${POLL_ATTR}="${value}" is not a whole number of milliseconds between ${MIN_POLL_MS} and ${MAX_POLL_MS} and is ignored. The {% zone %} tag writes the resolved interval.`,
  );
}

function warnPollZone(value: string): void {
  console.warn(
    `[next.partial] ${POLL_ATTR}="${value}" sits on an element without ${ATTR_ZONE} and is ignored. Polling re-GETs the zone by name, so the container must carry both attributes.`,
  );
}

// Warn on hand-written values the runtime ignores.
function validateAttrs(root: ParentNode): void {
  for (const el of matching(root, `[${LAZY_ATTR}]`)) {
    const value = attrOf(el, LAZY_ATTR);
    if (!LAZY_VALUES.has(value)) warnAttr(LAZY_ATTR, value, LAZY_VALUES);
  }
  for (const el of matching(root, `[${MERGE_ATTR}]`)) {
    const value = attrOf(el, MERGE_ATTR);
    if (!MERGE_VALUES.has(value)) warnAttr(MERGE_ATTR, value, MERGE_VALUES);
  }
  for (const el of matching(root, `[${POLL_ATTR}]`)) {
    const value = attrOf(el, POLL_ATTR);
    if (pollInterval(value) === null) warnPoll(value);
    else if (el.getAttribute(ATTR_ZONE) === null) warnPollZone(value);
  }
}

// Warn on a $csrf payload in the seeded context that the runtime ignored.
function warnCsrf(context: Readonly<Record<string, unknown>>): void {
  // Otherwise the only symptom is a 403 on every programmatic mutation.
  if (context.$csrf !== undefined && readCsrf(context.$csrf) === undefined) {
    console.warn(
      "[next] ignored a malformed $csrf payload, unsafe requests send no header",
    );
  }
}

/**
 * Build the dev diagnostics the runtime reports through once this chunk loads.
 *
 * The seeded context is checked for a malformed $csrf payload on the first attrs call.
 * The runtime calls attrs only on the diagnostics it keeps, so a second copy of the
 * chunk does not repeat the warning.
 */
export function createDiagnostics(
  context: Readonly<Record<string, unknown>> = {},
): Diagnostics {
  // A counter appended to each mark name, so two ops with one label get two marks.
  let timings = 0;
  let checked = false;
  return {
    dropped: reportDropped,
    timed(patch, run) {
      const zone = zoneOf(patch);
      const label = zone ?? patch.op;
      // A distinct mark name means a nested apply cannot clear this mark.
      timings += 1;
      const startMark = `next:apply:${label}:start:${timings}`;
      openMeasure(startMark);
      const started = performance.now();
      try {
        return run();
      } finally {
        const ms = (performance.now() - started).toFixed(1);
        closeMeasure(`next:apply:${label}`, startMark);
        reportTiming(
          zone === undefined
            ? `[next] op "${patch.op}" in ${ms} ms`
            : `[next] zone "${zone}" ${patch.op} in ${ms} ms`,
        );
      }
    },
    stripped(address) {
      console.warn(
        `[next.partial] removed a <script> from a patch targeting ${
          address ?? "no target"
        }. Behaviour ships through co-located assets and the event op.`,
      );
    },
    keyed(el) {
      console.warn(`[next.morph] ${ATTR_KEY} and id on one node`, el);
    },
    attrs(root) {
      if (!checked) warnCsrf(context);
      checked = true;
      validateAttrs(root);
    },
  };
}
