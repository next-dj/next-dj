// The optional WebSocket adapter negotiates a session-bound ticket over the
// guarded stream URL, then feeds the shared stream protocol used by SSE.

import { defaultWebSocket } from "./adapters";
import type { Transport, TransportControl } from "./stream";
import { isRecord } from "./protocol";
import type { FetchAdapter } from "./wire";

const NEGOTIATION_HEADER = "X-Next-Transport";
const MAX_FAILURES = 3;
const NEGOTIATION_MS = 5000;
// The heartbeat assumed until a hello names one, in seconds as the server sends it.
const HEARTBEAT = 25;

/** A WebSocket seam that returns a send function only after the handshake opens. */
export interface WebSocketAdapter {
  open(
    url: string,
    onOpen: (send: (body: string) => void) => void,
    onMessage: (body: unknown) => void,
    onClose: (code: number) => void,
  ): TransportControl;
}

/** The seams the lazy WebSocket factory needs, all replaceable in tests. */
export interface WsTransportDeps {
  fetch?: FetchAdapter | undefined;
  socket?: WebSocketAdapter | undefined;
  origin?: string | undefined;
  random?: () => number;
}

export type WsTransportFactory = (
  deps: WsTransportDeps & { fallback: Transport },
) => Transport;

/** Create the WebSocket-first transport with guarded SSE fallback. */
export function createWebSocketTransport(
  deps: WsTransportDeps & { fallback: Transport },
): Transport {
  const fetcher = deps.fetch ?? globalThis.fetch.bind(globalThis);
  const socket = socketAdapter(deps.socket);
  const origin = deps.origin ?? globalThis.location.origin;
  const random = deps.random ?? Math.random;
  return {
    name: "websocket",
    open(url, sink, cursor = new Map()): TransportControl {
      let closed = false;
      let fallbackMode = false;
      let failures = 0;
      let active: TransportControl | undefined;
      let controller: AbortController | undefined;
      let retry: ReturnType<typeof setTimeout> | undefined;
      let send: ((body: string) => void) | undefined;
      let heartbeat = 0;
      let watchdog: ReturnType<typeof setTimeout> | undefined;
      let healthy: ReturnType<typeof setTimeout> | undefined;
      const stopped = (): boolean => closed || fallbackMode;
      const quiet = (): void => {
        clearTimeout(watchdog);
        clearTimeout(healthy);
      };

      const useFallback = (): void => {
        quiet();
        fallbackMode = true;
        controller?.abort();
        clearTimeout(retry);
        const previous = active;
        active = undefined;
        previous?.close();
        active = deps.fallback.open(url, sink);
      };

      const failed = (immediate = false): void => {
        if (stopped()) return;
        quiet();
        const previous = active;
        active = undefined;
        send = undefined;
        previous?.close();
        sink.closed("transient");
        failures += 1;
        if (immediate || failures >= MAX_FAILURES) {
          useFallback();
          return;
        }
        const delay = 150 * 2 ** (failures - 1) + Math.floor(random() * 150);
        retry = setTimeout(() => void connect(), delay);
      };

      const connect = async (): Promise<void> => {
        if (stopped()) return;
        controller = new AbortController();
        // A server that never answers must not hold the stream on WS forever.
        retry = setTimeout(useFallback, NEGOTIATION_MS);
        try {
          const response = await fetcher(url, {
            method: "GET",
            credentials: "same-origin",
            headers: { [NEGOTIATION_HEADER]: "ws", Accept: "application/json" },
            signal: controller.signal,
          });
          if (stopped()) return;
          if (
            response.status === 401 ||
            response.status === 403 ||
            response.status === 406
          ) {
            useFallback();
            return;
          }
          if (!response.ok)
            throw new Error(`WebSocket negotiation returned ${response.status}`);
          if (!/^application\/json/i.test(response.headers.get("content-type") ?? "")) {
            useFallback();
            return;
          }
          const grant: unknown = await response.json();
          clearTimeout(retry);
          if (stopped()) return;
          const target = websocketUrl(grant, origin);
          if (target === undefined) {
            useFallback();
            return;
          }
          const opened: TransportControl = socket.open(
            target.url,
            (readySend) => {
              if (stopped()) return;
              send = readySend;
              readySend(
                JSON.stringify({
                  type: "sub",
                  ticket: target.ticket,
                  last_seq: Object.fromEntries(
                    [...cursor].map(([topic, [epoch, seq]]) => [topic, { epoch, seq }]),
                  ),
                }),
              );
              sink.opened();
            },
            (body) => {
              if (stopped() || active !== opened || typeof body !== "string") return;
              let frame: unknown;
              try {
                frame = JSON.parse(body);
              } catch {
                failed(true);
                return;
              }
              if (!isRecord(frame)) return;
              if (frame.type === "hello") {
                const beat = frame.heartbeat;
                heartbeat = typeof beat === "number" && beat > 0 ? beat : 0;
                clearTimeout(watchdog);
                clearTimeout(healthy);
                // Only a socket that outlives a heartbeat earns back its retries, so a
                // server that drops it right after hello still ends on SSE.
                healthy = setTimeout(
                  () => (failures = 0),
                  (heartbeat || HEARTBEAT) * 1000,
                );
              }
              // A half-open socket delivers nothing, so missed pings count as a drop.
              if (heartbeat > 0) {
                clearTimeout(watchdog);
                watchdog = setTimeout(failed, heartbeat * 2500);
              }
              // Patches text reaches the parse hook untouched for custom codecs.
              if (frame.type === "patches") {
                if (typeof frame.data === "string") sink.patches(frame.data);
              } else if (frame.type === "hello") sink.hello(body);
              else if (frame.type === "skip") sink.skip(body);
              else if (frame.type === "ping") {
                sink.clock(frame.server_time);
                send?.('{"type":"pong"}');
              } else if (frame.type === "error" && frame.code === "auth") useFallback();
            },
            (code) => {
              if (active === opened) failed(code === 4401 || code === 4403);
            },
          );
          active = opened;
          // An accepted socket that never says hello would otherwise hang the stream.
          heartbeat = 0;
          watchdog = setTimeout(failed, NEGOTIATION_MS);
        } catch {
          clearTimeout(retry);
          failed();
        }
      };

      void connect();
      return {
        close() {
          if (closed) return;
          closed = true;
          quiet();
          controller?.abort();
          clearTimeout(retry);
          active?.close();
        },
      };
    },
  };
}

function socketAdapter(socket: WebSocketAdapter | undefined): WebSocketAdapter {
  if (socket !== undefined) return socket;
  return defaultWebSocket();
}

function websocketUrl(
  value: unknown,
  origin: string,
): { url: string; ticket: string } | undefined {
  if (
    !isRecord(value) ||
    value.protocol !== 2 ||
    value.transport !== "ws" ||
    typeof value.ticket !== "string" ||
    value.ticket.length === 0 ||
    typeof value.url !== "string"
  )
    return undefined;
  try {
    const target = new URL(value.url, origin);
    if (
      target.origin !== origin ||
      (target.protocol !== "http:" && target.protocol !== "https:")
    )
      return undefined;
    target.protocol = target.protocol === "https:" ? "wss:" : "ws:";
    return { url: target.href, ticket: value.ticket };
  } catch {
    return undefined;
  }
}

export { defaultWebSocket };
