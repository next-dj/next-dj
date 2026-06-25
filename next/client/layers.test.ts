import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createLayers } from "./layers";
import type { DialogAdapter, LayerStack, PopStateAdapter } from "./layers";
import { HEADER_ORIGIN, HEADER_ZONE } from "./protocol";

type Dispatched = { event: string; detail: Record<string, unknown> };

// A mock dialog adapter that records its dismiss callback so a test fires Esc,
// backdrop, or a dialog-form close without jsdom's missing showModal.
function mockDialog() {
  const dismissed: ((reason: string) => void)[] = [];
  const adapter: DialogAdapter = {
    open(_dialog, onDismiss) {
      dismissed.push(onDismiss);
      return () => undefined;
    },
  };
  return { adapter, dismissed };
}

function makeStackOn(doc: Document) {
  const dispatched: Dispatched[] = [];
  const fetched: { url: string; zone: string }[] = [];
  const { adapter, dismissed } = mockDialog();
  const layers = createLayers({
    dispatch: (event, detail) => dispatched.push({ event, detail }),
    fetch: async (request) => {
      fetched.push({ url: request.url, zone: request.zone });
    },
    document: doc,
    dialog: adapter,
  });
  return { layers, dispatched, fetched, dismissed };
}

function makeStack() {
  return makeStackOn(document);
}

