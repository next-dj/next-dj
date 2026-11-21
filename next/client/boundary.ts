import type { PartialSurface } from "./partial";
import { ATTR_STATE } from "./protocol";

export interface Chunk {
  z: string;
  s?: string;
  k?: string;
  g?: string;
  p?: string;
  e: { version?: unknown; ops?: unknown; assets?: unknown };
}

export interface Captured extends Element {
  $l?: string;
}

export type ChunkHost = Pick<
  PartialSurface,
  "apply" | "_mark" | "_busy" | "_stamped" | "_fire"
>;

export interface Batching {
  stale: (el: Element) => boolean;
  scope: () => Iterable<Element>;
  live?: () => boolean;
  settled?: (boundaries: string[], status: string) => void;
  idle?: () => void;
}

export type Take = (...entries: (Chunk | 0)[]) => number;

declare global {
  interface Window {
    $nx?: (Chunk | 0)[];
  }
}

const LOADING = `[${ATTR_STATE}=loading]`;
const DIGEST = "data-next-error-digest";

type Hit = [Chunk, HTMLElement | null, (fault?: string | null) => void];

export function createChunks(
  partial: ChunkHost,
  { stale, scope, live, settled, idle }: Batching,
): Take {
  const queue: Chunk[] = [];
  const waiting: Chunk[] = [];
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
    for (const chunk of queue.splice(0)) {
      const el = document.getElementById(chunk.z);
      if (el === null) {
        if (chunk.p !== undefined) waiting.push(chunk);
      } else if (stale(el)) gone.push(chunk.z);
      else {
        (el as Captured).$l ??= el.innerHTML;
        hits.push([chunk, el, partial._mark([el])]);
      }
    }
    if (gone.length > 0) settled?.(gone, "dropped");
    if (hits.length > 0) {
      for (const chunk of waiting.splice(0))
        hits.push([chunk, null, partial._mark([])]);
      applying += 1;
      partial.apply(
        {
          version: "",
          ops: hits.flatMap(([chunk]) => chunk.e.ops),
          assets: hits.flatMap(([chunk]) => chunk.e.assets),
        },
        {
          isCurrent: live,
          done: ({ ok, nodes, stale: lost }) => {
            applying -= 1;
            const kept = lost ? undefined : nodes;
            const landed = hits.flatMap(([chunk, el, release]) => {
              const fault = chunk.s === "error" ? (chunk.k ?? "error") : null;
              const target = el ?? kept?.find((node) => node.id === chunk.z);
              release(kept ? fault : undefined);
              if (!target) {
                waiting.push(chunk);
                return [];
              }
              if (!el) partial._mark([target])(fault);
              if (!kept) return chunk.z;
              if (fault && chunk.g) target.setAttribute(DIGEST, chunk.g);
              else target.removeAttribute(DIGEST);
              return chunk.z;
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
  partial: ChunkHost,
  quiet: () => boolean,
  listening: AddEventListenerOptions,
): Take {
  const loose = (el: Element | null): boolean =>
    el === null || (!partial._busy(el) && loose(el.parentElement));
  const take = createChunks(partial, {
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
