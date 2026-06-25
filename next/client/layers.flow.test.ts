import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createPartial } from "./partial";
import type { PartialSurface } from "./partial";
import type { DialogAdapter } from "./layers";

type Dispatched = { event: string; detail: Record<string, unknown> };
type Call = { url: string; init: RequestInit };

const ENVELOPE_TYPE = "application/vnd.next.patches+json";

// A mock dialog adapter that records its dismiss callback per dialog, so a test
// fires Esc, backdrop, or a dialog-form close without jsdom's missing showModal.
function mockDialog() {
  const dismissers: ((reason: string) => void)[] = [];
  const adapter: DialogAdapter = {
    open(_dialog, onDismiss) {
      dismissers.push(onDismiss);
      return () => undefined;
    },
  };
  return { adapter, dismissers };
}

function envelopeResponse(
  body: string,
  init: { status?: number; type?: string; url?: string; redirected?: boolean } = {},
): Response {
  const headers = new Headers({ "content-type": init.type ?? ENVELOPE_TYPE });
  const response = new Response(body, { status: init.status ?? 200, headers });
  Object.defineProperty(response, "url", { value: init.url ?? "/" });
  Object.defineProperty(response, "redirected", { value: init.redirected ?? false });
  return response;
}

function envelope(ops: unknown[]): Record<string, unknown> {
  return { version: "v1", ops, assets: [], form: null };
}

function zoneMorphBody(zone: string, html: string): string {
  return JSON.stringify(envelope([{ op: "morph", target: { zone }, html }]));
}

function zoneMorph(zone: string, html: string): Record<string, unknown> {
  return envelope([{ op: "morph", target: { zone }, html }]);
}

// Drain the microtask queue so the re-GET fetch chain the accept kicks off has
// run through classification and the applier by the time the DOM is asserted.
function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

