// The zone poller behind data-next-poll. Zones with the same interval share one timer
// chain and one batched GET per owning page, and a hidden tab keeps no timers. It
// ships in next.poll.min.js, which the runtime fetches once a scan finds a poll zone.

import { ATTR_POLL, ATTR_ZONE, addZone, pollInterval } from "./protocol";
import type { VisibilityAdapter } from "./sse";
import type { Clock } from "./wire";

// The zones polled at one interval. lastFire is the reference point of a resume, and
// a null handle means the group is paused.
interface PollGroup {
  handle: number | null;
  lastFire: number;
  elements: Set<Element>;
}

/** The dependencies the triggers pass to the poller. */
export interface PollDeps {
  clock: Clock;
  visibility: VisibilityAdapter;
  // The owning page of a zone, the URL its tick GETs.
  pageUrl: (el: Element) => string;
  // One zone GET, several zones joined by commas.
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
  // Poll groups by interval, so a re-scan starts no second timer.
  const groups = new Map<number, PollGroup>();
  // Only answers whether an element already polls, so it holds elements weakly.
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

  // Re-reads each element. One that left the document or lost either attribute is
  // dropped, one with a changed interval moves group, and a deleted group does nothing.
  function pollTick(interval: number): void {
    const group = groups.get(interval);
    if (group === undefined) return;
    if (visibility.hidden()) {
      // Covers a missed visibilitychange. The next visible change resumes the group.
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
    // When hidden, clears every timer. When visible, runs each group whose interval
    // has elapsed since lastFire and schedules the others for the remaining time.
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
