import { beforeEach, describe, expect, it, vi } from "vitest";
import { Applier, parseEnvelope } from "./apply";
import type { Asset, AssetBridge, Envelope } from "./apply";

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
  return { version: "v1", ops, assets: [], form: null, ...extra };
}

describe("parseEnvelope", () => {
  it("collapses absent meta to empty values", () => {
    const parsed = parseEnvelope({ version: "v1" });
    expect(parsed.ops).toEqual([]);
    expect(parsed.assets).toEqual([]);
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

  it("drops a non-record op rather than carrying it into apply", () => {
    const parsed = parseEnvelope({
      version: "v1",
      ops: [null, { op: "inner" }, "nope", 7],
    });
    expect(parsed.ops).toEqual([{ op: "inner" }]);
  });

  it("collapses a non-record form-errors value to an empty map", () => {
    const parsed = parseEnvelope({
      version: "v1",
      form: { uid: "u1", valid: false, errors: "nope" },
    });
    expect(parsed.form).toEqual({ uid: "u1", valid: false, errors: {} });
  });

  it("drops a form-errors field whose messages are not all strings", () => {
    const parsed = parseEnvelope({
      version: "v1",
      form: {
        uid: "u1",
        valid: false,
        errors: { email: ["bad"], tags: ["ok", 7] },
      },
    });
    expect(parsed.form!.errors).toEqual({ email: ["bad"] });
  });

  it("defaults a form uid to an empty string when absent", () => {
    const parsed = parseEnvelope({ version: "v1", form: { valid: true } });
    expect(parsed.form).toEqual({ uid: "", valid: true, errors: {} });
  });

  it("drops a malformed asset rather than carrying it past the boundary", () => {
    const parsed = parseEnvelope({
      version: "v1",
      assets: [
        { kind: "css", url: "/ok.css" },
        null,
        { kind: "js" },
        { url: "/no-kind.js" },
        "nope",
        { kind: "css", url: "/also-ok.css" },
      ],
    });
    expect(parsed.assets).toEqual([
      { kind: "css", url: "/ok.css" },
      { kind: "css", url: "/also-ok.css" },
    ]);
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
    expect((onDoc.mock.calls[0]![0] as CustomEvent).detail).toEqual({ id: 42 });
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
    const err = dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.kind).toBe("op");
    expect(err!.detail.op).toBe("frobnicate");
    expect((err!.detail.error as Error).message).toBe("unknown op frobnicate");
  });

  it("marks partial:applied as degraded when an unknown op is skipped", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const { applier, dispatched } = makeApplier();
    applier.apply(envelope([{ op: "frobnicate", target: { zone: "z" } }]));
    const applied = dispatched.find((d) => d.event === "partial:applied");
    expect(applied!.detail.ok).toBe(false);
  });

  it("skips a non-record op without poisoning the rest of the envelope", () => {
    document.body.innerHTML =
      '<div data-next-zone="a">stale</div><div data-next-zone="b">stale</div>';
    const { applier, dispatched } = makeApplier();
    applier.apply(
      envelope([
        { op: "inner", target: { zone: "a" }, html: "fresh" },
        null,
        { op: "inner", target: { zone: "b" }, html: "fresh" },
      ]),
    );
    expect(document.querySelector('[data-next-zone="a"]')!.textContent).toBe("fresh");
    expect(document.querySelector('[data-next-zone="b"]')!.textContent).toBe("fresh");
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(false);
    const applied = dispatched.find((d) => d.event === "partial:applied");
    expect(applied!.detail.ok).toBe(true);
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

  it("coerces a non-record event detail to an empty object", () => {
    const { applier, dispatched } = makeApplier();
    const onDoc = vi.fn();
    document.addEventListener("ping", onDoc);
    applier.apply(envelope([{ op: "event", name: "ping", detail: "scalar" }]));
    expect((onDoc.mock.calls[0]![0] as CustomEvent).detail).toEqual({});
    expect(dispatched).toContainEqual({ event: "ping", detail: {} });
    document.removeEventListener("ping", onDoc);
  });

  it("contains a throwing op and surfaces it as partial:error", () => {
    const { applier, dispatched } = makeApplier();
    applier.defineOp("boom", () => {
      throw new Error("op blew up");
    });
    applier.apply(envelope([{ op: "boom" }]));
    const err = dispatched.find((d) => d.event === "partial:error");
    expect((err!.detail.error as Error).message).toBe("op blew up");
    expect(err!.detail.kind).toBe("op");
    expect(err!.detail.op).toBe("boom");
    const applied = dispatched.find((d) => d.event === "partial:applied");
    expect(applied!.detail.ok).toBe(false);
  });

  it("url without an href is a no-op", () => {
    const calls: string[] = [];
    const applier = new Applier({
      dispatch: () => undefined,
      mergeContext: () => undefined,
      document,
      history: { push: (h) => calls.push(h), replace: (h) => calls.push(h) },
    });
    applier.apply(envelope([{ op: "url" }]));
    expect(calls).toEqual([]);
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

  it("context merges server-serialised values into the client context", () => {
    const { applier, merged } = makeApplier();
    applier.apply(envelope([{ op: "context", data: { user: "x", count: 3 } }]));
    expect(merged).toEqual([{ user: "x", count: 3 }]);
  });

  it("context is no longer an unknown op and emits no partial:error", () => {
    const { applier, dispatched } = makeApplier();
    applier.apply(envelope([{ op: "context", data: { ok: true } }]));
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(false);
  });

  it("context with an empty data object merges nothing of substance", () => {
    const { applier, merged } = makeApplier();
    applier.apply(envelope([{ op: "context", data: {} }]));
    expect(merged).toEqual([{}]);
  });

  it("context without a data payload skips the merge", () => {
    const { applier, merged } = makeApplier();
    applier.apply(envelope([{ op: "context" }]));
    expect(merged).toEqual([]);
  });

  it("context with a null data payload skips the merge", () => {
    const { applier, merged } = makeApplier();
    applier.apply(envelope([{ op: "context", data: null }]));
    expect(merged).toEqual([]);
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
    const applied = dispatched.find((d) => d.event === "partial:applied");
    expect(applied!.detail.ok).toBe(true);
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

describe("Applier next:removed before detach", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  // Capture next:removed at the document, the bus the layer adapter delegates
  // on, recording the target, whether it was still connected at fire time, and
  // the event flags.
  function captureRemoved() {
    const seen: {
      target: Element;
      connected: boolean;
      bubbles: boolean;
      cancelable: boolean;
    }[] = [];
    const listener = (event: Event): void => {
      const target = event.target as Element;
      seen.push({
        target,
        connected: target.isConnected,
        bubbles: event.bubbles,
        cancelable: event.cancelable,
      });
    };
    document.addEventListener("next:removed", listener);
    return {
      seen,
      stop: () => document.removeEventListener("next:removed", listener),
    };
  }

  it("replace fires next:removed on the old node before it detaches", () => {
    document.body.innerHTML = '<div data-next-zone="z"><span>old</span></div>';
    const old = document.querySelector('[data-next-zone="z"]')!;
    const { applier } = makeApplier();
    const { seen, stop } = captureRemoved();
    applier.apply(
      envelope([
        { op: "replace", target: { zone: "z" }, html: '<p data-next-zone="z">new</p>' },
      ]),
    );
    stop();
    expect(seen).toHaveLength(1);
    expect(seen[0]!.target).toBe(old);
    expect(seen[0]!.connected).toBe(true);
    expect(seen[0]!.bubbles).toBe(true);
    expect(seen[0]!.cancelable).toBe(false);
  });

  it("inner fires next:removed on each old child before the swap", () => {
    document.body.innerHTML =
      '<div data-next-zone="z"><span>a</span><span>b</span></div>';
    const zone = document.querySelector('[data-next-zone="z"]')!;
    const children = Array.from(zone.children);
    const { applier } = makeApplier();
    const { seen, stop } = captureRemoved();
    applier.apply(envelope([{ op: "inner", target: { zone: "z" }, html: "<i>x</i>" }]));
    stop();
    expect(seen.map((s) => s.target)).toEqual(children);
    expect(seen.every((s) => s.connected)).toBe(true);
    expect(seen.every((s) => s.bubbles && !s.cancelable)).toBe(true);
  });

  it("remove fires next:removed on the target before it detaches", () => {
    document.body.innerHTML = '<div id="row-42">x</div>';
    const node = document.querySelector("#row-42")!;
    const { applier } = makeApplier();
    const { seen, stop } = captureRemoved();
    applier.apply(envelope([{ op: "remove", target: { css: "#row-42" } }]));
    stop();
    expect(seen).toHaveLength(1);
    expect(seen[0]!.target).toBe(node);
    expect(seen[0]!.connected).toBe(true);
  });

  it("merge fires next:removed on a deduped node it replaces in place", () => {
    document.body.innerHTML =
      '<ul data-next-zone="z"><li data-next-key="a">old</li></ul>';
    const old = document.querySelector('[data-next-key="a"]')!;
    const { applier } = makeApplier();
    const { seen, stop } = captureRemoved();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "z" },
          html: '<li data-next-key="a">new</li>',
        },
      ]),
    );
    stop();
    expect(seen).toHaveLength(1);
    expect(seen[0]!.target).toBe(old);
    expect(seen[0]!.connected).toBe(true);
    expect(seen[0]!.cancelable).toBe(false);
  });

  it("morph fires next:removed on a discarded trailing child", () => {
    document.body.innerHTML =
      '<ul data-next-zone="z"><li id="a">a</li><li id="b">b</li></ul>';
    const tail = document.querySelector("#b")!;
    const { applier } = makeApplier();
    const { seen, stop } = captureRemoved();
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { zone: "z" },
          html: '<ul data-next-zone="z"><li id="a">a</li></ul>',
        },
      ]),
    );
    stop();
    expect(seen.some((s) => s.target === tail && s.connected)).toBe(true);
    expect(seen.every((s) => s.bubbles && !s.cancelable)).toBe(true);
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

describe("Applier morph verb", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("morph reuses the live node and preserves an active value", () => {
    document.body.innerHTML =
      '<form data-next-action="u1"><input name="email" value="server"></form>';
    const input = document.querySelector<HTMLInputElement>('[name="email"]')!;
    input.value = "typed";
    input.focus();
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { form: "u1" },
          html: '<form data-next-action="u1"><input name="email" value="server"></form>',
        },
      ]),
    );
    expect(document.querySelector('[name="email"]')).toBe(input);
    expect(input.value).toBe("typed");
  });

  it("morph syncs an inactive field to the server value", () => {
    document.body.innerHTML = '<div data-next-zone="z"><p>old</p></div>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { zone: "z" },
          html: '<div data-next-zone="z"><p>new</p></div>',
        },
      ]),
    );
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("new");
  });

  it("morph neutralises scripts before the engine sees them", () => {
    document.body.innerHTML = '<div data-next-zone="z"></div>';
    const ran = vi.fn();
    (window as unknown as { __ran: () => void }).__ran = ran;
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { zone: "z" },
          html: '<div data-next-zone="z"><p>safe</p><script>window.__ran()</script></div>',
        },
      ]),
    );
    expect(document.querySelector('[data-next-zone="z"] script')).toBeNull();
    expect(ran).not.toHaveBeenCalled();
  });

  it("morph with extract carves the target out of a full document", () => {
    document.body.innerHTML = '<div data-next-zone="z"><span>old</span></div>';
    const { applier } = makeApplier();
    const full =
      "<html><body><header>chrome</header>" +
      '<div data-next-zone="z"><span>fresh</span></div></body></html>';
    applier.apply(
      envelope([{ op: "morph", target: { zone: "z" }, html: full, extract: true }]),
    );
    const zone = document.querySelector('[data-next-zone="z"]')!;
    expect(zone.textContent).toBe("fresh");
    expect(document.querySelector("header")).toBeNull();
  });

  it("morph with extract keeps a table row in its table context", () => {
    document.body.innerHTML =
      '<table><tbody><tr data-next-zone="r"><td>old</td></tr></tbody></table>';
    const { applier } = makeApplier();
    // A full-document reply: text/html parsing seats the tr inside its table, so
    // extract finds an intact <tr> rather than a stripped fragment.
    const full =
      "<html><body><table><tbody>" +
      '<tr data-next-zone="r"><td>fresh</td></tr></tbody></table></body></html>';
    applier.apply(
      envelope([{ op: "morph", target: { zone: "r" }, html: full, extract: true }]),
    );
    const row = document.querySelector('[data-next-zone="r"]')!;
    expect(row.tagName).toBe("TR");
    expect(row.textContent).toBe("fresh");
  });

  it("morph with extract is a no-op when the target is absent from the document", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { zone: "z" },
          html: "<html><body><p>no zone here</p></body></html>",
          extract: true,
        },
      ]),
    );
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("old");
  });

  it("resolves a form target to the instance carrying the request key", () => {
    document.body.innerHTML =
      '<form data-next-action="u1" data-next-key="a"><i>A</i></form>' +
      '<form data-next-action="u1" data-next-key="b"><i>B</i></form>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([{ op: "inner", target: { form: "u1" }, html: "<i>hit</i>" }]),
      undefined,
      "b",
    );
    const forms = document.querySelectorAll('[data-next-action="u1"]');
    expect(forms[0]!.textContent).toBe("A");
    expect(forms[1]!.textContent).toBe("hit");
  });

  it("resolves a form target to the first match when no request key is set", () => {
    document.body.innerHTML =
      '<form data-next-action="u1" data-next-key="a"><i>A</i></form>' +
      '<form data-next-action="u1" data-next-key="b"><i>B</i></form>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([{ op: "inner", target: { form: "u1" }, html: "<i>hit</i>" }]),
    );
    const forms = document.querySelectorAll('[data-next-action="u1"]');
    expect(forms[0]!.textContent).toBe("hit");
    expect(forms[1]!.textContent).toBe("B");
  });

  it("falls back to the first uid match when the key is absent from the root", () => {
    document.body.innerHTML = '<form data-next-action="u1"><i>A</i></form>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([{ op: "inner", target: { form: "u1" }, html: "<i>hit</i>" }]),
      undefined,
      "missing",
    );
    expect(document.querySelector('[data-next-action="u1"]')!.textContent).toBe("hit");
  });

  it("extract carves the form instance matching the request key", () => {
    document.body.innerHTML =
      '<form data-next-action="u1" data-next-key="a"><i>A</i></form>' +
      '<form data-next-action="u1" data-next-key="b"><i>B</i></form>';
    const { applier } = makeApplier();
    const full =
      "<html><body>" +
      '<form data-next-action="u1" data-next-key="a"><i>A-fresh</i></form>' +
      '<form data-next-action="u1" data-next-key="b"><i>B-fresh</i></form>' +
      "</body></html>";
    applier.apply(
      envelope([{ op: "morph", target: { form: "u1" }, html: full, extract: true }]),
      undefined,
      "b",
    );
    const forms = document.querySelectorAll('[data-next-action="u1"]');
    expect(forms[0]!.textContent).toBe("A");
    expect(forms[1]!.textContent).toBe("B-fresh");
  });

  it("morph is a no-op when the target is missing", () => {
    const { applier } = makeApplier();
    expect(() =>
      applier.apply(
        envelope([{ op: "morph", target: { zone: "missing" }, html: "<p>x</p>" }]),
      ),
    ).not.toThrow();
  });

  it("treats every field as clean under a snapshot with no dirty predicate wired", () => {
    document.body.innerHTML =
      '<form data-next-action="u1"><input name="email" value="old"></form>';
    const input = document.querySelector<HTMLInputElement>('[name="email"]')!;
    input.value = "typed";
    // No dirtySince dep, so the default predicate runs for the threaded snapshot
    // and reports no field dirty: the server value still wins.
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { form: "u1" },
          html: '<form data-next-action="u1"><input name="email" value="new"></form>',
        },
      ]),
      0,
    );
    expect(input.value).toBe("new");
  });

  it("morph treats every field as clean when no snapshot threads in", () => {
    document.body.innerHTML =
      '<form data-next-action="u1"><input name="email" value="old"></form>';
    const input = document.querySelector<HTMLInputElement>('[name="email"]')!;
    input.value = "typed";
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { form: "u1" },
          html: '<form data-next-action="u1"><input name="email" value="new"></form>',
        },
      ]),
    );
    expect(input.value).toBe("new");
  });

  it("morph by field target resolves a named field inside a form", () => {
    document.body.innerHTML =
      '<form data-next-action="u1"><input name="email" value="old"></form>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { field: ["u1", "email"] },
          html: '<input name="email" value="new">',
        },
      ]),
    );
    expect(document.querySelector<HTMLInputElement>('[name="email"]')!.value).toBe(
      "new",
    );
  });

  it("morph by field target is a no-op when the form is missing", () => {
    const { applier } = makeApplier();
    expect(() =>
      applier.apply(
        envelope([
          { op: "morph", target: { field: ["gone", "email"] }, html: "<input>" },
        ]),
      ),
    ).not.toThrow();
  });

  it("does not sync a dirty field built from the wire snapshot", () => {
    document.body.innerHTML =
      '<form data-next-action="u1"><input name="email" value="old"></form>';
    const input = document.querySelector<HTMLInputElement>('[name="email"]')!;
    input.value = "typed";
    const dispatched: Dispatched[] = [];
    const applier = new Applier({
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      mergeContext: () => undefined,
      document,
      dirtySince: () => (field) => field === input,
    });
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { form: "u1" },
          html: '<form data-next-action="u1"><input name="email" value="new"></form>',
        },
      ]),
      0,
    );
    expect(input.value).toBe("typed");
  });
});

