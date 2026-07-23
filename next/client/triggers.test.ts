import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { manualPollClock, manualVisibility } from "./test-doubles";
import { createTriggers } from "./triggers";
import type { IntersectionAdapter, TriggerDeps, Triggers } from "./triggers";
import type { Clock } from "./wire";

// The captured request is exactly the shape the triggers hand their fetch seam.
type Request = Parameters<TriggerDeps["fetch"]>[0];

function manualClock(): Clock & { run(): void } {
  let pending: (() => void) | null = null;
  return {
    now: () => 0,
    setTimeout: (handler) => {
      pending = handler;
      return 1;
    },
    clearTimeout: () => {
      pending = null;
    },
    run() {
      const h = pending;
      pending = null;
      h?.();
    },
  };
}

function manualObserver(): IntersectionAdapter & { reveal(): void } {
  let cb: (() => void) | null = null;
  return {
    observe: (_el, onReveal) => {
      cb = onReveal;
      return () => {
        cb = null;
      };
    },
    reveal() {
      cb?.();
    },
  };
}

function makeTriggers(over: Partial<Parameters<typeof createTriggers>[0]> = {}): {
  triggers: Triggers;
  requests: Request[];
  aborted: string[];
} {
  const requests: Request[] = [];
  const aborted: string[] = [];
  const triggers = createTriggers({
    fetch: (request) => requests.push(request),
    abort: (zone) => aborted.push(zone),
    document,
    clock: manualClock(),
    observer: manualObserver(),
    confirm: () => true,
    ...over,
  });
  return { triggers, requests, aborted };
}

