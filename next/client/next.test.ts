import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "./next";
// The scripts chunk registers on evaluation, as it does once the runtime fetches it.
import "./extras";

interface Consent {
  get(): Readonly<Record<string, boolean>>;
  decided(): boolean;
  update(choice: Record<string, boolean>, opts?: { reload?: boolean }): void;
}

interface NextStatic {
  context: Readonly<Record<string, unknown>>;
  partial: {
    apply(raw: unknown): unknown;
    fetch(request: { url: string; method?: string; uid?: string }): Promise<void>;
    defineOp(
      name: string,
      handler: (patch: Record<string, unknown>, ctx: unknown) => void,
    ): void;
    setCsrf(csrf: { header: string; token: string } | undefined): void;
    onMount(selector: string, callback: (el: Element) => void): () => void;
    ready(): void;
    _configure(adapters: {
      dev?: boolean;
      document?: Document;
      fetch?: (input: string, init: RequestInit) => Promise<Response>;
      navigate?: (url: string) => void;
    }): void;
    _reset(): void;
  };
  consent: Consent;
  scripts: {
    load(name: string): Promise<void>;
    status(name: string): string | undefined;
  };
  ready(chunk: "scripts"): Promise<{ consent: Consent; scripts: unknown }>;
  navigation: { current(): { url: string; path: string; title: string } };
  _init(context: Record<string, unknown>): void;
  on(event: string, listener: (payload: Record<string, unknown>) => void): () => void;
  use<T>(plugin: (next: NextStatic) => T): T;
}

const win = globalThis as unknown as { Next: NextStatic };

describe("Next._init", () => {
  beforeEach(() => {
    win.Next._init({});
  });

  it("stores values accessible via Next.context", () => {
    win.Next._init({ page: "home" });
    expect(win.Next.context.page).toBe("home");
  });

  it("replaces the entire context on each call", () => {
    win.Next._init({ old: "first" });
    win.Next._init({ new: "second" });
    expect(win.Next.context).not.toHaveProperty("old");
    expect(win.Next.context.new).toBe("second");
  });

  it("accepts an empty object and clears previous context", () => {
    win.Next._init({ key: "value" });
    win.Next._init({});
    expect(win.Next.context).toEqual({});
  });

  it("accepts nested objects", () => {
    win.Next._init({ meta: { page: "home", version: "1" } });
    expect(win.Next.context.meta).toEqual({ page: "home", version: "1" });
  });

  it("accepts all JSON primitive types", () => {
    win.Next._init({
      str: "text",
      num: 42,
      flag: true,
      arr: [1, 2],
      nil: null,
    });
    expect(win.Next.context.str).toBe("text");
    expect(win.Next.context.num).toBe(42);
    expect(win.Next.context.flag).toBe(true);
    expect(win.Next.context.arr).toEqual([1, 2]);
    expect(win.Next.context.nil).toBeNull();
  });
});

describe("Next._init dev channel", () => {
  // _configure is stubbed, a real call would leave this file's runtime wired for dev.
  function spyConfigure() {
    return vi.spyOn(win.Next.partial, "_configure").mockImplementation(() => undefined);
  }

  beforeEach(() => {
    win.Next._init({});
  });

  it("opens the dev channel from a literal true and before the initial scan", () => {
    const configure = spyConfigure();
    const ready = vi.spyOn(win.Next.partial, "ready");
    win.Next._init({ $dev: true });
    expect(configure).toHaveBeenCalledWith({ dev: true });
    expect(configure.mock.invocationCallOrder[0]!).toBeLessThan(
      ready.mock.invocationCallOrder[0]!,
    );
  });

  it("leaves the channel shut when the payload carries no key", () => {
    const configure = spyConfigure();
    win.Next._init({});
    expect(configure).not.toHaveBeenCalled();
  });

  it("leaves the channel shut for a truthy value that is not the boolean", () => {
    const configure = spyConfigure();
    win.Next._init({ $dev: "true" });
    expect(configure).not.toHaveBeenCalled();
  });

  it("keeps $dev in the context store and in the changed keys", () => {
    const configure = spyConfigure();
    const received: Record<string, unknown>[] = [];
    const off = win.Next.on("context-updated", (payload) => {
      received.push(payload);
    });
    win.Next._init({ $dev: true, page: "home" });
    expect(win.Next.context.$dev).toBe(true);
    expect(received[0]!.changed).toEqual(["$dev", "page"]);
    off();
    expect(configure).toHaveBeenCalledTimes(1);
  });
});

