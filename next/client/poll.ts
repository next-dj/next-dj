// The zone poller behind data-next-poll. Every zone on one cadence rides one timer
// chain and one batched GET per owning page, and a hidden tab holds no timers. It
// ships in next.poll.min.js, which the runtime fetches once a scan finds a poll zone.

import { ATTR_POLL, ATTR_ZONE, addZone, pollInterval } from "./protocol";
import type { VisibilityAdapter } from "./sse";
import type { Clock } from "./wire";

// One interval group of the poller, where every zone on the cadence rides one timer
// chain and one batched GET. lastFire anchors the resume, a null handle sleeps.
interface PollGroup {
  handle: number | null;
  lastFire: number;
  elements: Set<Element>;
}

/** The seams the poller draws on, lent by the triggers. */
export interface PollDeps {
  clock: Clock;
  visibility: VisibilityAdapter;
  // The owning page of a zone, the URL its tick GETs.
  pageUrl: (el: Element) => string;
  // One zone GET, the zones comma-joined.
  fetch: (url: string, zone: string) => void;
}

/** The poller handle the triggers drive. */
export interface Poller {
  // Arm one element, a no-op for one already polling or without a valid interval.
  start(el: Element): void;
  // Pause on a hidden tab, resume the due ticks on a visible one.
  wake(): void;
  _reset(): void;
}

/** The poller builder the poll chunk hands the runtime. */
export type PollFactory = (deps: PollDeps) => Poller;

/** Build the poller over the given seams. */
export function createPoller(deps: PollDeps): Poller {
  const { clock, visibility, pageUrl } = deps;
  // Poll groups by interval, with each element's group so a re-scan arms no new timer.
  const groups = new Map<number, PollGroup>();
  // Membership only answers "already polling", so the elements are held weakly.
  let membership = new WeakSet<Element>();

  function pollMs(el: Element): number | null {
    return pollInterval(el.getAttribute(ATTR_POLL));
  }

  // Chained setTimeout, not setInterval, so tests drive ticks one by one.
  function joinPoll(el: Element, interval: number): void {
    membership.add(el);
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

  // Each element is re-read live, a wrapper missing either attribute was morphed
  // away and tears down, a changed interval migrates, a vanished group returns.
  function pollTick(interval: number): void {
    const group = groups.get(interval);
    if (group === undefined) return;
    if (visibility.hidden()) {
      // Safety net for a missed visibilitychange, the visible flip wakes the group.
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
    for (const [url, zones] of batches) deps.fetch(url, zones.join(","));
    group.lastFire = clock.now();
    if (group.elements.size === 0) {
      groups.delete(interval);
      return;
    }
    group.handle = clock.setTimeout(() => pollTick(interval), interval);
  }

  return {
    start(el) {
      if (membership.has(el)) return;
      if (el.getAttribute(ATTR_ZONE) === null) return;
      const ms = pollMs(el);
      if (ms === null) return;
      joinPoll(el, ms);
    },
    // On hidden, live timers are silenced and the groups sleep. On visible, elapsed
    // against lastFire runs due ticks at once and resumes the rest with the time left.
    wake() {
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
    },
    _reset() {
      for (const group of groups.values()) {
        if (group.handle !== null) clock.clearTimeout(group.handle);
      }
      groups.clear();
      membership = new WeakSet();
    },
  };
}
