import { afterEach, describe, expect, it, vi } from "vitest";
import type { StreamMeta, Transport, TransportSink } from "./stream";
import { createWebSocketTransport, csrfSecret } from "./ws";
import type { WebSocketAdapter } from "./ws";

function harness() {
  const calls: { url: string; sink: TransportSink }[] = [];
  const fallback: Transport = {
    name: "sse",
    open(url, sink) {
      calls.push({ url, sink });
      sink.opened();
      return { close: vi.fn() };
    },
  };
  const fetch =
    vi.fn<(...args: Parameters<typeof globalThis.fetch>) => Promise<Response>>();
  const sockets: {
    url: string;
    open(): void;
    message(body: unknown): void;
    close(code: number): void;
    refuse(code: number, reason?: string): void;
    sent: string[];
    control: { close: ReturnType<typeof vi.fn> };
  }[] = [];
  const socket: WebSocketAdapter = {
    open(url, onOpen, onMessage, onClose) {
      const item = {
        url,
        open: () => onOpen((body) => item.sent.push(body)),
        message: onMessage,
        close: onClose,
        refuse: (code: number, reason = "auth") => {
          onMessage(JSON.stringify({ type: "error", code: reason }));
          onClose(code);
        },
        sent: [] as string[],
        control: { close: vi.fn() },
      };
      sockets.push(item);
      return item.control;
    },
  };
  const sink: TransportSink = {
    patches: vi.fn(),
    hello: vi.fn(),
    skip: vi.fn(),
    clock: vi.fn(),
    end: vi.fn(),
    opened: vi.fn(),
    closed: vi.fn(),
  };
  return { calls, fallback, fetch, sockets, socket, sink };
}

