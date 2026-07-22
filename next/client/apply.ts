// Envelope parsing, the built-in verbs, the custom-op registry, and the
// structural neutralisation of script elements before insertion. The applier
// stays a thin executor: the server authors every address and verb.

import { fireRemoved, morph } from "./morph";
import {
  ATTR_ACTION,
  ATTR_KEY,
  ATTR_ZONE,
  HEADER_ZONE,
  asString,
  cssEscape,
  currentUrl,
  devReader,
  isRecord,
} from "./protocol";
import type { DevFlag, PartialError } from "./protocol";
import type { Navigate } from "./wire";

export interface Target {
  zone?: string;
  form?: string;
  field?: [string, string];
  css?: string;
}

// The built-in verbs as a discriminated union keyed by op, so a handler that has
// narrowed on op reads its own fields without re-deriving them from unknown. Each
// variant lists only the fields the server authors for that verb, so an extra
// property is a type error rather than a silent passthrough.
export interface MorphPatch {
  op: "morph";
  target?: Target;
  html?: string;
  extract?: boolean;
}

export interface ReplacePatch {
  op: "replace";
  target?: Target;
  html?: string;
}

export interface InnerPatch {
  op: "inner";
  target?: Target;
  html?: string;
}

export interface MergePatch {
  op: "append" | "prepend";
  target?: Target;
  html?: string;
}

export interface RemovePatch {
  op: "remove";
  target?: Target;
}

export interface RefreshPatch {
  op: "refresh";
  target?: Target;
  zone?: string;
}

export interface EventPatch {
  op: "event";
  name?: string;
  detail?: unknown;
}

export interface LayerOpenPatch {
  op: "layer.open";
  zone?: string;
  href?: string;
}

export interface LayerClosePatch {
  op: "layer.close";
  result?: unknown;
  dismiss?: boolean;
  reason?: string;
}

export interface ToastPatch {
  op: "toast";
  text?: string;
  variant?: string;
}

export interface UrlPatch {
  op: "url";
  href?: string;
  action?: string;
}

export interface VisitPatch {
  op: "visit";
  href?: string;
  external?: boolean;
}

export interface ContextPatch {
  op: "context";
  data?: unknown;
}

export type BuiltinPatch =
  | MorphPatch
  | ReplacePatch
  | InnerPatch
  | MergePatch
  | RemovePatch
  | RefreshPatch
  | EventPatch
  | LayerOpenPatch
  | LayerClosePatch
  | ToastPatch
  | UrlPatch
  | VisitPatch
  | ContextPatch;

// A custom op registered through defineOp. Its op is any non-built-in string and
// its payload is open, so a plugin reads its own fields off the index signature.
export interface CustomPatch {
  op: string;
  [extra: string]: unknown;
}

export type Patch = BuiltinPatch | CustomPatch;

// The set of built-in op names, kept in sync with the BuiltinPatch union by the
// isBuiltin guard below. #applyOp tests isBuiltin first, so a custom op
// registered under a built-in name never reaches the registry, the built-in
// switch claims the name.
const BUILTIN_OPS = new Set<string>([
  "morph",
  "replace",
  "inner",
  "append",
  "prepend",
  "remove",
  "refresh",
  "event",
  "layer.open",
  "layer.close",
  "toast",
  "url",
  "visit",
  "context",
] satisfies BuiltinPatch["op"][]);

// Narrow a patch to a built-in verb by its op. A type predicate rather than an
// op-only check so the switch in #applyBuiltin sees a BuiltinPatch, with no
// CustomPatch in the union to defeat the per-op narrowing.
function isBuiltin(patch: Patch): patch is BuiltinPatch {
  return BUILTIN_OPS.has(patch.op);
}

// A wire op is a record naming its verb, the one field every patch shares, so
// an op-less record is dropped at the boundary like any other malformed op.
function isPatch(value: unknown): value is Patch {
  return isRecord(value) && typeof value.op === "string";
}

// How an asset is inserted, the one thing the loader has to know. kind stays a
// project vocabulary the server owns, the verb is what the client can act on.
export type AssetLoad = "link" | "script" | "module";

export interface Asset {
  kind: string;
  url: string;
  // The insertion verb the server derived from the renderer registered for the
  // kind. Absent when that renderer is a custom one the client has no verb for.
  load?: AssetLoad;
  // The body of a co-located inline asset, absent on a URL-form asset. The
  // server collects a zone's inline styles and scripts that have no URL, so the
  // loader inserts the body itself rather than a <link>/<script src>.
  inline?: string;
}

// The closed set of insertion verbs. The Asset type promises subscribers one of
// these three, so a wire load spelling anything else must not ride into assets.
function isAssetLoad(value: unknown): value is AssetLoad {
  return value === "link" || value === "script" || value === "module";
}

// The insertion verb of a wire asset. A verb the server spelled out wins, and a
// url-form entry of a built-in kind keeps its implied verb so an envelope from a
// server that predates the field still loads. An inline body takes no such
// fallback: the server only spells the verb when the kind's inline wrapper is the
// element this runtime would build, and the module kind has no such wrapper, so
// guessing from the name would execute a body a full page render prints
// verbatim.
export function assetLoad(
  kind: unknown,
  load: unknown,
  inline: unknown,
): AssetLoad | undefined {
  if (isAssetLoad(load)) return load;
  if (typeof inline === "string") return undefined;
  if (kind === "css") return "link";
  if (kind === "js") return "script";
  if (kind === "module") return "module";
  return undefined;
}

