// Default platform adapters: the injectable seams behind which the untestable
// browser globals live. jsdom does not implement `location.assign`, does not
// load resources for the clock-bound CSS wait, a real `fetch` would hit the
// network, `moveBefore` is absent, and the <dialog> modality (showModal, the
// focus trap, the dismiss gestures) is not modelled. Each function here is a
// thin pass-through to one of those globals, so the module is excluded from
// TS-coverage rather than painted with fake hits.

import type { HistoryAdapter } from "./apply";
import type { LinkLoader, SessionStore } from "./assets";
import type { DialogAdapter, DialogControl } from "./layers";
import type { Move } from "./morph";
import type { EventSourceAdapter, VisibilityAdapter } from "./sse";
import type { ConfirmAdapter, IntersectionAdapter } from "./triggers";
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

// The CSS loader seam. jsdom never fires link.onload, so the real insertion and
// the onload/onerror wiring live here behind the injectable LinkLoader and the
// assets module runs against a mock that drives the load, error, and timeout
// branches deterministically.
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

// The history seam for the url verb. push and replace map straight onto the
// History global, which mutates shared page state the harness inspects through
// a mock rather than the real bar.
export function defaultHistory(): HistoryAdapter {
  return {
    push: (href) => globalThis.history.pushState(null, "", href),
    replace: (href) => globalThis.history.replaceState(null, "", href),
  };
}

// The confirm gate for data-next-confirm. window.confirm blocks on a native
// dialog jsdom does not render, so it lives behind the seam and tests drive
// accept and cancel through a mock.
export function defaultConfirm(): ConfirmAdapter {
  return (text) => globalThis.confirm(text);
}

// The reload-once store. sessionStorage throws in private mode and when storage
// is disabled, and the version guard must never break navigation over it, so the
// real access lives here behind the seam wrapped in a tolerant try/catch and the
// assets module runs against an in-memory store in tests.
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
        // A full or disabled store means the guard cannot persist, the next
        // mismatch reloads again, which is the safe direction.
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

// The revealed-trigger geometry: a real IntersectionObserver fires the callback
// once the element scrolls into view, then disconnects (one-shot reveal). jsdom
// reports no intersections, so the geometry lives here behind the adapter and
// the triggers run against a mock that calls the reveal callback directly.
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

// The EventSource seam for the SSE bridge. jsdom does not implement EventSource,
// so the native connection, its next-patches listener, and the server-driven
// reconnect with retry live here, and the sse module runs against a mock that
// drives message and error directly.
export function defaultEventSource(): EventSourceAdapter {
  return {
    open(url, onMessage, onError) {
      const es = new EventSource(url, { withCredentials: true });
      es.addEventListener("next-patches", (event) =>
        onMessage((event as MessageEvent).data),
      );
      // A CLOSED readyState is a 4xx or a permanent failure with no native
      // reconnect, CONNECTING means the browser is already retrying.
      es.onerror = () => onError(es.readyState === EventSource.CLOSED);
      // close ends the connection and discards every listener with the object.
      return { close: () => es.close() };
    },
  };
}

// The visibility seam over document.visibilityState and visibilitychange. A
// background tab pauses the streams, a foreground tab resumes them. The real
// document signal lives here so the sse module runs against a mock that flips
// the state and fires the listener deterministically.
export function defaultVisibility(): VisibilityAdapter {
  return {
    hidden: () => document.visibilityState === "hidden",
    onChange(listener) {
      document.addEventListener("visibilitychange", listener);
      return () => document.removeEventListener("visibilitychange", listener);
    },
  };
}

// The native <dialog> modality: showModal traps focus, and the browser dismiss
// gestures (Esc, backdrop click, <form method="dialog">) are wired to one
// callback. jsdom models none of this, so the modality lives here behind the
// adapter seam and the layer stack runs against a mock in tests.
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
  // A click whose target is the dialog itself landed on the backdrop padding:
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