async function settle(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function grant(url = "/_next/ws/") {
  return Response.json({
    protocol: 2,
    transport: "ws",
    ticket: "signed-ticket",
    url,
    expires_in: 60,
  });
}

const MASKED = [
  "fw062o3AibVzOUYtJtC0uP0MIDbY3wEs5fxT6MItQLn5l3tNjTQBXB3Rs6p5X4VE",
  "QcrsoFV0mQwlcQhSwkUZBjSeSzOdaxTwGVYfs3ATUqYRJZMc6K8A45VjC22k45aI",
] as const;
const OTHER = "6Nu4CeRFMWSQZKRAnWfC6hw3MxglXkfR8xtBTuI2ShL2AkVzb319ii6nTbq84Gg4";

afterEach(() => vi.useRealTimers());

describe("WebSocket stream transport", () => {
  it("negotiates over the guarded URL and forwards protocol frames", async () => {
    const made = harness();
    made.fetch.mockImplementation(async () => grant());
    const transport = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    });
    transport.open("/poll/stream/", made.sink, new Map([["poll:4", ["a", 9]]]));
    await settle();

    expect(made.fetch).toHaveBeenCalledWith(
      "/poll/stream/",
      expect.objectContaining({
        method: "GET",
        credentials: "same-origin",
        headers: {
          "X-Next-Transport": "ws",
          Accept: "application/json",
        },
      }),
    );
    expect(made.sockets[0]!.url).toBe("wss://example.test/_next/ws/");
    made.sockets[0]!.open();
    expect(JSON.parse(made.sockets[0]!.sent[0]!)).toEqual({
      type: "sub",
      ticket: "signed-ticket",
      last_seq: { "poll:4": { epoch: "a", seq: 9 } },
    });
    expect(made.sink.opened).toHaveBeenCalledOnce();
    const hello = {
      type: "hello",
      topics: { "poll:4": { epoch: "a", seq: 9 } },
      server_time: 1000,
    };
    made.sockets[0]!.message(JSON.stringify(hello));
    made.sockets[0]!.message(
      JSON.stringify({ type: "patches", data: 'seq=10 {"not": json' }),
    );
    made.sockets[0]!.message(
      JSON.stringify({ type: "skip", topic: "poll:4", epoch: "a", seq: 11 }),
    );
    made.sockets[0]!.message(JSON.stringify({ type: "ping", server_time: 1200 }));
    made.sockets[0]!.message(JSON.stringify({ type: "ping" }));
    made.sockets[0]!.message(JSON.stringify({ type: "unknown", data: {} }));
    made.sockets[0]!.message(JSON.stringify({ type: "patches" }));
    made.sockets[0]!.message(JSON.stringify({ type: "patches", data: { seq: 12 } }));
    made.sockets[0]!.message(JSON.stringify({ type: "patches", data: [1] }));
    made.sockets[0]!.message({ type: "patches" });
    expect(made.sink.hello).toHaveBeenCalledExactlyOnceWith(JSON.stringify(hello));
    expect(made.sink.patches).toHaveBeenNthCalledWith(1, 'seq=10 {"not": json');
    expect(made.sink.patches).toHaveBeenNthCalledWith(2, { seq: 12 });
    expect(made.sink.patches).toHaveBeenCalledTimes(2);
    expect(made.sink.skip).toHaveBeenCalledWith(
      '{"type":"skip","topic":"poll:4","epoch":"a","seq":11}',
    );
    expect(made.sink.clock).toHaveBeenNthCalledWith(1, 1200);
    expect(made.sink.clock).toHaveBeenNthCalledWith(2, undefined);
    expect(made.sockets[0]!.sent.slice(1)).toEqual([
      '{"type":"pong"}',
      '{"type":"pong"}',
    ]);
    expect(made.calls).toHaveLength(0);
  });

  it.each([401, 403, 406])(
    "falls back immediately for negotiation status %i",
    async (status) => {
      const made = harness();
      made.fetch.mockResolvedValue(new Response("", { status }));
      createWebSocketTransport({
        fallback: made.fallback,
        fetch: made.fetch,
        socket: made.socket,
        origin: "https://example.test",
      }).open("/stream/", made.sink);
      await settle();
      expect(made.calls).toHaveLength(1);
      expect(made.sockets).toHaveLength(0);
    },
  );

  it("falls back after three failed negotiations with jittered retries", async () => {
    vi.useFakeTimers();
    const made = harness();
    made.fetch.mockRejectedValue(new Error("network down"));
    const opened = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
      random: () => 0.5,
    }).open("/stream/", made.sink);
    await settle();
    await vi.advanceTimersByTimeAsync(225);
    await vi.advanceTimersByTimeAsync(375);
    expect(made.fetch).toHaveBeenCalledTimes(3);
    expect(made.calls).toHaveLength(1);
    opened.close();
    expect(made.sink.closed).toHaveBeenCalledTimes(3);
    expect(made.sink.closed).toHaveBeenCalledWith("transient");
    expect(made.sink.closed).not.toHaveBeenCalledWith("fatal");
  });

  it("uses Math.random when no jitter seam is supplied", async () => {
    vi.useFakeTimers();
    const made = harness();
    const random = vi.spyOn(Math, "random").mockReturnValue(0);
    made.fetch.mockRejectedValue(new Error("network down"));
    createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    }).open("/stream/", made.sink);
    await settle();
    await vi.advanceTimersByTimeAsync(150);
    await settle();
    await vi.advanceTimersByTimeAsync(300);
    await settle();
    expect(made.fetch).toHaveBeenCalledTimes(3);
    expect(made.calls).toHaveLength(1);
    random.mockRestore();
  });

  it("reconnects after transient socket close and falls back after auth close", async () => {
    vi.useFakeTimers();
    const made = harness();
    made.fetch.mockImplementation(async () => grant());
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
      random: () => 0,
    }).open("/stream/", made.sink);
    await settle();
    made.sockets[0]!.close(1006);
    await vi.advanceTimersByTimeAsync(151);
    await settle();
    expect(made.sockets).toHaveLength(2);
    made.sockets[1]!.refuse(4403);
    expect(made.calls).toHaveLength(1);
    expect(made.fetch).toHaveBeenCalledTimes(2);
    control.close();
  });

  it("renegotiates once on 4401 and then falls back on a repeated 4401", async () => {
    const made = harness();
    made.fetch.mockImplementation(async () => grant());
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    }).open("/stream/", made.sink);
    await settle();
    made.sockets[0]!.refuse(4401);
    await settle();
    expect(made.fetch).toHaveBeenCalledTimes(2);
    expect(made.sockets).toHaveLength(2);
    expect(made.sockets[0]!.control.close).toHaveBeenCalledOnce();
    expect(made.calls).toHaveLength(0);
    made.sockets[1]!.refuse(4401);
    expect(made.calls).toHaveLength(1);
    expect(made.fetch).toHaveBeenCalledTimes(2);
    control.close();
  });

  it("allows one more renegotiation on 4401 after a hello", async () => {
    const made = harness();
    made.fetch.mockImplementation(async () => grant());
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    }).open("/stream/", made.sink);
    await settle();
    made.sockets[0]!.open();
    made.sockets[0]!.message(JSON.stringify({ type: "hello", topics: {} }));
    made.sockets[0]!.refuse(4401);
    await settle();
    expect(made.sockets).toHaveLength(2);
    made.sockets[1]!.open();
    made.sockets[1]!.message(JSON.stringify({ type: "hello", topics: {} }));
    made.sockets[1]!.refuse(4401);
    await settle();
    expect(made.sockets).toHaveLength(3);
    expect(made.calls).toHaveLength(0);
    made.sockets[2]!.refuse(4401);
    expect(made.calls).toHaveLength(1);
    expect(made.fetch).toHaveBeenCalledTimes(3);
    control.close();
  });

  it("falls back when the grant points off origin or the socket closes with 4403", async () => {
    const offOrigin = harness();
    offOrigin.fetch.mockResolvedValue(grant("https://attacker.test/ws/"));
    createWebSocketTransport({
      fallback: offOrigin.fallback,
      fetch: offOrigin.fetch,
      socket: offOrigin.socket,
      origin: "https://example.test",
    }).open("/stream/", offOrigin.sink);
    await settle();
    expect(offOrigin.calls).toHaveLength(1);

    const auth = harness();
    auth.fetch.mockResolvedValue(grant());
    createWebSocketTransport({
      fallback: auth.fallback,
      fetch: auth.fetch,
      socket: auth.socket,
      origin: "https://example.test",
    }).open("/stream/", auth.sink);
    await settle();
    auth.sockets[0]!.message(JSON.stringify({ type: "error", code: "auth" }));
    expect(auth.sockets[0]!.control.close).not.toHaveBeenCalled();
    expect(auth.calls).toHaveLength(0);
    auth.sockets[0]!.close(4403);
    expect(auth.sockets[0]!.control.close).toHaveBeenCalledOnce();
    expect(auth.calls).toHaveLength(1);
    expect(auth.fetch).toHaveBeenCalledOnce();
    auth.sockets[0]!.refuse(1006);
    expect(auth.calls).toHaveLength(1);
  });

  it("keeps a stream URL on SSE after a 4403 when it reopens", async () => {
    const made = harness();
    made.fetch.mockImplementation(async () => grant());
    const transport = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    });
    const first = transport.open("/stream/", made.sink);
    await settle();
    made.sockets[0]!.open();
    made.sockets[0]!.refuse(4403, "denied");
    expect(made.calls.map((call) => call.url)).toEqual(["/stream/"]);
    first.close();
    transport.open("/stream/", made.sink);
    transport.open("/other/", made.sink);
    await settle();
    expect(made.calls.map((call) => call.url)).toEqual(["/stream/", "/stream/"]);
    expect(made.fetch.mock.calls.map(([url]) => url)).toEqual(["/stream/", "/other/"]);
    expect(made.sink.end).not.toHaveBeenCalled();
  });

  it("names the transport that actually carries the stream", async () => {
    const made = harness();
    made.fetch.mockImplementation(async () => grant());
    const transport = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    });
    const first = transport.open("/stream/", made.sink);
    expect(first.transport).toBe("ws");
    await settle();
    made.sockets[0]!.open();
    made.sockets[0]!.refuse(4403, "denied");
    expect(first.transport).toBe("sse");
    first.close();
    const denied = transport.open("/stream/", made.sink);
    expect(denied.transport).toBe("sse");
    denied.close();
    expect(made.calls).toHaveLength(2);
  });

  it("reconnects after a 1013 close and counts it as a failed attempt", async () => {
    vi.useFakeTimers();
    const made = harness();
    made.fetch.mockImplementation(async () => grant());
    createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
      random: () => 0,
    }).open("/stream/", made.sink);
    for (let attempt = 0; attempt < 3; attempt += 1) {
      await vi.advanceTimersByTimeAsync(1000);
      made.sockets[attempt]!.open();
      made.sockets[attempt]!.close(1013);
    }
    expect(made.sink.closed).toHaveBeenCalledWith("transient");
    expect(made.fetch).toHaveBeenCalledTimes(3);
    expect(made.calls).toHaveLength(1);
  });

  it("falls back for malformed grants and invalid URL values", async () => {
    const malformed = harness();
    malformed.fetch.mockResolvedValue(
      Response.json({ protocol: 1, transport: "ws", ticket: "ticket", url: "/ws/" }),
    );
    createWebSocketTransport({
      fallback: malformed.fallback,
      fetch: malformed.fetch,
      socket: malformed.socket,
      origin: "https://example.test",
    }).open("/stream/", malformed.sink);
    await settle();
    expect(malformed.calls).toHaveLength(1);

    const invalidUrl = harness();
    invalidUrl.fetch.mockResolvedValue(grant("http://["));
    createWebSocketTransport({
      fallback: invalidUrl.fallback,
      fetch: invalidUrl.fetch,
      socket: invalidUrl.socket,
      origin: "https://example.test",
    }).open("/stream/", invalidUrl.sink);
    await settle();
    expect(invalidUrl.calls).toHaveLength(1);
  });

  it("retries a negotiation aborted by something other than the transport", async () => {
    vi.useFakeTimers();
    const made = harness();
    made.fetch.mockRejectedValue(new DOMException("aborted", "AbortError"));
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
      random: () => 0,
    }).open("/stream/", made.sink);
    await settle();
    expect(made.sink.closed).toHaveBeenCalledExactlyOnceWith("transient");
    await vi.advanceTimersByTimeAsync(150);
    expect(made.fetch).toHaveBeenCalledTimes(2);
    expect(made.calls).toHaveLength(0);
    control.close();
  });

  it("ignores a negotiation failure after the transport has closed", async () => {
    const made = harness();
    let rejectFetch: (reason: Error) => void = () => undefined;
    made.fetch.mockImplementation(
      () =>
        new Promise<Response>((_resolve, reject) => {
          rejectFetch = reject;
        }),
    );
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    }).open("/stream/", made.sink);
    control.close();
    rejectFetch(new Error("late network error"));
    await settle();
    expect(made.calls).toHaveLength(0);
    expect(made.fetch).toHaveBeenCalledOnce();
  });

  it("falls back on a malformed frame and closes the fallback", async () => {
    const made = harness();
    made.fetch.mockResolvedValue(grant());
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    }).open("/stream/", made.sink);
    await settle();
    made.sockets[0]!.message("not-json");
    expect(made.sockets[0]!.control.close).toHaveBeenCalledOnce();
    expect(made.calls).toHaveLength(1);
    control.close();
    expect(made.fetch).toHaveBeenCalledOnce();
    expect(made.calls[0]!.sink.opened).toHaveBeenCalledOnce();
  });

  it("uses the browser fetch and socket adapters by default", async () => {
    class FakeWebSocket {
      static latest: FakeWebSocket | undefined;
      onopen: (() => void) | null = null;
      onmessage: ((event: { data: unknown }) => void) | null = null;
      onclose: ((event: { code: number }) => void) | null = null;
      onerror: (() => void) | null = null;
      send = vi.fn();
      close = vi.fn();
      constructor(readonly url: string) {
        FakeWebSocket.latest = this;
      }
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(grant());
    const transport = createWebSocketTransport({
      fallback: harness().fallback,
    });
    const control = transport.open("/stream/", harness().sink);
    await settle();
    expect(fetch).toHaveBeenCalledOnce();
    expect(FakeWebSocket.latest?.url).toBe(
      `${globalThis.location.protocol === "https:" ? "wss:" : "ws:"}//${globalThis.location.host}/_next/ws/`,
    );
    FakeWebSocket.latest?.onopen?.();
    control.close();
    fetch.mockRestore();
    vi.unstubAllGlobals();
  });

  it("accepts an HTTP grant and tolerates duplicate closes", async () => {
    const made = harness();
    made.fetch.mockResolvedValue(grant());
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "http://example.test",
    }).open("/stream/", made.sink);
    await settle();
    expect(made.sockets[0]!.url).toBe("ws://example.test/_next/ws/");
    control.close();
    control.close();
    made.sockets[0]!.open();
    expect(made.sockets[0]!.sent).toEqual([]);
    expect(made.sink.opened).not.toHaveBeenCalled();
  });

  it("does not open a socket when a pending grant resolves after close", async () => {
    const made = harness();
    let resolveJson: (value: unknown) => void = () => undefined;
    made.fetch.mockResolvedValue(
      new Response(null, {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const json = vi
      .spyOn(Response.prototype, "json")
      .mockImplementation(() => new Promise((resolve) => (resolveJson = resolve)));
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    }).open("/stream/", made.sink);
    await settle();
    control.close();
    resolveJson({
      protocol: 2,
      transport: "ws",
      ticket: "ticket",
      url: "/_next/ws/",
    });
    await settle();
    expect(made.sockets).toHaveLength(0);
    json.mockRestore();
  });

  it("ignores late socket callbacks and non-record frames", async () => {
    const made = harness();
    made.fetch.mockResolvedValue(grant());
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    }).open("/stream/", made.sink);
    await settle();
    made.sockets[0]!.message("null");
    made.sockets[0]!.message(
      JSON.stringify({ type: "error", data: { code: "other" } }),
    );
    control.close();
    made.sockets[0]!.open();
    expect(made.sockets[0]!.sent).toEqual([]);
    expect(made.sink.opened).not.toHaveBeenCalled();
  });

  it("does not retry a scheduled connection after close", async () => {
    vi.useFakeTimers();
    const made = harness();
    made.fetch.mockRejectedValue(new Error("network down"));
    let retry: (() => void) | undefined;
    const timer = vi.spyOn(globalThis, "setTimeout").mockImplementation((handler) => {
      retry = handler;
      return 1 as unknown as ReturnType<typeof setTimeout>;
    });
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
      random: () => 0,
    }).open("/stream/", made.sink);
    await settle();
    control.close();
    retry?.();
    await settle();
    await vi.advanceTimersByTimeAsync(1000);
    expect(made.fetch).toHaveBeenCalledOnce();
    timer.mockRestore();
  });

  it("ignores a successful negotiation response that arrives after close", async () => {
    const made = harness();
    let resolveFetch: (response: Response) => void = () => undefined;
    made.fetch.mockImplementation(
      () => new Promise<Response>((resolve) => (resolveFetch = resolve)),
    );
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    }).open("/stream/", made.sink);
    control.close();
    resolveFetch(grant());
    await settle();
    expect(made.sockets).toHaveLength(0);
    expect(made.calls).toHaveLength(0);
  });

  it("retries an unsuccessful non-auth negotiation response", async () => {
    const made = harness();
    made.fetch.mockResolvedValue(new Response("", { status: 500 }));
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    }).open("/stream/", made.sink);
    await settle();
    expect(made.fetch).toHaveBeenCalledOnce();
    expect(made.calls).toHaveLength(0);
    control.close();
  });

  it.each(["text/event-stream", "text/html; charset=utf-8", ""])(
    "aborts and falls back when the negotiation reply is %j, not JSON",
    async (type) => {
      const made = harness();
      const json = vi.spyOn(Response.prototype, "json");
      made.fetch.mockResolvedValue(
        type === ""
          ? new Response(null, { status: 200 })
          : new Response("event: next-hello\n", {
              status: 200,
              headers: { "Content-Type": type },
            }),
      );
      createWebSocketTransport({
        fallback: made.fallback,
        fetch: made.fetch,
        socket: made.socket,
        origin: "https://example.test",
      }).open("/stream/", made.sink);
      await settle();
      const init = made.fetch.mock.calls[0]![1]!;
      expect(init.signal!.aborted).toBe(true);
      expect(json).not.toHaveBeenCalled();
      expect(made.calls).toHaveLength(1);
      expect(made.sockets).toHaveLength(0);
    },
  );

  it("accepts a JSON reply whose content type carries parameters", async () => {
    const made = harness();
    made.fetch.mockResolvedValue(
      new Response(
        JSON.stringify({
          protocol: 2,
          transport: "ws",
          ticket: "t",
          url: "/_next/ws/",
        }),
        { headers: { "Content-Type": "Application/JSON; charset=utf-8" } },
      ),
    );
    createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    }).open("/stream/", made.sink);
    await settle();
    expect(made.sockets).toHaveLength(1);
    expect(made.calls).toHaveLength(0);
  });

  it("falls back when the negotiation reply does not arrive in time", async () => {
    vi.useFakeTimers();
    const made = harness();
    let resolveFetch: (response: Response) => void = () => undefined;
    made.fetch.mockImplementation(
      () => new Promise<Response>((resolve) => (resolveFetch = resolve)),
    );
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    }).open("/stream/", made.sink);
    await vi.advanceTimersByTimeAsync(4999);
    expect(made.calls).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(made.calls).toHaveLength(1);
    expect(made.fetch.mock.calls[0]![1]!.signal!.aborted).toBe(true);
    resolveFetch(grant());
    await settle();
    expect(made.sockets).toHaveLength(0);
    expect(made.calls).toHaveLength(1);
    control.close();
    expect(made.calls[0]!.sink).toBe(made.sink);
  });

  it("falls back when the grant body stalls past the timeout", async () => {
    vi.useFakeTimers();
    const made = harness();
    made.fetch.mockImplementation(async (_url, init) => {
      const body = new ReadableStream({
        start(controller) {
          init!.signal!.addEventListener("abort", () =>
            controller.error(new DOMException("aborted", "AbortError")),
          );
        },
      });
      return new Response(body, { headers: { "Content-Type": "application/json" } });
    });
    createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    }).open("/stream/", made.sink);
    await vi.advanceTimersByTimeAsync(5000);
    await settle();
    expect(made.calls).toHaveLength(1);
    expect(made.sockets).toHaveLength(0);
    expect(made.sink.closed).not.toHaveBeenCalled();
  });

  it("clears the negotiation timeout once the grant is read", async () => {
    vi.useFakeTimers();
    const made = harness();
    made.fetch.mockImplementation(async () => grant());
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    }).open("/stream/", made.sink);
    await settle();
    made.sockets[0]!.open();
    made.sockets[0]!.message(JSON.stringify({ type: "hello", topics: {} }));
    await vi.advanceTimersByTimeAsync(60000);
    expect(made.calls).toHaveLength(0);
    expect(made.sink.closed).not.toHaveBeenCalled();
    control.close();
  });

  it("drops an accepted socket that sends no hello in time", async () => {
    vi.useFakeTimers();
    const made = harness();
    made.fetch.mockImplementation(async () => grant());
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
      random: () => 0,
    }).open("/stream/", made.sink);
    await settle();
    made.sockets[0]!.open();
    await vi.advanceTimersByTimeAsync(1000);
    made.sockets[0]!.message(JSON.stringify({ type: "ping", server_time: 1 }));
    await vi.advanceTimersByTimeAsync(3999);
    expect(made.sockets[0]!.control.close).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(made.sockets[0]!.control.close).toHaveBeenCalledOnce();
    expect(made.sink.closed).toHaveBeenCalledExactlyOnceWith("transient");
    await vi.advanceTimersByTimeAsync(150);
    await settle();
    expect(made.sockets).toHaveLength(2);
    expect(made.calls).toHaveLength(0);
    control.close();
  });

  function flapping(made: ReturnType<typeof harness>) {
    made.fetch.mockImplementation(async () => grant());
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
      random: () => 0,
    }).open("/stream/", made.sink);
    const hello = (index: number, heartbeat?: number): void => {
      made.sockets[index]!.open();
      made.sockets[index]!.message(
        JSON.stringify({ type: "hello", topics: {}, heartbeat }),
      );
    };
    const drop = async (index: number, delay: number): Promise<void> => {
      made.sockets[index]!.close(1011);
      await vi.advanceTimersByTimeAsync(delay);
      await settle();
    };
    return { control, hello, drop };
  }

  it("falls back to SSE when the server drops every socket right after hello", async () => {
    vi.useFakeTimers();
    const made = harness();
    const { control, hello, drop } = flapping(made);
    await settle();
    hello(0, 10);
    await drop(0, 150);
    hello(1, 10);
    await drop(1, 300);
    hello(2, 10);
    made.sockets[2]!.close(1011);
    expect(made.sockets).toHaveLength(3);
    expect(made.calls).toHaveLength(1);
    expect(made.sink.closed).toHaveBeenCalledTimes(3);
    expect(made.sink.closed).toHaveBeenLastCalledWith("transient");
    control.close();
  });

  it("resets the failure count once a socket outlives a heartbeat", async () => {
    vi.useFakeTimers();
    const made = harness();
    const { control, hello, drop } = flapping(made);
    await settle();
    hello(0);
    await drop(0, 150);
    hello(1, 1);
    await vi.advanceTimersByTimeAsync(1000);
    made.sockets[1]!.message(JSON.stringify({ type: "ping", server_time: 1 }));
    await drop(1, 150);
    hello(2, 1);
    await drop(2, 300);
    expect(made.sockets).toHaveLength(4);
    expect(made.calls).toHaveLength(0);
    made.sockets[3]!.close(1011);
    expect(made.calls).toHaveLength(1);
    control.close();
  });

  it("uses the default heartbeat to judge a hello without one", async () => {
    vi.useFakeTimers();
    const made = harness();
    const { control, hello, drop } = flapping(made);
    await settle();
    await drop(0, 150);
    hello(1, -5);
    await vi.advanceTimersByTimeAsync(24999);
    expect(made.sockets).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    await drop(1, 150);
    await drop(2, 300);
    expect(made.calls).toHaveLength(0);
    control.close();
  });

  it("drops a socket that misses pings and reconnects through the backoff", async () => {
    vi.useFakeTimers();
    const made = harness();
    const { control, hello } = flapping(made);
    await settle();
    hello(0, 2);
    await vi.advanceTimersByTimeAsync(4000);
    made.sockets[0]!.message(JSON.stringify({ type: "ping", server_time: 1 }));
    await vi.advanceTimersByTimeAsync(4999);
    expect(made.sockets[0]!.control.close).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(made.sockets[0]!.control.close).toHaveBeenCalledOnce();
    expect(made.sink.closed).toHaveBeenCalledExactlyOnceWith("transient");
    made.sockets[0]!.close(1006);
    made.sockets[0]!.message(JSON.stringify({ type: "hello", topics: {} }));
    expect(made.sink.closed).toHaveBeenCalledOnce();
    expect(made.sink.hello).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(150);
    await settle();
    expect(made.sockets).toHaveLength(2);
    control.close();
  });

  it("clears the watchdog on close and on fallback", async () => {
    vi.useFakeTimers();
    const closing = harness();
    const first = flapping(closing);
    await settle();
    first.hello(0, 1);
    first.control.close();
    await vi.advanceTimersByTimeAsync(60000);
    expect(closing.sink.closed).not.toHaveBeenCalled();
    expect(closing.fetch).toHaveBeenCalledOnce();

    const denied = harness();
    const second = flapping(denied);
    await settle();
    second.hello(0, 1);
    denied.sockets[0]!.refuse(4403);
    await vi.advanceTimersByTimeAsync(60000);
    expect(denied.calls).toHaveLength(1);
    expect(denied.sink.closed).toHaveBeenCalledOnce();
    expect(denied.fetch).toHaveBeenCalledOnce();
    second.control.close();
  });

  it("reads an auth frame that no close follows as a 4401", async () => {
    vi.useFakeTimers();
    const made = harness();
    made.fetch.mockImplementation(async () => grant());
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    }).open("/stream/", made.sink);
    await settle();
    made.sockets[0]!.open();
    made.sockets[0]!.message(
      JSON.stringify({ type: "hello", topics: {}, heartbeat: 1 }),
    );
    made.sockets[0]!.message(JSON.stringify({ type: "error", code: "auth" }));
    await vi.advanceTimersByTimeAsync(4999);
    expect(made.sockets).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    await settle();
    expect(made.sockets[0]!.control.close).toHaveBeenCalledOnce();
    expect(made.fetch).toHaveBeenCalledTimes(2);
    expect(made.sockets).toHaveLength(2);
    made.sockets[0]!.close(4401);
    expect(made.calls).toHaveLength(0);
    made.sockets[1]!.message(JSON.stringify({ type: "error", code: "auth" }));
    await vi.advanceTimersByTimeAsync(5000);
    expect(made.calls).toHaveLength(1);
    expect(made.fetch).toHaveBeenCalledTimes(2);
    control.close();
  });

  it("retries over WebSocket after a protocol error and a 4400 close", async () => {
    vi.useFakeTimers();
    const made = harness();
    made.fetch.mockImplementation(async () => grant());
    const control = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
      random: () => 0,
    }).open("/stream/", made.sink);
    await settle();
    made.sockets[0]!.refuse(4400, "protocol");
    expect(made.sink.closed).toHaveBeenCalledWith("transient");
    await vi.advanceTimersByTimeAsync(151);
    await settle();
    expect(made.fetch).toHaveBeenCalledTimes(2);
    expect(made.calls).toHaveLength(0);
    control.close();
  });
});