describe("Next._init csrf seed", () => {
  // The bodies the stub fetch hands back, shifted one per request.
  let bodies: string[];
  let calls: RequestInit[];

  function envelope(csrf?: string): string {
    const meta =
      csrf === undefined ? "" : `,"csrf":{"header":"X-CSRFToken","token":"${csrf}"}`;
    return `{"version":"v1","ops":[],"assets":[],"form":null${meta}}`;
  }

  function header(index: number): string | null {
    return new Headers(calls[index]!.headers).get("X-CSRFToken");
  }

  beforeEach(() => {
    bodies = [];
    calls = [];
    win.Next.partial._reset();
    win.Next.partial._configure({
      document,
      fetch: (_url, init) => {
        calls.push(init);
        return Promise.resolve(
          new Response(bodies.shift() ?? envelope(), {
            status: 200,
            headers: { "content-type": "application/vnd.next.patches+json" },
          }),
        );
      },
      navigate: () => {},
    });
  });

  afterEach(() => {
    win.Next.partial._reset();
    win.Next._init({});
  });

  it("seeds the token so a programmatic mutation carries the CSRF header", async () => {
    win.Next._init({ $csrf: { header: "X-CSRFToken", token: "seeded" } });
    await win.Next.partial.fetch({ url: "/mutate/", method: "POST" });
    expect(header(0)).toBe("seeded");
  });

  it("sends no CSRF header when the payload carries no $csrf", async () => {
    win.Next._init({ page: "home" });
    await win.Next.partial.fetch({ url: "/mutate/", method: "POST" });
    expect(header(0)).toBeNull();
  });

  it("keeps a token learned from an envelope when a later payload omits $csrf", async () => {
    win.Next.partial.setCsrf({ header: "X-CSRFToken", token: "learned" });
    win.Next._init({ page: "home" });
    await win.Next.partial.fetch({ url: "/mutate/", method: "POST" });
    expect(header(0)).toBe("learned");
  });

  it("lets an envelope rotation override the seeded token", async () => {
    bodies = [envelope("rotated")];
    win.Next._init({ $csrf: { header: "X-CSRFToken", token: "seeded" } });
    await win.Next.partial.fetch({ url: "/mutate/", method: "POST", uid: "u1" });
    await win.Next.partial.fetch({ url: "/mutate/", method: "POST", uid: "u1" });
    expect(header(0)).toBe("seeded");
    expect(header(1)).toBe("rotated");
  });

  it("leaves a safe method without the header even with a seed", async () => {
    win.Next._init({ $csrf: { header: "X-CSRFToken", token: "seeded" } });
    await win.Next.partial.fetch({ url: "/list/" });
    expect(header(0)).toBeNull();
  });

  it.each([
    ["a non-object", "nope"],
    ["a missing token", { header: "X-CSRFToken" }],
    ["a missing header", { token: "seeded" }],
    ["a non-string token", { header: "X-CSRFToken", token: 42 }],
  ])("ignores %s payload and still boots", async (_label, payload) => {
    let ready = 0;
    const off = win.Next.on("ready", () => {
      ready += 1;
    });
    ready = 0;
    win.Next._init({ $csrf: payload });
    off();
    await win.Next.partial.fetch({ url: "/mutate/", method: "POST" });
    expect(ready).toBe(1);
    expect(header(0)).toBeNull();
    expect(win.Next.context.$csrf).toEqual(payload);
  });

  it("warns about a malformed payload only in dev", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    // _configure is stubbed so the shared runtime does not stay wired for dev.
    const configure = vi
      .spyOn(win.Next.partial, "_configure")
      .mockImplementation(() => undefined);
    win.Next._init({ $csrf: { header: "X-CSRFToken" } });
    expect(warn).not.toHaveBeenCalled();
    win.Next._init({ $dev: true, $csrf: { header: "X-CSRFToken" } });
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("$csrf"));
    warn.mockClear();
    win.Next._init({ $dev: true, $csrf: { header: "X-CSRFToken", token: "ok" } });
    expect(warn).not.toHaveBeenCalled();
    expect(configure).toHaveBeenCalledTimes(2);
  });
});

