import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Wire } from "./wire";
import { createCsrf } from "./csrf";
import type { CsrfMint } from "./csrf";
import { lazyChunk } from "./chunks";
import type { CsrfPayload, CsrfSource, WireDeps, WireRequest } from "./wire";
import type { SessionStore } from "./assets";
import type { Envelope } from "./apply";
import {
  ACCEPT,
  CONTENT_TYPE,
  HEADER_REQUEST_ID,
  REQUEST_FLAG,
  currentUrl,
} from "./protocol";
import { chunkModules } from "./test-doubles";

// Isolated per harness so the navigate-once flag never leaks through jsdom storage.
function memorySession(): SessionStore {
  const store = new Map<string, string>();
  return {
    get: (key) => store.get(key) ?? null,
    set: (key, value) => void store.set(key, value),
    remove: (key) => void store.delete(key),
  };
}

function envelopeResponse(
  body: string,
  init: { status?: number; type?: string; url?: string; redirected?: boolean } = {},
): Response {
  const headers = new Headers({
    "content-type": init.type ?? CONTENT_TYPE,
  });
  const response = new Response(body, { status: init.status ?? 200, headers });
  Object.defineProperty(response, "url", { value: init.url ?? "/here/" });
  Object.defineProperty(response, "redirected", {
    value: init.redirected ?? false,
  });
  return response;
}

type Harness = ReturnType<typeof makeWire>;

function makeWire(
  responder: (url: string, init: RequestInit) => Promise<Response>,
  opts: {
    version?: string;
    csrf?: CsrfPayload;
    ensure?: CsrfSource["ensure"];
    session?: SessionStore;
    applied?: boolean;
    tunnel?: WireDeps["tunnel"];
    document?: Document;
  } = {},
) {
  const dispatched: { event: string; detail: Record<string, unknown> }[] = [];
  const navigated: string[] = [];
  const envelopes: unknown[] = [];
  const pages: (string | undefined)[] = [];
  const calls: { url: string; init: RequestInit }[] = [];
  const session = opts.session ?? memorySession();
  const wire = new Wire({
    fetch: (url, init) => {
      calls.push({ url, init });
      return responder(url, init);
    },
    navigate: (url) => navigated.push(url),
    session,
    dispatch: (event, detail) => dispatched.push({ event, detail }),
    onEnvelope: async (raw, _response, _snapshot, _key, page) => {
      envelopes.push(raw);
      pages.push(page);
      return { envelope: raw as Envelope, ok: opts.applied ?? true };
    },
    ...(opts.tunnel === undefined ? {} : { tunnel: opts.tunnel }),
    ...(opts.document === undefined ? {} : { document: opts.document }),
    version: () => opts.version ?? "v1",
    csrf: {
      current: () => opts.csrf,
      ensure: opts.ensure ?? (async () => opts.csrf),
    },
  });
  return { wire, dispatched, navigated, envelopes, pages, calls, session };
}

const ENVELOPE = '{"version":"v1","ops":[],"assets":[],"form":null}';

describe("Wire headers", () => {
  it("sends the intent headers on a GET", async () => {
    const h: Harness = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({ url: "/list/", zone: "request-list" });
    const headers = new Headers(h.calls[0]!.init.headers);
    expect(headers.get(REQUEST_FLAG)).toBe("1");
    expect(headers.get("Accept")).toBe(ACCEPT);
    expect(headers.get("X-Next-Version")).toBe("v1");
    expect(headers.get("X-Next-Zone")).toBe("request-list");
  });

  it("omits the version header before the client has learned one", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE), { version: "" });
    await h.wire.fetch({ url: "/list/", zone: "request-list" });
    const headers = new Headers(h.calls[0]!.init.headers);
    expect(headers.has("X-Next-Version")).toBe(false);
  });

  it("adds the CSRF header from the payload on unsafe methods", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE), {
      csrf: { header: "X-CSRFToken", token: "tok" },
    });
    await h.wire.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    const headers = new Headers(h.calls[0]!.init.headers);
    expect(headers.get("X-CSRFToken")).toBe("tok");
  });

  it("omits the CSRF header on safe methods", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE), {
      csrf: { header: "X-CSRFToken", token: "tok" },
    });
    await h.wire.fetch({ url: "/list/", zone: "z" });
    const headers = new Headers(h.calls[0]!.init.headers);
    expect(headers.has("X-CSRFToken")).toBe(false);
  });
});

describe("Wire request body", () => {
  it("forwards a request body to the fetch init", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE), {
      csrf: { header: "X-CSRFToken", token: "tok" },
    });
    await h.wire.fetch({
      url: "/_next/form/u1/",
      method: "POST",
      uid: "u1",
      body: "name=alice",
    });
    expect(h.calls[0]!.init.body).toBe("name=alice");
  });
});

