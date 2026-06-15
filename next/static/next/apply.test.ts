import { beforeEach, describe, expect, it, vi } from "vitest";
import { Applier, parseEnvelope } from "./apply";
import type { Envelope } from "./apply";

type Dispatched = { event: string; detail: Record<string, unknown> };

function makeApplier(dev = false) {
  const dispatched: Dispatched[] = [];
  const merged: Record<string, unknown>[] = [];
  const applier = new Applier({
    dispatch: (event, detail) => dispatched.push({ event, detail }),
    mergeContext: (data) => merged.push(data),
    document,
    dev,
  });
  return { applier, dispatched, merged };
}

function envelope(ops: unknown[], extra: Record<string, unknown> = {}): unknown {
  return { version: "v1", ops, assets: [], defer: [], form: null, ...extra };
}

describe("parseEnvelope", () => {
  it("collapses absent meta to empty values", () => {
    const parsed = parseEnvelope({ version: "v1" });
    expect(parsed.ops).toEqual([]);
    expect(parsed.assets).toEqual([]);
    expect(parsed.defer).toEqual([]);
    expect(parsed.form).toBeNull();
    expect(parsed.csrf).toBeUndefined();
    expect(parsed.request_id).toBeUndefined();
  });

  it("reads csrf and request_id when present", () => {
    const parsed = parseEnvelope({
      version: "v1",
      csrf: { header: "X-CSRFToken", token: "abc" },
      request_id: "r1",
    });
    expect(parsed.csrf).toEqual({ header: "X-CSRFToken", token: "abc" });
    expect(parsed.request_id).toBe("r1");
  });

  it("drops a partial csrf object", () => {
    const parsed = parseEnvelope({ version: "v1", csrf: { header: "X" } });
    expect(parsed.csrf).toBeUndefined();
  });

  it("throws when the value is not an object", () => {
    expect(() => parseEnvelope("nope")).toThrow(TypeError);
  });

  it("throws when version is missing", () => {
    expect(() => parseEnvelope({ ops: [] })).toThrow(TypeError);
  });
});

describe("Applier verbs", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("replace swaps a zone wholesale", () => {
    document.body.innerHTML = '<div data-next-zone="z"><span>old</span></div>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        { op: "replace", target: { zone: "z" }, html: '<p data-next-zone="z">new</p>' },
      ]),
    );
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("new");
    expect(document.querySelectorAll('[data-next-zone="z"]')).toHaveLength(1);
  });

  it("inner replaces only the contents", () => {
    document.body.innerHTML = '<div data-next-zone="z"><span>old</span></div>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([{ op: "inner", target: { zone: "z" }, html: "<b>hi</b>" }]),
    );
    const zone = document.querySelector('[data-next-zone="z"]')!;
    expect(zone.innerHTML).toBe("<b>hi</b>");
  });

  it("remove deletes a target by selector", () => {
    document.body.innerHTML = '<div id="row-42">x</div>';
    const { applier } = makeApplier();
    applier.apply(envelope([{ op: "remove", target: { css: "#row-42" } }]));
    expect(document.querySelector("#row-42")).toBeNull();
  });

  it("resolves a form target by data-next-action", () => {
    document.body.innerHTML = '<form data-next-action="u1"><i>old</i></form>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([{ op: "inner", target: { form: "u1" }, html: "<i>new</i>" }]),
    );
    expect(document.querySelector('[data-next-action="u1"]')!.textContent).toBe("new");
  });

  it("resolves a field target by form uid and name", () => {
    document.body.innerHTML = '<form data-next-action="u1"><input name="email"></form>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "replace",
          target: { field: ["u1", "email"] },
          html: '<input name="email" value="x">',
        },
      ]),
    );
    const input = document.querySelector<HTMLInputElement>('[name="email"]')!;
    expect(input.value).toBe("x");
  });

  it("event dispatches a CustomEvent on document and the bus", () => {
    const { applier, dispatched } = makeApplier();
    const onDoc = vi.fn();
    document.addEventListener("request-created", onDoc);
    applier.apply(
      envelope([{ op: "event", name: "request-created", detail: { id: 42 } }]),
    );
    expect(onDoc).toHaveBeenCalledOnce();
    expect((onDoc.mock.calls[0][0] as CustomEvent).detail).toEqual({ id: 42 });
    expect(dispatched).toContainEqual({
      event: "request-created",
      detail: { id: 42 },
    });
    document.removeEventListener("request-created", onDoc);
  });

  it("skips an unknown op and emits partial:error, applying the rest", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const { applier, dispatched } = makeApplier();
    applier.apply(
      envelope([
        { op: "frobnicate", target: { zone: "z" } },
        { op: "inner", target: { zone: "z" }, html: "new" },
      ]),
    );
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("new");
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(true);
  });

  it("is a no-op when the target is absent from the document", () => {
    const { applier } = makeApplier();
    expect(() =>
      applier.apply(
        envelope([{ op: "inner", target: { zone: "missing" }, html: "x" }]),
      ),
    ).not.toThrow();
  });

  it("is a no-op for a target object with no recognised key", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const { applier } = makeApplier();
    applier.apply(envelope([{ op: "inner", target: {}, html: "x" }]));
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("old");
  });

  it("is a no-op for a remove without a target", () => {
    const { applier } = makeApplier();
    expect(() => applier.apply(envelope([{ op: "remove" }]))).not.toThrow();
  });

  it("skips an event op without a name", () => {
    const { applier, dispatched } = makeApplier();
    applier.apply(envelope([{ op: "event", detail: { a: 1 } }]));
    expect(
      dispatched.filter(
        (d) => d.event !== "partial:before-apply" && d.event !== "partial:applied",
      ),
    ).toEqual([]);
  });
});

