import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createDirtyTracker } from "./dirty";

// The runtime never stamps by hand, a delegated listener does it off a real event.
function touch(el: Element, type = "input"): void {
  el.dispatchEvent(new Event(type, { bubbles: true }));
}

describe("createDirtyTracker", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("stamps a field dirty only after the request snapshot", () => {
    const tracker = createDirtyTracker();
    tracker.install(document);
    const a = document.createElement("input");
    const b = document.createElement("input");
    document.body.append(a, b);
    touch(a);
    const snapshot = tracker.snapshot();
    touch(b);
    const isDirty = tracker.isDirtySince(snapshot);
    expect(isDirty(b)).toBe(true);
    expect(isDirty(a)).toBe(false);
  });

  it("treats an untouched field as clean", () => {
    const tracker = createDirtyTracker();
    const fresh = document.createElement("input");
    expect(tracker.isDirtySince(0)(fresh)).toBe(false);
  });

  it("reports a touched element regardless of the snapshot", () => {
    const tracker = createDirtyTracker();
    tracker.install(document);
    const el = document.createElement("details");
    document.body.append(el);
    expect(tracker.isTouched(el)).toBe(false);
    touch(el, "toggle");
    expect(tracker.isTouched(el)).toBe(true);
  });

  it("stamps through delegated input, change, and toggle listeners", () => {
    const tracker = createDirtyTracker();
    tracker.install(document);
    const snapshot = tracker.snapshot();
    const input = document.createElement("input");
    const select = document.createElement("select");
    const details = document.createElement("details");
    document.body.append(input, select, details);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    select.dispatchEvent(new Event("change", { bubbles: true }));
    details.dispatchEvent(new Event("toggle"));
    const isDirty = tracker.isDirtySince(snapshot);
    expect(isDirty(input)).toBe(true);
    expect(isDirty(select)).toBe(true);
    expect(isDirty(details)).toBe(true);
  });

  it("re-installing detaches the previous listener", () => {
    const tracker = createDirtyTracker();
    tracker.install(document);
    tracker.install(document);
    const snapshot = tracker.snapshot();
    const input = document.createElement("input");
    document.body.append(input);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    expect(tracker.isDirtySince(snapshot)(input)).toBe(true);
  });

  it("ignores a non-element event target", () => {
    const tracker = createDirtyTracker();
    tracker.install(document);
    expect(() => document.dispatchEvent(new Event("input"))).not.toThrow();
  });

  it("honours an injected monotonic counter", () => {
    let n = 100;
    const tracker = createDirtyTracker({ next: () => (n += 10) });
    tracker.install(document);
    const el = document.createElement("input");
    document.body.append(el);
    touch(el);
    expect(tracker.snapshot()).toBe(110);
    expect(tracker.isDirtySince(100)(el)).toBe(true);
    expect(tracker.isDirtySince(110)(el)).toBe(false);
  });

  it("_reset clears stamps and the counter", () => {
    const tracker = createDirtyTracker();
    tracker.install(document);
    const el = document.createElement("input");
    document.body.append(el);
    touch(el);
    tracker._reset();
    expect(tracker.snapshot()).toBe(0);
    expect(tracker.isDirtySince(0)(el)).toBe(false);
  });

  it("_reset detaches the capture-phase listeners", () => {
    const tracker = createDirtyTracker();
    tracker.install(document);
    tracker._reset();
    const input = document.createElement("input");
    document.body.append(input);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    // The listener came off in _reset, so the event stamps nothing.
    expect(tracker.isDirtySince(0)(input)).toBe(false);
  });
});
