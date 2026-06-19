// The morph engine: an old target subtree is brought up to a new HTML string by
// reusing the live nodes that already match, so a submit keeps focus, caret,
// typed values, open `<details>`, playing media, and scroll. The match runs on
// id-sets (a wrapper without an id still matches through the ids of its
// children), then a left-to-right child walk reuses, moves, creates, or discards
// nodes. Live properties (value/checked/selected/open) split from their
// attribute twins and honour an injected dirty predicate. The server authors the
// target, so the engine never reaches outside it.

import { defaultMove } from "./adapters";

export type MorphMode = "node" | "children";

// Relocate a live node before a reference node. The default moveBefore-or-
// insertBefore adapter lives in adapters.ts, callers inject a mock in tests.
export type Move = (parent: ParentNode, node: Node, before: Node | null) => void;

export interface MorphOptions {
  // Morph the target itself or only its children. Default "node".
  mode?: MorphMode;
  // A field carrying local input made after the request snapshot. Default
  // () => false. A dirty field keeps its live value untouched.
  isDirty?: (field: Element) => boolean;
  // The move adapter. Default moveBefore with an insertBefore fallback. It is a
  // DI-seam so the native branch is exercised through a mock in jsdom.
  move?: Move;
  // Before a pair of nodes. false skips the whole pair, the node and subtree.
  beforeNode?: (oldNode: Node | null, newNode: Node) => boolean | void;
  // After a pair has morphed.
  afterNode?: (oldNode: Node, newNode: Node) => void;
  // An old node found no pair and is about to be discarded. false keeps it.
  onDiscard?: (node: Node) => boolean | void;
}

interface Ctx {
  ids: Map<Element, Set<string>>;
  isDirty: (field: Element) => boolean;
  move: Move;
  beforeNode: (oldNode: Node | null, newNode: Node) => boolean | void;
  afterNode: (oldNode: Node, newNode: Node) => void;
  onDiscard: (node: Node) => boolean | void;
}

// Read an id strictly through getAttribute: the `id` property is subject to DOM
// clobbering, an `<input name="id">` inside a form shadows form.id.
function readId(el: Element): string | null {
  const key = el.getAttribute("data-next-key");
  if (key !== null) {
    if (el.getAttribute("id") !== null) {
      // dev warn: a node carries either a key or an id, not both.
      console.warn("[next.morph] data-next-key and id on one node", el);
    }
    return key;
  }
  return el.getAttribute("id");
}

// Build id-sets for one tree in a single querySelectorAll pass. Each element's
// own id bubbles up into every ancestor's set, so a wrapper without an id still
// votes through its descendants' ids. Every collected id also lands in the raw
// universe, the source of the persistent (both-sides) intersection.
function collectIds(
  root: Element,
  into: Map<Element, Set<string>>,
  universe: Set<string>,
): void {
  consume(root, root, into, universe);
  const tagged = root.querySelectorAll<Element>("[id],[data-next-key]");
  for (const el of Array.from(tagged)) {
    consume(el, root, into, universe);
  }
}

function consume(
  el: Element,
  root: Element,
  into: Map<Element, Set<string>>,
  universe: Set<string>,
): void {
  const id = readId(el);
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

// Persistent ids are those present in both trees. An id on one side only owns no
// match and must not vote, a match on it would be a match with nothing.
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

// A node is hard-matchable when both sides are elements with the same tag and a
// non-empty persistent id-set intersection.
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

// A soft match is a same nodeType, same tag pair with empty id-sets. Elements
// carrying a persistent id are reserved for a future hard match.
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

// Find a match for one new child among old siblings from the pointer. A hard
// match is searched along the whole scan, then a soft match is taken only at the
// pointer. A pointer carrying a persistent id is reserved for a hard match the
// scan would have found, so isSoftMatch refuses it and the new node is inserted.
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

// An element with a hyphen in its tag or with a shadowRoot is an atomic unit: on
// a tag match only its attributes sync, its children are not morphed, the engine
// never enters the shadow root. Declarative shadow DOM in the new markup is part
// of this atomic rule.
function isAtomic(el: Element): boolean {
  return el.tagName.includes("-") || el.shadowRoot != null;
}

// A keep node is left untouched so a foreign root mounted into it survives a
// morph. With an id the child walk pairs it by hard match, without one it pairs
// by position, so a framework root the server renders with no stable id is
// preserved all the same.
function isKept(el: Element): boolean {
  return el.hasAttribute("data-next-keep");
}

function emit(target: Element, name: string, detail: Record<string, unknown>): boolean {
  const event = new CustomEvent(name, { bubbles: true, cancelable: true, detail });
  target.dispatchEvent(event);
  return !event.defaultPrevented;
}

// Signal an element is about to detach so an adapter root mounted into it can
// unmount before the node leaves the document. Fired on the detaching root
// itself, bubbles to the document where the delegated listener lives, and is
// not cancelable: the detach is already decided. The complement of next:mounted.
export function fireRemoved(node: Element): void {
  node.dispatchEvent(new CustomEvent("next:removed", { bubbles: true }));
}

function discard(ctx: Ctx, node: Node): void {
  if (ctx.onDiscard(node) === false) return;
  if (isElement(node)) fireRemoved(node);
  (node as ChildNode).remove();
}

// Sync the live value/checked/selected and the open state. The attribute twin is
// synced by the attribute pass (server default), the live property is set only
// when the field is neither active nor dirty.
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
    // Setting defaultSelected reflects the selected attribute and can perturb
    // the live selection, so under a dirty select the live selected is pinned.
    const wasSelected = o.selected;
    o.defaultSelected = n.defaultSelected;
    o.selected = locked ? wasSelected : n.selected;
  }
}

