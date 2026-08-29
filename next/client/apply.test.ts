import { beforeEach, describe, expect, it, vi } from "vitest";
import { Applier, parseEnvelope } from "./apply";
import type { Asset, AssetBridge, Envelope } from "./apply";

interface Dispatched {
  event: string;
  detail: Record<string, unknown>;
}

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

// Both boundary channels at once, so a case asserting one of them also asserts
// the silence of the other.
function spyConsole() {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const debug = vi.spyOn(console, "debug").mockImplementation(() => {});
  return {
    warn,
    debug,
    restore: () => {
      warn.mockRestore();
      debug.mockRestore();
    },
  };
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

  it("drops a record that names no verb and one whose verb is not a string", () => {
    const parsed = parseEnvelope({
      version: "v1",
      ops: [{}, { op: 7 }, { op: "inner" }, { target: { zone: "z" }, html: "hi" }],
    });
    expect(parsed.ops).toEqual([{ op: "inner" }]);
  });

  it("counts an op-less record among the malformed ops in dev", () => {
    const logs = spyConsole();
    parseEnvelope({ version: "v1", ops: [{}, { op: 7 }, { op: "inner" }] }, true);
    expect(logs.warn).toHaveBeenCalledExactlyOnceWith(
      "[next] dropped malformed ops: 2",
    );
    logs.restore();
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

  it("keeps an inline asset carrying a body but no url", () => {
    const parsed = parseEnvelope({
      version: "v1",
      assets: [
        { kind: "css", url: "", inline: ".z{color:red}", load: "link" },
        { kind: "js", inline: "console.log(1)", load: "script" },
        { kind: "css" },
      ],
    });
    expect(parsed.assets).toEqual([
      { kind: "css", url: "", inline: ".z{color:red}", load: "link" },
      { kind: "js", inline: "console.log(1)", load: "script" },
    ]);
  });

  it("drops an inline body whose kind name is the only thing naming a verb", () => {
    // The module kind registers no inline wrapper, so guessing the verb from the
    // name would execute a body the full render prints verbatim.
    const parsed = parseEnvelope({
      version: "v1",
      assets: [
        { kind: "module", url: "", inline: "mount()" },
        { kind: "css", url: "/a.css" },
      ],
    });
    expect(parsed.assets).toEqual([{ kind: "css", url: "/a.css" }]);
  });

  it("keeps the kind fallback for a url-form entry an older server sent", () => {
    const parsed = parseEnvelope({
      version: "v1",
      assets: [{ kind: "module", url: "/island.mjs" }],
    });
    expect(parsed.assets).toEqual([{ kind: "module", url: "/island.mjs" }]);
  });

  it("drops an entry whose kind is not a string, the type it is read as", () => {
    const logs = spyConsole();
    const parsed = parseEnvelope(
      { version: "v1", assets: [{ kind: 42, load: "link", url: "/a.css" }] },
      true,
    );
    // The boundary and the dev breakdown call the same entry broken, so the
    // console cannot report an asset the loader went on to insert.
    expect(parsed.assets).toEqual([]);
    expect(logs.warn).toHaveBeenCalledExactlyOnceWith(
      "[next] dropped malformed assets: 1",
    );
    logs.restore();
  });

  it("keeps a custom kind that carries a server insertion verb", () => {
    const parsed = parseEnvelope({
      version: "v1",
      assets: [
        { kind: "styles", url: "/theme.css", load: "link" },
        { kind: "island", url: "/island.mjs", load: "module" },
        { kind: "legacy", inline: "boot()", load: "script" },
      ],
    });
    expect(parsed.assets).toEqual([
      { kind: "styles", url: "/theme.css", load: "link" },
      { kind: "island", url: "/island.mjs", load: "module" },
      { kind: "legacy", inline: "boot()", load: "script" },
    ]);
  });

  it("keeps the built-in kinds when the server sends no verb", () => {
    const parsed = parseEnvelope({
      version: "v1",
      assets: [
        { kind: "css", url: "/a.css" },
        { kind: "js", url: "/a.js" },
        { kind: "module", url: "/a.mjs" },
      ],
    });
    expect(parsed.assets.map((asset) => asset.kind)).toEqual(["css", "js", "module"]);
  });

  it("drops an entry whose load is not one of the insertion verbs", () => {
    const parsed = parseEnvelope({
      version: "v1",
      assets: [
        { kind: "js", url: "/a.js", load: "worklet" },
        { kind: "css", url: "/b.css", load: 7 },
        { kind: "css", url: "/c.css" },
      ],
    });
    // A load the client cannot act on is a broken entry, not a css asset that
    // happens to carry one. An entry spelling no load keeps its kind's meaning.
    expect(parsed.assets).toEqual([{ kind: "css", url: "/c.css" }]);
  });

  it("counts an entry with an unknown load among the malformed ones", () => {
    const logs = spyConsole();
    parseEnvelope(
      { version: "v1", assets: [{ kind: "css", url: "/b.css", load: 7 }] },
      true,
    );
    expect(logs.warn).toHaveBeenCalledExactlyOnceWith(
      "[next] dropped malformed assets: 1",
    );
    expect(logs.debug).not.toHaveBeenCalled();
    logs.restore();
  });

  it("keeps the server verb over the legacy meaning of the kind", () => {
    const parsed = parseEnvelope({
      version: "v1",
      assets: [{ kind: "css", url: "/island.mjs", load: "module" }],
    });
    expect(parsed.assets).toEqual([
      { kind: "css", url: "/island.mjs", load: "module" },
    ]);
  });

  it("names a kind whose verb the client cannot resolve in the skip line", () => {
    const logs = spyConsole();
    const parsed = parseEnvelope(
      {
        version: "v1",
        assets: [
          { kind: "island", url: "/island.mjs", load: "module" },
          { kind: "wasm", url: "/lib.wasm" },
        ],
      },
      true,
    );
    expect(logs.warn).not.toHaveBeenCalled();
    expect(logs.debug).toHaveBeenCalledExactlyOnceWith(
      "[next] skipped assets of unsupported kind (1): wasm",
    );
    expect(parsed.assets).toHaveLength(1);
    logs.restore();
  });

  it("counts the malformed ops in dev and says nothing about the assets", () => {
    const logs = spyConsole();
    const parsed = parseEnvelope({ version: "v1", ops: [null, { op: "inner" }] }, true);
    expect(logs.warn).toHaveBeenCalledExactlyOnceWith(
      "[next] dropped malformed ops: 1",
    );
    expect(logs.debug).not.toHaveBeenCalled();
    expect(parsed.ops).toEqual([{ op: "inner" }]);
    logs.restore();
  });

  it("counts the malformed assets in dev and says nothing about the ops", () => {
    const logs = spyConsole();
    const parsed = parseEnvelope(
      {
        version: "v1",
        ops: [{ op: "inner" }],
        assets: [
          { kind: "css", url: "/ok.css" },
          "nope",
          { url: "/no-kind.js" },
          { kind: "js" },
        ],
      },
      true,
    );
    expect(logs.warn).toHaveBeenCalledExactlyOnceWith(
      "[next] dropped malformed assets: 3",
    );
    expect(logs.debug).not.toHaveBeenCalled();
    expect(parsed.assets).toEqual([{ kind: "css", url: "/ok.css" }]);
    logs.restore();
  });

  it("reports a custom asset kind as a skip rather than as damage", () => {
    const logs = spyConsole();
    const parsed = parseEnvelope(
      {
        version: "v1",
        assets: [
          { kind: "css", url: "/components/poll_chart.css" },
          { kind: "vue", url: "/dist/page-CBz.js" },
          { kind: "vue", url: "/dist/component-Dlb.js" },
        ],
      },
      true,
    );
    expect(logs.warn).not.toHaveBeenCalled();
    expect(logs.debug).toHaveBeenCalledExactlyOnceWith(
      "[next] skipped assets of unsupported kind (2): vue",
    );
    expect(parsed.assets).toEqual([{ kind: "css", url: "/components/poll_chart.css" }]);
    logs.restore();
  });

  it("names each unsupported kind once in the skip line", () => {
    const logs = spyConsole();
    parseEnvelope(
      {
        version: "v1",
        assets: [
          { kind: "vue", url: "/a.js" },
          { kind: "wasm", inline: "AGFzbQ==" },
          { kind: "vue", url: "/b.js" },
          { kind: "js", url: "/c.js" },
        ],
      },
      true,
    );
    expect(logs.warn).not.toHaveBeenCalled();
    expect(logs.debug).toHaveBeenCalledExactlyOnceWith(
      "[next] skipped assets of unsupported kind (3): vue, wasm",
    );
    logs.restore();
  });

  it("reports all three categories of a mixed envelope in dev", () => {
    const logs = spyConsole();
    const parsed = parseEnvelope(
      {
        version: "v1",
        ops: [null, "nope", { op: "inner" }],
        assets: [
          { kind: "css", url: "/ok.css" },
          { url: "/no-kind.js" },
          { kind: "vue", url: "/page.js" },
        ],
      },
      true,
    );
    expect(logs.warn.mock.calls).toEqual([
      ["[next] dropped malformed ops: 2"],
      ["[next] dropped malformed assets: 1"],
    ]);
    expect(logs.debug).toHaveBeenCalledExactlyOnceWith(
      "[next] skipped assets of unsupported kind (1): vue",
    );
    expect(parsed.ops).toEqual([{ op: "inner" }]);
    expect(parsed.assets).toEqual([{ kind: "css", url: "/ok.css" }]);
    logs.restore();
  });

  it("stays silent on the same malformed envelope without dev", () => {
    const logs = spyConsole();
    const wire = {
      version: "v1",
      ops: [null, { op: "inner" }],
      assets: [
        { kind: "css", url: "/ok.css" },
        { url: "/no-kind.js" },
        "nope",
        { kind: "vue", url: "/page.js" },
      ],
    };
    const implicit = parseEnvelope(wire);
    const explicit = parseEnvelope(wire, false);
    expect(logs.warn).not.toHaveBeenCalled();
    expect(logs.debug).not.toHaveBeenCalled();
    expect(implicit).toEqual(explicit);
    expect(explicit.ops).toEqual([{ op: "inner" }]);
    expect(explicit.assets).toEqual([{ kind: "css", url: "/ok.css" }]);
    logs.restore();
  });

  it("keeps quiet in dev when nothing is dropped", () => {
    const logs = spyConsole();
    const parsed = parseEnvelope(
      {
        version: "v1",
        ops: [{ op: "inner" }],
        assets: [
          { kind: "css", url: "/ok.css" },
          { kind: "js", inline: "console.log(1)", load: "script" },
        ],
      },
      true,
    );
    expect(logs.warn).not.toHaveBeenCalled();
    expect(logs.debug).not.toHaveBeenCalled();
    expect(parsed.ops).toHaveLength(1);
    logs.restore();
  });

  it("keeps quiet in dev on an envelope carrying no ops and no assets", () => {
    const logs = spyConsole();
    const parsed = parseEnvelope({ version: "v1" }, true);
    expect(logs.warn).not.toHaveBeenCalled();
    expect(logs.debug).not.toHaveBeenCalled();
    expect(parsed.ops).toEqual([]);
    expect(parsed.assets).toEqual([]);
    logs.restore();
  });

  it("names an ops field that is present but not a list", () => {
    const logs = spyConsole();
    // A backend serialising one op as an object instead of a list drops the
    // whole envelope's ops, the most common serialisation slip there is.
    const parsed = parseEnvelope(
      { version: "v1", ops: { op: "morph", html: "<p>x</p>" } },
      true,
    );
    expect(logs.warn).toHaveBeenCalledExactlyOnceWith(
      "[next] envelope ops is not an array, all ops dropped",
    );
    expect(parsed.ops).toEqual([]);
    logs.restore();
  });

  it("names an assets field that is present but not a list", () => {
    const logs = spyConsole();
    const parsed = parseEnvelope(
      { version: "v1", ops: [{ op: "inner" }], assets: { kind: "css", url: "/a.css" } },
      true,
    );
    expect(logs.warn).toHaveBeenCalledExactlyOnceWith(
      "[next] envelope assets is not an array, all assets dropped",
    );
    expect(parsed.assets).toEqual([]);
    logs.restore();
  });

  it("names a null ops field, a field the server did spell", () => {
    const logs = spyConsole();
    parseEnvelope({ version: "v1", ops: null, assets: null }, true);
    expect(logs.warn.mock.calls).toEqual([
      ["[next] envelope ops is not an array, all ops dropped"],
      ["[next] envelope assets is not an array, all assets dropped"],
    ]);
    logs.restore();
  });

  it("stays silent on a non-list ops field without dev", () => {
    const logs = spyConsole();
    const parsed = parseEnvelope({ version: "v1", ops: { op: "morph" } });
    expect(logs.warn).not.toHaveBeenCalled();
    expect(parsed.ops).toEqual([]);
    logs.restore();
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

  it("names an address that refuses to serialise as no target", () => {
    document.body.innerHTML = '<form data-next-action="u1"><input name="email"></form>';
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { applier } = makeApplier(true);
    // The uid and the name still resolve the field, only the description of the
    // address is impossible, and a warn is not worth an exception.
    const field: unknown[] = ["u1", "email"];
    field.push(field);
    applier.apply(
      envelope([{ op: "inner", target: { field }, html: "<script>1</script>" }]),
    );
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("targeting no target"));
    warn.mockRestore();
  });

  it("carries dev into the morph engine so markup slips are diagnosed", () => {
    document.body.innerHTML =
      '<ul data-next-zone="z"><li id="x" data-next-key="x">old</li></ul>';
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { applier } = makeApplier(true);
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { zone: "z" },
          html: '<ul data-next-zone="z"><li id="x" data-next-key="x">new</li></ul>',
        },
      ]),
    );
    expect(warn).toHaveBeenCalled();
    expect(document.querySelector("#x")!.textContent).toBe("new");
    warn.mockRestore();
  });

  it("stays silent on the same markup slip in non-dev builds", () => {
    document.body.innerHTML =
      '<ul data-next-zone="z"><li id="x" data-next-key="x">old</li></ul>';
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { applier } = makeApplier(false);
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { zone: "z" },
          html: '<ul data-next-zone="z"><li id="x" data-next-key="x">new</li></ul>',
        },
      ]),
    );
    expect(warn).not.toHaveBeenCalled();
    expect(document.querySelector("#x")!.textContent).toBe("new");
    warn.mockRestore();
  });

  it("counts dropped ops through apply in dev builds", () => {
    document.body.innerHTML = '<div data-next-zone="z"></div>';
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { applier } = makeApplier(true);
    applier.apply(envelope([null, { op: "inner", target: { zone: "z" }, html: "hi" }]));
    expect(warn).toHaveBeenCalledWith("[next] dropped malformed ops: 1");
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("hi");
    warn.mockRestore();
  });
});

