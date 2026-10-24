import { afterEach, describe, expect, it, vi } from "vitest";
import type { Transport, TransportSink } from "./stream";
import { createWebSocketTransport } from "./ws";
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
    made.sockets[0]!.message({ type: "patches" });
    expect(made.sink.hello).toHaveBeenCalledExactlyOnceWith(JSON.stringify(hello));
    expect(made.sink.patches).toHaveBeenCalledExactlyOnceWith('seq=10 {"not": json');
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
    made.sockets[1]!.close(4401);
    expect(made.calls).toHaveLength(1);
    control.close();
  });

  it("falls back when the grant points off origin or the socket reports auth", async () => {
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
    expect(auth.sockets[0]!.control.close).toHaveBeenCalledOnce();
    expect(auth.calls).toHaveLength(1);
    auth.sockets[0]!.message(JSON.stringify({ type: "error", code: "auth" }));
    auth.sockets[0]!.close(1006);
    expect(auth.calls).toHaveLength(1);
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
    denied.sockets[0]!.message(JSON.stringify({ type: "error", code: "auth" }));
    await vi.advanceTimersByTimeAsync(60000);
    expect(denied.calls).toHaveLength(1);
    expect(denied.sink.closed).not.toHaveBeenCalled();
    second.control.close();
  });

  it("falls back on a top-level auth error frame", async () => {
    const made = harness();
    made.fetch.mockResolvedValue(grant());
    createWebSocketTransport({
      fallback: made.fallback,
      fetch: made.fetch,
      socket: made.socket,
      origin: "https://example.test",
    }).open("/stream/", made.sink);
    await settle();
    made.sockets[0]!.message(JSON.stringify({ type: "error", code: "auth" }));
    expect(made.calls).toHaveLength(1);
  });
});