// Narrow an unknown wire entry to an Asset. The manifest crosses the wire
// boundary like the ops do, so a malformed entry is dropped here rather than
// cast blind into envelope.assets and the event details. The kind is checked
// against the type it is declared as, so this boundary and the dev breakdown
// call the same entries broken. An entry whose insertion verb does not resolve
// is dropped too, since a partial:applied listener would otherwise read a load
// the type promises is a verb.
export function isAsset(value: unknown): value is Asset {
  return (
    isWellFormedAsset(value) &&
    assetLoad(value.kind, value.load, value.inline) !== undefined
  );
}

export interface FormMeta {
  uid: string;
  valid: boolean;
  errors: Record<string, string[]>;
}

// The parsed wire envelope is read-only past the boundary: the applier and the
// partial:before-apply listeners observe it, none of them rewrite it.
export interface Envelope {
  readonly version: string;
  readonly ops: readonly Patch[];
  readonly assets: readonly Asset[];
  readonly form: FormMeta | null;
  csrf?: { header: string; token: string };
  request_id?: string;
}

// A custom-op handler receives the open patch shape, so a plugin reads its own
// server-authored fields off the index signature. Built-in verbs and custom ops
// share one apply path and one ApplyContext, the core eating its own dog food,
// but the built-ins carry static variants and so dispatch through a typed switch
// rather than the registry that erases their shape.
export type OpHandler = (patch: CustomPatch, ctx: ApplyContext) => void;

export interface ApplyContext {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  mergeContext: (data: Record<string, unknown>) => void;
  root: Document;
  dev: boolean;
}

// The layer-aware bits the applier needs from the layer stack: a zone resolve
// (top-down, or scoped to the page a zone GET fetched), the unscoped selector
// resolve for form targets, the owning-page URL of an element, the open and
// close verbs, and the toast container. The LayerStack satisfies this
// structurally, so partial.ts passes it directly. A server-initiated open
// carries no opener element and may seed a zone, an href with a zone, or
// neither.
export interface LayerBridge {
  resolveZone(name: string, root: ParentNode, page?: string): Element | null;
  resolveSelector(selector: string, root: ParentNode): Element | null;
  urlFor(el: Element): string;
  open(opener: null, href?: string, zone?: string): unknown;
  close(detail: { result?: unknown; dismiss?: boolean; reason?: string }): void;
  toast(text: string, variant: string): void;
}

// The history seam for the `url` verb. The server validates the href, so the
// runtime only pushes or replaces. Injectable because jsdom's history is shared
// global state the harness inspects.
export interface HistoryAdapter {
  push(href: string): void;
  replace(href: string): void;
}

// The asset and version bridge the applier consults around the ops. The applier
// gates ops behind the CSS delta and runs the JS delta after, but owns neither
// the loader nor the registry: those live in assets.ts. Absent, the ops run
// inline with no asset handling, the path the verb-only tests exercise.
export interface AssetBridge {
  loadCss(manifest: readonly Asset[], done: () => void): void;
  loadJs(manifest: readonly Asset[]): void;
  versionMismatch(envelopeVersion: string, url: string): boolean;
  acceptVersion(envelopeVersion: string): void;
}

// The re-executable mount registry: a callback runs over the document on `ready`
// and over every inserted subtree after each apply, the one-to-one replacement
// for DOMContentLoaded. triggers.ts also rides this hook to bind delegated
// handlers on newly inserted zones.
export type MountCallback = (root: ParentNode) => void;

export interface MountRegistry {
  // Run the registered callbacks over a freshly inserted subtree.
  run(root: ParentNode): void;
}

// The fetch bridge the `refresh` verb uses to re-GET a zone with its own
// cookies. Absent, it is a no-op. The same shape the layer stack already
// passes, so partial.ts wires one binding.
export type ZoneFetch = (request: {
  url: string;
  zone: string;
  headers?: Record<string, string>;
}) => void;

// The mutable state of a single apply, captured once per envelope and threaded
// through the ops rather than stored on the applier. When an envelope ships new
// CSS its ops defer behind loadCss, so a second apply (an SSE event takes no
// lock) can start before the first resumes. A per-apply struct keeps each
// envelope's dirty predicate, request key, and touched set bound to its own run
// instead of letting the later apply clobber the earlier one's instance fields.
interface ApplyState {
  isDirty: (field: Element) => boolean;
  requestKey: string | undefined;
  // The page a safe zone GET fetched, scoping its zone patches to that page.
  page: string | undefined;
  touched: Element[];
}