describe("layer stack", () => {
  let layers: LayerStack;
  let dispatched: Dispatched[];
  let fetched: { url: string; zone: string }[];
  let dismissed: ((reason: string) => void)[];

  beforeEach(() => {
    document.body.innerHTML = "";
    const made = makeStack();
    layers = made.layers;
    dispatched = made.dispatched;
    fetched = made.fetched;
    dismissed = made.dismissed;
  });

  it("builds the zone container before the request and GETs the body", async () => {
    await layers.open(null, "/wizard/", "access-wizard");
    expect(
      document.querySelector('dialog [data-next-zone="access-wizard"]'),
    ).not.toBeNull();
    expect(fetched).toEqual([{ url: "/wizard/", zone: "access-wizard" }]);
    expect(dispatched.some((d) => d.event === "partial:layer-opened")).toBe(true);
  });

  it("stamps data-next-dialog on the created dialog element", async () => {
    await layers.open(null, "/wizard/", "access-wizard");
    expect(document.querySelector("[data-next-dialog]")).not.toBeNull();
  });

  it("resolves a layer zone before the same-named page zone", async () => {
    document.body.innerHTML = '<div data-next-zone="z" id="page"></div>';
    await layers.open(null, "/w/", "z");
    const found = layers.resolveZone("z", document);
    expect(found).not.toBeNull();
    expect((found as Element).id).toBe("");
    expect((found as Element).closest("dialog")).not.toBeNull();
  });

  it("resolves a zone nested inside a layer container", async () => {
    await layers.open(null, "/w/", "outer");
    const container = document.querySelector('dialog [data-next-zone="outer"]')!;
    container.innerHTML = '<section data-next-zone="inner">x</section>';
    const found = layers.resolveZone("inner", document);
    expect(found).not.toBeNull();
    expect((found as Element).tagName).toBe("SECTION");
  });

  it("falls back to the page zone when no layer holds it", () => {
    document.body.innerHTML = '<div data-next-zone="only">page</div>';
    const found = layers.resolveZone("only", document);
    expect((found as Element).textContent).toBe("page");
  });

  it("accept fires layer-accepted with the result and re-GETs the host zone", async () => {
    const opener = document.createElement("a");
    opener.setAttribute("href", "/wizard/");
    opener.setAttribute("data-next-accepted", "request-list");
    document.body.append(opener);
    await layers.open(opener, "/wizard/", "access-wizard");
    fetched.length = 0;
    layers.close({ result: { id: 42 } });
    const accepted = dispatched.find((d) => d.event === "partial:layer-accepted");
    expect(accepted?.detail.result).toEqual({ id: 42 });
    expect(fetched.some((f) => f.zone === "request-list")).toBe(true);
    expect(layers.size()).toBe(0);
  });

  it("dismiss via the close verb fires layer-dismissed with the reason", async () => {
    await layers.open(null, "/w/", "z");
    layers.close({ dismiss: true, reason: "cancel" });
    const event = dispatched.find((d) => d.event === "partial:layer-dismissed");
    expect(event?.detail.reason).toBe("cancel");
    expect(layers.size()).toBe(0);
  });

  it("a browser dismiss gesture rejects the layer with its reason", async () => {
    await layers.open(null, "/w/", "z");
    dismissed[0]!("escape");
    const event = dispatched.find((d) => d.event === "partial:layer-dismissed");
    expect(event?.detail.reason).toBe("escape");
    expect(layers.size()).toBe(0);
  });

  it("toast renders text as textContent, never as HTML", () => {
    layers.toast("<img src=x onerror=alert(1)>", "error");
    const item = document.querySelector("[data-next-toasts] [data-next-toast]");
    expect(item?.querySelector("img")).toBeNull();
    expect(item?.textContent).toBe("<img src=x onerror=alert(1)>");
  });

  it("busy marks initiator and target and releases them", () => {
    const a = document.createElement("button");
    const b = document.createElement("div");
    const release = layers.busy(a, b);
    expect(a.getAttribute("aria-busy")).toBe("true");
    expect(b.hasAttribute("data-next-busy")).toBe(true);
    release();
    expect(a.hasAttribute("aria-busy")).toBe(false);
    expect(b.hasAttribute("data-next-busy")).toBe(false);
  });

  it("a delegated click on a layer link opens without breaking no-JS links", () => {
    const detach = layers.install(document);
    const plain = document.createElement("a");
    plain.setAttribute("href", "/page/");
    document.body.append(plain);
    const opener = document.createElement("a");
    opener.setAttribute("href", "/wizard/");
    opener.setAttribute("data-next-layer", "access-wizard");
    document.body.append(opener);
    const open = vi.spyOn(layers, "open");
    plain.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    expect(layers.size()).toBe(0);
    opener.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    expect(layers.size()).toBe(1);
    open.mockRestore();
    detach();
  });

  it("_reset closes open layers and drops the toast container", async () => {
    await layers.open(null, "/w/", "z");
    layers.toast("hi", "info");
    layers._reset();
    expect(layers.size()).toBe(0);
    expect(document.querySelector("[data-next-toasts]")).toBeNull();
    expect(document.querySelector("dialog")).toBeNull();
  });

  it("_reset tears down the delegated click and popstate listeners", () => {
    let popstateDetached = 0;
    const popstate: PopStateAdapter = {
      listen: () => () => {
        popstateDetached += 1;
      },
    };
    const local = createLayers({
      dispatch: () => undefined,
      fetch: async () => undefined,
      document,
      dialog: mockDialog().adapter,
      popstate,
    });
    local.install(document);
    local._reset();
    // The popstate detach ran exactly once, and a click on a layer link no
    // longer opens a layer: the delegated handler came off the document.
    expect(popstateDetached).toBe(1);
    const opener = document.createElement("a");
    opener.setAttribute("href", "/wizard/");
    opener.setAttribute("data-next-layer", "z");
    document.body.append(opener);
    opener.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    expect(local.size()).toBe(0);

    // A fresh install after the reset rebinds the handler, so the click opens
    // again.
    local.install(document);
    opener.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    expect(local.size()).toBe(1);
    local._reset();
  });

  it("close on an empty stack is a no-op", () => {
    layers.close({ result: 1 });
    expect(dispatched.some((d) => d.event === "partial:layer-accepted")).toBe(false);
    expect(layers.size()).toBe(0);
  });

  it("a dismiss verb with no reason falls back to the dismissed reason", async () => {
    await layers.open(null, "/w/", "z");
    layers.close({ dismiss: true });
    const event = dispatched.find((d) => d.event === "partial:layer-dismissed");
    expect(event?.detail.reason).toBe("dismissed");
  });

  it("a second browser dismiss on an already-closed dialog is a no-op", async () => {
    await layers.open(null, "/w/", "z");
    dismissed[0]!("escape");
    const before = dispatched.filter(
      (d) => d.event === "partial:layer-dismissed",
    ).length;
    dismissed[0]!("escape");
    const after = dispatched.filter(
      (d) => d.event === "partial:layer-dismissed",
    ).length;
    expect(after).toBe(before);
  });

  it("returns focus only to an HTMLElement, skipping a null activeElement", async () => {
    // A document proxy whose activeElement is null exercises the focus guard's
    // false arm, which jsdom cannot reach since its activeElement is the body.
    const proxy = new Proxy(document, {
      get(base, prop) {
        if (prop === "activeElement") return null;
        const value = Reflect.get(base, prop);
        return typeof value === "function" ? value.bind(base) : value;
      },
    });
    const local = makeStackOn(proxy as Document);
    await local.layers.open(null, "/w/", "z");
    expect(() => local.layers.close({ result: 1 })).not.toThrow();
    expect(local.layers.size()).toBe(0);
  });

  it("reuses the connected toast host across toasts", () => {
    layers.toast("one", "info");
    layers.toast("two", "info");
    const hosts = document.querySelectorAll("[data-next-toasts]");
    expect(hosts).toHaveLength(1);
    expect(hosts[0]!.children).toHaveLength(2);
  });

  it("rebuilds the toast host once it is detached from the document", () => {
    layers.toast("one", "info");
    document.querySelector("[data-next-toasts]")!.remove();
    layers.toast("two", "info");
    const hosts = document.querySelectorAll("[data-next-toasts]");
    expect(hosts).toHaveLength(1);
    expect(hosts[0]!.children).toHaveLength(1);
    expect(hosts[0]!.textContent).toBe("two");
  });

  it("ignores a click whose target is not an Element", () => {
    const detach = layers.install(document);
    const event = new Event("click", { bubbles: true });
    Object.defineProperty(event, "target", { value: null });
    expect(() => document.dispatchEvent(event)).not.toThrow();
    expect(layers.size()).toBe(0);
    detach();
  });

  it("treats a layer link with an empty zone as a plain navigation", () => {
    const detach = layers.install(document);
    const opener = document.createElement("a");
    opener.setAttribute("href", "/wizard/");
    opener.setAttribute("data-next-layer", "");
    document.body.append(opener);
    const event = new MouseEvent("click", { bubbles: true, cancelable: true });
    opener.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
    expect(layers.size()).toBe(0);
    detach();
  });

  it("install replaces a prior delegated handler", () => {
    const first = layers.install(document);
    const second = layers.install(document);
    const opener = document.createElement("a");
    opener.setAttribute("href", "/wizard/");
    opener.setAttribute("data-next-layer", "z");
    document.body.append(opener);
    opener.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    // The first install was torn down by the second, so a single open fires.
    expect(layers.size()).toBe(1);
    expect(first).not.toBe(second);
    second();
  });

  it("the teardown is idempotent across the popstate listener", () => {
    let detached = 0;
    const popstate: PopStateAdapter = {
      listen: () => () => {
        detached += 1;
      },
    };
    const local = createLayers({
      dispatch: () => undefined,
      fetch: async () => undefined,
      document,
      dialog: mockDialog().adapter,
      popstate,
    });
    const teardown = local.install(document);
    teardown();
    teardown();
    expect(detached).toBe(1);
  });
});