describe("Applier script neutralisation", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("removes every script from a patch before insertion", () => {
    document.body.innerHTML = '<div data-next-zone="z"></div>';
    const ran = vi.fn();
    (window as unknown as { __ran: () => void }).__ran = ran;
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "inner",
          target: { zone: "z" },
          html: "<p>safe</p><script>window.__ran()</script>",
        },
      ]),
    );
    expect(document.querySelector('[data-next-zone="z"] script')).toBeNull();
    expect(ran).not.toHaveBeenCalled();
  });

  it("warns on each neutralised script in dev builds", () => {
    document.body.innerHTML = '<div data-next-zone="z"></div>';
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { applier } = makeApplier(true);
    applier.apply(
      envelope([
        {
          op: "inner",
          target: { zone: "z" },
          html: "<script>1</script><script>2</script>",
        },
      ]),
    );
    expect(warn).toHaveBeenCalledTimes(2);
    warn.mockRestore();
  });

  it("stays silent on neutralised scripts in non-dev builds", () => {
    document.body.innerHTML = '<div data-next-zone="z"></div>';
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { applier } = makeApplier(false);
    applier.apply(
      envelope([{ op: "inner", target: { zone: "z" }, html: "<script>1</script>" }]),
    );
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });
});

describe("Applier lifecycle events", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("emits before-apply then applied", () => {
    const { applier, dispatched } = makeApplier();
    applier.apply(envelope([]));
    const names = dispatched.map((d) => d.event);
    expect(names).toEqual(["partial:before-apply", "partial:applied"]);
  });

  it("a cancelled before-apply skips the ops", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const { applier } = makeApplier();
    document.addEventListener("partial:before-apply", (e) => e.preventDefault(), {
      once: true,
    });
    applier.apply(envelope([{ op: "inner", target: { zone: "z" }, html: "new" }]));
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("old");
  });
});

describe("Applier custom ops and reset", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("runs a registered custom op", () => {
    const { applier } = makeApplier();
    const seen: unknown[] = [];
    applier.defineOp("confetti", (patch) => seen.push(patch.origin));
    applier.apply(envelope([{ op: "confetti", origin: "button" }]));
    expect(seen).toEqual(["button"]);
  });

  it("_reset drops custom ops and keeps built-ins", () => {
    const { applier, dispatched } = makeApplier();
    applier.defineOp("confetti", () => undefined);
    applier._reset();
    applier.apply(envelope([{ op: "confetti" }]));
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(true);
  });
});

describe("Applier csrf rotation", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("rotates the token in every form of the document", () => {
    document.body.innerHTML =
      '<form><input name="csrfmiddlewaretoken" value="old"></form>' +
      '<form><input name="csrfmiddlewaretoken" value="old"></form>';
    const { applier } = makeApplier();
    const result: Envelope = applier.apply(
      envelope([], { csrf: { header: "X-CSRFToken", token: "fresh" } }),
    );
    const inputs = document.querySelectorAll<HTMLInputElement>(
      'input[name="csrfmiddlewaretoken"]',
    );
    expect([...inputs].map((i) => i.value)).toEqual(["fresh", "fresh"]);
    expect(result.csrf).toEqual({ header: "X-CSRFToken", token: "fresh" });
  });
});