describe("trigger delegation", () => {
  let detach: () => void;

  beforeEach(() => {
    document.body.innerHTML = "";
  });

  afterEach(() => {
    detach?.();
    window.history.replaceState(null, "", "/");
  });

  it("batches load zones into one GET on ready", () => {
    document.body.innerHTML =
      '<div data-next-zone="a" data-next-lazy="load"></div>' +
      '<div data-next-zone="b" data-next-lazy="load"></div>';
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    triggers.ready();
    expect(requests).toHaveLength(1);
    expect(requests[0]!.zone).toBe("a,b");
    expect(requests[0]!.headers).toBeUndefined();
  });

  it("groups a load batch by the owning page URL, one GET per page", () => {
    document.body.innerHTML =
      '<div id="a" data-next-zone="a" data-next-lazy="load"></div>' +
      '<div id="b" data-next-zone="b" data-next-lazy="load"></div>' +
      '<div id="c" data-next-zone="c" data-next-lazy="load"></div>';
    const { triggers, requests } = makeTriggers({
      pageUrl: (el) => (el.id === "c" ? "/layer/" : "/host/"),
    });
    detach = triggers.install(document);
    triggers.ready();
    expect(requests).toHaveLength(2);
    expect(requests[0]).toMatchObject({ url: "/host/", zone: "a,b" });
    expect(requests[1]).toMatchObject({ url: "/layer/", zone: "c" });
  });

  it("does not re-fire a load zone re-scanned by a parent morph", () => {
    document.body.innerHTML = '<div data-next-zone="a" data-next-lazy="load"></div>';
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    triggers.scan(document.body);
    triggers.scan(document.body);
    expect(requests).toHaveLength(1);
  });

  it("debounces a filter auto-submit per element", () => {
    const clock = manualClock();
    document.body.innerHTML =
      '<form action="/c/" data-next-target="results">' +
      '<input name="q" data-next-trigger="input" data-next-debounce="300">' +
      "</form>";
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    const input = document.querySelector("input")!;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    expect(requests).toHaveLength(0);
    clock.run();
    expect(requests).toHaveLength(1);
    expect(requests[0]!.zone).toBe("results");
  });

  it("reveals through the observer adapter", () => {
    const observer = manualObserver();
    document.body.innerHTML =
      '<div data-next-zone="late" data-next-lazy="revealed"></div>';
    const { triggers, requests } = makeTriggers({ observer });
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(requests).toHaveLength(0);
    observer.reveal();
    expect(requests).toHaveLength(1);
    expect(requests[0]!.zone).toBe("late");
  });

  it("a revealed zone GETs the page answered by the pageUrl dep", () => {
    const observer = manualObserver();
    document.body.innerHTML =
      '<div data-next-zone="late" data-next-lazy="revealed"></div>';
    const { triggers, requests } = makeTriggers({
      observer,
      pageUrl: () => "/host/",
    });
    detach = triggers.install(document);
    triggers.scan(document.body);
    observer.reveal();
    expect(requests[0]!.url).toBe("/host/");
  });

  it("blocks a request when confirm is cancelled", () => {
    document.body.innerHTML =
      '<a href="/p2/" data-next-merge="append" data-next-target="list" ' +
      'data-next-confirm="sure?">more</a>';
    const { triggers, requests } = makeTriggers({ confirm: () => false });
    detach = triggers.install(document);
    document
      .querySelector("a")!
      .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(requests).toHaveLength(0);
  });

  it("paginates with a merge header on a click", () => {
    document.body.innerHTML =
      '<a href="/p2/" data-next-merge="append" data-next-target="list">more</a>';
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document
      .querySelector("a")!
      .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(requests).toHaveLength(1);
    expect(requests[0]!.headers?.["X-Next-Merge"]).toBe("append");
  });

  it("validates on blur with a field header and no file fields", () => {
    document.body.innerHTML =
      '<form action="/_next/form/u/" data-next-validate="blur" data-next-action="u">' +
      '<input name="email" value="a@b.c">' +
      "</form>";
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    const input = document.querySelector("input")!;
    input.dispatchEvent(new FocusEvent("blur"));
    expect(requests).toHaveLength(1);
    expect(requests[0]!.method).toBe("POST");
    expect(requests[0]!.headers?.["X-Next-Validate"]).toBe("email");
    expect(requests[0]!.zone).toBe("validate:u");
    expect(requests[0]!.abortable).toBe(true);
  });

  it("aborts the in-flight validation when the form submits", () => {
    document.body.innerHTML =
      '<form action="/_next/form/u/" data-next-validate="blur" data-next-action="u">' +
      '<input name="email" value="a@b.c">' +
      "</form>";
    const { triggers, aborted } = makeTriggers();
    detach = triggers.install(document);
    const form = document.querySelector("form")!;
    document.querySelector("input")!.dispatchEvent(new FocusEvent("blur"));
    form.dispatchEvent(new Event("submit", { bubbles: true }));
    expect(aborted).toEqual(["validate:u"]);
  });

  it("intercepts a next-action submit as a partial post carrying the zone", () => {
    document.body.innerHTML =
      '<form action="/_next/form/u/" data-next-action="u" data-next-target="wizard">' +
      '<input name="email" value="a@b.c">' +
      '<button type="submit" name="advance" value="next">Continue</button>' +
      "</form>";
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    const form = document.querySelector("form")!;
    const event = new Event("submit", { bubbles: true, cancelable: true });
    Object.defineProperty(event, "submitter", { value: form.querySelector("button") });
    form.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(requests).toHaveLength(1);
    expect(requests[0]!.method).toBe("POST");
    expect(requests[0]!.uid).toBe("u");
    expect(requests[0]!.zone).toBe("wizard");
    const body = requests[0]!.body as FormData;
    expect(body.get("email")).toBe("a@b.c");
    expect(body.get("advance")).toBe("next");
  });

  it("carries the form's data-next-key so the response morphs that instance", () => {
    document.body.innerHTML =
      '<form action="/_next/form/u/" data-next-action="u" data-next-key="row-7">' +
      "</form>";
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    expect(requests[0]!.key).toBe("row-7");
  });

  it("omits the key for a form without one", () => {
    document.body.innerHTML =
      '<form action="/_next/form/u/" data-next-action="u"></form>';
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    expect(requests[0]!.key).toBeUndefined();
  });

  it("leaves a form without a next action to submit natively", () => {
    document.body.innerHTML = '<form action="/x/"><input name="q"></form>';
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    const event = new Event("submit", { bubbles: true, cancelable: true });
    document.querySelector("form")!.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
    expect(requests).toHaveLength(0);
  });

  it("arms an infinite-scroll sentinel observer", () => {
    const observer = manualObserver();
    document.body.innerHTML =
      '<a href="/p2/" data-next-merge="append" data-next-target="list" ' +
      'data-next-lazy="revealed">sentinel</a>';
    const { triggers, requests } = makeTriggers({ observer });
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(requests).toHaveLength(0);
    observer.reveal();
    expect(requests).toHaveLength(1);
    expect(requests[0]!.headers?.["X-Next-Merge"]).toBe("append");
  });

  it("ignores an input event whose trigger names a different type", () => {
    document.body.innerHTML =
      '<form action="/c/" data-next-target="r">' +
      '<input name="q" data-next-trigger="change">' +
      "</form>";
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document
      .querySelector("input")!
      .dispatchEvent(new Event("input", { bubbles: true }));
    expect(requests).toHaveLength(0);
  });

  it("ignores a filter trigger with no target zone", () => {
    document.body.innerHTML =
      '<form action="/c/"><input name="q" data-next-trigger="input"></form>';
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document
      .querySelector("input")!
      .dispatchEvent(new Event("input", { bubbles: true }));
    expect(requests).toHaveLength(0);
  });

  it("submits a filter immediately when no debounce is set", () => {
    document.body.innerHTML =
      '<form action="/c/" data-next-target="r">' +
      '<input name="q" value="x" data-next-trigger="input">' +
      "</form>";
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document
      .querySelector("input")!
      .dispatchEvent(new Event("input", { bubbles: true }));
    expect(requests).toHaveLength(1);
    expect(requests[0]!.url).toBe("/c/?q=x");
  });

  it("syncs the address bar with replaceState, not a history entry", () => {
    document.body.innerHTML =
      '<form action="/c/" data-next-target="r">' +
      '<input name="q" value="x" data-next-trigger="input">' +
      "</form>";
    const replaceState = vi.spyOn(window.history, "replaceState");
    const pushState = vi.spyOn(window.history, "pushState");
    const { triggers } = makeTriggers();
    detach = triggers.install(document);
    document
      .querySelector("input")!
      .dispatchEvent(new Event("input", { bubbles: true }));
    expect(replaceState).toHaveBeenCalledWith(null, "", "/c/?q=x");
    expect(pushState).not.toHaveBeenCalled();
    replaceState.mockRestore();
    pushState.mockRestore();
  });

  it("falls back to the current path when a filter form has no action", () => {
    window.history.replaceState(null, "", "/catalog/?old=1");
    document.body.innerHTML =
      '<form data-next-target="r">' +
      '<input name="q" value="z" data-next-trigger="input">' +
      "</form>";
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document
      .querySelector("input")!
      .dispatchEvent(new Event("input", { bubbles: true }));
    expect(requests[0]!.url).toBe("/catalog/?q=z");
  });

  it("skips a blur with no field name", () => {
    document.body.innerHTML =
      '<form action="/f/" data-next-validate="blur" data-next-action="u">' +
      "<input>" +
      "</form>";
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document.querySelector("input")!.dispatchEvent(new FocusEvent("blur"));
    expect(requests).toHaveLength(0);
  });

  it("strips file fields from the validate body", () => {
    document.body.innerHTML =
      '<form action="/f/" data-next-validate="blur" data-next-action="u">' +
      '<input name="doc" type="file">' +
      '<input name="email" value="a@b.c">' +
      "</form>";
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document
      .querySelector('input[name="email"]')!
      .dispatchEvent(new FocusEvent("blur"));
    const body = requests[0]!.body as FormData;
    expect(body.has("doc")).toBe(false);
    expect(body.get("email")).toBe("a@b.c");
  });

  it("does not paginate a link with an empty href", () => {
    document.body.innerHTML =
      '<a href="" data-next-merge="append" data-next-target="list">more</a>';
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document
      .querySelector("a")!
      .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(requests).toHaveLength(0);
  });

  it("paginates after an accepted confirm", () => {
    document.body.innerHTML =
      '<a href="/p2/" data-next-merge="append" data-next-target="list" ' +
      'data-next-confirm="ok?">more</a>';
    const { triggers, requests } = makeTriggers({ confirm: () => true });
    detach = triggers.install(document);
    document
      .querySelector("a")!
      .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(requests).toHaveLength(1);
  });

  it("handles a submit of a form that never validated", () => {
    document.body.innerHTML =
      '<form data-next-validate="blur" data-next-action="u"></form>';
    const { triggers } = makeTriggers();
    detach = triggers.install(document);
    const form = document.querySelector("form")!;
    expect(() =>
      form.dispatchEvent(new Event("submit", { bubbles: true })),
    ).not.toThrow();
  });

  it("clears the pending timer when a fresh event arrives mid-debounce", () => {
    const clock = manualClock();
    document.body.innerHTML =
      '<form action="/c/" data-next-target="r">' +
      '<input name="q" data-next-trigger="input" data-next-debounce="300">' +
      "</form>";
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    const input = document.querySelector("input")!;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("input", { bubbles: true }));
    clock.run();
    expect(requests).toHaveLength(1);
  });

  it("skips a load zone element with no zone name", () => {
    document.body.innerHTML = '<div data-next-lazy="load"></div>';
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    triggers.ready();
    expect(requests).toHaveLength(0);
  });

  it("does not arm a plain pagination link as a sentinel on scan", () => {
    document.body.innerHTML =
      '<a href="/p2/" data-next-merge="append" data-next-target="list">more</a>';
    const observer = manualObserver();
    const { triggers, requests } = makeTriggers({ observer });
    detach = triggers.install(document);
    triggers.scan(document.body);
    observer.reveal();
    expect(requests).toHaveLength(0);
  });

  it("ignores delegated events whose target is not an element", () => {
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document.dispatchEvent(new Event("input", { bubbles: true }));
    document.dispatchEvent(new FocusEvent("blur"));
    document.dispatchEvent(new Event("submit", { bubbles: true }));
    document.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(requests).toHaveLength(0);
  });

  it("ignores an input event on an element outside any trigger", () => {
    document.body.innerHTML = "<div><span>plain</span></div>";
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document
      .querySelector("span")!
      .dispatchEvent(new Event("input", { bubbles: true }));
    expect(requests).toHaveLength(0);
  });

  it("ignores a filter trigger that sits outside a form", () => {
    document.body.innerHTML =
      '<div data-next-target="r"><input data-next-trigger="input"></div>';
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document
      .querySelector("input")!
      .dispatchEvent(new Event("input", { bubbles: true }));
    expect(requests).toHaveLength(0);
  });

  it("ignores a blur on an element outside any validate form", () => {
    document.body.innerHTML = '<form action="/x/"><input name="q"></form>';
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document.querySelector("input")!.dispatchEvent(new FocusEvent("blur"));
    expect(requests).toHaveLength(0);
  });

  it("keys the validate zone empty when the form carries no action uid", () => {
    document.body.innerHTML =
      '<form action="/f/" data-next-validate="blur">' +
      '<input name="email" value="a@b.c">' +
      "</form>";
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document.querySelector("input")!.dispatchEvent(new FocusEvent("blur"));
    expect(requests[0]!.zone).toBe("validate:");
  });

  it("falls back to the current path when a validate form has no action", () => {
    window.history.replaceState(null, "", "/here/");
    document.body.innerHTML =
      '<form data-next-validate="blur" data-next-action="u">' +
      '<input name="email" value="a@b.c">' +
      "</form>";
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document.querySelector("input")!.dispatchEvent(new FocusEvent("blur"));
    expect(requests[0]!.url).toBe("/here/");
  });

  it("submits a filter to the bare action when the query is empty", () => {
    document.body.innerHTML =
      '<form action="/c/" data-next-target="r">' +
      '<input data-next-trigger="input">' +
      "</form>";
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document
      .querySelector("input")!
      .dispatchEvent(new Event("input", { bubbles: true }));
    expect(requests[0]!.url).toBe("/c/");
  });

  it("appends an empty submitter value when the pressed button has none", () => {
    document.body.innerHTML =
      '<form action="/_next/form/u/" data-next-action="u">' +
      '<button type="submit" name="advance">Go</button>' +
      "</form>";
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    const form = document.querySelector("form")!;
    const event = new Event("submit", { bubbles: true, cancelable: true });
    Object.defineProperty(event, "submitter", {
      value: form.querySelector("button"),
    });
    form.dispatchEvent(event);
    const body = requests[0]!.body as FormData;
    expect(body.get("advance")).toBe("");
  });

  it("drops a file field from a filter auto-submit query", () => {
    document.body.innerHTML =
      '<form action="/c/" data-next-target="r">' +
      '<input name="doc" type="file" data-next-trigger="input">' +
      "</form>";
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document
      .querySelector("input")!
      .dispatchEvent(new Event("input", { bubbles: true }));
    expect(requests[0]!.url).toBe("/c/");
  });

  it("skips a pagination link whose target zone is empty", () => {
    document.body.innerHTML =
      '<a href="/p2/" data-next-merge="append" data-next-target="">more</a>';
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document
      .querySelector("a")!
      .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(requests).toHaveLength(0);
  });

  it("arms no sentinel for a revealed element without a zone, merge, or target", () => {
    const observer = manualObserver();
    document.body.innerHTML =
      '<div data-next-lazy="revealed"></div>' +
      '<div data-next-lazy="revealed" data-next-merge="append"></div>';
    const { triggers, requests } = makeTriggers({ observer });
    detach = triggers.install(document);
    triggers.scan(document.body);
    observer.reveal();
    expect(requests).toHaveLength(0);
  });

  it("re-installs cleanly by detaching the previous binding first", () => {
    document.body.innerHTML =
      '<a href="/p2/" data-next-merge="append" data-next-target="list">more</a>';
    const { triggers, requests } = makeTriggers();
    const first = triggers.install(document);
    detach = triggers.install(document);
    document
      .querySelector("a")!
      .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(requests).toHaveLength(1);
    first();
  });

  it("_reset stops outstanding observers", () => {
    const observer = manualObserver();
    document.body.innerHTML =
      '<div data-next-zone="late" data-next-lazy="revealed"></div>';
    const { triggers, requests } = makeTriggers({ observer });
    detach = triggers.install(document);
    triggers.scan(document.body);
    triggers._reset();
    observer.reveal();
    expect(requests).toHaveLength(0);
  });
});

