// Envelope parsing, the built-in verbs, the custom-op registry, and script
// neutralisation before insertion. The server authors every address and verb.

import { fireRemoved, morph } from "./morph";
import { readPatch, writeHead } from "./head";
import type { HeadPatch } from "./head";
import type { Intent, Navigation } from "./navigation";
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
import type { DevFlag, Diagnostics, PartialError } from "./protocol";
import type { Navigate } from "./wire";

export interface Target {
  zone?: string;
  form?: string;
  field?: [string, string];
  css?: string;
}

/** The built-in verbs as a discriminated union keyed by op. */
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

/** How a merge row is matched against the container, the server's vocabulary. */
export type DedupeMode = "key" | "id";

export interface MergePatch {
  op: "append" | "prepend";
  target?: Target;
  html?: string;
  // Absent from an older server's envelope, which keyed by data-next-key then id.
  dedupe?: DedupeMode;
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

// Each key is the resolved tag value, null removing the tag and absent leaving it.
export interface MetaPatch {
  op: "meta";
  title?: unknown;
  description?: unknown;
  canonical?: unknown;
  robots?: unknown;
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
  | ContextPatch
  | MetaPatch;

/** A custom op registered through defineOp, its payload open past the op. */
export interface CustomPatch {
  op: string;
  [extra: string]: unknown;
}

export type Patch = BuiltinPatch | CustomPatch;

// The built-in op names, kept in sync with the BuiltinPatch union. A custom op
// under a built-in name never reaches the registry, the switch claims it.
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
  "meta",
] satisfies BuiltinPatch["op"][]);

// A predicate, not a boolean check, so #applyBuiltin keeps the per-op narrowing.
function isBuiltin(patch: Patch): patch is BuiltinPatch {
  return BUILTIN_OPS.has(patch.op);
}

// An op-less record is dropped at the boundary like any other malformed op.
export function isPatch(value: unknown): value is Patch {
  return isRecord(value) && typeof value.op === "string";
}

/** How an asset is inserted, the verb the loader acts on. */
export type AssetLoad = "link" | "script" | "module";

export interface Asset {
  kind: string;
  url: string;
  // The server-derived insertion verb. Absent for a custom kind with no verb.
  load?: AssetLoad;
  // The inline body of a co-located asset, absent on a URL-form asset.
  inline?: string;
}

// A wire load spelling anything outside the three verbs must not ride into assets.
function isAssetLoad(value: unknown): value is AssetLoad {
  return value === "link" || value === "script" || value === "module";
}

/**
 * The insertion verb of a wire asset.
 *
 * A url-form entry of a built-in kind keeps its implied verb so an older server's
 * envelope still loads. An inline body takes no such fallback, since guessing the verb
 * from the kind name would execute a body the full render prints verbatim.
 */
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

/** Narrow an unknown wire entry to an Asset, dropping a malformed one. */
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

/** The parsed wire envelope, read-only past the boundary. */
export interface Envelope {
  readonly version: string;
  readonly ops: readonly Patch[];
  readonly assets: readonly Asset[];
  readonly form: FormMeta | null;
  csrf?: { header: string; token: string };
  request_id?: string;
}

/** A custom-op handler over the open patch shape and the shared ApplyContext. */
export type OpHandler = (patch: CustomPatch, ctx: ApplyContext) => void;

export interface ApplyContext {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  mergeContext: (data: Record<string, unknown>) => void;
  root: Document;
  dev: boolean;
}

/** The layer-stack surface the applier needs, satisfied structurally by LayerStack. */
export interface LayerBridge {
  resolveZone(name: string, root: ParentNode, page?: string): Element | null;
  resolveSelector(selector: string, root: ParentNode): Element | null;
  urlFor(el: Element): string;
  open(opener: null, href?: string, zone?: string): unknown;
  close(detail: { result?: unknown; dismiss?: boolean; reason?: string }): void;
  head(patch: HeadPatch, page?: string): void;
  toast(text: string, variant: string): void;
}

