import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Applier, parseEnvelope } from "./apply";
import type { ApplyOptions, Envelope } from "./apply";
import { createLayers } from "./layers";
import { createNavigation } from "./navigation";
import { createDrain, drain } from "./drain";
import type { DrainHost, Fill } from "./drain";
import { locate, markers } from "./layout";
import {
  ATTR_DIGEST,
  ATTR_ERROR,
  ATTR_STATE,
  EVENT_END,
  EVENT_HELLO,
  EVENT_PATCH,
  PROTOCOL,
  isBusy,
  markBusy,
  stamped,
} from "./protocol";
import { frames } from "./reader";
import { createStack } from "./stack";
import { landed } from "./test-doubles";
import type { DialogAdapter } from "./layers";

const GOLDEN_DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../tests/partial/golden",
);

interface GoldenMeta {
  name: string;
  description: string;
  content_type: string;
  status: number;
  headers: Record<string, string>;
  envelope_file: string;
}

function readMeta(name: string): GoldenMeta {
  const raw = readFileSync(join(GOLDEN_DIR, `${name}.meta.json`), "utf-8");
  return JSON.parse(raw) as GoldenMeta;
}

// Read the same raw bytes the server backend serialised. Both toolchains
// parsing one file is the guard against the wire format drifting apart.
function readEnvelopeBytes(file: string): string {
  return readFileSync(join(GOLDEN_DIR, file), "utf-8");
}

function caseNames(): string[] {
  return readdirSync(GOLDEN_DIR)
    .filter((name) => name.endsWith(".meta.json"))
    .map((name) => name.slice(0, -".meta.json".length))
    .sort();
}

function makeApplier() {
  const dispatched: { event: string; detail: Record<string, unknown> }[] = [];
  const applier = new Applier({
    dispatch: (event, detail) => dispatched.push({ event, detail }),
    mergeContext: () => undefined,
    document,
  });
  return { applier, dispatched };
}

describe("golden fixtures classify as envelopes", () => {
  for (const name of caseNames()) {
    it(`${name} declares the envelope content type and parses its bytes`, () => {
      const meta = readMeta(name);
      expect(meta.content_type).toBe("application/vnd.next.patches+json");
      expect(meta.headers["Content-Type"]).toBe("application/vnd.next.patches+json");
      expect(meta.headers.Vary).toContain("X-Next-Merge");
      const envelope = parseEnvelope(JSON.parse(readEnvelopeBytes(meta.envelope_file)));
      expect(envelope.version).toBe("9f3c2e1b");
    });
  }
});

