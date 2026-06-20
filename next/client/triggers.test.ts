import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createTriggers } from "./triggers";
import type { IntersectionAdapter, TriggerDeps, Triggers } from "./triggers";
import type { Clock } from "./wire";

// The captured request is exactly what the runtime hands its fetch seam, so the
// assertions read the same shape (carrying abortable) the triggers emit.
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
    version: () => "v1",
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
    expect(requests[0].headers?.["X-Next-Zone"]).toBe("a,b");
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
    expect(requests[0].zone).toBe("results");
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
    expect(requests[0].zone).toBe("late");
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
    expect(requests[0].headers?.["X-Next-Merge"]).toBe("append");
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
    expect(requests[0].method).toBe("POST");
    expect(requests[0].headers?.["X-Next-Validate"]).toBe("email");
    expect(requests[0].zone).toBe("validate:u");
    expect(requests[0].abortable).toBe(true);
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
    expect(requests[0].method).toBe("POST");
    expect(requests[0].uid).toBe("u");
    expect(requests[0].zone).toBe("wizard");
    const body = requests[0].body as FormData;
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
    expect(requests[0].key).toBe("row-7");
  });

  it("omits the key for a form without one", () => {
    document.body.innerHTML =
      '<form action="/_next/form/u/" data-next-action="u"></form>';
    const { triggers, requests } = makeTriggers();
    detach = triggers.install(document);
    document
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    expect(requests[0].key).toBeUndefined();
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
    expect(requests[0].headers?.["X-Next-Merge"]).toBe("append");
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
    expect(requests[0].url).toBe("/c/?q=x");
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
    expect(requests[0].url).toBe("/catalog/?q=z");
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
    const body = requests[0].body as FormData;
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
    expect(requests[0].zone).toBe("validate:");
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
    expect(requests[0].url).toBe("/here/");
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
    expect(requests[0].url).toBe("/c/");
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
    const body = requests[0].body as FormData;
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
    expect(requests[0].url).toBe("/c/");
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