/** The history seam the navigation writes through, injectable for the jsdom harness. */
export interface HistoryAdapter {
  push(href: string): void;
  replace(href: string): void;
}

/** The asset and version bridge the applier consults around the ops. */
export interface AssetBridge {
  loadCss(manifest: readonly Asset[], done: () => void): void;
  loadJs(manifest: readonly Asset[]): void;
  versionMismatch(envelopeVersion: string, url: string): boolean;
  acceptVersion(envelopeVersion: string): void;
}

/** A mount callback run over every inserted subtree, the DOMContentLoaded stand-in. */
export type MountCallback = (root: ParentNode) => void;

export interface MountRegistry {
  /** Run the registered callbacks over a freshly inserted subtree. */
  run(root: ParentNode): void;
}

/** The post-insert pass, next:mounted and the registry over each attached node. */
export function mountNodes(
  nodes: readonly Element[],
  registry: MountRegistry | undefined,
): void {
  for (const node of nodes) {
    if (!node.isConnected) continue;
    node.dispatchEvent(new CustomEvent("next:mounted", { bubbles: true }));
    registry?.run(node);
  }
}

/** The fetch bridge the refresh verb uses to re-GET a zone. */
export type ZoneFetch = (request: {
  url: string;
  zone: string;
  headers?: Record<string, string>;
}) => void;

// The mutable state of one apply, threaded through the ops so two overlapping applies
// (the second starting while the first defers behind loadCss) stay apart.
interface ApplyState {
  isDirty: (field: Element) => boolean;
  requestKey: string | undefined;
  page: string | undefined;
  owner: string | undefined;
  touched: Element[];
  // The meta and url ops, held for the commit phase so their order in the envelope
  // cannot decide which history entry a title lands on.
  head: HeadPatch | undefined;
  intents: Intent[];
}

/** What an apply knows about the request it answers. */
export interface ApplyOptions {
  snapshot?: number | undefined;
  key?: string | undefined;
  // The page a safe zone GET fetched, scoping its zone patches to that page.
  page?: string | undefined;
  // The page a meta op belongs to, apart from page so a stream never scopes zones.
  owner?: string | undefined;
}

export interface ApplyDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  mergeContext: (data: Record<string, unknown>) => void;
  document?: Document;
  // The dev flag custom ops read, a getter so the owner can flip it.
  dev?: DevFlag;
  // The dev channel once its chunk lands. Absent or undefined, nothing is reported.
  diagnostics?: () => Diagnostics | undefined;
  // The morph dirty predicate from a request snapshot. Absent, no field is dirty.
  dirtySince?: (snapshot: number) => (field: Element) => boolean;
  // Whether an element was ever touched, carrying <details> open state past a patch.
  isTouched?: (el: Element) => boolean;
  // The four seams below are read through a call, not captured: the owner rebuilds
  // them under a live applier, and a captured instance would be the outgoing one.
  // The layer stack. Absent, zone resolve falls back to the document.
  layers?: () => LayerBridge;
  // The address bar the url verb and a layer's held push commit to, absent a no-op.
  navigation?: () => Navigation;
  // The navigation seam for the visit verb. Absent, the verb is a no-op.
  navigate?: () => Navigate;
  // The asset loader and version safeguard. Absent, ops run with no asset handling.
  assets?: () => AssetBridge;
  // The mount registry, run over inserted subtrees. Absent, only next:mounted fires.
  mount?: MountRegistry;
  // The zone re-GET used by the refresh verb. Absent, it is a no-op.
  refresh?: ZoneFetch;
  // The current URL for the version safeguard. Absent, the document location is used.
  here?: () => string;
}

// The raw wire envelope, every field still unknown until parseEnvelope narrows it.
type RawEnvelope = Record<string, unknown>;

// Narrow a form-errors record, keeping only string-array values.
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

// Build the typed form meta from its unknown wire value, null when absent.
function parseFormMeta(value: unknown): FormMeta | null {
  if (!isRecord(value)) return null;
  const uid = asString(value.uid) ?? "";
  const valid = value.valid === true;
  return { uid, valid, errors: parseFormErrors(value.errors) };
}