describe("Wire classification", () => {
  it("hands a partial envelope to onEnvelope", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({ url: "/list/", zone: "z" });
    expect(h.envelopes).toHaveLength(1);
    expect(h.navigated).toEqual([]);
  });

  it("navigates on a non-envelope content-type", async () => {
    const h = makeWire(async () =>
      envelopeResponse("<html></html>", { type: "text/html", url: "/login/" }),
    );
    await h.wire.fetch({ url: "/list/", zone: "z" });
    expect(h.navigated).toEqual(["/login/"]);
    expect(h.envelopes).toEqual([]);
  });

  it("navigates on a redirected response even with the envelope type", async () => {
    const h = makeWire(async () =>
      envelopeResponse(ENVELOPE, { url: "/final/", redirected: true }),
    );
    await h.wire.fetch({ url: "/list/", zone: "z" });
    expect(h.navigated).toEqual(["/final/"]);
  });

  it("errors instead of a 405 navigation when a mutation is denied", async () => {
    const h = makeWire(async () =>
      envelopeResponse("<html>denied</html>", {
        status: 403,
        type: "text/html",
        url: "/_next/form/u1/",
      }),
    );
    await h.wire.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    expect(h.navigated).toEqual([]);
    const err = h.dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.status).toBe(403);
    expect(err!.detail.kind).toBe("http");
  });

  it("navigates on 409 on a safe method", async () => {
    const h = makeWire(async () =>
      envelopeResponse("", { status: 409, type: "text/plain", url: "/here/" }),
    );
    await h.wire.fetch({ url: "/here/", zone: "z" });
    expect(h.navigated).toEqual(["/here/"]);
  });

  it("falls back to the request url on a 409 with an empty response url", async () => {
    const h = makeWire(async () =>
      envelopeResponse("", { status: 409, type: "text/plain", url: "" }),
    );
    await h.wire.fetch({ url: "/here/", zone: "z" });
    expect(h.navigated).toEqual([`${location.origin}/here/`]);
  });

  it("falls back to the request url on a non-envelope with an empty response url", async () => {
    const h = makeWire(async () =>
      envelopeResponse("<html></html>", { type: "text/html", url: "" }),
    );
    await h.wire.fetch({ url: "/list/", zone: "z" });
    expect(h.navigated).toEqual([`${location.origin}/list/`]);
  });

  it("treats a response with no content-type header as a navigation", async () => {
    const h = makeWire(async () => {
      const response = new Response("<html></html>", { status: 200 });
      response.headers.delete("content-type");
      Object.defineProperty(response, "url", { value: "/page/" });
      Object.defineProperty(response, "redirected", { value: false });
      return response;
    });
    await h.wire.fetch({ url: "/list/", zone: "z" });
    expect(h.navigated).toEqual(["/page/"]);
    expect(h.envelopes).toEqual([]);
  });

  it("emits partial:error on 5xx with the body", async () => {
    const h = makeWire(async () =>
      envelopeResponse("boom", { status: 500, type: "text/plain" }),
    );
    await h.wire.fetch({ url: "/list/", zone: "z" });
    const err = h.dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.status).toBe(500);
    expect(err!.detail.body).toBe("boom");
    expect(err!.detail.kind).toBe("http");
  });

  it("emits partial:error when the fetch itself rejects", async () => {
    const h = makeWire(async () => {
      throw new TypeError("network down");
    });
    await h.wire.fetch({ url: "/list/", zone: "z" });
    const err = h.dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.kind).toBe("network");
    expect(err!.detail.error).toBeInstanceOf(TypeError);
    expect(err!.detail.status).toBeUndefined();
    expect(err!.detail.body).toBeUndefined();
  });

  it("emits partial:error on malformed JSON", async () => {
    const h = makeWire(async () => envelopeResponse("{not json"));
    await h.wire.fetch({ url: "/list/", zone: "z" });
    const err = h.dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.kind).toBe("parse");
    expect(h.envelopes).toEqual([]);
  });

  it("runs a parse-hook for a foreign content-type", async () => {
    const h = makeWire(async () =>
      envelopeResponse("raw-body", { type: "text/vnd.next.stream+html" }),
    );
    h.wire.parseHook("text/vnd.next.stream+html", (_resp, body) => ({
      version: "v1",
      ops: [],
      body,
    }));
    await h.wire.fetch({ url: "/list/", zone: "z" });
    expect(h.envelopes).toHaveLength(1);
    expect((h.envelopes[0] as { body: string }).body).toBe("raw-body");
  });

  // A response whose body read rejects after the headers arrived.
  function cutOff(error: Error): Response {
    const response = envelopeResponse(ENVELOPE);
    Object.defineProperty(response, "text", { value: () => Promise.reject(error) });
    return response;
  }

  it("reports a body read the connection cut off as a network error", async () => {
    const h = makeWire(async () => cutOff(new TypeError("connection reset")));
    await expect(h.wire.fetch({ url: "/list/", zone: "z" })).resolves.toMatchObject({
      status: "error",
      applied: false,
    });
    const err = h.dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail).toMatchObject({
      kind: "network",
      error: expect.any(TypeError),
    });
    expect(h.envelopes).toEqual([]);
  });

  it("drops a body read an abort cut off without an error", async () => {
    const aborted = new Error("The operation was aborted.");
    aborted.name = "AbortError";
    const h = makeWire(async () => cutOff(aborted));
    await expect(h.wire.fetch({ url: "/list/", zone: "z" })).resolves.toMatchObject({
      status: "dropped",
      applied: false,
    });
    expect(h.dispatched.some((d) => d.event === "partial:error")).toBe(false);
  });

  it("reports an envelope the handler rejects as a parse error with its body", async () => {
    const dispatched: { event: string; detail: Record<string, unknown> }[] = [];
    const wire = new Wire({
      fetch: async () => envelopeResponse("{}"),
      session: memorySession(),
      dispatch: (event, detail) => dispatched.push({ event, detail }),
      onEnvelope: () => {
        throw new TypeError("envelope without a version");
      },
      csrf: { current: () => undefined, ensure: async () => undefined },
    });
    await expect(wire.fetch({ url: "/list/", zone: "z" })).resolves.toMatchObject({
      status: "error",
      applied: false,
    });
    const errors = dispatched.filter((d) => d.event === "partial:error");
    expect(errors.map((d) => d.detail)).toEqual([
      { kind: "parse", body: "{}", error: expect.any(TypeError) },
    ]);
  });

  it("reports a parse-hook that throws as a parse error with the body", async () => {
    const h = makeWire(async () =>
      envelopeResponse("raw-body", { type: "text/vnd.next.stream+html" }),
    );
    h.wire.parseHook("text/vnd.next.stream+html", () => {
      throw new SyntaxError("bad frame");
    });
    await expect(h.wire.fetch({ url: "/list/", zone: "z" })).resolves.toMatchObject({
      status: "error",
      applied: false,
    });
    const err = h.dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail).toMatchObject({ kind: "parse", body: "raw-body" });
    expect(h.envelopes).toEqual([]);
  });
});

describe("Wire non-envelope navigate-once", () => {
  const NAVIGATED = "next:partial:navigated";

  function html(url: string): Response {
    return envelopeResponse("<html>login</html>", { type: "text/html", url });
  }

  it("navigates and sets the flag on the first non-envelope", async () => {
    const session = memorySession();
    const h = makeWire(async () => html("/login/"), { session });
    await h.wire.fetch({ url: "/list/", zone: "z" });
    expect(h.navigated).toEqual(["/login/"]);
    expect(session.get(NAVIGATED)).toBe("1");
  });

  it("degrades to partial:error without navigating when the flag stands", async () => {
    const session = memorySession();
    session.set(NAVIGATED, "1");
    const h = makeWire(async () => html("/login/"), { session });
    await h.wire.fetch({ url: "/list/", zone: "z" });
    expect(h.navigated).toEqual([]);
    const err = h.dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.kind).toBe("network");
    expect(err!.detail.error).toBeInstanceOf(Error);
    expect(session.get(NAVIGATED)).toBeNull();
  });

  it("clears the flag once a correct envelope classifies", async () => {
    const session = memorySession();
    session.set(NAVIGATED, "1");
    const h = makeWire(async () => envelopeResponse(ENVELOPE), { session });
    await h.wire.fetch({ url: "/list/", zone: "z" });
    expect(h.envelopes).toHaveLength(1);
    expect(session.get(NAVIGATED)).toBeNull();
  });

  it("clears the flag on a parse-hook envelope too", async () => {
    const session = memorySession();
    session.set(NAVIGATED, "1");
    const h = makeWire(
      async () => envelopeResponse("raw", { type: "text/vnd.next.stream+html" }),
      { session },
    );
    h.wire.parseHook("text/vnd.next.stream+html", () => ({ version: "v1", ops: [] }));
    await h.wire.fetch({ url: "/list/", zone: "z" });
    expect(h.envelopes).toHaveLength(1);
    expect(session.get(NAVIGATED)).toBeNull();
  });

  it("guards the redirect branch the same way", async () => {
    const session = memorySession();
    session.set(NAVIGATED, "1");
    const h = makeWire(
      async () => envelopeResponse(ENVELOPE, { url: "/final/", redirected: true }),
      { session },
    );
    await h.wire.fetch({ url: "/list/", zone: "z" });
    expect(h.navigated).toEqual([]);
    const err = h.dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.kind).toBe("network");
  });

  it("still errors on a mutating non-envelope with no redirect, flag untouched", async () => {
    const session = memorySession();
    const h = makeWire(
      async () =>
        envelopeResponse("<html>denied</html>", {
          status: 403,
          type: "text/html",
          url: "/_next/form/u1/",
        }),
      { session },
    );
    await h.wire.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    expect(h.navigated).toEqual([]);
    const err = h.dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.kind).toBe("http");
    expect(session.get(NAVIGATED)).toBeNull();
  });
});