describe("Applier dev timing", () => {
  // The user timing runs for real, the spies only record the names the runtime
  // writes. Only console.debug is silenced.
  function spyTiming() {
    return {
      mark: vi.spyOn(performance, "mark"),
      measure: vi.spyOn(performance, "measure"),
      clear: vi.spyOn(performance, "clearMarks"),
      clearSpans: vi.spyOn(performance, "clearMeasures"),
      debug: vi.spyOn(console, "debug").mockImplementation(() => undefined),
    };
  }

  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("measures a zone patch under its zone name", () => {
    document.body.innerHTML = '<div data-next-zone="cart">old</div>';
    const timing = spyTiming();
    const { applier } = makeApplier(true);
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { zone: "cart" },
          html: '<div data-next-zone="cart">new</div>',
        },
      ]),
    );
    expect(timing.mark).toHaveBeenCalledWith("next:apply:cart:start:1");
    expect(timing.measure).toHaveBeenCalledWith(
      "next:apply:cart",
      "next:apply:cart:start:1",
    );
    expect(timing.clear).toHaveBeenCalledWith("next:apply:cart:start:1");
    // A dev tab lives for hours, so neither half of the span stays in the entry
    // buffer once the panel has recorded it.
    expect(timing.clearSpans).toHaveBeenCalledWith("next:apply:cart");
    expect(timing.debug).toHaveBeenCalledWith(
      expect.stringMatching(/^\[next] zone "cart" morph in \d+\.\d ms$/),
    );
  });

  it("falls back to the verb when the op names no zone", () => {
    const timing = spyTiming();
    const { applier } = makeApplier(true);
    applier.apply(envelope([{ op: "toast", text: "saved" }]));
    expect(timing.mark).toHaveBeenCalledWith("next:apply:toast:start:1");
    expect(timing.measure).toHaveBeenCalledWith(
      "next:apply:toast",
      "next:apply:toast:start:1",
    );
    expect(timing.debug).toHaveBeenCalledWith(
      expect.stringMatching(/^\[next] op "toast" in \d+\.\d ms$/),
    );
  });

  it("falls back to the verb when the target names something other than a zone", () => {
    document.body.innerHTML = '<div id="slot">old</div>';
    const timing = spyTiming();
    const { applier } = makeApplier(true);
    applier.apply(envelope([{ op: "inner", target: { css: "#slot" }, html: "new" }]));
    expect(timing.mark).toHaveBeenCalledWith("next:apply:inner:start:1");
    expect(timing.debug).toHaveBeenCalledWith(
      expect.stringMatching(/^\[next] op "inner" in \d+\.\d ms$/),
    );
  });

  it("closes the measure of a throwing op and still reports the failure", () => {
    const timing = spyTiming();
    const { applier, dispatched } = makeApplier(true);
    applier.defineOp("boom", () => {
      throw new Error("op blew up");
    });
    applier.apply(envelope([{ op: "boom" }]));
    expect(timing.measure).toHaveBeenCalledWith(
      "next:apply:boom",
      "next:apply:boom:start:1",
    );
    expect(timing.clear).toHaveBeenCalledWith("next:apply:boom:start:1");
    const err = dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.op).toBe("boom");
  });

  it("stays silent outside dev builds", () => {
    document.body.innerHTML = '<div data-next-zone="cart">old</div>';
    const timing = spyTiming();
    const { applier } = makeApplier(false);
    applier.apply(envelope([{ op: "inner", target: { zone: "cart" }, html: "new" }]));
    expect(timing.mark).not.toHaveBeenCalled();
    expect(timing.measure).not.toHaveBeenCalled();
    expect(timing.debug).not.toHaveBeenCalled();
    expect(document.querySelector('[data-next-zone="cart"]')!.textContent).toBe("new");
  });

  it("gives each op its own start mark so a nested apply cannot clear it", () => {
    document.body.innerHTML = '<div data-next-zone="cart">old</div>';
    const timing = spyTiming();
    const { applier, dispatched } = makeApplier(true);
    // A custom op re-entering the applier under the same label is the shape of
    // a next:morph-element listener applying its own patch.
    applier.defineOp("nested", () => {
      applier.apply(envelope([{ op: "inner", target: { zone: "cart" }, html: "new" }]));
    });
    applier.apply(envelope([{ op: "nested", target: { zone: "cart" } }]));
    const marks = timing.mark.mock.calls.map((call) => call[0]);
    expect(marks).toHaveLength(2);
    expect(new Set(marks).size).toBe(2);
    expect(timing.measure.mock.calls.map((call) => call[0])).toEqual([
      "next:apply:cart",
      "next:apply:cart",
    ]);
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(false);
    expect(
      dispatched
        .filter((d) => d.event === "partial:applied")
        .every((d) => d.detail.ok === true),
    ).toBe(true);
    expect(document.querySelector('[data-next-zone="cart"]')!.textContent).toBe("new");
  });

  it("keeps a cleared entry buffer from failing the op it was timing", () => {
    const timing = spyTiming();
    const { applier, dispatched } = makeApplier(true);
    applier.defineOp("clears", () => {
      performance.clearMarks();
    });
    applier.apply(envelope([{ op: "clears" }]));
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(false);
    const applied = dispatched.find((d) => d.event === "partial:applied");
    expect(applied!.detail.ok).toBe(true);
    expect(timing.debug).toHaveBeenCalledWith(
      expect.stringMatching(/^\[next] op "clears" in \d+\.\d ms$/),
    );
  });

  it("applies the op of a page whose user timing refuses the start mark", () => {
    const timing = spyTiming();
    timing.mark.mockImplementation(() => {
      throw new Error("user timing unavailable");
    });
    document.body.innerHTML = '<div data-next-zone="cart">old</div>';
    const { applier, dispatched } = makeApplier(true);
    applier.apply(envelope([{ op: "inner", target: { zone: "cart" }, html: "new" }]));
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(false);
    expect(dispatched.find((d) => d.event === "partial:applied")!.detail.ok).toBe(true);
    expect(document.querySelector('[data-next-zone="cart"]')!.textContent).toBe("new");
  });

  it("keeps a replaced console.debug from failing the op it reports on", () => {
    const timing = spyTiming();
    // Analytics wrappers and dev overlays replace console.debug, and a throwing
    // stub in the finally would turn a clean op into partial:error.
    timing.debug.mockImplementation(() => {
      throw new Error("console hijacked");
    });
    document.body.innerHTML = '<div data-next-zone="cart">old</div>';
    const { applier, dispatched } = makeApplier(true);
    applier.apply(envelope([{ op: "inner", target: { zone: "cart" }, html: "new" }]));
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(false);
    expect(dispatched.find((d) => d.event === "partial:applied")!.detail.ok).toBe(true);
    expect(document.querySelector('[data-next-zone="cart"]')!.textContent).toBe("new");
  });

  it("keeps the real error of a throwing op whose timing line was refused", () => {
    const timing = spyTiming();
    timing.debug.mockImplementation(() => {
      throw new Error("console hijacked");
    });
    const { applier, dispatched } = makeApplier(true);
    applier.defineOp("boom", () => {
      throw new Error("op blew up");
    });
    applier.apply(envelope([{ op: "boom" }]));
    const err = dispatched.find((d) => d.event === "partial:error");
    expect((err!.detail.error as Error).message).toBe("op blew up");
  });

  it("keeps the real error of a throwing op whose mark was cleared", () => {
    const timing = spyTiming();
    const { applier, dispatched } = makeApplier(true);
    applier.defineOp("clears", () => {
      performance.clearMarks();
      throw new Error("op blew up");
    });
    applier.apply(envelope([{ op: "clears" }]));
    const err = dispatched.find((d) => d.event === "partial:error");
    expect((err!.detail.error as Error).message).toBe("op blew up");
    expect(timing.debug).toHaveBeenCalled();
  });

  it("measures a refresh under the zone it re-GETs", () => {
    const timing = spyTiming();
    const { applier } = makeApplier(true);
    applier.apply(envelope([{ op: "refresh", zone: "feed" }]));
    expect(timing.measure).toHaveBeenCalledWith("next:apply:feed", expect.any(String));
    expect(timing.debug).toHaveBeenCalledWith(
      expect.stringMatching(/^\[next] zone "feed" refresh in \d+\.\d ms$/),
    );
  });

  it("measures a refresh carrying its zone in the target under that zone", () => {
    const timing = spyTiming();
    const { applier } = makeApplier(true);
    applier.apply(envelope([{ op: "refresh", target: { zone: "feed" } }]));
    expect(timing.measure).toHaveBeenCalledWith("next:apply:feed", expect.any(String));
  });

  it("measures a layer.open under the zone it seeds", () => {
    const timing = spyTiming();
    const { applier } = makeApplier(true);
    applier.apply(envelope([{ op: "layer.open", zone: "cart", href: "/cart/" }]));
    expect(timing.measure).toHaveBeenCalledWith("next:apply:cart", expect.any(String));
  });

  it("falls back to the verb for a layer.open naming no zone", () => {
    const timing = spyTiming();
    const { applier } = makeApplier(true);
    applier.apply(envelope([{ op: "layer.open" }]));
    expect(timing.measure).toHaveBeenCalledWith(
      "next:apply:layer.open",
      expect.any(String),
    );
  });

  it("ignores a top-level zone on a verb that does not address one", () => {
    document.body.innerHTML = '<div data-next-zone="cart">old</div>';
    const timing = spyTiming();
    const { applier } = makeApplier(true);
    // inner reads its address from target only, so a stray top-level zone must
    // not name the measurement after a zone the op never touched.
    applier.apply(
      envelope([{ op: "inner", target: { css: "[data-next-zone]" }, zone: "cart" }]),
    );
    expect(timing.measure).toHaveBeenCalledWith("next:apply:inner", expect.any(String));
  });

  it("degrades the applied signal for an unknown verb while timing it", () => {
    const timing = spyTiming();
    const { applier, dispatched } = makeApplier(true);
    applier.apply(envelope([{ op: "frobnicate" }]));
    const applied = dispatched.find((d) => d.event === "partial:applied");
    expect(applied!.detail.ok).toBe(false);
    expect(timing.debug).toHaveBeenCalledWith(
      expect.stringMatching(/^\[next] op "frobnicate" in \d+\.\d ms$/),
    );
  });

  it("reports the span the op took, not the clock reading it started at", () => {
    const timing = spyTiming();
    // The op moves the mocked clock itself, so the reported number can only come
    // from the difference between the two readings.
    let clock = 100;
    vi.spyOn(performance, "now").mockImplementation(() => clock);
    const { applier } = makeApplier(true);
    applier.defineOp("slow", () => {
      clock = 103.2;
    });
    applier.apply(envelope([{ op: "slow" }]));
    expect(timing.debug).toHaveBeenCalledWith('[next] op "slow" in 3.2 ms');
  });

  it("stays silent when dev is left at its default", () => {
    const timing = spyTiming();
    const applier = new Applier({
      dispatch: () => undefined,
      mergeContext: () => undefined,
      document,
    });
    applier.apply(envelope([{ op: "toast", text: "saved" }]));
    expect(timing.mark).not.toHaveBeenCalled();
    expect(timing.debug).not.toHaveBeenCalled();
  });
});