// Some attributes are owned by syncLive, not the generic pass: the value, checked
// and selected twins are set there with their dirty rule, a toggled <details> is
// dirty and keeps its open state, and a <dialog>'s open belongs to the layer
// surface, the morph boundary.
function skipAttribute(ctx: Ctx, el: Element, name: string): boolean {
  const tag = el.tagName;
  if (name === "value" || name === "checked") return tag === "INPUT";
  if (name === "selected") return tag === "OPTION";
  if (name === "open") {
    if (tag === "DIALOG") return true;
    return tag === "DETAILS" && ctx.isDirty(el);
  }
  return false;
}

function isActiveOrDirty(ctx: Ctx, el: Element): boolean {
  return el.ownerDocument.activeElement === el || ctx.isDirty(el);
}

// Three-phase attribute sync: add the missing, update the changed, remove the
// extra. Matching attributes are left alone so no extra mutation restarts a CSS
// animation or wakes a MutationObserver. Each change is cancelable.
function syncAttributes(ctx: Ctx, oldEl: Element, newEl: Element): void {
  const newAttrs = newEl.attributes;
  for (let i = 0; i < newAttrs.length; i++) {
    const attr = newAttrs[i];
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
  const oldAttrs = oldEl.attributes;
  for (let i = oldAttrs.length - 1; i >= 0; i--) {
    const name = oldAttrs[i].name;
    if (newEl.hasAttribute(name) || skipAttribute(ctx, oldEl, name)) continue;
    if (emit(oldEl, "next:morph-attribute", { name, mutationType: "remove" })) {
      oldEl.removeAttribute(name);
    }
  }
}

// Morph a single pair. data-next-keep, custom-element atomicity, the attribute
// pass, the live-property pass, then recursion. The pair is reused, never the
// new node grafted in.
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

// Walk new children left to right with an insertion pointer into old children.
// hard match -> soft match -> create. A match at the pointer is morphed in place
// and the pointer advances. A match found further on is moved before the pointer
// and morphed, the pointer stays so the skipped old nodes are revisited by later
// new children or swept at the end. The trailing old children are discarded once
// the new children run out.
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
      // No match: insert a fresh node before the pointer. The only path new
      // content takes into the document, already script-neutralised upstream.
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
  // Discard the trailing old children.
  while (pointer !== null) {
    const after = pointer.nextSibling;
    discard(ctx, pointer);
    pointer = after;
  }
}

// A match always shares the tag of its new pair, so an atomic element with a
// changed tag never matches: it is inserted fresh and the old one discarded,
// which is the honest connected/disconnected lifecycle for a custom element.
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
  // jsdom keeps document.activeElement at <body> at minimum so the null branch
  // is unreachable under the test runner, it guards a real null in older agents.
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

// Restore focus only on a real loss: a stray focus() is itself visible. The
// caret restore runs in try/catch. Focus never participates in matching, a
// reused active node keeps focus natively because it never left the document.
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
        (snap.direction as "forward" | "backward" | "none") ?? undefined,
      );
    } catch {
      // Not a text field with a settable selection range.
    }
  }
}

// Parse a string through an inert <template>. Full documents for extract are
// parsed by the applier and handed in already cut out as an element.
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

// Bring target up to html. Returns the resulting root, which may differ from
// target by reference when the root tag changed and the root was recreated.
export function morph(
  target: Element,
  html: string | Element | DocumentFragment,
  options: MorphOptions = {},
): Element {
  const content = parseContent(target, html);
  const mode = options.mode ?? "node";
  const doc = target.ownerDocument;

  const newRoot =
    content instanceof DocumentFragment
      ? mode === "children"
        ? content
        : firstElement(content)
      : content;
  if (newRoot === null) return target;

  // One id-map across both trees, plus a raw id universe per side. Persistent
  // ids are those present on both sides, the only ones that vote in matching.
  const ids = new Map<Element, Set<string>>();
  const oldUniverse = new Set<string>();
  const newUniverse = new Set<string>();
  collectIds(target, ids, oldUniverse);
  if (newRoot instanceof Element) {
    collectIds(newRoot, ids, newUniverse);
  } else {
    for (const child of Array.from(newRoot.childNodes)) {
      if (isElement(child)) collectIds(child, ids, newUniverse);
    }
  }
  const persistent = new Set<string>();
  for (const id of oldUniverse) {
    if (newUniverse.has(id)) persistent.add(id);
  }

  const ctx: Ctx = {
    ids,
    isDirty: options.isDirty ?? (() => false),
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
      // Root tag changed: recreate the node so the result root is the new node
      // and it sits on the target position.
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
