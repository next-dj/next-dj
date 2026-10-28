import { describe, expect, it } from "vitest";
import {
  MAX_POLL_MS,
  REQUEST_TIMEOUT_MS,
  inPlace,
  isPlainActivation,
  requestTimeout,
} from "./protocol";

describe("isPlainActivation", () => {
  function link(attrs = ""): Element {
    document.body.innerHTML = `<a href="/p/" ${attrs}>p</a>`;
    return document.querySelector("a")!;
  }

  it("takes a primary click with no modifier on a same-tab link", () => {
    expect(isPlainActivation(new MouseEvent("click"), link())).toBe(true);
  });

  it("takes an activation that carries no pointer state", () => {
    expect(isPlainActivation(new Event("click"), link())).toBe(true);
  });

  it("refuses an activation a handler already cancelled", () => {
    const event = new MouseEvent("click", { cancelable: true });
    event.preventDefault();
    expect(isPlainActivation(event, link())).toBe(false);
  });

  it("refuses a link that downloads or opens elsewhere", () => {
    expect(isPlainActivation(new Event("click"), link("download"))).toBe(false);
    expect(isPlainActivation(new Event("click"), link('target="_top"'))).toBe(false);
  });
});

describe("inPlace", () => {
  it("reads an absent, empty or _self name as the current page", () => {
    expect(inPlace(null)).toBe(true);
    expect(inPlace("")).toBe(true);
    expect(inPlace("_Self")).toBe(true);
    expect(inPlace("_blank")).toBe(false);
  });
});

describe("requestTimeout", () => {
  it("keeps a timer-range value and zero, which turns the deadline off", () => {
    expect(requestTimeout(5000)).toBe(5000);
    expect(requestTimeout(0)).toBe(0);
    expect(requestTimeout(MAX_POLL_MS)).toBe(MAX_POLL_MS);
  });

  it.each([[undefined], ["5000"], [-1], [Number.NaN], [MAX_POLL_MS + 1]])(
    "falls back to the default for %s",
    (raw) => {
      expect(requestTimeout(raw)).toBe(REQUEST_TIMEOUT_MS);
    },
  );
});
