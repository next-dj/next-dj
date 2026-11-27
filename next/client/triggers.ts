// Delegated handlers for the data-next-* triggers, bound once on the document.
// Sentinels are armed per inserted subtree after each apply and wait for the observer.

import {
  defaultClock,
  defaultConfirm,
  defaultObserver,
  defaultVisibility,
} from "./adapters";
import type { HistoryAdapter } from "./apply";
import type { LazyModule } from "./chunks";
import { inPlace, isPlainActivation } from "./events";
import { FORMS } from "./forms";
import type { Forms, FormsFactory } from "./forms";
import type { PollFactory, Poller } from "./poll";
import {
  ATTR_ACTION,
  ATTR_CONFIRM,
  ATTR_DEFER,
  ATTR_KEY,
  ATTR_MARGIN,
  ATTR_MERGE,
  ATTR_POLL,
  ATTR_TARGET,
  ATTR_TRANSPORT,
  HEADER_MERGE,
  HEADER_ORIGIN,
  currentUrl,
  matching,
} from "./protocol";
import type { Diagnostics, WireTransport } from "./protocol";
import type { VisibilityAdapter } from "./sse";
import type { Clock } from "./wire";

const CONFIRM_ATTR = ATTR_CONFIRM;
export const POLL_ATTR = ATTR_POLL;

const ARMED = 0;
const IDLE = 1;
const SENT = 2;
const LEFT = 3;
const OWED = 4;
const DONE = 5;

/** The geometry seam over IntersectionObserver, which jsdom does not model. */
export interface IntersectionAdapter {
  observe(el: Element, onView: (visible: boolean) => void, margin: string): () => void;
}

/** The confirm gate, injectable so tests drive accept and cancel without a dialog. */
export type ConfirmAdapter = (text: string) => boolean;

/** A request the triggers hand to the partial fetch. */
export interface TriggerRequest {
  url: string;
  method?: string;
  uid?: string;
  zone?: string;
  queue?: string;
  headers?: Record<string, string>;
  body?: BodyInit;
  abortable?: boolean;
  key?: string;
  owner?: string;
  transport?: WireTransport;
  background?: boolean;
}

/** The seams the triggers draw on. */
export interface TriggerDeps {
  // The partial fetch, named by zone and merge intent, never selectors.
  fetch: (request: TriggerRequest) => void | Promise<{ status: string }>;
  // Abort the in-flight request on a queue, so a submit cancels its own validation.
  abort: (key: string) => void;
  document?: Document;
  clock?: Clock;
  observer?: IntersectionAdapter;
  // The tab-visibility seam the SSE bridge shares, a hidden tab holds no poll timers.
  visibility?: VisibilityAdapter;
  // The zone poller, exported by the poll chunk.
  poll: LazyModule<PollFactory>;
  // The inline validation and filter forms, exported by the forms chunk.
  forms?: LazyModule<FormsFactory> | undefined;
  // The owning page of an element. Absent, poll GETs read the address bar.
  pageUrl?: (el: Element) => string;
  // The host page of the layer an element sits in, answered by the layer stack. Absent,
  // a submit inside a layer stamps no origin, the same as one outside.
  layerHost?: (el: Element) => string | undefined;
  // The confirm gate. Absent, the default calls window.confirm.
  confirm?: ConfirmAdapter;
  // The address-bar seam a filter submit syncs through, shared with the url verb.
  history?: HistoryAdapter;
  // Records a new URL for the page a filter form is on, and answers false when the
  // address bar shows another page and must stay. Absent, the bar is always updated.
  rewrite?: (el: Element, href: string) => boolean;
  // The dev channel that warns on a hand-written value outside its closed set, read
  // through a call so it arrives without rebuilding the listeners and timers.
  diagnostics?: () => Diagnostics | undefined;
}

/** The triggers handle, installed once and scanned per apply. */
export interface Triggers {
  // Bind the delegated listeners once, the lifecycle of the layer stack.
  install(doc: Document): () => void;
  // Arm the sentinels and start the polls of a freshly inserted subtree.
  scan(root: ParentNode): void;
  submission(form: HTMLFormElement, submitter?: Element | null): TriggerRequest;
  sender(el: Element): Pick<TriggerRequest, "owner" | "headers">;
  /** Send once per entry of an element into view, the sentinels' watch. */
  watch: Watch;
  sweep(): void;
  /** Harness seam, absent from the shipped bundles. */
  _reset(): void;
}

export type Sent = ReturnType<TriggerDeps["fetch"]>;

export type Watch = (el: Element, send: () => Sent) => void;

/** The transport a form's requests ride, `any` letting them over the open socket. */
export function transportOf(form: Element): WireTransport {
  return form.getAttribute(ATTR_TRANSPORT) === "any" ? "any" : "http";
}