describe("Wire access refusals", () => {
  const LOGIN = "/accounts/login/?next=/orders/";

  function login(): Response {
    return envelopeResponse("<html>sign in</html>", {
      type: "text/html",
      url: LOGIN,
      redirected: true,
    });
  }

  function refusal(status: number, url: string): Response {
    return envelopeResponse(`<html>${status}</html>`, {
      status,
      type: "text/html",
      url,
    });
  }

  function errors(h: Harness) {
    return h.dispatched.filter((d) => d.event === "partial:error");
  }

  it("navigates a zone GET redirected to the login page there, reporting nothing", async () => {
    const h = makeWire(async () => login());
    await h.wire.fetch({ url: "/orders/", zone: "list" });
    expect(h.navigated).toEqual([LOGIN]);
    expect(h.envelopes).toEqual([]);
    expect(errors(h)).toEqual([]);
  });

  it.each([403, 404])(
    "reports a %i zone GET and then navigates to the refused page",
    async (status) => {
      const log: unknown[] = [];
      const wire = new Wire({
        fetch: async () => refusal(status, "/orders/"),
        navigate: (url) => log.push({ navigate: url }),
        session: memorySession(),
        dispatch: (event, detail) => log.push({ event, detail }),
        onEnvelope: async (raw) => {
          log.push({ applied: raw });
          return { envelope: raw as Envelope, ok: true };
        },
        csrf: { current: () => undefined, ensure: async () => undefined },
      });
      await wire.fetch({ url: "/orders/", zone: "list" });
      expect(log.slice(1)).toEqual([
        {
          event: "partial:error",
          detail: { kind: "http", status, body: `<html>${status}</html>` },
        },
        { navigate: "/orders/" },
      ]);
    },
  );

  it("navigates a refused zone GET to its own target when the response has no url", async () => {
    const h = makeWire(async () => refusal(403, ""));
    await expect(
      h.wire.fetch({ url: "/orders/", zone: "list" }),
    ).resolves.toMatchObject({
      status: "error",
      error: { kind: "http", status: 403 },
    });
    expect(h.navigated).toEqual([`${location.origin}/orders/`]);
  });

  it("frees the zone queue after a refusal, so the next GET runs", async () => {
    const h = makeWire(async () => refusal(403, "/orders/"));
    await h.wire.fetch({ url: "/orders/", zone: "list" });
    await h.wire.fetch({ url: "/orders/", zone: "list" });
    expect(h.calls).toHaveLength(2);
  });

  it("applies a refusal the server answers with an envelope", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE, { status: 403 }));
    await h.wire.fetch({ url: "/orders/", zone: "list" });
    expect(h.envelopes).toHaveLength(1);
    expect(h.navigated).toEqual([]);
    expect(errors(h)).toEqual([]);
  });

  it("navigates an action redirected to the login page there and frees the lock", async () => {
    const h = makeWire(async () => login());
    await h.wire.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    expect(h.navigated).toEqual([LOGIN]);
    expect(errors(h)).toEqual([]);
    await h.wire.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    expect(h.calls).toHaveLength(2);
  });

  it.each([403, 404])(
    "reports a %i action without navigating and frees the lock",
    async (status) => {
      const h = makeWire(async () => refusal(status, "/_next/form/u1/"));
      await h.wire.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
      expect(h.navigated).toEqual([]);
      expect(h.envelopes).toEqual([]);
      expect(errors(h).map((d) => d.detail)).toEqual([
        { kind: "http", status, body: `<html>${status}</html>` },
      ]);
      await h.wire.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
      expect(h.calls).toHaveLength(2);
    },
  );
});

describe("Wire mutation lock", () => {
  it("drops a second submit while busy: double click yields one fetch", async () => {
    let resolve!: (r: Response) => void;
    const h = makeWire(() => new Promise<Response>((r) => (resolve = r)));
    const first = h.wire.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    const second = h.wire.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    resolve(envelopeResponse(ENVELOPE));
    await Promise.all([first, second]);
    expect(h.calls).toHaveLength(1);
  });

  it("allows a fresh submit after the lock releases", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    await h.wire.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    expect(h.calls).toHaveLength(2);
  });
});

