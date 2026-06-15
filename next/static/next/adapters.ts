// Default platform adapters: the injectable seams behind which the untestable
// browser globals live. jsdom does not implement `location.assign`, does not
// load resources for the clock-bound CSS wait, a real `fetch` would hit the
// network, and `moveBefore` is absent. Each function here is a thin pass-through
// to one of those globals, so the module is excluded from TS-coverage rather
// than painted with fake hits.

import type { Move } from "./morph";
import type { Clock, FetchAdapter, Navigate } from "./wire";

export function defaultFetch(): FetchAdapter {
  return (input, init) => globalThis.fetch(input, init);
}

export function defaultClock(): Clock {
  return {
    now: () => Date.now(),
    setTimeout: (handler, ms) =>
      globalThis.setTimeout(handler, ms) as unknown as number,
    clearTimeout: (handle) => globalThis.clearTimeout(handle),
  };
}

export function defaultNavigate(): Navigate {
  return (url) => globalThis.location.assign(url);
}

// moveBefore moves a node atomically, without a disconnect/connect cycle, so an
// iframe, video, focus, and CSS animation survive the move. The feature-detect
// and the native call live here, behind the injectable `move` seam, so callers
// override it in tests. The native branch is absent from jsdom and is exercised
// through a mock adapter.
const HAS_MOVE_BEFORE = "moveBefore" in Element.prototype;

export const defaultMove: Move = (parent, node, before) => {
  if (HAS_MOVE_BEFORE) {
    try {
      (parent as ParentNode & { moveBefore(n: Node, b: Node | null): void }).moveBefore(
        node,
        before,
      );
      return;
    } catch {
      // A cross-document or hierarchy error falls back to insertBefore.
    }
  }
  parent.insertBefore(node, before);
};