describe("layer requests carry the host origin", () => {
  type Request = { url: string; zone: string; headers?: Record<string, string> };

  function makeOriginStack() {
    const requests: Request[] = [];
    const layers = createLayers({
      dispatch: () => undefined,
      fetch: async (request) => {
        requests.push(request);
      },
      document,
      dialog: { open: () => () => undefined },
    });
    return { layers, requests };
  }

  beforeEach(() => {
    document.body.innerHTML = "";
    window.history.replaceState(null, "", "/host/page/");
  });

  it("stamps X-Next-Origin with the host page on the body GET", async () => {
    const { layers, requests } = makeOriginStack();
    await layers.open(null, "/wizard/identity/", "access-wizard");
    expect(requests[0]!.headers?.[HEADER_ORIGIN]).toBe("/host/page/");
    expect(requests[0]!.headers?.[HEADER_ZONE]).toBe("access-wizard");
    layers._reset();
  });

  it("re-GETs the host zone on accept with the same captured origin", async () => {
    const { layers, requests } = makeOriginStack();
    const opener = document.createElement("a");
    opener.setAttribute("href", "/wizard/identity/");
    opener.setAttribute("data-next-accepted", "request-list");
    document.body.append(opener);
    await layers.open(opener, "/wizard/identity/", "access-wizard");
    requests.length = 0;
    layers.close({ result: { id: 7 } });
    expect(requests[0]!.url).toBe("/host/page/");
    expect(requests[0]!.headers?.[HEADER_ORIGIN]).toBe("/host/page/");
    expect(requests[0]!.headers?.[HEADER_ZONE]).toBe("request-list");
    layers._reset();
  });

  it("keeps the open-time host even after a later navigation", async () => {
    const { layers, requests } = makeOriginStack();
    const opener = document.createElement("a");
    opener.setAttribute("href", "/wizard/identity/");
    opener.setAttribute("data-next-accepted", "request-list");
    document.body.append(opener);
    await layers.open(opener, "/wizard/identity/", "access-wizard");
    window.history.replaceState(null, "", "/moved/elsewhere/");
    requests.length = 0;
    layers.close({ result: { id: 7 } });
    expect(requests[0]!.url).toBe("/host/page/");
    expect(requests[0]!.headers?.[HEADER_ORIGIN]).toBe("/host/page/");
    layers._reset();
  });
});