describe("zone polling", () => {
  let detach: () => void;

  beforeEach(() => {
    document.body.innerHTML = "";
  });

  afterEach(() => {
    detach?.();
  });

  it("re-GETs a polling zone on each tick", () => {
    const clock = manualPollClock();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(requests).toHaveLength(0);
    clock.tick();
    expect(requests).toHaveLength(1);
    expect(requests[0]!.zone).toBe("t");
    expect(requests[0]!.headers).toBeUndefined();
    clock.tick();
    expect(requests).toHaveLength(2);
  });

  it("batches same-interval zones into one comma-joined GET per tick", () => {
    const clock = manualPollClock();
    document.body.innerHTML =
      '<div data-next-zone="a" data-next-poll="5000"></div>' +
      '<div data-next-zone="b" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(clock.pending()).toBe(1);
    clock.tick();
    expect(requests).toHaveLength(1);
    expect(requests[0]!.zone).toBe("a,b");
    clock.tick();
    expect(requests).toHaveLength(2);
  });

  it("a same-interval batch still GETs one request per owning page", () => {
    const clock = manualPollClock();
    document.body.innerHTML =
      '<div id="a" data-next-zone="a" data-next-poll="5000"></div>' +
      '<div id="b" data-next-zone="b" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({
      clock,
      pageUrl: (el) => (el.id === "b" ? "/layer/" : "/host/"),
    });
    detach = triggers.install(document);
    triggers.scan(document.body);
    clock.tick();
    expect(requests).toHaveLength(2);
    expect(requests[0]).toMatchObject({ url: "/host/", zone: "a" });
    expect(requests[1]).toMatchObject({ url: "/layer/", zone: "b" });
  });

  it("a poller armed on a hidden tab sleeps and resumes as a single chain", () => {
    const clock = manualPollClock();
    const visibility = manualVisibility();
    // Hidden before install, so no visibilitychange ever clears a pending timer.
    visibility.setHidden(true);
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock, visibility });
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(clock.pending()).toBe(0);
    clock.tick();
    expect(requests).toHaveLength(0);
    clock.setNow(6000);
    visibility.setHidden(false);
    expect(requests).toHaveLength(1);
    expect(clock.pending()).toBe(1);
    clock.tick();
    expect(requests).toHaveLength(2);
    expect(clock.pending()).toBe(1);
  });

  it("a poller armed hidden and revealed early re-arms with the remaining time", () => {
    const clock = manualPollClock();
    const visibility = manualVisibility();
    visibility.setHidden(true);
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock, visibility });
    detach = triggers.install(document);
    triggers.scan(document.body);
    clock.setNow(3000);
    visibility.setHidden(false);
    expect(requests).toHaveLength(0);
    expect(clock.pending()).toBe(1);
    expect(clock.intervals).toEqual([2000]);
    clock.tick();
    expect(requests).toHaveLength(1);
  });

  it("a tick landing on a hidden tab puts the poller to sleep", () => {
    const clock = manualPollClock();
    const visibility = manualVisibility();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock, visibility });
    // No install, so the hidden flip delivers no visibilitychange and the armed
    // timer fires into the in-tick safety net.
    triggers.scan(document.body);
    visibility.setHidden(true);
    clock.tick();
    expect(requests).toHaveLength(0);
    expect(clock.pending()).toBe(0);
  });

  it("a repeated hidden flip tolerates a poller already sleeping", () => {
    const clock = manualPollClock();
    const visibility = manualVisibility();
    visibility.setHidden(true);
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock, visibility });
    detach = triggers.install(document);
    triggers.scan(document.body);
    visibility.setHidden(true);
    expect(clock.pending()).toBe(0);
    clock.setNow(6000);
    visibility.setHidden(false);
    expect(requests).toHaveLength(1);
    expect(clock.pending()).toBe(1);
  });

  it("a hidden flip silences the pending poll timer", () => {
    const clock = manualPollClock();
    const visibility = manualVisibility();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock, visibility });
    detach = triggers.install(document);
    triggers.scan(document.body);
    visibility.setHidden(true);
    clock.tick();
    expect(requests).toHaveLength(0);
    clock.tick();
    expect(requests).toHaveLength(0);
  });

  it("a long hide runs the poller tick immediately on the visible flip", () => {
    const clock = manualPollClock();
    const visibility = manualVisibility();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock, visibility });
    detach = triggers.install(document);
    triggers.scan(document.body);
    clock.tick();
    expect(requests).toHaveLength(1);
    visibility.setHidden(true);
    clock.setNow(6000);
    visibility.setHidden(false);
    expect(requests).toHaveLength(2);
    expect(clock.pending()).toBe(1);
    clock.tick();
    expect(requests).toHaveLength(3);
  });

  it("a brief flicker re-arms the timers without an immediate fetch", () => {
    const clock = manualPollClock();
    const visibility = manualVisibility();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock, visibility });
    detach = triggers.install(document);
    triggers.scan(document.body);
    clock.tick();
    expect(requests).toHaveLength(1);
    visibility.setHidden(true);
    clock.setNow(1000);
    visibility.setHidden(false);
    // The countdown resumes with the remaining time, so rapid switching cannot
    // postpone a due tick.
    expect(requests).toHaveLength(1);
    expect(clock.pending()).toBe(1);
    expect(clock.intervals).toEqual([5000, 5000, 4000]);
    clock.tick();
    expect(requests).toHaveLength(2);
  });

  it("a repeated visible event does not fork a second timer chain", () => {
    const clock = manualPollClock();
    const visibility = manualVisibility();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock, visibility });
    detach = triggers.install(document);
    triggers.scan(document.body);
    // A visible event with no intervening hidden clears the live handle before
    // re-arming, so a second one forks no chain.
    visibility.setHidden(false);
    visibility.setHidden(false);
    expect(clock.pending()).toBe(1);
    clock.tick();
    expect(requests).toHaveLength(1);
    expect(clock.pending()).toBe(1);
  });

  it("resume tears down a poller whose zone vanished while hidden", () => {
    const clock = manualPollClock();
    const visibility = manualVisibility();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock, visibility });
    detach = triggers.install(document);
    triggers.scan(document.body);
    visibility.setHidden(true);
    document.querySelector("div")!.removeAttribute("data-next-poll");
    visibility.setHidden(false);
    expect(requests).toHaveLength(0);
    clock.tick();
    expect(requests).toHaveLength(0);
  });

  it("stops the timer when the zone leaves the DOM", () => {
    const clock = manualPollClock();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const el = document.querySelector("div")!;
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    triggers.scan(document.body);
    el.remove();
    clock.tick();
    expect(requests).toHaveLength(0);
    clock.tick();
    expect(requests).toHaveLength(0);
  });

  it("stops the poller when the poll attribute is removed between ticks", () => {
    const clock = manualPollClock();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    triggers.scan(document.body);
    document.querySelector("div")!.removeAttribute("data-next-poll");
    clock.tick();
    expect(requests).toHaveLength(0);
    clock.tick();
    expect(requests).toHaveLength(0);
  });

  it("stops the poller when the zone attribute is removed between ticks", () => {
    const clock = manualPollClock();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    triggers.scan(document.body);
    document.querySelector("div")!.removeAttribute("data-next-zone");
    clock.tick();
    expect(requests).toHaveLength(0);
    clock.tick();
    expect(requests).toHaveLength(0);
  });

  it("a tick reschedules with the interval re-read from the live element", () => {
    const clock = manualPollClock();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(clock.intervals).toEqual([5000]);
    document.querySelector("div")!.setAttribute("data-next-poll", "9000");
    clock.tick();
    expect(requests).toHaveLength(1);
    expect(clock.intervals).toEqual([5000, 9000]);
    clock.tick();
    expect(requests).toHaveLength(2);
  });

  it("arms a poller when the scan root itself is the poll wrapper", () => {
    const clock = manualPollClock();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const el = document.querySelector("div")!;
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    triggers.scan(el);
    clock.tick();
    expect(requests).toHaveLength(1);
    expect(requests[0]!.zone).toBe("t");
  });

  it("fires the load batch when the scan root itself is the load wrapper", () => {
    document.body.innerHTML = '<div data-next-zone="a" data-next-lazy="load"></div>';
    const el = document.querySelector("div")!;
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    triggers.scan(el);
    expect(requests).toHaveLength(1);
    expect(requests[0]!.zone).toBe("a");
  });

  it("polls the URL answered by the pageUrl dep, not the address bar", () => {
    const clock = manualPollClock();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock, pageUrl: () => "/host/" });
    detach = triggers.install(document);
    triggers.scan(document.body);
    clock.tick();
    expect(requests[0]!.url).toBe("/host/");
  });

  it("ignores a hand-written duration literal under the strict grammar", () => {
    const clock = manualPollClock();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5s"></div>';
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    triggers.scan(document.body);
    clock.tick();
    expect(requests).toHaveLength(0);
  });

  it("does not arm a second timer on re-scan", () => {
    const clock = manualPollClock();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    triggers.scan(document.body);
    triggers.scan(document.body);
    clock.tick();
    expect(requests).toHaveLength(1);
  });

  it("_reset stops outstanding pollers", () => {
    const clock = manualPollClock();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    triggers.scan(document.body);
    triggers._reset();
    clock.tick();
    expect(requests).toHaveLength(0);
  });

  it("_reset drops a sleeping poller that holds no timer to clear", () => {
    const clock = manualPollClock();
    const visibility = manualVisibility();
    visibility.setHidden(true);
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock, visibility });
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(clock.pending()).toBe(0);
    triggers._reset();
    clock.setNow(6000);
    visibility.setHidden(false);
    expect(requests).toHaveLength(0);
    expect(clock.pending()).toBe(0);
  });

  it("a tick that outlived _reset returns without re-inserting its group", () => {
    // A no-op clearTimeout models a callback the browser already dequeued, so the
    // orphan tick must find its group gone and die silently.
    const handlers: (() => void)[] = [];
    const clock: Clock = {
      now: () => 0,
      setTimeout: (handler) => handlers.push(handler),
      clearTimeout: () => undefined,
    };
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    triggers.scan(document.body);
    triggers._reset();
    handlers[0]!();
    expect(requests).toHaveLength(0);
    expect(handlers).toHaveLength(1);
  });

  it("ignores a zone with a non-positive interval", () => {
    const clock = manualPollClock();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="0"></div>';
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    triggers.scan(document.body);
    clock.tick();
    expect(requests).toHaveLength(0);
  });

  it("ignores a sub-second interval under the tag's floor", () => {
    const clock = manualPollClock();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="500"></div>';
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(clock.pending()).toBe(0);
    clock.tick();
    expect(requests).toHaveLength(0);
  });

  it("ignores a non-numeric interval", () => {
    const clock = manualPollClock();
    document.body.innerHTML = '<div data-next-zone="t" data-next-poll="soon"></div>';
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    triggers.scan(document.body);
    clock.tick();
    expect(requests).toHaveLength(0);
  });

  it("ignores an interval above the browser's signed-32-bit timer bound", () => {
    const clock = manualPollClock();
    document.body.innerHTML =
      '<div data-next-zone="t" data-next-poll="2147483648"></div>';
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(clock.pending()).toBe(0);
    clock.tick();
    expect(requests).toHaveLength(0);
  });

  it("ignores a polling element without a zone name", () => {
    const clock = manualPollClock();
    document.body.innerHTML = '<div data-next-poll="5000"></div>';
    const { triggers, requests } = makeTriggers({ clock });
    detach = triggers.install(document);
    triggers.scan(document.body);
    clock.tick();
    expect(requests).toHaveLength(0);
  });
});

