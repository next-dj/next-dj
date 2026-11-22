// Shared test doubles for the seams more than one suite drives. Imported only
// from *.test.ts files, so the bundle built from next.ts never ships them.

import { vi } from "vitest";
import type { LayerBridge } from "./apply";
import { lazyChunk } from "./chunks";
import type { Lazy, LazyModule } from "./chunks";
import { mintCsrf } from "./csrf";
import type { Fill } from "./drain";
import { createPoller } from "./poll";
import type { HistoryHost } from "./router";
import { createSse } from "./sse";
import { createWebSocketTransport } from "./ws";
import type { VisibilityAdapter } from "./sse";
import type { DeferArm } from "./triggers";
import type { Clock } from "./wire";

// Holds every pending timer with a working clearTimeout, so a duplicate chain
// shows up as pending() above one. tick() drains the due set before re-arms.
export function manualPollClock(): Clock & {
  tick(): void;
  pending(): number;
  setNow(ms: number): void;
  intervals: number[];
} {
  let now = 0;
  let handles = 0;
  const timers = new Map<number, () => void>();
  const intervals: number[] = [];
  return {
    now: () => now,
    setTimeout: (handler, ms) => {
      handles += 1;
      timers.set(handles, handler);
      intervals.push(ms);
      return handles;
    },
    clearTimeout: (handle) => {
      timers.delete(handle);
    },
    tick() {
      const due = Array.from(timers.values());
      timers.clear();
      for (const handler of due) handler();
    },
    pending: () => timers.size,
    setNow(ms) {
      now = ms;
    },
    intervals,
  };
}

// Holds every onChange subscriber, since the SSE bridge and poll triggers share it.
export function manualVisibility(): VisibilityAdapter & {
  setHidden(v: boolean): void;
} {
  let hidden = false;
  const listeners = new Set<() => void>();
  return {
    hidden: () => hidden,
    onChange(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    setHidden(v) {
      hidden = v;
      for (const listener of listeners) listener();
    },
  };
}

export const STUB_OWNER = "/owner/";

const select = (selector: string, root: ParentNode): Element | null =>
  root.querySelector(selector);

export function stubBridge(overrides: Partial<LayerBridge> = {}): LayerBridge {
  return {
    resolveZone: (name, root) => select(`[data-next-zone="${name}"]`, root),
    resolveSelector: select,
    urlFor: () => STUB_OWNER,
    open: vi.fn<LayerBridge["open"]>(),
    close: vi.fn<LayerBridge["close"]>(),
    head: vi.fn<LayerBridge["head"]>(),
    toast: vi.fn<LayerBridge["toast"]>(),
    ...overrides,
  };
}

// A chunk module that has already loaded, so a suite runs the runtime synchronously.
export function landed<T>(value: T): LazyModule<T> {
  return {
    get: () => value,
    load: () => Promise.resolve(value),
    whenLoaded: (fn) => fn(value),
  };
}

// A chunk not fetched yet, whose tag lands in the head. The test lands its module.
export function unloaded<T>(key: string): Lazy<T> & { tags(): number } {
  const chunk = lazyChunk<T>(
    {
      dispatch: () => undefined,
      nonce: undefined,
      context: () => ({ $chunks: { [key]: `/static/next/next.${key}.min.js` } }),
    },
    key,
  );
  const tags = (): number =>
    document.head.querySelectorAll(`script[src$="next.${key}.min.js"]`).length;
  return { ...chunk, tags };
}

// The modules of the stream, ws, csrf, and poll chunks, as createPartial takes them.
export const chunkModules = {
  context: () => ({}),
  stream: landed(createSse),
  ws: landed(createWebSocketTransport),
  csrf: landed(mintCsrf),
  poll: landed(createPoller),
  defer: landed<DeferArm>(() => undefined),
};

// The seams the router hands its chunks, each a quiet default a suite overrides.
export function historyHost(over: Partial<HistoryHost> = {}): HistoryHost {
  return {
    partial: {} as HistoryHost["partial"],
    settings: vi.fn<HistoryHost["settings"]>().mockReturnValue({}),
    context: vi.fn<HistoryHost["context"]>().mockReturnValue({}),
    region: vi.fn<HistoryHost["region"]>(),
    chain: vi.fn<HistoryHost["chain"]>().mockReturnValue([]),
    key: vi.fn<HistoryHost["key"]>().mockReturnValue(""),
    ask: vi.fn<HistoryHost["ask"]>().mockRejectedValue(new Error("offline")),
    owned: vi.fn<HistoryHost["owned"]>().mockReturnValue(true),
    plain: vi.fn<HistoryHost["plain"]>().mockReturnValue(false),
    going: vi.fn<HistoryHost["going"]>().mockReturnValue(""),
    flush: vi.fn<HistoryHost["flush"]>(),
    ...over,
  };
}

// Holds every requested animation frame until paint() runs the ones queued so far.
export function manualFrames(): { queued: FrameRequestCallback[]; paint(): void } {
  const queued: FrameRequestCallback[] = [];
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
    queued.push(callback),
  );
  return {
    queued,
    paint() {
      for (const callback of queued.splice(0)) callback(0);
    },
  };
}

// A ready fill of one boundary, the shape a stream pushes onto $nx or sends as a patch.
export function fill(id: string, html: string, extra: Partial<Fill> = {}): Fill {
  return {
    z: id,
    s: "ready",
    e: { ops: [{ op: "inner", target: { boundary: id }, html }], assets: [] },
    ...extra,
  };
}