export interface ApplyDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  mergeContext: (data: Record<string, unknown>) => void;
  document?: Document;
  // Dev builds warn on each neutralised script. The flag is injectable so
  // tests assert both the warn-on and the silent-off behaviour, and a getter
  // form lets the owner flip it without rebuilding the applier and losing the
  // custom ops registered against it.
  dev?: DevFlag;
  // Build the morph dirty predicate from the request snapshot wire.ts threads
  // in. Absent, no field is treated as dirty and the server value always wins.
  dirtySince?: (snapshot: number) => (field: Element) => boolean;
  // Whether an element was ever touched, blind to any snapshot. It carries the
  // <details> open state past a patch the user never asked for. Absent, no
  // element reads as touched and the server open state wins.
  isTouched?: (el: Element) => boolean;
  // The layer stack, consulted for zone targets (top layer down) and the home
  // of layer.close and toast. Absent, zone resolve falls back to the document.
  layers?: LayerBridge;
  // The history seam for the url verb. Absent, the verb is a no-op.
  history?: HistoryAdapter;
  // The navigation seam for the visit verb, a hard navigation to a
  // server-validated redirect. Absent, the verb is a no-op.
  navigate?: Navigate;
  // The asset loader and version safeguard. Absent, ops run with no CSS gate,
  // no JS delta, and no version check.
  assets?: AssetBridge;
  // The re-executable mount registry, run over every inserted subtree. Absent,
  // only next:mounted fires.
  mount?: MountRegistry;
  // The zone re-GET used by the refresh verb. Absent, it is a no-op.
  refresh?: ZoneFetch;
  // The current URL the version safeguard reloads on a mismatch. Absent, the
  // document's own location is used.
  here?: () => string;
}

// The raw shape of a wire envelope after the only structural check JSON.parse
// leaves to do. Every field is still unknown: parseEnvelope narrows each one,
// so the typed Envelope is built from honest checks rather than a blind cast
// over the wire boundary.
type RawEnvelope = Record<string, unknown>;

// Narrow a form-errors record to the field-to-messages shape, keeping only
// string-array values so a malformed errors map cannot smuggle a non-array past
// the boundary.
function parseFormErrors(value: unknown): Record<string, string[]> {
  if (!isRecord(value)) return {};
  const errors: Record<string, string[]> = {};
  for (const [field, messages] of Object.entries(value)) {
    if (Array.isArray(messages) && messages.every((m) => typeof m === "string")) {
      errors[field] = messages;
    }
  }
  return errors;
}

// Build the typed form meta from its unknown wire value, reading each field
// through a check rather than pretending the record already has the shape. An
// absent or non-record form collapses to null.
function parseFormMeta(value: unknown): FormMeta | null {
  if (!isRecord(value)) return null;
  const uid = asString(value.uid) ?? "";
  const valid = value.valid === true;
  return { uid, valid, errors: parseFormErrors(value.errors) };
}

// A wire asset is well-formed when it names a kind, carries a url or an inline
// body, and spells no load outside the three verbs. Blind to which kind it is,
// since the server registers custom kinds and a shape check is the only honest
// measure of "broken" at this boundary.
function isWellFormedAsset(
  value: unknown,
): value is { kind: string; load: AssetLoad | undefined; inline?: unknown } {
  return (
    isRecord(value) &&
    typeof value.kind === "string" &&
    (value.load === undefined || isAssetLoad(value.load)) &&
    (typeof value.url === "string" || typeof value.inline === "string")
  );
}

// parseEnvelope drops a non-list field whole, and the per-entry counters would
// then report zero malformed, which reads as nothing wrong. An absent field is
// the normal terse envelope and says nothing.
function reportNonArray(field: string, value: unknown): void {
  if (value !== undefined && !Array.isArray(value)) {
    console.warn(`[next] envelope ${field} is not an array, all ${field} dropped`);
  }
}

// The dev-only breakdown of what the two boundary filters dropped. Called from
// behind the dev flag, so production never walks the wire arrays a second time.
function reportDropped(wire: RawEnvelope): void {
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
  // A debug line rather than a warn, since a custom kind whose renderer implies
  // no insertion verb is a normal configuration and the asset is intact. The
  // kinds are named because that is the only signal such an asset leaves.
  if (skipped > 0) {
    const named = Array.from(kinds).join(", ");
    console.debug(`[next] skipped assets of unsupported kind (${skipped}): ${named}`);
  }
}

// Narrow an unknown JSON value into an Envelope. Missing meta collapses to its
// empty value so a terse server envelope parses without optional-field noise.
export function parseEnvelope(raw: unknown, dev = false): Envelope {
  if (!isRecord(raw)) {
    throw new TypeError("partial envelope is not an object");
  }
  const wire: RawEnvelope = raw;
  const version = asString(wire.version);
  if (version === undefined) {
    throw new TypeError("partial envelope is missing version");
  }
  // Keep only ops naming a verb, so a non-object element (ops: [null]) or an
  // op-less record is a dropped op rather than a poison that throws mid-apply
  // over a half-mutated DOM.
  const ops = Array.isArray(wire.ops) ? wire.ops.filter(isPatch) : [];
  const assets = Array.isArray(wire.assets) ? wire.assets.filter(isAsset) : [];
  if (dev) reportDropped(wire);
  const form = parseFormMeta(wire.form);
  const envelope: Envelope = { version, ops, assets, form };
  if (isRecord(raw.csrf)) {
    const header = asString(raw.csrf.header);
    const token = asString(raw.csrf.token);
    if (header !== undefined && token !== undefined) {
      envelope.csrf = { header, token };
    }
  }
  const requestId = asString(raw.request_id);
  if (requestId !== undefined) {
    envelope.request_id = requestId;
  }
  return envelope;
}

