import { beforeEach, describe, expect, it } from "vitest";
import { Wire, CONTENT_TYPE, REQUEST_FLAG, HEADER_REQUEST_ID } from "./wire";
import { ACCEPT } from "./apply";

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
  opts: { version?: string; csrf?: { header: string; token: string } } = {},
) {
  const dispatched: { event: string; detail: Record<string, unknown> }[] = [];
  const navigated: string[] = [];
  const envelopes: unknown[] = [];
  const calls: { url: string; init: RequestInit }[] = [];
  const wire = new Wire({
    fetch: (url, init) => {
      calls.push({ url, init });
      return responder(url, init);
    },
    navigate: (url) => navigated.push(url),
    dispatch: (event, detail) => dispatched.push({ event, detail }),
    onEnvelope: (raw) => envelopes.push(raw),
    version: () => opts.version ?? "v1",
    csrf: () => opts.csrf,
  });
  return { wire, dispatched, navigated, envelopes, calls };
}

const ENVELOPE = '{"version":"v1","ops":[],"assets":[],"defer":[],"form":null}';

describe("Wire headers", () => {
  it("sends the intent headers on a GET", async () => {
    const h: Harness = makeWire(async () => envelopeResponse(ENVELOPE));
    await h.wire.fetch({ url: "/list/", zone: "request-list" });
    const headers = h.calls[0].init.headers as Record<string, string>;
    expect(headers[REQUEST_FLAG]).toBe("1");
    expect(headers.Accept).toBe(ACCEPT);
    expect(headers["X-Next-Version"]).toBe("v1");
    expect(headers["X-Next-Zone"]).toBe("request-list");
  });

  it("omits the version header before the client has learned one", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE), { version: "" });
    await h.wire.fetch({ url: "/list/", zone: "request-list" });
    const headers = h.calls[0].init.headers as Record<string, string>;
    expect("X-Next-Version" in headers).toBe(false);
  });

  it("adds the CSRF header from the payload on unsafe methods", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE), {
      csrf: { header: "X-CSRFToken", token: "tok" },
    });
    await h.wire.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    const headers = h.calls[0].init.headers as Record<string, string>;
    expect(headers["X-CSRFToken"]).toBe("tok");
  });

  it("omits the CSRF header on safe methods", async () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE), {
      csrf: { header: "X-CSRFToken", token: "tok" },
    });
    await h.wire.fetch({ url: "/list/", zone: "z" });
    const headers = h.calls[0].init.headers as Record<string, string>;
    expect(headers["X-CSRFToken"]).toBeUndefined();
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

  it("navigates on 409 on a safe method", async () => {
    const h = makeWire(async () =>
      envelopeResponse("", { status: 409, type: "text/plain", url: "/here/" }),
    );
    await h.wire.fetch({ url: "/here/", zone: "z" });
    expect(h.navigated).toEqual(["/here/"]);
  });

  it("emits partial:error on 5xx with the body", async () => {
    const h = makeWire(async () =>
      envelopeResponse("boom", { status: 500, type: "text/plain" }),
    );
    await h.wire.fetch({ url: "/list/", zone: "z" });
    const err = h.dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.status).toBe(500);
    expect(err!.detail.body).toBe("boom");
  });

  it("emits partial:error when the fetch itself rejects", async () => {
    const h = makeWire(async () => {
      throw new TypeError("network down");
    });
    await h.wire.fetch({ url: "/list/", zone: "z" });
    const err = h.dispatched.find((d) => d.event === "partial:error");
    expect(err!.detail.status).toBe(0);
    expect(err!.detail.error).toBeInstanceOf(TypeError);
  });

  it("emits partial:error on malformed JSON", async () => {
    const h = makeWire(async () => envelopeResponse("{not json"));
    await h.wire.fetch({ url: "/list/", zone: "z" });
    expect(h.dispatched.some((d) => d.event === "partial:error")).toBe(true);
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

describe("Wire safe-GET queue", () => {
  it("aborts the in-flight GET of the same zone (latest-wins)", async () => {
    const signals: AbortSignal[] = [];
    let resolveFirst!: (r: Response) => void;
    let n = 0;
    const h = makeWire((_url, init) => {
      signals.push(init.signal!);
      n += 1;
      if (n === 1) return new Promise<Response>((r) => (resolveFirst = r));
      return Promise.resolve(envelopeResponse(ENVELOPE));
    });
    const first = h.wire.fetch({ url: "/p1/", zone: "list" });
    const second = h.wire.fetch({ url: "/p2/", zone: "list" });
    expect(signals[0].aborted).toBe(true);
    resolveFirst(envelopeResponse(ENVELOPE));
    await Promise.all([first, second]);
    // Only the latest response is applied, the stale one is dropped.
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
    const first = h.wire.fetch({ url: "/p1/", zone: "list" });
    const second = h.wire.fetch({ url: "/p2/", zone: "list" });
    await Promise.all([first, second]);
    expect(h.dispatched.some((d) => d.event === "partial:error")).toBe(false);
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
    expect(signals[0].aborted).toBe(true);
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

  it("abort on an idle zone is a no-op", () => {
    const h = makeWire(async () => envelopeResponse(ENVELOPE));
    expect(() => h.wire.abort("nothing")).not.toThrow();
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
      onEnvelope: () => undefined,
      rememberRequestId: (id) => remembered.push(id),
    });
    return { wire, remembered, calls };
  }

  function header(calls: { init: RequestInit }[], name: string): string | undefined {
    return (calls[0].init.headers as Record<string, string>)[name];
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
    expect(header(h.calls, HEADER_REQUEST_ID)).toBeUndefined();
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
    expect(header(h.calls, HEADER_REQUEST_ID)).toBeUndefined();
    expect(h.remembered).toEqual([]);
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
});

describe("Wire reset", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("aborts in-flight queues and clears state on _reset", async () => {
    const h = makeWire(() => new Promise<Response>(() => {}));
    void h.wire.fetch({ url: "/p1/", zone: "list" });
    h.wire._reset();
    // After reset the queue key is free, a fresh GET starts at seq 1.
    const h2 = makeWire(async () => envelopeResponse(ENVELOPE));
    await h2.wire.fetch({ url: "/p2/", zone: "list" });
    expect(h2.envelopes).toHaveLength(1);
  });
});
