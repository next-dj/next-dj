import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createPartial } from "./partial";
import type { PartialSurface } from "./partial";
import type { DialogAdapter } from "./layers";
import { chunkModules, manualPollClock } from "./test-doubles";

interface Dispatched {
  event: string;
  detail: Record<string, unknown>;
}
interface Call {
  url: string;
  init: RequestInit;
}

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
      ...chunkModules,
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
    window.history.replaceState(null, "", "/");
  });

  function headerOf(call: Call, name: string): string | null {
    return new Headers(call.init.headers).get(name);
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
    expect(calls[0]!.url).toBe(`${location.origin}/request/identity/`);
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

  it("a meta op after layer.close in one envelope keeps its title", async () => {
    document.title = "Feed";
    respond = () =>
      envelopeResponse(zoneMorphBody("z", '<div data-next-zone="z">step</div>'));
    await partial.layers.open(null, "/w/", "z");
    partial.apply(envelope([{ op: "meta", title: "Wizard" }]));
    expect(document.title).toBe("Wizard");
    partial.apply(
      envelope([
        { op: "layer.close", result: 1 },
        { op: "meta", title: "Saved" },
      ]),
    );
    expect(partial.layers.size()).toBe(0);
    expect(document.title).toBe("Saved");
  });

  it("the title a base-page mutation answers waits under a layer opened meanwhile", async () => {
    window.history.replaceState(null, "", "/feed/");
    document.title = "Feed";
    document.body.innerHTML =
      '<form action="/_next/form/u/" data-next-action="u"></form>';
    let release!: (r: Response) => void;
    respond = () => new Promise<Response>((r) => (release = r));
    document
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    const saved = release;
    respond = () =>
      envelopeResponse(
        JSON.stringify(
          envelope([
            { op: "morph", target: { zone: "photo" }, html: "<div>p</div>" },
            { op: "meta", title: "Photo" },
          ]),
        ),
      );
    await partial.layers.open(null, "/photos/1/", "photo");
    expect(document.title).toBe("Photo");
    saved(envelopeResponse(JSON.stringify(envelope([{ op: "meta", title: "Saved" }]))));
    await flush();
    expect(document.title).toBe("Photo");
    partial.layers.close({ result: undefined });
    expect(document.title).toBe("Saved");
    window.history.replaceState(null, "", "/");
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

  it("a refused layer body reports the status and leaves no busy mark behind", async () => {
    const opener = document.createElement("a");
    document.body.append(opener);
    respond = () =>
      envelopeResponse("<html>403</html>", { status: 403, type: "text/html" });
    await partial.layers.open(opener, "/w/", "z");
    const container = document.querySelector('dialog [data-next-zone="z"]')!;
    expect(container.textContent).toBe("");
    expect(opener.hasAttribute("aria-busy")).toBe(false);
    expect(container.hasAttribute("data-next-busy")).toBe(false);
    const error = dispatched.find((d) => d.event === "partial:error");
    expect(error!.detail).toEqual({
      kind: "http",
      status: 403,
      body: "<html>403</html>",
    });
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

  it("a filter inside a layer moves the layer's URL and a close restores the host", async () => {
    window.history.replaceState(null, "", "/feed/?page=2");
    respond = () =>
      envelopeResponse(
        zoneMorphBody(
          "photo",
          '<div data-next-zone="photo"><form action="/photos/" data-next-target="grid">' +
            '<input name="q" value="cat" data-next-trigger="input"></form>' +
            '<div data-next-zone="grid">all</div></div>',
        ),
      );
    await partial.layers.open(null, "/photos/", "photo");
    respond = () =>
      envelopeResponse(zoneMorphBody("grid", '<div data-next-zone="grid">cats</div>'));
    document
      .querySelector("dialog input")!
      .dispatchEvent(new Event("input", { bubbles: true }));
    await flush();
    expect(location.pathname + location.search).toBe("/photos/?q=cat");
    expect(document.querySelector('dialog [data-next-zone="grid"]')!.textContent).toBe(
      "cats",
    );
    partial.layers.close({ result: undefined });
    expect(location.pathname + location.search).toBe("/feed/?page=2");
    window.history.replaceState(null, "", "/");
  });

  function photoBody(extra = ""): string {
    return JSON.stringify(
      envelope([
        {
          op: "morph",
          target: { zone: "photo-modal" },
          html: `<div data-next-zone="photo-modal">photo${extra}</div>`,
        },
      ]),
    );
  }

  function photoLink(attrs: Record<string, string>): HTMLAnchorElement {
    const link = document.createElement("a");
    link.setAttribute("href", "/photo/7/");
    for (const [name, value] of Object.entries(attrs)) link.setAttribute(name, value);
    document.body.append(link);
    return link;
  }

  function click(el: Element): MouseEvent {
    const event = new MouseEvent("click", { bubbles: true, cancelable: true });
    el.dispatchEvent(event);
    return event;
  }

  it("a zone link click GETs its own href with the zone and host origin and pushes it", async () => {
    window.history.replaceState(null, "", "/feed/");
    respond = () => envelopeResponse(photoBody());
    const event = click(photoLink({ "data-next-layer": "photo-modal" }));
    await flush();
    expect(event.defaultPrevented).toBe(true);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.url).toBe(`${location.origin}/photo/7/`);
    expect(calls[0]!.init.method).toBe("GET");
    expect(headerOf(calls[0]!, "X-Next-Zone")).toBe("photo-modal");
    expect(headerOf(calls[0]!, "X-Next-Origin")).toBe("/feed/");
    expect(location.pathname).toBe("/photo/7/");
    expect(
      document.querySelector('dialog [data-next-zone="photo-modal"]'),
    ).not.toBeNull();
  });

  it("a plain link to the same URL is neither prevented nor fetched", async () => {
    const event = click(photoLink({}));
    await flush();
    expect(event.defaultPrevented).toBe(false);
    expect(calls).toHaveLength(0);
    expect(partial.layers.size()).toBe(0);
  });

  it("a server layer.open op GETs its href with the zone and host origin and pushes it", async () => {
    window.history.replaceState(null, "", "/feed/");
    respond = () => envelopeResponse(photoBody());
    partial.apply(
      envelope([{ op: "layer.open", href: "/photo/7/", zone: "photo-modal" }]),
    );
    await flush();
    expect(calls).toHaveLength(1);
    expect(calls[0]!.url).toBe(`${location.origin}/photo/7/`);
    expect(headerOf(calls[0]!, "X-Next-Zone")).toBe("photo-modal");
    expect(headerOf(calls[0]!, "X-Next-Origin")).toBe("/feed/");
    expect(location.pathname).toBe("/photo/7/");
    expect(partial.layers.size()).toBe(1);
  });

  describe("requests made from inside an open layer", () => {
    async function openPhoto(extra: string): Promise<void> {
      window.history.replaceState(null, "", "/feed/");
      respond = () => envelopeResponse(photoBody(extra));
      await partial.layers.open(null, "/photo/7/", "photo-modal");
      calls.length = 0;
      respond = () => envelopeResponse(zoneMorphBody("none", "<div></div>"));
    }

    it("a refresh op GETs the layer URL with its zone and no origin", async () => {
      await openPhoto('<div data-next-zone="comments">c</div>');
      partial.apply(envelope([{ op: "refresh", zone: "comments" }]));
      await flush();
      expect(calls).toHaveLength(1);
      expect(calls[0]!.url).toBe(`${location.origin}/photo/7/`);
      expect(headerOf(calls[0]!, "X-Next-Zone")).toBe("comments");
      expect(headerOf(calls[0]!, "X-Next-Origin")).toBeNull();
    });

    it("a lazy load zone in the layer body GETs the layer URL with its zone and no origin", async () => {
      window.history.replaceState(null, "", "/feed/");
      respond = (call) =>
        headerOf(call, "X-Next-Zone") === "photo-modal"
          ? envelopeResponse(
              photoBody('<div data-next-zone="comments" data-next-lazy="load"></div>'),
            )
          : envelopeResponse(zoneMorphBody("none", "<div></div>"));
      await partial.layers.open(null, "/photo/7/", "photo-modal");
      await flush();
      expect(calls).toHaveLength(2);
      expect(calls[1]!.url).toBe(`${location.origin}/photo/7/`);
      expect(headerOf(calls[1]!, "X-Next-Zone")).toBe("comments");
      expect(headerOf(calls[1]!, "X-Next-Origin")).toBeNull();
    });

    it("a poll tick of a zone in the layer GETs the layer URL with its zone and no origin", async () => {
      const clock = manualPollClock();
      partial._configure({
        document,
        clock,
        dialog: mockDialog().adapter,
        navigate: () => undefined,
        fetch: async (url, init) => {
          const call = { url, init };
          calls.push(call);
          return respond(call);
        },
      });
      await openPhoto('<div data-next-zone="comments" data-next-poll="5000"></div>');
      clock.tick();
      await flush();
      expect(calls).toHaveLength(1);
      expect(calls[0]!.url).toBe(`${location.origin}/photo/7/`);
      expect(headerOf(calls[0]!, "X-Next-Zone")).toBe("comments");
      expect(headerOf(calls[0]!, "X-Next-Origin")).toBeNull();
    });

    it("a pagination link in the layer GETs its own href with the zone and merge intent", async () => {
      await openPhoto(
        '<div data-next-zone="comments"><a href="/photo/7/?page=2" ' +
          'data-next-merge="append" data-next-target="comments">more</a></div>',
      );
      click(document.querySelector("dialog a")!);
      await flush();
      expect(calls).toHaveLength(1);
      expect(calls[0]!.url).toBe(`${location.origin}/photo/7/?page=2`);
      expect(headerOf(calls[0]!, "X-Next-Zone")).toBe("comments");
      expect(headerOf(calls[0]!, "X-Next-Merge")).toBe("append");
      expect(headerOf(calls[0]!, "X-Next-Origin")).toBeNull();
    });

    it("an action submitted from the layer stamps its host origin and layer page", async () => {
      document.body.insertAdjacentHTML("afterbegin", '<p id="base">base</p>');
      await openPhoto('<button id="like">like</button>');
      const submit = (from: Element) =>
        partial.submit({ action: "/_next/form/like/", data: { n: 1 }, from });
      const fields = (call: Call) => Object.fromEntries(call.init.body as FormData);
      await expect(submit(document.querySelector("#like")!)).resolves.toMatchObject({
        status: "ok",
      });
      expect(headerOf(calls[0]!, "X-Next-Origin")).toBe("/feed/");
      expect(fields(calls[0]!)).toEqual({ n: "1", _next_form_origin: "/photo/7/" });
      await submit(document.querySelector("#base")!);
      expect(headerOf(calls[1]!, "X-Next-Origin")).toBeNull();
      expect(fields(calls[1]!)).toEqual({ n: "1", _next_form_origin: "/feed/" });
    });
  });

  describe("a meta op in the layer body titles the tab while the layer is open", () => {
    function metaBody(): string {
      return JSON.stringify(
        envelope([
          {
            op: "morph",
            target: { zone: "photo-modal" },
            html: '<div data-next-zone="photo-modal">photo</div>',
          },
          { op: "meta", title: "Photo 7", description: "A photo" },
        ]),
      );
    }

    function description(): string | null | undefined {
      return document.head
        .querySelector('meta[name="description"]')
        ?.getAttribute("content");
    }

    beforeEach(() => {
      window.history.replaceState(null, "", "/feed/");
      document.head.innerHTML =
        '<title>Feed</title><meta name="description" content="Feed list">';
      respond = () => envelopeResponse(metaBody());
    });

    afterEach(() => {
      document.head.innerHTML = "";
    });

    it("sets the title and description once the body lands", async () => {
      await partial.layers.open(null, "/photo/7/", "photo-modal");
      expect(document.title).toBe("Photo 7");
      expect(description()).toBe("A photo");
    });

    it("a later refresh response for the layer URL retitles the tab", async () => {
      await partial.layers.open(null, "/photo/7/", "photo-modal");
      respond = () =>
        envelopeResponse(
          JSON.stringify(
            envelope([
              { op: "morph", target: { zone: "photo-modal" }, html: "<div>x</div>" },
              { op: "meta", title: "Photo 7 (2)", description: null },
            ]),
          ),
        );
      await partial.fetch({
        url: "/photo/7/",
        zone: "photo-modal",
        headers: { "X-Next-Zone": "photo-modal" },
      });
      expect(document.title).toBe("Photo 7 (2)");
      expect(description()).toBeUndefined();
      partial.layers.close({ result: 1 });
      expect(document.title).toBe("Feed");
      expect(description()).toBe("Feed list");
    });

    it.each([
      ["accept", () => partial.layers.close({ result: 1 })],
      ["dismiss gesture", () => dismissers[dismissers.length - 1]!("escape")],
      [
        "Back",
        () => {
          window.history.replaceState(null, "", "/feed/");
          window.dispatchEvent(new PopStateEvent("popstate"));
        },
      ],
      ["reset", () => partial.layers._reset()],
    ])("restores the host title and description on %s", async (_name, closeLayer) => {
      await partial.layers.open(null, "/photo/7/", "photo-modal");
      closeLayer();
      expect(partial.layers.size()).toBe(0);
      expect(document.title).toBe("Feed");
      expect(description()).toBe("Feed list");
    });
  });
});
