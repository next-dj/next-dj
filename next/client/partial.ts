// Assembly of the `Next.partial` surface from the wire and apply modules, and of the
// kernel the chunks install against.

import { Applier, mountNodes } from "./apply";
import type {
  Applied,
  ApplyDeps,
  ApplyHooks,
  ApplyOptions,
  Envelope,
  HistoryAdapter,
  MountCallback,
  OpHandler,
} from "./apply";
import { Wire, none, zoneKey } from "./wire";
import type {
  Clock,
  CsrfPayload,
  EnvelopeOptions,
  FetchAdapter,
  Navigate,
  Outcome,
  ParseHook,
  WireRequest,
} from "./wire";
import { createDirtyTracker } from "./dirty";
import type { FormsFactory } from "./forms";
import type {
  DialogAdapter,
  LayerStack,
  LayersFactory,
  PopStateAdapter,
} from "./layers";
import { createStack } from "./stack";
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
import { defaultHistory, defaultNavigate, defaultPopState } from "./adapters";
import type { Handlers, Hooks, Kernel } from "./kernel";
import { ORDER_LAYERS } from "./kernel";
import {
  ATTR_STREAM,
  CONTENT_TYPE,
  ECHO_LIMIT,
  ORIGIN_FIELD,
  asString,
  currentUrl,
  fire,
  isBusy,
  markBusy,
  matching,
  now,
  requestTimeout,
  stamped,
} from "./protocol";
import type { Diagnostics, WireTransport } from "./protocol";
import type { StreamMeta } from "./stream";

/** The core seams the applier and the fetch layer read from. */
export interface PartialDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  mergeContext: (data: Record<string, unknown>) => void;
  context: () => Record<string, unknown>;
  // The stream bridge, the CSRF token fetch, the poller, the forms and the modal layers,
  // each in its own lazy chunk.
  stream: LazyModule<SseFactory>;
  ws?: LazyModule<WsTransportFactory>;
  csrf: LazyModule<CsrfMint>;
  poll: LazyModule<PollFactory>;
  forms?: LazyModule<FormsFactory>;
  layers: LazyModule<LayersFactory>;
  // What the kernel draws from the facade. Absent, a chunk loads nothing more.
  load?: Kernel["load"];
  module?: Kernel["module"];
  scripts?: Kernel["scripts"];
  nonce?: string | undefined;
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
  reload?: () => void;
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

/** The public `Next.partial` surface. */
export interface PartialSurface {
  apply(raw: unknown, options?: ApplyOptions): Envelope;
  fetch(request: WireRequest | Element): Promise<Outcome>;
  submit(target: HTMLFormElement | SubmitAction): Promise<Outcome>;
  defineOp(name: string, handler: OpHandler): void;
  parseHook(contentType: string, hook: ParseHook): void;
  setCsrf(csrf: CsrfPayload | undefined): void;
  /** Run the post-morph mount pass over markup inserted outside an envelope. */
  mount(nodes: readonly Element[]): void;
  /** Register a mount callback, returning a teardown that unregisters it. */
  onMount(selector: string, callback: (el: Element) => void): () => void;
  layers: LayerStack;
  /** Transport-neutral API over the live stream bridge. */
  stream: StreamSurface;
  /** Run the on-`ready` work, called by the core from the inline `_init`. */
  ready(): void;
  /** Harness seam, absent from the shipped bundle: swap the adapters and rebuild. */
  _configure(adapters: PartialAdapters): void;
  /** Harness seam, absent from the shipped bundle: drop every piece of page state. */
  _reset(): void;
}

/** The partial surface, the kernel of the chunks and the controls of the facade. */
export interface Runtime {
  partial: PartialSurface;
  kernel: Kernel;
  /** Open the dev channel, attaching the diagnostics once their chunk has loaded. */
  dev(diagnostics?: Diagnostics): void;
  /** The current URL, path and title, returned by Next.navigation.current(). */
  current(): NavigationState;
}