export class Applier {
  readonly #ops = new Map<string, OpHandler>();
  readonly #dispatch: (event: string, detail: Record<string, unknown>) => void;
  readonly #mergeContext: (data: Record<string, unknown>) => void;
  readonly #document: Document;
  readonly #dirtySince: (snapshot: number) => (field: Element) => boolean;
  readonly #isTouched: (el: Element) => boolean;
  readonly #layers: LayerBridge | undefined;
  readonly #history: HistoryAdapter | undefined;
  readonly #navigate: Navigate | undefined;
  readonly #assets: AssetBridge | undefined;
  readonly #mount: MountRegistry | undefined;
  readonly #refresh: ZoneFetch | undefined;
  readonly #here: () => string;
  readonly #dev: () => boolean;
  // Monotonic apply counter per zone. The lazy-zone triggers read it so a zone
  // whose ancestor was re-created mid-flight does not enqueue a stale second GET.
  readonly #applied = new Map<string, number>();
  // Serial of the dev timing marks, so two ops sharing a label hold two marks.
  #timings = 0;

  constructor(deps: ApplyDeps) {
    this.#dispatch = deps.dispatch;
    this.#mergeContext = deps.mergeContext;
    this.#document = deps.document ?? document;
    this.#dev = devReader(deps.dev);
    this.#dirtySince = deps.dirtySince ?? (() => () => false);
    this.#isTouched = deps.isTouched ?? (() => false);
    this.#layers = deps.layers;
    this.#history = deps.history;
    this.#navigate = deps.navigate;
    this.#assets = deps.assets;
    this.#mount = deps.mount;
    this.#refresh = deps.refresh;
    this.#here = deps.here ?? (() => currentUrl(this.#document));
  }

  // Drop every custom op so vitest files do not leak registrations into one
  // another. The built-ins live in the typed switch, not the registry, so they
  // survive the clear with no re-seat.
  _reset(): void {
    this.#ops.clear();
    this.#applied.clear();
  }

  // The apply counter of a zone, exposed so the lazy-zone triggers drop a GET
  // aimed at a generation that has already moved on.
  generation(zone: string): number {
    return this.#applied.get(zone) ?? 0;
  }

  defineOp(name: string, handler: OpHandler): void {
    this.#ops.set(name, handler);
  }

  // The snapshot is the dirty counter wire.ts captured at fetch time. A direct
  // apply with no snapshot uses the highest mark, so no field reads as dirty.
  // The page scopes zone patches to the page a zone GET fetched. The pipeline
  // is normative: version, before-apply, CSS delta, ops, JS delta, mount, then
  // applied. CSS is gated before the ops, so the body after the gate runs in
  // a continuation. With no asset bridge the gate is a straight-through call
  // and the whole apply stays synchronous.
  apply(raw: unknown, snapshot?: number, key?: string, page?: string): Envelope {
    const envelope = parseEnvelope(raw, this.#dev());
    // A version mismatch is a full visit instead of an apply, guarded against a
    // reload loop inside the bridge. true means the bridge took over.
    if (this.#assets?.versionMismatch(envelope.version, this.#here())) {
      return envelope;
    }
    const beforeApply = this.#emit("partial:before-apply", { envelope }, true);
    if (beforeApply.defaultPrevented) return envelope;
    // The per-apply state is captured here and threaded through the ops so two
    // overlapping applies (the second arriving while the first defers behind
    // loadCss) keep their dirty predicate, request key, and touched set apart.
    const state: ApplyState = {
      isDirty: snapshot === undefined ? () => false : this.#dirtySince(snapshot),
      requestKey: key,
      page,
      touched: [],
    };
    const runOps = (): void => this.#runOps(envelope, state);
    if (this.#assets !== undefined) {
      this.#assets.loadCss(envelope.assets, runOps);
    } else {
      runOps();
    }
    return envelope;
  }

  #runOps(envelope: Envelope, state: ApplyState): void {
    // ok stays true only while every op applies clean. A contained op failure or
    // an unknown verb flips it, so partial:applied carries an honest degraded
    // signal even though mount and the event still run over what did change.
    let ok = true;
    for (const op of envelope.ops) {
      // A single failing op is contained so it never poisons the envelope: the
      // remaining ops still apply, the failure surfaces as partial:error, and
      // mount and partial:applied still run over what did change.
      try {
        if (!this.#timedOp(op, state)) ok = false;
      } catch (error) {
        ok = false;
        this.#opError(op, error);
      }
    }
    if (envelope.csrf) this.#rotateCsrf(envelope.csrf);
    // JS after the ops: the target DOM is in place, each URL runs once.
    this.#assets?.loadJs(envelope.assets);
    this.#assets?.acceptVersion(envelope.version);
    this.#runMount(state);
    this.#emit("partial:applied", { envelope, ok }, false);
  }

  // next:mounted on each touched node and a mount-registry pass over each, so
  // "behaviour, revive what was inserted" gets the DOM and the code together.
  #runMount(state: ApplyState): void {
    for (const node of state.touched) {
      if (!node.isConnected) continue;
      node.dispatchEvent(new CustomEvent("next:mounted", { bubbles: true }));
      this.#mount?.run(node);
    }
  }