/** The zone an interactive element targets, named on itself or an ancestor. */
export function targetZone(el: Element): string | null {
  return el.closest(`[${ATTR_TARGET}]`)?.getAttribute(ATTR_TARGET) ?? null;
}

/** Build the trigger handlers over the given seams. */
export function createTriggers(deps: TriggerDeps): Triggers {
  const doc = deps.document ?? document;
  const clock = deps.clock ?? defaultClock();
  const observer = deps.observer ?? defaultObserver();
  const confirm = deps.confirm ?? defaultConfirm();
  const visibility = deps.visibility ?? defaultVisibility();
  const diagnostics = deps.diagnostics ?? ((): undefined => undefined);
  // Sentinels already armed, so a re-inserted element fires no second GET.
  const activated = new WeakSet<Element>();
  // Live observer teardowns by element. A reveal drops its own entry once its request
  // settles, so an infinite scroll does not pile them up for the life of the page.
  const observed = new Map<Element, () => void>();
  let detach: (() => void) | null = null;
  // The forms once their chunk has loaded, and the document the triggers listen on.
  let forms: Forms | undefined;
  let listened: Document | undefined;
  let detachForms: (() => void) | undefined;

  function here(): string {
    return currentUrl(doc);
  }

  const pageUrl = deps.pageUrl ?? (() => here());
  const layerHost = deps.layerHost ?? ((): undefined => undefined);

  function adopt(factory: FormsFactory): void {
    forms ??= factory({ ...deps, document: doc, clock });
    if (listened && !detachForms) detachForms = forms.install(listened);
  }

  const landed = deps.forms?.get();
  if (landed) adopt(landed);

  // A pagination link or sentinel GETs the next page with a merge intent.
  function paginate(el: Element, zone: string): Sent {
    const href = el.getAttribute("href");
    if (href === null || href === "") return;
    // Every caller reaches paginate via a [data-next-merge] match, so this never runs.
    /* v8 ignore next */
    const merge = el.getAttribute(ATTR_MERGE) ?? "append";
    return deps.fetch({ url: href, zone, headers: { [HEADER_MERGE]: merge } });
  }

  // Bound in the bubble phase, so a submit the form's own handlers cancelled stays so.
  function onSubmit(event: Event): void {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.hasAttribute(ATTR_ACTION) || event.defaultPrevented) return;
    const submitter = (event as SubmitEvent).submitter;
    // A submitter asking for another method or browsing context posts natively.
    const method = submitter?.getAttribute("formmethod")?.toLowerCase() ?? "";
    if (method !== "" && method !== "post") return;
    if (!inPlace(submitter?.getAttribute("formtarget") ?? form.getAttribute("target")))
      return;
    // Intercept as a partial mutation under the uid lock. Without the runtime the
    // form posts natively, so this is the enhancement, never the only path.
    event.preventDefault();
    void deps.fetch(submission(form, submitter));
  }

  function submission(
    form: HTMLFormElement,
    submitter?: Element | null,
  ): TriggerRequest {
    // A submit cancels its own validation, the debounce armed on blur as much as the
    // request already sent, so no late answer morphs the form over this response.
    forms?.cancel(form);
    // The pressed button's formaction overrides the form's, as on a native submit.
    const url =
      submitter?.getAttribute("formaction") ?? form.getAttribute("action") ?? here();
    const body = new FormData(form);
    // A native submit carries the pressed button, so replay its name onto the body.
    const name = submitter?.getAttribute("name") ?? "";
    if (name !== "") body.append(name, submitter?.getAttribute("value") ?? "");
    // The declared zone travels as the morph target, without one the server uses uid.
    const zone = targetZone(form);
    // The form's own key, so the response morphs this instance.
    const key = form.getAttribute(ATTR_KEY);
    return {
      url,
      method: "POST",
      uid: form.getAttribute(ATTR_ACTION) ?? url,
      ...sender(form),
      ...(zone !== null ? { zone } : {}),
      ...(key !== null ? { key } : {}),
      body,
      transport: transportOf(form),
    };
  }

  // A mutation fired from inside a layer names the host page, so the server resolves
  // a foreign zone against the host and not the step page in the modal.
  function sender(el: Element): Pick<TriggerRequest, "owner" | "headers"> {
    const origin = layerHost(el);
    return {
      owner: pageUrl(el),
      ...(origin !== undefined ? { headers: { [HEADER_ORIGIN]: origin } } : {}),
    };
  }

  // Bound in the capture phase, so a cancelled confirm stops every later handler.
  function onConfirm(event: Event): void {
    const el = event.target;
    if (!(el instanceof Element)) return;
    const confirmer = el.closest(`[${CONFIRM_ATTR}]`);
    if (confirmer === null) return;
    /* v8 ignore next */
    const text = confirmer.getAttribute(CONFIRM_ATTR) ?? "";
    if (!confirm(text)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }

  // Bound in the bubble phase, so a handler on the link that cancelled the click wins.
  function onClick(event: Event): void {
    const el = event.target;
    if (!(el instanceof Element)) return;
    const link = el.closest(`a[${ATTR_MERGE}][${ATTR_TARGET}]`);
    if (link === null || !isPlainActivation(event, link)) return;
    const zone = link.getAttribute(ATTR_TARGET);
    if (zone !== null && zone !== "") {
      event.preventDefault();
      void paginate(link, zone);
    }
  }

  // One request per entry into view, tracked so _reset can stop the ones still waiting.
  function watch(el: Element, send: () => Sent): void {
    observed.get(el)?.();
    let state = ARMED;
    let stop = (): void => undefined;
    const finish = (): void => {
      state = DONE;
      stop();
      observed.delete(el);
    };
    const fire = (): void => {
      state = SENT;
      const sent = send();
      if (sent instanceof Promise)
        void sent.then(
          ({ status }) => settle(status === "error"),
          () => settle(true),
        );
      else settle(false);
    };
    const settle = (failed: boolean): void => {
      if (state === DONE) return;
      if (!failed || !el.isConnected) finish();
      else if (state === OWED) fire();
      else state = state === LEFT ? ARMED : IDLE;
    };
    stop = observer.observe(
      el,
      (visible) => {
        if (!el.isConnected) finish();
        else if (!visible) state = state < SENT ? ARMED : LEFT;
        else if (state === ARMED) fire();
        else if (state === LEFT) state = OWED;
      },
      el.getAttribute(ATTR_MARGIN) ?? "",
    );
    if (state === DONE) stop();
    else observed.set(el, finish);
  }

  // The poller, built on first use once its chunk has loaded.
  let poller: Poller | undefined;
  // Bumped by _reset, so a chunk landing late starts no poller for a dead instance.
  let epoch = 0;

  function startPolls(factory: PollFactory, els: Element[]): void {
    poller ??= factory({
      clock,
      visibility,
      pageUrl,
      fetch: (url, zone) => void deps.fetch({ url, zone, background: true }),
    });
    for (const el of els) poller.start(el);
  }

  function scan(root: ParentNode): void {
    diagnostics()?.attrs(root);
    // Only marked sentinels arm an observer, plain pagination links stay clicks.
    for (const el of matching(root, `[${ATTR_MERGE}][${ATTR_DEFER}]`)) {
      const zone = el.getAttribute(ATTR_TARGET);
      if (zone === null || activated.has(el)) continue;
      activated.add(el);
      watch(el, () => paginate(el, zone));
    }
    // A page with no validated form or trigger never fetches the forms.
    if (!forms && deps.forms && matching(root, FORMS).length > 0) {
      const started = epoch;
      deps.forms.whenLoaded((factory) => {
        if (started === epoch) adopt(factory);
      });
    }
    // A page with no poll zone never fetches the poller.
    const polled = matching(root, `[${POLL_ATTR}]`);
    if (polled.length === 0) return;
    const started = epoch;
    deps.poll.whenLoaded((factory) => {
      if (started === epoch) startPolls(factory, polled);
    });
  }

  function install(target: Document): () => void {
    if (detach !== null) detach();
    // One controller owns every listener of this install, so no teardown can drift
    // from the capture flag its addEventListener used.
    const controller = new AbortController();
    const signal = controller.signal;
    listened = target;
    detachForms = forms?.install(target);
    signal.addEventListener("abort", () => {
      detachForms?.();
      detachForms = listened = undefined;
    });
    target.addEventListener("submit", onSubmit, { signal });
    target.addEventListener("click", onConfirm, { capture: true, signal });
    target.addEventListener("click", onClick, { signal });
    // The visibility subscription pauses and resumes the poll timers. Its teardown
    // runs on the signal, so a second detach stays a no-op.
    const stopVisibility = visibility.onChange(() => poller?.wake());
    signal.addEventListener("abort", stopVisibility, { once: true });
    detach = () => controller.abort();
    return detach;
  }

  const exposed = {
    install,
    scan,
    submission,
    sender,
    watch,
    sweep() {
      for (const [el, finish] of observed) if (!el.isConnected) finish();
    },
  } satisfies Omit<Triggers, "_reset"> as Triggers;

  TEST: {
    exposed._reset = () => {
      for (const finish of observed.values()) finish();
      poller?._reset();
      epoch += 1;
      // Also remove the listeners install added, so a reset instance handles no event.
      detach?.();
      detach = null;
    };
  }

  return exposed;
}