describe("Applier verbs default an absent html to empty", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("morph with no html parses an empty fragment without throwing", () => {
    document.body.innerHTML = '<div data-next-zone="z"><span>old</span></div>';
    const { applier } = makeApplier();
    expect(() =>
      applier.apply(envelope([{ op: "morph", target: { zone: "z" } }])),
    ).not.toThrow();
    expect(document.querySelector('[data-next-zone="z"]')).not.toBeNull();
  });

  it("replace with no html drops the target with no replacement node", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const { applier } = makeApplier();
    applier.apply(envelope([{ op: "replace", target: { zone: "z" } }]));
    expect(document.querySelector('[data-next-zone="z"]')).toBeNull();
  });

  it("inner with no html clears the target contents", () => {
    document.body.innerHTML = '<div data-next-zone="z"><span>old</span></div>';
    const { applier } = makeApplier();
    applier.apply(envelope([{ op: "inner", target: { zone: "z" } }]));
    expect(document.querySelector('[data-next-zone="z"]')!.innerHTML).toBe("");
  });

  it("append with no html appends nothing", () => {
    document.body.innerHTML = '<ul data-next-zone="z"><li>a</li></ul>';
    const { applier } = makeApplier();
    applier.apply(envelope([{ op: "append", target: { zone: "z" } }]));
    expect(document.querySelectorAll("li")).toHaveLength(1);
  });
});