async function subscribed(
  topics: unknown = { room: {} },
  limits: Record<string, unknown> = { tunnel: {} },
) {
  const made = harness();
  made.fetch.mockImplementation(async () => grant());
  const transport = createWebSocketTransport({
    fallback: made.fallback,
    fetch: made.fetch,
    socket: made.socket,
    origin: "https://example.test",
    random: () => 0,
  });
  const control = transport.open("/stream/", made.sink);
  await settle();
  const socket = made.sockets[0]!;
  socket.open();
  socket.message(JSON.stringify({ type: "hello", topics, heartbeat: 0, ...limits }));
  const last = (): Record<string, unknown> =>
    JSON.parse(socket.sent[socket.sent.length - 1]!) as Record<string, unknown>;
  const reply = (frame: Record<string, unknown>): void =>
    socket.message(JSON.stringify({ id: last().id, ...frame }));
  return { made, transport, control, socket, last, reply };
}

function formBody(entries: [string, string | Blob][]): FormData {
  const body = new FormData();
  for (const [key, value] of entries) body.append(key, value);
  return body;
}

describe("WebSocket connection id", () => {
  const meta = (topic: string): StreamMeta => ({
    topic,
    epoch: "e",
    seq: 1,
    serverTime: 0,
  });

  it("names the connection a hello announced for the topics it holds", async () => {
    const s = await subscribed({ room: {} }, { connection: "c-1" });
    expect(s.transport.tag(meta("room")).connection).toBe("c-1");
    expect(s.transport.tag(meta("other")).connection).toBeUndefined();
    expect(s.transport.tag(undefined)).toBeUndefined();
    s.socket.message(JSON.stringify({ type: "hello", topics: { room: {} } }));
    expect(s.transport.tag(meta("room")).connection).toBeUndefined();
    s.control.close();
  });

  it("names no connection once the socket is gone", async () => {
    const s = await subscribed({ room: {} }, { connection: "c-1" });
    s.socket.refuse(4403);
    expect(s.transport.tag(meta("room")).connection).toBeUndefined();
    s.control.close();
  });
});

