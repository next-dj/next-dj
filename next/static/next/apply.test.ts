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
    expect(seen[0].target).toBe(old);
    expect(seen[0].connected).toBe(true);
    expect(seen[0].bubbles).toBe(true);
    expect(seen[0].cancelable).toBe(false);
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
    expect(seen[0].target).toBe(node);
    expect(seen[0].connected).toBe(true);
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
    expect(seen[0].target).toBe(old);
    expect(seen[0].connected).toBe(true);
    expect(seen[0].cancelable).toBe(false);
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

  it("morph is a no-op when the target is missing", () => {
    const { applier } = makeApplier();
    expect(() =>
      applier.apply(
        envelope([{ op: "morph", target: { zone: "missing" }, html: "<p>x</p>" }]),
      ),
    ).not.toThrow();
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

  it("layer.close carries result, dismiss, and reason to the stack", () => {
    const { applier, calls } = makeLayerApplier();
    applier.apply(envelope([{ op: "layer.close", result: { id: 7 }, dismiss: false }]));
    expect(calls[0]).toEqual({
      verb: "close",
      args: [{ result: { id: 7 }, dismiss: false, reason: undefined }],
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
