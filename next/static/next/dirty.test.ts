import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createDirtyTracker } from "./dirty";

describe("createDirtyTracker", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("stamps a field dirty only after the request snapshot", () => {
    const tracker = createDirtyTracker();
    const a = document.createElement("input");
    const b = document.createElement("input");
    tracker.stamp(a);
    const snapshot = tracker.snapshot();
    tracker.stamp(b);
    const isDirty = tracker.isDirtySince(snapshot);
    expect(isDirty(b)).toBe(true);
    expect(isDirty(a)).toBe(false);
  });

  it("treats an untouched field as clean", () => {
    const tracker = createDirtyTracker();
    const fresh = document.createElement("input");
    expect(tracker.isDirtySince(0)(fresh)).toBe(false);
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
    const el = document.createElement("input");
    tracker.stamp(el);
    expect(tracker.snapshot()).toBe(110);
    expect(tracker.isDirtySince(100)(el)).toBe(true);
    expect(tracker.isDirtySince(110)(el)).toBe(false);
  });

  it("_reset clears stamps and the counter", () => {
    const tracker = createDirtyTracker();
    const el = document.createElement("input");
    tracker.stamp(el);
    tracker._reset();
    expect(tracker.snapshot()).toBe(0);
    expect(tracker.isDirtySince(0)(el)).toBe(false);
  });
});
