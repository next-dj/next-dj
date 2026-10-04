// Modal layers over the native <dialog> with accept/dismiss semantics. Targets
// resolve top layer down, so a zone inside the modal wins over the same-named
// page zone underneath. The native modality lives behind an injectable adapter.

import { defaultPopState } from "./adapters";
import { readHead, writeHead } from "./head";
import type { Head, HeadPatch } from "./head";
import { fireRemoved } from "./morph";
import type { Navigation } from "./navigation";
import {
  ATTR_ZONE,
  HEADER_ORIGIN,
  HEADER_ZONE,
  cssEscape,
  fire,
  pageKey,
  currentUrl as pageUrl,
} from "./protocol";

const LAYER_ATTR = "data-next-layer";
const ACCEPTED_ATTR = "data-next-accepted";
const BUSY_ATTR = "data-next-busy";

/** The detail passed to close, an accept result or a dismiss reason. */
export interface LayerCloseEvent {
  // The accept result, absent on a dismiss.
  result?: unknown;
  // The dismiss reason ("escape", "backdrop", "dialog", server text), absent on accept.
  reason?: string;
  dismiss?: boolean;
}

/** Ends the dialog from the runtime side without re-firing dismiss. */
export type DialogControl = () => void;

/** The native dialog modality behind a seam, open traps focus and wires dismiss. */
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
  // The opening page URL, captured at open time. Sent as X-Next-Origin so the
  // server resolves the host for an out-of-band render of its zones.
  host: string;
  // The head restored on close, kept current by a meta op of a page under the layer.
  head: Head;
  // Set once a meta op arrived for this layer's own page, so a meta op of a page
  // under it is applied on close instead of over the modal.
  titled: boolean;
  // The page key of the layer's own URL, absent for a layer with no body to fetch. It
  // scopes the body's zones and head even when the browser refused the push.
  pushedUrl?: string;
  // Set once the body envelope announced the URL, so a close announces its replace.
  committed: boolean;
  // The queue of the body fetch, aborted on remove so a late body is not applied.
  key: string;
  // Drops the held push, so a layer closed before its body reverts it unannounced.
  // Absent when the layer has no history entry of its own.
  drop?: (() => void) | undefined;
}

