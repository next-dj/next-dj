// The SSE transport maps EventSource frames onto the shared stream protocol.

import { defaultEventSource } from "./adapters";
import { createStream } from "./stream";
import type {
  Stream,
  StreamDeps,
  StreamFactory,
  StreamMeta,
  Transport,
  TransportControl,
  VisibilityAdapter,
} from "./stream";
import { SSE_END, SSE_HELLO, SSE_SKIP } from "./protocol";
import type { WsTransportFactory } from "./ws";

/** The EventSource seam over the native API, which jsdom does not model. */
export interface EventSourceAdapter {
  open(
    url: string,
    onMessage: (data: string) => void,
    onError: (fatal: boolean) => void,
    onControl?: (name: string, data: string) => void,
    onOpen?: () => void,
  ): TransportControl;
}

/** The seams the SSE factory draws on, injectable so jsdom-blind pieces stay testable. */
export type SseDeps = Omit<StreamDeps, "transport"> & {
  source?: EventSourceAdapter;
  websocket?: WsTransportFactory | undefined;
  useWebSocket?: boolean;
};

/** The bridge handle scanned per apply and fed own request ids by the wire. */
export type Sse = Stream;

export type SseFactory = (deps: SseDeps) => Sse;
export type SourceControl = TransportControl;

/** Build the SSE bridge over the given seams. */
export function createSse(deps: SseDeps): Sse {
  const fallback = createSseTransport(deps.source ?? defaultEventSource());
  return createStream({
    ...deps,
    transport:
      deps.useWebSocket && deps.websocket !== undefined
        ? deps.websocket({
            fallback,
            origin: deps.document?.location.origin,
          })
        : fallback,
  });
}

/** Build the EventSource adapter used by the stream state machine. */
export function createSseTransport(source: EventSourceAdapter): Transport {
  return {
    name: "sse",
    open(url, sink): TransportControl {
      return source.open(
        url,
        sink.patches,
        (fatal) => sink.closed(fatal ? "fatal" : "transient"),
        (name, data) => {
          if (name === SSE_HELLO) sink.hello(data);
          else if (name === SSE_SKIP) sink.skip(data);
          else if (name === SSE_END) sink.end(data);
        },
        sink.opened,
      );
    },
  };
}

export type { StreamFactory, StreamMeta, VisibilityAdapter };