describe("golden fixtures apply to the DOM", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("replace_zone swaps the named zone wholesale", () => {
    document.body.innerHTML =
      '<div data-next-zone="request-list"><span>stale</span></div>';
    const meta = readMeta("replace_zone");
    const { applier } = makeApplier();
    applier.apply(JSON.parse(readEnvelopeBytes(meta.envelope_file)));
    const zones = document.querySelectorAll('[data-next-zone="request-list"]');
    expect(zones).toHaveLength(1);
    expect(zones[0]!.querySelector("ul")).not.toBeNull();
    expect(zones[0]!.querySelector("span")).toBeNull();
  });

  it("inner_zone replaces only the contents of the zone", () => {
    document.body.innerHTML =
      '<div data-next-zone="request-list"><span>stale</span></div>';
    const meta = readMeta("inner_zone");
    const { applier } = makeApplier();
    applier.apply(JSON.parse(readEnvelopeBytes(meta.envelope_file)));
    const zone = document.querySelector('[data-next-zone="request-list"]')!;
    expect(zone.querySelectorAll("li")).toHaveLength(2);
    expect(zone.textContent).toBe("onetwo");
  });

  it("remove_row deletes the addressed node", () => {
    document.body.innerHTML =
      '<ul><li id="row-42">gone</li><li id="row-7">kept</li></ul>';
    const meta = readMeta("remove_row");
    const { applier } = makeApplier();
    applier.apply(JSON.parse(readEnvelopeBytes(meta.envelope_file)));
    expect(document.querySelector("#row-42")).toBeNull();
    expect(document.querySelector("#row-7")).not.toBeNull();
  });

  it("event_only dispatches the named CustomEvent without touching the DOM", () => {
    const meta = readMeta("event_only");
    const { applier, dispatched } = makeApplier();
    const onDoc = vi.fn();
    document.addEventListener("request-created", onDoc);
    applier.apply(JSON.parse(readEnvelopeBytes(meta.envelope_file)));
    document.removeEventListener("request-created", onDoc);
    expect(onDoc).toHaveBeenCalledOnce();
    expect((onDoc.mock.calls[0]![0] as CustomEvent).detail).toEqual({ id: 42 });
    expect(dispatched).toContainEqual({
      event: "request-created",
      detail: { id: 42 },
    });
  });

  it("invalid_form morphs the form by uid and exposes machine-readable errors", () => {
    document.body.innerHTML =
      '<form data-next-action="ab12cd34"><input name="name" value="typo"></form>';
    const meta = readMeta("invalid_form");
    const { applier, dispatched } = makeApplier();
    const onToast = vi.fn();
    document.addEventListener("toast", onToast);
    const result: Envelope = applier.apply(
      JSON.parse(readEnvelopeBytes(meta.envelope_file)),
    );
    document.removeEventListener("toast", onToast);
    const form = document.querySelector('[data-next-action="ab12cd34"]')!;
    expect(form.querySelector(".errorlist")).not.toBeNull();
    expect(form.querySelector('input[aria-invalid="true"]')).not.toBeNull();
    expect(result.form).toEqual({
      uid: "ab12cd34",
      valid: false,
      errors: { name: ["This field is required."] },
    });
    expect(onToast).toHaveBeenCalledOnce();
    expect(dispatched).toContainEqual({
      event: "toast",
      detail: { text: "Could not save", variant: "error" },
    });
    expect(meta.headers["X-Next-Form"]).toBe("invalid");
    expect(meta.headers["X-Next-Action"]).toBe("ab12cd34");
  });

  it("invalid_form_extract carves the failed form out of a full document", () => {
    document.body.innerHTML =
      '<form data-next-action="3f9ac21d75e04b88"><input name="title" value="kept"></form>';
    const meta = readMeta("invalid_form_extract");
    const { applier } = makeApplier();
    const result: Envelope = applier.apply(
      JSON.parse(readEnvelopeBytes(meta.envelope_file)),
    );
    const form = document.querySelector('[data-next-action="3f9ac21d75e04b88"]')!;
    expect(form.querySelector(".errorlist")).not.toBeNull();
    expect(form.querySelector('input[aria-invalid="true"]')).not.toBeNull();
    expect(result.form).toEqual({
      uid: "3f9ac21d75e04b88",
      valid: false,
      errors: { title: ["This field is required."] },
    });
    expect(meta.headers["X-Next-Action"]).toBe("3f9ac21d75e04b88");
  });

  it("result_form_visit names a single internal visit href", () => {
    const meta = readMeta("result_form_visit");
    const envelope: Envelope = parseEnvelope(
      JSON.parse(readEnvelopeBytes(meta.envelope_file)),
    );
    expect(envelope.ops).toEqual([{ op: "visit", href: "/board/7/settings/" }]);
  });

  it("validate_form morphs the form by uid and carries scrubbed errors", () => {
    document.body.innerHTML =
      '<form data-next-action="ab12cd34"><input name="email" value="bad"></form>';
    const meta = readMeta("validate_form");
    const { applier } = makeApplier();
    const result: Envelope = applier.apply(
      JSON.parse(readEnvelopeBytes(meta.envelope_file)),
    );
    const form = document.querySelector('[data-next-action="ab12cd34"]')!;
    expect(form.querySelector('input[aria-invalid="true"]')).not.toBeNull();
    expect(result.form).toEqual({
      uid: "ab12cd34",
      valid: false,
      errors: { email: ["Enter a valid email address."] },
    });
    expect(meta.headers["X-Next-Form"]).toBeUndefined();
  });

  it("wizard_advance morphs the master zone to the next step form", () => {
    document.body.innerHTML =
      '<div data-next-zone="wizard-zone"><form data-next-action="ab12cd34">' +
      '<input name="name" value="Ada"></form></div>';
    const meta = readMeta("wizard_advance");
    const { applier } = makeApplier();
    applier.apply(JSON.parse(readEnvelopeBytes(meta.envelope_file)));
    const zone = document.querySelector('[data-next-zone="wizard-zone"]')!;
    expect(zone.querySelector('input[name="scope"]')).not.toBeNull();
    expect(zone.querySelector('input[name="name"]')).toBeNull();
  });

  it("append_page grows the list and replaces the sentinel by id", () => {
    document.body.innerHTML =
      '<ul data-next-zone="catalog-results">' +
      '<li data-next-key="11">eleven</li>' +
      '<li data-next-key="sentinel" id="sentinel">old</li></ul>';
    const meta = readMeta("append_page");
    const { applier } = makeApplier();
    applier.apply(JSON.parse(readEnvelopeBytes(meta.envelope_file)));
    const rows = document.querySelectorAll('[data-next-zone="catalog-results"] > li');
    const keys = Array.from(rows).map((li) => li.getAttribute("data-next-key"));
    expect(keys).toEqual(["11", "sentinel", "12"]);
    expect(document.querySelectorAll("#sentinel")).toHaveLength(1);
    expect(document.querySelector("#sentinel a")).not.toBeNull();
  });

  it("zone_get morphs both zones from a batched GET", () => {
    document.body.innerHTML =
      '<div data-next-zone="alpha"><p>stale</p></div>' +
      '<section data-next-zone="beta"><p>stale</p></section>';
    const meta = readMeta("zone_get");
    const { applier, dispatched } = makeApplier();
    const envelope: Envelope = applier.apply(
      JSON.parse(readEnvelopeBytes(meta.envelope_file)),
    );
    expect(document.querySelector('[data-next-zone="alpha"]')!.textContent).toBe(
      "alpha hi",
    );
    expect(document.querySelector('[data-next-zone="beta"]')!.textContent).toBe(
      "beta hi",
    );
    expect(envelope.assets).toEqual([
      { kind: "css", url: "/static/next/zoned.css", load: "link" },
    ]);
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(false);
  });

  it("sse_refresh re-GETs the named zone over the refresh seam", () => {
    const refresh = vi.fn();
    const applier = new Applier({
      dispatch: () => undefined,
      mergeContext: () => undefined,
      document,
      refresh,
      here: () => "/polls/7/",
    });
    const meta = readMeta("sse_refresh");
    const envelope: Envelope = applier.apply(
      JSON.parse(readEnvelopeBytes(meta.envelope_file)),
    );
    expect(envelope.request_id).toBe("r9");
    expect(refresh).toHaveBeenCalledWith({
      url: "/polls/7/",
      zone: "poll-results",
      headers: { "X-Next-Zone": "poll-results" },
    });
  });

  it("context_merge feeds the serialized values into the client context", () => {
    const merged: Record<string, unknown>[] = [];
    const dispatched: { event: string; detail: Record<string, unknown> }[] = [];
    const applier = new Applier({
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      mergeContext: (data) => merged.push(data),
      document,
    });
    const meta = readMeta("context_merge");
    applier.apply(JSON.parse(readEnvelopeBytes(meta.envelope_file)));
    expect(merged).toEqual([{ unread: 3, user: "ada" }]);
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(false);
  });
});

