import { beforeEach, describe, expect, it, vi } from "vitest";
import { createLayers } from "./layers";
import type { DialogAdapter, LayerStack } from "./layers";
import { HEADER_ORIGIN, HEADER_ZONE } from "./wire";

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

function makeStack() {
  const dispatched: Dispatched[] = [];
  const fetched: { url: string; zone: string }[] = [];
  const { adapter, dismissed } = mockDialog();
  const layers = createLayers({
    dispatch: (event, detail) => dispatched.push({ event, detail }),
    fetch: async (request) => {
      fetched.push({ url: request.url, zone: request.zone });
    },
    document,
    dialog: adapter,
  });
  return { layers, dispatched, fetched, dismissed };
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
    dismissed[0]("escape");
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
    expect(requests[0].headers?.[HEADER_ORIGIN]).toBe("/host/page/");
    expect(requests[0].headers?.[HEADER_ZONE]).toBe("access-wizard");
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
    expect(requests[0].url).toBe("/host/page/");
    expect(requests[0].headers?.[HEADER_ORIGIN]).toBe("/host/page/");
    expect(requests[0].headers?.[HEADER_ZONE]).toBe("request-list");
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
    expect(requests[0].url).toBe("/host/page/");
    expect(requests[0].headers?.[HEADER_ORIGIN]).toBe("/host/page/");
    layers._reset();
  });
});