/** The seams createLayers needs, each defaulting to a platform adapter. */
export interface LayerDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  // The partial fetch, GETs the layer body and the host re-GET on accept.
  fetch: (request: {
    url: string;
    zone: string;
    queue?: string;
    headers?: Record<string, string>;
  }) => Promise<void>;
  // Abort a queued fetch, so a closed layer drops its body in flight.
  abort?: (key: string) => void;
  document?: Document;
  dialog?: DialogAdapter;
  // The navigation shared with the applier, which announces the push a layer holds.
  navigation: Navigation;
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
  /** The host page of the layer owning an element, absent outside every layer. */
  hostFor(el: Element): string | undefined;
  /** Record a new URL for an element's page, true when the address bar shows it. */
  rewrite(el: Element, href: string): boolean;
  /** Open a layer, building the dialog and zone container before the request. */
  open(opener: HTMLElement | null, href?: string, zone?: string): Promise<void>;
  /** Close the top layer, a result accepts and a dismiss rejects with a reason. */
  close(detail: LayerCloseEvent): void;
  /** Sync a page's head tags, a page under a titled layer shows them once it closes. */
  head(patch: HeadPatch, page?: string): void;
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
  const dialogAdapter = deps.dialog ?? nativeDialog();

  const abort = deps.abort ?? ((): void => undefined);
  const popstate = deps.popstate ?? defaultPopState();
  const stack: Layer[] = [];
  let serial = 0;
  let toastHost: HTMLElement | null = null;
  let detach: (() => void) | null = null;

  function topLayer(): Layer | undefined {
    return stack[stack.length - 1];
  }

  // Top layer first, the order every lookup resolves in.
  function topDown(): Layer[] {
    return Array.from(stack).reverse();
  }

  // The topmost layer holding an element, absent for one on the base page.
  function layerOf(el: Element): Layer | undefined {
    return topDown().find((layer) => layer.root.contains(el));
  }

  function currentUrl(): string {
    return pageUrl(doc);
  }

  // A page-scoped lookup searches only that page's subtree, an unmatched page
  // (its layer closed mid-flight) degrades to the top-down walk.
  function resolveZone(name: string, root: ParentNode, page?: string): Element | null {
    const selector = `[${ATTR_ZONE}="${cssEscape(name)}"]`;
    if (page !== undefined) {
      const owner = topDown().find((layer) => layer.pushedUrl === page);
      if (owner !== undefined) return findIn(owner.root, selector);
      const bottom = stack[0];
      const base = bottom === undefined ? currentUrl() : bottom.host;
      if (page === base) return findOutsideLayers(selector, root);
    }
    return resolveSelector(selector, root);
  }

  // Top-down walk, the topmost layer holding a match wins and the document last.
  function resolveSelector(selector: string, root: ParentNode): Element | null {
    for (const layer of topDown()) {
      const found = findIn(layer.root, selector);
      if (found !== null) return found;
    }
    return root.querySelector(selector);
  }

  // The layer's container carries the zone, so it matches too, not just descendants.
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
    // A seeded layer pushed no URL, its zones belong to the opening page.
    const layer = layerOf(el);
    if (layer !== undefined) return layer.pushedUrl ?? layer.host;
    const bottom = stack[0];
    return bottom === undefined ? currentUrl() : bottom.host;
  }

  // The opening page of the layer an element is in. A mutation fired there sends it as
  // X-Next-Origin, so the server resolves a foreign zone against the host.
  function hostFor(el: Element): string | undefined {
    return layerOf(el)?.host;
  }

  // A filter inside a layer updates the layer's own URL with the address bar, so a
  // close still replaces it with the host. A layer that wrote no URL, and the base
  // page under one that did, leave the address bar unchanged.
  function rewrite(el: Element, href: string): boolean {
    const layer = layerOf(el);
    const page = layer?.pushedUrl ?? stack[0]?.host ?? currentUrl();
    if (layer?.committed === false || page !== currentUrl()) return false;
    if (layer !== undefined) layer.pushedUrl = href;
    return true;
  }

  function busy(initiator: Element | null, target: Element | null): () => void {
    const marked = [initiator, target].filter((el): el is Element => el !== null);
    for (const el of marked) {
      el.toggleAttribute(BUSY_ATTR, true);
      el.setAttribute("aria-busy", "true");
    }
    return () => {
      for (const el of marked) {
        el.toggleAttribute(BUSY_ATTR, false);
        el.removeAttribute("aria-busy");
      }
    };
  }

  async function open(
    opener: HTMLElement | null,
    href?: string,
    zone?: string,
  ): Promise<void> {
    // A second open for a busy opener is dropped here, so neither path stacks a modal.
    if (opener?.hasAttribute(BUSY_ATTR) === true) return;
    const dialog = doc.createElement("dialog");
    dialog.setAttribute("data-next-dialog", "");
    const root = doc.createElement("div");
    // A seeded zone names the container before the request, so the first morph
    // resolves the target normally. An empty open leaves the shell unnamed.
    if (zone !== undefined) root.setAttribute(ATTR_ZONE, zone);
    dialog.append(root);
    doc.body.append(dialog);
    const returnFocus = doc.activeElement;
    // The opening page, captured before the request so a later navigation cannot
    // move it. The query is kept, so a filtered host comes back with its filters.
    const host = currentUrl();
    // A browser dismiss gesture (Esc, backdrop, dialog form) reaches the same
    // close path as a server dismiss, so the reason flows through one channel.
    const close = dialogAdapter.open(dialog, (reason) => dismissFrom(dialog, reason));
    serial += 1;
    const layer: Layer = {
      dialog,
      root,
      opener,
      close,
      returnFocus,
      host,
      head: readHead(doc),
      titled: false,
      committed: false,
      key: `layer:${serial}`,
    };
    stack.push(layer);
    // Both ends go busy before the request, and the opener's flag is what the
    // double-click guard above reads. Nothing awaits before this line.
    const release = busy(opener, root);
    // A body fetch needs both the URL and the zone. A zone-only or empty open
    // shows a bare modal for a later patch to seed, with no history entry.
    const seeded = href !== undefined && zone !== undefined;
    try {
      if (seeded) {
        // The layer's own URL makes the modal shareable, and pushing it now lets Back
        // close the layer while the body loads. The body envelope announces it, and a
        // layer closed before that reverts it unannounced.
        const owner = pageKey(href, doc);
        layer.pushedUrl = owner;
        layer.drop = deps.navigation.hold(owner, href, () => (layer.committed = true));
      }
      emit("partial:layer-opened", { opener });
      if (seeded) {
        await deps.fetch({
          url: href,
          zone,
          queue: layer.key,
          headers: { [HEADER_ZONE]: zone, [HEADER_ORIGIN]: host },
        });
      }
    } catch (e) {
      remove(layer);
      throw e;
    } finally {
      release();
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
      // under the modal morphs. The host is sent as X-Next-Origin for the server.
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
    // remove can run twice for one layer. A dismiss gesture removes it while the body
    // fetch is in flight, and the rejection of open calls it again, as a no-op.
    if (index === -1) return;
    const commit = deps.navigation.begin();
    try {
      stack.splice(index, 1);
      layer.drop?.();
      abort(layer.key);
      layer.close();
      // Fire next:removed on the detaching root so an island inside the modal
      // unmounts before the subtree leaves the document, the apply verbs' contract.
      fireRemoved(layer.dialog);
      layer.dialog.remove();
      if (layer.returnFocus instanceof HTMLElement) layer.returnFocus.focus();
      // After a programmatic close the URL is still the pushed one, so it is replaced
      // with the host. After Back the browser has already changed it.
      if (layer.committed && currentUrl() === layer.pushedUrl) {
        commit.write({ href: layer.host, action: "replace" });
      }
      // The saved head passes to the layer above, or is written when none is above.
      const above = stack[index];
      if (above !== undefined) above.head = layer.head;
      else writeHead(doc, layer.head);
    } finally {
      commit.end();
    }
  }

  // A page's tags update the saved head of each layer above it up to the first titled
  // one, and are written to the document only when no titled layer covers the page.
  function head(patch: HeadPatch, page?: string): void {
    const above = firstLayerAbove(page);
    if (above === -1) return;
    const owner = page === undefined ? stack[stack.length - 1] : stack[above - 1];
    if (owner !== undefined) owner.titled = true;
    for (const layer of stack.slice(above)) {
      layer.head = { ...layer.head, ...patch };
      if (layer.titled) return;
    }
    writeHead(doc, patch);
  }

  // The index of the first layer above a page, or -1 for a page no longer on the
  // stack. Its layer closed while the envelope was in flight, so its tags are stale.
  function firstLayerAbove(page: string | undefined): number {
    if (page === undefined) return stack.length;
    const owner = topDown().find((layer) => layer.pushedUrl === page);
    if (owner !== undefined) return stack.indexOf(owner) + 1;
    return page === (stack[0]?.host ?? currentUrl()) ? 0 : -1;
  }

  // Back past the URL of the topmost layer with a history entry closes that layer and
  // the layers above it that have none. The pass repeats while the bar is off the next
  // such URL, so a jump back over several entries closes every layer it skipped. Zones
  // and history stay as they are.
  function onPopstate(): void {
    deps.navigation.popped(() => {
      for (;;) {
        const layers = topDown();
        const anchor = layers.find((layer) => layer.drop !== undefined);
        if (anchor === undefined || anchor.pushedUrl === currentUrl()) return;
        for (const layer of layers) {
          dismissFrom(layer.dialog, "popstate");
          if (layer === anchor) break;
        }
      }
    });
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
    fire(doc, deps.dispatch, event, detail);
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
    // One controller owns every listener this install binds, so no teardown can
    // drift from the flags its addEventListener used.
    const controller = new AbortController();
    target.addEventListener("click", onClick, { signal: controller.signal });
    // The popstate teardown runs on the same signal, so one abort removes both.
    const stopPopstate = popstate.listen(onPopstate);
    controller.signal.addEventListener("abort", stopPopstate, { once: true });
    detach = () => controller.abort();
    return detach;
  }

  return {
    resolveZone,
    resolveSelector,
    urlFor,
    hostFor,
    rewrite,
    open,
    close,
    head,
    toast,
    size: () => stack.length,
    busy,
    install,
    _reset() {
      for (const layer of topDown()) remove(layer);
      if (toastHost !== null) {
        toastHost.remove();
        toastHost = null;
      }
      // Drop the click and popstate listeners install bound, so a reset leaves none.
      if (detach !== null) {
        detach();
        detach = null;
      }
    },
  };
}

