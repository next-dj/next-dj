// The dev channel: what the envelope boundary dropped, per-op timing, stripped
// scripts, doubly keyed nodes and ignored trigger attributes. It ships in
// next.dev.min.js, which the runtime fetches only for a page rendered under $dev.

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

// The closed value sets the dev warning guards, so a typo is caught at authoring
// time rather than dropped in silence. Merge mirrors the server's vocabulary.
const LAZY_VALUES = new Set(["load", "revealed"]);
const MERGE_VALUES = new Set(["append", "prepend"]);

// A non-list field is dropped whole, so the per-entry counters cannot report it.
// An absent field is the normal terse envelope and says nothing.
function reportNonArray(field: string, value: unknown): void {
  if (value !== undefined && !Array.isArray(value)) {
    console.warn(`[next] envelope ${field} is not an array, all ${field} dropped`);
  }
}

// The breakdown of what the two boundary filters dropped, walking the wire arrays a
// second time, which is why production never does it.
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
  // A debug line, not a warn, since a custom kind with no insertion verb is a
  // normal configuration. The kinds are named as the only signal such an asset leaves.
  if (skipped > 0) {
    const named = Array.from(kinds).join(", ");
    console.debug(`[next] skipped assets of unsupported kind (${skipped}): ${named}`);
  }
}

// Contained so a stubbed or exhausted user timing cannot fail the op it measures.
function openMeasure(startMark: string): void {
  try {
    performance.mark(startMark);
  } catch {
    // A measurement never decides the fate of what it measures.
  }
}

// Close the diagnostic span of one op. A user-timing failure stays inside here,
// so the finally of the timing cannot displace the op's outcome.
function closeMeasure(name: string, startMark: string): void {
  try {
    performance.measure(name, startMark);
    // A dev tab lives for hours and the panel already recorded the span as it
    // was created, so neither the mark nor the measure stays in the buffer.
    performance.clearMarks(startMark);
    performance.clearMeasures(name);
  } catch {
    // A measurement never decides the fate of what it measured.
  }
}

// The timing line of one op. A page may replace console.debug with a throwing
// stub, so the failure stays inside and cannot displace the op's outcome.
function reportTiming(message: string): void {
  try {
    console.debug(message);
  } catch {
    // A measurement never decides the fate of what it measured.
  }
}

// The zone an op addresses, read the way each verb resolves its own zone, so
// refresh prefers its top-level zone and layer.open carries only that field.
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

// The interval has no closed set to list, so the message spells the bounds.
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

// Warn on hand-written values the runtime drops in silence.
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

/** Warn on a $csrf payload the runtime ignored, read off the seeded context. */
export function warnCsrf(context: Readonly<Record<string, unknown>>): void {
  // Otherwise the only symptom is a 403 on every programmatic mutation.
  if (context.$csrf !== undefined && readCsrf(context.$csrf) === undefined) {
    console.warn(
      "[next] ignored a malformed $csrf payload, unsafe requests send no header",
    );
  }
}

/** Build the dev channel the runtime reports through once this chunk lands. */
export function createDiagnostics(): Diagnostics {
  // Serial of the timing marks, so two ops sharing a label hold two marks.
  let timings = 0;
  return {
    dropped: reportDropped,
    timed(patch, run) {
      const zone = zoneOf(patch);
      const label = zone ?? patch.op;
      // The serial keeps each mark distinct, so a nested apply cannot clear it.
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
    attrs: validateAttrs,
  };
}