  // Dev times every op so an apply shows up in the Performance panel beside the
  // rest of the page timeline. Production stops at the first line, one branch on
  // the hot path.
  #timedOp(patch: Patch, state: ApplyState): boolean {
    if (!this.#dev()) return this.#applyOp(patch, state);
    const zone = zoneOf(patch);
    const label = zone ?? patch.op;
    // The serial keeps this mark distinct from the mark of a nested apply under
    // the same label, whose finally would otherwise clear it. The measure keeps
    // the plain name a reader looks for in the panel.
    this.#timings += 1;
    const startMark = `next:apply:${label}:start:${this.#timings}`;
    openMeasure(startMark);
    const started = performance.now();
    try {
      return this.#applyOp(patch, state);
    } finally {
      const ms = (performance.now() - started).toFixed(1);
      closeMeasure(`next:apply:${label}`, startMark);
      reportTiming(
        zone === undefined
          ? `[next] op "${patch.op}" in ${ms} ms`
          : `[next] zone "${zone}" ${patch.op} in ${ms} ms`,
      );
    }
  }

  // Returns true when the op dispatched to a known verb, false when it was an
  // unknown verb the envelope is degraded by. A thrown op is caught by the
  // caller, which records the same failure.
  #applyOp(patch: Patch, state: ApplyState): boolean {
    // A built-in verb dispatches through a typed switch, where narrowing on op
    // gives each verb its own variant without re-deriving fields from unknown.
    // Checking built-ins first also narrows the remaining patch to CustomPatch,
    // so a custom handler reads its own server-authored fields off the open
    // shape with no cast at the call site.
    if (isBuiltin(patch)) {
      this.#applyBuiltin(patch, state);
      return true;
    }
    // A custom op registered through defineOp shares this apply path and the
    // same ApplyContext as the built-ins, the core eating its own dog food.
    const handler = this.#ops.get(patch.op);
    if (handler !== undefined) {
      handler(patch, this.#context());
      return true;
    }
    // An unknown verb is a single skipped op, never a poisoned envelope.
    this.#opError(patch, new Error(`unknown op ${patch.op}`));
    return false;
  }

