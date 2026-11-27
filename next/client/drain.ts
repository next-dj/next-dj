// The fill queue of a streamed answer. Each fill settles one boundary, and the fills that
// wait apply as envelopes sized to a time budget, so one task stays short.

import type { Kernel } from "./kernel";
import {
  ATTR_DIGEST,
  CHUNK_BOUNDARY,
  CHUNK_DIGEST,
  CHUNK_END,
  CHUNK_ENVELOPE,
  CHUNK_KIND,
  CHUNK_PARENT,
  CHUNK_STATE,
  CHUNK_TEMPLATE,
  LOADING,
} from "./protocol";

/** One settled boundary as the server streams it. The keys stay short on the wire. */
export interface Fill {
  /** The id of the boundary element the fill settles. */
  [CHUNK_BOUNDARY]: string;
  /** The state the boundary settles into, `error` or ready. */
  [CHUNK_STATE]?: string;
  /** The error kind of an error fill. */
  [CHUNK_KIND]?: string;
  /** The error digest of an error fill. */
  [CHUNK_DIGEST]?: string;
  /** The parent boundary whose fill puts this one's element in place. */
  [CHUNK_PARENT]?: string;
  /** The id of the template a document chunk carries the boundary's HTML in. */
  [CHUNK_TEMPLATE]?: string;
  [CHUNK_ENVELOPE]: { version?: unknown; ops?: unknown; assets?: unknown };
}

export type DrainHost = Pick<
  Kernel,
  "partial" | "mark" | "busy" | "stamped" | "fire" | "fallbacks"
>;

export interface Batching {
  /** Whether a boundary changed under the stream, so its fill must not land. */
  stale: (el: Element) => boolean;
  /** The boundaries still loading that the end of the stream marks incomplete. */
  scope: () => Iterable<Element>;
  live?: () => boolean;
  settled?: (boundaries: string[], status: string) => void;
  idle?: () => void;
}

/** Queue fills, a `0` marking the end of the stream. Shaped as `Array.push`. */
export type Take = (...entries: (Fill | typeof CHUNK_END)[]) => number;

declare global {
  interface Window {
    $nx?: (Fill | typeof CHUNK_END)[];
  }
}

// The milliseconds one task spends applying fills before it yields to input and paint.
export const BUDGET = 8;

interface Scheduler {
  yield?: () => Promise<void>;
}

// The element is null for a nested fill whose parent brings it in this same batch.
interface Hit {
  fill: Fill;
  el: HTMLElement | null;
  release: (fault?: string | null) => void;
}

// The template leaves the document as its fill is taken, whether the fill lands or is
// dropped, and its parsed nodes become the content of the op settling the boundary.
function unpack(fill: Fill): Fill {
  if (fill[CHUNK_TEMPLATE] === undefined) return fill;
  const held = document.getElementById(fill[CHUNK_TEMPLATE]);
  held?.remove();
  if (
    !(held instanceof HTMLTemplateElement) ||
    !Array.isArray(fill[CHUNK_ENVELOPE].ops)
  )
    return fill;
  type Op = { target?: { boundary?: string }; content?: DocumentFragment } | null;
  const op = (fill[CHUNK_ENVELOPE].ops as Op[]).find(
    (item) => item?.target?.boundary === fill[CHUNK_BOUNDARY],
  );
  if (op) op.content = held.content;
  return fill;
}