describe("WebSocket upstream events", () => {
  it("sends an up frame and resolves with the ack", async () => {
    const s = await subscribed();
    const sent = s.transport.up("room", "typing", { user: 1 });
    expect(s.last()).toEqual({
      type: "up",
      topic: "room",
      name: "typing",
      data: { user: 1 },
      id: expect.any(String),
    });
    s.reply({ type: "ack", ok: true });
    await expect(sent).resolves.toBe(true);
    const refused = s.transport.up("room", "typing", null);
    s.reply({ type: "ack", ok: false, error: "denied" });
    await expect(refused).resolves.toBe(false);
    s.control.close();
  });

  it("resolves false without a subscribed socket for the topic", async () => {
    const made = harness();
    const transport = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    });
    await expect(transport.up("room", "typing", {})).resolves.toBe(false);
    const s = await subscribed();
    await expect(s.transport.up("other", "typing", {})).resolves.toBe(false);
    expect(s.socket.sent.map((body) => JSON.parse(body).type)).toEqual(["sub"]);
    s.control.close();
  });

  it("takes no topics from a hello that lists none", async () => {
    const s = await subscribed(["room"]);
    await expect(s.transport.up("room", "typing", {})).resolves.toBe(false);
    s.control.close();
  });

  it("resolves false when the ack does not come in time and ignores it later", async () => {
    vi.useFakeTimers();
    const s = await subscribed();
    const sent = s.transport.up("room", "typing", {});
    const id = s.last().id;
    await vi.advanceTimersByTimeAsync(5000);
    await expect(sent).resolves.toBe(false);
    s.socket.message(JSON.stringify({ type: "ack", id, ok: true }));
    s.socket.message(JSON.stringify({ type: "error", code: "limit" }));
    expect(s.made.calls).toHaveLength(0);
    s.control.close();
  });

  it("waits for an ack as long as the hello's upstream timeout plus a margin", async () => {
    vi.useFakeTimers();
    const s = await subscribed({ room: {} }, { upstream: { timeout: 10 } });
    let done = false;
    const sent = s.transport.up("room", "typing", {});
    void sent.then(() => (done = true));
    await vi.advanceTimersByTimeAsync(14999);
    expect(done).toBe(false);
    s.reply({ type: "ack", ok: true });
    await expect(sent).resolves.toBe(true);
    const late = s.transport.up("room", "typing", {});
    await vi.advanceTimersByTimeAsync(15000);
    await expect(late).resolves.toBe(false);
    s.control.close();
  });

  it("resolves false when the socket closes before the ack", async () => {
    const s = await subscribed();
    const sent = s.transport.up("room", "typing", {});
    s.socket.close(1006);
    await expect(sent).resolves.toBe(false);
    await expect(s.transport.up("room", "typing", {})).resolves.toBe(false);
    s.control.close();
  });

  it("resolves false for an event larger than an inbound frame", async () => {
    const s = await subscribed();
    await expect(s.transport.up("room", "typing", "x".repeat(65536))).resolves.toBe(
      false,
    );
    expect(s.socket.sent).toHaveLength(1);
    s.control.close();
  });

  it("ignores a hello and an ack that arrive before the socket opened", async () => {
    const made = harness();
    made.fetch.mockImplementation(async () => grant());
    const transport = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    });
    const control = transport.open("/stream/", made.sink);
    await settle();
    made.sockets[0]!.message(JSON.stringify({ type: "hello", topics: { room: {} } }));
    made.sockets[0]!.message(JSON.stringify({ type: "ack", id: "1", ok: true }));
    await expect(transport.up("room", "typing", {})).resolves.toBe(false);
    control.close();
  });

  it("drops the socket of a fallback, so events report false", async () => {
    const s = await subscribed();
    s.socket.refuse(4403);
    await expect(s.transport.up("room", "typing", {})).resolves.toBe(false);
    s.control.close();
  });

  it("reconnects over WebSocket after a rate-limit close", async () => {
    vi.useFakeTimers();
    const s = await subscribed();
    s.socket.close(4429);
    expect(s.made.sink.closed).toHaveBeenCalledWith("transient");
    await vi.advanceTimersByTimeAsync(151);
    await settle();
    expect(s.made.calls).toHaveLength(0);
    expect(s.made.fetch).toHaveBeenCalledTimes(2);
    expect(s.made.sockets).toHaveLength(2);
    s.control.close();
  });
});