describe("layer golden fixtures apply through the layer stack", () => {
  function noopDialog(): DialogAdapter {
    return { open: () => () => undefined };
  }

  function makeLayerApplier() {
    const dispatched: { event: string; detail: Record<string, unknown> }[] = [];
    const dispatch = (event: string, detail: Record<string, unknown>): number =>
      dispatched.push({ event, detail });
    const layers = createStack({
      dispatch,
      chunk: landed(createLayers),
      build: (factory) =>
        factory({
          dispatch,
          fetch: async () => undefined,
          document,
          dialog: noopDialog(),
          navigation: createNavigation({ dispatch: () => undefined }),
          mark: markBusy,
        }),
      popped: () => undefined,
      navigate: () => undefined,
    });
    const applier = new Applier({
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      mergeContext: () => undefined,
      document,
      layers: () => layers,
    });
    return { applier, layers, dispatched };
  }

  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("layer_close accepts the open layer with its result and toasts the text", async () => {
    const { applier, layers, dispatched } = makeLayerApplier();
    await layers.open(null, "/request/identity/", "access-wizard");
    const meta = readMeta("layer_close");
    applier.apply(JSON.parse(readEnvelopeBytes(meta.envelope_file)));
    const accepted = dispatched.find((d) => d.event === "partial:layer-accepted");
    expect(accepted?.detail.result).toEqual({ id: 42 });
    expect(layers.size()).toBe(0);
    const toast = document.querySelector("[data-next-toasts] [data-next-toast]")!;
    expect(toast.textContent).toBe("Request created");
    layers._reset();
  });

  it("layer_oob_list closes the layer and morphs the host zone in one pass", async () => {
    document.body.innerHTML =
      '<div data-next-zone="request-list"><ul><li>stale</li></ul></div>';
    const { applier, layers } = makeLayerApplier();
    await layers.open(null, "/request/identity/", "access-wizard");
    const meta = readMeta("layer_oob_list");
    applier.apply(JSON.parse(readEnvelopeBytes(meta.envelope_file)));
    expect(layers.size()).toBe(0);
    const list = document.querySelector('[data-next-zone="request-list"]')!;
    expect(list.textContent).toContain("fresh");
    expect(list.querySelector("li")!.textContent).toBe("fresh");
    layers._reset();
  });
});

