// Delegated handlers for the data-next-* triggers. Listeners bind once on the
// document so morph and any insertion survive for free (the delegation idiom).
// The imperative lazy-zone activation, on the other hand, runs per inserted
// subtree after each apply (never a module-load scan, never a whole-document
// scan): a load zone fires its batched GET on `ready`, a revealed zone is handed
// to the observer when it enters the DOM, a pagination sentinel arms itself.
//
// Per-element debounce runs through an injected clock. The revealed trigger runs
// through an injected IntersectionObserver adapter, since jsdom models no
// geometry. Inline validation submits a FormData stripped of file fields on
// blur, collapses bursts through a latest-wins counter, and a form submit
// cancels its own in-flight validation so a late validation answer cannot
// overwrite a response the server already accepted.

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
  matching,
} from "./protocol";
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
// X-Next-Validate is local to inline validation, so it stays here rather than in
// the shared protocol vocabulary.
const HEADER_VALIDATE = "X-Next-Validate";

// The closed value sets of the hand-written attributes the dev warning guards.
// A lazy value outside the set is silently dropped client-side. A merge value is
// forwarded to the server, whose accepted vocabulary this set mirrors, so a typo
// is caught at authoring time rather than failing quietly downstream.
const LAZY_VALUES = new Set(["load", "revealed"]);
const MERGE_VALUES = new Set(["append", "prepend"]);

// One interval group of the poller engine: every zone on the same cadence
// rides one timer chain and one batched GET. lastFire anchors the visibility
// resume, so only a group whose own interval elapsed while hidden runs at
// once. A null handle is a sleeping group: the tab is hidden, no timer is
// live.
interface PollGroup {
  handle: number | null;
  lastFire: number;
  elements: Set<Element>;
}

// The geometry seam: real IntersectionObserver behind a mock the harness drives
// by calling the callback, since jsdom reports no intersections.
export interface IntersectionAdapter {
  observe(el: Element, onReveal: () => void): () => void;
}

export type ConfirmAdapter = (text: string) => boolean;

export interface TriggerDeps {
  // The partial fetch, the only transport the triggers reach. They name zones
  // and merge intent, never selectors.
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
  // Abort the in-flight request on a zone queue, called when a form submit must
  // cancel its own inline validation.
  abort: (zone: string) => void;
  document?: Document;
  clock?: Clock;
  observer?: IntersectionAdapter;
  // The tab-visibility seam, shared with the SSE bridge: a hidden tab holds no
  // poll timers, a returning tab runs each group only once its own interval
  // elapsed while hidden.
  visibility?: VisibilityAdapter;
  // The URL of the page that owns an element, answered by the layer stack.
  // Absent, lazy and poll GETs read the address bar.
  pageUrl?: (el: Element) => string;
  // The confirm gate. Absent, the default calls window.confirm. Injectable so
  // tests drive accept and cancel without a real dialog.
  confirm?: ConfirmAdapter;
  // Dev builds warn on a hand-written data-next-* value outside its closed set,
  // which the runtime would otherwise drop in silence. Injectable so tests
  // assert both the warn-on and the silent-off behaviour.
  dev?: boolean;
}

export interface Triggers {
  // Bind the delegated listeners once, the same lifecycle as the layer stack.
  install(doc: Document): () => void;
  // Fire the batched load zones of the document on `ready`.
  ready(): void;
  // Activate the lazy zones and arm the sentinels inside a freshly inserted
  // subtree, run from the mount registry after every apply.
  scan(root: ParentNode): void;
  _reset(): void;
}