describe("WebSocket close with replies in flight", () => {
  const target = "https://example.test/_next/form/u1/";
  const post = { method: "POST", body: "" };

  it("keeps the socket until a tunneled reply lands and reads nothing else", async () => {
    const s = await subscribed();
    const reply = s.transport.http(target, post);
    const id = s.last().id;
    const sent = s.socket.sent.length;
    s.control.close();
    expect(s.socket.control.close).not.toHaveBeenCalled();
    expect(s.transport.http(target, post)).toBeUndefined();
    await expect(s.transport.up("room", "typing", {})).resolves.toBe(false);
    s.socket.message(JSON.stringify({ type: "patches", data: { seq: 1 } }));
    s.socket.message(JSON.stringify({ type: "ping" }));
    s.socket.message("{");
    s.socket.message(JSON.stringify({ type: "http", id, status: 204 }));
    await expect(reply).resolves.toMatchObject({ status: 204 });
    expect(s.socket.control.close).toHaveBeenCalledOnce();
    expect(s.socket.sent).toHaveLength(sent);
    expect(s.made.sink.patches).not.toHaveBeenCalled();
    expect(s.made.calls).toHaveLength(0);
    s.control.close();
    expect(s.socket.control.close).toHaveBeenCalledOnce();
  });

  it("drains a pending up ack before the socket goes", async () => {
    const s = await subscribed();
    const sent = s.transport.up("room", "typing", {});
    const id = s.last().id;
    s.control.close();
    expect(s.socket.control.close).not.toHaveBeenCalled();
    s.socket.message(JSON.stringify({ type: "ack", id, ok: true }));
    await expect(sent).resolves.toBe(true);
    expect(s.socket.control.close).toHaveBeenCalledOnce();
  });

  it("closes the socket once the tunnel timeout gives up on the reply", async () => {
    vi.useFakeTimers();
    const s = await subscribed({ room: {} }, { tunnel: { timeout: 2 } });
    const reply = s.transport.http(target, post);
    s.control.close();
    await vi.advanceTimersByTimeAsync(6999);
    expect(s.socket.control.close).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await expect(reply).resolves.toBe("timeout");
    expect(s.socket.control.close).toHaveBeenCalledOnce();
  });

  it("closes at once without a reply in flight", async () => {
    const s = await subscribed();
    s.control.close();
    expect(s.socket.control.close).toHaveBeenCalledOnce();
  });
});

