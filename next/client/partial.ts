// Assembly of the `Next.partial` surface from the wire and apply modules.

import { Applier, mountNodes } from "./apply";
import type {
  ApplyDeps,
  Envelope,
  HistoryAdapter,
  MountCallback,
  OpHandler,
} from "./apply";
import { Wire } from "./wire";
import type {
  Clock,
  CsrfPayload,
  FetchAdapter,
  Navigate,
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
import { createCsrf } from "./csrf";
import type { Csrf, CsrfMint } from "./csrf";
import type { LazyModule } from "./chunks";
import type { PollFactory } from "./poll";
import { createNavigation } from "./navigation";
import type { NavigationState } from "./navigation";
import { defaultHistory, defaultNavigate } from "./adapters";
import { ATTR_SSE, currentUrl, fire, matching } from "./protocol";
import type { Diagnostics } from "./protocol";

/** The core seams the applier and the fetch layer read from. */
export interface PartialDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  mergeContext: (data: Record<string, unknown>) => void;
  // The stream bridge, the CSRF mint, and the poller, each held by its own lazy chunk.
  sse: LazyModule<SseFactory>;
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
  // The dev channel, handed over once next.dev.min.js lands.
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
  visibility?: VisibilityAdapter;
}

/** The public `Next.partial` surface plus its underscore-prefixed test seams. */
export interface PartialSurface {
  apply(raw: unknown): Envelope;
  fetch(request: WireRequest): Promise<void>;
  defineOp(name: string, handler: OpHandler): void;
  parseHook(contentType: string, hook: ParseHook): void;
  setCsrf(csrf: CsrfPayload | undefined): void;
  /** Where the page stands, what Next.navigation.current() answers. */
  _current(): NavigationState;
  /** Run the post-morph mount pass over markup inserted outside an envelope. */
  mount(nodes: readonly Element[]): void;
  /** Register a mount callback, returning a teardown that unregisters it. */
  onMount(selector: string, callback: (el: Element) => void): () => void;
  layers: LayerStack;
  sse: Sse;
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
  let csrf: Csrf = createCsrf({ mint: deps.csrf });
  let navigation = createNavigation(navigationDeps());
  let layers = createLayers(layerDeps());
  let triggers = createTriggers(triggerDeps());
  // The bridge once its chunk landed, built over the adapters configured last.
  let bridge: Sse | undefined;
  let sseAdapters: PartialAdapters | undefined;
  const live = (): Sse | undefined => {
    const factory = deps.sse.get();
    if (bridge === undefined && factory !== undefined) {
      bridge = factory(sseDeps(sseAdapters));
    }
    return bridge;
  };
  // A page with no stream never fetches the bridge. An id remembered before it lands
  // answers a mutation no stream it opens was subscribed for, so none is kept.
  const sse: Sse = {
    scan(root) {
      if (matching(root, `[${ATTR_SSE}]`).length === 0) return;
      const landed = live();
      if (landed !== undefined) landed.scan(root);
      else void deps.sse.load().then(() => live()?.scan(root));
    },
    remember: (id) => bridge?.remember(id),
    size: () => bridge?.size() ?? 0,
    _reset: () => bridge?._reset(),
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
      // The visit verb rides this hard-navigation seam, the url verb rides navigation.
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
      // Shares the applier's navigation, whose commit writes the push a layer holds.
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
      // A filter submit syncs the address bar through the navigation, so it
      // announces like any other write and never goes behind the runtime's back.
      history: navigation.asHistory(),
      diagnostics: readDiagnostics,
      poll: deps.poll,
    };
  }

  function sseDeps(adapters?: PartialAdapters) {
    return {
      ...adapters,
      // A stream event carries no dirty snapshot, so the server value wins.
      apply: (raw: unknown, page: string) => void applier.apply(raw, { owner: page }),
      fetch: (request: WireRequest) => void wire.fetch(request),
      dispatch: announce(adapters),
      pageUrl: (el: Element) => layers.urlFor(el),
    };
  }

  function wireDeps(adapters?: PartialAdapters) {
    return {
      // The fetch, document, navigate, and the same reload-once store the asset
      // guard uses.
      ...adapters,
      dispatch: announce(adapters),
      onEnvelope: (
        raw: unknown,
        _response: Response,
        snapshot: number,
        key: string | undefined,
        page: string | undefined,
      ) => {
        const envelope = applier.apply(raw, { snapshot, key, page });
        // A csrf meta rotates the token so the next mutation submits the fresh
        // one, not just the forms already in the document.
        if (envelope.csrf) csrf.set(envelope.csrf);
      },
      version: () => assets.version(),
      csrf: { current: () => csrf.current(), ensure: () => csrf.ensure() },
      dirtySnapshot: () => dirty.snapshot(),
      // Feeds the ring id to the SSE bridge so the echo stream event drops.
      rememberRequestId: (id: string) => sse.remember(id),
    };
  }

  const surface: PartialSurface = {
    apply(raw) {
      return applier.apply(raw);
    },
    fetch(request) {
      return wire.fetch(request);
    },
    defineOp(name, handler) {
      applier.defineOp(name, handler);
    },
    parseHook(contentType, hook) {
      wire.parseHook(contentType, hook);
    },
    setCsrf(next) {
      csrf.set(next);
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
        for (const el of Array.from(document.querySelectorAll(selector))) {
          callback(el);
        }
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
      // A dev chunk landing after the ready scan catches up over the mounted page.
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
      csrf = createCsrf({ ...adapters, mint: deps.csrf });
      csrf.set(token);
      navigation = createNavigation(navigationDeps(adapters));
      layers = createLayers(layerDeps(adapters));
      triggers = createTriggers(triggerDeps(adapters));
      bridge = undefined;
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