describe("Wire outcomes", () => {
  const POST = { url: "/_next/form/u1/", method: "POST", uid: "u1" } as const;

  function pending() {
    const replies: ((r: Response) => void)[] = [];
    const h = makeWire(() => new Promise<Response>((r) => replies.push(r)));
    const answer = async (body = ENVELOPE): Promise<void> => {
      replies.shift()!(envelopeResponse(body));
      for (let i = 0; i < 10; i += 1) await Promise.resolve();
    };
    return { h, answer };
  }

  it("resolves an applied envelope as ok with the envelope", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    const outcome = await h.wire.fetch(POST);
    expect(outcome).toEqual({
      status: "ok",
      applied: true,
      envelope: JSON.parse(ENVELOPE),
      errors: undefined,
    });
  });

  it("resolves an invalid form with its field errors", async () => {
    const errors = { title: ["This field is required."] };
    const body = JSON.stringify({ form: { uid: "u1", valid: false, errors } });
    const h = makeWire(async () => envelopeResponse(body), { applied: false });
    await expect(h.wire.fetch(POST)).resolves.toMatchObject({
      status: "invalid",
      applied: false,
      errors,
    });
  });

  it("resolves an envelope answered with a client error as an error", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE, { status: 422 }));
    await expect(h.wire.fetch(POST)).resolves.toMatchObject({
      status: "error",
      applied: true,
      envelope: JSON.parse(ENVELOPE),
    });
    expect(h.dispatched.some((d) => d.event === "partial:error")).toBe(false);
  });

  it("resolves a network failure with the error it fired", async () => {
    const h = makeWire(async () => {
      throw new TypeError("offline");
    });
    const outcome = await h.wire.fetch(POST);
    expect(outcome).toEqual({
      status: "error",
      applied: false,
      error: { kind: "network", error: expect.any(TypeError) },
    });
    expect(h.dispatched.find((d) => d.event === "partial:error")!.detail).toBe(
      outcome.error,
    );
  });

  it("resolves a redirect the runtime follows as ok and not applied", async () => {
    const h = makeWire(async () =>
      envelopeResponse("", { type: "text/html", redirected: true, url: "/done/" }),
    );
    await expect(h.wire.fetch(POST)).resolves.toEqual({ status: "ok", applied: false });
    expect(h.navigated).toEqual(["/done/"]);
  });

  it("resolves a redirect with its location when the request keeps the page", async () => {
    const urls = ["/c/?id=7", ""];
    const h = makeWire(async () =>
      envelopeResponse("", { type: "text/html", redirected: true, url: urls.shift()! }),
    );
    await expect(h.wire.fetch({ ...POST, navigate: false })).resolves.toEqual({
      status: "ok",
      applied: false,
      location: "/c/?id=7",
    });
    await expect(
      h.wire.fetch({ url: "/page/", navigate: false }),
    ).resolves.toMatchObject({ location: `${location.origin}/page/` });
    expect(h.navigated).toEqual([]);
  });

  it("resolves a deferred token the csrf chunk failed to load as an error", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE), {
      csrf: { header: "X-CSRFToken", url: "/csrf/" },
      ensure: async () => null,
    });
    await expect(h.wire.fetch(POST)).resolves.toEqual({
      status: "error",
      applied: false,
    });
    expect(h.calls).toEqual([]);
  });

  it("resolves a submit dropped by the lock as dropped", async () => {
    const { h, answer } = pending();
    const first = h.wire.fetch(POST);
    await expect(h.wire.fetch(POST)).resolves.toEqual({
      status: "dropped",
      applied: false,
    });
    await answer();
    await expect(first).resolves.toMatchObject({ status: "ok" });
    expect(h.calls).toHaveLength(1);
  });

  it("runs waiting submits in turn once the lock frees, each one sent", async () => {
    const { h, answer } = pending();
    const bodies = (): unknown[] => h.calls.map((call) => call.init.body);
    const first = h.wire.fetch({ ...POST, body: "a" });
    const second = h.wire.fetch({ ...POST, body: "b", wait: true });
    const third = h.wire.fetch({ ...POST, body: "c", wait: true });
    await answer();
    await expect(first).resolves.toMatchObject({ status: "ok" });
    expect(bodies()).toEqual(["a", "b"]);
    await answer();
    await expect(second).resolves.toMatchObject({ status: "ok", applied: true });
    expect(bodies()).toEqual(["a", "b", "c"]);
    await answer();
    await expect(third).resolves.toMatchObject({ status: "ok", applied: true });
    const free = h.wire.fetch(POST);
    await answer();
    await expect(free).resolves.toMatchObject({ status: "ok" });
  });

  it("drops the older waiters for a latest submit, the newest winning", async () => {
    const { h, answer } = pending();
    const bodies = (): unknown[] => h.calls.map((call) => call.init.body);
    const first = h.wire.fetch({ ...POST, body: "a" });
    const second = h.wire.fetch({ ...POST, body: "b", wait: "latest" });
    const third = h.wire.fetch({ ...POST, body: "c", wait: "latest" });
    await expect(second).resolves.toEqual({ status: "dropped", applied: false });
    expect(bodies()).toEqual(["a"]);
    await answer();
    await expect(first).resolves.toMatchObject({ status: "ok" });
    expect(bodies()).toEqual(["a", "c"]);
    await answer();
    await expect(third).resolves.toMatchObject({ status: "ok", applied: true });
    expect(bodies()).toEqual(["a", "c"]);
  });

  it("resolves an invalid header name as a network error", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    const outcome = await h.wire.fetch({
      url: "/p/",
      zone: "z",
      headers: { "bad header": "1" },
    });
    expect(outcome).toMatchObject({ status: "error", applied: false });
    expect(outcome.error).toMatchObject({ kind: "network" });
    expect(h.calls).toEqual([]);
  });

  it("drops the waiting submit on reset", async () => {
    const { h } = pending();
    void h.wire.fetch(POST);
    const waiting = h.wire.fetch({ ...POST, wait: true });
    h.wire._reset();
    await expect(waiting).resolves.toEqual({ status: "dropped", applied: false });
    expect(h.calls).toHaveLength(1);
  });

  it("resolves a GET a newer one superseded as dropped", async () => {
    const { h, answer } = pending();
    const first = h.wire.fetch({ url: "/p/", zone: "list" });
    const second = h.wire.fetch({ url: "/p/", zone: "list" });
    await answer();
    await answer();
    await expect(first).resolves.toEqual({ status: "dropped", applied: false });
    await expect(second).resolves.toMatchObject({ status: "ok" });
  });
});

