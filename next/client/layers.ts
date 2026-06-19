// Modal layers over the native <dialog> with accept/dismiss semantics. A layer
// opens from a click on <a data-next-layer="name">: the runtime builds the
// dialog and a zone container named after the link before any request, so the
// first morph resolves the target by the ordinary path. Targets resolve from
// the top layer down, so a master zone inside the modal is found before the
// same-named page zone underneath it. The layer content never knows it is in a
// layer.
//
// The native modality (showModal, the focus trap, dismiss wiring for Esc,
// backdrop, and <form method="dialog">) lives behind an injectable adapter, so
// the surface here is exercised in jsdom while the untestable browser globals
// stay in the excluded adapters file.

import { defaultDialog, defaultHistory, defaultPopState } from "./adapters";
import type { HistoryAdapter } from "./apply";
import { HEADER_ORIGIN, HEADER_ZONE, cssEscape } from "./protocol";

const LAYER_ATTR = "data-next-layer";
const ACCEPTED_ATTR = "data-next-accepted";
const BUSY_ATTR = "data-next-busy";

export interface LayerCloseEvent {
  // The result of an accept close, or undefined for a dismiss.
  result?: unknown;
  // The reason of a dismiss close: "dialog" form, "escape", "backdrop", or a
  // server-authored string. Absent on accept.
  reason?: string;
  dismiss?: boolean;
}

// The native dialog modality, behind a seam the harness overrides. open shows
// the modal, traps focus, and wires the browser dismiss gestures (Esc,
// backdrop, <form method="dialog">) to the callback. It returns the close that
// ends the dialog from the runtime side without re-firing dismiss.
export type DialogControl = () => void;

export interface DialogAdapter {
  open(dialog: HTMLDialogElement, onDismiss: (reason: string) => void): DialogControl;
}

// The popstate seam for the intercepting modal lifecycle. listen registers the
// Back handler and returns its teardown, so the browser global stays in the
// excluded adapters file while tests invoke the handler through a mock.
export interface PopStateAdapter {
  listen(handler: () => void): () => void;
}

interface Layer {
  dialog: HTMLDialogElement;
  root: HTMLElement;
  // The link that opened the layer, or null for a server-initiated open. It
  // carries data-next-accepted and receives focus on close.
  opener: HTMLElement | null;
  close: DialogControl;
  // The element to return focus to once the layer closes.
  returnFocus: Element | null;
  // The path of the page that opened the layer, captured at open time. It rides
  // X-Next-Origin on the layer's requests so the server resolves the host for a
  // page-addressed out-of-band render of its zones, rather than falling back to
  // the step page the form lives on.
  host: string;
  // The honest URL pushed when the layer opened, so the modal is shareable and
  // refresh-resolves to the standalone page. Absent for a layer that never
  // touched history. A programmatic close replaces it back to the host, and the
  // popstate handler closes the layer once Back moves past it.
  pushedUrl?: string;
}

export interface LayerDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  // The partial fetch, used to GET the layer body and the host re-GET on
  // accept. The layer layer never builds its own fetch.
  fetch: (request: {
    url: string;
    zone: string;
    headers?: Record<string, string>;
  }) => Promise<void>;
  document?: Document;
  dialog?: DialogAdapter;
  // The history seam, shared with the applier, so an opening layer pushes its
  // honest URL and a programmatic close replaces it back to the host.
  history?: HistoryAdapter;
  // The Back-gesture seam. A popstate whose URL no longer matches the top
  // layer's pushed URL closes that layer.
  popstate?: PopStateAdapter;
}