describe("layer flow through the partial surface", () => {
  let partial: PartialSurface;
  let dispatched: Dispatched[];
  let calls: Call[];
  let dismissers: ((reason: string) => void)[];
  let respond: (call: Call) => Response | Promise<Response>;

  beforeEach(() => {
    document.body.innerHTML = "";
    dispatched = [];
    calls = [];
    const made = mockDialog();
    dismissers = made.dismissers;
    respond = () => envelopeResponse(zoneMorphBody("none", "<div></div>"));
    partial = createPartial({
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      mergeContext: () => undefined,
    });
    partial._configure({
      document,
      dialog: made.adapter,
      navigate: () => undefined,
      fetch: async (url, init) => {
        const call = { url, init };
        calls.push(call);
        return respond(call);
      },
    });
  });

  afterEach(() => {
    partial._reset();
  });

  function headerOf(call: Call, name: string): string | undefined {
    return (call.init.headers as Record<string, string>)[name];
  }

  it("opens a dialog with the zone container before the request and lands the first morph in it", async () => {
    respond = () =>
      envelopeResponse(
        zoneMorphBody(
          "access-wizard",
          '<div data-next-zone="access-wizard">step</div>',
        ),
      );
    await partial.layers.open(null, "/request/identity/", "access-wizard");
    const container = document.querySelector(
      'dialog [data-next-zone="access-wizard"]',
    )!;
    expect(container).not.toBeNull();
    expect(container.textContent).toBe("step");
    expect(calls[0]!.url).toBe("/request/identity/");
    expect(headerOf(calls[0]!, "X-Next-Zone")).toBe("access-wizard");
  });

  it("resolves a same-named zone in the top layer before the one beneath it", async () => {
    document.body.innerHTML = '<div data-next-zone="dup" id="page">page</div>';
    respond = () =>
      envelopeResponse(zoneMorphBody("dup", '<div data-next-zone="dup">layer</div>'));
    await partial.layers.open(null, "/w/", "dup");
    // The applier resolves zone targets top layer down, so this morph addresses
    // the layer's container, never the same-named page zone underneath it.
    partial.apply(zoneMorph("dup", '<div data-next-zone="dup">patched</div>'));
    expect(document.querySelector('dialog [data-next-zone="dup"]')!.textContent).toBe(
      "patched",
    );
    expect(document.querySelector("#page")!.textContent).toBe("page");
  });

  it("resolves a zone in the upper of two stacked layers before the lower", async () => {
    respond = (call) =>
      envelopeResponse(
        zoneMorphBody(
          headerOf(call, "X-Next-Zone") ?? "dup",
          '<div data-next-zone="dup">layer</div>',
        ),
      );
    await partial.layers.open(null, "/lower/", "dup");
    await partial.layers.open(null, "/upper/", "dup");
    expect(partial.layers.size()).toBe(2);
    partial.apply(zoneMorph("dup", '<div data-next-zone="dup">top</div>'));
    const dialogs = document.querySelectorAll('dialog [data-next-zone="dup"]');
    // The last-opened (upper) layer's container is the second dialog, and the
    // top-down resolve addresses it, leaving the lower layer untouched.
    expect(dialogs[0]!.textContent).toBe("layer");
    expect(dialogs[1]!.textContent).toBe("top");
  });

  it("a server-initiated layer.open verb opens a layer through the stack", async () => {
    respond = () =>
      envelopeResponse(
        zoneMorphBody("settings", '<div data-next-zone="settings">x</div>'),
      );
    partial.apply(
      envelope([{ op: "layer.open", href: "/settings/", zone: "settings" }]),
    );
    await flush();
    expect(partial.layers.size()).toBe(1);
    expect(document.querySelector('dialog [data-next-zone="settings"]')).not.toBeNull();
  });

  it("on accept fires layer-accepted and re-GETs the host zone named by the opener", async () => {
    const opener = document.createElement("a");
    opener.setAttribute("href", "/request/identity/");
    opener.setAttribute("data-next-accepted", "request-list");
    document.body.append(opener);
    document.body.append(
      Object.assign(document.createElement("div"), {
        innerHTML: '<div data-next-zone="request-list">stale</div>',
      }),
    );
    respond = (call) =>
      headerOf(call, "X-Next-Zone") === "request-list"
        ? envelopeResponse(
            zoneMorphBody(
              "request-list",
              '<div data-next-zone="request-list">fresh</div>',
            ),
          )
        : envelopeResponse(zoneMorphBody("access-wizard", "<div>wizard</div>"));
    await partial.layers.open(opener, "/request/identity/", "access-wizard");
    calls.length = 0;
    partial.apply(
      envelope([
        { op: "layer.close", result: { id: 42 } },
        { op: "toast", text: "Request created", variant: "success" },
      ]),
    );
    await flush();
    const accepted = dispatched.find((d) => d.event === "partial:layer-accepted");
    expect(accepted?.detail.result).toEqual({ id: 42 });
    expect(calls.some((c) => headerOf(c, "X-Next-Zone") === "request-list")).toBe(true);
    expect(partial.layers.size()).toBe(0);
    expect(
      document.querySelector(':not(dialog) > [data-next-zone="request-list"]')!
        .textContent,
    ).toBe("fresh");
  });

  it("a validation-error envelope morphs the master zone and leaves the layer open", async () => {
    respond = () =>
      envelopeResponse(
        zoneMorphBody(
          "access-wizard",
          '<div data-next-zone="access-wizard">step</div>',
        ),
      );
    await partial.layers.open(null, "/request/identity/", "access-wizard");
    partial.apply(
      zoneMorph(
        "access-wizard",
        '<div data-next-zone="access-wizard"><p class="errorlist">bad</p></div>',
      ),
    );
    // No op addressed the layer, so the modal survives by construction.
    expect(partial.layers.size()).toBe(1);
    const container = document.querySelector(
      'dialog [data-next-zone="access-wizard"]',
    )!;
    expect(container.querySelector(".errorlist")).not.toBeNull();
    expect(dispatched.some((d) => d.event === "partial:layer-dismissed")).toBe(false);
    expect(dispatched.some((d) => d.event === "partial:layer-accepted")).toBe(false);
  });

  it("a double submit inside the layer composition yields a single fetch", async () => {
    let release!: (r: Response) => void;
    respond = () => new Promise<Response>((r) => (release = r));
    const first = partial.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    const second = partial.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    release(envelopeResponse(zoneMorphBody("access-wizard", "<div>step</div>")));
    await Promise.all([first, second]);
    expect(calls).toHaveLength(1);
  });

  it("an expired session mid-wizard navigates fully instead of applying a layer patch", async () => {
    const navigated: string[] = [];
    partial._configure({
      document,
      dialog: mockDialog().adapter,
      navigate: (url) => navigated.push(url),
      fetch: async () =>
        envelopeResponse("<html>login</html>", {
          type: "text/html",
          url: "/login/",
          redirected: true,
        }),
    });
    await partial.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    expect(navigated).toEqual(["/login/"]);
    expect(partial.layers.size()).toBe(0);
  });

  it("a toast op renders its text as textContent, never parsed as markup", () => {
    partial.apply(
      envelope([{ op: "toast", text: "<script>alert(1)</script>", variant: "error" }]),
    );
    const item = document.querySelector("[data-next-toasts] [data-next-toast]")!;
    expect(item.querySelector("script")).toBeNull();
    expect(item.textContent).toBe("<script>alert(1)</script>");
  });

  it("marks the initiator and target busy for the open request and releases the pair", async () => {
    const opener = document.createElement("a");
    document.body.append(opener);
    let release!: (r: Response) => void;
    respond = () => new Promise<Response>((r) => (release = r));
    const opening = partial.layers.open(opener, "/w/", "z");
    const container = document.querySelector('dialog [data-next-zone="z"]')!;
    expect(opener.getAttribute("aria-busy")).toBe("true");
    expect(container.hasAttribute("data-next-busy")).toBe(true);
    release(envelopeResponse(zoneMorphBody("z", '<div data-next-zone="z">done</div>')));
    await opening;
    expect(opener.hasAttribute("aria-busy")).toBe(false);
    expect(container.hasAttribute("data-next-busy")).toBe(false);
  });

  it("a browser dismiss gesture rejects the matching layer with its reason", async () => {
    respond = () =>
      envelopeResponse(zoneMorphBody("z", '<div data-next-zone="z">step</div>'));
    await partial.layers.open(null, "/w/", "z");
    dismissers[dismissers.length - 1]!("escape");
    const dismissed = dispatched.find((d) => d.event === "partial:layer-dismissed");
    expect(dismissed?.detail.reason).toBe("escape");
    expect(partial.layers.size()).toBe(0);
  });

  it("opens a layer from a delegated click and keeps a plain link a navigation", () => {
    const plain = document.createElement("a");
    plain.setAttribute("href", "/page/");
    document.body.append(plain);
    const opener = document.createElement("a");
    opener.setAttribute("href", "/request/identity/");
    opener.setAttribute("data-next-layer", "access-wizard");
    document.body.append(opener);
    const click = () => new MouseEvent("click", { bubbles: true, cancelable: true });
    const plainEvent = click();
    plain.dispatchEvent(plainEvent);
    expect(plainEvent.defaultPrevented).toBe(false);
    expect(partial.layers.size()).toBe(0);
    const layerEvent = click();
    opener.dispatchEvent(layerEvent);
    expect(layerEvent.defaultPrevented).toBe(true);
    expect(partial.layers.size()).toBe(1);
  });
});
