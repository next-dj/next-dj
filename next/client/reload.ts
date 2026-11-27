// The loading UI of a reload: a zone marked reload="loading" shows its skeleton, or the
// loading content of its boundaries, once the request outlives its delay, and a layer
// shows its loading template until its answer fills it.

import type { Kernel } from "./kernel";
import { ORDER_CAPTURE } from "./kernel";
import { fireRemoved } from "./morph";
import {
  ATTR_ADOPTED,
  ATTR_BOUNDARY,
  ATTR_BOUNDARY_KEY,
  ATTR_BUSY,
  ATTR_DELAY,
  ATTR_LAYER,
  ATTR_LOADING,
  ATTR_RELOAD,
  ATTR_RESERVE,
  ATTR_STATE,
  ATTR_ZONE,
  cssEscape,
  duration,
  matching,
} from "./protocol";
import type { Settled } from "./wire";

const PENDING = `[${ATTR_STATE}=idle],[${ATTR_STATE}=loading]`;
const RESERVE_OFF = `[${ATTR_RESERVE}="off"]`;
// The loading copies kept per boundary key, the oldest dropped past this many.
const CAPTURED = 100;

export type ReloadHost = Pick<Kernel, "partial" | "fallbacks" | "hook">;

// A loading UI shown in place of an element, what it replaced and what it showed.
interface Swap {
  el: HTMLElement;
  kept: Node[];
  shown: string;
  height: string;
}

// A zone reload whose loading UI waits for its delay, then for the answer.
interface Reload {
  zone: Element;
  timer: ReturnType<typeof setTimeout>;
  swaps: Swap[];
}

// A layer root showing its loading template until the answer fills it.
interface Opened {
  root: Element;
  timer: ReturnType<typeof setTimeout>;
  shown?: string;
}

interface LayerOpened {
  opener: Element | null;
  root: Element;
}

function changed(field: Element): boolean {
  if (field instanceof HTMLSelectElement) {
    const initial = Array.from(field.options).findIndex((o) => o.defaultSelected);
    return field.selectedIndex !== Math.max(initial, 0);
  }
  const input = field as HTMLInputElement;
  return /^(checkbox|radio)$/.test(input.type)
    ? input.checked !== input.defaultChecked
    : input.value !== input.defaultValue;
}

function discard(node: Node): void {
  if (node instanceof Element) fireRemoved(node);
}