export function createTriggers(deps: TriggerDeps): Triggers {
  const doc = deps.document ?? document;
  const clock = deps.clock ?? defaultClock();
  const observer = deps.observer ?? defaultObserver();
  const confirm = deps.confirm ?? defaultConfirm();
  const visibility = deps.visibility ?? defaultVisibility();
  const dev = deps.dev ?? false;
  // Per-element debounce handles, keyed by the element itself.
  const timers = new WeakMap<Element, number>();
  // Lazy zones already activated, so a parent morph that re-inserts the same
  // zone element does not fire a second GET for it.
  const activated = new WeakSet<Element>();
  // Observer teardowns, dropped on reset so vitest files do not leak observers.
  const observed: (() => void)[] = [];
  // Poller groups keyed by interval, plus each element's group index so a
  // re-scan never arms a second timer and _reset can stop every chain.
  const groups = new Map<number, PollGroup>();
  const membership = new Map<Element, number>();
  let detach: (() => void) | null = null;

  function here(): string {
    return currentUrl(doc);
  }

  const pageUrl = deps.pageUrl ?? (() => here());

  // The abortable zone key of a form's inline validation, shared by the sender
  // and the submit canceller so they address the same queue.
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
  // with replaceState, no history entry: this is an address sync, not a visit.
  function submitFilter(form: HTMLFormElement, zone: string): void {
    // URLSearchParams takes string pairs, so the FormData is walked and file
    // fields are dropped, rather than casting the whole FormData past the check.
    const pairs: [string, string][] = [];
    for (const [name, value] of new FormData(form)) {
      if (typeof value === "string") pairs.push([name, value]);
    }
    const query = new URLSearchParams(pairs).toString();
    // An absent and an empty action both mean the current URL sans query.
    const attr = form.getAttribute("action");
    const action = attr === null || attr === "" ? here().replace(/\?.*$/, "") : attr;
    const url = query === "" ? action : `${action}?${query}`;
    doc.defaultView?.history.replaceState(null, "", url);
    zoneGet(url, zone);
  }

  // The one shape of a zone GET. The wire stamps X-Next-Zone from request.zone
  // and asserts the asset version once one is learned, so the triggers pass
  // intent, never headers.
  function zoneGet(url: string, zone: string): void {
    deps.fetch({ url, zone });
  }

  // A pagination link or sentinel GETs the next page with a merge intent, the
  // server authors the append patch with dedup.
  function paginate(el: Element, zone: string): void {
    const href = el.getAttribute("href");
    if (href === null || href === "") return;
    // Every caller (onClick and the sentinel activate) reaches paginate only via
    // a [data-next-merge] match, so the attribute is always present and the
    // "append" fallback is never taken.
    /* v8 ignore next */
    const merge = el.getAttribute(MERGE_ATTR) ?? "append";
    deps.fetch({ url: href, zone, headers: { [HEADER_MERGE]: merge } });
  }

  // Inline validation on blur. The FormData drops file fields so a multipart
  // form does not re-upload on every blur, the request carries the field name,
  // and the abortable zone queue collapses a burst of blurs to latest-wins.
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
      // The validate request rides a POST for its body but mutates nothing: it
      // is keyed by zone and abortable, never taking the uid mutation lock, so a
      // fresh blur or a submit of the same form aborts it through the queue.
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
    // A submit cancels its own in-flight validation: the wire aborts the
    // validate zone so a late answer never morphs the form the server is about
    // to re-render from the submit.
    deps.abort(validateZone(uid));
    // Intercept the submit as a partial mutation under the uid lock. Without
    // the runtime the form posts natively for the full post-then-redirect
    // cycle, so this is the enhancement and never the only path.
    event.preventDefault();
    const body = new FormData(form);
    // A native submit carries the pressed button, so a Continue or a Back name
    // reaches the server: replay it onto the data the engine sends.
    const submitter = (event as SubmitEvent).submitter;
    const name = submitter?.getAttribute("name") ?? "";
    if (name !== "") body.append(name, submitter?.getAttribute("value") ?? "");
    // The zone the form declares travels as the morph target, so an invalid
    // submit repaints that zone and a wizard step advances it in place. Without
    // one the server falls back to the form by uid.
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
      // closest matched on the attribute's presence, so getAttribute returns a
      // string (empty at worst) and the "" fallback is never taken.
      /* v8 ignore next */
      const text = confirmer.getAttribute(CONFIRM_ATTR) ?? "";
      if (!confirm(text)) {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
    }
    const link = el.closest(`a[${MERGE_ATTR}][${TARGET_ATTR}]`);
    // The selector restricts the match to <a> elements, which are always
    // HTMLAnchorElement, so the narrowing guard's false branch never runs. The
    // instanceof stays for the type narrowing the closure below relies on.
    /* v8 ignore next */
    if (link instanceof HTMLAnchorElement) {
      const zone = link.getAttribute(TARGET_ATTR);
      if (zone !== null && zone !== "") {
        event.preventDefault();
        paginate(link, zone);
      }
    }
  }

  // Activate one lazy zone or sentinel. load fires straight away (its batch is
  // handled on ready), revealed waits for the observer, a sentinel arms its
  // observer to paginate.
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

  // The poll interval under a strict decimal grammar: parseInt("5s") is 5 and
  // would poll at 5ms, so only an all-digit value within the server tag's own
  // bounds is an interval.
  function pollMs(el: Element): number | null {
    const raw = el.getAttribute(POLL_ATTR);
    if (raw === null || !/^\d+$/.test(raw)) return null;
    const ms = Number(raw);
    return ms >= MIN_POLL_MS && ms <= MAX_POLL_MS ? ms : null;
  }

  // Chained setTimeout, no setInterval, so tests drive ticks one by one. On a
  // hidden tab the group registers sleeping and the visible flip wakes it.
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

  // Each element is re-read live: the server keeps data-next-poll on partial
  // responses, so a wrapper missing either attribute was morphed away and
  // tears down, and a changed interval migrates after this fetch. A group
  // already gone (_reset won the race) returns silently.
  function pollTick(interval: number): void {
    const group = groups.get(interval);
    if (group === undefined) return;
    if (visibility.hidden()) {
      // Safety net for an undelivered hidden visibilitychange: sleep instead
      // of rescheduling, the visible flip wakes the group.
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
      const url = pageUrl(el);
      const zones = batches.get(url);
      if (zones === undefined) batches.set(url, [zone]);
      else zones.push(zone);
      if (ms !== interval) {
        group.elements.delete(el);
        membership.delete(el);
        joinPoll(el, ms);
      }
    }
    for (const [url, zones] of batches) zoneGet(url, zones.join(","));
    group.lastFire = clock.now();
    if (group.elements.size === 0) {
      groups.delete(interval);
      return;
    }
    group.handle = clock.setTimeout(() => pollTick(interval), interval);
  }

  // On hidden, silence every live timer but keep the groups sleeping. On
  // visible, each group measures elapsed against its own lastFire: due ticks
  // run at once, the rest resume with the remaining time. Live handles are
  // cleared first so a repeated visible event cannot stack a second chain.
  // Entries are copied since a tick may delete its group mid-walk.
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

  // Batch the load zones into one comma-joined GET per owning page, one round
  // trip for every load zone the page contributed. The batch groups by the
  // layer-resolved page URL: a base-page zone and a layer zone activated by
  // the same apply GET their own pages, not whatever the address bar reads.
  function loadBatch(root: ParentNode): void {
    const batches = new Map<string, string[]>();
    for (const el of matching(root, `[${LAZY_ATTR}="load"]`)) {
      if (activated.has(el)) continue;
      const zone = el.getAttribute(ATTR_ZONE);
      if (zone === null) continue;
      activated.add(el);
      const url = pageUrl(el);
      const zones = batches.get(url);
      if (zones === undefined) batches.set(url, [zone]);
      else zones.push(zone);
    }
    // The wire queues per path and zone batch, so a re-fired batch supersedes
    // its own page's predecessor and never another page's.
    for (const [url, zones] of batches) zoneGet(url, zones.join(","));
  }

  // The attribute of an element matched by an attribute selector, so the null
  // arm cannot occur and stays out of the branch count.
  function attrOf(el: Element, name: string): string {
    /* v8 ignore next */
    return el.getAttribute(name) ?? "";
  }

  // Warn on hand-written values the runtime drops in silence: an attribute
  // outside its closed set, a poll interval failing the grammar or bounds, a
  // poll element naming no zone. Dev-only.
  function validateAttrs(root: ParentNode): void {
    if (!dev) return;
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
      // Only sentinels (non-anchor or marked) arm an observer, plain pagination
      // links stay click-driven and are skipped here.
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
    // The visibility subscription pauses and resumes the poll timers, torn
    // down with the event listeners, the same choreography as the SSE bridge.
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