describe("Next.context", () => {
  beforeEach(() => {
    win.Next._init({});
  });

  it("returns a frozen object", () => {
    win.Next._init({ key: "val" });
    expect(Object.isFrozen(win.Next.context)).toBe(true);
  });

  it("returns a new object on each property access", () => {
    win.Next._init({ key: "val" });
    const first = win.Next.context;
    const second = win.Next.context;
    expect(first).not.toBe(second);
  });

  it("mutations to the returned object do not affect stored context", () => {
    win.Next._init({ key: "original" });
    const snap = win.Next.context as Record<string, unknown>;
    try {
      snap.key = "mutated";
    } catch {
      /* frozen */
    }
    expect(win.Next.context.key).toBe("original");
  });

  it("is empty after _init with an empty object", () => {
    win.Next._init({ key: "value" });
    win.Next._init({});
    expect(Object.keys(win.Next.context)).toHaveLength(0);
  });
});

describe("window.Next", () => {
  it("is assigned on globalThis", () => {
    expect(win.Next).toBeDefined();
  });

  it("exposes a context getter", () => {
    expect(typeof win.Next.context).toBe("object");
    expect(win.Next.context).not.toBeNull();
  });

  it("exposes a _init method", () => {
    expect(typeof win.Next._init).toBe("function");
  });

  it("exposes an on method", () => {
    expect(typeof win.Next.on).toBe("function");
  });

  it("exposes a use method", () => {
    expect(typeof win.Next.use).toBe("function");
  });
});

describe("Next.on", () => {
  beforeEach(() => {
    win.Next._init({});
  });

  it("fires the ready listener on _init", () => {
    let called = 0;
    win.Next.on("ready", () => {
      called += 1;
    });
    called = 0;
    win.Next._init({ page: "home" });
    expect(called).toBe(1);
  });

  it("fires the context-updated listener on _init with all seeded keys changed", () => {
    const received: Record<string, unknown>[] = [];
    win.Next.on("context-updated", (payload) => {
      received.push(payload);
    });
    win.Next._init({ user: "alice", page: "home" });
    expect(received).toHaveLength(1);
    expect(received[0]).toEqual({
      context: { user: "alice", page: "home" },
      changed: ["user", "page"],
    });
  });

  it("supports multiple listeners on the same event", () => {
    let a = 0;
    let b = 0;
    win.Next.on("ready", () => {
      a += 1;
    });
    win.Next.on("ready", () => {
      b += 1;
    });
    a = 0;
    b = 0;
    win.Next._init({});
    expect(a).toBe(1);
    expect(b).toBe(1);
  });

  it("isolates a throwing listener so the rest still receive the payload", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const seen: string[] = [];
    win.Next.on("context-updated", () => seen.push("first"));
    win.Next.on("context-updated", () => {
      throw new Error("boom");
    });
    win.Next.on("context-updated", () => seen.push("third"));
    win.Next._init({});
    expect(seen).toEqual(["first", "third"]);
    expect(error).toHaveBeenCalledWith("[next] listener threw", expect.any(Error));
    error.mockRestore();
  });

  it("snapshots the bucket so a listener subscribing mid-dispatch waits a round", () => {
    let lateCalls = 0;
    const late = (): void => {
      lateCalls += 1;
    };
    win.Next.on("context-updated", () => {
      win.Next.on("context-updated", late);
    });
    win.Next._init({});
    expect(lateCalls).toBe(0);
    win.Next._init({});
    expect(lateCalls).toBeGreaterThan(0);
  });

  it("returns an unsubscribe function that stops future dispatches", () => {
    let called = 0;
    const off = win.Next.on("ready", () => {
      called += 1;
    });
    called = 0;
    win.Next._init({});
    off();
    win.Next._init({});
    expect(called).toBe(1);
  });

  it("fires ready immediately for listeners registered after _init", () => {
    win.Next._init({ page: "home" });
    let received: Record<string, unknown> | null = null;
    win.Next.on("ready", (ctx) => {
      received = ctx;
    });
    expect(received).toEqual({ page: "home" });
  });

  it("does not replay context-updated for late subscribers", () => {
    win.Next._init({ page: "home" });
    let called = 0;
    win.Next.on("context-updated", () => {
      called += 1;
    });
    expect(called).toBe(0);
  });
});