describe("Wire safe-GET queue", () => {
  it("aborts the in-flight GET of the same page and zone (latest-wins)", async () => {
    const signals: AbortSignal[] = [];
    let resolveFirst!: (r: Response) => void;
    let n = 0;
    const h = makeWire((_url, init) => {
      signals.push(init.signal!);
      n += 1;
      if (n === 1) return new Promise<Response>((r) => (resolveFirst = r));
      return Promise.resolve(envelopeResponse(ENVELOPE));
    });
    const first = h.wire.fetch({ url: "/p/", zone: "list" });
    const second = h.wire.fetch({ url: "/p/", zone: "list" });
    expect(signals[0]!.aborted).toBe(true);
    resolveFirst(envelopeResponse(ENVELOPE));
    await Promise.all([first, second]);
    expect(h.envelopes).toHaveLength(1);
  });

  it("runs same-zone GETs for different pages independently", async () => {
    const signals: AbortSignal[] = [];
    let resolveFirst!: (r: Response) => void;
    let n = 0;
    const h = makeWire((_url, init) => {
      signals.push(init.signal!);
      n += 1;
      if (n === 1) return new Promise<Response>((r) => (resolveFirst = r));
      return Promise.resolve(envelopeResponse(ENVELOPE));
    });
    const first = h.wire.fetch({ url: "/host/", zone: "list" });
    const second = h.wire.fetch({ url: "/modal/", zone: "list" });
    expect(signals[0]!.aborted).toBe(false);
    resolveFirst(envelopeResponse(ENVELOPE));
    await Promise.all([first, second]);
    expect(h.envelopes).toHaveLength(2);
  });

  it("a re-filtered GET of the same page supersedes across query strings", async () => {
    const signals: AbortSignal[] = [];
    let resolveFirst!: (r: Response) => void;
    let n = 0;
    const h = makeWire((_url, init) => {
      signals.push(init.signal!);
      n += 1;
      if (n === 1) return new Promise<Response>((r) => (resolveFirst = r));
      return Promise.resolve(envelopeResponse(ENVELOPE));
    });
    const first = h.wire.fetch({ url: "/p/?window=1h", zone: "totals" });
    const second = h.wire.fetch({ url: "/p/?window=6h", zone: "totals" });
    expect(signals[0]!.aborted).toBe(true);
    resolveFirst(envelopeResponse(ENVELOPE));
    await Promise.all([first, second]);
    expect(h.envelopes).toHaveLength(1);
  });

  it("does not abort across different zones", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    await Promise.all([
      h.wire.fetch({ url: "/a/", zone: "a" }),
      h.wire.fetch({ url: "/b/", zone: "b" }),
    ]);
    expect(h.envelopes).toHaveLength(2);
  });

  it("a mutating POST with a zone intent never joins the GET queue", async () => {
    const signals: (AbortSignal | undefined)[] = [];
    let resolveFirst!: (r: Response) => void;
    let n = 0;
    const h = makeWire((_url, init) => {
      signals.push(init.signal ?? undefined);
      n += 1;
      if (n === 1) return new Promise<Response>((r) => (resolveFirst = r));
      return Promise.resolve(envelopeResponse(ENVELOPE));
    });
    const inflight = h.wire.fetch({ url: "/p/", zone: "z" });
    await h.wire.fetch({ url: "/p/", method: "POST", uid: "u1", zone: "z" });
    expect(signals[0]!.aborted).toBe(false);
    resolveFirst(envelopeResponse(ENVELOPE));
    await inflight;
    expect(h.envelopes).toHaveLength(2);
  });

  it("keeps AbortError out of partial:error", async () => {
    let n = 0;
    const h = makeWire((_url, init) => {
      n += 1;
      if (n === 1) {
        return new Promise<Response>((_resolve, reject) => {
          init.signal!.addEventListener("abort", () => {
            const err = new Error("aborted");
            err.name = "AbortError";
            reject(err);
          });
        });
      }
      return Promise.resolve(envelopeResponse(ENVELOPE));
    });
    const first = h.wire.fetch({ url: "/p/", zone: "list" });
    const second = h.wire.fetch({ url: "/p/", zone: "list" });
    await Promise.all([first, second]);
    expect(h.dispatched.some((d) => d.event === "partial:error")).toBe(false);
  });
});

describe("Wire page threading", () => {
  it("threads the fetched url as the page of a safe zone GET", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({ url: "/host/?q=1", zone: "list" });
    expect(h.pages).toEqual(["/host/?q=1"]);
  });

  it("a mutation applies with no page, keeping the top-down resolve", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({
      url: "/_next/form/u1/",
      method: "POST",
      uid: "u1",
      zone: "z",
    });
    expect(h.pages).toEqual([undefined]);
  });

  it("an abortable validate POST applies with no page", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({
      url: "/f/",
      method: "POST",
      zone: "validate:u",
      abortable: true,
    });
    expect(h.pages).toEqual([undefined]);
  });

  it("a zone-less GET applies with no page", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({ url: "/list/" });
    expect(h.pages).toEqual([undefined]);
  });

  it("the parse-hook path threads the page alongside the foreign envelope", async () => {
    const h = makeWire(async () =>
      envelopeResponse("raw", { type: "text/vnd.next.stream+html" }),
    );
    h.wire.parseHook("text/vnd.next.stream+html", () => ({ version: "v1", ops: [] }));
    await h.wire.fetch({ url: "/list/", zone: "z" });
    expect(h.pages).toEqual(["/list/"]);
  });
});