function opOf(name: string, op: string): Record<string, unknown> {
  const raw = JSON.parse(readEnvelopeBytes(readMeta(name).envelope_file)) as {
    ops: Record<string, unknown>[];
  };
  return raw.ops.find((entry) => entry.op === op)!;
}

describe("head golden fixtures", () => {
  beforeEach(() => {
    document.head.innerHTML =
      '<title>Stale</title><meta name="description" content="stale">' +
      '<meta name="robots" content="stale">';
  });

  it("meta_head syncs the four head tags", () => {
    expect(existsSync(join(GOLDEN_DIR, "meta_head.meta.json"))).toBe(true);
    const meta = opOf("meta_head", "meta");
    const { applier } = makeApplier();
    applier.apply(JSON.parse(readEnvelopeBytes(readMeta("meta_head").envelope_file)));
    const tag = (selector: string, attr: string): string | null =>
      document.head.querySelector(selector)?.getAttribute(attr) ?? null;
    expect(document.head.querySelector("title")?.textContent ?? null).toBe(meta.title);
    expect(tag('meta[name="description"]', "content")).toBe(meta.description);
    expect(tag('link[rel="canonical"]', "href")).toBe(meta.canonical);
    expect(tag('meta[name="robots"]', "content")).toBe(meta.robots);
  });
});

function readFormat(family: string, name: string): string {
  return readFileSync(join(GOLDEN_DIR, family, name), "utf-8");
}

// What each chunk script of a streamed document pushes, read without running it.
function pushed(root: ParentNode): (Fill | 0)[] {
  return [...root.querySelectorAll("script")].map((script) => {
    const found = /\.push\((.*)\);document/s.exec(script.text);
    script.remove();
    return JSON.parse(found![1]!) as Fill | 0;
  });
}

function drainHost(): DrainHost {
  const applier = new Applier({
    dispatch: () => undefined,
    mergeContext: () => undefined,
  });
  return {
    partial: {
      apply: (raw: unknown, options?: ApplyOptions) => applier.apply(raw, options),
    },
    mark: markBusy,
    busy: isBusy,
    stamped,
    fire: () => undefined,
    fallbacks: new WeakMap(),
  } as unknown as DrainHost;
}

async function settle(): Promise<void> {
  for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
}

describe("golden document chunks apply as the stream reader lands them", () => {
  beforeEach(() => {
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    document.head.innerHTML = "";
    delete window.$nx;
  });

  function land(name: string): void {
    document.body.innerHTML = readFormat("chunk", `${name}.html`);
    window.$nx = pushed(document.body);
    drain(drainHost(), () => false, {});
  }

  const boundary = (): HTMLElement => document.getElementById("b-5ea1c0de-0")!;

  it("ready fills the boundary from its template, the markup parsed once", async () => {
    land("ready");
    await settle();
    expect(boundary().getAttribute(ATTR_STATE)).toBe("ready");
    expect(boundary().querySelector("h2")!.textContent).toBe("Ready");
    expect(boundary().querySelector("p")!.textContent).toBe("Привет & 1 < 2");
    expect(document.querySelector("template")).toBeNull();
  });

  it("error settles the boundary with its kind and digest", async () => {
    land("error");
    await settle();
    expect(boundary().getAttribute(ATTR_STATE)).toBe("error");
    expect(boundary().getAttribute(ATTR_ERROR)).toBe("timeout");
    expect(boundary().getAttribute(ATTR_DIGEST)).toBe("c0ffee");
    expect(boundary().textContent).toBe("timed out");
  });

  it("nested fills a row boundary its parent's template brought in", async () => {
    land("nested");
    await settle();
    await settle();
    const row = document.getElementById("b-5ea1c0de-1")!;
    expect(row.tagName).toBe("TR");
    expect(row.textContent).toBe("cell");
    expect(row.getAttribute(ATTR_STATE)).toBe("ready");
    expect(document.querySelector("template")).toBeNull();
  });

  it("noindex settles a missing page body and marks the head", async () => {
    land("noindex");
    await settle();
    expect(boundary().getAttribute(ATTR_ERROR)).toBe("not_found");
    expect(
      document.head.querySelector('meta[name="robots"]')!.getAttribute("content"),
    ).toBe("noindex");
  });
});

