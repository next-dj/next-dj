// Envelope parsing, the built-in verbs, the custom-op registry, and the
// structural neutralisation of script elements before insertion. The applier
// stays a thin executor: the server authors every address and verb.

import { morph } from "./morph";

// The wire content-type marker (invariant 9) and the Accept that doubles the
// partial switch on content negotiation. Both must match the server exactly.
export const CONTENT_TYPE = "application/vnd.next.patches+json";
export const ACCEPT = "application/vnd.next.patches+json, text/html;q=0.9";

export interface Target {
  zone?: string;
  form?: string;
  field?: [string, string];
  css?: string;
}

export interface Patch {
  op: string;
  target?: Target;
  html?: string;
  [extra: string]: unknown;
}

export interface Asset {
  kind: string;
  url: string;
}

export interface DeferZone {
  zone: string;
  trigger: string;
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
  defer: DeferZone[];
  form: FormMeta | null;
  csrf?: { header: string; token: string };
  request_id?: string;
}

// A custom-op handler receives the raw patch. Built-in verbs and the custom
// registry share the same shape so the core eats its own dog food.
export type OpHandler = (patch: Patch, ctx: ApplyContext) => void;

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
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

// Narrow an unknown JSON value into an Envelope. Missing meta collapses to its
// empty value so a terse server envelope parses without optional-field noise.
export function parseEnvelope(raw: unknown): Envelope {
  if (!isRecord(raw)) {
    throw new TypeError("partial envelope is not an object");
  }
  const version = asString(raw.version);
  if (version === undefined) {
    throw new TypeError("partial envelope is missing version");
  }
  const ops = Array.isArray(raw.ops) ? (raw.ops as Patch[]) : [];
  const assets = Array.isArray(raw.assets) ? (raw.assets as Asset[]) : [];
  const defer = Array.isArray(raw.defer) ? (raw.defer as DeferZone[]) : [];
  const form = isRecord(raw.form) ? (raw.form as unknown as FormMeta) : null;
  const envelope: Envelope = { version, ops, assets, defer, form };
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
  #dev: boolean;
  // The morph dirty predicate for the envelope in flight, built from the wire
  // snapshot and reset once the envelope is fully applied.
  #isDirty: (field: Element) => boolean = () => false;

  constructor(deps: ApplyDeps) {
    this.#dispatch = deps.dispatch;
    this.#mergeContext = deps.mergeContext;
    this.#document = deps.document ?? document;
    this.#dev = deps.dev ?? false;
    this.#dirtySince = deps.dirtySince ?? (() => () => false);
    this.#layers = deps.layers;
    this.#history = deps.history;
    this.#registerBuiltins();
  }

  // Drop every custom op and re-seat the built-ins so vitest files do not
  // leak registrations into one another.
  _reset(): void {
    this.#ops.clear();
    this.#registerBuiltins();
  }

  defineOp(name: string, handler: OpHandler): void {
    this.#ops.set(name, handler);
  }

  // The snapshot is the dirty counter wire.ts captured at fetch time. A direct
  // apply with no snapshot uses the highest mark, so no field reads as dirty.
  apply(raw: unknown, snapshot?: number): Envelope {
    const envelope = parseEnvelope(raw);
    const beforeApply = this.#emit("partial:before-apply", { envelope }, true);
    if (beforeApply.defaultPrevented) return envelope;
    this.#isDirty = snapshot === undefined ? () => false : this.#dirtySince(snapshot);
    for (const op of envelope.ops) {
      this.#applyOp(op);
    }
    if (envelope.csrf) this.#rotateCsrf(envelope.csrf);
    this.#emit("partial:applied", { envelope }, false);
    return envelope;
  }

  #applyOp(patch: Patch): void {
    const handler = this.#ops.get(patch.op);
    if (handler === undefined) {
      // An unknown verb is a single skipped op, never a poisoned envelope.
      this.#emit(
        "partial:error",
        { status: 0, body: "", error: new Error(`unknown op ${patch.op}`) },
        false,
      );
      return;
    }
    handler(patch, this.#context());
  }

  #context(): ApplyContext {
    return {
      dispatch: this.#dispatch,
      mergeContext: this.#mergeContext,
      root: this.#document,
      dev: this.#dev,
    };
  }

  #registerBuiltins(): void {
    // morph is the default verb: the target subtree is reused, not swapped.
    // replace is the explicit opt-out (a wholesale swap, no morph), inner
    // replaces only the contents.
    this.#ops.set("morph", (patch) => this.#morph(patch));
    this.#ops.set("replace", (patch) => this.#replace(patch));
    this.#ops.set("inner", (patch) => this.#inner(patch));
    this.#ops.set("remove", (patch) => this.#remove(patch));
    this.#ops.set("event", (patch) => this.#event(patch));
    // The layer, toast, and history verbs ride the same ops registry as the
    // custom defineOp path: the core eats its own dog food.
    this.#ops.set("layer.open", (patch) => {
      const zone = asString(patch.zone);
      const href = asString(patch.href);
      if (zone !== undefined && href !== undefined)
        this.#layers?.open(null, href, zone);
    });
    this.#ops.set("layer.close", (patch) => {
      // A validation error addresses no layer, so the modal survives by
      // construction: only an explicit close patch reaches the stack.
      this.#layers?.close({
        result: patch.result,
        dismiss: patch.dismiss === true,
        reason: asString(patch.reason),
      });
    });
    this.#ops.set("toast", (patch) => {
      // toast is sugar over the stack's built-in container, set as textContent
      // there, never parsed as HTML.
      const text = asString(patch.text);
      if (text !== undefined)
        this.#layers?.toast(text, asString(patch.variant) ?? "info");
    });
    this.#ops.set("url", (patch) => {
      // History from a server-validated href: push or replace, never authored.
      const href = asString(patch.href);
      if (href === undefined) return;
      if (asString(patch.action) === "replace") this.#history?.replace(href);
      else this.#history?.push(href);
    });
  }

  // The default verb. The new content is parsed and script-neutralised, then the
  // morph engine brings the live target up to it with the dirty predicate of the
  // envelope in flight. extract carves the target node out of a full document.
  #morph(patch: Patch): void {
    const node = this.#resolve(patch.target);
    if (node === null) return;
    const html = patch.html ?? "";
    const content =
      patch.extract === true
        ? this.#extract(html, node, patch.target)
        : this.#fragment(html, patch.target);
    if (content === null) return;
    morph(node, content, { isDirty: this.#isDirty });
  }

  // Parse a full document and carve out the node matching the target, the path
  // for a server reply that ships the whole page. The cut node still goes
  // through script neutralisation before the engine sees it.
  #extract(
    html: string,
    target: Element,
    patchTarget: Target | undefined,
  ): Element | null {
    const parsed = new DOMParser().parseFromString(html, "text/html");
    const found = this.#resolveIn(parsed, patchTarget) ?? matchByTag(parsed, target);
    if (found === null) return null;
    this.#neutraliseScripts(found, patchTarget);
    return found;
  }

  #replace(patch: Patch): void {
    const node = this.#resolve(patch.target);
    if (node === null) return;
    const fragment = this.#fragment(patch.html ?? "", patch.target);
    node.replaceWith(fragment);
  }

  #inner(patch: Patch): void {
    const node = this.#resolve(patch.target);
    if (node === null) return;
    const fragment = this.#fragment(patch.html ?? "", patch.target);
    node.replaceChildren(fragment);
  }

  #remove(patch: Patch): void {
    const node = this.#resolve(patch.target);
    if (node === null) return;
    node.remove();
  }

  #event(patch: Patch): void {
    const name = asString(patch.name);
    if (name === undefined) return;
    const detail = isRecord(patch.detail) ? patch.detail : {};
    this.#emit(name, detail, false);
  }

  // Parse a fragment through `<template>` and structurally neutralise every
  // script before the node ever reaches the live document (invariant 4). The
  // guarantee is observable from jsdom, not leaning on template semantics.
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

  // Resolve against the live document. A zone is asked of the layer stack
  // first, top layer down, so a zone inside the upper modal wins over the
  // same-named page zone beneath it.
  #resolve(target: Target | undefined): Element | null {
    if (target?.zone !== undefined && this.#layers !== undefined) {
      return this.#layers.resolveZone(target.zone, this.#document);
    }
    return this.#resolveIn(this.#document, target);
  }

  // Resolve a target against any root, the live document for the verbs or the
  // parsed document for extract. The layer-aware zone resolve lives in #resolve,
  // so the parsed extract document never consults the stack.
  #resolveIn(root: Document, target: Target | undefined): Element | null {
    if (target === undefined) return null;
    if (target.zone !== undefined) {
      return root.querySelector(`[data-next-zone="${cssEscape(target.zone)}"]`);
    }
    if (target.form !== undefined) {
      return this.#resolveForm(root, target.form);
    }
    if (target.field !== undefined) {
      const [uid, name] = target.field;
      const form = this.#resolveForm(root, uid);
      if (form === null) return null;
      return form.querySelector(`[name="${cssEscape(name)}"]`);
    }
    if (target.css !== undefined) {
      return root.querySelector(target.css);
    }
    return null;
  }

  #resolveForm(root: Document, uid: string): Element | null {
    return root.querySelector(`[data-next-action="${cssEscape(uid)}"]`);
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

function describeTarget(target: Target | undefined): string {
  if (target === undefined) return "no target";
  const key = Object.keys(target)[0];
  return key === undefined
    ? "no target"
    : `${key} ${JSON.stringify(target[key as keyof Target])}`;
}

// CSS.escape is unavailable in jsdom, so quoted attribute values are escaped by
// hand. Server-authored uids and zone names are ASCII slugs, this only guards
// the rare embedded quote or backslash.
function cssEscape(value: string): string {
  return value.replace(/["\\]/g, "\\$&");
}