describe("Applier replace stays a wholesale opt-out", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("replace is a no-op when the target is missing", () => {
    const { applier } = makeApplier();
    expect(() =>
      applier.apply(
        envelope([{ op: "replace", target: { zone: "gone" }, html: "<p>x</p>" }]),
      ),
    ).not.toThrow();
  });

  it("replace swaps the node and does not preserve a live value", () => {
    document.body.innerHTML =
      '<form data-next-action="u1"><input name="email" value="server"></form>';
    const old = document.querySelector<HTMLInputElement>('[name="email"]')!;
    old.value = "typed";
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "replace",
          target: { form: "u1" },
          html: '<form data-next-action="u1"><input name="email" value="server"></form>',
        },
      ]),
    );
    const fresh = document.querySelector<HTMLInputElement>('[name="email"]')!;
    expect(fresh).not.toBe(old);
    expect(fresh.value).toBe("server");
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

describe("Applier layer, toast, and url verbs", () => {
  function makeLayerApplier() {
    const calls: { verb: string; args: unknown[] }[] = [];
    const layers = {
      resolveZone: (name: string, root: ParentNode) =>
        root.querySelector(`[data-next-zone="${name}"]`),
      open: (opener: null, href: string, zone: string) =>
        calls.push({ verb: "open", args: [opener, href, zone] }),
      close: (detail: Record<string, unknown>) =>
        calls.push({ verb: "close", args: [detail] }),
      toast: (text: string, variant: string) =>
        calls.push({ verb: "toast", args: [text, variant] }),
    };
    const history = {
      push: (href: string) => calls.push({ verb: "push", args: [href] }),
      replace: (href: string) => calls.push({ verb: "replace", args: [href] }),
    };
    const applier = new Applier({
      dispatch: () => undefined,
      mergeContext: () => undefined,
      document,
      layers,
      history,
    });
    return { applier, calls };
  }

  it("layer.open routes an href and zone into the stack with a null opener", () => {
    const { applier, calls } = makeLayerApplier();
    applier.apply(envelope([{ op: "layer.open", href: "/w/", zone: "wiz" }]));
    expect(calls).toEqual([{ verb: "open", args: [null, "/w/", "wiz"] }]);
  });

  it("layer.open without a zone or href is a no-op", () => {
    const { applier, calls } = makeLayerApplier();
    applier.apply(envelope([{ op: "layer.open", href: "/w/" }]));
    expect(calls).toEqual([]);
  });

  it("toast without text is a no-op", () => {
    const { applier, calls } = makeLayerApplier();
    applier.apply(envelope([{ op: "toast" }]));
    expect(calls).toEqual([]);
  });

  it("layer.close carries result and dismiss, omitting an absent reason", () => {
    const { applier, calls } = makeLayerApplier();
    applier.apply(envelope([{ op: "layer.close", result: { id: 7 }, dismiss: false }]));
    expect(calls[0]).toEqual({
      verb: "close",
      args: [{ result: { id: 7 }, dismiss: false }],
    });
  });

  it("layer.close threads an explicit reason to the stack", () => {
    const { applier, calls } = makeLayerApplier();
    applier.apply(envelope([{ op: "layer.close", dismiss: true, reason: "escape" }]));
    expect(calls[0]).toEqual({
      verb: "close",
      args: [{ result: undefined, dismiss: true, reason: "escape" }],
    });
  });

  it("toast hands text and a defaulted variant to the stack", () => {
    const { applier, calls } = makeLayerApplier();
    applier.apply(envelope([{ op: "toast", text: "saved" }]));
    expect(calls).toEqual([{ verb: "toast", args: ["saved", "info"] }]);
  });

  it("url pushes by default and replaces on the explicit action", () => {
    const { applier, calls } = makeLayerApplier();
    applier.apply(
      envelope([
        { op: "url", href: "/a/" },
        { op: "url", action: "replace", href: "/b/" },
      ]),
    );
    expect(calls).toEqual([
      { verb: "push", args: ["/a/"] },
      { verb: "replace", args: ["/b/"] },
    ]);
  });

  it("a zone target resolves through the layer bridge first", () => {
    document.body.innerHTML = '<div data-next-zone="z">page</div>';
    const { applier } = makeLayerApplier();
    applier.apply(envelope([{ op: "inner", target: { zone: "z" }, html: "patched" }]));
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("patched");
  });
});

