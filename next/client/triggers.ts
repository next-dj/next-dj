// Delegated handlers for the data-next-* triggers, bound once on the document.
// Lazy-zone activation runs per inserted subtree after each apply. Load zones are
// batched on ready, revealed zones and pagination sentinels wait for the observer.

import {
  defaultClock,
  defaultConfirm,
  defaultHistory,
  defaultObserver,
  defaultVisibility,
} from "./adapters";
import type { HistoryAdapter } from "./apply";
import type { LazyModule } from "./chunks";
import type { PollFactory, Poller } from "./poll";
import {
  ATTR_ACTION,
  ATTR_POLL,
  ATTR_KEY,
  ATTR_ZONE,
  HEADER_MERGE,
  HEADER_ORIGIN,
  addZone,
  currentUrl,
  matching,
} from "./protocol";
import type { Diagnostics } from "./protocol";
import type { VisibilityAdapter } from "./sse";
import type { Clock } from "./wire";

const TRIGGER_ATTR = "data-next-trigger";
const TARGET_ATTR = "data-next-target";
const DEBOUNCE_ATTR = "data-next-debounce";
export const MERGE_ATTR = "data-next-merge";
const CONFIRM_ATTR = "data-next-confirm";
export const LAZY_ATTR = "data-next-lazy";
export const POLL_ATTR = ATTR_POLL;
const VALIDATE_ATTR = "data-next-validate";
// X-Next-Validate is local to inline validation, not shared protocol vocabulary.
const HEADER_VALIDATE = "X-Next-Validate";

/** The geometry seam over IntersectionObserver, which jsdom does not model. */
export interface IntersectionAdapter {
  observe(el: Element, onReveal: () => void): () => void;
}

/** The confirm gate, injectable so tests drive accept and cancel without a dialog. */
export type ConfirmAdapter = (text: string) => boolean;

/** The seams the triggers draw on. */
export interface TriggerDeps {
  // The partial fetch, named by zone and merge intent, never selectors.
  fetch: (request: {
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
  }) => void;
  // Abort the in-flight request on a queue, so a submit cancels its own validation.
  abort: (key: string) => void;
  document?: Document;
  clock?: Clock;
  observer?: IntersectionAdapter;
  // The tab-visibility seam the SSE bridge shares, a hidden tab holds no poll timers.
  visibility?: VisibilityAdapter;
  // The zone poller, exported by the poll chunk.
  poll: LazyModule<PollFactory>;
  // The owning page of an element. Absent, lazy and poll GETs read the address bar.
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
  // Fire the batched load zones on ready.
  ready(): void;
  // Activate lazy zones and arm sentinels in a freshly inserted subtree.
  scan(root: ParentNode): void;
  _reset(): void;
}

