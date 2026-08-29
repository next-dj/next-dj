// Delegated handlers for the data-next-* triggers, bound once on the document.
// Lazy-zone activation runs per inserted subtree after each apply: load zones
// batch on ready, revealed zones and pagination sentinels wait for the observer.

import {
  defaultClock,
  defaultConfirm,
  defaultObserver,
  defaultVisibility,
} from "./adapters";
import {
  ATTR_ACTION,
  ATTR_KEY,
  ATTR_ZONE,
  HEADER_MERGE,
  MAX_POLL_MS,
  MIN_POLL_MS,
  currentUrl,
  devReader,
  matching,
} from "./protocol";
import type { DevFlag } from "./protocol";
import type { VisibilityAdapter } from "./sse";
import type { Clock } from "./wire";

const TRIGGER_ATTR = "data-next-trigger";
const TARGET_ATTR = "data-next-target";
const DEBOUNCE_ATTR = "data-next-debounce";
const MERGE_ATTR = "data-next-merge";
const CONFIRM_ATTR = "data-next-confirm";
const LAZY_ATTR = "data-next-lazy";
const POLL_ATTR = "data-next-poll";
const VALIDATE_ATTR = "data-next-validate";
// X-Next-Validate is local to inline validation, not shared protocol vocabulary.
const HEADER_VALIDATE = "X-Next-Validate";

// The closed value sets the dev warning guards, so a typo is caught at authoring
// time rather than dropped in silence. Merge mirrors the server's vocabulary.
const LAZY_VALUES = new Set(["load", "revealed"]);
const MERGE_VALUES = new Set(["append", "prepend"]);

// One interval group of the poller, where every zone on the cadence rides one timer
// chain and one batched GET. lastFire anchors the resume, a null handle sleeps.
interface PollGroup {
  handle: number | null;
  lastFire: number;
  elements: Set<Element>;
}

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
    headers?: Record<string, string>;
    body?: BodyInit;
    abortable?: boolean;
    key?: string;
  }) => void;
  // Abort the in-flight request on a zone queue, so a submit cancels its validation.
  abort: (zone: string) => void;
  document?: Document;
  clock?: Clock;
  observer?: IntersectionAdapter;
  // The tab-visibility seam the SSE bridge shares, a hidden tab holds no poll timers.
  visibility?: VisibilityAdapter;
  // The owning page of an element, answered by the layer stack. Absent, lazy and
  // poll GETs read the address bar.
  pageUrl?: (el: Element) => string;
  // The confirm gate. Absent, the default calls window.confirm.
  confirm?: ConfirmAdapter;
  // Dev builds warn on a hand-written value outside its closed set. A getter form
  // lets the owner flip it without rebuilding the listeners and timers.
  dev?: DevFlag;
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

// Add a zone to the per-page batch, one comma-joined GET per owning page.
function addZone(batches: Map<string, string[]>, url: string, zone: string): void {
  const zones = batches.get(url);
  if (zones === undefined) batches.set(url, [zone]);
  else zones.push(zone);
}