describe("Wire abortable validation", () => {
  it("queues an abortable POST by zone without taking the mutation lock", async () => {
    const signals: AbortSignal[] = [];
    let resolveFirst!: (r: Response) => void;
    let n = 0;
    const h = makeWire((_url, init) => {
      signals.push(init.signal!);
      n += 1;
      if (n === 1) return new Promise<Response>((r) => (resolveFirst = r));
      return Promise.resolve(envelopeResponse(ENVELOPE));
    });
    const first = h.wire.fetch({
      url: "/f/",
      method: "POST",
      zone: "validate:u",
      abortable: true,
    });
    const second = h.wire.fetch({
      url: "/f/",
      method: "POST",
      zone: "validate:u",
      abortable: true,
    });
    expect(signals[0]!.aborted).toBe(true);
    resolveFirst(envelopeResponse(ENVELOPE));
    await Promise.all([first, second]);
    expect(h.envelopes).toHaveLength(1);
  });

  it("abort cancels the in-flight zone request and drops its late answer", async () => {
    let resolveFirst!: (r: Response) => void;
    const h = makeWire((_url, init) => {
      void init;
      return new Promise<Response>((r) => (resolveFirst = r));
    });
    const inflight = h.wire.fetch({
      url: "/f/",
      method: "POST",
      zone: "validate:u",
      abortable: true,
    });
    h.wire.abort("validate:u");
    resolveFirst(envelopeResponse(ENVELOPE));
    await inflight;
    expect(h.envelopes).toHaveLength(0);
  });

  it("a GET on a named queue is aborted by that bare key", async () => {
    let signal: AbortSignal | undefined;
    const h = makeWire((_url, init) => {
      signal = init.signal ?? undefined;
      return new Promise<Response>(() => undefined);
    });
    void h.wire.fetch({ url: "/photos/1/", zone: "photo", queue: "layer:1" });
    h.wire.abort("layer:1");
    expect(signal?.aborted).toBe(true);
  });

  it("a named queue on a locked mutation joins no queue", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({ url: "/f/", method: "POST", uid: "u", queue: "q" });
    expect(h.calls[0]!.init.signal).toBeUndefined();
  });

  it("a mutation needing a deferred token that never comes is held back", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE), {
      csrf: { header: "X-CSRFToken", url: "/_next/csrf/" },
      ensure: async () => ({ header: "X-CSRFToken", url: "/_next/csrf/" }),
    });
    await h.wire.fetch({ url: "/f/", method: "POST", uid: "u" });
    expect(h.calls).toEqual([]);
    expect(h.dispatched[0]).toMatchObject({
      event: "partial:error",
      detail: { kind: "csrf", url: "/f/" },
    });
  });

  it("a csrf chunk that failed to load is reported once, as the asset", async () => {
    document.head.innerHTML = "";
    const reported: Record<string, unknown>[] = [];
    const mint = lazyChunk<CsrfMint>(
      {
        dispatch: (_event, detail) => reported.push(detail),
        nonce: undefined,
        context: () => ({ $chunks: { csrf: "/static/next/next.csrf.min.js" } }),
      },
      "csrf",
    );
    const csrf = createCsrf({ mint });
    csrf.set({ header: "X-CSRFToken", url: "/_next/csrf/" });
    const h = makeWire(async () => envelopeResponse(ENVELOPE), {
      csrf: { header: "X-CSRFToken", url: "/_next/csrf/" },
      ensure: () => csrf.ensure(),
    });
    const sent = h.wire.fetch({ url: "/f/", method: "POST", uid: "u" });
    document.head.querySelector("script")!.dispatchEvent(new Event("error"));
    await sent;
    expect(h.calls).toEqual([]);
    expect(h.dispatched).toEqual([]);
    expect(reported).toEqual([expect.objectContaining({ kind: "asset" })]);
    // The lock is released, so the next submit tries again.
    void h.wire.fetch({ url: "/f/", method: "POST", uid: "u" });
    expect(document.head.querySelectorAll("script")).toHaveLength(1);
  });

  it("stamps the declared zone, not the queue key, on an abortable POST", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({
      url: "/f/",
      method: "POST",
      zone: "wizard",
      queue: "validate:u",
      abortable: true,
    });
    const headers = new Headers(h.calls[0]!.init.headers);
    expect(headers.get("X-Next-Zone")).toBe("wizard");
  });

  it("queues an abortable POST on its own key, not on the declared zone", async () => {
    const signals: AbortSignal[] = [];
    let resolveFirst!: (r: Response) => void;
    let n = 0;
    const h = makeWire((_url, init) => {
      signals.push(init.signal!);
      n += 1;
      if (n === 1) return new Promise<Response>((r) => (resolveFirst = r));
      return Promise.resolve(envelopeResponse(ENVELOPE));
    });
    const first = h.wire.fetch({
      url: "/f/",
      method: "POST",
      zone: "wizard",
      queue: "validate:u",
      abortable: true,
    });
    h.wire.abort("validate:u");
    resolveFirst(envelopeResponse(ENVELOPE));
    await first;
    expect(signals[0]!.aborted).toBe(true);
    expect(h.envelopes).toHaveLength(0);
  });

  it("abort on an idle zone is a no-op", () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    expect(() => h.wire.abort("nothing")).not.toThrow();
  });
});

describe("Wire queue pruning", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("drops the queue of a settled request", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({ url: "/photos/1/", zone: "photo", queue: "layer:1" });
    const abort = vi.spyOn(AbortController.prototype, "abort");
    h.wire._reset();
    expect(abort).not.toHaveBeenCalled();
    expect(h.calls[0]!.init.signal!.aborted).toBe(false);
  });

  it("drops the queue abort cancels", () => {
    const h = makeWire(() => new Promise<Response>(() => undefined));
    void h.wire.fetch({ url: "/photos/1/", zone: "photo", queue: "layer:1" });
    h.wire.abort("layer:1");
    const abort = vi.spyOn(AbortController.prototype, "abort");
    h.wire._reset();
    expect(abort).not.toHaveBeenCalled();
  });

  it("drops the queue of a request its token wait held back", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE), {
      csrf: { header: "X-CSRFToken", url: "/_next/csrf/" },
      ensure: async () => undefined,
    });
    await h.wire.fetch({ url: "/f/", method: "POST", queue: "q", abortable: true });
    const abort = vi.spyOn(AbortController.prototype, "abort");
    h.wire._reset();
    expect(abort).not.toHaveBeenCalled();
  });

  it("drops a late answer of an aborted request once its key is reused", async () => {
    const pending: ((r: Response) => void)[] = [];
    const h = makeWire(() => new Promise<Response>((r) => pending.push(r)));
    const first = h.wire.fetch({ url: "/f/", zone: "z", queue: "q" });
    h.wire.abort("q");
    const second = h.wire.fetch({ url: "/f/", zone: "z", queue: "q" });
    pending[0]!(envelopeResponse('{"version":"v1","ops":[{"op":"stale"}]}'));
    await first;
    pending[1]!(envelopeResponse(ENVELOPE));
    await second;
    expect(h.envelopes).toEqual([JSON.parse(ENVELOPE)]);
  });

  it("keeps the queue of a fresher request when a superseded one settles", async () => {
    const signals: AbortSignal[] = [];
    const pending: ((r: Response) => void)[] = [];
    const h = makeWire((_url, init) => {
      signals.push(init.signal!);
      return new Promise<Response>((r) => pending.push(r));
    });
    const first = h.wire.fetch({ url: "/f/", zone: "z", queue: "q" });
    void h.wire.fetch({ url: "/f/", zone: "z", queue: "q" });
    pending[0]!(envelopeResponse(ENVELOPE));
    await first;
    h.wire.abort("q");
    expect(signals[1]!.aborted).toBe(true);
  });
});

describe("Wire before-request", () => {
  it("emits partial:before-request with url, method, intent", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({ url: "/list/", zone: "z" });
    const ev = h.dispatched.find((d) => d.event === "partial:before-request");
    expect(ev!.detail.url).toBe("/list/");
    expect(ev!.detail.method).toBe("GET");
  });
});

