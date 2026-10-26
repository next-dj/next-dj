// Assembly of the `Next.partial` surface from the wire and apply modules.

import { Applier, mountNodes } from "./apply";
import type {
  Applied,
  ApplyDeps,
  Envelope,
  HistoryAdapter,
  MountCallback,
  OpHandler,
} from "./apply";
import { Wire, none } from "./wire";
import type {
  Clock,
  CsrfPayload,
  FetchAdapter,
  Navigate,
  Outcome,
  ParseHook,
  WireRequest,
} from "./wire";
import { createDirtyTracker } from "./dirty";
import { createLayers } from "./layers";
import type { DialogAdapter, LayerStack, PopStateAdapter } from "./layers";
import { createAssets } from "./assets";
import type { LinkLoader, SessionStore } from "./assets";
import { createTriggers } from "./triggers";
import type { ConfirmAdapter, IntersectionAdapter } from "./triggers";
import type { EventSourceAdapter, Sse, SseFactory, VisibilityAdapter } from "./sse";
import type { SocketTransport, WebSocketAdapter, WsTransportFactory } from "./ws";
import { createCsrf } from "./csrf";
import type { Csrf, CsrfMint } from "./csrf";
import type { LazyModule } from "./chunks";
import type { PollFactory } from "./poll";
import { createNavigation } from "./navigation";
import type { NavigationState } from "./navigation";
import { defaultHistory, defaultNavigate } from "./adapters";
import {
  ATTR_SSE,
  ATTR_STREAM,
  CONTENT_TYPE,
  ECHO_LIMIT,
  ORIGIN_FIELD,
  currentUrl,
  fire,
  matching,
} from "./protocol";
import type { Diagnostics, WireTransport } from "./protocol";
import type { StreamMeta } from "./stream";

/** The core seams the applier and the fetch layer read from. */
export interface PartialDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  mergeContext: (data: Record<string, unknown>) => void;
  context: () => Record<string, unknown>;
  // The stream bridge, the CSRF token fetch and the poller, each in its own lazy chunk.
  stream: LazyModule<SseFactory>;
  ws?: LazyModule<WsTransportFactory>;
  csrf: LazyModule<CsrfMint>;
  poll: LazyModule<PollFactory>;
}

/** The injectable platform seams, each overridable by the test harness. */
export interface PartialAdapters {
  fetch?: FetchAdapter;
  clock?: Clock;
  navigate?: Navigate;
  document?: Document;
  dev?: boolean;
  // The dev diagnostics, set once next.dev.min.js has loaded.
  diagnostics?: Diagnostics;
  dialog?: DialogAdapter;
  history?: HistoryAdapter;
  popstate?: PopStateAdapter;
  loadLink?: LinkLoader;
  observer?: IntersectionAdapter;
  session?: SessionStore;
  confirm?: ConfirmAdapter;
  cssTimeoutMs?: number;
  source?: EventSourceAdapter;
  websocket?: WebSocketAdapter;
  visibility?: VisibilityAdapter;
}

export type StreamSurface = Sse & {
  send(
    topic: string,
    name: string,
    data: unknown,
    options?: { ack?: boolean },
  ): Promise<boolean>;
};

type SubmitValue = string | number | boolean | Blob;

/** An action POST built without a form, the `{% action_url %}` URL and its fields. */
export interface SubmitAction {
  action: string;
  data?: FormData | Record<string, SubmitValue>;
  zone?: string;
  transport?: WireTransport;
  key?: string;
  navigate?: boolean;
  from?: Element;
}

/** The public `Next.partial` surface plus its underscore-prefixed test seams. */
export interface PartialSurface {
  apply(raw: unknown): Envelope;
  fetch(request: WireRequest): Promise<Outcome>;
  submit(target: HTMLFormElement | SubmitAction): Promise<Outcome>;
  defineOp(name: string, handler: OpHandler): void;
  parseHook(contentType: string, hook: ParseHook): void;
  setCsrf(csrf: CsrfPayload | undefined): void;
  /** The current URL, path and title, returned by Next.navigation.current(). */
  _current(): NavigationState;
  /** Run the post-morph mount pass over markup inserted outside an envelope. */
  mount(nodes: readonly Element[]): void;
  /** Register a mount callback, returning a teardown that unregisters it. */
  onMount(selector: string, callback: (el: Element) => void): () => void;
  layers: LayerStack;
  sse: StreamSurface;
  /** Transport-neutral stream API; `sse` remains an alias during the migration. */
  stream: StreamSurface;
  /** Run the on-`ready` work, called by the core from the inline `_init`. */
  ready(): void;
  /** Configure the adapters and rebuild the wire and applier. */
  _configure(adapters: PartialAdapters): void;
  _reset(): void;
}

