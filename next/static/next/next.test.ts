import { beforeEach, describe, expect, it } from "vitest";
import "./next";

type NextStatic = {
  context: Readonly<Record<string, unknown>>;
  _init(context: Record<string, unknown>): void;
};

const win = globalThis as unknown as { Next: NextStatic };

describe("Next._init", () => {
  beforeEach(() => {
    win.Next._init({});
  });

  it("stores values accessible via Next.context", () => {
    win.Next._init({ page: "home" });
    expect(win.Next.context.page).toBe("home");
  });

  it("replaces the entire context on each call", () => {
    win.Next._init({ old: "first" });
    win.Next._init({ new: "second" });
    expect(win.Next.context).not.toHaveProperty("old");
    expect(win.Next.context.new).toBe("second");
  });

  it("accepts an empty object and clears previous context", () => {
    win.Next._init({ key: "value" });
    win.Next._init({});
    expect(win.Next.context).toEqual({});
  });

  it("accepts nested objects", () => {
    win.Next._init({ meta: { page: "home", version: "1" } });
    expect(win.Next.context.meta).toEqual({ page: "home", version: "1" });
  });

  it("accepts all JSON primitive types", () => {
    win.Next._init({
      str: "text",
      num: 42,
      flag: true,
      arr: [1, 2],
      nil: null,
    });
    expect(win.Next.context.str).toBe("text");
    expect(win.Next.context.num).toBe(42);
    expect(win.Next.context.flag).toBe(true);
    expect(win.Next.context.arr).toEqual([1, 2]);
    expect(win.Next.context.nil).toBeNull();
  });
});

describe("Next.context", () => {
  beforeEach(() => {
    win.Next._init({});
  });

  it("returns a frozen object", () => {
    win.Next._init({ key: "val" });
    expect(Object.isFrozen(win.Next.context)).toBe(true);
  });

  it("returns a new object on each property access", () => {
    win.Next._init({ key: "val" });
    const first = win.Next.context;
    const second = win.Next.context;
    expect(first).not.toBe(second);
  });

  it("mutations to the returned object do not affect stored context", () => {
    win.Next._init({ key: "original" });
    const snap = win.Next.context as Record<string, unknown>;
    try {
      snap.key = "mutated";
    } catch {
      /* frozen */
    }
    expect(win.Next.context.key).toBe("original");
  });

  it("is empty after _init with an empty object", () => {
    win.Next._init({ key: "value" });
    win.Next._init({});
    expect(Object.keys(win.Next.context)).toHaveLength(0);
  });
});

describe("window.Next", () => {
  it("is assigned on globalThis", () => {
    expect(win.Next).toBeDefined();
  });

  it("exposes a context getter", () => {
    expect(typeof win.Next.context).toBe("object");
    expect(win.Next.context).not.toBeNull();
  });

  it("exposes a _init method", () => {
    expect(typeof win.Next._init).toBe("function");
  });
});