  // The partial:error of one failed op, its target named when the patch carried
  // a recognised address. Shared by the contained-throw path and the unknown
  // verb so both report the failure the same way.
  #opError(patch: Patch, error: unknown): void {
    const target = describeOpTarget(patch);
    this.#emit(
      "partial:error",
      {
        kind: "op",
        op: patch.op,
        ...(target !== undefined ? { target } : {}),
        error,
      } satisfies PartialError,
      false,
    );
  }

  // The built-in verbs ride the same apply path and ApplyContext as the custom
  // ops, the core eating its own dog food, but their static variants dispatch
  // through this switch rather than a registry that would erase the shape.
  #applyBuiltin(patch: BuiltinPatch, state: ApplyState): void {
    switch (patch.op) {
      case "morph":
        this.#morph(patch, state);
        return;
      case "replace":
        this.#replace(patch, state);
        return;
      case "inner":
        this.#inner(patch, state);
        return;
      case "append":
      case "prepend":
        this.#merge(patch, state);
        return;
      case "remove":
        this.#remove(patch, state);
        return;
      case "refresh":
        this.#refreshOp(patch, state);
        return;
      case "event":
        this.#event(patch);
        return;
      case "layer.open":
        this.#layerOpen(patch);
        return;
      case "layer.close":
        this.#layerClose(patch);
        return;
      case "toast":
        this.#toast(patch);
        return;
      case "url":
        this.#url(patch);
        return;
      case "visit":
        this.#visit(patch);
        return;
      case "context":
        this.#contextOp(patch);
        return;
    }
  }

  #context(): ApplyContext {
    return {
      dispatch: this.#dispatch,
      mergeContext: this.#mergeContext,
      root: this.#document,
      dev: this.#dev(),
    };
  }

  // An href without a zone names no container, the same rule the server
  // builder enforces, so the malformed op stays a no-op.
  #layerOpen(patch: LayerOpenPatch): void {
    if (patch.href !== undefined && patch.zone === undefined) return;
    this.#layers?.open(null, patch.href, patch.zone);
  }

  #layerClose(patch: LayerClosePatch): void {
    // A validation error addresses no layer, so the modal survives by
    // construction: only an explicit close patch reaches the stack.
    this.#layers?.close({
      result: patch.result,
      dismiss: patch.dismiss === true,
      ...(patch.reason !== undefined ? { reason: patch.reason } : {}),
    });
  }

  // toast is sugar over the stack's built-in container, set as textContent
  // there, never parsed as HTML.
  #toast(patch: ToastPatch): void {
    if (patch.text !== undefined)
      this.#layers?.toast(patch.text, patch.variant ?? "info");
  }

  // History from a server-validated href: push or replace, never authored.
  #url(patch: UrlPatch): void {
    if (patch.href === undefined) return;
    if (patch.action === "replace") this.#history?.replace(patch.href);
    else this.#history?.push(patch.href);
  }

  // A redirect is a hard navigation, not a history push: location.assign takes
  // any origin, so the same seam carries an external redirect. The external flag
  // is the server's, the client does not branch on it.
  #visit(patch: VisitPatch): void {
    if (patch.href !== undefined) this.#navigate?.(patch.href);
  }

  // Merge server-serialised provider values into the client context, which fires
  // context-updated so islands react. Only registered serialize providers reach
  // here, the server builds the data.
  #contextOp(patch: ContextPatch): void {
    if (isRecord(patch.data)) this.#mergeContext(patch.data);
  }

  // The default verb. The new content is parsed and script-neutralised, then the
  // morph engine brings the live target up to it with the dirty predicate of the
  // envelope in flight. extract carves the target node out of a full document.
  #morph(patch: MorphPatch, state: ApplyState): void {
    const node = this.#resolve(patch.target, state);
    if (node === null) return;
    const html = patch.html ?? "";
    const content =
      patch.extract === true
        ? this.#extract(html, node, patch.target, state)
        : this.#fragment(html, patch.target);
    if (content === null) return;
    // A root-tag change recreates the node, so morph returns the live root to
    // mark, not the detached original the mount pass would skip.
    const result = morph(node, content, {
      isDirty: state.isDirty,
      isTouched: this.#isTouched,
      dev: this.#dev(),
    });
    this.#mark(result, patch.target, state);
  }

  // Parse a full document and carve out the node matching the target, the path
  // for a server reply that ships the whole page. The cut node still goes
  // through script neutralisation before the engine sees it.
  #extract(
    html: string,
    target: Element,
    patchTarget: Target | undefined,
    state: ApplyState,
  ): Element | null {
    const parsed = new DOMParser().parseFromString(html, "text/html");
    const found =
      this.#resolveIn(parsed, patchTarget, state) ?? matchByTag(parsed, target);
    if (found === null) return null;
    this.#neutraliseScripts(found, patchTarget);
    return found;
  }

  #replace(patch: ReplacePatch, state: ApplyState): void {
    const node = this.#resolve(patch.target, state);
    if (node === null) return;
    const fragment = this.#fragment(patch.html ?? "", patch.target);
    // Every root element captured before the fragment empties into the
    // document, so the mount pass revives each replacement, not only the first.
    const inserted = Array.from(fragment.children);
    fireRemoved(node);
    node.replaceWith(fragment);
    for (const el of inserted) state.touched.push(el);
    // Bump the zone generation once for the replace, even with no root element.
    this.#mark(null, patch.target, state);
  }

  #inner(patch: InnerPatch, state: ApplyState): void {
    const node = this.#resolve(patch.target, state);
    if (node === null) return;
    const fragment = this.#fragment(patch.html ?? "", patch.target);
    // Each old child detaches when the contents swap, so each child element
    // gets its own next:removed while it is still connected.
    for (const child of Array.from(node.children)) fireRemoved(child);
    node.replaceChildren(fragment);
    this.#mark(node, patch.target, state);
  }

  // append and prepend dedupe by data-next-key, falling back to id: an existing
  // node with the same key is replaced in place, not duplicated, so a re-fetched
  // page of a paginated list cannot double its rows.
  #merge(patch: MergePatch, state: ApplyState): void {
    const node = this.#resolve(patch.target, state);
    if (node === null) return;
    const fragment = this.#fragment(patch.html ?? "", patch.target);
    const incoming = Array.from(fragment.children);
    // New rows collect into a fragment so prepend inserts them all in their
    // source order in one move, rather than one-by-one which would reverse them.
    const fresh = this.#document.createDocumentFragment();
    // The keyed rows that found no match, carried with their key so the
    // reconcile pass below can look them up again without re-reading the key.
    const missed: [string, Element][] = [];
    // One pass over the live children instead of a scan per incoming row, so m
    // rows merged into n cost n + m rather than n * m. The index waits for the
    // first keyed row, since a keyless batch matches nothing.
    let index: Map<string, Element> | undefined;
    // Whether an island unmount hook has run. fireRemoved is the only point
    // between the index build and the last row where page code can touch the
    // container, so this is exactly "the snapshot may have gone stale".
    let fired = false;
    for (const child of incoming) {
      const key = keyOf(child);
      if (key === null) {
        fresh.append(child);
        continue;
      }
      index ??= keyIndex(node);
      const existing = index.get(key);
      // The index is a snapshot while fireRemoved runs island unmount listeners
      // that may detach another row, and replaceWith on a detached node is a
      // no-op that would swallow this row. A hit that left the container reads
      // as a miss, the same as an absent entry.
      if (existing?.parentNode !== node) {
        fresh.append(child);
        missed.push([key, child]);
        continue;
      }
      fireRemoved(existing);
      fired = true;
      // The hook may detach the very row it fired on, so the match is re-checked
      // rather than replaced into nothing.
      if (existing.parentNode !== node) {
        fresh.append(child);
        missed.push([key, child]);
        continue;
      }
      existing.replaceWith(child);
      // A live scan would find the replacement from here on, so a later row
      // carrying the same key replaces what just landed, not the detached node.
      index.set(key, child);
    }
    if (fired && missed.length > 0) this.#reconcile(node, missed);
    if (patch.op === "append") node.append(fresh);
    else node.prepend(fresh);
    this.#mark(node, patch.target, state);
    for (const child of incoming) state.touched.push(child);
  }

  // Match the rows that missed against the container an unmount hook has since
  // rewritten. A keyed row such a hook inserts is invisible to the snapshot, and
  // without this pass it would land beside the key it was meant to replace. One
  // rebuild per merge, taken only when a hook ran and left rows unmatched, so
  // the cost stays n + m and an adversarial hook cannot make the merge unbounded.
  #reconcile(node: Element, missed: [string, Element][]): void {
    const live = keyIndex(node);
    for (const [key, child] of missed) {
      const existing = live.get(key);
      if (existing === undefined) continue;
      fireRemoved(existing);
      // A hook running inside this pass can still detach its own row, and the
      // fresh fragment already holds the replacement, so an unreplaced row
      // lands at the edge instead of being swapped away inside the fragment.
      if (existing.parentNode !== node) continue;
      existing.replaceWith(child);
      live.set(key, child);
    }
  }

  #remove(patch: RemovePatch, state: ApplyState): void {
    const node = this.#resolve(patch.target, state);
    if (node === null) return;
    fireRemoved(node);
    node.remove();
  }

  // refresh re-GETs the zone with its own cookies, the safe default of an SSE
  // fan-out: the server says "this zone is stale", the client fetches it fresh.
  // The re-GET targets the page that owns the zone, resolved through the layer
  // stack like a poll tick, so a base-page zone refreshes against its own page
  // even while a modal holds the address bar. A zone absent from the DOM falls
  // back to the current URL.
  #refreshOp(patch: RefreshPatch, state: ApplyState): void {
    const zone = patch.zone ?? patch.target?.zone;
    if (zone === undefined) return;
    const node = this.#resolve({ zone }, state);
    const url =
      node !== null && this.#layers !== undefined
        ? this.#layers.urlFor(node)
        : this.#here();
    this.#refresh?.({ url, zone, headers: { [HEADER_ZONE]: zone } });
  }

  #event(patch: EventPatch): void {
    if (patch.name === undefined) return;
    const detail = isRecord(patch.detail) ? patch.detail : {};
    this.#emit(patch.name, detail, false);
  }

  // Parse a fragment through `<template>` and structurally neutralise every
  // script before the node ever reaches the live document, so no server html can
  // run a script through a patch. The guarantee is observable from jsdom, not
  // leaning on template semantics.
  #fragment(html: string, target: Target | undefined): DocumentFragment {
    const template = this.#document.createElement("template");
    template.innerHTML = html;
    this.#neutraliseScripts(template.content, target);
    return template.content;
  }

  #neutraliseScripts(root: ParentNode, target: Target | undefined): void {
    const scripts = root.querySelectorAll("script");
    for (const script of Array.from(scripts)) {
      script.remove();
      if (this.#dev()) {
        console.warn(
          `[next.partial] removed a <script> from a patch targeting ${
            describeTarget(target) ?? "no target"
          }. Behaviour ships through co-located assets and the event op.`,
        );
      }
    }
  }

  // Record a node as touched for the mount pass and bump the zone's apply
  // counter, the generation the lazy triggers read.
  #mark(node: Element | null, target: Target | undefined, state: ApplyState): void {
    if (node !== null) state.touched.push(node);
    const zone = target?.zone;
    if (zone !== undefined) this.#applied.set(zone, this.generation(zone) + 1);
  }

  // Resolve against the live document. A zone goes to the layer stack with the
  // envelope's page, so a base-page poll cannot morph a same-named modal zone.
  #resolve(target: Target | undefined, state: ApplyState): Element | null {
    if (target?.zone !== undefined && this.#layers !== undefined) {
      return this.#layers.resolveZone(target.zone, this.#document, state.page);
    }
    return this.#resolveIn(this.#document, target, state);
  }

  // Resolve a target against any root, the live document for the verbs or the
  // parsed document for extract. The layer-aware zone resolve lives in #resolve,
  // so the parsed extract document never consults the stack.
  #resolveIn(
    root: Document,
    target: Target | undefined,
    state: ApplyState,
  ): Element | null {
    if (target === undefined) return null;
    if (target.zone !== undefined) {
      return root.querySelector(`[${ATTR_ZONE}="${cssEscape(target.zone)}"]`);
    }
    if (target.form !== undefined) {
      return this.#resolveForm(root, target.form, state);
    }
    if (target.field !== undefined) {
      const [uid, name] = target.field;
      const form = this.#resolveForm(root, uid, state);
      if (form === null) return null;
      return form.querySelector(`[name="${cssEscape(name)}"]`);
    }
    if (target.css !== undefined) {
      return root.querySelector(target.css);
    }
    return null;
  }

  // A repeated form shares one action uid across rows, so an in-flight key picks
  // the submitted row. A keyless request falls back to the first uid match.
  #resolveForm(root: Document, uid: string, state: ApplyState): Element | null {
    const key = state.requestKey;
    if (key !== undefined) {
      const scoped = this.#formQuery(
        root,
        `[${ATTR_ACTION}="${cssEscape(uid)}"][${ATTR_KEY}="${cssEscape(key)}"]`,
      );
      if (scoped !== null) return scoped;
    }
    return this.#formQuery(root, `[${ATTR_ACTION}="${cssEscape(uid)}"]`);
  }

  // In the live document a modal form wins over a same-uid form under it.
  // The parsed extract document holds no layers, so it keeps the plain lookup.
  #formQuery(root: Document, selector: string): Element | null {
    if (root === this.#document && this.#layers !== undefined) {
      return this.#layers.resolveSelector(selector, root);
    }
    return root.querySelector(selector);
  }

  // Rotate the CSRF token in every form of the document so unmorphed forms do
  // not keep a stale token after a `rotate_token` in a layer login.
  #rotateCsrf(csrf: { header: string; token: string }): void {
    const inputs = this.#document.querySelectorAll<HTMLInputElement>(
      'input[name="csrfmiddlewaretoken"]',
    );
    for (const input of Array.from(inputs)) {
      input.value = csrf.token;
    }
  }

  #emit(
    event: string,
    detail: Record<string, unknown>,
    cancelable: boolean,
  ): CustomEvent {
    const custom = new CustomEvent(event, { detail, cancelable });
    this.#document.dispatchEvent(custom);
    this.#dispatch(event, detail);
    return custom;
  }
}

