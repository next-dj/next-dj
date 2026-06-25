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
  isRecord,
} from "./protocol";
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
]);

// Narrow a patch to a built-in verb by its op. A type predicate rather than an
// op-only check so the switch in #applyBuiltin sees a BuiltinPatch, with no
// CustomPatch in the union to defeat the per-op narrowing.
function isBuiltin(patch: Patch): patch is BuiltinPatch {
  return typeof patch.op === "string" && BUILTIN_OPS.has(patch.op);
}

export interface Asset {
  kind: string;
  url: string;
}

// Narrow an unknown wire entry to an Asset. The manifest crosses the wire
// boundary like the ops do, so a malformed entry is dropped here rather than
// cast blind into envelope.assets and the event details. Only the two kinds the
// loader acts on pass, so a junk kind never rides into the event details.
export function isAsset(value: unknown): value is Asset {
  return (
    isRecord(value) &&
    (value.kind === "css" || value.kind === "js") &&
    typeof value.url === "string"
  );
}

export interface FormMeta {
  uid: string;
  valid: boolean;
  errors: Record<string, string[]>;
}

export interface Envelope {
  version: string;
  ops: Patch[];
  assets: Asset[];
  form: FormMeta | null;
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

// The layer-aware bits the applier needs from the layer stack: a top-down zone
// resolve, the open and close verbs, and the toast container. The LayerStack
// satisfies this structurally, so partial.ts passes it directly. A
// server-initiated open carries no opener element.
export interface LayerBridge {
  resolveZone(name: string, root: ParentNode): Element | null;
  open(opener: null, href: string, zone: string): unknown;
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
  loadCss(manifest: Asset[], done: () => void): void;
  loadJs(manifest: Asset[]): void;
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
  touched: Element[];
}

export interface ApplyDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  mergeContext: (data: Record<string, unknown>) => void;
  document?: Document;
  // Dev builds warn on each neutralised script. The flag is injectable so
  // tests assert both the warn-on and the silent-off behaviour.
  dev?: boolean;
  // Build the morph dirty predicate from the request snapshot wire.ts threads
  // in. Absent, no field is treated as dirty and the server value always wins.
  dirtySince?: (snapshot: number) => (field: Element) => boolean;
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

// Narrow an unknown JSON value into an Envelope. Missing meta collapses to its
// empty value so a terse server envelope parses without optional-field noise.
export function parseEnvelope(raw: unknown): Envelope {
  if (!isRecord(raw)) {
    throw new TypeError("partial envelope is not an object");
  }
  const wire: RawEnvelope = raw;
  const version = asString(wire.version);
  if (version === undefined) {
    throw new TypeError("partial envelope is missing version");
  }
  // Keep only record ops, so a non-object element (ops: [null]) is a dropped op
  // rather than a poison that throws mid-apply over a half-mutated DOM. Each op
  // still carries an unknown op name, narrowed by isBuiltin at apply time.
  const ops = Array.isArray(wire.ops) ? (wire.ops.filter(isRecord) as Patch[]) : [];
  const assets = Array.isArray(wire.assets) ? wire.assets.filter(isAsset) : [];
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
  readonly #ops: Map<string, OpHandler> = new Map();
  readonly #dispatch: (event: string, detail: Record<string, unknown>) => void;
  readonly #mergeContext: (data: Record<string, unknown>) => void;
  readonly #document: Document;
  readonly #dirtySince: (snapshot: number) => (field: Element) => boolean;
  readonly #layers: LayerBridge | undefined;
  readonly #history: HistoryAdapter | undefined;
  readonly #navigate: Navigate | undefined;
  readonly #assets: AssetBridge | undefined;
  readonly #mount: MountRegistry | undefined;
  readonly #refresh: ZoneFetch | undefined;
  readonly #here: () => string;
  readonly #dev: boolean;
  // Monotonic apply counter per zone. The lazy-zone triggers read it so a zone
  // whose ancestor was re-created mid-flight does not enqueue a stale second GET.
  readonly #applied: Map<string, number> = new Map();

  constructor(deps: ApplyDeps) {
    this.#dispatch = deps.dispatch;
    this.#mergeContext = deps.mergeContext;
    this.#document = deps.document ?? document;
    this.#dev = deps.dev ?? false;
    this.#dirtySince = deps.dirtySince ?? (() => () => false);
    this.#layers = deps.layers;
    this.#history = deps.history;
    this.#navigate = deps.navigate;
    this.#assets = deps.assets;
    this.#mount = deps.mount;
    this.#refresh = deps.refresh;
    this.#here = deps.here ?? (() => this.#document.location.pathname);
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
  // The pipeline is normative: version → before-apply → CSS delta → ops → JS
  // delta → mount → applied. CSS is gated before the ops, so the body after the
  // gate runs in a continuation. With no asset bridge the gate is a
  // straight-through call and the whole apply stays synchronous.
  apply(raw: unknown, snapshot?: number, key?: string): Envelope {
    const envelope = parseEnvelope(raw);
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
        if (!this.#applyOp(op, state)) ok = false;
      } catch (error) {
        ok = false;
        this.#emit("partial:error", { kind: "op", op: op.op, error }, false);
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
    this.#emit(
      "partial:error",
      { kind: "op", op: patch.op, error: new Error(`unknown op ${patch.op}`) },
      false,
    );
    return false;
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
        this.#merge(patch, "append", state);
        return;
      case "prepend":
        this.#merge(patch, "prepend", state);
        return;
      case "remove":
        this.#remove(patch, state);
        return;
      case "refresh":
        this.#refreshOp(patch);
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
      dev: this.#dev,
    };
  }

  #layerOpen(patch: LayerOpenPatch): void {
    if (patch.zone !== undefined && patch.href !== undefined)
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
    morph(node, content, { isDirty: state.isDirty });
    this.#mark(node, patch.target, state);
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
    // The first child is the new live node, captured before the fragment is
    // emptied into the document, so mount sees the replacement.
    const inserted = fragment.firstElementChild;
    fireRemoved(node);
    node.replaceWith(fragment);
    this.#mark(inserted ?? null, patch.target, state);
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
  #merge(patch: MergePatch, side: "append" | "prepend", state: ApplyState): void {
    const node = this.#resolve(patch.target, state);
    if (node === null) return;
    const fragment = this.#fragment(patch.html ?? "", patch.target);
    const incoming = Array.from(fragment.children);
    // New rows collect into a fragment so prepend inserts them all in their
    // source order in one move, rather than one-by-one which would reverse them.
    const fresh = this.#document.createDocumentFragment();
    for (const child of incoming) {
      const key = keyOf(child);
      const existing = key === null ? null : matchKey(node, key);
      if (existing !== null) {
        fireRemoved(existing);
        existing.replaceWith(child);
      } else {
        fresh.append(child);
      }
    }
    if (side === "append") node.append(fresh);
    else node.prepend(fresh);
    this.#mark(node, patch.target, state);
    for (const child of incoming) state.touched.push(child);
  }