export function createDrain(
  kernel: DrainHost,
  { stale, scope, live, settled, idle }: Batching,
): Take {
  const queue: Fill[] = [];
  // Nested fills whose parent has not landed yet, retried with every batch.
  const orphans: Fill[] = [];
  const { fallbacks } = kernel;
  let applying = 0;
  let ended = false;
  // The milliseconds one fill took to apply the last time, a guess of one until a batch
  // has been timed, so the first batch takes a budget's worth of eight.
  let cost = 1;

  // At the end of the stream every boundary still loading is marked incomplete.
  const finishIfDone = (): void => {
    if (!ended || queue.length > 0) return;
    for (const el of scope()) if (el.matches(LOADING)) kernel.mark([el])("incomplete");
    if (applying === 0) idle?.();
  };

  // scheduler.yield resumes ahead of other queued tasks once input has run. Without it
  // a frame is the yield, and a hidden tab, which paints nothing, drains on a microtask.
  const schedule = (): void => {
    const scheduler = (globalThis as { scheduler?: Scheduler }).scheduler;
    if (scheduler?.yield) void scheduler.yield().then(flush);
    else (document.hidden ? queueMicrotask : requestAnimationFrame)(flush);
  };

  const take: Take = (...entries) => {
    for (const entry of entries)
      if (entry === CHUNK_END) ended = true;
      else if (queue.push(unpack(entry)) === 1) schedule();
    finishIfDone();
    return 0;
  };

  const collect = (batch: Fill[]): Hit[] => {
    const hits: Hit[] = [];
    const gone: string[] = [];
    for (const fill of batch) {
      const el = document.getElementById(fill[CHUNK_BOUNDARY]);
      if (el === null) {
        if (fill[CHUNK_PARENT] !== undefined) orphans.push(fill);
      } else if (stale(el)) gone.push(fill[CHUNK_BOUNDARY]);
      else {
        if (!fallbacks.has(el)) fallbacks.set(el, el.innerHTML);
        hits.push({ fill, el, release: kernel.mark([el]) });
      }
    }
    if (gone.length > 0) settled?.(gone, "dropped");
    return hits;
  };

  // Settle each boundary of an applied batch, the orphans it did not bring in kept.
  const land = (hits: Hit[], nodes: readonly Element[] | undefined): string[] =>
    hits.flatMap(({ fill, el, release }) => {
      const fault =
        fill[CHUNK_STATE] === "error" ? (fill[CHUNK_KIND] ?? "error") : null;
      const target = el ?? nodes?.find((node) => node.id === fill[CHUNK_BOUNDARY]);
      release(nodes ? fault : undefined);
      if (!target) {
        orphans.push(fill);
        return [];
      }
      if (!el) kernel.mark([target])(fault);
      if (!nodes) return fill[CHUNK_BOUNDARY];
      if (fault && fill[CHUNK_DIGEST])
        target.setAttribute(ATTR_DIGEST, fill[CHUNK_DIGEST]);
      else target.removeAttribute(ATTR_DIGEST);
      return fill[CHUNK_BOUNDARY];
    });

  // One envelope for the batch, so its fills cost one apply pass and one mount pass.
  const applyBatch = (batch: Fill[]): void => {
    const hits = collect(batch);
    if (hits.length === 0) {
      if (applying === 0) orphans.length = 0;
      return;
    }
    for (const fill of orphans.splice(0))
      hits.push({ fill, el: null, release: kernel.mark([]) });
    applying += 1;
    kernel.partial.apply(
      {
        version: "",
        ops: hits.flatMap(({ fill }) => fill[CHUNK_ENVELOPE].ops),
        assets: hits.flatMap(({ fill }) => fill[CHUNK_ENVELOPE].assets),
      },
      {
        isCurrent: live,
        done: ({ ok, nodes, stale: lost }) => {
          applying -= 1;
          const kept = lost ? undefined : nodes;
          const landed = land(hits, kept);
          if (applying === 0) take(...orphans.splice(0));
          settled?.(landed, kept === undefined ? "dropped" : ok ? "ok" : "error");
          finishIfDone();
        },
      },
    );
  };

  // Each batch takes as many fills as the cost of the last one says fit the time left,
  // and the task yields once the budget is spent.
  const flush = (): void => {
    const start = performance.now();
    let spent = 0;
    while (queue.length > 0 && spent < BUDGET) {
      const size = Math.max(1, Math.floor((BUDGET - spent) / cost));
      const batch = queue.splice(0, size);
      const before = performance.now();
      applyBatch(batch);
      const after = performance.now();
      // An apply waiting on its stylesheets ran nothing yet, so it teaches no cost.
      if (after > before) cost = (after - before) / batch.length;
      spent = after - start;
    }
    if (queue.length > 0) schedule();
    finishIfDone();
  };

  return take;
}

/** Drain the `$nx` queue of a streamed document, the fills before the bundle first. */
export function drain(
  kernel: DrainHost,
  quiet: () => boolean,
  listening: AddEventListenerOptions,
): Take {
  const loose = (el: Element | null): boolean =>
    el === null || (!kernel.busy(el) && loose(el.parentElement));
  const take = createDrain(kernel, {
    stale: (el) => kernel.busy(el) || kernel.stamped(el) > 0,
    scope: () => (quiet() ? [] : [...document.querySelectorAll(LOADING)].filter(loose)),
    settled: (boundaries, status) =>
      kernel.fire("partial:settled", { zones: [], boundaries, status }),
  });
  const pushed = (window.$nx ??= []);
  take(...pushed.splice(0));
  pushed.push = take;
  if (document.readyState === "complete") take(CHUNK_END);
  addEventListener("load", () => take(CHUNK_END), listening);
  document.addEventListener("partial:settled", () => take(), listening);
  return take;
}
