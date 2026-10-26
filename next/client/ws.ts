// The optional WebSocket adapter negotiates a session-bound ticket over the
// guarded stream URL, then feeds the shared stream protocol used by SSE.

import { defaultWebSocket } from "./adapters";
import type { StreamMeta, Transport, TransportControl } from "./stream";
import { REFUSAL_DISABLED, REFUSAL_STALE, asString, isRecord } from "./protocol";
import type { FetchAdapter } from "./wire";

const NEGOTIATION_HEADER = "X-Next-Transport";
const MAX_FAILURES = 3;
const NEGOTIATION_MS = 5000;
const ACK_MS = 5000;
// The heartbeat assumed until a hello names one, in seconds as the server sends it.
const HEARTBEAT = 25;
const MAX_FRAME = 65536;
const MAX_UP = 16384;
const TUNNEL_S = 30;
// A margin over the server's own timeout, so its timeout answer arrives first.
const MARGIN_S = 5;

type Frame = Record<string, unknown>;

interface Tunnel {
  ms: number;
  headers: string[];
  prefixes: string[];
  csrf: string | undefined;
}

interface Link {
  send: (body: string) => void;
  topics: Set<string>;
  replies: Map<string, (reply: Frame | null) => void>;
  born: number;
  tunnel: Tunnel;
  // Set until a hello advertises the tunnel, and again once the server refuses it.
  off: boolean;
  frame: number;
  up: number;
  renewed: boolean;
  renew: () => void;
  connection?: string | undefined;
}

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
  // The CSRF token the page holds when the transport is built.
  token?: string | undefined;
}

export interface SocketTransport extends Transport {
  up(topic: string, name: string, data: unknown, ack?: boolean): Promise<boolean>;
  http(
    target: string,
    init: RequestInit,
    csrf?: string,
  ): Promise<Response | string | undefined> | undefined;
  renew(token: string | undefined): void;
  tag<T extends StreamMeta | undefined>(meta: T): T;
}

export type WsTransportFactory = (
  deps: WsTransportDeps & { fallback: Transport },
) => SocketTransport;