  #remove(patch: RemovePatch, state: ApplyState): void {
    const node = this.#resolve(patch.target, state);
    if (node === null) return;
    fireRemoved(node);
    node.remove();
  }

  // refresh re-GETs the zone with its own cookies, the safe default of an SSE
  // fan-out: the server says "this zone is stale", the client fetches it fresh.
  #refreshOp(patch: RefreshPatch): void {
    const zone = patch.zone ?? patch.target?.zone;
    if (zone === undefined) return;
    this.#refresh?.({
      url: this.#here(),
      zone,
      headers: { [HEADER_ZONE]: zone },
    });
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
      if (this.#dev) {
        console.warn(
          `[next.partial] removed a <script> from a patch targeting ${describeTarget(
            target,
          )}. Behaviour ships through co-located assets and the event op.`,
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

  // Resolve against the live document. A zone is asked of the layer stack
  // first, top layer down, so a zone inside the upper modal wins over the
  // same-named page zone beneath it.
  #resolve(target: Target | undefined, state: ApplyState): Element | null {
    if (target?.zone !== undefined && this.#layers !== undefined) {
      return this.#layers.resolveZone(target.zone, this.#document);
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
  // the submitted row; a keyless request falls back to the first uid match.
  #resolveForm(root: Document, uid: string, state: ApplyState): Element | null {
    const key = state.requestKey;
    if (key !== undefined) {
      const scoped = root.querySelector(
        `[${ATTR_ACTION}="${cssEscape(uid)}"][${ATTR_KEY}="${cssEscape(key)}"]`,
      );
      if (scoped !== null) return scoped;
    }
    return root.querySelector(`[${ATTR_ACTION}="${cssEscape(uid)}"]`);
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

// Find an existing child of the container sharing the incoming row's key, the
// node append/prepend replaces in place.
function matchKey(container: Element, key: string): Element | null {
  for (const child of Array.from(container.children)) {
    if (keyOf(child) === key) return child;
  }
  return null;
}

function describeTarget(target: Target | undefined): string {
  // Script neutralisation only runs on a resolved node, which requires a target
  // carrying a recognised key, so the no-target and no-key guards are unreachable
  // here and exist only to keep the helper total.
  /* v8 ignore start */
  if (target === undefined) return "no target";
  const key = Object.keys(target)[0];
  if (key === undefined) return "no target";
  /* v8 ignore stop */
  return `${key} ${JSON.stringify(target[key as keyof Target])}`;
}