type HarnessSeam = "_configure" | "_reset";

/** Build the partial surface and the kernel, wiring the modules onto the shared deps. */
export function createPartial(deps: PartialDeps): Runtime {
  // Live closure state the applier and triggers read through a call. The channel
  // opens from inside the inline `_init`, where a rebuild would take the CSP
  // nonce off that script and drop ops and parse hooks registered before boot.
  let dev = false;
  const readDev = (): boolean => dev;
  let diagnostics: Diagnostics | undefined;
  const readDiagnostics = (): Diagnostics | undefined => diagnostics;
  // Diagnostics that arrive after the ready scan check the already mounted page.
  const diagnose = (enabled: boolean, attached?: Diagnostics): void => {
    dev = enabled;
    const previous = diagnostics;
    diagnostics = enabled ? (attached ?? previous) : undefined;
    if (mounted && diagnostics !== previous) diagnostics?.attrs(document);
  };

  // The handlers the chunks serve and the apply hooks they register, each list kept
  // in the order of the hooks, so the arrival of a bundle never decides it.
  const handlers: Partial<Handlers> = {};
  const orders: { [K in keyof Hooks]: number[] } = { before: [], applied: [] };
  const hooks: { [K in keyof Hooks]: Hooks[K][] } = { before: [], applied: [] };
  const readies: (() => void)[] = [];

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
    stream.scan(root);
    layers.scan(root);
  };

  let assets = createAssets(assetsDeps());
  let history: HistoryAdapter = defaultHistory();
  let navigate: Navigate = defaultNavigate();
  const rotated = (token: string | undefined): void => wsTransport?.renew(token);
  let csrf: Csrf = createCsrf({ mint: deps.csrf, rotated });
  let navigation = createNavigation(navigationDeps());
  let layers = createStack(stackDeps());
  let triggers = createTriggers(triggerDeps());
  // The SSE bridge once its chunk has loaded, built with the adapters configured last.
  let bridge: Sse | undefined;
  let wsTransport: SocketTransport | undefined;
  // The adapters configured last, which a rebuilt bridge or wire takes.
  let configured: PartialAdapters | undefined;
  // The request ids sent before the bridge is built. A stream that opens once its
  // chunk lands can still carry their echoes, so the bridge takes them over.
  let unbridged: string[] = [];
  // Bumped on every reset of the bridge, so a chunk landing late scans no stale root.
  let epoch = 0;
  const live = (factory: SseFactory): Sse => {
    if (bridge === undefined) {
      bridge = factory(sseDeps(configured));
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
  // Fetched together, and the bridge waits on ws since it reads that factory once.
  const withBridge = (callback: (factory: SseFactory) => void): void => {
    const ws = socketFirst() ? deps.ws?.load() : undefined;
    deps.stream.whenLoaded((factory) =>
      ws === undefined ? callback(factory) : void ws.then(() => callback(factory)),
    );
  };
  const tag = <T extends StreamMeta | undefined>(meta: T): T =>
    wsTransport?.tag(meta) ?? meta;
  // A page with no stream never fetches the bridge.
  const stream = {
    scan(root) {
      if (matching(root, `[${ATTR_STREAM}]:not([${ATTR_STREAM}=""])`).length === 0)
        return;
      const started = epoch;
      withBridge((factory) => {
        if (started === epoch) live(factory).scan(root);
      });
    },
    remember(id) {
      if (bridge !== undefined) bridge.remember(id);
      else unbridged = [...unbridged, id].slice(-ECHO_LIMIT);
    },
    forget(id) {
      bridge?.forget(id);
      unbridged = unbridged.filter((kept) => kept !== id);
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
  } satisfies Omit<StreamSurface, "_reset"> as StreamSurface;
  TEST: stream._reset = () => {
    bridge?._reset();
    unbridged = [];
    epoch += 1;
  };
  let wireGeneration = 0;
  let applier = new Applier(applyDeps());
  let wire = new Wire(wireDeps());
  let detachLayers = layers.install(document, defaultPopState());
  let detachTriggers = triggers.install(document);
  let detachCsrf = csrf.install(document);

  function hook<K extends keyof Hooks>(phase: K, order: number, run: Hooks[K]): void {
    const at = orders[phase].filter((held) => held <= order).length;
    orders[phase].splice(at, 0, order);
    hooks[phase].splice(at, 0, run);
  }

  hook("before", ORDER_LAYERS, (detail) => layers.before(detail));

  // Each deps object spreads the adapters first, since a module reads only the seams
  // it names, and the runtime's own keys follow so an adapter cannot shadow them.
  function navigationDeps(adapters?: PartialAdapters) {
    return {
      ...adapters,
      dispatch: deps.dispatch,
      history,
      answer: () => handlers.pop?.(),
    };
  }

  // Every runtime event fires on the document and the bus alike. The wire, assets and
  // stream report through here, the other modules fire both themselves.
  function announce(adapters?: PartialAdapters) {
    const doc = adapters?.document ?? document;
    return (event: string, detail: Record<string, unknown>, cancelable?: boolean) =>
      fire(doc, deps.dispatch, event, detail, cancelable);
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
      slot: (target) => handlers.slot?.(target),
      hooks: hooks satisfies ApplyHooks,
    };
  }

  function stackDeps(adapters?: PartialAdapters) {
    return {
      ...adapters,
      dispatch: deps.dispatch,
      chunk: deps.layers,
      build: (factory: LayersFactory) =>
        factory({
          ...adapters,
          dispatch: deps.dispatch,
          // The applier's navigation, whose commit announces the push a layer holds.
          navigation,
          fetch: (request: WireRequest) => wire.fetch(request),
          abort: (key: string) => wire.abort(key),
          take: (href: string, zone: string) => handlers.take?.(href, zone),
          mark: markBusy,
        }),
      popped: () => navigation.popped(() => undefined),
      navigate: (href: string) => navigate(href),
    };
  }

  function triggerDeps(adapters?: PartialAdapters) {
    return {
      ...adapters,
      fetch: (request: WireRequest) => wire.fetch(request),
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
      forms: deps.forms,
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
        wire.abort(zoneKey(request.url, request.zone)),
      dispatch: announce(adapters),
      pageUrl: (el: Element) => layers.urlFor(el),
      release: (current: boolean) => assets.release(current),
    };
  }

  function wireDeps(adapters?: PartialAdapters) {
    const generation = wireGeneration;
    return {
      // The fetch, document, navigate, and the session store the asset guard uses.
      ...adapters,
      active: () => wireGeneration === generation,
      dispatch: announce(adapters),
      onEnvelope: (raw: unknown, options: EnvelopeOptions) =>
        new Promise<Applied>((done) => {
          // A mutation scopes no zones, but its meta op belongs to the submitting page.
          const envelope = applier.apply(raw, { ...options, done });
          // A csrf meta rotates the token so the next mutation submits the fresh
          // one, not just the forms already in the document.
          if (envelope.csrf) surface.setCsrf(envelope.csrf);
        }),
      version: () => assets.version(),
      // Read per request, so the $timeout of the _init payload applies once it lands.
      timeout: () => requestTimeout(deps.context().$timeout),
      csrf: {
        current: () => csrf.current(),
        ensure: (signal?: AbortSignal) => csrf.ensure(signal),
      },
      dirtySnapshot: () => dirty.snapshot(),
      // Feeds the ring id to the SSE bridge so the echo stream event drops.
      rememberRequestId: (id: string) => stream.remember(id),
      forgetRequestId: (id: string) => stream.forget(id),
      tunnel: (target: string, init: RequestInit, header?: string) =>
        wsTransport?.http(target, init, header),
      resolveZone: (name: string, root: ParentNode, page?: string) =>
        layers.resolveZone(name, root, page),
      reader: () => (deps.context().$stream === true ? handlers.read : undefined),
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

  const surface = {
    apply(raw, options) {
      return applier.apply(raw, options);
    },
    fetch(request) {
      if (!(request instanceof Element)) return wire.fetch(request);
      const served = handlers.fetch;
      if (served) return served(request);
      // An element is fetched by the boundaries bundle, which this page did not load.
      diagnostics?.unserved(request);
      return Promise.resolve(none("dropped"));
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
    get layers(): LayerStack {
      return layers;
    },
    get stream() {
      return stream;
    },
    ready() {
      assets.pin(asString(deps.context().$version) ?? "");
      assets.seed();
      runMount(document);
      mounted = true;
      for (const listener of readies) listener();
    },
  } satisfies Omit<PartialSurface, HarnessSeam> as PartialSurface;

  const kernel: Kernel = {
    partial: surface,
    context: deps.context,
    nonce: deps.nonce,
    onReady(listener) {
      readies.push(listener);
      if (mounted) listener();
    },
    scripts: deps.scripts ?? (() => Promise.reject(new Error("[next] no scripts"))),
    load: deps.load ?? (() => Promise.resolve(undefined)),
    module: deps.module ?? (() => undefined),
    serve(name, handler) {
      (handlers as Partial<Record<typeof name, typeof handler>>)[name] ??= handler;
    },
    served: (name) => handlers[name],
    hook,
    fire: announce(),
    mark: markBusy,
    busy: isBusy,
    stamped,
    now,
    headers: (headers) => wire.headers({ url: "", headers }, "GET"),
    arm: (controller) => wire.arm(controller),
    parse: (contentType, body) => wire.parseStream(contentType, body),
    sweep() {
      triggers.sweep();
      wire.sweep();
    },
    release: (current) => assets.release(current),
    watch: (el, send) => triggers.watch(el, send),
    fallbacks: new WeakMap(),
    diagnostics: readDiagnostics,
  };

  TEST: {
    // A dev-only adapter object swaps nothing in, so it must not cost a rebuild.
    const devKeys = new Set(["dev", "diagnostics"]);
    surface._configure = (adapters) => {
      diagnose(adapters.dev ?? false, adapters.diagnostics);
      if (Object.keys(adapters).every((key) => devKeys.has(key))) return;
      // A reply from the outgoing seams must not apply to the replacement runtime.
      wireGeneration += 1;
      wire._reset();
      csrf.cancelPending();
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
      stream._reset();
      // The token outlives the rebuild, only the fetch seam it mints through changes.
      const token = csrf.current();
      csrf = createCsrf({ ...adapters, mint: deps.csrf, rotated });
      csrf.set(token);
      navigation = createNavigation(navigationDeps(adapters));
      layers = createStack(stackDeps(adapters));
      triggers = createTriggers(triggerDeps(adapters));
      bridge = undefined;
      wsTransport = undefined;
      configured = adapters;
      applier = new Applier(applyDeps(adapters));
      wire = new Wire(wireDeps(adapters));
      const doc = adapters.document ?? document;
      detachLayers = layers.install(doc, adapters.popstate ?? defaultPopState());
      detachTriggers = triggers.install(doc);
      detachCsrf = csrf.install(doc);
    };
    surface._reset = () => {
      wireGeneration += 1;
      wire._reset();
      wire = new Wire(wireDeps(configured));
      applier._reset();
      dirty._reset();
      layers._reset();
      triggers._reset();
      assets._reset();
      stream._reset();
      mounts.length = 0;
      readies.length = 0;
      mounted = false;
      csrf._reset();
    };
  }

  return {
    partial: surface,
    kernel,
    dev: (attached) => diagnose(true, attached),
    current: () => navigation.current(),
  };
}
