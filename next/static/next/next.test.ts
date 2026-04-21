import { beforeEach, describe, expect, it } from "vitest";
import "./next";

type NextStatic = {
  context: Readonly<Record<string, unknown>>;
  _init(context: Record<string, unknown>): void;
  on(
    event: "ready" | "context-updated",
    listener: (payload: Record<string, unknown>) => void,
  ): () => void;
  use<T>(plugin: (next: NextStatic) => T): T;
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

  it("exposes an on method", () => {
    expect(typeof win.Next.on).toBe("function");
  });

  it("exposes a use method", () => {
    expect(typeof win.Next.use).toBe("function");
  });
});

describe("Next.on", () => {
  beforeEach(() => {
    win.Next._init({});
  });

  it("fires the ready listener on _init", () => {
    let called = 0;
    win.Next.on("ready", () => {
      called += 1;
    });
    called = 0;
    win.Next._init({ page: "home" });
    expect(called).toBe(1);
  });

  it("fires the context-updated listener on _init", () => {
    const received: Array<Record<string, unknown>> = [];
    win.Next.on("context-updated", (payload) => {
      received.push(payload);
    });
    win.Next._init({ user: "alice" });
    expect(received).toHaveLength(1);
    expect(received[0]).toEqual({ user: "alice" });
  });

  it("supports multiple listeners on the same event", () => {
    let a = 0;
    let b = 0;
    win.Next.on("ready", () => {
      a += 1;
    });
    win.Next.on("ready", () => {
      b += 1;
    });
    a = 0;
    b = 0;
    win.Next._init({});
    expect(a).toBe(1);
    expect(b).toBe(1);
  });

  it("returns an unsubscribe function that stops future dispatches", () => {
    let called = 0;
    const off = win.Next.on("ready", () => {
      called += 1;
    });
    called = 0;
    win.Next._init({});
    off();
    win.Next._init({});
    expect(called).toBe(1);
  });

  it("fires ready immediately for listeners registered after _init", () => {
    win.Next._init({ page: "home" });
    let received: Record<string, unknown> | null = null;
    win.Next.on("ready", (ctx) => {
      received = ctx;
    });
    expect(received).toEqual({ page: "home" });
  });

  it("does not replay context-updated for late subscribers", () => {
    win.Next._init({ page: "home" });
    let called = 0;
    win.Next.on("context-updated", () => {
      called += 1;
    });
    expect(called).toBe(0);
  });
});

describe("Next.use", () => {
  beforeEach(() => {
    win.Next._init({});
  });

  it("calls the plugin with the Next namespace", () => {
    let seen: unknown = null;
    win.Next.use((next) => {
      seen = next;
    });
    expect(seen).toBe(win.Next);
  });

  it("returns the plugin's return value", () => {
    const result = win.Next.use(() => "hello");
    expect(result).toBe("hello");
  });

  it("lets plugins subscribe to events", () => {
    let triggered = false;
    win.Next.use((next) => {
      next.on("ready", () => {
        triggered = true;
      });
    });
    win.Next._init({});
    expect(triggered).toBe(true);
  });
});