// A dev-only adapter object swaps nothing in, so it must not cost a rebuild.
const DEV_KEYS = new Set(["dev", "diagnostics"]);

function onlyDev(adapters: PartialAdapters): boolean {
  return Object.keys(adapters).every((key) => DEV_KEYS.has(key));
}

/** Build the partial surface, wiring the sub-modules onto the shared deps. */
export function createPartial(deps: PartialDeps): PartialSurface {
  // Live closure state the applier and triggers read through a call. The channel
  // opens from inside the inline `_init`, where a rebuild would take the CSP
  // nonce off that script and drop ops and parse hooks registered before boot.
  let dev = false;
  const readDev = (): boolean => dev;
  let diagnostics: Diagnostics | undefined;
  const readDiagnostics = (): Diagnostics | undefined => diagnostics;

  const dirty = createDirtyTracker();
  dirty.install(document);

  // Shared by onMount and triggers. The `mounted` flag records whether the
  // initial `ready` pass has run, so a late callback catches up over the DOM.
  const mounts: { selector: string; callback: (el: Element) => void }[] = [];
  let mounted = false;
  const runMount: MountCallback = (root) => {
    for (const entry of mounts) {
      for (const el of matching(root, entry.selector)) entry.callback(el);
    }
    triggers.scan(root);
    sse.scan(root);
  };

  let assets = createAssets(assetsDeps());
  let history: HistoryAdapter = defaultHistory();
  let navigate: Navigate = defaultNavigate();
  const rotated = (token: string | undefined): void => wsTransport?.renew(token);
  let csrf: Csrf = createCsrf({ mint: deps.csrf, rotated });
  let navigation = createNavigation(navigationDeps());
  let layers = createLayers(layerDeps());
  let triggers = createTriggers(triggerDeps());
  // The SSE bridge once its chunk has loaded, built with the adapters configured last.
  let bridge: Sse | undefined;
  let wsTransport: SocketTransport | undefined;
  let sseAdapters: PartialAdapters | undefined;
  // The request ids sent before the bridge is built. A stream that opens once its
  // chunk lands can still carry their echoes, so the bridge takes them over.
  let unbridged: string[] = [];
  // Bumped on every reset of the bridge, so a chunk landing late scans no stale root.
  let epoch = 0;
  const live = (factory: SseFactory): Sse => {
    if (bridge === undefined) {
      bridge = factory(sseDeps(sseAdapters));
      for (const id of unbridged) bridge.remember(id);
      unbridged = [];
    }
    return bridge;
  };
  const realtime = () =>
    deps.context().$realtime as
      | { content_type?: string; transports?: unknown[] }
      | undefined;
  const socketFirst = (): boolean => realtime()?.transports?.includes("ws") === true;
  const withBridge = (callback: (factory: SseFactory) => void): void => {
    const loadStream = (): void => deps.stream.whenLoaded(callback);
    if (socketFirst() && deps.ws !== undefined) {
      void deps.ws.load().then(loadStream);
    } else loadStream();
  };
  const tag = <T extends StreamMeta | undefined>(meta: T): T =>
    wsTransport?.tag(meta) ?? meta;
  // A page with no stream never fetches the bridge.
  const sse: StreamSurface = {
    scan(root) {
      if (matching(root, `[${ATTR_SSE}], [${ATTR_STREAM}]`).length === 0) return;
      const started = epoch;
      withBridge((factory) => {
        if (started === epoch) live(factory).scan(root);
      });
    },
    remember(id) {
      if (bridge !== undefined) bridge.remember(id);
      else unbridged = [...unbridged, id].slice(-ECHO_LIMIT);
    },
    size: () => bridge?.size() ?? 0,
    now: () => bridge?.now() ?? Date.now(),
    offset: () => bridge?.offset() ?? 0,
    on(topic, name, callback) {
      const started = epoch;
      let active = true;
      let unsubscribe: (() => void) | undefined;
      withBridge((factory) => {
        if (active && started === epoch) {
          unsubscribe = live(factory).on(topic, name, (detail, meta) =>
            callback(detail, tag(meta)),
          );
        }
      });
      return () => {
        active = false;
        unsubscribe?.();
      };
    },
    meta: () => tag(bridge?.meta()),
    send: (topic, name, data, options) =>
      wsTransport?.up(topic, name, data, options?.ack !== false) ??
      Promise.resolve(false),
    _reset() {
      bridge?._reset();
      unbridged = [];
      epoch += 1;
    },
  };
  let applier = new Applier(applyDeps());
  let wire = new Wire(wireDeps());
  let detachLayers = layers.install(document);
  let detachTriggers = triggers.install(document);
  let detachCsrf = csrf.install(document);

  // Each deps object spreads the adapters first, since a module reads only the seams
  // it names, and the runtime's own keys follow so an adapter cannot shadow them.
  function navigationDeps(adapters?: PartialAdapters) {
    return { ...adapters, dispatch: deps.dispatch, history };
  }

  // Every runtime event fires on the document and the bus alike. The wire, assets and
  // stream report through here, the other modules fire both themselves.
  function announce(adapters?: PartialAdapters) {
    const doc = adapters?.document ?? document;
    return (event: string, detail: Record<string, unknown>): void =>
      fire(doc, deps.dispatch, event, detail);
  }

  function assetsDeps(adapters?: PartialAdapters) {
    return { ...adapters, dispatch: announce(adapters) };
  }

  function applyDeps(adapters?: PartialAdapters): ApplyDeps {
    return {
      dispatch: deps.dispatch,
      mergeContext: deps.mergeContext,
      ...adapters,
      dev: readDev,
      diagnostics: readDiagnostics,
      dirtySince: (snapshot) => dirty.isDirtySince(snapshot),
      isTouched: (el) => dirty.isTouched(el),
      // Every seam _configure rebuilds is read through a call, so the order in which
      // it rebuilds the stack and the applier cannot decide what this one sees.
      layers: () => layers,
      navigation: () => navigation,
      // The visit verb uses this full-navigation seam, the url verb uses navigation.
      navigate: () => navigate,
      assets: () => assets,
      mount: { run: runMount },
      refresh: (request) => void wire.fetch(request),
      here: () => currentUrl(adapters?.document ?? document),
    };
  }

  function layerDeps(adapters?: PartialAdapters) {
    return {
      ...adapters,
      dispatch: deps.dispatch,
      // The applier's navigation, whose commit announces the push a layer holds.
      navigation,
      fetch: (request: WireRequest) => wire.fetch(request),
      abort: (key: string) => wire.abort(key),
    };
  }

  function triggerDeps(adapters?: PartialAdapters) {
    return {
      ...adapters,
      fetch: (request: WireRequest) => void wire.fetch(request),
      abort: (key: string) => wire.abort(key),
      // The owning page of an element, so a base-page zone keeps GETting the
      // host URL while a modal layer holds the address bar.
      pageUrl: (el: Element) => layers.urlFor(el),
      // The host page of the layer a form sits in, so a mutation fired from inside a
      // modal stamps the origin the server resolves its zones against.
      layerHost: (el: Element) => layers.hostFor(el),
      rewrite: (el: Element, href: string) => layers.rewrite(el, href),
      // A filter submit updates the URL through the navigation, so it is announced
      // like any other write.
      history: navigation.asHistory(),
      diagnostics: readDiagnostics,
      poll: deps.poll,
    };
  }

  function sseDeps(adapters?: PartialAdapters) {
    const { websocket: socket, ...streamAdapters } = adapters ?? {};
    const contentType = realtime()?.content_type ?? CONTENT_TYPE;
    const wsFactory = deps.ws?.get();
    return {
      ...streamAdapters,
      websocket:
        wsFactory &&
        ((options: Parameters<WsTransportFactory>[0]) =>
          (wsTransport = wsFactory({
            ...options,
            fetch: adapters?.fetch,
            socket,
            token: csrf.current()?.token,
          }))),
      useWebSocket: socketFirst(),
      // A stream event carries no dirty snapshot, so the server value wins.
      apply: (raw: unknown, page: string) => void applier.apply(raw, { owner: page }),
      parse: (body: string) => wire.parseStream(contentType, body),
      fetch: (request: WireRequest) => wire.fetch(request),
      cancel: (request: { url: string; zone: string }) =>
        wire.abort(`${request.url.split("?")[0]} ${request.zone}`),
      dispatch: announce(adapters),
      pageUrl: (el: Element) => layers.urlFor(el),
    };
  }

  function wireDeps(adapters?: PartialAdapters) {
    return {
      // The fetch, document, navigate, and the session store the asset guard uses.
      ...adapters,
      dispatch: announce(adapters),
      onEnvelope: (
        raw: unknown,
        _response: Response,
        snapshot: number,
        key: string | undefined,
        page: string | undefined,
        owner: string | undefined,
      ) =>
        new Promise<Applied>((done) => {
          // A mutation scopes no zones, but its meta op belongs to the submitting page.
          const envelope = applier.apply(raw, { snapshot, key, page, owner, done });
          // A csrf meta rotates the token so the next mutation submits the fresh
          // one, not just the forms already in the document.
          if (envelope.csrf) surface.setCsrf(envelope.csrf);
        }),
      version: () => assets.version(),
      csrf: { current: () => csrf.current(), ensure: () => csrf.ensure() },
      dirtySnapshot: () => dirty.snapshot(),
      // Feeds the ring id to the SSE bridge so the echo stream event drops.
      rememberRequestId: (id: string) => sse.remember(id),
      tunnel: (target: string, init: RequestInit, header?: string) =>
        wsTransport?.http(target, init, header),
    };
  }

  function fields(data: SubmitAction["data"], origin: string): FormData {
    const body = new FormData();
    for (const [name, value] of data instanceof FormData
      ? data
      : Object.entries(data ?? {}))
      body.append(name, value instanceof Blob ? value : String(value));
    if (!body.has(ORIGIN_FIELD)) body.set(ORIGIN_FIELD, origin);
    return body;
  }

  const surface: PartialSurface = {
    apply(raw) {
      return applier.apply(raw);
    },
    fetch(request) {
      return wire.fetch(request);
    },
    async submit(target) {
      if (target instanceof HTMLFormElement)
        return wire.fetch({ ...triggers.submission(target), wait: true });
      try {
        const { from, data, ...action } = target;
        const sender = from ? triggers.sender(from) : {};
        return await wire.fetch({
          ...action,
          ...sender,
          url: action.action,
          method: "POST",
          uid: action.key ?? action.action,
          body: fields(data, sender.owner ?? currentUrl(document)),
          wait: action.key === undefined || "latest",
        });
      } catch (error) {
        return { ...none("error"), error: { kind: "network", error } };
      }
    },
    defineOp(name, handler) {
      applier.defineOp(name, handler);
    },
    parseHook(contentType, hook) {
      wire.parseHook(contentType, hook);
    },
    setCsrf(next) {
      csrf.set(next);
      rotated(next?.token);
    },
    mount(nodes) {
      mountNodes(nodes, { run: runMount });
    },
    onMount(selector, callback) {
      const entry = { selector, callback };
      mounts.push(entry);
      // A callback registered after `ready` catches up over the present DOM,
      // mirroring `Next.on("ready")` for late subscribers.
      if (mounted) {
        for (const el of matching(document, selector)) callback(el);
      }
      return () => {
        const index = mounts.indexOf(entry);
        if (index !== -1) mounts.splice(index, 1);
      };
    },
    get layers() {
      return layers;
    },
    get sse() {
      return sse;
    },
    get stream() {
      return sse;
    },
    _current: () => navigation.current(),
    ready() {
      assets.seed();
      runMount(document);
      mounted = true;
      triggers.ready();
    },
    _configure(adapters) {
      dev = adapters.dev ?? false;
      const previous = diagnostics;
      diagnostics = dev ? (adapters.diagnostics ?? previous) : undefined;
      // Diagnostics that arrive after the ready scan check the already mounted page.
      if (mounted && diagnostics !== previous) diagnostics?.attrs(document);
      if (onlyDev(adapters)) return;
      if (adapters.document !== undefined) dirty.install(adapters.document);
      if (adapters.history !== undefined) history = adapters.history;
      if (adapters.navigate !== undefined) navigate = adapters.navigate;
      // The outgoing registry may still watch the old document's parse.
      assets._reset();
      assets = createAssets(assetsDeps(adapters));
      detachLayers();
      detachTriggers();
      detachCsrf();
      // Stop the old pollers and observers, or they orphan onto the live wire.
      triggers._reset();
      sse._reset();
      // The token outlives the rebuild, only the fetch seam it mints through changes.
      const token = csrf.current();
      csrf = createCsrf({ ...adapters, mint: deps.csrf, rotated });
      csrf.set(token);
      navigation = createNavigation(navigationDeps(adapters));
      layers = createLayers(layerDeps(adapters));
      triggers = createTriggers(triggerDeps(adapters));
      bridge = undefined;
      wsTransport = undefined;
      sseAdapters = adapters;
      applier = new Applier(applyDeps(adapters));
      wire = new Wire(wireDeps(adapters));
      const doc = adapters.document ?? document;
      detachLayers = layers.install(doc);
      detachTriggers = triggers.install(doc);
      detachCsrf = csrf.install(doc);
    },
    _reset() {
      wire._reset();
      applier._reset();
      dirty._reset();
      layers._reset();
      triggers._reset();
      assets._reset();
      sse._reset();
      mounts.length = 0;
      mounted = false;
      csrf._reset();
    },
  };

  return surface;
}
