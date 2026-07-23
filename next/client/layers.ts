// Modal layers over the native <dialog> with accept/dismiss semantics. Targets
// resolve top layer down, so a zone inside the modal wins over the same-named
// page zone underneath. The native modality lives behind an injectable adapter.

import { defaultDialog, defaultHistory, defaultPopState } from "./adapters";
import type { HistoryAdapter } from "./apply";
import { fireRemoved } from "./morph";
import {
  HEADER_ORIGIN,
  HEADER_ZONE,
  cssEscape,
  currentUrl as pageUrl,
} from "./protocol";

const LAYER_ATTR = "data-next-layer";
const ACCEPTED_ATTR = "data-next-accepted";
const BUSY_ATTR = "data-next-busy";

/** The detail passed to close, an accept result or a dismiss reason. */
export interface LayerCloseEvent {
  // The accept result, absent on a dismiss.
  result?: unknown;
  // The dismiss reason ("escape", "backdrop", "dialog", or server text), absent on accept.
  reason?: string;
  dismiss?: boolean;
}

/** Ends the dialog from the runtime side without re-firing dismiss. */
export type DialogControl = () => void;

/** The native dialog modality behind a seam, open traps focus and wires the dismiss gestures. */
export interface DialogAdapter {
  open(dialog: HTMLDialogElement, onDismiss: (reason: string) => void): DialogControl;
}

/** The popstate seam, listen registers the Back handler and returns its teardown. */
export interface PopStateAdapter {
  listen(handler: () => void): () => void;
}

interface Layer {
  dialog: HTMLDialogElement;
  root: HTMLElement;
  // The opener link, or null for a server-initiated open. Takes focus on close.
  opener: HTMLElement | null;
  close: DialogControl;
  returnFocus: Element | null;
  // The opening page URL, captured at open time. Rides X-Next-Origin so the
  // server resolves the host for an out-of-band render of its zones.
  host: string;
  // The honest URL pushed on open, absent for a layer that never touched
  // history. A programmatic close replaces it back to the host.
  pushedUrl?: string;
}

/** The seams createLayers needs, each defaulting to a platform adapter. */
export interface LayerDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  // The partial fetch, GETs the layer body and the host re-GET on accept.
  fetch: (request: {
    url: string;
    zone: string;
    headers?: Record<string, string>;
  }) => Promise<void>;
  document?: Document;
  dialog?: DialogAdapter;
  // The history seam shared with the applier, so open pushes and close replaces.
  history?: HistoryAdapter;
  // The Back-gesture seam, a popstate past the top layer's pushed URL closes it.
  popstate?: PopStateAdapter;
}

export interface LayerStack {
  /** Resolve a zone, scoped to the page that fetched it or the top-down walk. */
  resolveZone(name: string, root: ParentNode, page?: string): Element | null;
  /** Resolve any selector with the top-down walk, a modal match wins. */
  resolveSelector(selector: string, root: ParentNode): Element | null;
  /** The URL of the page that owns an element, a poll tick GETs this not the bar. */
  urlFor(el: Element): string;
  /** Open a layer, building the dialog and zone container before the request. */
  open(opener: HTMLElement | null, href?: string, zone?: string): Promise<void>;
  /** Close the top layer, a result accepts and a dismiss rejects with a reason. */
  close(detail: LayerCloseEvent): void;
  /** Append a toast as textContent, never parsed as HTML. */
  toast(text: string, variant: string): void;
  /** The number of open layers. */
  size(): number;
  /** Mark the initiator and target busy for a request, returning the releaser. */
  busy(initiator: Element | null, target: Element | null): () => void;
  /** Install the delegated click handler, returning its teardown. */
  install(doc: Document): () => void;
  _reset(): void;
}

