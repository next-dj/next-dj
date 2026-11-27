import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BeforeApply } from "./apply";
import { createLayers } from "./layers";
import type { DialogAdapter, Layers, LayersFactory, PopStateAdapter } from "./layers";
import { createNavigation } from "./navigation";
import { markBusy } from "./protocol";
import { createStack } from "./stack";
import type { Stack } from "./stack";
import { unloaded } from "./test-doubles";

type Chunk = ReturnType<typeof unloaded<LayersFactory>>;

const dialog: DialogAdapter = { open: () => () => undefined };

let made: Stack[];

beforeEach(() => {
  made = [];
  document.head.innerHTML = "";
  document.body.innerHTML = "";
  history.replaceState(null, "", "/");
});

afterEach(() => {
  for (const stack of made) stack._reset();
});

function build(chunk: Chunk = unloaded<LayersFactory>("layers")) {
  const navigation = createNavigation({ dispatch: () => undefined });
  const fired: string[] = [];
  const popped = vi.fn();
  const navigate = vi.fn<(href: string) => void>();
  const fetch = vi.fn(() => Promise.resolve());
  const stack = createStack({
    dispatch: (event) => void fired.push(event),
    chunk,
    build: (factory) =>
      factory({
        dispatch: () => undefined,
        fetch,
        navigation,
        dialog,
        mark: markBusy,
      }),
    popped,
    navigate,
  });
  made.push(stack);
  return { stack, chunk, fired, popped, navigate, fetch };
}

function tag(): HTMLScriptElement | null {
  return document.head.querySelector('script[src$="next.layers.min.js"]');
}

function opening(): BeforeApply & { released: () => boolean } {
  let released = false;
  return {
    envelope: {
      version: "",
      ops: [{ op: "layer.open", href: "/m/", zone: "m" }],
      assets: [],
      form: null,
    },
    boundaries: [],
    hold: () => () => (released = true),
    released: () => released,
  };
}

