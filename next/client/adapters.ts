// Default platform adapters, thin pass-throughs to the browser globals jsdom
// cannot drive. Each seam is injectable so callers run against mocks, and the
// module is excluded from TS-coverage rather than painted with fake hits.

import type { HistoryAdapter } from "./apply";
import type { LinkLoader, SessionStore } from "./assets";
import type { DialogAdapter, DialogControl, PopStateAdapter } from "./layers";
import type { Move } from "./morph";
import type { EventSourceAdapter, VisibilityAdapter } from "./sse";
import type { ConfirmAdapter, IntersectionAdapter } from "./triggers";
import type { Clock, FetchAdapter, Navigate } from "./wire";

/** The fetch seam, a real fetch would hit the network in tests. */
export function defaultFetch(): FetchAdapter {
  return (input, init) => globalThis.fetch(input, init);
}

/** The clock seam over Date.now and the timer globals. */
export function defaultClock(): Clock {
  return {
    now: () => Date.now(),
    setTimeout: (handler, ms) =>
      globalThis.setTimeout(handler, ms) as unknown as number,
    clearTimeout: (handle) => globalThis.clearTimeout(handle),
  };
}

/** The full-navigation seam, jsdom does not implement location.assign. */
export function defaultNavigate(): Navigate {
  return (url) => globalThis.location.assign(url);
}

/** The CSS loader seam, jsdom never fires link.onload so the insertion lives here. */
export function defaultLinkLoader(): LinkLoader {
  return (url, nonce, done, clock, timeoutMs) => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = url;
    if (nonce !== undefined) link.nonce = nonce;
    let settled = false;
    const finish = (ok: boolean): void => {
      if (settled) return;
      settled = true;
      clock.clearTimeout(timer);
      done(ok);
    };
    link.onload = () => finish(true);
    link.onerror = () => finish(false);
    const timer = clock.setTimeout(() => finish(false), timeoutMs);
    document.head.append(link);
  };
}

/** The history seam for the url verb, push and replace map onto the History global. */
export function defaultHistory(): HistoryAdapter {
  return {
    push: (href) => globalThis.history.pushState(null, "", href),
    replace: (href) => globalThis.history.replaceState(null, "", href),
  };
}

/** The popstate seam, the Back gesture is a browser global jsdom does not drive. */
export function defaultPopState(): PopStateAdapter {
  return {
    listen(handler) {
      const fn = (): void => handler();
      window.addEventListener("popstate", fn);
      return () => window.removeEventListener("popstate", fn);
    },
  };
}

/** The confirm gate for data-next-confirm, window.confirm blocks on a native dialog. */
export function defaultConfirm(): ConfirmAdapter {
  return (text) => globalThis.confirm(text);
}

/** The reload-once store, sessionStorage throws in private mode so access is guarded. */
export function defaultSession(): SessionStore {
  return {
    get(key) {
      try {
        return globalThis.sessionStorage.getItem(key);
      } catch {
        return null;
      }
    },
    set(key, value) {
      try {
        globalThis.sessionStorage.setItem(key, value);
      } catch {
        // A full or disabled store cannot persist, the next mismatch reloads again.
      }
    },
    remove(key) {
      try {
        globalThis.sessionStorage.removeItem(key);
      } catch {
        // Same tolerance as set.
      }
    },
  };
}

/** The one-shot reveal geometry, jsdom reports no IntersectionObserver intersections. */
export function defaultObserver(): IntersectionAdapter {
  return {
    observe(el, onReveal) {
      const io = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            onReveal();
            io.disconnect();
          }
        }
      });
      io.observe(el);
      return () => io.disconnect();
    },
  };
}

// moveBefore moves a node atomically, so an iframe, video, focus, and CSS
// animation survive the move without a disconnect/connect cycle.
const HAS_MOVE_BEFORE = "moveBefore" in Element.prototype;

/** The atomic-move seam over moveBefore, falling back to insertBefore. */
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

/** The EventSource seam for the SSE bridge, jsdom does not implement EventSource. */
export function defaultEventSource(): EventSourceAdapter {
  return {
    open(url, onMessage, onError) {
      const es = new EventSource(url, { withCredentials: true });
      es.addEventListener("next-patches", (event) =>
        onMessage((event as MessageEvent<string>).data),
      );
      // A CLOSED readyState is a 4xx or a permanent failure with no native
      // reconnect, CONNECTING means the browser is already retrying.
      es.onerror = () => onError(es.readyState === EventSource.CLOSED);
      // close ends the connection and discards every listener with the object.
      return { close: () => es.close() };
    },
  };
}

/** The visibility seam over document.visibilityState, a background tab pauses the streams. */
export function defaultVisibility(): VisibilityAdapter {
  return {
    hidden: () => document.visibilityState === "hidden",
    onChange(listener) {
      document.addEventListener("visibilitychange", listener);
      return () => document.removeEventListener("visibilitychange", listener);
    },
  };
}

/** The native <dialog> modality, showModal traps focus and wires the dismiss gestures. */
export function defaultDialog(): DialogAdapter {
  return { open: openNativeDialog };
}

function openNativeDialog(
  dialog: HTMLDialogElement,
  onDismiss: (reason: string) => void,
): DialogControl {
  let runtimeClose = false;
  const onCancel = (event: Event): void => {
    event.preventDefault();
    onDismiss("escape");
  };
  const onClose = (): void => {
    // <form method="dialog"> closes with returnValue as the reason.
    if (!runtimeClose) onDismiss(dialog.returnValue || "dialog");
  };
  // A click whose target is the dialog itself landed on the backdrop padding,
  // children intercept inner clicks, so element identity is the hit-test.
  const onPointer = (event: Event): void => {
    if (event.target === dialog) onDismiss("backdrop");
  };
  dialog.addEventListener("cancel", onCancel);
  dialog.addEventListener("close", onClose);
  dialog.addEventListener("click", onPointer);
  dialog.showModal();
  (dialog.querySelector<HTMLElement>("[autofocus]") ?? dialog).focus();
  return (): void => {
    runtimeClose = true;
    dialog.removeEventListener("cancel", onCancel);
    dialog.removeEventListener("close", onClose);
    dialog.removeEventListener("click", onPointer);
    if (dialog.open) dialog.close();
  };
}