/** Create the WebSocket-first transport with guarded SSE fallback. */
export function createWebSocketTransport(
  deps: WsTransportDeps & { fallback: Transport },
): SocketTransport {
  const fetcher = deps.fetch ?? globalThis.fetch.bind(globalThis);
  const socket = socketAdapter(deps.socket);
  const origin = deps.origin ?? globalThis.location.origin;
  const random = deps.random ?? Math.random;
  const links = new Set<Link>();
  let asked = 0;
  let generation = 0;
  let secret = csrfSecret(deps.token);

  const drain = (link: Link): void => {
    if (link.born !== generation && link.replies.size === 0 && links.has(link))
      link.renew();
  };

  const ask = (
    link: Link | undefined,
    frame: Frame,
    ms: number,
    limit: "frame" | "up",
    ack = true,
  ): Promise<Frame | null> | undefined => {
    if (link === undefined) return undefined;
    const id = String((asked += 1));
    const body = JSON.stringify(ack ? { ...frame, id } : frame);
    if (bytes(body) > link[limit]) return undefined;
    if (!ack) {
      link.send(body);
      return Promise.resolve({ ok: true });
    }
    return new Promise((resolve) => {
      const settle = (reply: Frame | null): void => {
        clearTimeout(timer);
        link.replies.delete(id);
        resolve(reply);
        drain(link);
      };
      const timer = setTimeout(settle, ms, { status: 0, error: "timeout" });
      link.replies.set(id, settle);
      link.send(body);
    });
  };

  const holder = (topic: string): Link | undefined =>
    [...links].find((open) => open.topics.has(topic));

  return {
    name: "ws",
    tag(meta) {
      if (meta) meta.connection = holder(meta.topic)?.connection;
      return meta;
    },
    up(topic, name, data, ack) {
      const reply = ask(
        holder(topic),
        { type: "up", topic, name, data },
        ACK_MS,
        "up",
        ack,
      );
      return reply?.then((frame) => frame?.ok === true) ?? Promise.resolve(false);
    },
    http(target, init, csrf) {
      const pairs = [...new Headers(init.headers)];
      const headers = Object.fromEntries(pairs);
      const body = tunnelBody(init.body, headers);
      const link = [...links].find((open) => open.born === generation && !open.off);
      if (
        link === undefined ||
        pairs.some(
          ([key]) =>
            !link.tunnel.headers.includes(key) &&
            !link.tunnel.prefixes.some((prefix) => key.startsWith(prefix)) &&
            key !== (link.tunnel.csrf ?? csrf?.toLowerCase()),
        ) ||
        body === undefined
      )
        return undefined;
      const url = new URL(target);
      return ask(
        link,
        {
          type: "http",
          method: init.method,
          path: url.pathname + url.search,
          headers,
          body,
        },
        link.tunnel.ms,
        "frame",
      )?.then((reply) => {
        if (reply === null) return "closed";
        if (reply.status !== 0) return tunnelResponse(reply, url);
        const stale = reply.error === REFUSAL_STALE;
        if (reply.error === REFUSAL_DISABLED || (stale && link.renewed))
          link.off = true;
        else if (stale) {
          link.born = -1;
          drain(link);
        }
        return reply.retry === true ? undefined : String(reply.error);
      });
    },
    renew(token) {
      const next = csrfSecret(token);
      if (next === undefined || next === secret) return;
      secret = next;
      generation += 1;
      links.forEach(drain);
    },
    open(url, sink, cursor = new Map()): TransportControl {
      let closed = false;
      let fallbackMode = false;
      let failures = 0;
      let active: TransportControl | undefined;
      let controller: AbortController | undefined;
      let retry: ReturnType<typeof setTimeout> | undefined;
      let link: Link | undefined;
      let heartbeat = 0;
      let watchdog: ReturnType<typeof setTimeout> | undefined;
      let healthy: ReturnType<typeof setTimeout> | undefined;
      let renewing = false;
      let reauthed = false;
      const stopped = (): boolean => closed || fallbackMode;
      const quiet = (): void => {
        clearTimeout(watchdog);
        clearTimeout(healthy);
        const gone = link;
        if (gone === undefined) return;
        link = undefined;
        links.delete(gone);
        for (const settle of gone.replies.values()) settle(null);
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

      const drop = (): void => {
        renewing = false;
        quiet();
        const previous = active;
        active = undefined;
        previous?.close();
        sink.closed("transient");
      };

      const failed = (immediate = false): void => {
        if (stopped()) return;
        drop();
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
          const born = generation;
          const ended = (code: number): void => {
            if (active !== opened) return;
            if (code === 4401 && !reauthed) {
              reauthed = true;
              drop();
              void connect();
              return;
            }
            failed(code === 4401 || code === 4403);
          };
          const opened: TransportControl = socket.open(
            target.url,
            (readySend) => {
              if (stopped()) return;
              link = {
                send: readySend,
                topics: new Set(),
                replies: new Map(),
                born,
                tunnel: tunnelOf({}),
                off: true,
                frame: MAX_FRAME,
                up: MAX_UP,
                renewed: renewing,
                renew: () => {
                  drop();
                  renewing = true;
                  void connect();
                },
              };
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
                reauthed = false;
                const beat = frame.heartbeat;
                heartbeat = typeof beat === "number" && beat > 0 ? beat : 0;
                clearTimeout(watchdog);
                clearTimeout(healthy);
                if (link !== undefined) {
                  link.topics = new Set(
                    isRecord(frame.topics) ? Object.keys(frame.topics) : [],
                  );
                  const tunnel = frame.tunnel;
                  const upstream = isRecord(frame.upstream) ? frame.upstream : {};
                  link.up = positive(upstream.frame, MAX_UP);
                  link.connection = asString(frame.connection);
                  if (isRecord(tunnel)) {
                    link.off = false;
                    link.frame = positive(tunnel.frame, MAX_FRAME);
                    link.tunnel = tunnelOf(tunnel);
                  }
                  links.add(link);
                }
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
              if (frame.type === "patches") {
                if (typeof frame.data === "string" || isRecord(frame.data))
                  sink.patches(frame.data);
              } else if (frame.type === "hello") sink.hello(body);
              else if (frame.type === "skip") sink.skip(body);
              else if (frame.type === "ping") {
                sink.clock(frame.server_time);
                link?.send('{"type":"pong"}');
              } else if (frame.type === "ack" || frame.type === "http")
                link?.replies.get(String(frame.id))?.(frame);
              else if (frame.type === "error" && frame.code === "auth") {
                clearTimeout(watchdog);
                watchdog = setTimeout(ended, NEGOTIATION_MS, 4401);
              }
              if (link !== undefined) drain(link);
            },
            ended,
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

const CSRF_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

// Django re-masks the secret on each get_token(), so only the secret marks a rotation.
export function csrfSecret(token: string | undefined): string | undefined {
  if (token?.length !== 64) return token;
  const at = (i: number): number => CSRF_CHARS.indexOf(token.charAt(i));
  let secret = "";
  for (let i = 0; i < 32; i += 1)
    secret += CSRF_CHARS.charAt((at(i + 32) - at(i) + 62) % 62);
  return secret;
}

function bytes(text: string): number {
  return new TextEncoder().encode(text).length;
}

function positive(value: unknown, fallback: number): number {
  return typeof value === "number" && value > 0 ? value : fallback;
}

function names(value: unknown, fallback: string[]): string[] {
  return Array.isArray(value)
    ? value.filter((name): name is string => typeof name === "string")
    : fallback;
}

function tunnelOf(limits: Frame): Tunnel {
  return {
    ms: (positive(limits.timeout, TUNNEL_S) + MARGIN_S) * 1000,
    headers: names(limits.headers, ["accept", "content-type"]),
    prefixes: names(limits.prefixes, ["x-next-"]),
    csrf: typeof limits.csrf === "string" ? limits.csrf : undefined,
  };
}

function tunnelBody(
  body: RequestInit["body"],
  headers: Record<string, string>,
): string | undefined {
  if (typeof body === "string") return body;
  if (body === undefined || body === null) return "";
  if (!(body instanceof FormData || body instanceof URLSearchParams)) return undefined;
  const fields: string[][] = [];
  for (const [key, value] of body) {
    if (typeof value !== "string") return undefined;
    fields.push([key, value]);
  }
  headers["content-type"] = "application/x-www-form-urlencoded;charset=UTF-8";
  return new URLSearchParams(fields).toString();
}

function tunnelResponse(reply: Frame, url: URL): Response {
  if (typeof reply.status !== "number")
    throw new Error(`tunnel refused the request: ${String(reply.error)}`);
  const headers = new Headers(
    isRecord(reply.headers) ? (reply.headers as Record<string, string>) : {},
  );
  const empty = reply.status === 204 || reply.status === 205 || reply.status === 304;
  const response = new Response(
    empty ? null : typeof reply.body === "string" ? reply.body : "",
    {
      status: reply.status,
      headers,
    },
  );
  const location = headers.get("location");
  if (location !== null && reply.status >= 300 && reply.status < 400) {
    const next = new URL(location, url);
    if (next.origin !== url.origin)
      throw new TypeError("cross-origin redirect refused");
    Object.defineProperties(response, {
      redirected: { value: true },
      url: { value: next.href },
    });
  }
  return response;
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