describe("the golden partial stream reads as the reader reads it", () => {
  it("names the protocol, lands its envelope and fills its boundary", async () => {
    const text = readFormat("stream", "boundary.sse");
    const body = new Response(text).body!.getReader();
    const read: [string, unknown][] = [];
    for await (const [event, data] of frames(body))
      read.push([event, JSON.parse(data)]);
    expect(read.map(([event]) => event)).toEqual([
      EVENT_HELLO,
      EVENT_PATCH,
      EVENT_PATCH,
      EVENT_END,
    ]);
    expect(read[0]![1]).toEqual({ protocol: PROTOCOL, streamed: true });
    document.body.innerHTML = '<div data-next-zone="results"></div>';
    const { applier } = makeApplier();
    applier.apply((read[1]![1] as Fill).e);
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    const take = createDrain(drainHost(), { stale: () => false, scope: () => [] });
    take(read[2]![1] as Fill, 0);
    await settle();
    const filled = document.getElementById("b-5ea1c0de-0")!;
    expect(filled.textContent).toBe("filled");
    expect(filled.getAttribute(ATTR_STATE)).toBe("ready");
    expect(read[3]![1]).toEqual({ code: "complete" });
  });
});

describe("golden navigation envelopes", () => {
  interface Navigation {
    ops: Record<string, unknown>[];
    layouts: [string, string, string | null][];
    root: string | null;
    dropped?: { script: string; template: string | null };
  }

  const read = (name: string): Navigation =>
    JSON.parse(readFormat("navigation", `${name}.json`)) as Navigation;

  it("slot morphs the slot of the kept layout and keeps every response id", () => {
    const envelope = read("slot");
    const [, section] = envelope.layouts;
    expect(envelope.layouts.every(([, , responseId]) => responseId === null)).toBe(
      true,
    );
    document.body.innerHTML =
      `<header>h</header><!--nx:s ${section![0]} S--><h1>A</h1>` +
      `<!--/nx:s ${section![0]} S-->`;
    const applier = new Applier({
      dispatch: () => undefined,
      mergeContext: () => undefined,
      slot: (target) => markers(document, target.slot ?? "", "S"),
    });
    applier.apply(envelope);
    expect(document.querySelector("h1")!.textContent).toBe("B");
    expect(document.querySelector("header")!.textContent).toBe("h");
  });

  it("body morphs the whole body and hands every layout a fresh response id", () => {
    const envelope = read("body");
    expect(envelope.ops[0]!.target).toEqual({ body: true });
    expect(
      envelope.layouts.every(([, , responseId]) => typeof responseId === "string"),
    ).toBe(true);
    document.body.innerHTML = "<h1>A</h1>";
    const applier = new Applier({
      dispatch: () => undefined,
      mergeContext: () => undefined,
      slot: (target) => locate(document, [], target),
    });
    applier.apply(envelope);
    expect(document.querySelector("h1")!.textContent).toBe("B");
    expect(document.querySelector("script:not([type])")).toBeNull();
  });

  it("visit refuses the swap and names the script a DEBUG server dropped", () => {
    const envelope = read("visit");
    const navigate = vi.fn();
    const applier = new Applier({
      dispatch: () => undefined,
      mergeContext: () => undefined,
      navigate: () => navigate,
    });
    applier.apply(envelope);
    expect(navigate).toHaveBeenCalledWith("/sec/scripted/");
    expect(envelope.dropped).toEqual({
      script: '<script src="/w.js">',
      template: "/pages/sec/scripted/template.djx",
    });
  });
});
