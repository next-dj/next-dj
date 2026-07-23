// The morph engine brings an old subtree up to new HTML by reusing the live
// nodes that still match, so focus, caret, typed values, open details, and
// scroll survive a patch. Matching runs on id-sets, then a child walk reuses.

import { defaultMove } from "./adapters";

/** Whether a morph replaces the target itself or only its children. */
export type MorphMode = "node" | "children";

/** Relocate a live node before a reference node, a DI seam for the move adapter. */
export type Move = (parent: ParentNode, node: Node, before: Node | null) => void;

/** Injectable hooks and predicates that steer one morph run. */
export interface MorphOptions {
  // Morph the target itself or only its children. Default "node".
  mode?: MorphMode;
  // A dirty field keeps its live value against the server default. Default false.
  isDirty?: (field: Element) => boolean;
  // A touched <details> keeps its open state against a patch it never asked for,
  // since the toggle has no live focus signal a field relies on. Default false.
  isTouched?: (el: Element) => boolean;
  // The move adapter, a DI seam so the native branch runs through a mock in jsdom.
  move?: Move;
  // Before a pair. false skips the whole pair and its subtree.
  beforeNode?: (oldNode: Node | null, newNode: Node) => boolean | void;
  // After a pair has morphed.
  afterNode?: (oldNode: Node, newNode: Node) => void;
  // An unmatched old node is about to be discarded. false keeps it.
  onDiscard?: (node: Node) => boolean | void;
  // Emit markup diagnostics to the console. Default false.
  dev?: boolean;
}

interface Ctx {
  ids: Map<Element, Set<string>>;
  isDirty: (field: Element) => boolean;
  isTouched: (el: Element) => boolean;
  move: Move;
  beforeNode: (oldNode: Node | null, newNode: Node) => boolean | void;
  afterNode: (oldNode: Node, newNode: Node) => void;
  onDiscard: (node: Node) => boolean | void;
}

// Read the id through getAttribute: the `id` property is subject to DOM
// clobbering, an `<input name="id">` shadows form.id.
function readId(el: Element, dev: boolean): string | null {
  const key = el.getAttribute("data-next-key");
  if (key !== null) {
    if (dev && el.getAttribute("id") !== null) {
      console.warn("[next.morph] data-next-key and id on one node", el);
    }
    return key;
  }
  return el.getAttribute("id");
}

// Build id-sets for one tree in a single pass. Each element's id bubbles into
// every ancestor's set, so a wrapper without an id still votes through its
// descendants. Collected ids also land in the universe for the intersection.
function collectIds(
  root: Element,
  into: Map<Element, Set<string>>,
  universe: Set<string>,
  dev: boolean,
): void {
  consume(root, root, into, universe, dev);
  const tagged = root.querySelectorAll("[id],[data-next-key]");
  for (const el of Array.from(tagged)) {
    consume(el, root, into, universe, dev);
  }
}

function consume(
  el: Element,
  root: Element,
  into: Map<Element, Set<string>>,
  universe: Set<string>,
  dev: boolean,
): void {
  const id = readId(el, dev);
  if (id === null) return;
  universe.add(id);
  let node: Element | null = el;
  while (node !== null) {
    let set = into.get(node);
    if (set === undefined) {
      set = new Set();
      into.set(node, set);
    }
    set.add(id);
    if (node === root) break;
    node = node.parentElement;
  }
}

// Persistent ids are present in both trees. An id on one side owns no match, so
// it must not vote.
function intersects(a: Set<string> | undefined, persistent: Set<string>): boolean {
  if (a === undefined) return false;
  for (const id of a) {
    if (persistent.has(id)) return true;
  }
  return false;
}

function sharesPersistent(
  ctx: Ctx,
  oldEl: Element,
  newEl: Element,
  persistent: Set<string>,
): boolean {
  const oldIds = ctx.ids.get(oldEl);
  const newIds = ctx.ids.get(newEl);
  if (oldIds === undefined || newIds === undefined) return false;
  for (const id of oldIds) {
    if (persistent.has(id) && newIds.has(id)) return true;
  }
  return false;
}

function isElement(node: Node): node is Element {
  return node.nodeType === 1;
}