describe("Applier visit verb", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  function makeVisitApplier() {
    const visited: string[] = [];
    const dispatched: Dispatched[] = [];
    const applier = new Applier({
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      mergeContext: () => undefined,
      document,
      navigate: (url) => visited.push(url),
    });
    return { applier, visited, dispatched };
  }

  it("visit navigates to an internal href and emits no error", () => {
    const { applier, visited, dispatched } = makeVisitApplier();
    applier.apply(envelope([{ op: "visit", href: "/dashboard/" }]));
    expect(visited).toEqual(["/dashboard/"]);
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(false);
  });

  it("visit navigates to a cross-origin href through the same seam", () => {
    const { applier, visited } = makeVisitApplier();
    applier.apply(envelope([{ op: "visit", href: "https://example.com/oauth/" }]));
    expect(visited).toEqual(["https://example.com/oauth/"]);
  });

  it("visit without an href is a no-op", () => {
    const { applier, visited, dispatched } = makeVisitApplier();
    applier.apply(envelope([{ op: "visit" }]));
    expect(visited).toEqual([]);
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(false);
  });

  it("visit is a no-op when no navigate seam is wired", () => {
    const dispatched: Dispatched[] = [];
    const applier = new Applier({
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      mergeContext: () => undefined,
      document,
    });
    expect(() => applier.apply(envelope([{ op: "visit", href: "/x/" }]))).not.toThrow();
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(false);
  });
});

