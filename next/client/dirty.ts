// Delegated listeners stamp touched elements with a monotonic counter. A field
// is dirty relative to a response when its stamp is later than the snapshot
// taken at fetch time, so an answer for one field never wipes what the user
// typed into another.

/** Tracks which elements the user touched, keyed against per-request snapshots. */
export interface DirtyTracker {
  /** Stamp the element as locally touched. */
  stamp(el: Element): void;
  /** The current counter, captured at fetch time. */
  snapshot(): number;
  /** An element is dirty when its stamp is later than the request snapshot. */
  isDirtySince(snapshot: number): (el: Element) => boolean;
  // A <details> open state has no focus-like "user is here" signal, so once
  // toggled its openness belongs to the user for the life of the page.
  isTouched(el: Element): boolean;
  install(doc: Document): void;
  _reset(): void;
}

export interface DirtyDeps {
  /** The monotonic source, injectable so tests advance it deterministically. */
  next?: () => number;
}

const TOUCH_EVENTS = ["input", "change", "toggle"];

export function createDirtyTracker(deps: DirtyDeps = {}): DirtyTracker {
  let counter = 0;
  // High-water mark of stamps, so an injected counter is honoured as the source.
  let last = 0;
  const next = deps.next ?? (() => (counter += 1));
  let stamps = new WeakMap<Element, number>();
  let installed: Document | null = null;
  let listener: ((event: Event) => void) | null = null;

  function stamp(el: Element): void {
    last = next();
    stamps.set(el, last);
  }

  function detach(): void {
    if (installed !== null && listener !== null) {
      for (const name of TOUCH_EVENTS) {
        installed.removeEventListener(name, listener, true);
      }
    }
    installed = null;
    listener = null;
  }

  function install(doc: Document): void {
    detach();
    // The capture phase reaches a toggle on <details>, which does not bubble.
    listener = (event) => {
      const el = event.target;
      if (el instanceof Element) stamp(el);
    };
    for (const name of TOUCH_EVENTS) {
      doc.addEventListener(name, listener, true);
    }
    installed = doc;
  }

  return {
    stamp,
    snapshot: () => last,
    isDirtySince(snapshot) {
      return (el) => {
        const at = stamps.get(el);
        return at !== undefined && at > snapshot;
      };
    },
    isTouched: (el) => stamps.has(el),
    install,
    _reset() {
      // Also drop the capture-phase listeners so a reset leaves nothing bound.
      detach();
      stamps = new WeakMap();
      counter = 0;
      last = 0;
    },
  };
}
