// Default platform adapters: the injectable seams behind which the untestable
// browser globals live. jsdom does not implement `location.assign`, does not
// load resources for the clock-bound CSS wait, a real `fetch` would hit the
// network, `moveBefore` is absent, and the <dialog> modality (showModal, the
// focus trap, the dismiss gestures) is not modelled. Each function here is a
// thin pass-through to one of those globals, so the module is excluded from
// TS-coverage rather than painted with fake hits.

import type { HistoryAdapter } from "./apply";
import type { DialogAdapter, DialogControl } from "./layers";
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

// The history seam for the url verb. push and replace map straight onto the
// History global, which mutates shared page state the harness inspects through
// a mock rather than the real bar.
export function defaultHistory(): HistoryAdapter {
  return {
    push: (href) => globalThis.history.pushState(null, "", href),
    replace: (href) => globalThis.history.replaceState(null, "", href),
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