describe("Wire echo request id", () => {
  function withRemember(
    responder: (url: string, init: RequestInit) => Promise<Response>,
  ) {
    const remembered: string[] = [];
    const calls: { init: RequestInit }[] = [];
    const wire = new Wire({
      fetch: (url, init) => {
        calls.push({ init });
        return responder(url, init);
      },
      navigate: () => undefined,
      dispatch: () => undefined,
      onEnvelope: async (raw) => ({ envelope: raw as Envelope, ok: true }),
      csrf: createCsrf({ mint: chunkModules.csrf }),
      rememberRequestId: (id) => remembered.push(id),
    });
    return { wire, remembered, calls };
  }

  function header(calls: { init: RequestInit }[], name: string): string | null {
    return new Headers(calls[0]!.init.headers).get(name);
  }

  it("stamps a request id on a mutation and reports it to the ring", async () => {
    const h = withRemember(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    const stamped = header(h.calls, HEADER_REQUEST_ID);
    expect(stamped).toBeDefined();
    expect(h.remembered).toEqual([stamped]);
  });

  it("does not stamp a request id on a safe GET", async () => {
    const h = withRemember(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({ url: "/list/", zone: "z" });
    expect(header(h.calls, HEADER_REQUEST_ID)).toBeNull();
    expect(h.remembered).toEqual([]);
  });

  it("leaves an abortable validate POST out of the echo ring", async () => {
    const h = withRemember(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({
      url: "/_next/form/u1/",
      method: "POST",
      zone: "z",
      abortable: true,
    });
    expect(header(h.calls, HEADER_REQUEST_ID)).toBeNull();
    expect(h.remembered).toEqual([]);
  });

  it("falls back to a timestamp id when crypto.randomUUID is absent", async () => {
    const original = globalThis.crypto;
    Object.defineProperty(globalThis, "crypto", {
      value: { randomUUID: undefined },
      configurable: true,
    });
    try {
      const h = withRemember(async () => envelopeResponse(ENVELOPE));
      await h.wire.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
      const stamped = header(h.calls, HEADER_REQUEST_ID);
      expect(stamped).toMatch(/^\d+-[a-z0-9]+$/);
      expect(h.remembered).toEqual([stamped]);
    } finally {
      Object.defineProperty(globalThis, "crypto", {
        value: original,
        configurable: true,
      });
    }
  });

  it("keeps a caller-supplied request id over a fresh one", async () => {
    const h = withRemember(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({
      url: "/_next/form/u1/",
      method: "POST",
      uid: "u1",
      headers: { [HEADER_REQUEST_ID]: "given" },
    });
    expect(header(h.calls, HEADER_REQUEST_ID)).toBe("given");
    expect(h.remembered).toEqual([]);
  });

  it("sees a caller id written in another case as the same header", async () => {
    const h = withRemember(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({
      url: "/_next/form/u1/",
      method: "POST",
      uid: "u1",
      headers: { "x-next-request-id": "given" },
    });
    const sent = new Headers(h.calls[0]!.init.headers);
    expect(Array.from(sent).filter(([name]) => name === "x-next-request-id")).toEqual([
      ["x-next-request-id", "given"],
    ]);
    expect(h.remembered).toEqual([]);
  });
});

describe("Wire reset", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("aborts in-flight queues and clears state on _reset", async () => {
    const h = makeWire(() => new Promise<Response>(() => {}));
    void h.wire.fetch({ url: "/p1/", zone: "list" });
    h.wire._reset();
    const h2 = makeWire(async () => envelopeResponse(ENVELOPE));
    await h2.wire.fetch({ url: "/p2/", zone: "list" });
    expect(h2.envelopes).toHaveLength(1);
  });
});

describe("Wire same-origin targets", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("GETs a page served under a double-slash path on its own origin", async () => {
    window.history.replaceState(
      null,
      "",
      `${location.origin}//attacker.example/x/?q=1`,
    );
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    const page = currentUrl(document);
    expect(page).toBe("//attacker.example/x/?q=1");
    await h.wire.fetch({ url: page, zone: "lazy" });
    expect(h.calls).toHaveLength(1);
    expect(h.calls[0]!.url).toBe(`${location.origin}//attacker.example/x/?q=1`);
    expect(new URL(h.calls[0]!.url).origin).toBe(location.origin);
    expect(h.calls[0]!.init.mode).toBe("same-origin");
    expect(h.pages).toEqual(["//attacker.example/x/?q=1"]);
  });

  it("navigates a double-slash page on its own origin when the response url is empty", async () => {
    window.history.replaceState(null, "", `${location.origin}//attacker.example/x/`);
    const h = makeWire(async () =>
      envelopeResponse("<html></html>", { type: "text/html", url: "" }),
    );
    await h.wire.fetch({ url: currentUrl(document), zone: "z" });
    expect(h.navigated).toEqual([`${location.origin}//attacker.example/x/`]);
  });

  it("sends a mutation same-origin too", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({ url: "/_next/form/u/", method: "POST", uid: "u" });
    expect(h.calls[0]!.url).toBe(`${location.origin}/_next/form/u/`);
    expect(h.calls[0]!.init.mode).toBe("same-origin");
  });

  it("resolves a relative url against the document base", async () => {
    window.history.replaceState(null, "", `${location.origin}//attacker.example/x/`);
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({ url: "?page=2", zone: "list" });
    expect(h.calls[0]!.url).toBe(`${location.origin}//attacker.example/x/?page=2`);
  });

  it("keeps a backslash path on the page's origin", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({ url: "/\\attacker.example/x/", zone: "z" });
    expect(new URL(h.calls[0]!.url).origin).toBe(location.origin);
  });

  it.each([
    "https://attacker.example/x/",
    "\\\\attacker.example/x/",
    " //attacker.example/x/",
    "http://[",
  ])("refuses %j without a request", async (url) => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({ url, method: "POST", uid: "u", zone: "z" });
    expect(h.calls).toHaveLength(0);
    expect(h.envelopes).toHaveLength(0);
    const err = h.dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.kind).toBe("network");
    expect(err!.detail.url).toBe(url);
    expect(h.dispatched.some((d) => d.event === "partial:before-request")).toBe(false);
  });

  it("leaves the in-flight GET of a queue alive when a refused one names it", async () => {
    let release: (response: Response) => void = () => undefined;
    const h = makeWire(
      () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    );
    const first = h.wire.fetch({ url: "/list/", zone: "list" });
    await h.wire.fetch({ url: "https://attacker.example/list/", zone: "list" });
    release(envelopeResponse(ENVELOPE));
    await first;
    expect(h.calls).toHaveLength(1);
    expect(h.calls[0]!.init.signal!.aborted).toBe(false);
    expect(h.envelopes).toHaveLength(1);
  });
});