describe("Next.on arbitrary events", () => {
  beforeEach(() => {
    win.Next._init({});
  });

  it("carries lifecycle and user events beyond the literal union", () => {
    let detail: Record<string, unknown> | null = null;
    const off = win.Next.on("partial:applied", (payload) => {
      detail = payload;
    });
    win.Next.partial.apply({
      version: "v1",
      ops: [],
      assets: [],
      form: null,
    });
    expect(detail).not.toBeNull();
    off();
  });
});

describe("Next.partial namespace", () => {
  beforeEach(() => {
    win.Next._init({});
    win.Next.partial._reset();
  });

  it("is exposed on the Next class", () => {
    expect(typeof win.Next.partial).toBe("object");
    expect(typeof win.Next.partial.apply).toBe("function");
    expect(typeof win.Next.partial.fetch).toBe("function");
    expect(typeof win.Next.partial.defineOp).toBe("function");
    expect(typeof win.Next.partial._reset).toBe("function");
  });

  it("merges into Next.context through a custom op and fires context-updated", () => {
    let fired = 0;
    const off = win.Next.on("context-updated", () => {
      fired += 1;
    });
    fired = 0;
    win.Next.partial.defineOp("seed", (_patch, ctx) => {
      (ctx as { mergeContext(data: Record<string, unknown>): void }).mergeContext({
        poll: 7,
      });
    });
    win.Next.partial.apply({
      version: "v1",
      ops: [{ op: "seed" }],
      assets: [],
      form: null,
    });
    expect(win.Next.context.poll).toBe(7);
    expect(fired).toBe(1);
    off();
  });

  it("reports only the delta keys as changed while context carries the whole store", () => {
    win.Next._init({ page: "home", user: "alice" });
    const received: Record<string, unknown>[] = [];
    const off = win.Next.on("context-updated", (payload) => {
      received.push(payload);
    });
    win.Next.partial.defineOp("seed", (_patch, ctx) => {
      (ctx as { mergeContext(data: Record<string, unknown>): void }).mergeContext({
        user: "bob",
        poll: 7,
      });
    });
    win.Next.partial.apply({
      version: "v1",
      ops: [{ op: "seed" }],
      assets: [],
      form: null,
    });
    expect(received).toHaveLength(1);
    expect(received[0]).toEqual({
      context: { page: "home", user: "bob", poll: 7 },
      changed: ["user", "poll"],
    });
    off();
  });
});

describe("Next.use", () => {
  beforeEach(() => {
    win.Next._init({});
  });

  it("calls the plugin with the Next namespace", () => {
    let seen: unknown = null;
    win.Next.use((next) => {
      seen = next;
    });
    expect(seen).toBe(win.Next);
  });

  it("returns the plugin's return value", () => {
    const result = win.Next.use(() => "hello");
    expect(result).toBe("hello");
  });

  it("lets plugins subscribe to events", () => {
    let triggered = false;
    win.Next.use((next) => {
      next.on("ready", () => {
        triggered = true;
      });
    });
    win.Next._init({});
    expect(triggered).toBe(true);
  });
});

describe("Next._init deferred csrf", () => {
  const calls: string[] = [];

  beforeEach(() => {
    calls.length = 0;
    win.Next.partial._reset();
    win.Next.partial._configure({
      document,
      navigate: () => {},
      fetch: async (url, init) => {
        calls.push(new URL(url).pathname);
        const token = new Headers(init.headers).get("X-CSRFToken");
        if (token !== null) calls.push(`token ${token}`);
        const minted = url.endsWith("/_next/csrf/");
        return new Response(
          minted
            ? '{"header":"X-CSRFToken","token":"minted"}'
            : '{"version":"v1","ops":[],"assets":[],"form":null}',
          {
            headers: {
              "content-type": minted
                ? "application/json"
                : "application/vnd.next.patches+json",
            },
          },
        );
      },
    });
  });

  afterEach(() => {
    win.Next.partial._reset();
    win.Next._init({});
  });

  it("seeds the endpoint so the first mutation mints its token", async () => {
    win.Next._init({ $csrf: { header: "X-CSRFToken", url: "/_next/csrf/" } });
    await win.Next.partial.fetch({ url: "/mutate/", method: "POST" });
    expect(calls).toEqual(["/_next/csrf/", "/mutate/", "token minted"]);
  });
});

