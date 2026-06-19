import { beforeEach, describe, expect, it } from "vitest";
import { createPartial } from "./partial";
import type { PartialSurface } from "./partial";

function makeSurface() {
  const dispatched: { event: string; detail: Record<string, unknown> }[] = [];
  const merged: Record<string, unknown>[] = [];
  const partial = createPartial({
    dispatch: (event, detail) => dispatched.push({ event, detail }),
    mergeContext: (data) => merged.push(data),
  });
  return { partial, dispatched, merged };
}

describe("createPartial surface", () => {
  let partial: PartialSurface;
  let dispatched: { event: string; detail: Record<string, unknown> }[];

  beforeEach(() => {
    document.body.innerHTML = "";
    const made = makeSurface();
    partial = made.partial;
    dispatched = made.dispatched;
    partial._configure({ document });
  });

  it("apply runs the verbs through the configured document", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    partial.apply({
      version: "v1",
      ops: [{ op: "inner", target: { zone: "z" }, html: "new" }],
      assets: [],
      form: null,
    });
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("new");
  });

  it("defineOp registers a custom verb reachable from apply", () => {
    const seen: unknown[] = [];
    partial.defineOp("confetti", (patch) => seen.push(patch.origin));
    partial.apply({
      version: "v1",
      ops: [{ op: "confetti", origin: "btn" }],
      assets: [],
      form: null,
    });
    expect(seen).toEqual(["btn"]);
  });

  it("fetch sends the CSRF token set through setCsrf on unsafe methods", async () => {
    const calls: RequestInit[] = [];
    partial._configure({
      document,
      fetch: async (_url, init) => {
        calls.push(init);
        return new Response('{"version":"v1","ops":[],"assets":[],"form":null}', {
          status: 200,
          headers: { "content-type": "application/vnd.next.patches+json" },
        });
      },
      navigate: () => {},
    });
    partial.setCsrf({ header: "X-CSRFToken", token: "tok" });
    await partial.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    expect((calls[0].headers as Record<string, string>)["X-CSRFToken"]).toBe("tok");
  });

  it("a csrf meta in an applied envelope rotates the token for the next mutation", async () => {
    const calls: RequestInit[] = [];
    let body =
      '{"version":"v1","ops":[],"assets":[],"form":null,' +
      '"csrf":{"header":"X-CSRFToken","token":"rotated"}}';
    partial._configure({
      document,
      fetch: async (_url, init) => {
        calls.push(init);
        const r = new Response(body, {
          status: 200,
          headers: { "content-type": "application/vnd.next.patches+json" },
        });
        body = '{"version":"v1","ops":[],"assets":[],"form":null}';
        return r;
      },
      navigate: () => {},
    });
    partial.setCsrf({ header: "X-CSRFToken", token: "old" });
    await partial.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    await partial.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    expect((calls[1].headers as Record<string, string>)["X-CSRFToken"]).toBe("rotated");
  });

  it("parseHook reaches the applier for a foreign content-type", async () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    partial._configure({
      document,
      fetch: async () =>
        new Response("stream-body", {
          status: 200,
          headers: { "content-type": "text/vnd.next.stream+html" },
        }),
      navigate: () => {},
    });
    partial.parseHook("text/vnd.next.stream+html", () => ({
      version: "v1",
      ops: [{ op: "inner", target: { zone: "z" }, html: "hooked" }],
      assets: [],
      form: null,
    }));
    await partial.fetch({ url: "/list/", zone: "z" });
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("hooked");
  });

  it("_reset clears custom ops, csrf, and configured adapters", async () => {
    partial.defineOp("confetti", () => undefined);
    partial.setCsrf({ header: "X-CSRFToken", token: "tok" });
    partial._reset();
    partial._configure({ document });
    partial.apply({
      version: "v1",
      ops: [{ op: "confetti" }],
      assets: [],
      form: null,
    });
    expect(dispatched.some((d) => d.event === "partial:error")).toBe(true);
  });

  it("emits partial:before-request before the fetch resolves", async () => {
    partial._configure({
      document,
      fetch: async () =>
        new Response('{"version":"v1","ops":[],"assets":[],"form":null}', {
          status: 200,
          headers: { "content-type": "application/vnd.next.patches+json" },
        }),
      navigate: () => {},
    });
    await partial.fetch({ url: "/list/", zone: "z" });
    expect(dispatched.some((d) => d.event === "partial:before-request")).toBe(true);
  });

  it("onMount runs over the initial DOM on ready and over inserted subtrees", () => {
    document.body.innerHTML = '<div data-next-zone="z"><span class="w">a</span></div>';
    const seen: string[] = [];
    partial._configure({ document });
    partial.onMount(".w", (el) => seen.push(el.textContent ?? ""));
    partial.ready();
    expect(seen).toEqual(["a"]);
    partial.apply({
      version: "v1",
      ops: [
        {
          op: "morph",
          target: { zone: "z" },
          html: '<div data-next-zone="z"><span class="w">b</span></div>',
        },
      ],
      assets: [],
      form: null,
    });
    expect(seen).toEqual(["a", "b"]);
  });

  it("onMount fires when the inserted subtree root itself matches the selector", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const seen: string[] = [];
    partial._configure({ document });
    partial.onMount('[data-next-zone="z"]', (el) => seen.push(el.textContent ?? ""));
    partial.apply({
      version: "v1",
      ops: [
        {
          op: "morph",
          target: { zone: "z" },
          html: '<div data-next-zone="z">new</div>',
        },
      ],
      assets: [],
      form: null,
    });
    expect(seen).toEqual(["new"]);
  });

  it("onMount registered after ready catches up over the present document", () => {
    document.body.innerHTML = "<div data-x>here</div>";
    const seen: Element[] = [];
    partial._configure({ document });
    partial.ready();
    partial.onMount("[data-x]", (el) => seen.push(el));
    expect(seen).toEqual([document.querySelector("[data-x]")]);
  });

  it("onMount registered before ready runs exactly once on ready", () => {
    document.body.innerHTML = "<div data-x>here</div>";
    const seen: string[] = [];
    partial._configure({ document });
    partial.onMount("[data-x]", (el) => seen.push(el.textContent ?? ""));
    partial.ready();
    expect(seen).toEqual(["here"]);
  });

  it("a late onMount still runs over a subtree inserted by a later apply", () => {
    document.body.innerHTML = '<div data-next-zone="z"><span class="w">a</span></div>';
    const seen: string[] = [];
    partial._configure({ document });
    partial.ready();
    partial.onMount(".w", (el) => seen.push(el.textContent ?? ""));
    expect(seen).toEqual(["a"]);
    partial.apply({
      version: "v1",
      ops: [
        {
          op: "morph",
          target: { zone: "z" },
          html: '<div data-next-zone="z"><span class="w">b</span></div>',
        },
      ],
      assets: [],
      form: null,
    });
    expect(seen).toEqual(["a", "b"]);
  });

  it("ready batches the document's load zones into one zone fetch", async () => {
    document.body.innerHTML =
      '<div data-next-zone="a" data-next-lazy="load"></div>' +
      '<div data-next-zone="b" data-next-lazy="load"></div>';
    const calls: { url: string; init: RequestInit }[] = [];
    partial._configure({
      document,
      fetch: async (url, init) => {
        calls.push({ url, init });
        return new Response('{"version":"v1","ops":[],"assets":[],"form":null}', {
          status: 200,
          headers: { "content-type": "application/vnd.next.patches+json" },
        });
      },
      navigate: () => {},
    });
    partial.ready();
    await Promise.resolve();
    expect(calls).toHaveLength(1);
    expect((calls[0].init.headers as Record<string, string>)["X-Next-Zone"]).toBe(
      "a,b",
    );
  });
});

describe("Next.partial integration", () => {
  beforeEach(async () => {
    await import("./next");
  });

  it("exposes the partial surface on the global Next", async () => {
    const win = globalThis as unknown as {
      Next: { partial: PartialSurface };
    };
    expect(typeof win.Next.partial.apply).toBe("function");
    expect(typeof win.Next.partial.fetch).toBe("function");
    expect(typeof win.Next.partial.defineOp).toBe("function");
    expect(typeof win.Next.partial._reset).toBe("function");
  });
});
