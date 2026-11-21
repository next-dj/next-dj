import type { BeforeApply } from "./apply";
import type { Captured } from "./boundary";
import { fireRemoved } from "./morph";
import type { PartialSurface } from "./partial";
import {
  ATTR_BOUNDARY,
  ATTR_BUSY,
  ATTR_STATE,
  ATTR_ZONE,
  cssEscape,
  duration,
  matching,
} from "./protocol";
import type { Settled } from "./wire";

const PENDING = `[${ATTR_STATE}=idle],[${ATTR_STATE}=loading]`;

export interface ReloadHost {
  partial: Pick<PartialSurface, "layers" | "onMount">;
}

type Swap = [el: HTMLElement, kept: Node[], shown: string, height: string];

type Reload = [zone: Element, timer: ReturnType<typeof setTimeout>, swaps: Swap[]];

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
  { partial }: ReloadHost,
  listening: AddEventListenerOptions = {},
): void {
  const doc = document;
  const captured = new Map<string, string>();
  const reloads = new Map<string, Reload>();
  const named = new Set<string | null>();

  const keyOf = (el: Element): string =>
    el.getAttribute("data-next-boundary-key") ?? el.id;

  const stash = (name: string, adopted = false): string | undefined => {
    const template = doc.querySelector<HTMLTemplateElement>(
      `template[data-next-loading="${cssEscape(name)}"]`,
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
    const live = (el as Captured).$l;
    if (!captured.has(key) && (live !== undefined || el.matches(PENDING)))
      captured.set(key, live ?? el.innerHTML);
  }

  function swap(swaps: Swap[], el: Element, html: string): void {
    const target = el as HTMLElement;
    const height = target.style.minHeight;
    const kept = Array.from(el.childNodes);
    if (el.closest('[data-next-reserve="off"]') === null)
      target.style.minHeight = `${el.getBoundingClientRect().height}px`;
    el.innerHTML = html;
    swaps.push([target, kept, el.innerHTML, height]);
  }

  function show(name: string, [zone, , swaps]: Reload): void {
    const skeleton = stash(name);
    if (skeleton !== undefined && !held(zone)) {
      swap(swaps, zone, skeleton);
      return;
    }
    for (const el of matching(zone, `[${ATTR_BOUNDARY}][${ATTR_STATE}=ready]`)) {
      const key = keyOf(el);
      const html =
        captured.get(key) ??
        (el as Captured).$l ??
        stash(key, el.hasAttribute("data-next-adopted"));
      if (html !== undefined && el.isConnected && !held(el)) swap(swaps, el, html);
    }
  }

  function finish(name: string): void {
    const entry = reloads.get(name);
    if (entry === undefined || entry[0].hasAttribute(ATTR_BUSY)) return;
    reloads.delete(name);
    clearTimeout(entry[1]);
    for (const [el, kept, shown, height] of entry[2]) {
      el.style.minHeight = height;
      if (el.isConnected && el.innerHTML === shown) el.replaceChildren(...kept);
      else kept.forEach(discard);
    }
  }

  function onRequest(event: Event): void {
    const { intent } = (event as CustomEvent<{ intent: { zone?: string } }>).detail;
    for (const part of (intent.zone ?? "").split(",")) {
      const name = part.trim();
      if (!named.has(name) || reloads.has(name)) continue;
      const zone = partial.layers.resolveZone(name, doc);
      if (zone?.getAttribute("data-next-reload") !== "loading") continue;
      const delay = duration(zone.getAttribute("data-next-delay")) ?? 150;
      const entry: Reload = [zone, setTimeout(() => show(name, entry), delay), []];
      reloads.set(name, entry);
    }
  }

  partial.onMount(`[${ATTR_ZONE}][data-next-reload=loading]`, (zone) =>
    named.add(zone.getAttribute(ATTR_ZONE)),
  );
  listening.signal?.addEventListener("abort", () => {
    for (const entry of reloads.values()) clearTimeout(entry[1]);
  });
  doc.addEventListener("partial:before-request", onRequest, listening);
  doc.addEventListener(
    "partial:before-apply",
    (event) => {
      for (const el of (event as CustomEvent<BeforeApply>).detail.boundaries)
        capture(el);
    },
    listening,
  );
  doc.addEventListener(
    "partial:settled",
    (event) => {
      for (const name of (event as CustomEvent<Settled>).detail.zones) finish(name);
    },
    listening,
  );
}