describe("Next.consent and Next.scripts", () => {
  function clearCookies(): void {
    for (const pair of document.cookie.split(";")) {
      const name = pair.split("=")[0]!.trim();
      if (name !== "") document.cookie = `${name}=; max-age=0; path=/`;
    }
  }

  // The chunk's registry lives for the file, so each case names its own scripts.
  beforeEach(() => {
    document.head.innerHTML = "";
  });

  afterEach(clearCookies);

  it("seeds consent from $consent and activates a gated script on update", () => {
    win.Next._init({
      $consent: {
        categories: ["necessary", "marketing"],
        decided: false,
        granted: ["necessary"],
      },
      $scripts: [
        { name: "pixel", init: "window.nextPixel = 1", category: "marketing" },
        { name: "base", init: "window.nextBase = 1" },
      ],
    });
    expect(win.Next.consent.decided()).toBe(false);
    expect(win.Next.scripts.status("base")).toBe("loaded");
    expect(win.Next.scripts.status("pixel")).toBe("blocked");
    win.Next.consent.update({ marketing: true });
    expect(win.Next.consent.get()).toEqual({ necessary: true, marketing: true });
    expect(win.Next.scripts.status("pixel")).toBe("loaded");
    expect(document.cookie).toContain("next_consent=1:marketing:");
  });

  it("announces the starting consent once the chunk is configured", () => {
    const seen: Record<string, unknown>[] = [];
    const off = win.Next.on("next:consent", (payload) => seen.push(payload));
    document.cookie = "next_consent=1:marketing:1700000000; path=/";
    win.Next._init({
      $consent: { categories: ["necessary", "marketing"], decided: false, granted: [] },
    });
    off();
    expect(seen).toEqual([
      { granted: ["necessary", "marketing"], denied: [], changed: [], initial: true },
    ]);
  });

  it("leaves a late subscriber to read the state rather than replaying it", () => {
    win.Next._init({ $consent: { categories: ["necessary", "analytics"] } });
    win.Next.consent.update({ analytics: true });
    const seen = vi.fn();
    const off = win.Next.on("next:consent", seen);
    off();
    expect(seen).not.toHaveBeenCalled();
    expect(win.Next.consent.get()).toEqual({ necessary: true, analytics: true });
  });

  it("resolves ready with the installed surfaces once configured", async () => {
    document.cookie = "next_consent=1:analytics:1700000000; path=/";
    win.Next._init({
      $consent: { categories: ["necessary", "analytics", "marketing"] },
    });
    const chunk = await win.Next.ready("scripts");
    expect(chunk.consent).toBe(win.Next.consent);
    expect(chunk.scripts).toBe(win.Next.scripts);
    expect(chunk.consent.decided()).toBe(true);
    expect(chunk.consent.get()).toEqual({
      necessary: true,
      analytics: true,
      marketing: false,
    });
  });

  it("reveals consented markup a zone morph brings in", () => {
    document.cookie = "next_consent=1:marketing:1700000000; path=/";
    win.Next._init({ $consent: { categories: ["necessary", "marketing"] } });
    document.body.innerHTML = '<div data-next-zone="video"></div>';
    win.Next.partial.apply({
      version: "v1",
      ops: [
        {
          op: "inner",
          target: { zone: "video" },
          html:
            '<template data-next-consented="marketing"><b>video</b></template>' +
            "<a>allow</a><!--/next-consented-->",
        },
      ],
    });
    expect(document.querySelector('[data-next-zone="video"]')!.innerHTML).toBe(
      "<b>video</b>",
    );
  });

  it("reveals only inside the nodes a patch touched", () => {
    document.cookie = "next_consent=1:marketing:1700000000; path=/";
    win.Next._init({ $consent: { categories: ["necessary", "marketing"] } });
    const block =
      '<template data-next-consented="marketing"><b>video</b></template>' +
      "<a>allow</a><!--/next-consented-->";
    document.body.innerHTML =
      `<div id="aside">${block}</div>` + '<div data-next-zone="video"></div>';
    win.Next.partial.apply({
      version: "v1",
      ops: [{ op: "inner", target: { zone: "video" }, html: block }],
    });
    expect(document.querySelector("#aside template")).not.toBeNull();
    expect(document.querySelector('[data-next-zone="video"]')!.innerHTML).toBe(
      "<b>video</b>",
    );
  });

  it("reveals a consented block a replace patch puts in place", () => {
    document.cookie = "next_consent=1:marketing:1700000000; path=/";
    win.Next._init({ $consent: { categories: ["necessary", "marketing"] } });
    document.body.innerHTML = '<p id="slot"></p>';
    win.Next.partial.apply({
      version: "v1",
      ops: [
        {
          op: "replace",
          target: { css: "#slot" },
          html:
            '<template data-next-consented="marketing"><b>video</b></template>' +
            "<a>allow</a><!--/next-consented-->",
        },
      ],
    });
    expect(document.body.innerHTML).toBe("<b>video</b>");
  });

  it("mounts revealed markup the way it mounts morphed markup", async () => {
    const calls: string[] = [];
    win.Next.partial._reset();
    win.Next.partial._configure({
      document,
      navigate: () => {},
      fetch: async (url, init) => {
        calls.push(
          `${new URL(url).pathname} ${new Headers(init.headers).get("X-Next-Zone")}`,
        );
        return new Response('{"version":"v1","ops":[]}', {
          headers: { "content-type": "application/vnd.next.patches+json" },
        });
      },
    });
    const mounted: string[] = [];
    const embeds: Element[] = [];
    const onMounted = (event: Event): void => {
      mounted.push((event.target as Element).className);
    };
    document.addEventListener("next:mounted", onMounted);
    const off = win.Next.partial.onMount(".embed", (el) => embeds.push(el));
    document.cookie = "next_consent=1:marketing:1700000000; path=/";
    document.body.innerHTML =
      '<template data-next-consented="marketing"><div class="embed" ' +
      'data-next-zone="player" data-next-lazy="load"></div></template>' +
      "<a>allow</a><!--/next-consented-->";
    win.Next._init({ $consent: { categories: ["necessary", "marketing"] } });
    document.removeEventListener("next:mounted", onMounted);
    off();
    await Promise.resolve();
    expect(mounted).toEqual(["embed"]);
    expect(embeds).toHaveLength(1);
    expect(calls).toEqual([`${location.pathname} player`]);
    win.Next.partial._reset();
  });

  it("carries the bootstrap nonce onto an inserted script", () => {
    win.Next._init({ $scripts: [{ name: "plain", init: "window.nextPlain = 1" }] });
    const el = document.head.querySelector('script[data-next-script="plain"]')!;
    expect(el.getAttribute("nonce")).toBeNull();
  });
});

