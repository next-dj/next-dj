import { describe, expect, it } from "vitest";
import { inPlace, isPlainActivation } from "./events";

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