// When the target address is absent from the parsed document, fall back to the
// first body element sharing the live target's tag. text/html parsing already
// seats tr/td inside the right table context, so this keeps those intact.
function matchByTag(parsed: Document, target: Element): Element | null {
  const tag = target.tagName.toLowerCase();
  return parsed.body.querySelector(tag);
}

// The dedup key of a list row: data-next-key first, then id. Absent both, the
// row has no identity and is always inserted, never matched.
function keyOf(el: Element): string | null {
  return el.getAttribute(ATTR_KEY) ?? (el.id !== "" ? el.id : null);
}

// Index the keyed children of a merge container. The first child holding a key
// wins, so a repeated key resolves to the same node an ordered scan would have
// stopped at. Keyless children stay out, a row with no identity never matches.
function keyIndex(container: Element): Map<string, Element> {
  const index = new Map<string, Element>();
  for (const child of container.children) {
    const key = keyOf(child);
    if (key !== null && !index.has(key)) index.set(key, child);
  }
  return index;
}

// Open the diagnostic span of one op. Same containment as closeMeasure: the mark
// runs before the try block that guards the op, so a page whose user timing is
// stubbed or exhausted would otherwise fail the op it was about to measure.
function openMeasure(startMark: string): void {
  try {
    performance.mark(startMark);
  } catch {
    // A measurement never decides the fate of what it measures.
  }
}