/** Build the trigger handlers over the given seams. */
export function createTriggers(deps: TriggerDeps): Triggers {
  const doc = deps.document ?? document;
  const clock = deps.clock ?? defaultClock();
  const observer = deps.observer ?? defaultObserver();
  const confirm = deps.confirm ?? defaultConfirm();
  const visibility = deps.visibility ?? defaultVisibility();
  const dev = devReader(deps.dev);
  // Per-element debounce handles, keyed by the element.
  const timers = new WeakMap<Element, number>();
  // Lazy zones already activated, so a parent morph re-inserting the same element
  // fires no second GET.
  const activated = new WeakSet<Element>();
  // Observer teardowns, dropped on reset so vitest files do not leak observers.
  const observed: (() => void)[] = [];
  // Poller groups keyed by interval, plus each element's group so a re-scan arms
  // no second timer and _reset can stop every chain.
  const groups = new Map<number, PollGroup>();
  const membership = new Map<Element, number>();
  let detach: (() => void) | null = null;

  function here(): string {
    return currentUrl(doc);
  }

  const pageUrl = deps.pageUrl ?? (() => here());

  // The abortable zone key of inline validation, shared by sender and canceller.
  function validateZone(uid: string | null): string {
    return `validate:${uid ?? ""}`;
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

  // Debounce by element: a fresh event clears the pending timer, so only the
  // last of a burst runs. ms 0 runs immediately.
  function debounced(el: Element, ms: number, run: () => void): void {
    const pending = timers.get(el);
    if (pending !== undefined) clock.clearTimeout(pending);
    if (ms === 0) {
      run();
      return;
    }
    timers.set(el, clock.setTimeout(run, ms));
  }

  // A filter form auto-submits its query as a zone GET and syncs the address bar
  // with replaceState, an address sync not a visit.
  function submitFilter(form: HTMLFormElement, zone: string): void {
    // URLSearchParams takes string pairs, so file fields are walked out.
    const pairs: [string, string][] = [];
    for (const [name, value] of new FormData(form)) {
      if (typeof value === "string") pairs.push([name, value]);
    }
    const query = new URLSearchParams(pairs).toString();
    // An absent or empty action means the current URL sans query.
    const attr = form.getAttribute("action");
    const action = attr === null || attr === "" ? here().replace(/\?.*$/, "") : attr;
    const url = query === "" ? action : `${action}?${query}`;
    doc.defaultView?.history.replaceState(null, "", url);
    zoneGet(url, zone);
  }

  // The one shape of a zone GET. The wire stamps X-Next-Zone from request.zone, so
  // the triggers pass intent, never headers.
  function zoneGet(url: string, zone: string): void {
    deps.fetch({ url, zone });
  }

  // Fire one comma-joined zone GET per owning page.
  function flushBatches(batches: Map<string, string[]>): void {
    for (const [url, zones] of batches) zoneGet(url, zones.join(","));
  }

  // A pagination link or sentinel GETs the next page with a merge intent, the
  // server authors the append patch.
  function paginate(el: Element, zone: string): void {
    const href = el.getAttribute("href");
    if (href === null || href === "") return;
    // Every caller reaches paginate via a [data-next-merge] match, so this never runs.
    /* v8 ignore next */
    const merge = el.getAttribute(MERGE_ATTR) ?? "append";
    deps.fetch({ url: href, zone, headers: { [HEADER_MERGE]: merge } });
  }

  // Inline validation on blur. The FormData drops file fields so a multipart form
  // does not re-upload on every blur, and the abortable zone collapses a burst.
  function validateField(form: HTMLFormElement, field: string): void {
    const uid = form.getAttribute(ATTR_ACTION);
    const data = new FormData();
    for (const [name, value] of new FormData(form)) {
      if (value instanceof File) continue;
      data.append(name, value);
    }
    deps.fetch({
      url: form.getAttribute("action") ?? here(),
      method: "POST",
      // The validate POST carries a body but mutates nothing: keyed by zone and
      // abortable, never taking the uid lock, so a fresh blur or submit aborts it.
      zone: validateZone(uid),
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
    debounced(el, debounceMs(form), () => validateField(form, name));
  }

  function onSubmit(event: Event): void {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const uid = form.getAttribute(ATTR_ACTION);
    if (uid === null) return;
    // A submit cancels its own in-flight validation, so a late answer never morphs
    // the form the server is about to re-render.
    deps.abort(validateZone(uid));
    // Intercept as a partial mutation under the uid lock. Without the runtime the
    // form posts natively, so this is the enhancement, never the only path.
    event.preventDefault();
    const body = new FormData(form);
    // A native submit carries the pressed button, so replay its name onto the body.
    const submitter = (event as SubmitEvent).submitter;
    const name = submitter?.getAttribute("name") ?? "";
    if (name !== "") body.append(name, submitter?.getAttribute("value") ?? "");
    // The declared zone travels as the morph target. Without one the server falls
    // back to the form by uid.
    const zone = targetZone(form);
    // The form's own key, so the response morphs this instance.
    const key = form.getAttribute(ATTR_KEY);
    deps.fetch({
      url: form.getAttribute("action") ?? here(),
      method: "POST",
      uid,
      ...(zone !== null ? { zone } : {}),
      ...(key !== null ? { key } : {}),
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
      const stop = observer.observe(el, () => zoneGet(pageUrl(el), zone));
      observed.push(stop);
      return;
    }
    const targetZ = el.getAttribute(TARGET_ATTR);
    if (merge !== null && targetZ !== null) {
      // An infinite-scroll sentinel: paginate when it scrolls into view.
      activated.add(el);
      const stop = observer.observe(el, () => paginate(el, targetZ));
      observed.push(stop);
    }
  }

  // The poll interval under a strict decimal grammar: only an all-digit value in
  // the server tag's bounds is an interval, so parseInt("5s")=5 is rejected.
  function pollMs(el: Element): number | null {
    const raw = el.getAttribute(POLL_ATTR);
    if (raw === null || !/^\d+$/.test(raw)) return null;
    const ms = Number(raw);
    return ms >= MIN_POLL_MS && ms <= MAX_POLL_MS ? ms : null;
  }

  // Chained setTimeout, not setInterval, so tests drive ticks one by one. A hidden
  // tab registers the group sleeping.
  function joinPoll(el: Element, interval: number): void {
    membership.set(el, interval);
    const group = groups.get(interval);
    if (group !== undefined) {
      group.elements.add(el);
      return;
    }
    groups.set(interval, {
      handle: visibility.hidden()
        ? null
        : clock.setTimeout(() => pollTick(interval), interval),
      lastFire: clock.now(),
      elements: new Set([el]),
    });
  }

  function startPoll(el: Element): void {
    if (membership.has(el)) return;
    if (el.getAttribute(ATTR_ZONE) === null) return;
    const ms = pollMs(el);
    if (ms === null) return;
    joinPoll(el, ms);
  }

  // Each element is re-read live, a wrapper missing either attribute was morphed
  // away and tears down, a changed interval migrates, a vanished group returns.
  function pollTick(interval: number): void {
    const group = groups.get(interval);
    if (group === undefined) return;
    if (visibility.hidden()) {
      // Safety net for an undelivered hidden visibilitychange: sleep, the visible
      // flip wakes the group.
      group.handle = null;
      return;
    }
    const batches = new Map<string, string[]>();
    for (const el of Array.from(group.elements)) {
      const zone = el.getAttribute(ATTR_ZONE);
      const ms = pollMs(el);
      if (!el.isConnected || zone === null || ms === null) {
        group.elements.delete(el);
        membership.delete(el);
        continue;
      }
      addZone(batches, pageUrl(el), zone);
      if (ms !== interval) {
        group.elements.delete(el);
        membership.delete(el);
        joinPoll(el, ms);
      }
    }
    flushBatches(batches);
    group.lastFire = clock.now();
    if (group.elements.size === 0) {
      groups.delete(interval);
      return;
    }
    group.handle = clock.setTimeout(() => pollTick(interval), interval);
  }

  // On hidden, silence every live timer but keep the groups sleeping. On visible,
  // each group measures elapsed against its own lastFire: due ticks run at once,
  // the rest resume with the remaining time.
  function onVisibility(): void {
    if (visibility.hidden()) {
      for (const group of groups.values()) {
        if (group.handle !== null) clock.clearTimeout(group.handle);
        group.handle = null;
      }
      return;
    }
    for (const [interval, group] of Array.from(groups.entries())) {
      if (group.handle !== null) {
        clock.clearTimeout(group.handle);
        group.handle = null;
      }
      const elapsed = clock.now() - group.lastFire;
      if (elapsed >= interval) {
        pollTick(interval);
      } else {
        group.handle = clock.setTimeout(() => pollTick(interval), interval - elapsed);
      }
    }
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
    // The wire queues per path and zone batch, so a re-fired batch supersedes its
    // own page's predecessor and never another page's.
    flushBatches(batches);
  }

  // An element matched by an attribute selector has it, so the null arm cannot occur.
  function attrOf(el: Element, name: string): string {
    /* v8 ignore next */
    return el.getAttribute(name) ?? "";
  }

  // Warn on hand-written values the runtime drops in silence. Dev-only.
  function validateAttrs(root: ParentNode): void {
    if (!dev()) return;
    for (const el of matching(root, `[${LAZY_ATTR}]`)) {
      const value = attrOf(el, LAZY_ATTR);
      if (!LAZY_VALUES.has(value)) warnAttr(LAZY_ATTR, value, LAZY_VALUES);
    }
    for (const el of matching(root, `[${MERGE_ATTR}]`)) {
      const value = attrOf(el, MERGE_ATTR);
      if (!MERGE_VALUES.has(value)) warnAttr(MERGE_ATTR, value, MERGE_VALUES);
    }
    for (const el of matching(root, `[${POLL_ATTR}]`)) {
      const value = attrOf(el, POLL_ATTR);
      if (pollMs(el) === null) warnPoll(value);
      else if (el.getAttribute(ATTR_ZONE) === null) warnPollZone(value);
    }
  }

  function warnAttr(attr: string, value: string, allowed: Set<string>): void {
    const set = Array.from(allowed).join(", ");
    console.warn(
      `[next.partial] ${attr}="${value}" is not a recognised value and is ignored. Use one of: ${set}.`,
    );
  }

  // The interval has no closed set to list, so the message spells the bounds.
  function warnPoll(value: string): void {
    console.warn(
      `[next.partial] ${POLL_ATTR}="${value}" is not a whole number of milliseconds between ${MIN_POLL_MS} and ${MAX_POLL_MS} and is ignored. The {% zone %} tag writes the resolved interval.`,
    );
  }

  function warnPollZone(value: string): void {
    console.warn(
      `[next.partial] ${POLL_ATTR}="${value}" sits on an element without ${ATTR_ZONE} and is ignored. Polling re-GETs the zone by name, so the container must carry both attributes.`,
    );
  }

  function scan(root: ParentNode): void {
    validateAttrs(root);
    for (const el of matching(root, `[${LAZY_ATTR}="revealed"]`)) {
      activate(el);
    }
    for (const el of matching(root, `a[${MERGE_ATTR}][${TARGET_ATTR}]`)) {
      // Only marked sentinels arm an observer, plain pagination links stay clicks.
      if (el.hasAttribute(LAZY_ATTR)) activate(el);
    }
    for (const el of matching(root, `[${POLL_ATTR}]`)) {
      startPoll(el);
    }
  }

  function install(target: Document): () => void {
    if (detach !== null) detach();
    target.addEventListener("input", onInput);
    target.addEventListener("change", onInput);
    // Capture for blur, which does not bubble.
    target.addEventListener("blur", onBlur, true);
    target.addEventListener("submit", onSubmit, true);
    target.addEventListener("click", onClick, true);
    // The visibility subscription pauses and resumes the poll timers, the same
    // choreography as the SSE bridge.
    const stopVisibility = visibility.onChange(onVisibility);
    detach = () => {
      target.removeEventListener("input", onInput);
      target.removeEventListener("change", onInput);
      target.removeEventListener("blur", onBlur, true);
      target.removeEventListener("submit", onSubmit, true);
      target.removeEventListener("click", onClick, true);
      stopVisibility();
    };
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
      for (const stop of observed) stop();
      observed.length = 0;
      for (const group of groups.values()) {
        if (group.handle !== null) clock.clearTimeout(group.handle);
      }
      groups.clear();
      membership.clear();
    },
  };
}