describe("dev attribute validation", () => {
  let detach: () => void;

  beforeEach(() => {
    document.body.innerHTML = "";
  });

  afterEach(() => {
    detach?.();
  });

  it("warns on an out-of-set data-next-lazy value in dev", () => {
    document.body.innerHTML = '<div data-next-zone="z" data-next-lazy="loaded"></div>';
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { triggers } = makeTriggers({ dev: true });
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0]![0]).toContain('data-next-lazy="loaded"');
    expect(warn.mock.calls[0]![0]).toContain("load, revealed");
    warn.mockRestore();
  });

  it("warns on an out-of-set data-next-merge value in dev", () => {
    document.body.innerHTML =
      '<a href="/p2/" data-next-merge="add" data-next-target="list">more</a>';
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { triggers } = makeTriggers({ dev: true });
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0]![0]).toContain('data-next-merge="add"');
    expect(warn.mock.calls[0]![0]).toContain("append, prepend");
    warn.mockRestore();
  });

  it("warns on a malformed data-next-poll value in dev", () => {
    document.body.innerHTML = '<div data-next-zone="z" data-next-poll="5s"></div>';
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { triggers } = makeTriggers({ dev: true });
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0]![0]).toContain('data-next-poll="5s"');
    expect(warn.mock.calls[0]![0]).toContain("whole number of milliseconds");
    warn.mockRestore();
  });

  it("warns on a poll interval above the timer bound in dev", () => {
    document.body.innerHTML =
      '<div data-next-zone="z" data-next-poll="2147483648"></div>';
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { triggers } = makeTriggers({ dev: true });
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0]![0]).toContain('data-next-poll="2147483648"');
    expect(warn.mock.calls[0]![0]).toContain("between 1000 and 2147483647");
    warn.mockRestore();
  });

  it("warns on a sub-second poll interval in dev, naming the floor", () => {
    document.body.innerHTML = '<div data-next-zone="z" data-next-poll="500"></div>';
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { triggers } = makeTriggers({ dev: true });
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0]![0]).toContain('data-next-poll="500"');
    expect(warn.mock.calls[0]![0]).toContain("between 1000 and 2147483647");
    warn.mockRestore();
  });

  it("warns on a valid poll interval whose element names no zone in dev", () => {
    document.body.innerHTML = '<div data-next-poll="5000"></div>';
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { triggers } = makeTriggers({ dev: true });
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0]![0]).toContain('data-next-poll="5000"');
    expect(warn.mock.calls[0]![0]).toContain("without data-next-zone");
    warn.mockRestore();
  });

  it("warns when the scan root itself carries the malformed attribute", () => {
    document.body.innerHTML = '<div data-next-zone="z" data-next-lazy="loaded"></div>';
    const el = document.querySelector("div")!;
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { triggers } = makeTriggers({ dev: true });
    detach = triggers.install(document);
    // A replace patch scans the new wrapper element itself, not only descendants.
    triggers.scan(el);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0]![0]).toContain('data-next-lazy="loaded"');
    warn.mockRestore();
  });

  it("stays silent on recognised values in dev", () => {
    document.body.innerHTML =
      '<div data-next-zone="z" data-next-lazy="load"></div>' +
      '<a href="/p2/" data-next-merge="append" data-next-target="list">more</a>' +
      '<div data-next-zone="p" data-next-poll="5000"></div>';
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { triggers } = makeTriggers({ dev: true });
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it("stays silent on an out-of-set value when dev is off", () => {
    document.body.innerHTML =
      '<div data-next-zone="z" data-next-lazy="loaded"></div>' +
      '<a href="/p2/" data-next-merge="add" data-next-target="list">more</a>' +
      '<div data-next-zone="p" data-next-poll="5s"></div>' +
      '<div data-next-poll="5000"></div>';
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { triggers } = makeTriggers();
    detach = triggers.install(document);
    triggers.scan(document.body);
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });
});