describe("WebSocket request tunnel", () => {
  const target = "https://example.test/_next/form/u1/?step=2";

  it("sends a form post as an http frame with its tunnel headers", async () => {
    const s = await subscribed();
    const reply = s.transport.http(
      target,
      {
        method: "POST",
        headers: new Headers({
          "X-Next-Request": "1",
          "X-Next-Request-Id": "r1",
          "X-CSRFToken": "tok",
          Accept: "application/json",
          "Content-Type": "multipart/form-data",
        }),
        body: formBody([
          ["title", "a b&c"],
          ["n", "1"],
        ]),
      },
      "X-CSRFToken",
    );
    expect(s.last()).toEqual({
      type: "http",
      id: expect.any(String),
      method: "POST",
      path: "/_next/form/u1/?step=2",
      headers: {
        "x-next-request": "1",
        "x-next-request-id": "r1",
        "x-csrftoken": "tok",
        accept: "application/json",
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
      },
      body: "title=a+b%26c&n=1",
    });
    s.reply({
      type: "http",
      status: 200,
      headers: { "content-type": "application/vnd.next.patches+json" },
      body: '{"ops":[]}',
    });
    const response = (await reply) as Response;
    expect(response.status).toBe(200);
    expect(response.redirected).toBe(false);
    expect(response.headers.get("content-type")).toBe(
      "application/vnd.next.patches+json",
    );
    await expect(response.text()).resolves.toBe('{"ops":[]}');
    void s.transport.http(
      target,
      { method: "POST", headers: { "X-XSRF-Token": "xs" } },
      "X-XSRF-TOKEN",
    );
    expect(s.last().headers).toEqual({ "x-xsrf-token": "xs" });
    s.control.close();
  });

  it.each([
    [{ Authorization: "secret" }, "X-CSRFToken"],
    [{ "X-Other": "1", "X-Next-Request": "1" }, "X-CSRFToken"],
    [{ "X-CSRFToken": "tok" }, "X-XSRF-TOKEN"],
    [{ "X-CSRFToken": "tok" }, undefined],
  ])(
    "leaves a request with a foreign header %j to plain HTTP",
    async (headers, csrf) => {
      const s = await subscribed();
      expect(
        s.transport.http(target, { method: "POST", headers }, csrf),
      ).toBeUndefined();
      expect(s.socket.sent).toHaveLength(1);
      s.control.close();
    },
  );

  it("carries a text, url-encoded or empty body as it is", async () => {
    const s = await subscribed();
    void s.transport.http(target, { method: "POST", body: '{"a":1}' });
    expect(s.last().body).toBe('{"a":1}');
    expect(s.last().headers).toEqual({});
    void s.transport.http(target, {
      method: "POST",
      body: new URLSearchParams([["a", "1"]]),
    });
    expect(s.last().body).toBe("a=1");
    void s.transport.http(target, { method: "POST" });
    expect(s.last().body).toBe("");
    void s.transport.http(target, { method: "POST", body: null });
    expect(s.last().body).toBe("");
    s.control.close();
  });

  it("leaves files, binary and oversized bodies to plain HTTP", async () => {
    const s = await subscribed();
    const file = new File(["data"], "a.txt");
    expect(
      s.transport.http(target, { method: "POST", body: formBody([["f", file]]) }),
    ).toBeUndefined();
    expect(s.transport.http(target, { method: "POST", body: new Blob(["x"]) })).toBe(
      undefined,
    );
    expect(
      s.transport.http(target, { method: "POST", body: "x".repeat(65536) }),
    ).toBeUndefined();
    expect(s.socket.sent).toHaveLength(1);
    s.control.close();
  });

  it("leaves a request to plain HTTP without an open socket", () => {
    const made = harness();
    const transport = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    });
    expect(transport.http(target, { method: "POST", body: "" })).toBeUndefined();
  });

  it("refuses a redirect off the origin like a same-origin fetch", async () => {
    const s = await subscribed();
    const reply = s.transport.http(target, { method: "POST", body: "" });
    s.reply({
      type: "http",
      status: 302,
      headers: { location: "https://evil.test/" },
      body: "",
    });
    await expect(reply).rejects.toThrow(TypeError);
    const local = s.transport.http(target, { method: "POST", body: "" });
    s.reply({ type: "http", status: 303, headers: { location: "//example.test/x" } });
    expect(((await local) as Response).url).toBe("https://example.test/x");
    s.control.close();
  });

  it("marks a redirect so the wire navigates to its location", async () => {
    const s = await subscribed();
    const reply = s.transport.http(target, { method: "POST", body: "" });
    s.reply({ type: "http", status: 302, headers: { location: "/done/" }, body: "" });
    const response = (await reply) as Response;
    expect(response.redirected).toBe(true);
    expect(response.url).toBe("https://example.test/done/");
    const moved = s.transport.http(target, { method: "POST", body: "" });
    s.reply({ type: "http", status: 304 });
    const unchanged = (await moved) as Response;
    expect(unchanged.redirected).toBe(false);
    await expect(unchanged.text()).resolves.toBe("");
    const located = s.transport.http(target, { method: "POST", body: "" });
    s.reply({ type: "http", status: 201, headers: { location: "/new/" } });
    expect(((await located) as Response).redirected).toBe(false);
    s.control.close();
  });

  it.each([
    [{ status: 0, error: "http_only", retry: false }, "http_only"],
    [{ status: 0, error: "timeout", retry: false }, "timeout"],
    [{ status: 0, error: "limit" }, "limit"],
  ])("names why a request the server ran got no reply %j", async (frame, reason) => {
    const s = await subscribed();
    const reply = s.transport.http(target, { method: "POST", body: "" });
    s.reply({ type: "http", ...frame });
    await expect(reply).resolves.toBe(reason);
    s.control.close();
  });

  it.each([
    [{ error: "unknown" }, "unknown"],
    [{ status: 42 }, "range"],
  ])("rejects a malformed reply %j", async (frame, error) => {
    const s = await subscribed();
    const reply = s.transport.http(target, { method: "POST", body: "" });
    s.reply({ type: "http", ...frame });
    await expect(reply).rejects.toThrow(error);
    s.control.close();
  });

  it("names a socket that closed before the reply", async () => {
    const s = await subscribed();
    const reply = s.transport.http(target, { method: "POST", body: "" });
    s.socket.close(1006);
    await expect(reply).resolves.toBe("closed");
    s.control.close();
  });

  it("ignores a reply with an id it never sent", async () => {
    const s = await subscribed();
    const reply = s.transport.http(target, { method: "POST", body: "" });
    s.socket.message(JSON.stringify({ type: "http", id: "nope", status: 200 }));
    s.control.close();
    s.socket.message(JSON.stringify({ type: "http", id: "nope", status: 200 }));
    expect(s.socket.control.close).not.toHaveBeenCalled();
    s.socket.close(1006);
    await expect(reply).resolves.toBe("closed");
    expect(s.socket.control.close).toHaveBeenCalledOnce();
  });
});

