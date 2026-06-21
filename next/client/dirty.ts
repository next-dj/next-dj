// The dirty registry: delegated input/change/toggle listeners on the document
// stamp the touched element with a monotonic counter. wire.ts takes a snapshot
// of the counter at fetch time and threads it to apply. A field is dirty
// relative to a response when its stamp is later than that snapshot, so a
// validation answer for field A never wipes the input the user typed into field
// B. The morph engine owns no registry, it takes the predicate.

export interface DirtyTracker {
  // Stamp the element as locally touched. Wired to delegated listeners, exposed
  // for tests that drive the registry without synthesising DOM events.
  stamp(el: Element): void;
  // The current counter, captured by wire.ts at fetch time.
  snapshot(): number;
  // A predicate over the response snapshot: an element is dirty when its stamp
  // is later than the snapshot of the request that produced the response.
  isDirtySince(snapshot: number): (el: Element) => boolean;
  install(doc: Document): void;
  _reset(): void;
}

export interface DirtyDeps {
  // The monotonic source, injectable so tests advance it deterministically.
  next?: () => number;
}

const TOUCH_EVENTS = ["input", "change", "toggle"];

export function createDirtyTracker(deps: DirtyDeps = {}): DirtyTracker {
  let counter = 0;
  // The high-water mark of stamps. snapshot returns it so an injected counter is
  // honoured as faithfully as the built-in one.
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
    install,
    _reset() {
      // A clean slate also drops the capture-phase input/change/toggle
      // listeners install bound, so a reset between tests leaves nothing on the
      // document.
      detach();
      stamps = new WeakMap();
      counter = 0;
      last = 0;
    },
  };
}