/** The default modality over the native <dialog>, showModal traps focus for us. */
export function nativeDialog(): DialogAdapter {
  return { open: openNativeDialog };
}

// Kept beside the layers rather than among the platform adapters, since the dismiss
// gestures are runtime logic. Only showModal and close belong to the browser.
function openNativeDialog(
  dialog: HTMLDialogElement,
  onDismiss: (reason: string) => void,
): DialogControl {
  const controller = new AbortController();
  const signal = controller.signal;
  dialog.addEventListener(
    "cancel",
    (event) => {
      event.preventDefault();
      onDismiss("escape");
    },
    { signal },
  );
  // <form method="dialog"> closes with returnValue as the reason. A runtime close
  // aborts these listeners first, so it never comes back through here.
  dialog.addEventListener("close", () => onDismiss(dialog.returnValue || "dialog"), {
    signal,
  });
  // A click whose target is the dialog itself hit the backdrop, since children
  // receive the inner clicks, so element identity is the hit test.
  dialog.addEventListener(
    "click",
    (event) => {
      if (event.target === dialog) onDismiss("backdrop");
    },
    { signal },
  );
  dialog.showModal();
  (dialog.querySelector<HTMLElement>("[autofocus]") ?? dialog).focus();
  return (): void => {
    controller.abort();
    if (dialog.open) dialog.close();
  };
}