describe("Next.navigation", () => {
  afterEach(() => {
    win.Next._init({});
  });

  it("answers where the page stands", () => {
    document.title = "Home";
    expect(win.Next.navigation.current()).toEqual({
      url: location.href,
      path: location.pathname + location.search,
      title: "Home",
    });
  });

  it("announces nothing for the page load itself", () => {
    const navigated = vi.fn();
    const off = win.Next.on("next:navigated", navigated);
    win.Next._init({});
    off();
    expect(navigated).not.toHaveBeenCalled();
  });
});

describe("the dev chunk", () => {
  function devTags(): Element[] {
    return Array.from(document.head.querySelectorAll('script[src*="next.dev"]'));
  }

  beforeEach(() => {
    for (const el of devTags()) el.remove();
  });

  afterEach(() => {
    win.Next._init({});
  });

  // First, since the page fetches its dev chunk once and only a failure frees it.
  it("reports a dev chunk that failed to load on the bus", () => {
    const errors: Record<string, unknown>[] = [];
    const off = win.Next.on("partial:error", (payload) => errors.push(payload));
    win.Next._init({ $dev: true, $chunks: { dev: "/static/next/next.dev.min.js" } });
    devTags()[0]!.dispatchEvent(new Event("error"));
    off();
    expect(errors).toEqual([
      expect.objectContaining({ kind: "asset", url: "/static/next/next.dev.min.js" }),
    ]);
  });

  it("is fetched once for a page rendered under $dev", () => {
    const chunks = { dev: "/static/next/next.dev.min.js" };
    win.Next._init({ $dev: true, $chunks: chunks });
    win.Next._init({ $dev: true, $chunks: chunks });
    expect(devTags().map((el) => el.getAttribute("src"))).toEqual([
      "/static/next/next.dev.min.js",
    ]);
  });

  it("is never fetched for a production page", () => {
    win.Next._init({ $chunks: { dev: "/static/next/next.dev.min.js" } });
    win.Next._init({ $dev: "true", $chunks: { dev: "/static/next/next.dev.min.js" } });
    expect(devTags()).toEqual([]);
  });
});