describe("Applier op errors name their target", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("a throwing op carries the human-readable address it aimed at", () => {
    const { applier, dispatched } = makeApplier();
    applier.defineOp("boom", () => {
      throw new Error("op blew up");
    });
    applier.apply(envelope([{ op: "boom", target: { zone: "cart" } }]));
    const err = dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.target).toBe('zone "cart"');
  });

  it("an unknown verb carries its address too", () => {
    const { applier, dispatched } = makeApplier();
    applier.apply(envelope([{ op: "frobnicate", target: { form: "a1b2" } }]));
    const err = dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.target).toBe('form "a1b2"');
  });

  it("omits the key when the op names no target", () => {
    const { applier, dispatched } = makeApplier();
    applier.apply(envelope([{ op: "frobnicate" }]));
    const err = dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail).not.toHaveProperty("target");
  });

  it("omits the key for a target object with no recognised address", () => {
    const { applier, dispatched } = makeApplier();
    applier.apply(envelope([{ op: "frobnicate", target: {} }]));
    const bare = dispatched.find((d) => d.event === "partial:error");
    expect(bare!.detail).not.toHaveProperty("target");
    const foreign = makeApplier();
    foreign.applier.apply(envelope([{ op: "frobnicate", target: { region: "cart" } }]));
    const err = foreign.dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail).not.toHaveProperty("target");
  });

  it("omits the key when the target is not an object", () => {
    const { applier, dispatched } = makeApplier();
    applier.apply(envelope([{ op: "frobnicate", target: "cart" }]));
    const err = dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail).not.toHaveProperty("target");
  });

  it("names the address the resolver reads, not the first key serialised", () => {
    const { applier, dispatched } = makeApplier();
    applier.apply(
      envelope([{ op: "frobnicate", target: { css: "#slot", zone: "cart" } }]),
    );
    const err = dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.target).toBe('zone "cart"');
  });

  it("prefers form over the field and css addresses under it", () => {
    const { applier, dispatched } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "frobnicate",
          target: { css: "#slot", field: ["u1", "email"], form: "a1b2" },
        },
      ]),
    );
    const err = dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.target).toBe('form "a1b2"');
  });

  it("prefers field over the css address under it", () => {
    const { applier, dispatched } = makeApplier();
    applier.apply(
      envelope([
        { op: "frobnicate", target: { css: "#slot", field: ["u1", "email"] } },
      ]),
    );
    const err = dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.target).toBe('field ["u1","email"]');
  });

  it("omits the key when the recognised address carries no value", () => {
    const { applier, dispatched } = makeApplier();
    applier.apply(envelope([{ op: "frobnicate", target: { zone: undefined } }]));
    const err = dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail).not.toHaveProperty("target");
  });

  it("contains a throwing op whose target refuses to serialise", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const { applier, dispatched } = makeApplier();
    applier.defineOp("boom", () => {
      throw new Error("op blew up");
    });
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    applier.apply(
      envelope([
        { op: "boom", target: { zone: cyclic } },
        { op: "inner", target: { zone: "z" }, html: "new" },
      ]),
    );
    const err = dispatched.find((d) => d.event === "partial:error");
    expect((err!.detail.error as Error).message).toBe("op blew up");
    expect(err!.detail).not.toHaveProperty("target");
    // A target's description never decides whether the rest of the envelope applies.
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("new");
    const applied = dispatched.find((d) => d.event === "partial:applied");
    expect(applied!.detail.ok).toBe(false);
  });

  it("skips an unknown verb whose target refuses to serialise", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const { applier, dispatched } = makeApplier();
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    applier.apply(
      envelope([
        { op: "frobnicate", target: { zone: cyclic } },
        { op: "inner", target: { zone: "z" }, html: "new" },
      ]),
    );
    const err = dispatched.find((d) => d.event === "partial:error");
    expect((err!.detail.error as Error).message).toBe("unknown op frobnicate");
    expect(err!.detail).not.toHaveProperty("target");
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("new");
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

  // Capture next:removed at the document, the bus the layer adapter delegates on,
  // recording the target, whether it was connected at fire time, and the flags.
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

  it("keeps a touched details open under a patch the user never asked for", () => {
    document.body.innerHTML =
      '<div data-next-zone="z"><details id="d" open></details></div>';
    const details = document.querySelector<HTMLDetailsElement>("#d")!;
    const dispatched: Dispatched[] = [];
    const applier = new Applier({
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      mergeContext: () => undefined,
      document,
      // The user toggled the detail before this poll response, so it reads as
      // touched even though its stamp predates the request snapshot.
      isTouched: (el) => el === details,
    });
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { zone: "z" },
          html: '<div data-next-zone="z"><details id="d"></details></div>',
        },
      ]),
      0,
    );
    expect(document.querySelector("#d")!.hasAttribute("open")).toBe(true);
  });

  it("syncs an untouched details from the server", () => {
    document.body.innerHTML =
      '<div data-next-zone="z"><details id="d" open></details></div>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { zone: "z" },
          html: '<div data-next-zone="z"><details id="d"></details></div>',
        },
      ]),
    );
    expect(document.querySelector("#d")!.hasAttribute("open")).toBe(false);
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
      resolveSelector: (selector: string, root: ParentNode) =>
        root.querySelector(selector),
      urlFor: () => "/here/",
      open: (opener: null, href?: string, zone?: string) =>
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

  it("layer.open seeds a zone-only open with an undefined href", () => {
    const { applier, calls } = makeLayerApplier();
    applier.apply(envelope([{ op: "layer.open", zone: "cart" }]));
    expect(calls).toEqual([{ verb: "open", args: [null, undefined, "cart"] }]);
  });

  it("layer.open with an href and no zone is dropped as malformed", () => {
    const { applier, calls } = makeLayerApplier();
    applier.apply(envelope([{ op: "layer.open", href: "/w/" }]));
    expect(calls).toEqual([]);
  });

  it("layer.open with neither zone nor href still opens a bare modal", () => {
    const { applier, calls } = makeLayerApplier();
    applier.apply(envelope([{ op: "layer.open" }]));
    expect(calls).toEqual([{ verb: "open", args: [null, undefined, undefined] }]);
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

  it("a form target resolves through the layer stack, modal form first", () => {
    document.body.innerHTML =
      '<form data-next-action="u1" id="page-form"></form>' +
      '<dialog><div><form data-next-action="u1" id="modal-form"></form></div></dialog>';
    const layers = {
      resolveZone: () => null,
      resolveSelector: (selector: string) =>
        document.querySelector(`dialog ${selector}`),
      urlFor: () => "/here/",
      open: () => undefined,
      close: () => undefined,
      toast: () => undefined,
    };
    const applier = new Applier({
      dispatch: () => undefined,
      mergeContext: () => undefined,
      document,
      layers,
    });
    applier.apply(envelope([{ op: "inner", target: { form: "u1" }, html: "patched" }]));
    expect(document.getElementById("modal-form")!.innerHTML).toBe("patched");
    expect(document.getElementById("page-form")!.innerHTML).toBe("");
  });
});

describe("Applier page-scoped zone resolve", () => {
  function makeRecordingApplier() {
    const pages: (string | undefined)[] = [];
    const layers = {
      resolveZone: (name: string, root: ParentNode, page?: string) => {
        pages.push(page);
        return root.querySelector(`[data-next-zone="${name}"]`);
      },
      resolveSelector: (selector: string, root: ParentNode) =>
        root.querySelector(selector),
      urlFor: () => "/here/",
      open: () => undefined,
      close: () => undefined,
      toast: () => undefined,
    };
    const applier = new Applier({
      dispatch: () => undefined,
      mergeContext: () => undefined,
      document,
      layers,
    });
    return { applier, pages };
  }

  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("threads the fetched page of a zone GET into the layer resolve", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const { applier, pages } = makeRecordingApplier();
    applier.apply(
      envelope([{ op: "inner", target: { zone: "z" }, html: "new" }]),
      undefined,
      undefined,
      "/host/",
    );
    expect(pages).toEqual(["/host/"]);
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("new");
  });

  it("a direct apply resolves zones with no page", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const { applier, pages } = makeRecordingApplier();
    applier.apply(envelope([{ op: "inner", target: { zone: "z" }, html: "new" }]));
    expect(pages).toEqual([undefined]);
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