export interface LayerStack {
  // Resolve a zone from the top layer down, then the page root. The applier
  // calls this for every zone-addressed patch so a layer target wins.
  resolveZone(name: string, root: ParentNode): Element | null;
  // Open a layer. Builds the dialog and the zone container before the request,
  // then GETs the body into it. The opener link, when present, carries
  // data-next-accepted and takes focus back on close. A server-initiated open
  // passes null.
  open(opener: HTMLElement | null, href: string, zone: string): Promise<void>;
  // Close the top layer. A result accepts, a dismiss rejects with a reason. An
  // empty argument accepts with no result.
  close(detail: LayerCloseEvent): void;
  // Append a toast to the built-in container. The text is set as textContent,
  // never parsed as HTML, so server text cannot inject markup.
  toast(text: string, variant: string): void;
  // The number of open layers, for tests and the applier's top-down resolve.
  size(): number;
  // Mark the initiator and target busy for the duration of a layer request, and
  // return the releaser. Exposed so the open path and the applier share it.
  busy(initiator: Element | null, target: Element | null): () => void;
  // Install the delegated click handler that opens layers without JS-less
  // breakage. Returns the teardown.
  install(doc: Document): () => void;
  _reset(): void;
}

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
    return doc.location.pathname + doc.location.search;
  }

  function resolveZone(name: string, root: ParentNode): Element | null {
    const selector = `[data-next-zone="${cssEscape(name)}"]`;
    // Top layer down: a zone inside the upper modal is found before the
    // same-named page zone beneath it. The layer's own container carries the
    // zone, so it is matched directly, not only its descendants.
    for (let i = stack.length - 1; i >= 0; i -= 1) {
      const container = stack[i].root;
      if (container.matches(selector)) return container;
      const found = container.querySelector(selector);
      if (found !== null) return found;
    }
    return root.querySelector(selector);
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
    href: string,
    zone: string,
  ): Promise<void> {
    const dialog = doc.createElement("dialog");
    dialog.setAttribute("data-next-dialog", "");
    const root = doc.createElement("div");
    // The zone container is named after the opener's own attribute and built
    // before the request, so the first morph finds the target by the ordinary
    // resolve without interpreting the response.
    root.setAttribute("data-next-zone", zone);
    dialog.append(root);
    doc.body.append(dialog);
    const returnFocus = doc.activeElement;
    // The host page is the one that opened the layer, captured before the
    // request so a later navigation cannot move it. It rides X-Next-Origin.
    const host = doc.location.pathname;
    // A browser dismiss gesture (Esc, backdrop, dialog form) reaches the same
    // close path as a server dismiss, so the reason flows through one channel.
    const close = dialogAdapter.open(dialog, (reason) => dismissFrom(dialog, reason));
    const layer: Layer = { dialog, root, opener, close, returnFocus, host };
    stack.push(layer);
    // Push the honest URL of the layer body so the modal is shareable and a
    // refresh resolves it as the standalone page, while Back closes it.
    history.push(href);
    layer.pushedUrl = currentUrl();
    emit("partial:layer-opened", { opener });
    const release = busy(opener, root);
    try {
      await deps.fetch({
        url: href,
        zone,
        headers: { [HEADER_ZONE]: zone, [HEADER_ORIGIN]: host },
      });
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
      // The opener wires master and list: on accept the host page is re-GET for
      // the named zone, so the list under the modal morphs. The master never
      // names the list. The host is the page that opened the layer, remembered
      // at open time and sent as X-Next-Origin so the server resolves it.
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
    // The miss arm is a defensive guard: every caller (close, dismissFrom,
    // _reset) hands remove a layer that is still on the stack, so indexOf never
    // returns -1 through the public surface.
    /* v8 ignore next */
    if (index !== -1) stack.splice(index, 1);
    layer.close();
    layer.dialog.remove();
    if (layer.returnFocus instanceof HTMLElement) layer.returnFocus.focus();
    // A programmatic close still sits on the pushed URL, so replace it back to
    // the host. A close driven by Back already moved the URL, so the guard
    // skips it and no second history entry is written, which also keeps the
    // popstate handler from looping back into this layer.
    if (layer.pushedUrl !== undefined && currentUrl() === layer.pushedUrl) {
      history.replace(layer.host);
    }
  }

  // The single bounded Back handler: when the top layer's pushed URL is no
  // longer current, the user navigated past it, so close that layer. It never
  // restores zones or writes history, the narrow contract that keeps this short
  // of a client router.
  function onPopstate(): void {
    const layer = topLayer();
    if (layer?.pushedUrl !== undefined && layer.pushedUrl !== currentUrl()) {
      dismissFrom(layer.dialog, "popstate");
    }
  }

  function toast(text: string, variant: string): void {
    const host = ensureToastHost();
    const item = doc.createElement("div");
    item.setAttribute("data-next-toast", variant);
    // textContent, never innerHTML: a toast string from drained messages or a
    // server patch cannot smuggle markup.
    item.textContent = text;
    host.append(item);
    emit("next:toast", { text, variant });
  }

  function ensureToastHost(): HTMLElement {
    if (toastHost !== null && toastHost.isConnected) return toastHost;
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
    // No zone or no href is a plain navigation: the no-JS path is untouched.
    if (zone === null || zone === "" || href === null || href === "") return;
    event.preventDefault();
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
    },
  };
}
