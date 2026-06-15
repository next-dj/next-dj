// Default platform adapters: the injectable seams behind which the untestable
// browser globals live. jsdom does not implement `location.assign`, does not
// load resources for the clock-bound CSS wait, and a real `fetch` would hit the
// network. Each function here is a thin pass-through to one of those globals, so
// the module is excluded from TS-coverage rather than painted with fake hits.

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
