import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Applier, parseEnvelope } from "./apply";
import type { Envelope } from "./apply";
import { createLayers } from "./layers";
import type { DialogAdapter } from "./layers";

const GOLDEN_DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../tests/partial/golden",
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
    expect(zones[0].querySelector("ul")).not.toBeNull();
    expect(zones[0].querySelector("span")).toBeNull();
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
    expect((onDoc.mock.calls[0][0] as CustomEvent).detail).toEqual({ id: 42 });
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
    const layers = createLayers({
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      fetch: async () => undefined,
      document,
      dialog: noopDialog(),
    });
    const applier = new Applier({
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      mergeContext: () => undefined,
      document,
      layers,
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
