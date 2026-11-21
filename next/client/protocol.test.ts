import { describe, expect, it } from "vitest";
import {
  ATTR_BUSY,
  MAX_TIMEOUT_MS,
  REQUEST_TIMEOUT_MS,
  duration,
  isBusy,
  markBusy,
  now,
  requestTimeout,
  stamp,
  stamped,
  streaming,
} from "./protocol";

describe("requestTimeout", () => {
  it("keeps a timer-range value and zero, which turns the deadline off", () => {
    expect(requestTimeout(5000)).toBe(5000);
    expect(requestTimeout(0)).toBe(0);
    expect(requestTimeout(MAX_TIMEOUT_MS)).toBe(MAX_TIMEOUT_MS);
  });

  it.each([[undefined], ["5000"], [-1], [Number.NaN], [MAX_TIMEOUT_MS + 1]])(
    "falls back to the default for %s",
    (raw) => {
      expect(requestTimeout(raw)).toBe(REQUEST_TIMEOUT_MS);
    },
  );
});

describe("markBusy", () => {
  const busy = (el: Element): boolean =>
    el.hasAttribute(ATTR_BUSY) && el.getAttribute("aria-busy") === "true";

  it("marks each element and skips an unresolved one", () => {
    const zone = document.createElement("div");
    const release = markBusy([zone, null]);
    expect(busy(zone)).toBe(true);
    release();
    expect(zone.hasAttribute(ATTR_BUSY)).toBe(false);
    expect(zone.hasAttribute("aria-busy")).toBe(false);
  });

  it("keeps the mark until the last overlapping hold is released", () => {
    const zone = document.createElement("div");
    const first = markBusy([zone]);
    const second = markBusy([zone]);
    first();
    expect(busy(zone)).toBe(true);
    second();
    expect(busy(zone)).toBe(false);
  });

  it("leaves aria-busy alone for a quiet hold", () => {
    const zone = document.createElement("div");
    const release = markBusy([zone], true);
    expect(zone.hasAttribute(ATTR_BUSY)).toBe(true);
    expect(zone.hasAttribute("aria-busy")).toBe(false);
    release();
    expect(zone.hasAttribute(ATTR_BUSY)).toBe(false);
  });

  it("releases a hold once however often its releaser runs", () => {
    const zone = document.createElement("div");
    const first = markBusy([zone]);
    const second = markBusy([zone]);
    first();
    first();
    expect(busy(zone)).toBe(true);
    expect(isBusy(zone)).toBe(true);
    second();
    expect(busy(zone)).toBe(false);
    expect(isBusy(zone)).toBe(false);
  });
});

describe("markBusy state", () => {
  const marks = (el: Element): (string | null)[] => [
    el.getAttribute("data-next-state"),
    el.getAttribute("data-next-error"),
  ];

  function element([state, error]: (string | null)[]): Element {
    const el = document.createElement("div");
    if (state) el.setAttribute("data-next-state", state);
    if (error) el.setAttribute("data-next-error", error);
    return el;
  }

  it.each([
    {
      found: ["idle", null],
      during: ["loading", null],
      fault: null,
      after: ["ready", null],
    },
    {
      found: [null, null],
      during: [null, null],
      fault: null,
      after: ["ready", null],
    },
    {
      found: ["ready", null],
      during: ["ready", null],
      fault: null,
      after: ["ready", null],
    },
    {
      found: ["ready", null],
      during: ["ready", null],
      fault: "network",
      after: ["error", "network"],
    },
    {
      found: ["error", "timeout"],
      during: ["loading", null],
      fault: null,
      after: ["ready", null],
    },
    {
      found: ["error", "timeout"],
      during: ["loading", null],
      fault: undefined,
      after: ["error", "timeout"],
    },
    {
      found: ["idle", null],
      during: ["loading", null],
      fault: undefined,
      after: ["idle", null],
    },
    {
      found: [null, null],
      during: [null, null],
      fault: undefined,
      after: [null, null],
    },
  ])("walks $found to $after on $fault", ({ found, during, fault, after }) => {
    const el = element(found);
    const release = markBusy([el]);
    expect(marks(el)).toEqual(during);
    release(fault);
    expect(marks(el)).toEqual(after);
  });

  it("keeps an error kind the reply wrote while the hold was open", () => {
    const el = element(["idle", null]);
    const release = markBusy([el]);
    el.setAttribute("data-next-error", "forbidden");
    release(null);
    expect(marks(el)).toEqual(["error", "forbidden"]);
  });

  it("writes on the last release, against the state the first hold found", () => {
    const el = element(["idle", null]);
    const first = markBusy([el]);
    const second = markBusy([el]);
    first("network");
    expect(marks(el)).toEqual(["loading", null]);
    second();
    expect(marks(el)).toEqual(["idle", null]);
  });

  it("clears a server-sent aria-busy once its loading element settles", () => {
    const el = element(["loading", null]);
    el.setAttribute("aria-busy", "true");
    markBusy([el])(null);
    expect(el.hasAttribute("aria-busy")).toBe(false);
    expect(marks(el)).toEqual(["ready", null]);
  });
});

describe("duration", () => {
  it.each([
    ["250", 250],
    ["250ms", 250],
    ["2s", 2000],
    ["15s", 15000],
    ["0", 0],
  ])("reads %s as %i milliseconds", (raw, ms) => {
    expect(duration(raw)).toBe(ms);
  });

  it.each([[null], [undefined], [""], ["soon"], ["-5"], ["5 s"], ["1.5s"]])(
    "reads %s as no duration",
    (raw) => {
      expect(duration(raw)).toBeUndefined();
    },
  );
});

describe("streaming", () => {
  it("tells a streamed page by the mark on its root", () => {
    expect(streaming(document)).toBe(false);
    document.body.setAttribute("data-next-streamed", "");
    expect(streaming(document)).toBe(true);
    document.body.removeAttribute("data-next-streamed");
  });
});

describe("stamp", () => {
  it("dates an element by the latest stamp on it or an ancestor", () => {
    document.body.innerHTML = '<div id="outer"><p id="inner"></p></div>';
    const outer = document.getElementById("outer")!;
    const inner = document.getElementById("inner")!;
    expect([stamped(inner), stamped(null)]).toEqual([0, 0]);
    stamp(inner);
    const first = now();
    expect(stamped(inner)).toBe(first);
    stamp(outer);
    expect(now()).toBe(first + 1);
    expect([stamped(outer), stamped(inner)]).toEqual([first + 1, first + 1]);
    document.body.innerHTML = "";
  });
});