// A hard match is two same-tag elements sharing a persistent id.
function isHardMatch(
  ctx: Ctx,
  oldNode: Node,
  newNode: Node,
  persistent: Set<string>,
): boolean {
  return (
    isElement(oldNode) &&
    isElement(newNode) &&
    oldNode.tagName === newNode.tagName &&
    sharesPersistent(ctx, oldNode, newNode, persistent)
  );
}

// A soft match is a same-tag pair with no persistent id, ids are reserved for a
// hard match.
function isSoftMatch(
  ctx: Ctx,
  oldNode: Node,
  newNode: Node,
  persistent: Set<string>,
): boolean {
  if (oldNode.nodeType !== newNode.nodeType) return false;
  if (isElement(oldNode) && isElement(newNode)) {
    if (oldNode.tagName !== newNode.tagName) return false;
    return !intersects(ctx.ids.get(oldNode), persistent);
  }
  return true;
}

// Find a match for one new child. A hard match is searched along the whole scan,
// a soft match is taken only at the pointer, which is reserved when it carries a
// persistent id.
function findMatch(
  ctx: Ctx,
  pointer: Node | null,
  newChild: Node,
  persistent: Set<string>,
): Node | null {
  for (let scan = pointer; scan !== null; scan = scan.nextSibling) {
    if (isHardMatch(ctx, scan, newChild, persistent)) {
      return scan;
    }
  }
  if (pointer === null || !isSoftMatch(ctx, pointer, newChild, persistent)) {
    return null;
  }
  return pointer;
}

// A hyphenated tag or a shadow root is atomic: on a tag match only attributes
// sync, children are never morphed, the engine never enters the shadow root.
function isAtomic(el: Element): boolean {
  return el.tagName.includes("-") || el.shadowRoot != null;
}

// A keep node is left untouched so a foreign root mounted into it survives, hard
// matched by id or paired by position when the server gives it no stable id.
function isKept(el: Element): boolean {
  return el.hasAttribute("data-next-keep");
}

function emit(target: Element, name: string, detail: Record<string, unknown>): boolean {
  const event = new CustomEvent(name, { bubbles: true, cancelable: true, detail });
  target.dispatchEvent(event);
  return !event.defaultPrevented;
}

/** Signal an element is about to detach so a mounted adapter root can unmount. */
export function fireRemoved(node: Element): void {
  node.dispatchEvent(new CustomEvent("next:removed", { bubbles: true }));
}

function discard(ctx: Ctx, node: Node): void {
  if (ctx.onDiscard(node) === false) return;
  if (isElement(node)) fireRemoved(node);
  (node as ChildNode).remove();
}

// Sync the live value/checked/selected. The attribute twin is handled by the
// attribute pass, the live property is set only when the field is neither active
// nor dirty.
function syncLive(ctx: Ctx, oldEl: Element, newEl: Element): void {
  const tag = oldEl.tagName;
  if (tag === "INPUT") {
    const o = oldEl as HTMLInputElement;
    const n = newEl as HTMLInputElement;
    const type = o.type;
    if (type === "file") return;
    if (type === "checkbox" || type === "radio") {
      o.defaultChecked = n.defaultChecked;
      if (!isActiveOrDirty(ctx, o)) o.checked = n.checked;
      return;
    }
    o.defaultValue = n.defaultValue;
    if (!isActiveOrDirty(ctx, o)) o.value = n.value;
    return;
  }
  if (tag === "TEXTAREA") {
    const o = oldEl as HTMLTextAreaElement;
    const n = newEl as HTMLTextAreaElement;
    o.defaultValue = n.defaultValue;
    if (!isActiveOrDirty(ctx, o)) o.value = n.value;
    return;
  }
  if (tag === "OPTION") {
    const o = oldEl as HTMLOptionElement;
    const n = newEl as HTMLOptionElement;
    const select = o.closest("select");
    const locked = select !== null && isActiveOrDirty(ctx, select);
    // defaultSelected reflects the selected attribute and can perturb the live
    // selection, so a dirty select pins the live selected.
    const wasSelected = o.selected;
    o.defaultSelected = n.defaultSelected;
    o.selected = locked ? wasSelected : n.selected;
  }
}