// Well-formedness is blind to the kind, since the server may register custom kinds.
export function isWellFormedAsset(
  value: unknown,
): value is { kind: string; load: AssetLoad | undefined; inline?: unknown } {
  return (
    isRecord(value) &&
    typeof value.kind === "string" &&
    (value.load === undefined || isAssetLoad(value.load)) &&
    (typeof value.url === "string" || typeof value.inline === "string")
  );
}

/** Narrow an unknown JSON value into an Envelope, collapsing absent meta. */
export function parseEnvelope(raw: unknown, diagnostics?: Diagnostics): Envelope {
  if (!isRecord(raw)) {
    throw new TypeError("partial envelope is not an object");
  }
  const wire: RawEnvelope = raw;
  const version = asString(wire.version);
  if (version === undefined) {
    throw new TypeError("partial envelope is missing version");
  }
  // Ops without a verb are dropped, so none throws mid-apply over a half-mutated DOM.
  const ops = Array.isArray(wire.ops) ? wire.ops.filter(isPatch) : [];
  const assets = Array.isArray(wire.assets) ? wire.assets.filter(isAsset) : [];
  diagnostics?.dropped(wire);
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
  readonly #layers: () => LayerBridge | undefined;
  readonly #navigation: () => Navigation | undefined;
  readonly #navigate: () => Navigate | undefined;
  readonly #assets: () => AssetBridge | undefined;
  readonly #mount: MountRegistry | undefined;
  readonly #refresh: ZoneFetch | undefined;
  readonly #here: () => string;
  readonly #dev: () => boolean;
  readonly #diagnostics: () => Diagnostics | undefined;
  // Monotonic apply counter per zone. The lazy-zone triggers read it so a zone
  // whose ancestor was re-created mid-flight does not enqueue a stale second GET.
  readonly #applied = new Map<string, number>();

  constructor(deps: ApplyDeps) {
    this.#dispatch = deps.dispatch;
    this.#mergeContext = deps.mergeContext;
    this.#document = deps.document ?? document;
    this.#dev = devReader(deps.dev);
    this.#diagnostics = deps.diagnostics ?? (() => undefined);
    this.#dirtySince = deps.dirtySince ?? (() => () => false);
    this.#isTouched = deps.isTouched ?? (() => false);
    this.#layers = deps.layers ?? (() => undefined);
    this.#navigation = deps.navigation ?? (() => undefined);
    this.#navigate = deps.navigate ?? (() => undefined);
    this.#assets = deps.assets ?? (() => undefined);
    this.#mount = deps.mount;
    this.#refresh = deps.refresh;
    this.#here = deps.here ?? (() => currentUrl(this.#document));
  }

  /** Drop every custom op, leaving the typed built-ins untouched. */
  _reset(): void {
    this.#ops.clear();
    this.#applied.clear();
  }

  /** The apply counter of a zone, read by the lazy-zone triggers. */
  generation(zone: string): number {
    return this.#applied.get(zone) ?? 0;
  }

  /** Register a custom op handler under a non-built-in name. */
  defineOp(name: string, handler: OpHandler): void {
    this.#ops.set(name, handler);
  }

  /**
   * Parse and apply a wire envelope, returning the parsed form.
   *
   * The phases run in a fixed order of version, before-apply, CSS delta, ops, history
   * and head, JS delta, mount, applied, then next:navigated. CSS gates the ops, so the
   * rest runs in a continuation.
   */
  apply(raw: unknown, options: ApplyOptions = {}): Envelope {
    const { snapshot, key, page, owner = page } = options;
    const envelope = parseEnvelope(raw, this.#diagnostics());
    // A version mismatch is a full visit instead of an apply, guarded against a
    // reload loop inside the bridge. true means the bridge took over.
    if (this.#assets()?.versionMismatch(envelope.version, this.#here())) {
      return envelope;
    }
    const beforeApply = this.#emit("partial:before-apply", { envelope }, true);
    if (beforeApply.defaultPrevented) return envelope;
    // Captured per-apply and threaded through the ops, so two overlapping
    // applies keep their dirty predicate, request key, and touched set apart.
    const state: ApplyState = {
      isDirty: snapshot === undefined ? () => false : this.#dirtySince(snapshot),
      requestKey: key,
      page,
      owner,
      touched: [],
      head: undefined,
      intents: [],
    };
    const runOps = (): void => this.#runOps(envelope, state);
    const assets = this.#assets();
    if (assets !== undefined) {
      assets.loadCss(envelope.assets, runOps);
    } else {
      runOps();
    }
    return envelope;
  }

  #runOps(envelope: Envelope, state: ApplyState): void {
    // Opened before the ops, so a layer.close writing history folds into this commit.
    const commit = this.#navigation()?.begin();
    try {
      // ok flips on any contained failure, so partial:applied carries an honest signal.
      let ok = true;
      for (const op of envelope.ops) {
        // A failing op is contained, the rest apply and it surfaces as partial:error.
        try {
          if (!this.#timedOp(op, state)) ok = false;
        } catch (error) {
          ok = false;
          this.#opError(op, error);
        }
      }
      // History before the head, so the entry being left keeps its own title.
      commit?.claim(state.owner);
      for (const intent of state.intents) commit?.write(intent);
      if (state.head !== undefined) this.#commitHead(state.head, state);
      if (envelope.csrf) this.#rotateCsrf(envelope.csrf);
      // JS after the ops: the target DOM is in place, each URL runs once.
      this.#assets()?.loadJs(envelope.assets);
      this.#assets()?.acceptVersion(envelope.version);
      mountNodes(state.touched, this.#mount);
      this.#emit("partial:applied", { envelope, ok, nodes: state.touched }, false);
    } finally {
      commit?.end();
    }
  }

  // Against the stack as the ops left it. An envelope moving the address bar speaks
  // for the page it moves to, the top of the stack, not the page it was fetched for.
  #commitHead(head: HeadPatch, state: ApplyState): void {
    const layers = this.#layers();
    if (layers === undefined) writeHead(this.#document, head);
    else layers.head(head, state.intents.length > 0 ? undefined : state.owner);
  }

  // Dev times every op, production stops at the first line, one branch on the hot path.
  #timedOp(patch: Patch, state: ApplyState): boolean {
    const diagnostics = this.#diagnostics();
    const run = (): boolean => this.#applyOp(patch, state);
    return diagnostics === undefined ? run() : diagnostics.timed(patch, run);
  }

  // Returns false for an unknown verb, a thrown op is caught by the caller.
  #applyOp(patch: Patch, state: ApplyState): boolean {
    // Built-ins first, so the switch narrows each verb and the remaining patch
    // narrows to CustomPatch for the handler with no cast.
    if (isBuiltin(patch)) {
      this.#applyBuiltin(patch, state);
      return true;
    }
    // A custom op shares this apply path and ApplyContext with the built-ins.
    const handler = this.#ops.get(patch.op);
    if (handler !== undefined) {
      handler(patch, this.#context());
      return true;
    }
    // An unknown verb is a single skipped op, never a poisoned envelope.
    this.#opError(patch, new Error(`unknown op ${patch.op}`));
    return false;
  }

  // The partial:error of one failed op, shared by the throw path and unknown verbs.
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

  // A switch, not the registry, so each built-in verb keeps its static variant.
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
        this.#url(patch, state);
        return;
      case "visit":
        this.#visit(patch);
        return;
      case "context":
        this.#contextOp(patch);
        return;
      case "meta":
        this.#meta(patch, state);
        return;
      // A verb missing here would be a silent no-op reported as ok, so the never
      // binding turns it into a build error, and this throw is that error at runtime.
      /* v8 ignore next 3 */
      default: {
        const unhandled: never = patch;
        throw new TypeError(`unhandled built-in op ${JSON.stringify(unhandled)}`);
      }
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
    this.#layers()?.open(null, patch.href, patch.zone);
  }

  #layerClose(patch: LayerClosePatch): void {
    // A validation error addresses no layer, so the modal survives by
    // construction: only an explicit close patch reaches the stack.
    this.#layers()?.close({
      result: patch.result,
      dismiss: patch.dismiss === true,
      ...(patch.reason !== undefined ? { reason: patch.reason } : {}),
    });
  }

  // toast is sugar over the stack's container, textContent there, never parsed as HTML.
  #toast(patch: ToastPatch): void {
    if (patch.text !== undefined)
      this.#layers()?.toast(patch.text, patch.variant ?? "info");
  }

  // History from a server-validated href: push or replace, never authored.
  #url(patch: UrlPatch, state: ApplyState): void {
    if (patch.href === undefined) return;
    const action = patch.action === "replace" ? "replace" : "push";
    state.intents.push({ href: patch.href, action });
  }

  // A redirect is a hard navigation, not a history push. The same seam carries
  // an external redirect, the client does not branch on the external flag.
  #visit(patch: VisitPatch): void {
    if (patch.href !== undefined) this.#navigate()?.(patch.href);
  }

  // Merging into the client context fires context-updated, so islands react.
  #contextOp(patch: ContextPatch): void {
    if (isRecord(patch.data)) this.#mergeContext(patch.data);
  }

  // A later meta op overrides an earlier one tag by tag. One naming no readable tag
  // is dropped, so it cannot mark a layer as carrying its own head.
  #meta(patch: MetaPatch, state: ApplyState): void {
    const head = readPatch(patch);
    if (Object.keys(head).length > 0) state.head = { ...state.head, ...head };
  }

  // The default verb, parsing and neutralising content then morphing the live
  // target. extract carves the target node out of a full document.
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
      keyed: this.#diagnostics()?.keyed,
    });
    this.#mark(result, patch.target, state);
  }

  // Carve the target node out of a full-document reply, still script-neutralised.
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

  // append and prepend dedupe rows under the patch's mode, replacing a matching
  // node in place so a re-fetched list cannot double its rows.
  #merge(patch: MergePatch, state: ApplyState): void {
    const node = this.#resolve(patch.target, state);
    if (node === null) return;
    // Any spelling but "id" keys the default way, so a malformed wire value degrades to
    // the documented default rather than losing every match.
    const mode: DedupeMode = patch.dedupe === "id" ? "id" : "key";
    const fragment = this.#fragment(patch.html ?? "", patch.target);
    const incoming = Array.from(fragment.children);
    // New rows collect into a fragment so prepend inserts them all in their
    // source order in one move, rather than one-by-one which would reverse them.
    const fresh = this.#document.createDocumentFragment();
    // The keyed rows that found no match, carried with their key so the
    // reconcile pass below can look them up again without re-reading the key.
    const missed: [string, Element][] = [];
    // One pass over the live children, not a scan per row, so n rows and m cost
    // n + m. The index waits for the first keyed row, a keyless batch matches nothing.
    let index: Map<string, Element> | undefined;
    // Whether an unmount hook ran, the only point where page code can stale the index.
    let fired = false;
    for (const child of incoming) {
      const key = keyOf(child, mode);
      if (key === null) {
        fresh.append(child);
        continue;
      }
      index ??= keyIndex(node, mode);
      const existing = index.get(key);
      // The index is a snapshot, and replaceWith on a detached node is a no-op
      // that would swallow this row. A hit that left the container reads as a miss.
      if (existing?.parentNode !== node) {
        fresh.append(child);
        missed.push([key, child]);
        continue;
      }
      fireRemoved(existing);
      fired = true;
      // The hook may detach the row it fired on, so the match is re-checked.
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
    if (fired && missed.length > 0) this.#reconcile(node, missed, mode);
    if (patch.op === "append") node.append(fresh);
    else node.prepend(fresh);
    this.#mark(node, patch.target, state);
    for (const child of incoming) state.touched.push(child);
  }

  // One rebuild per merge, only when a hook left rows unmatched, so it stays bounded.
  #reconcile(node: Element, missed: [string, Element][], mode: DedupeMode): void {
    const live = keyIndex(node, mode);
    for (const [key, child] of missed) {
      const existing = live.get(key);
      if (existing === undefined) continue;
      fireRemoved(existing);
      // A hook here can still detach its own row, and the fresh fragment holds
      // the replacement, so an unreplaced row lands at the edge.
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

  // refresh re-GETs the zone with its own cookies, against the page that owns
  // the zone so a base-page zone refreshes even while a modal holds the address
  // bar. A zone absent from the DOM falls back to the current URL.
  #refreshOp(patch: RefreshPatch, state: ApplyState): void {
    const zone = patch.zone ?? patch.target?.zone;
    if (zone === undefined) return;
    const node = this.#resolve({ zone }, state);
    const layers = this.#layers();
    const url =
      node !== null && layers !== undefined ? layers.urlFor(node) : this.#here();
    this.#refresh?.({ url, zone, headers: { [HEADER_ZONE]: zone } });
  }

  #event(patch: EventPatch): void {
    if (patch.name === undefined) return;
    const detail = isRecord(patch.detail) ? patch.detail : {};
    this.#emit(patch.name, detail, false);
  }

  // Parse through <template> and neutralise every script before the node reaches
  // the live document, so no server html can run a script through a patch.
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
      this.#diagnostics()?.stripped(describeTarget(target));
    }
    // A template's content sits outside querySelectorAll, and a consented block's
    // body runs its scripts as it is revealed, so each one is swept as well. The same
    // block rendered with the page keeps its embed script, since no patch runs one.
    for (const template of Array.from(root.querySelectorAll("template"))) {
      this.#neutraliseScripts(template.content, target);
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
    const layers = this.#layers();
    if (target?.zone !== undefined && layers !== undefined) {
      return layers.resolveZone(target.zone, this.#document, state.page);
    }
    return this.#resolveIn(this.#document, target, state);
  }

  // Resolve a target against any root. The layer-aware zone resolve lives in
  // #resolve, so the parsed extract document never consults the stack.
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
    const layers = this.#layers();
    if (root === this.#document && layers !== undefined) {
      return layers.resolveSelector(selector, root);
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

// When the target is absent from the parsed document, fall back to the first
// body element sharing the live target's tag, seated in its table context.
function matchByTag(parsed: Document, target: Element): Element | null {
  const tag = target.tagName.toLowerCase();
  return parsed.body.querySelector(tag);
}

// The dedup key of a list row. "key" reads data-next-key then falls back to id, "id"
// reads only id, so a row with a key but no id has no identity and always inserts.
// The id comes off the attribute, as morph reads it: the property is subject to DOM
// clobbering, where an <input name="id"> shadows form.id with the field itself.
function keyOf(el: Element, mode: DedupeMode): string | null {
  const raw = el.getAttribute("id") ?? "";
  const id = raw === "" ? null : raw;
  if (mode === "id") return id;
  return el.getAttribute(ATTR_KEY) ?? id;
}

// Index the keyed children of a merge container under the keying rule the incoming rows
// use, first holder of a key wins. A keyless child never matches.
function keyIndex(container: Element, mode: DedupeMode): Map<string, Element> {
  const index = new Map<string, Element>();
  for (const child of container.children) {
    const key = keyOf(child, mode);
    if (key !== null && !index.has(key)) index.set(key, child);
  }
  return index;
}

// The human-readable address an op aimed at, for the error detail. A foreign or
// empty record describes nothing, the same as an absent target.
function describeOpTarget(patch: { op: string; target?: unknown }): string | undefined {
  return isRecord(patch.target) ? describeTarget(patch.target as Target) : undefined;
}

// The addresses a target may carry, in the order #resolveIn reads them, so a
// description names the address the op resolved through.
const TARGET_KEYS = [
  "zone",
  "form",
  "field",
  "css",
] as const satisfies (keyof Target)[];

// The human-readable address a target spells. Absent when it carries no
// recognised address, or when that address refuses to serialise (a circular
// value), so a description that cannot be produced reads as no description.
function describeTarget(target: Target | undefined): string | undefined {
  // The no-target guard is unreachable from either caller, kept to make it total.
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
