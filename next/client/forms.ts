// Inline validation and filter forms, the forms chunk. A page whose mount finds neither
// a validated form nor a trigger never fetches it, and the forms post as they would
// without it until it loads.

import { defaultClock, defaultHistory } from "./adapters";
import type { HistoryAdapter } from "./apply";
import {
  ATTR_ACTION,
  ATTR_DEBOUNCE,
  ATTR_TRIGGER,
  ATTR_VALIDATE,
  HEADER_VALIDATE,
  currentUrl,
} from "./protocol";
import { targetZone, transportOf } from "./triggers";
import type { TriggerRequest } from "./triggers";
import type { Clock } from "./wire";

/** The marks that make a page fetch the forms chunk. */
export const FORMS = `form[${ATTR_VALIDATE}],[${ATTR_TRIGGER}]`;

/** The seams the forms draw on, the ones the triggers hand over. */
export interface FormsDeps {
  fetch: (request: TriggerRequest) => unknown;
  abort: (key: string) => void;
  document?: Document;
  clock?: Clock;
  history?: HistoryAdapter;
  rewrite?: (el: Element, href: string) => boolean;
}

/** The forms handle, installed with the triggers. */
export interface Forms {
  /** Bind the input, change and blur listeners, returning their teardown. */
  install(doc: Document): () => void;
  /** Drop the pending validation of a form, as its submit supersedes it. */
  cancel(form: HTMLFormElement): void;
}

export type FormsFactory = (deps: FormsDeps) => Forms;

export function createForms(deps: FormsDeps): Forms {
  const doc = deps.document ?? document;
  const clock = deps.clock ?? defaultClock();
  const history = deps.history ?? defaultHistory();
  const rewrite = deps.rewrite ?? ((): boolean => true);
  // Per-element debounce handles, keyed by the element.
  const timers = new WeakMap<Element, number>();
  // A form's pending validation. The debounce timer and the POST it would send share
  // one controller, so a submit cancels whichever of the two is pending.
  const validations = new WeakMap<HTMLFormElement, AbortController>();

  // The abortable queue key of inline validation, shared by sender and canceller.
  function validateQueue(uid: string | null): string {
    return `validate:${uid ?? ""}`;
  }

  // Drop the form's pending validation, timer and in-flight request alike.
  function cancel(form: HTMLFormElement): void {
    const pending = validations.get(form);
    if (pending === undefined) return;
    validations.delete(form);
    pending.abort();
  }

  // Arm the next validation of a form, superseding the one before it.
  function armValidation(form: HTMLFormElement): AbortSignal {
    cancel(form);
    const controller = new AbortController();
    const queue = validateQueue(form.getAttribute(ATTR_ACTION));
    controller.signal.addEventListener("abort", () => deps.abort(queue), {
      once: true,
    });
    validations.set(form, controller);
    return controller.signal;
  }

  // The debounce of a validation, held by the same signal as its request, so an
  // abort before the timer fires leaves nothing behind to send.
  function afterDelay(ms: number, signal: AbortSignal, run: () => void): void {
    if (ms === 0) {
      run();
      return;
    }
    const handle = clock.setTimeout(run, ms);
    signal.addEventListener("abort", () => clock.clearTimeout(handle), { once: true });
  }

  function debounceMs(el: Element): number {
    const raw = el.closest(`[${ATTR_DEBOUNCE}]`)?.getAttribute(ATTR_DEBOUNCE);
    const ms = raw === null || raw === undefined ? 0 : Number.parseInt(raw, 10);
    return Number.isFinite(ms) && ms > 0 ? ms : 0;
  }

  // A fresh event clears the element's pending timer, so only the last of a burst runs.
  function debounced(el: Element, ms: number, run: () => void): void {
    const pending = timers.get(el);
    if (pending !== undefined) clock.clearTimeout(pending);
    if (ms === 0) {
      run();
      return;
    }
    timers.set(el, clock.setTimeout(run, ms));
  }

  // A filter form auto-submits as a zone GET, replaceState syncs the bar, not a visit.
  // URL owns the join, so an action carrying its own query never grows a second one.
  function submitFilter(form: HTMLFormElement, zone: string): void {
    // URLSearchParams takes string pairs, so file fields are walked out.
    const pairs: [string, string][] = [];
    for (const [name, value] of new FormData(form)) {
      if (typeof value === "string") pairs.push([name, value]);
    }
    // An absent or empty action resolves to the page itself, query included, and
    // assigning the search then drops whatever the old query held.
    const target = new URL(form.getAttribute("action") ?? "", doc.baseURI);
    target.search = new URLSearchParams(pairs).toString();
    const url = target.pathname + target.search;
    if (rewrite(form, url)) history.replace(url);
    // The wire stamps X-Next-Zone from request.zone, triggers pass intent, not headers.
    void deps.fetch({ url, zone });
  }

  // Inline validation on blur. The FormData drops file fields so a multipart form
  // does not re-upload on every blur, and the abortable queue collapses a burst.
  function validateField(form: HTMLFormElement, field: string): void {
    const uid = form.getAttribute(ATTR_ACTION);
    const zone = targetZone(form);
    const data = new FormData();
    for (const [name, value] of new FormData(form)) {
      if (value instanceof File) continue;
      data.append(name, value);
    }
    void deps.fetch({
      url: form.getAttribute("action") ?? currentUrl(doc),
      method: "POST",
      // The validate POST carries a body but mutates nothing. It uses an abortable
      // queue of its own, never the uid lock, so a new blur or submit aborts it.
      queue: validateQueue(uid),
      // The declared zone, so the server can answer a validation with a zone morph.
      ...(zone !== null ? { zone } : {}),
      abortable: true,
      headers: { [HEADER_VALIDATE]: field },
      body: data,
      transport: transportOf(form),
    });
  }

  function onInput(event: Event): void {
    const el = event.target;
    if (!(el instanceof Element)) return;
    const trigger = el.closest(`[${ATTR_TRIGGER}]`);
    if (!(trigger instanceof HTMLElement)) return;
    const want = trigger.getAttribute(ATTR_TRIGGER);
    if (want !== event.type) return;
    const zone = targetZone(trigger);
    if (zone === null) return;
    const form = trigger.closest("form");
    if (!(form instanceof HTMLFormElement)) return;
    debounced(form, debounceMs(trigger), () => submitFilter(form, zone));
  }

  function onBlur(event: Event): void {
    const el = event.target;
    if (!(el instanceof HTMLElement)) return;
    const form = el.closest(`form[${ATTR_VALIDATE}]`);
    if (!(form instanceof HTMLFormElement)) return;
    const name = el.getAttribute("name");
    if (name === null || name === "") return;
    afterDelay(debounceMs(form), armValidation(form), () => validateField(form, name));
  }

  return {
    install(target) {
      // One controller owns every listener of this install, so no teardown can drift
      // from the capture flag its addEventListener used.
      const controller = new AbortController();
      const signal = controller.signal;
      target.addEventListener("input", onInput, { signal });
      target.addEventListener("change", onInput, { signal });
      // Capture for blur, which does not bubble.
      target.addEventListener("blur", onBlur, { capture: true, signal });
      return () => controller.abort();
    },
    cancel,
  };
}