// Some attributes are owned by syncLive, not the generic pass: value/checked/
// selected twins, a touched <details> open, and a <dialog> open that belongs to
// the layer surface.
function skipAttribute(ctx: Ctx, el: Element, name: string): boolean {
  const tag = el.tagName;
  if (name === "value" || name === "checked") return tag === "INPUT";
  if (name === "selected") return tag === "OPTION";
  if (name === "open") {
    if (tag === "DIALOG") return true;
    // A <details> is server-owned until the user first toggles it, then the
    // user's for the life of the page, so a poll or SSE patch must not resync it
    // shut on a toggle that predates the snapshot and reads as clean.
    return tag === "DETAILS" && ctx.isTouched(el);
  }
  return false;
}

function isActiveOrDirty(ctx: Ctx, el: Element): boolean {
  return el.ownerDocument.activeElement === el || ctx.isDirty(el);
}

// Three-phase attribute sync: add missing, update changed, remove extra.
// Matching attributes are left alone so no extra mutation restarts a CSS
// animation or wakes a MutationObserver. Each change is cancelable.
function syncAttributes(ctx: Ctx, oldEl: Element, newEl: Element): void {
  const newAttrs = newEl.attributes;
  for (const attr of Array.from(newAttrs)) {
    if (skipAttribute(ctx, oldEl, attr.name)) continue;
    if (oldEl.getAttribute(attr.name) === attr.value) continue;
    if (
      emit(oldEl, "next:morph-attribute", { name: attr.name, mutationType: "update" })
    ) {
      try {
        oldEl.setAttribute(attr.name, attr.value);
      } catch {
        // Invalid DOM attribute names (@change, :class) are skipped silently.
      }
    }
  }
  // Snapshot the old names: removeAttribute mutates the live NamedNodeMap, so a
  // fixed list keeps the pass stable while it removes.
  const oldNames = Array.from(oldEl.attributes, (attr) => attr.name);
  for (const name of oldNames) {
    if (newEl.hasAttribute(name) || skipAttribute(ctx, oldEl, name)) continue;
    if (emit(oldEl, "next:morph-attribute", { name, mutationType: "remove" })) {
      oldEl.removeAttribute(name);
    }
  }
}

// Morph a single pair: keep, atomicity, attributes, live properties, recursion.
// The pair is reused, never the new node grafted in.
function morphNode(
  ctx: Ctx,
  oldEl: Element,
  newEl: Element,
  persistent: Set<string>,
): void {
  if (ctx.beforeNode(oldEl, newEl) === false) return;
  if (!emit(oldEl, "next:morph-element", { newNode: newEl })) return;

  if (isKept(oldEl)) {
    // A keep node with an id is untouched: no attribute sync, no recursion.
    ctx.afterNode(oldEl, newEl);
    return;
  }

  syncAttributes(ctx, oldEl, newEl);
  syncLive(ctx, oldEl, newEl);

  if (!isAtomic(oldEl)) {
    morphChildren(ctx, oldEl, newEl, persistent);
  }
  ctx.afterNode(oldEl, newEl);
}

// Walk new children left to right against an insertion pointer into old children:
// hard match, soft match, or create. A match at the pointer morphs in place, a
// match found further on is moved before the pointer, and trailing old children
// are discarded when the new ones run out.
function morphChildren(
  ctx: Ctx,
  oldParent: Element,
  newParent: ParentNode,
  persistent: Set<string>,
): void {
  let pointer: Node | null = oldParent.firstChild;
  let newChild = newParent.firstChild;
  while (newChild !== null) {
    const next = newChild.nextSibling;
    const match = findMatch(ctx, pointer, newChild, persistent);
    if (match === null) {
      // No match: insert a fresh node before the pointer, the only path new
      // content takes into the document.
      if (ctx.beforeNode(null, newChild) !== false) {
        oldParent.insertBefore(newChild, pointer);
      }
      newChild = next;
      continue;
    }
    if (match === pointer) {
      pointer = match.nextSibling;
    } else {
      ctx.move(oldParent, match, pointer);
    }
    applyMatch(ctx, match, newChild, persistent);
    newChild = next;
  }
  while (pointer !== null) {
    const after = pointer.nextSibling;
    discard(ctx, pointer);
    pointer = after;
  }
}

// A match always shares its new pair's tag, so an atomic element with a changed
// tag never matches: it is inserted fresh and the old one discarded, the honest
// connected/disconnected lifecycle for a custom element.
function applyMatch(
  ctx: Ctx,
  match: Node,
  newChild: Node,
  persistent: Set<string>,
): void {
  if (isElement(match) && isElement(newChild)) {
    morphNode(ctx, match, newChild, persistent);
    return;
  }
  // Text and comments match by nodeType, only nodeValue is synced.
  if (match.nodeValue !== newChild.nodeValue) {
    match.nodeValue = newChild.nodeValue;
  }
}