describe("the stack before its chunk loads", () => {
  it("answers every lookup for the base page alone", () => {
    document.body.innerHTML =
      '<div data-next-zone="z" id="z"></div><div data-next-toasts id="t"></div>';
    const { stack } = build();
    const el = document.getElementById("z")!;
    expect(stack.resolveZone("z", document)).toBe(el);
    expect(stack.resolveSelector("[data-next-toasts]", document)?.id).toBe("t");
    expect(stack.urlFor(el)).toBe("/");
    expect(stack.hostFor(el)).toBeUndefined();
    expect(stack.rewrite(el, "/?q=1")).toBe(true);
    expect(stack.size()).toBe(0);
    stack.close({ dismiss: true });
    expect(tag()).toBeNull();
  });

  it("writes the head of the page on screen and leaves another page's alone", () => {
    document.title = "Before";
    const { stack } = build();
    stack.head({ title: "Other" }, "/elsewhere/");
    expect(document.title).toBe("Before");
    stack.head({ title: "Here" }, "/");
    expect(document.title).toBe("Here");
    stack.head({ title: "Now" });
    expect(document.title).toBe("Now");
  });

  it("builds a toast tray once the page has none", () => {
    const { stack, fired } = build();
    const item = stack.toast("<b>saved</b>", "success");
    expect(item.textContent).toBe("<b>saved</b>");
    expect(item.parentElement!.getAttribute("aria-live")).toBe("polite");
    stack.toast("again", "info");
    expect(document.querySelectorAll("[data-next-toasts]")).toHaveLength(1);
    expect(fired).toEqual(["next:toast", "next:toast"]);
  });

  it("fetches the chunk once a mount finds a layer link", () => {
    document.body.innerHTML = '<p id="p"></p>';
    const { stack } = build();
    stack.scan(document);
    expect(tag()).toBeNull();
    document.body.innerHTML = '<a href="/m/" data-next-layer="m">m</a>';
    stack.scan(document);
    expect(tag()).not.toBeNull();
  });

  it("announces a popstate to the navigation while no layer can be open", () => {
    const { stack, popped } = build();
    let listen: () => void = () => undefined;
    const popstate: PopStateAdapter = {
      listen: (handler) => {
        listen = handler;
        return () => undefined;
      },
    };
    stack.install(document, popstate);
    listen();
    expect(popped).toHaveBeenCalledTimes(1);
  });

  it("holds an envelope that opens a layer until the chunk lands", async () => {
    const { stack, chunk } = build();
    const detail = opening();
    stack.before({ ...detail, envelope: { ...detail.envelope, ops: [] } });
    expect(tag()).toBeNull();
    stack.before(detail);
    expect(detail.released()).toBe(false);
    chunk.land(createLayers);
    await vi.waitFor(() => expect(detail.released()).toBe(true));
    const again = opening();
    stack.before(again);
    expect(again.released()).toBe(false);
  });

  it("releases a held envelope when the chunk fails", async () => {
    const { stack } = build();
    const detail = opening();
    stack.before(detail);
    tag()!.dispatchEvent(new Event("error"));
    await vi.waitFor(() => expect(detail.released()).toBe(true));
  });

  it("opens a clicked layer link once the chunk lands", async () => {
    document.body.innerHTML = '<a id="l" href="/m/" data-next-layer="m">m</a>';
    const { stack, chunk, fetch } = build();
    stack.install(document);
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    document.getElementById("l")!.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true);
    chunk.land(createLayers);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(stack.size()).toBe(1);
  });

  it("leaves a link without a zone or a plain click on it to the browser", () => {
    document.body.innerHTML =
      '<a id="bare" href="/m/" data-next-layer="">m</a>' +
      '<a id="mod" href="/m/" data-next-layer="m">m</a><p id="p"></p>';
    const { stack } = build();
    stack.install(document);
    const fire = (id: string, init: MouseEventInit = {}): boolean => {
      const click = new MouseEvent("click", {
        bubbles: true,
        cancelable: true,
        ...init,
      });
      document.getElementById(id)!.dispatchEvent(click);
      return click.defaultPrevented;
    };
    expect(fire("bare")).toBe(false);
    expect(fire("mod", { ctrlKey: true })).toBe(false);
    expect(fire("p")).toBe(false);
    expect(tag()).toBeNull();
  });

  it("drops a click on an opener already busy", () => {
    document.body.innerHTML =
      '<a id="l" href="/m/" data-next-layer="m" data-next-busy>m</a>';
    const { stack } = build();
    stack.install(document);
    document
      .getElementById("l")!
      .dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    expect(tag()).toBeNull();
  });

  it("visits a layer link in full when its chunk cannot load", async () => {
    const { stack, navigate } = build();
    const opened = stack.open(null, "/m/", "m");
    tag()!.dispatchEvent(new Event("error"));
    await opened;
    expect(navigate).toHaveBeenCalledWith("/m/");
    const bare = stack.open(null);
    tag()!.dispatchEvent(new Event("error"));
    await bare;
    expect(navigate).toHaveBeenCalledTimes(1);
  });

  it("hands every call to the layers once they are built", async () => {
    const { stack, chunk } = build();
    const layers = {
      resolveZone: vi.fn(() => null),
      resolveSelector: vi.fn(() => null),
      urlFor: vi.fn(() => "/m/"),
      hostFor: vi.fn(() => "/"),
      rewrite: vi.fn(() => false),
      open: vi.fn(() => Promise.resolve()),
      close: vi.fn(),
      head: vi.fn(),
      size: vi.fn(() => 2),
      popstate: vi.fn(),
      _reset: vi.fn(),
    } satisfies Layers;
    const opened = stack.open(null);
    chunk.land(() => layers);
    await opened;
    const el = document.body;
    expect(stack.resolveZone("z", document)).toBeNull();
    expect(stack.resolveSelector("p", document)).toBeNull();
    expect(stack.urlFor(el)).toBe("/m/");
    expect(stack.hostFor(el)).toBe("/");
    expect(stack.rewrite(el, "/x/")).toBe(false);
    expect(stack.size()).toBe(2);
    stack.close({ result: 1 });
    stack.head({ title: "T" });
    stack.scan(document);
    let listen: () => void = () => undefined;
    stack.install(document, {
      listen: (handler) => ((listen = handler), () => undefined),
    });
    listen();
    expect(layers.close).toHaveBeenCalledWith({ result: 1 });
    expect(layers.head).toHaveBeenCalledWith({ title: "T" }, undefined);
    expect(layers.popstate).toHaveBeenCalledTimes(1);
    expect(layers.open).toHaveBeenCalledWith(null, undefined, undefined);
  });
});