describe("WebSocket tunnel refusals", () => {
  const target = "https://example.test/_next/form/u1/";
  const post = { method: "POST", body: "" };
  const kinds = (socket: { sent: string[] }): unknown[] =>
    socket.sent.map((body) => JSON.parse(body).type);

  it.each(["limit", "transport", "path", "method", "headers", "body"])(
    "leaves a request the server did not run to fetch on %s",
    async (error) => {
      const s = await subscribed();
      const reply = s.transport.http(target, post);
      s.reply({ type: "http", status: 0, error, retry: true });
      await expect(reply).resolves.toBeUndefined();
      expect(s.transport.http(target, post)).toBeInstanceOf(Promise);
      expect(s.made.fetch).toHaveBeenCalledOnce();
      s.control.close();
    },
  );

  it("keeps a socket stale twice in a row on fetch without reconnecting again", async () => {
    const s = await subscribed();
    const first = s.transport.http(target, post);
    s.reply({ type: "http", status: 0, error: "stale", retry: true });
    await expect(first).resolves.toBeUndefined();
    await settle();
    const fresh = s.made.sockets[1]!;
    fresh.open();
    fresh.message(
      JSON.stringify({ type: "hello", topics: { room: {} }, heartbeat: 0, tunnel: {} }),
    );
    const second = s.transport.http(target, post);
    const id = (JSON.parse(fresh.sent[fresh.sent.length - 1]!) as { id: string }).id;
    fresh.message(
      JSON.stringify({ type: "http", id, status: 0, error: "stale", retry: true }),
    );
    await expect(second).resolves.toBeUndefined();
    await settle();
    expect(s.transport.http(target, post)).toBeUndefined();
    expect(fresh.control.close).not.toHaveBeenCalled();
    expect(s.made.sockets).toHaveLength(2);
    expect(s.made.fetch).toHaveBeenCalledTimes(2);
    s.control.close();
  });

  it("tunnels again once a stale-cut socket reconnects for another reason", async () => {
    vi.useFakeTimers();
    const s = await subscribed();
    const first = s.transport.http(target, post);
    s.reply({ type: "http", status: 0, error: "stale", retry: true });
    await first;
    await vi.advanceTimersByTimeAsync(0);
    const fresh = s.made.sockets[1]!;
    fresh.open();
    fresh.message(
      JSON.stringify({ type: "hello", topics: {}, heartbeat: 0, tunnel: {} }),
    );
    const second = s.transport.http(target, post);
    const id = (JSON.parse(fresh.sent[fresh.sent.length - 1]!) as { id: string }).id;
    fresh.message(
      JSON.stringify({ type: "http", id, status: 0, error: "stale", retry: true }),
    );
    await second;
    fresh.close(1006);
    await vi.advanceTimersByTimeAsync(151);
    const later = s.made.sockets[2]!;
    later.open();
    later.message(
      JSON.stringify({ type: "hello", topics: {}, heartbeat: 0, tunnel: {} }),
    );
    void s.transport.http(target, post);
    expect(JSON.parse(later.sent[later.sent.length - 1]!)).toMatchObject({
      type: "http",
    });
    s.control.close();
  });

  it("sends nothing more through a socket that has the tunnel disabled", async () => {
    const s = await subscribed();
    const reply = s.transport.http(target, post);
    s.reply({ type: "http", status: 0, error: "disabled", retry: true });
    await expect(reply).resolves.toBeUndefined();
    expect(s.transport.http(target, post)).toBeUndefined();
    const event = s.transport.up("room", "typing", {});
    s.reply({ type: "ack", ok: true });
    await expect(event).resolves.toBe(true);
    expect(kinds(s.socket)).toEqual(["sub", "http", "up"]);
    expect(s.made.sockets).toHaveLength(1);
    s.control.close();
  });

  it("reconnects a stale socket and routes requests to fetch until the new hello", async () => {
    const s = await subscribed();
    const reply = s.transport.http(target, post);
    s.reply({ type: "http", status: 0, error: "stale", retry: true });
    await expect(reply).resolves.toBeUndefined();
    expect(s.socket.control.close).toHaveBeenCalledOnce();
    expect(s.made.sink.closed).toHaveBeenCalledWith("transient");
    expect(s.transport.http(target, post)).toBeUndefined();
    await settle();
    expect(s.made.fetch).toHaveBeenCalledTimes(2);
    const next = s.made.sockets[1]!;
    next.open();
    expect(s.transport.http(target, post)).toBeUndefined();
    next.message(
      JSON.stringify({ type: "hello", topics: { room: {} }, heartbeat: 0, tunnel: {} }),
    );
    void s.transport.http(target, post);
    expect(kinds(next)).toEqual(["sub", "http"]);
    expect(s.made.calls).toHaveLength(0);
    s.control.close();
  });

  it("waits for the pending replies of a stale socket before it reconnects", async () => {
    const s = await subscribed();
    const first = s.transport.http(target, post);
    const firstId = s.last().id;
    const event = s.transport.up("room", "typing", {});
    const eventId = s.last().id;
    const second = s.transport.http(target, post);
    s.reply({ type: "http", status: 0, error: "stale", retry: true });
    await expect(second).resolves.toBeUndefined();
    expect(s.socket.control.close).not.toHaveBeenCalled();
    s.socket.message(JSON.stringify({ type: "ack", id: eventId, ok: true }));
    await expect(event).resolves.toBe(true);
    expect(s.socket.control.close).not.toHaveBeenCalled();
    s.socket.message(
      JSON.stringify({ type: "http", id: firstId, status: 204, headers: {} }),
    );
    expect(((await first) as Response).status).toBe(204);
    expect(s.socket.control.close).toHaveBeenCalledOnce();
    s.control.close();
  });

  it("settles the replies of a stale socket that drops before they arrive", async () => {
    const s = await subscribed();
    const first = s.transport.http(target, post);
    const second = s.transport.http(target, post);
    s.reply({ type: "http", status: 0, error: "stale", retry: true });
    await expect(second).resolves.toBeUndefined();
    s.socket.close(1006);
    await expect(first).resolves.toBe("closed");
    s.control.close();
  });

  it("reconnects every socket once the CSRF cookie rotates", async () => {
    const s = await subscribed();
    const pending = s.transport.http(target, post);
    s.transport.renew("rotated");
    expect(s.socket.control.close).not.toHaveBeenCalled();
    expect(s.transport.http(target, post)).toBeUndefined();
    s.reply({ type: "http", status: 200, body: "ok" });
    expect(((await pending) as Response).status).toBe(200);
    expect(s.socket.control.close).toHaveBeenCalledOnce();
    await settle();
    const next = s.made.sockets[1]!;
    next.open();
    next.message(
      JSON.stringify({ type: "hello", topics: { room: {} }, heartbeat: 0, tunnel: {} }),
    );
    expect(s.transport.http(target, post)).toBeInstanceOf(Promise);
    expect(next.control.close).not.toHaveBeenCalled();
    s.control.close();
  });

  it("reconnects a socket whose handshake predates a rotation once it says hello", async () => {
    const made = harness();
    made.fetch.mockImplementation(async () => grant());
    const transport = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    });
    transport.renew("first");
    const control = transport.open("/stream/", made.sink);
    await settle();
    made.sockets[0]!.open();
    transport.renew("second");
    made.sockets[0]!.message(
      JSON.stringify({ type: "hello", topics: {}, heartbeat: 5 }),
    );
    expect(made.sockets[0]!.control.close).toHaveBeenCalledOnce();
    expect(transport.http(target, post)).toBeUndefined();
    await settle();
    expect(made.sockets).toHaveLength(2);
    control.close();
  });

  it("reconnects only for a token whose unmasked secret is new", async () => {
    const made = harness();
    made.fetch.mockImplementation(async () => grant());
    const transport = createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
      token: MASKED[0],
    });
    const control = transport.open("/stream/", made.sink);
    await settle();
    made.sockets[0]!.open();
    made.sockets[0]!.message(JSON.stringify({ type: "hello", topics: {}, tunnel: {} }));
    transport.renew(MASKED[1]);
    transport.renew(undefined);
    expect(transport.http(target, post)).toBeInstanceOf(Promise);
    expect(made.sockets[0]!.control.close).not.toHaveBeenCalled();
    transport.renew(OTHER);
    expect(made.sockets[0]!.control.close).not.toHaveBeenCalled();
    made.sockets[0]!.message(
      JSON.stringify({ type: "http", id: "1", status: 204, headers: {} }),
    );
    await settle();
    expect(made.sockets[0]!.control.close).toHaveBeenCalledOnce();
    expect(made.sockets).toHaveLength(2);
    control.close();
  });

  it("gives up on a reply after 35 seconds and ignores it later", async () => {
    vi.useFakeTimers();
    const s = await subscribed();
    const reply = s.transport.http(target, post);
    const id = s.last().id;
    await vi.advanceTimersByTimeAsync(34999);
    s.socket.message(JSON.stringify({ type: "ping" }));
    await vi.advanceTimersByTimeAsync(1);
    await expect(reply).resolves.toBe("timeout");
    s.socket.message(JSON.stringify({ type: "http", id, status: 200 }));
    expect(s.made.calls).toHaveLength(0);
    s.control.close();
  });
});