/** The layer stack over the seams in deps, defaulting each to a platform adapter. */
export function createLayers(deps: LayerDeps): LayerStack {
  const doc = deps.document ?? document;
  const dialogAdapter = deps.dialog ?? defaultDialog();
  const history = deps.history ?? defaultHistory();
  const popstate = deps.popstate ?? defaultPopState();
  const stack: Layer[] = [];
  let toastHost: HTMLElement | null = null;
  let detach: (() => void) | null = null;
  let popstateDetach: (() => void) | null = null;

  function topLayer(): Layer | undefined {
    return stack[stack.length - 1];
  }

  function currentUrl(): string {
    return pageUrl(doc);
  }

  // A page-scoped lookup searches only that page's subtree, an unmatched page
  // (its layer closed mid-flight) degrades to the top-down walk.
  function resolveZone(name: string, root: ParentNode, page?: string): Element | null {
    const selector = `[data-next-zone="${cssEscape(name)}"]`;
    if (page !== undefined) {
      for (const layer of Array.from(stack).reverse()) {
        if (layer.pushedUrl === page) return findIn(layer.root, selector);
      }
      const bottom = stack[0];
      const base = bottom === undefined ? currentUrl() : bottom.host;
      if (page === base) return findOutsideLayers(selector, root);
    }
    return resolveSelector(selector, root);
  }

  // Top-down walk, the topmost layer holding a match wins and the document last.
  function resolveSelector(selector: string, root: ParentNode): Element | null {
    for (const layer of Array.from(stack).reverse()) {
      const found = findIn(layer.root, selector);
      if (found !== null) return found;
    }
    return root.querySelector(selector);
  }

  // The layer's own container carries the zone, so it is matched directly, not
  // only its descendants.
  function findIn(container: HTMLElement, selector: string): Element | null {
    if (container.matches(selector)) return container;
    return container.querySelector(selector);
  }

  // Dialogs live in the body, so skip matches inside any dialog subtree.
  function findOutsideLayers(selector: string, root: ParentNode): Element | null {
    for (const el of Array.from(root.querySelectorAll(selector))) {
      if (!stack.some((layer) => layer.dialog.contains(el))) return el;
    }
    return null;
  }

  // An element inside a layer belongs to its pushed URL, one in no layer to the
  // base page (the bottom layer's host while any is open, else the current URL).
  function urlFor(el: Element): string {
    for (const layer of Array.from(stack).reverse()) {
      if (layer.root.contains(el)) {
        // A seeded layer pushed no URL, its zones belong to the opening page.
        return layer.pushedUrl ?? layer.host;
      }
    }
    const bottom = stack[0];
    return bottom === undefined ? currentUrl() : bottom.host;
  }

  function busy(initiator: Element | null, target: Element | null): () => void {
    const marked: Element[] = [];
    for (const el of [initiator, target]) {
      if (el === null) continue;
      el.setAttribute(BUSY_ATTR, "");
      el.setAttribute("aria-busy", "true");
      marked.push(el);
    }
    return () => {
      for (const el of marked) {
        el.removeAttribute(BUSY_ATTR);
        el.removeAttribute("aria-busy");
      }
    };
  }

  async function open(
    opener: HTMLElement | null,
    href?: string,
    zone?: string,
  ): Promise<void> {
    // Mark the opener busy before any mutation, the double-click guard reads it.
    // A second open for a busy opener is dropped here, so neither path stacks a modal.
    if (opener !== null) {
      if (opener.hasAttribute(BUSY_ATTR)) return;
      opener.setAttribute(BUSY_ATTR, "");
      opener.setAttribute("aria-busy", "true");
    }
    const dialog = doc.createElement("dialog");
    dialog.setAttribute("data-next-dialog", "");
    const root = doc.createElement("div");
    // A seeded zone names the container before the request, so the first morph
    // resolves the target normally. An empty open leaves the shell unnamed.
    if (zone !== undefined) root.setAttribute("data-next-zone", zone);
    dialog.append(root);
    doc.body.append(dialog);
    const returnFocus = doc.activeElement;
    // The opening page, captured before the request so a later navigation cannot
    // move it. The query is kept, so a filtered host comes back with its filters.
    const host = currentUrl();
    // A browser dismiss gesture (Esc, backdrop, dialog form) reaches the same
    // close path as a server dismiss, so the reason flows through one channel.
    const close = dialogAdapter.open(dialog, (reason) => dismissFrom(dialog, reason));
    const layer: Layer = { dialog, root, opener, close, returnFocus, host };
    stack.push(layer);
    // The opener already carries busy, so only the target zone is marked here.
    const release = busy(null, root);
    // A body fetch needs both the URL and the zone. A zone-only or empty open
    // shows a bare modal for a later patch to seed, with no history entry.
    const seeded = href !== undefined && zone !== undefined;
    try {
      if (seeded) {
        // Push the honest URL so the modal is shareable and Back closes it. Inside
        // the try because pushState can throw (Safari rate limit) and must unwind.
        history.push(href);
        layer.pushedUrl = currentUrl();
      }
      emit("partial:layer-opened", { opener });
      if (seeded) {
        await deps.fetch({
          url: href,
          zone,
          headers: { [HEADER_ZONE]: zone, [HEADER_ORIGIN]: host },
        });
      }
    } catch (e) {
      // remove rolls the URL back to the host only when the push actually
      // moved it (pushedUrl set), so a failed push rolls back nothing.
      remove(layer);
      throw e;
    } finally {
      release();
      if (opener !== null) {
        opener.removeAttribute(BUSY_ATTR);
        opener.removeAttribute("aria-busy");
      }
    }
  }

  function close(detail: LayerCloseEvent): void {
    const layer = topLayer();
    if (layer === undefined) return;
    if (detail.dismiss === true) {
      dismissFrom(layer.dialog, detail.reason ?? "dismissed");
      return;
    }
    remove(layer);
    emit("partial:layer-accepted", { result: detail.result });
    const accepted = layer.opener?.getAttribute(ACCEPTED_ATTR);
    if (accepted) {
      // On accept the host page is re-GET for the opener's named zone, so the list
      // under the modal morphs. The host rides X-Next-Origin so the server resolves it.
      void deps.fetch({
        url: layer.host,
        zone: accepted,
        headers: { [HEADER_ZONE]: accepted, [HEADER_ORIGIN]: layer.host },
      });
    }
  }

  // A browser dismiss gesture finds its own layer by dialog and rejects it.
  function dismissFrom(dialog: HTMLDialogElement, reason: string): void {
    const layer = stack.find((entry) => entry.dialog === dialog);
    if (layer === undefined) return;
    remove(layer);
    emit("partial:layer-dismissed", { reason });
  }

  // Splice the layer out, end its dialog, and return focus to the opener.
  function remove(layer: Layer): void {
    const index = stack.indexOf(layer);
    // remove can land twice on one layer: a dismiss gesture tears it down while
    // open's fetch is in flight, then the reject re-enters here. The early return
    // makes the second call a no-op so nothing runs twice.
    if (index === -1) return;
    stack.splice(index, 1);
    layer.close();
    // Fire next:removed on the detaching root so an island inside the modal
    // unmounts before the subtree leaves the document, the apply verbs' contract.
    fireRemoved(layer.dialog);
    layer.dialog.remove();
    if (layer.returnFocus instanceof HTMLElement) layer.returnFocus.focus();
    // A programmatic close still sits on the pushed URL, so replace it back to the
    // host. A Back-driven close already moved the URL, so the guard skips it.
    if (layer.pushedUrl !== undefined && currentUrl() === layer.pushedUrl) {
      history.replace(layer.host);
    }
  }

  // Back past the topmost pushed URL closes that layer and the bare layers above
  // it. Never restores zones or writes history, short of a client router.
  function onPopstate(): void {
    const layers = Array.from(stack).reverse();
    const anchor = layers.find((layer) => layer.pushedUrl !== undefined);
    if (anchor === undefined || anchor.pushedUrl === currentUrl()) return;
    for (const layer of layers) {
      dismissFrom(layer.dialog, "popstate");
      if (layer === anchor) return;
    }
  }

  function toast(text: string, variant: string): void {
    const host = ensureToastHost();
    const item = doc.createElement("div");
    item.setAttribute("data-next-toast", variant);
    // textContent, never innerHTML, so a server toast string cannot smuggle markup.
    item.textContent = text;
    host.append(item);
    emit("next:toast", { text, variant });
  }

  function ensureToastHost(): HTMLElement {
    if (toastHost?.isConnected) return toastHost;
    const host = doc.createElement("div");
    host.setAttribute("data-next-toasts", "");
    host.setAttribute("aria-live", "polite");
    doc.body.append(host);
    toastHost = host;
    return host;
  }

  function emit(event: string, detail: Record<string, unknown>): void {
    doc.dispatchEvent(new CustomEvent(event, { detail }));
    deps.dispatch(event, detail);
  }

  function onClick(event: Event): void {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const opener = target.closest(`[${LAYER_ATTR}]`);
    if (!(opener instanceof HTMLElement)) return;
    const zone = opener.getAttribute(LAYER_ATTR);
    const href = opener.getAttribute("href");
    // No zone or no href is a plain navigation, the no-JS path is untouched.
    if (zone === null || zone === "" || href === null || href === "") return;
    event.preventDefault();
    // A second click while the first is in flight is dropped, no double dialog.
    if (opener.hasAttribute(BUSY_ATTR)) return;
    void open(opener, href, zone);
  }

  function install(target: Document): () => void {
    if (detach !== null) detach();
    target.addEventListener("click", onClick);
    popstateDetach = popstate.listen(onPopstate);
    detach = () => {
      target.removeEventListener("click", onClick);
      if (popstateDetach !== null) popstateDetach();
      popstateDetach = null;
    };
    return detach;
  }

  return {
    resolveZone,
    resolveSelector,
    urlFor,
    open,
    close,
    toast,
    size: () => stack.length,
    busy,
    install,
    _reset() {
      for (const layer of [...stack]) remove(layer);
      if (toastHost !== null) {
        toastHost.remove();
        toastHost = null;
      }
      // Also drop the delegated click and popstate listeners install bound, so a
      // reset leaves nothing on the document or window.
      if (detach !== null) {
        detach();
        detach = null;
      }
    },
  };
}