export function createReload(
  { partial, fallbacks, hook }: ReloadHost,
  listening: AddEventListenerOptions = {},
): void {
  const doc = document;
  const captured = new Map<string, string>();
  const reloads = new Map<string, Reload>();
  const named = new Set<string | null>();
  const opened = new Map<string, Opened>();

  const keyOf = (el: Element): string => el.getAttribute(ATTR_BOUNDARY_KEY) ?? el.id;

  const stash = (name: string, adopted = false): string | undefined => {
    const template = doc.querySelector<HTMLTemplateElement>(
      `template[${ATTR_LOADING}="${cssEscape(name)}"]`,
    );
    return adopted
      ? template?.content.firstElementChild?.innerHTML
      : template?.innerHTML;
  };

  const held = (el: Element): boolean =>
    el.contains(doc.activeElement) ||
    matching(el, "input,select,textarea").some(changed);

  function capture(el: Element): void {
    const key = keyOf(el);
    const live = fallbacks.get(el);
    if (captured.has(key) || (live === undefined && !el.matches(PENDING))) return;
    captured.set(key, live ?? el.innerHTML);
    for (const old of captured.keys())
      if (captured.size > CAPTURED) captured.delete(old);
  }

  // Every height is read before the first loading UI goes in, so one layout serves the
  // whole zone instead of one forced layout per boundary.
  function swap(swaps: Swap[], shows: [el: Element, html: string][]): void {
    const heights = shows.map(([el]) =>
      el.closest(RESERVE_OFF) === null ? el.getBoundingClientRect().height : undefined,
    );
    shows.forEach(([el, html], index) => {
      const target = el as HTMLElement;
      const height = target.style.minHeight;
      const kept = Array.from(el.childNodes);
      const reserved = heights[index];
      if (reserved !== undefined) target.style.minHeight = `${reserved}px`;
      el.innerHTML = html;
      swaps.push({ el: target, kept, shown: el.innerHTML, height });
    });
  }

  function show(name: string, { zone, swaps }: Reload): void {
    const skeleton = stash(name);
    if (skeleton !== undefined && !held(zone)) {
      swap(swaps, [[zone, skeleton]]);
      return;
    }
    const shows: [Element, string][] = [];
    for (const el of matching(zone, `[${ATTR_BOUNDARY}][${ATTR_STATE}=ready]`)) {
      const key = keyOf(el);
      const html =
        captured.get(key) ??
        fallbacks.get(el) ??
        stash(key, el.hasAttribute(ATTR_ADOPTED));
      if (html !== undefined && el.isConnected && !held(el)) shows.push([el, html]);
    }
    swap(swaps, shows);
  }

  function finish(name: string): void {
    const entry = reloads.get(name);
    if (entry === undefined || entry.zone.hasAttribute(ATTR_BUSY)) return;
    reloads.delete(name);
    clearTimeout(entry.timer);
    for (const { el, kept, shown, height } of entry.swaps) {
      el.style.minHeight = height;
      if (el.isConnected && el.innerHTML === shown) el.replaceChildren(...kept);
      else kept.forEach(discard);
    }
  }

  // An answer that lands first leaves the root filled or released, so nothing shows.
  function onOpened(event: Event): void {
    const { opener, root } = (event as CustomEvent<LayerOpened>).detail;
    const name = root.getAttribute(ATTR_ZONE);
    const html =
      name &&
      doc.querySelector<HTMLTemplateElement>(
        `template[${ATTR_LAYER}="${cssEscape(name)}"]`,
      )?.innerHTML;
    if (!name || !html) return;
    const entry: Opened = {
      root,
      timer: setTimeout(
        () => {
          if (!root.hasAttribute(ATTR_BUSY) || root.firstChild) return;
          root.innerHTML = html;
          entry.shown = root.innerHTML;
        },
        duration(opener?.getAttribute(ATTR_DELAY)) ?? 150,
      ),
    };
    opened.set(name, entry);
  }

  // A layer whose answer failed drops the loading UI, its root left empty as before.
  function closed(name: string): void {
    const entry = opened.get(name);
    if (entry === undefined) return;
    opened.delete(name);
    clearTimeout(entry.timer);
    if (entry.root.innerHTML === entry.shown) entry.root.replaceChildren();
  }

  function onRequest(event: Event): void {
    const { intent } = (event as CustomEvent<{ intent: { zone?: string } }>).detail;
    for (const part of (intent.zone ?? "").split(",")) {
      const name = part.trim();
      if (!named.has(name) || reloads.has(name)) continue;
      const zone = partial.layers.resolveZone(name, doc);
      if (zone?.getAttribute(ATTR_RELOAD) !== "loading") continue;
      const delay = duration(zone.getAttribute(ATTR_DELAY)) ?? 150;
      const entry: Reload = {
        zone,
        timer: setTimeout(() => show(name, entry), delay),
        swaps: [],
      };
      reloads.set(name, entry);
    }
  }

  partial.onMount(`[${ATTR_ZONE}][${ATTR_RELOAD}=loading]`, (zone) =>
    named.add(zone.getAttribute(ATTR_ZONE)),
  );
  // The harness tears the chunk down between cases, a page keeps its timers.
  TEST: listening.signal?.addEventListener("abort", () => {
    for (const entry of [...reloads.values(), ...opened.values()])
      clearTimeout(entry.timer);
  });
  doc.addEventListener("partial:layer-opened", onOpened, listening);
  doc.addEventListener("partial:before-request", onRequest, listening);
  hook("before", ORDER_CAPTURE, ({ boundaries }) => boundaries.forEach(capture));
  doc.addEventListener(
    "partial:settled",
    (event) => {
      for (const name of (event as CustomEvent<Settled>).detail.zones) {
        finish(name);
        closed(name);
      }
    },
    listening,
  );
}
