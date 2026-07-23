// Shared test doubles for the seams more than one suite drives. Imported only
// from *.test.ts files, so the bundle built from next.ts never ships them.

import type { VisibilityAdapter } from "./sse";
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

// Holds every onChange subscriber, since the SSE bridge and the poll triggers
// share the seam.
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
