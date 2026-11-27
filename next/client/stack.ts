// The layer stack as the runtime sees it. Modal layers ship in the layers chunk, so a
// page without a layer link never fetches them, and until the chunk loads every lookup
// answers for the base page alone. Toasts stay here, since any page may receive one.

import { defaultPopState } from "./adapters";
import type { BeforeApply } from "./apply";
import type { LazyModule } from "./chunks";
import { isPlainActivation } from "./events";
import { writeHead } from "./head";
import type { HeadPatch } from "./head";
import type { Layers, LayersFactory, LayerStack, PopStateAdapter } from "./layers";
import {
  ATTR_BUSY,
  ATTR_LAYER,
  ATTR_TOAST,
  ATTR_TOASTS,
  ATTR_ZONE,
  cssEscape,
  currentUrl,
  fire,
  matching,
} from "./protocol";

const TOASTS_ATTR = ATTR_TOASTS;

/** The seams of the stack, the chunk factory and what the chunk is built with. */
export interface StackDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  document?: Document;
  chunk: LazyModule<LayersFactory>;
  build: (factory: LayersFactory) => Layers;
  // A popstate with no layer open is still announced, the router may own it.
  popped: () => void;
  // The full visit a layer link falls back to when the chunk cannot load.
  navigate: (href: string) => void;
}

/** The stack the runtime shares, its layers chunk behind it once loaded. */
export interface Stack extends LayerStack {
  /** Hold an envelope that opens a layer until the chunk has loaded. */
  before(detail: BeforeApply): void;
  /** Fetch the chunk once a mount finds a layer link. */
  scan(root: ParentNode): void;
  /** Bind the layer link clicks and the popstate, returning the teardown. */
  install(doc: Document, popstate?: PopStateAdapter): () => void;
  /** Harness seam, absent from the shipped bundles. */
  _reset(): void;
}

/** Build the runtime's stack, the chunk's layers taking over every call once built. */
export function createStack(deps: StackDeps): Stack {
  const doc = deps.document ?? document;
  const factory = deps.chunk.get();
  let layers = factory && deps.build(factory);
  let built: Element | undefined;
  let detach: (() => void) | undefined;

  const load = async (): Promise<Layers | undefined> => {
    const loaded = deps.chunk.get() ?? (await deps.chunk.load());
    return (layers ??= loaded && deps.build(loaded));
  };

  const resolveSelector = (selector: string, root: ParentNode): Element | null =>
    layers ? layers.resolveSelector(selector, root) : root.querySelector(selector);

  // A tray of the page wins top layer down, so the runtime builds one only as a fallback.
  function tray(): Element {
    const found = resolveSelector(`[${TOASTS_ATTR}]`, doc);
    if (found !== null) return found;
    built = doc.createElement("div");
    built.setAttribute(TOASTS_ATTR, "");
    built.setAttribute("aria-live", "polite");
    doc.body.append(built);
    return built;
  }

  function onClick(event: Event): void {
    const target = event.target;
    const opener = target instanceof Element ? target.closest(`[${ATTR_LAYER}]`) : null;
    if (!(opener instanceof HTMLElement)) return;
    const zone = opener.getAttribute(ATTR_LAYER);
    const href = opener.getAttribute("href");
    // No zone or no href is a plain navigation, the no-JS path is untouched.
    if (!zone || !href || !isPlainActivation(event, opener)) return;
    event.preventDefault();
    // A second click while the first is in flight is dropped, no double dialog.
    if (!opener.hasAttribute(ATTR_BUSY)) void open(opener, href, zone);
  }

  // Loaded layers open in the same task, so a second click finds the opener busy. A chunk
  // that cannot load leaves a link the full visit it is without scripts.
  async function open(
    opener: HTMLElement | null,
    href?: string,
    zone?: string,
  ): Promise<void> {
    const loaded = layers ?? (await load());
    if (loaded) await loaded.open(opener, href, zone);
    else if (href !== undefined) deps.navigate(href);
  }

  const stack: Omit<Stack, "_reset"> = {
    resolveZone: (name, root, page) =>
      layers
        ? layers.resolveZone(name, root, page)
        : root.querySelector(`[${ATTR_ZONE}="${cssEscape(name)}"]`),
    resolveSelector,
    urlFor: (el) => layers?.urlFor(el) ?? currentUrl(doc),
    hostFor: (el) => layers?.hostFor(el),
    rewrite: (el, href) => layers?.rewrite(el, href) ?? true,
    open,
    close: (detail) => layers?.close(detail),
    // With no layer open the tags of the page on screen are written as they come.
    head(patch: HeadPatch, page?: string) {
      if (layers) layers.head(patch, page);
      else if (page === undefined || page === currentUrl(doc)) writeHead(doc, patch);
    },
    // textContent, never innerHTML, so a server toast string cannot smuggle markup.
    toast(text, variant) {
      const item = doc.createElement("div");
      item.setAttribute(ATTR_TOAST, variant);
      item.textContent = text;
      tray().append(item);
      fire(doc, deps.dispatch, "next:toast", { text, variant, element: item });
      return item;
    },
    size: () => layers?.size() ?? 0,
    before(detail) {
      if (layers || !detail.envelope.ops.some((op) => op.op === "layer.open")) return;
      void load().finally(detail.hold());
    },
    scan(root) {
      if (!layers && matching(root, `[${ATTR_LAYER}]`).length > 0) void load();
    },
    install(target, popstate = defaultPopState()) {
      detach?.();
      const controller = new AbortController();
      const { signal } = controller;
      target.addEventListener("click", onClick, { signal });
      const stop = popstate.listen(() => (layers ? layers.popstate() : deps.popped()));
      signal.addEventListener("abort", stop, { once: true });
      detach = () => controller.abort();
      return detach;
    },
  };

  TEST: (stack as Stack)._reset = () => {
    layers?._reset();
    built?.remove();
    built = undefined;
    // Drop the click and popstate listeners install bound, so a reset leaves none.
    detach?.();
  };

  return stack as Stack;
}
