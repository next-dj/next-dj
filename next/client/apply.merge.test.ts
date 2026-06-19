import { beforeEach, describe, expect, it, vi } from "vitest";
import { Applier } from "./apply";
import type { ApplyDeps, AssetBridge, Asset, MountRegistry, ZoneFetch } from "./apply";

type Dispatched = { event: string; detail: Record<string, unknown> };

function makeApplier(over: Partial<ApplyDeps> = {}) {
  const dispatched: Dispatched[] = [];
  const applier = new Applier({
    dispatch: (event, detail) => dispatched.push({ event, detail }),
    mergeContext: () => undefined,
    document,
    ...over,
  });
  return { applier, dispatched };
}

function envelope(ops: unknown[], extra: Record<string, unknown> = {}): unknown {
  return { version: "v1", ops, assets: [], defer: [], form: null, ...extra };
}

describe("append and prepend dedup", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("appends children to the end of a zone", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li data-next-key="1">a</li></ul>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li data-next-key="2">b</li>',
        },
      ]),
    );
    const keys = Array.from(document.querySelectorAll("li")).map((li) =>
      li.getAttribute("data-next-key"),
    );
    expect(keys).toEqual(["1", "2"]);
  });

  it("replaces an existing row sharing the dedup key instead of duplicating", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li data-next-key="1">old</li></ul>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li data-next-key="1">new</li>',
        },
      ]),
    );
    const rows = document.querySelectorAll("li");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toBe("new");
  });

  it("falls back to id when no data-next-key is set", () => {
    document.body.innerHTML = '<ul data-next-zone="rows"><li id="r1">old</li></ul>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        { op: "append", target: { zone: "rows" }, html: '<li id="r1">new</li>' },
      ]),
    );
    expect(document.querySelectorAll("li")).toHaveLength(1);
    expect(document.querySelector("#r1")!.textContent).toBe("new");
  });

  it("prepend dedups by key, replacing the existing row", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li data-next-key="1">old</li></ul>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "prepend",
          target: { zone: "rows" },
          html: '<li data-next-key="1">new</li>',
        },
      ]),
    );
    expect(document.querySelectorAll("li")).toHaveLength(1);
    expect(document.querySelector("li")!.textContent).toBe("new");
  });

  it("appends a keyless row without matching", () => {
    document.body.innerHTML = '<ul data-next-zone="rows"><li>a</li></ul>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([{ op: "append", target: { zone: "rows" }, html: "<li>b</li>" }]),
    );
    expect(document.querySelectorAll("li")).toHaveLength(2);
  });

  it("prepends to the start", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li data-next-key="2">b</li></ul>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "prepend",
          target: { zone: "rows" },
          html: '<li data-next-key="1">a</li>',
        },
      ]),
    );
    const keys = Array.from(document.querySelectorAll("li")).map((li) =>
      li.getAttribute("data-next-key"),
    );
    expect(keys).toEqual(["1", "2"]);
  });
});

describe("refresh verb", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("refresh re-GETs the named zone", () => {
    const refresh = vi.fn<ZoneFetch>();
    const { applier } = makeApplier({ refresh, here: () => "/page/" });
    applier.apply(envelope([{ op: "refresh", zone: "feed" }]));
    expect(refresh).toHaveBeenCalledWith({
      url: "/page/",
      zone: "feed",
      headers: { "X-Next-Zone": "feed" },
    });
  });
});

describe("mount and generation", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("fires next:mounted on the touched node and runs the mount registry", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const ran: string[] = [];
    const mount: MountRegistry = { run: (root) => ran.push((root as Element).tagName) };
    const zone = document.querySelector('[data-next-zone="z"]')!;
    const seen: string[] = [];
    zone.addEventListener("next:mounted", () => seen.push("mounted"));
    const { applier } = makeApplier({ mount });
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { zone: "z" },
          html: '<div data-next-zone="z">new</div>',
        },
      ]),
    );
    expect(seen).toEqual(["mounted"]);
    expect(ran).toEqual(["DIV"]);
  });

  it("bumps the per-zone generation on each apply", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const { applier } = makeApplier();
    expect(applier.generation("z")).toBe(0);
    applier.apply(envelope([{ op: "inner", target: { zone: "z" }, html: "one" }]));
    applier.apply(envelope([{ op: "inner", target: { zone: "z" }, html: "two" }]));
    expect(applier.generation("z")).toBe(2);
  });
});

describe("asset bridge pipeline", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("gates ops behind the css load and runs js after", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const order: string[] = [];
    const assets: AssetBridge = {
      loadCss: (_m: Asset[], done) => {
        order.push("css");
        done();
      },
      loadJs: () => order.push("js"),
      versionMismatch: () => false,
      acceptVersion: () => undefined,
    };
    const { applier } = makeApplier({ assets });
    applier.apply(envelope([{ op: "inner", target: { zone: "z" }, html: "new" }]));
    expect(order).toEqual(["css", "js"]);
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("new");
  });

  it("skips the apply on a version mismatch", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const assets: AssetBridge = {
      loadCss: (_m: Asset[], done) => done(),
      loadJs: () => undefined,
      versionMismatch: () => true,
      acceptVersion: () => undefined,
    };
    const { applier } = makeApplier({ assets });
    applier.apply(envelope([{ op: "inner", target: { zone: "z" }, html: "new" }]));
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("old");
  });
});