describe("Wire WebSocket tunnel", () => {
  const post: WireRequest = {
    url: "/_next/form/u1/",
    method: "POST",
    uid: "u1",
    transport: "any",
    body: "a=1",
  };

  it("sends a marked POST through the socket instead of fetch", async () => {
    const tunnel = vi.fn<NonNullable<WireDeps["tunnel"]>>(async () =>
      envelopeResponse(ENVELOPE),
    );
    const h = makeWire(async () => envelopeResponse(ENVELOPE), {
      tunnel,
      csrf: { header: "X-CSRFToken", token: "tok" },
    });
    await h.wire.fetch(post);
    expect(h.calls).toHaveLength(0);
    expect(h.envelopes).toHaveLength(1);
    const [target, init, header] = tunnel.mock.calls[0]!;
    expect(target).toBe(`${location.origin}/_next/form/u1/`);
    expect(header).toBe("X-CSRFToken");
    expect(init.method).toBe("POST");
    expect(init.body).toBe("a=1");
    const headers = new Headers(init.headers);
    expect(headers.get("X-CSRFToken")).toBe("tok");
    expect(headers.get(HEADER_REQUEST_ID)).toMatch(/.+/);
  });

  it.each([
    ["an envelope", () => envelopeResponse(ENVELOPE)],
    ["a refusal", () => envelopeResponse("denied", { status: 403, type: "text/html" })],
    [
      "a redirect",
      () => envelopeResponse("<p>", { type: "text/html", redirected: true }),
    ],
    ["a server error", () => envelopeResponse("boom", { status: 500 })],
    ["a malformed envelope", () => envelopeResponse("{")],
  ])("applies %s from the socket as it applies the fetched one", async (_, reply) => {
    const fetched = makeWire(async () => reply());
    const tunneled = makeWire(async () => reply(), { tunnel: async () => reply() });
    await fetched.wire.fetch({ ...post, transport: "http" });
    await tunneled.wire.fetch(post);
    expect(tunneled.calls).toHaveLength(0);
    expect(tunneled.envelopes).toEqual(fetched.envelopes);
    expect(tunneled.navigated).toEqual(fetched.navigated);
    expect(tunneled.dispatched).toEqual(fetched.dispatched);
  });

  const unmarked: WireRequest = { url: post.url, method: "POST", body: "a=1" };
  it.each<[string, WireRequest]>([
    ["an unmarked POST", unmarked],
    ["a marked GET", { url: "/list/", transport: "any" }],
    ["a POST marked for HTTP", { ...post, transport: "http" }],
  ])("keeps %s on fetch", async (_, request) => {
    const tunnel = vi.fn(async () => envelopeResponse(ENVELOPE));
    const h = makeWire(async () => envelopeResponse(ENVELOPE), { tunnel });
    await h.wire.fetch(request);
    expect(tunnel).not.toHaveBeenCalled();
    expect(h.calls).toHaveLength(1);
  });

  it("fetches a marked POST when no socket transport is wired", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch(post);
    expect(h.calls).toHaveLength(1);
  });

  it("falls back to fetch when the request cannot ride the socket", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE), {
      tunnel: () => undefined,
    });
    await h.wire.fetch(post);
    expect(h.calls).toHaveLength(1);
    expect(h.envelopes).toHaveLength(1);
  });

  it("reports a mutation whose socket closed before the reply and does not resend", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE), {
      tunnel: async () => "closed",
    });
    await h.wire.fetch(post);
    expect(h.calls).toHaveLength(0);
    expect(h.dispatched.filter((d) => d.event === "partial:error")).toEqual([
      {
        event: "partial:error",
        detail: { kind: "undelivered", url: post.url, reason: "closed" },
      },
    ]);
    await h.wire.fetch(post);
    expect(
      h.dispatched.filter((d) => d.event === "partial:before-request"),
    ).toHaveLength(2);
  });

  it("resends a validation whose reply never arrived over fetch", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE), {
      tunnel: async () => "http_only",
    });
    await h.wire.fetch({
      url: post.url,
      method: "POST",
      transport: "any",
      abortable: true,
      queue: "v",
    });
    expect(h.calls).toHaveLength(1);
    expect(h.envelopes).toHaveLength(1);
  });

  it("reloads the page after reporting a reply the server kept to HTTP", async () => {
    const seen: string[][] = [];
    const reload = vi.fn(() => seen.push(h.dispatched.map((d) => d.event)));
    const page = {
      location: { origin: document.location.origin, reload },
      baseURI: document.baseURI,
    } as unknown as Document;
    const h = makeWire(async () => envelopeResponse(ENVELOPE), {
      tunnel: async () => "http_only",
      document: page,
    });
    await h.wire.fetch(post);
    expect(h.calls).toHaveLength(0);
    expect(h.envelopes).toHaveLength(0);
    expect(h.dispatched.filter((d) => d.event === "partial:error")).toEqual([
      {
        event: "partial:error",
        detail: { kind: "undelivered", url: post.url, reason: "http_only" },
      },
    ]);
    expect(seen).toEqual([["partial:before-request", "partial:error"]]);
  });

  it("keeps the page for a reply lost with the socket or to its timeout", async () => {
    const reload = vi.fn();
    const page = {
      location: { origin: document.location.origin, reload },
      baseURI: document.baseURI,
    } as unknown as Document;
    for (const reason of ["closed", "timeout", "limit"]) {
      const h = makeWire(async () => envelopeResponse(ENVELOPE), {
        tunnel: async () => reason,
        document: page,
      });
      await h.wire.fetch(post);
      expect(h.calls).toHaveLength(0);
    }
    expect(reload).not.toHaveBeenCalled();
  });

  it("reports a malformed socket reply as a network error", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE), {
      tunnel: () => Promise.reject(new TypeError("cross-origin redirect refused")),
    });
    await h.wire.fetch(post);
    expect(h.calls).toHaveLength(0);
    expect(h.dispatched.filter((d) => d.event === "partial:error")).toEqual([
      {
        event: "partial:error",
        detail: { kind: "network", error: expect.any(TypeError) },
      },
    ]);
  });

  it("resends a request the server did not run over fetch exactly once", async () => {
    const tunnel = vi.fn<NonNullable<WireDeps["tunnel"]>>(async () => undefined);
    const h = makeWire(async () => envelopeResponse(ENVELOPE), { tunnel });
    await h.wire.fetch(post);
    expect(tunnel).toHaveBeenCalledOnce();
    expect(h.calls).toHaveLength(1);
    expect(new Headers(h.calls[0]!.init.headers).get(HEADER_REQUEST_ID)).toBe(
      new Headers(tunnel.mock.calls[0]![1].headers).get(HEADER_REQUEST_ID),
    );
    expect(h.envelopes).toHaveLength(1);
  });

  it("drops a refusal of a validation that a newer one superseded", async () => {
    let refuse: (error: Error) => void = () => undefined;
    const h = makeWire(async () => envelopeResponse(ENVELOPE), {
      tunnel: () =>
        new Promise((_, reject) => {
          refuse = reject;
        }),
    });
    const validation: WireRequest = {
      url: post.url,
      method: "POST",
      transport: "any",
      abortable: true,
      queue: "v",
    };
    const first = h.wire.fetch(validation);
    await Promise.resolve();
    h.wire.abort("v");
    refuse(new Error("tunnel refused the request: http_only"));
    await first;
    expect(h.dispatched.filter((d) => d.event === "partial:error")).toEqual([]);
  });
});
