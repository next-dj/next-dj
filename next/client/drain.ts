import type { PartialSurface } from "./partial";
import { ATTR_STATE } from "./protocol";

export interface Fill {
  z: string;
  s?: string;
  k?: string;
  g?: string;
  p?: string;
  e: { version?: unknown; ops?: unknown; assets?: unknown };
}

export type DrainHost = Pick<
  PartialSurface,
  "apply" | "_mark" | "_busy" | "_stamped" | "_fire" | "_fallbacks"
>;

export interface Batching {
  stale: (el: Element) => boolean;
  scope: () => Iterable<Element>;
  live?: () => boolean;
  settled?: (boundaries: string[], status: string) => void;
  idle?: () => void;
}

export type Take = (...entries: (Fill | 0)[]) => number;

declare global {
  interface Window {
    $nx?: (Fill | 0)[];
  }
}

const LOADING = `[${ATTR_STATE}=loading]`;
const DIGEST = "data-next-error-digest";

type Hit = [Fill, HTMLElement | null, (fault?: string | null) => void];

export function createDrain(
  partial: DrainHost,
  { stale, scope, live, settled, idle }: Batching,
): Take {
  const queue: Fill[] = [];
  const waiting: Fill[] = [];
  const fallbacks = (partial._fallbacks ??= new WeakMap());
  let applying = 0;
  let ended = false;

  const settle = (): void => {
    if (!ended || queue.length > 0) return;
    for (const el of scope())
      if (el.matches(LOADING)) partial._mark([el])("incomplete");
    if (applying === 0) idle?.();
  };

  const take: Take = (...entries) => {
    for (const entry of entries)
      if (entry === 0) ended = true;
      else if (queue.push(entry) === 1)
        (document.hidden ? queueMicrotask : requestAnimationFrame)(flush);
    settle();
    return 0;
  };

  const flush = (): void => {
    const hits: Hit[] = [];
    const gone: string[] = [];
    for (const fill of queue.splice(0)) {
      const el = document.getElementById(fill.z);
      if (el === null) {
        if (fill.p !== undefined) waiting.push(fill);
      } else if (stale(el)) gone.push(fill.z);
      else {
        if (!fallbacks.has(el)) fallbacks.set(el, el.innerHTML);
        hits.push([fill, el, partial._mark([el])]);
      }
    }
    if (gone.length > 0) settled?.(gone, "dropped");
    if (hits.length > 0) {
      for (const fill of waiting.splice(0)) hits.push([fill, null, partial._mark([])]);
      applying += 1;
      partial.apply(
        {
          version: "",
          ops: hits.flatMap(([fill]) => fill.e.ops),
          assets: hits.flatMap(([fill]) => fill.e.assets),
        },
        {
          isCurrent: live,
          done: ({ ok, nodes, stale: lost }) => {
            applying -= 1;
            const kept = lost ? undefined : nodes;
            const landed = hits.flatMap(([fill, el, release]) => {
              const fault = fill.s === "error" ? (fill.k ?? "error") : null;
              const target = el ?? kept?.find((node) => node.id === fill.z);
              release(kept ? fault : undefined);
              if (!target) {
                waiting.push(fill);
                return [];
              }
              if (!el) partial._mark([target])(fault);
              if (!kept) return fill.z;
              if (fault && fill.g) target.setAttribute(DIGEST, fill.g);
              else target.removeAttribute(DIGEST);
              return fill.z;
            });
            if (applying === 0) take(...waiting.splice(0));
            settled?.(landed, kept === undefined ? "dropped" : ok ? "ok" : "error");
            settle();
          },
        },
      );
    } else if (applying === 0) waiting.length = 0;
    settle();
  };

  return take;
}

export function drain(
  partial: DrainHost,
  quiet: () => boolean,
  listening: AddEventListenerOptions,
): Take {
  const loose = (el: Element | null): boolean =>
    el === null || (!partial._busy(el) && loose(el.parentElement));
  const take = createDrain(partial, {
    stale: (el) => partial._busy(el) || partial._stamped(el) > 0,
    scope: () => (quiet() ? [] : [...document.querySelectorAll(LOADING)].filter(loose)),
    settled: (boundaries, status) =>
      partial._fire("partial:settled", { zones: [], boundaries, status }),
  });
  const pushed = (window.$nx ??= []);
  take(...pushed.splice(0));
  pushed.push = take;
  if (document.readyState === "complete") take(0);
  addEventListener("load", () => take(0), listening);
  document.addEventListener("partial:settled", () => take(), listening);
  return take;
}