interface FocusSnapshot {
  el: Element | null;
  start: number | null;
  end: number | null;
  direction: string | null;
}

function snapshotFocus(doc: Document): FocusSnapshot {
  const el = doc.activeElement;
  let start: number | null = null;
  let end: number | null = null;
  let direction: string | null = null;
  // jsdom keeps activeElement at <body> at minimum, so the null branch is
  // unreachable under the runner and guards a real null in older agents.
  /* v8 ignore next */
  if (el !== null) {
    try {
      const field = el as HTMLInputElement;
      start = field.selectionStart;
      end = field.selectionEnd;
      direction = field.selectionDirection;
    } catch {
      // Some browsers throw reading selectionStart on email/number inputs.
    }
  }
  return { el, start, end, direction };
}

// Restore focus only on a real loss, a stray focus() is itself visible. A reused
// active node keeps focus natively since it never left the document.
function restoreFocus(doc: Document, snap: FocusSnapshot): void {
  const el = snap.el;
  if (el === null || el === doc.body) return;
  if (doc.activeElement === el || !el.isConnected) return;
  (el as HTMLElement).focus();
  if (snap.start !== null && snap.end !== null) {
    try {
      (el as HTMLInputElement).setSelectionRange(
        snap.start,
        snap.end,
        (snap.direction ?? undefined) as "forward" | "backward" | "none" | undefined,
      );
    } catch {
      // Not a text field with a settable selection range.
    }
  }
}

// Parse a string through an inert <template>. A non-string is already a parsed
// element or fragment.
function parseContent(
  target: Element,
  html: string | Element | DocumentFragment,
): Element | DocumentFragment {
  if (typeof html !== "string") return html;
  const template = target.ownerDocument.createElement("template");
  template.innerHTML = html;
  return template.content;
}

function firstElement(fragment: DocumentFragment): Element | null {
  for (const node of Array.from(fragment.childNodes)) {
    if (isElement(node)) return node;
  }
  return null;
}

/** Bring target up to html and return the resulting root, a new node when the root tag changed. */
export function morph(
  target: Element,
  html: string | Element | DocumentFragment,
  options: MorphOptions = {},
): Element {
  const content = parseContent(target, html);
  const mode = options.mode ?? "node";
  const dev = options.dev ?? false;
  const doc = target.ownerDocument;

  const newRoot =
    content instanceof DocumentFragment
      ? mode === "children"
        ? content
        : firstElement(content)
      : content;
  if (newRoot === null) return target;

  // One id-map across both trees plus a raw universe per side. Persistent ids are
  // present on both sides, the only ones that vote.
  const ids = new Map<Element, Set<string>>();
  const oldUniverse = new Set<string>();
  const newUniverse = new Set<string>();
  collectIds(target, ids, oldUniverse, dev);
  if (newRoot instanceof Element) {
    collectIds(newRoot, ids, newUniverse, dev);
  } else {
    for (const child of Array.from(newRoot.childNodes)) {
      if (isElement(child)) collectIds(child, ids, newUniverse, dev);
    }
  }
  const persistent = new Set<string>();
  for (const id of oldUniverse) {
    if (newUniverse.has(id)) persistent.add(id);
  }

  const ctx: Ctx = {
    ids,
    isDirty: options.isDirty ?? (() => false),
    isTouched: options.isTouched ?? (() => false),
    move: options.move ?? defaultMove,
    beforeNode: options.beforeNode ?? (() => undefined),
    afterNode: options.afterNode ?? (() => undefined),
    onDiscard: options.onDiscard ?? (() => undefined),
  };

  const snap = snapshotFocus(doc);
  let result: Element = target;

  if (mode === "children") {
    morphChildren(ctx, target, newRoot, persistent);
  } else {
    const root = newRoot as Element;
    if (target.tagName === root.tagName) {
      morphNode(ctx, target, root, persistent);
    } else {
      // Root tag changed: recreate the node so the result root is the new node in
      // the target's position.
      const parent = target.parentNode;
      if (parent !== null) {
        parent.insertBefore(root, target);
        fireRemoved(target);
        target.remove();
      }
      result = root;
    }
  }

  restoreFocus(doc, snap);
  return result;
}