// Close the diagnostic span of one op. A user-timing failure stays inside here,
// since thrown from the finally of #timedOp it would displace the outcome of the
// op and the real error of an op that threw.
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

// The timing line of one op. A page can replace console.debug with a throwing
// stub (analytics wrappers, dev overlays do), and thrown from the finally of
// #timedOp it would displace the outcome of the op it reports on.
function reportTiming(message: string): void {
  try {
    console.debug(message);
  } catch {
    // A measurement never decides the fate of what it measured.
  }
}

// The zone an op addresses, the label a reader recognises in a timeline. Each
// verb is read the way it resolves its own zone, so refresh prefers its
// top-level zone over the target one and layer.open carries only the top-level
// field.
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

// The human-readable address an op aimed at, for the error detail. A foreign or
// empty record carries none of the addresses describeTarget reads and so
// describes nothing, the same as an absent target, and the key stays off the
// payload.
function describeOpTarget(patch: { op: string; target?: unknown }): string | undefined {
  return isRecord(patch.target) ? describeTarget(patch.target as Target) : undefined;
}

// The addresses a target may carry, in the order #resolveIn reads them, so a
// description names the address the op resolved through rather than the first
// key the server serialised.
const TARGET_KEYS = [
  "zone",
  "form",
  "field",
  "css",
] as const satisfies (keyof Target)[];

// The human-readable address a target spells. Absent when it carries none of the
// addresses the resolver reads, or when that address refuses to serialise:
// JSON.stringify throws on a circular value, so a description that cannot be
// produced reads as no description rather than deciding the fate of the op it
// describes from inside the very catch that contains that op.
function describeTarget(target: Target | undefined): string | undefined {
  // Script neutralisation only runs on a resolved node, which requires a target
  // carrying a recognised address, so the no-target guard is unreachable from
  // either caller and exists only to keep the helper total.
  /* v8 ignore start */
  if (target === undefined) return undefined;
  /* v8 ignore stop */
  for (const key of TARGET_KEYS) {
    const value = target[key];
    if (value === undefined) continue;
    try {
      return `${key} ${JSON.stringify(value)}`;
    } catch {
      return undefined;
    }
  }
  return undefined;
}