describe("Applier keeps overlapping applies apart across the CSS gate", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  // An asset bridge whose loadCss defers done whenever the manifest ships CSS,
  // so a test resumes the gated apply by hand. With no CSS the gate is
  // straight-through, the path a same-tick second apply takes.
  function deferringAssets(): { bridge: AssetBridge; flush: () => void } {
    const pending: (() => void)[] = [];
    const bridge: AssetBridge = {
      loadCss(manifest: Asset[], done: () => void) {
        if (manifest.some((a) => a.kind === "css")) pending.push(done);
        else done();
      },
      loadJs: () => undefined,
      versionMismatch: () => false,
      acceptVersion: () => undefined,
    };
    return {
      bridge,
      flush: () => {
        for (const done of pending.splice(0)) done();
      },
    };
  }

  it("binds each envelope's dirty predicate, request key, and touched set to its own apply", () => {
    document.body.innerHTML =
      '<form data-next-action="u1" data-next-key="a"><input name="f" value="server-a"></form>' +
      '<form data-next-action="u1" data-next-key="b"><input name="f" value="server-b"></form>';
    const forms = document.querySelectorAll<HTMLFormElement>('[data-next-action="u1"]');
    const inputA = forms[0]!.querySelector<HTMLInputElement>('[name="f"]')!;
    const inputB = forms[1]!.querySelector<HTMLInputElement>('[name="f"]')!;
    inputA.value = "typed-a";
    inputB.value = "typed-b";
    const { bridge, flush } = deferringAssets();
    // The snapshot picks which field the apply protects, so a predicate leaked
    // from the other apply would protect the wrong form's input. Apply A
    // (snapshot 1) protects its own input a, apply B (snapshot 2) input b.
    const applier = new Applier({
      dispatch: () => undefined,
      mergeContext: () => undefined,
      document,
      assets: bridge,
      dirtySince: (snapshot) => (field) =>
        snapshot === 1 ? field === inputA : field === inputB,
    });
    const mounted: string[] = [];
    const onMount = (event: Event): void => {
      const form = (event.target as Element).closest("form");
      mounted.push(form?.getAttribute("data-next-key") ?? "");
    };
    document.addEventListener("next:mounted", onMount);

    // Apply A ships CSS so its ops defer. It carries key a and a marker on its
    // morph html, so a leaked key would land this marker on form b.
    applier.apply(
      envelope(
        [
          {
            op: "morph",
            target: { form: "u1" },
            html: '<form data-next-action="u1" data-next-key="a" data-from="A"><input name="f" value="server-a-fresh"></form>',
          },
        ],
        { assets: [{ kind: "css", url: "/a.css" }] },
      ),
      1,
      "a",
    );
    // Apply B runs to completion in the same tick: no CSS, straight-through
    // gate. It carries key b and snapshot 2.
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { form: "u1" },
          html: '<form data-next-action="u1" data-next-key="b" data-from="B"><input name="f" value="server-b-fresh"></form>',
        },
      ]),
      2,
      "b",
    );

    // B already ran against its own form: marker on form b, its dirty input b
    // kept its typed value, and only form b mounted.
    expect(forms[1]!.getAttribute("data-from")).toBe("B");
    expect(inputB.value).toBe("typed-b");
    expect(forms[0]!.hasAttribute("data-from")).toBe(false);
    expect(mounted).toEqual(["b"]);

    // Resume A. With the per-apply state bound, A lands on form a (its key), A's
    // marker is on form a not b, A's predicate protects input a, and the mount
    // pass fires on form a.
    flush();
    expect(forms[0]!.getAttribute("data-from")).toBe("A");
    expect(forms[1]!.getAttribute("data-from")).toBe("B");
    expect(inputA.value).toBe("typed-a");
    expect(mounted).toEqual(["b", "a"]);

    document.removeEventListener("next:mounted", onMount);
  });
});