/** Build the trigger handlers over the given seams. */
export function createTriggers(deps: TriggerDeps): Triggers {
  const doc = deps.document ?? document;
  const clock = deps.clock ?? defaultClock();
  const observer = deps.observer ?? defaultObserver();
  const confirm = deps.confirm ?? defaultConfirm();
  const visibility = deps.visibility ?? defaultVisibility();
  const history = deps.history ?? defaultHistory();
  const diagnostics = deps.diagnostics ?? ((): undefined => undefined);
  // Per-element debounce handles, keyed by the element.
  const timers = new WeakMap<Element, number>();
  // Lazy zones already activated, so a re-inserted element fires no second GET.
  const activated = new WeakSet<Element>();
  // Live observer teardowns by element. A one-shot reveal drops its own entry as it
  // fires, so an infinite scroll does not pile them up for the life of the page.
  const observed = new Map<Element, () => void>();
  // A form's pending validation. The debounce timer and the POST it would send share
  // one controller, so a submit cancels whichever of the two is pending.
  const validations = new WeakMap<HTMLFormElement, AbortController>();
  let detach: (() => void) | null = null;

  function here(): string {
    return currentUrl(doc);
  }

  const pageUrl = deps.pageUrl ?? (() => here());
  const layerHost = deps.layerHost ?? ((): undefined => undefined);
  const rewrite = deps.rewrite ?? ((): boolean => true);

  // The abortable queue key of inline validation, shared by sender and canceller.
  function validateQueue(uid: string | null): string {
    return `validate:${uid ?? ""}`;
  }

  // Drop the form's pending validation, timer and in-flight request alike.
  function cancelValidation(form: HTMLFormElement): void {
    const pending = validations.get(form);
    if (pending === undefined) return;
    validations.delete(form);
    pending.abort();
  }

  // Arm the next validation of a form, superseding the one before it.
  function armValidation(form: HTMLFormElement): AbortSignal {
    cancelValidation(form);
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

  // Resolve the zone an interactive element targets, on itself or an ancestor.
  function targetZone(el: Element): string | null {
    const owner = el.closest(`[${TARGET_ATTR}]`);
    return owner?.getAttribute(TARGET_ATTR) ?? null;
  }

  function debounceMs(el: Element): number {
    const raw = el.closest(`[${DEBOUNCE_ATTR}]`)?.getAttribute(DEBOUNCE_ATTR);
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
    zoneGet(url, zone);
  }

  // The wire stamps X-Next-Zone from request.zone, triggers pass intent, not headers.
  function zoneGet(url: string, zone: string): void {
    deps.fetch({ url, zone });
  }

  // Fire one comma-joined zone GET per owning page.
  function flushBatches(batches: Map<string, string[]>): void {
    for (const [url, zones] of batches) zoneGet(url, zones.join(","));
  }

  // A pagination link or sentinel GETs the next page with a merge intent.
  function paginate(el: Element, zone: string): void {
    const href = el.getAttribute("href");
    if (href === null || href === "") return;
    // Every caller reaches paginate via a [data-next-merge] match, so this never runs.
    /* v8 ignore next */
    const merge = el.getAttribute(MERGE_ATTR) ?? "append";
    deps.fetch({ url: href, zone, headers: { [HEADER_MERGE]: merge } });
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
    deps.fetch({
      url: form.getAttribute("action") ?? here(),
      method: "POST",
      // The validate POST carries a body but mutates nothing. It uses an abortable
      // queue of its own, never the uid lock, so a new blur or submit aborts it.
      queue: validateQueue(uid),
      // The declared zone, so the server can answer a validation with a zone morph.
      ...(zone !== null ? { zone } : {}),
      abortable: true,
      headers: { [HEADER_VALIDATE]: field },
      body: data,
    });
  }

  function onInput(event: Event): void {
    const el = event.target;
    if (!(el instanceof Element)) return;
    const trigger = el.closest(`[${TRIGGER_ATTR}]`);
    if (!(trigger instanceof HTMLElement)) return;
    const want = trigger.getAttribute(TRIGGER_ATTR);
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
    const form = el.closest(`form[${VALIDATE_ATTR}]`);
    if (!(form instanceof HTMLFormElement)) return;
    const name = el.getAttribute("name");
    if (name === null || name === "") return;
    afterDelay(debounceMs(form), armValidation(form), () => validateField(form, name));
  }

  function onSubmit(event: Event): void {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const uid = form.getAttribute(ATTR_ACTION);
    if (uid === null) return;
    // A submit cancels its own validation, the debounce armed on blur as much as the
    // request already sent, so no late answer morphs the form over this response.
    cancelValidation(form);
    // Intercept as a partial mutation under the uid lock. Without the runtime the
    // form posts natively, so this is the enhancement, never the only path.
    event.preventDefault();
    const body = new FormData(form);
    // A native submit carries the pressed button, so replay its name onto the body.
    const submitter = (event as SubmitEvent).submitter;
    const name = submitter?.getAttribute("name") ?? "";
    if (name !== "") body.append(name, submitter?.getAttribute("value") ?? "");
    // The declared zone travels as the morph target, without one the server uses uid.
    const zone = targetZone(form);
    // The form's own key, so the response morphs this instance.
    const key = form.getAttribute(ATTR_KEY);
    // A mutation fired from inside a layer names the host page, so the server resolves
    // a foreign zone against the host and not the step page in the modal.
    const origin = layerHost(form);
    deps.fetch({
      url: form.getAttribute("action") ?? here(),
      method: "POST",
      uid,
      owner: pageUrl(form),
      ...(zone !== null ? { zone } : {}),
      ...(key !== null ? { key } : {}),
      ...(origin !== undefined ? { headers: { [HEADER_ORIGIN]: origin } } : {}),
      body,
    });
  }

  function onClick(event: Event): void {
    const el = event.target;
    if (!(el instanceof Element)) return;
    const confirmer = el.closest(`[${CONFIRM_ATTR}]`);
    if (confirmer !== null) {
      // closest matched the attribute, so getAttribute returns a string.
      /* v8 ignore next */
      const text = confirmer.getAttribute(CONFIRM_ATTR) ?? "";
      if (!confirm(text)) {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
    }
    const link = el.closest(`a[${MERGE_ATTR}][${TARGET_ATTR}]`);
    // The selector restricts the match to <a>, so the false branch never runs. The
    // instanceof stays for the narrowing the closure relies on.
    /* v8 ignore next */
    if (link instanceof HTMLAnchorElement) {
      const zone = link.getAttribute(TARGET_ATTR);
      if (zone !== null && zone !== "") {
        event.preventDefault();
        paginate(link, zone);
      }
    }
  }

  // Activate one lazy zone or sentinel. load fires straight away (batched on
  // ready), revealed and sentinels wait for the observer.
  function activate(el: Element): void {
    if (activated.has(el)) return;
    const zone = el.getAttribute(ATTR_ZONE);
    const lazy = el.getAttribute(LAZY_ATTR);
    const merge = el.getAttribute(MERGE_ATTR);
    if (zone !== null && lazy === "revealed") {
      activated.add(el);
      watch(el, () => zoneGet(pageUrl(el), zone));
      return;
    }
    const targetZ = el.getAttribute(TARGET_ATTR);
    if (merge !== null && targetZ !== null) {
      // An infinite-scroll sentinel: paginate when it scrolls into view.
      activated.add(el);
      watch(el, () => paginate(el, targetZ));
    }
  }

  // A one-shot reveal, tracked so _reset can stop the ones still waiting.
  function watch(el: Element, onReveal: () => void): void {
    const stop = observer.observe(el, () => {
      // Geometry is answered in a later task, so the entry is already in place.
      observed.delete(el);
      onReveal();
    });
    observed.set(el, stop);
  }

  // The poller, built on first use once its chunk has loaded.
  let poller: Poller | undefined;

  // Start polling the elements, false while the poll chunk has not loaded.
  function startPolls(els: Element[]): boolean {
    const factory = deps.poll.get();
    if (factory === undefined) return false;
    poller ??= factory({ clock, visibility, pageUrl, fetch: zoneGet });
    for (const el of els) poller.start(el);
    return true;
  }

  // Batch the load zones into one comma-joined GET per owning page. Grouping by
  // the layer-resolved page keeps a base-page zone and a layer zone apart.
  function loadBatch(root: ParentNode): void {
    const batches = new Map<string, string[]>();
    for (const el of matching(root, `[${LAZY_ATTR}="load"]`)) {
      if (activated.has(el)) continue;
      const zone = el.getAttribute(ATTR_ZONE);
      if (zone === null) continue;
      activated.add(el);
      addZone(batches, pageUrl(el), zone);
    }
    // The wire queues per path and zone batch, so a re-fired batch supersedes its own.
    flushBatches(batches);
  }

  function scan(root: ParentNode): void {
    diagnostics()?.attrs(root);
    for (const el of matching(root, `[${LAZY_ATTR}="revealed"]`)) {
      activate(el);
    }
    for (const el of matching(root, `a[${MERGE_ATTR}][${TARGET_ATTR}]`)) {
      // Only marked sentinels arm an observer, plain pagination links stay clicks.
      if (el.hasAttribute(LAZY_ATTR)) activate(el);
    }
    // A page with no poll zone never fetches the poller.
    const polled = matching(root, `[${POLL_ATTR}]`);
    if (polled.length > 0 && !startPolls(polled)) {
      void deps.poll.load().then(() => startPolls(polled));
    }
  }

  function install(target: Document): () => void {
    if (detach !== null) detach();
    // One controller owns every listener of this install, so no teardown can drift
    // from the capture flag its addEventListener used.
    const controller = new AbortController();
    const signal = controller.signal;
    target.addEventListener("input", onInput, { signal });
    target.addEventListener("change", onInput, { signal });
    // Capture for blur, which does not bubble.
    target.addEventListener("blur", onBlur, { capture: true, signal });
    target.addEventListener("submit", onSubmit, { capture: true, signal });
    target.addEventListener("click", onClick, { capture: true, signal });
    // The visibility subscription pauses and resumes the poll timers. Its teardown
    // runs on the signal, so a second detach stays a no-op.
    const stopVisibility = visibility.onChange(() => poller?.wake());
    signal.addEventListener("abort", stopVisibility, { once: true });
    detach = () => controller.abort();
    return detach;
  }

  return {
    install,
    ready() {
      loadBatch(doc);
    },
    scan(root) {
      scan(root);
      loadBatch(root);
    },
    _reset() {
      for (const stop of observed.values()) stop();
      observed.clear();
      poller?._reset();
      // Also remove the listeners install added, so a reset instance handles no event.
      detach?.();
      detach = null;
    },
  };
}