describe("layer intercepting URL lifecycle", () => {
  let layers: LayerStack;
  let dispatched: Dispatched[];
  let fire: () => void;

  beforeEach(() => {
    document.body.innerHTML = "";
    window.history.replaceState(null, "", "/feed/");
    dispatched = [];
    let handler: (() => void) | null = null;
    const popstate: PopStateAdapter = {
      listen(h) {
        handler = h;
        return () => {
          handler = null;
        };
      },
    };
    layers = createLayers({
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      fetch: async () => {},
      document,
      dialog: mockDialog().adapter,
      popstate,
    });
    layers.install(document);
    fire = () => handler?.();
  });

  afterEach(() => {
    layers._reset();
  });

  it("pushes the honest URL of the layer body on open", async () => {
    await layers.open(null, "/photos/1/", "photo");
    expect(window.location.pathname).toBe("/photos/1/");
    expect(layers.size()).toBe(1);
  });

  it("closes the top layer when Back moves past its pushed URL", async () => {
    await layers.open(null, "/photos/1/", "photo");
    window.history.replaceState(null, "", "/feed/");
    fire();
    expect(layers.size()).toBe(0);
    expect(
      dispatched.some(
        (d) => d.event === "partial:layer-dismissed" && d.detail.reason === "popstate",
      ),
    ).toBe(true);
  });

  it("replaces the URL back to the host on a programmatic accept", async () => {
    await layers.open(null, "/photos/1/", "photo");
    layers.close({ result: { id: 1 } });
    expect(layers.size()).toBe(0);
    expect(window.location.pathname).toBe("/feed/");
  });

  it("replaces the URL back to the host on a programmatic dismiss", async () => {
    await layers.open(null, "/photos/1/", "photo");
    layers.close({ dismiss: true, reason: "escape" });
    expect(layers.size()).toBe(0);
    expect(window.location.pathname).toBe("/feed/");
  });

  it("Back closes only the top of a nested stack", async () => {
    await layers.open(null, "/photos/1/", "a");
    await layers.open(null, "/photos/1/edit/", "b");
    window.history.replaceState(null, "", "/photos/1/");
    fire();
    expect(layers.size()).toBe(1);
  });

  it("a stray popstate after a programmatic close is a no-op", async () => {
    await layers.open(null, "/photos/1/", "photo");
    layers.close({ result: undefined });
    expect(layers.size()).toBe(0);
    fire();
    expect(layers.size()).toBe(0);
  });
});

describe("layer open is single-flight and rolls back on failure", () => {
  function makeHistory() {
    const pushed: string[] = [];
    const replaced: string[] = [];
    const history = {
      push: (href: string) => pushed.push(href),
      replace: (href: string) => replaced.push(href),
    };
    return { history, pushed, replaced };
  }

  let layers: LayerStack;

  beforeEach(() => {
    document.body.innerHTML = "";
    window.history.replaceState(null, "", "/feed/");
  });

  afterEach(() => {
    layers._reset();
  });

  it("a double click opens one dialog and pushes history once", () => {
    const { history, pushed } = makeHistory();
    layers = createLayers({
      dispatch: () => undefined,
      fetch: () => new Promise<void>(() => undefined),
      document,
      dialog: mockDialog().adapter,
      history,
    });
    const opener = document.createElement("a");
    opener.setAttribute("href", "/photos/1/");
    opener.setAttribute("data-next-layer", "photo");
    document.body.append(opener);
    layers.install(document);
    opener.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    opener.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    expect(document.querySelectorAll("[data-next-dialog]")).toHaveLength(1);
    expect(pushed).toEqual(["/photos/1/"]);
    expect(layers.size()).toBe(1);
  });

  it("a second open for a busy opener is dropped before any mutation", async () => {
    const { history, pushed } = makeHistory();
    layers = createLayers({
      dispatch: () => undefined,
      fetch: () => new Promise<void>(() => undefined),
      document,
      dialog: mockDialog().adapter,
      history,
    });
    const opener = document.createElement("a");
    opener.setAttribute("href", "/photos/1/");
    document.body.append(opener);
    void layers.open(opener, "/photos/1/", "photo");
    await layers.open(opener, "/photos/1/", "photo");
    expect(document.querySelectorAll("[data-next-dialog]")).toHaveLength(1);
    expect(pushed).toEqual(["/photos/1/"]);
  });

  it("a fetch failure tears down the orphan and rolls the URL back to the host", async () => {
    const { history, replaced } = makeHistory();
    layers = createLayers({
      dispatch: () => undefined,
      fetch: () => Promise.reject(new Error("boom")),
      document,
      dialog: mockDialog().adapter,
      history,
    });
    const opener = document.createElement("a");
    opener.setAttribute("href", "/photos/1/");
    document.body.append(opener);
    await expect(layers.open(opener, "/photos/1/", "photo")).rejects.toThrow("boom");
    expect(document.querySelector("[data-next-dialog]")).toBeNull();
    expect(layers.size()).toBe(0);
    expect(replaced).toEqual(["/feed/"]);
    expect(opener.hasAttribute("data-next-busy")).toBe(false);
    expect(opener.hasAttribute("aria-busy")).toBe(false);
  });
});