describe("WebSocket fire-and-forget events", () => {
  it("sends an up frame without an id and resolves true at once", async () => {
    const s = await subscribed();
    await expect(s.transport.up("room", "typing", { user: 1 }, false)).resolves.toBe(
      true,
    );
    expect(s.last()).toEqual({
      type: "up",
      topic: "room",
      name: "typing",
      data: { user: 1 },
    });
    s.socket.message(JSON.stringify({ type: "error", code: "limit" }));
    await expect(s.transport.up("other", "typing", {}, false)).resolves.toBe(false);
    await expect(
      s.transport.up("room", "typing", "x".repeat(65536), false),
    ).resolves.toBe(false);
    expect(s.socket.sent).toHaveLength(2);
    s.control.close();
  });
});

describe("WebSocket limits from the hello", () => {
  const target = "https://example.test/_next/form/u1/";
  const post = { method: "POST", body: "" };

  it("keeps requests on fetch when the hello advertises no tunnel", async () => {
    const s = await subscribed({ room: {} }, {});
    expect(s.transport.http(target, post)).toBeUndefined();
    const event = s.transport.up("room", "typing", {});
    s.reply({ type: "ack", ok: true });
    await expect(event).resolves.toBe(true);
    s.control.close();
  });

  it("admits exactly the headers and the CSRF name the hello lists", async () => {
    const s = await subscribed(
      { room: {} },
      { tunnel: { headers: ["accept"], prefixes: ["x-app-"], csrf: "x-token" } },
    );
    void s.transport.http(
      target,
      { ...post, headers: { Accept: "a", "X-App-Id": "1", "X-Token": "t" } },
      "X-CSRFToken",
    );
    expect(s.last().headers).toEqual({ accept: "a", "x-app-id": "1", "x-token": "t" });
    for (const headers of [
      { "Content-Type": "text/plain" },
      { "X-Next-Request": "1" },
      { "X-CSRFToken": "t" },
    ])
      expect(s.transport.http(target, { ...post, headers }, "X-CSRFToken")).toBe(
        undefined,
      );
    expect(s.socket.sent).toHaveLength(2);
    s.control.close();
  });

  it("caps a tunneled request at the frame the hello names", async () => {
    const s = await subscribed({ room: {} }, { tunnel: { frame: 200 } });
    expect(s.transport.http(target, { method: "POST", body: "x".repeat(200) })).toBe(
      undefined,
    );
    expect(s.transport.http(target, { method: "POST", body: "x" })).toBeInstanceOf(
      Promise,
    );
    expect(s.socket.sent).toHaveLength(2);
    s.control.close();
  });

  it("waits five seconds past the server timeout the hello names", async () => {
    vi.useFakeTimers();
    const s = await subscribed({ room: {} }, { tunnel: { timeout: 10 } });
    const reply = s.transport.http(target, post);
    let settled = false;
    void reply?.then(() => (settled = true));
    await vi.advanceTimersByTimeAsync(14999);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await expect(reply).resolves.toBe("timeout");
    s.control.close();
  });

  it("falls back to the defaults for malformed tunnel limits", async () => {
    vi.useFakeTimers();
    const s = await subscribed(
      { room: {} },
      {
        tunnel: { timeout: "10", frame: -1, headers: "accept", prefixes: [1], csrf: 2 },
      },
    );
    expect(
      s.transport.http(
        target,
        { ...post, headers: { "Content-Type": "text/plain", "X-CSRFToken": "t" } },
        "X-CSRFToken",
      ),
    ).toBeInstanceOf(Promise);
    expect(s.transport.http(target, { ...post, headers: { "X-Next-Zone": "z" } })).toBe(
      undefined,
    );
    const reply = s.transport.http(target, post);
    await vi.advanceTimersByTimeAsync(34999);
    s.socket.message(JSON.stringify({ type: "ping" }));
    await vi.advanceTimersByTimeAsync(1);
    await expect(reply).resolves.toBe("timeout");
    expect(s.transport.http(target, { method: "POST", body: "x".repeat(65536) })).toBe(
      undefined,
    );
    s.control.close();
  });

  it("refuses an up frame that outgrows the upstream limit unsent", async () => {
    const s = await subscribed({ room: {} }, { upstream: { frame: 64 } });
    await expect(s.transport.up("room", "typing", "x".repeat(11), false)).resolves.toBe(
      false,
    );
    await expect(s.transport.up("room", "typing", "é".repeat(6), false)).resolves.toBe(
      false,
    );
    await expect(s.transport.up("room", "typing", "x".repeat(10))).resolves.toBe(false);
    await expect(s.transport.up("room", "typing", "x".repeat(10), false)).resolves.toBe(
      true,
    );
    await expect(s.transport.up("room", "typing", "é".repeat(5), false)).resolves.toBe(
      true,
    );
    expect(s.socket.sent).toHaveLength(3);
    s.control.close();
  });

  it("holds an up frame to 16 KiB without an upstream limit", async () => {
    const s = await subscribed({ room: {} }, { upstream: { frame: 0 } });
    await expect(
      s.transport.up("room", "typing", "x".repeat(16331), false),
    ).resolves.toBe(false);
    await expect(
      s.transport.up("room", "typing", "x".repeat(16330), false),
    ).resolves.toBe(true);
    expect(s.socket.sent).toHaveLength(2);
    s.control.close();
  });

  it("resolves false on a receiver timeout and sends the event once", async () => {
    const s = await subscribed({ room: {} });
    const sent = s.transport.up("room", "typing", { user: 1 });
    s.reply({ type: "ack", ok: false, error: "timeout" });
    await expect(sent).resolves.toBe(false);
    expect(s.socket.sent.filter((body) => body.includes('"up"'))).toHaveLength(1);
    s.control.close();
  });
});

describe("csrfSecret", () => {
  const secret = "0THXeyP3IKCGHjFuKAoLDWdfUDoh4Irm";

  it("unmasks every masking of one secret to that secret", () => {
    expect(
      csrfSecret("fw062o3AibVzOUYtJtC0uP0MIDbY3wEs5fxT6MItQLn5l3tNjTQBXB3Rs6p5X4VE"),
    ).toBe(secret);
    expect(
      csrfSecret("QcrsoFV0mQwlcQhSwkUZBjSeSzOdaxTwGVYfs3ATUqYRJZMc6K8A45VjC22k45aI"),
    ).toBe(secret);
    expect(
      csrfSecret("6Nu4CeRFMWSQZKRAnWfC6hw3MxglXkfR8xtBTuI2ShL2AkVzb319ii6nTbq84Gg4"),
    ).toBe("cU9Hrq1xgv3mLKe9YhWHmbKuhOkXhwbn");
  });

  it("keeps a token of another length as it is", () => {
    expect(csrfSecret(secret)).toBe(secret);
    expect(csrfSecret(undefined)).toBeUndefined();
  });
});
