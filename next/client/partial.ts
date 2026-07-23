// Assembly of the `Next.partial` surface from the wire and apply modules.

import { Applier } from "./apply";
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
import { createSse } from "./sse";
import type { EventSourceAdapter, Sse, VisibilityAdapter } from "./sse";
import { defaultHistory, defaultNavigate } from "./adapters";
import { currentUrl, matching } from "./protocol";

/** The core seams the applier and the fetch layer read from. */
export interface PartialDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  mergeContext: (data: Record<string, unknown>) => void;
}

/** The injectable platform seams, each overridable by the test harness. */
export interface PartialAdapters {
  fetch?: FetchAdapter;
  clock?: Clock;
  navigate?: Navigate;
  document?: Document;
  dev?: boolean;
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

// An absent adapter contributes no key, so the deps object never carries an
// explicit undefined that exactOptionalPropertyTypes would reject.
function opt<K extends string, V>(key: K, value: V | undefined): Partial<Record<K, V>> {
  return value === undefined ? {} : ({ [key]: value } as Record<K, V>);
}

// A dev-only adapter object swaps nothing in, so it must not cost a rebuild.
function onlyDev(adapters: PartialAdapters): boolean {
  return Object.keys(adapters).every((key) => key === "dev");
}

/** Build the partial surface, wiring the sub-modules onto the shared deps. */
export function createPartial(deps: PartialDeps): PartialSurface {
  let csrf: CsrfPayload | undefined;

  // Live closure state the applier and triggers read through a call. The channel
  // opens from inside the inline `_init`, where a rebuild would take the CSP
  // nonce off that script and drop ops and parse hooks registered before boot.
  let dev = false;
  const readDev = (): boolean => dev;

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
  let layers = createLayers(layerDeps());
  let triggers = createTriggers(triggerDeps());
  let sse = createSse(sseDeps());
  let applier = new Applier(applyDeps());
  let wire = new Wire(wireDeps());
  let detachLayers = layers.install(document);
  let detachTriggers = triggers.install(document);

  function assetsDeps(adapters?: PartialAdapters) {
    return {
      dispatch: deps.dispatch,
      ...opt("document", adapters?.document),
      ...opt("clock", adapters?.clock),
      ...opt("loadLink", adapters?.loadLink),
      ...opt("navigate", adapters?.navigate),
      ...opt("session", adapters?.session),
      ...opt("cssTimeoutMs", adapters?.cssTimeoutMs),
    };
  }

  function applyDeps(adapters?: PartialAdapters): ApplyDeps {
    return {
      dispatch: deps.dispatch,
      mergeContext: deps.mergeContext,
      ...opt("document", adapters?.document),
      dev: readDev,
      dirtySince: (snapshot) => dirty.isDirtySince(snapshot),
      isTouched: (el) => dirty.isTouched(el),
      // _configure rebuilds the stack before the applier, so this stays live.
      layers,
      history,
      // The visit verb rides this hard-navigation seam, the url verb rides
      // history. _configure swaps in the mock, the same as history.
      navigate,
      assets,
      mount: { run: runMount },
      refresh: (request) => void wire.fetch(request),
      here: () => currentUrl(adapters?.document ?? document),
    };
  }

  function layerDeps(adapters?: PartialAdapters) {
    return {
      dispatch: deps.dispatch,
      ...opt("document", adapters?.document),
      ...opt("dialog", adapters?.dialog),
      // Shares the applier's history seam so a modal pushes its honest URL.
      history,
      ...opt("popstate", adapters?.popstate),
      fetch: (request: { url: string; zone: string }) => wire.fetch(request),
    };
  }

  function triggerDeps(adapters?: PartialAdapters) {
    return {
      fetch: (request: WireRequest) => void wire.fetch(request),
      abort: (zone: string) => wire.abort(zone),
      // The owning page of an element, so a base-page zone keeps GETting the
      // host URL while a modal layer holds the address bar.
      pageUrl: (el: Element) => layers.urlFor(el),
      ...opt("document", adapters?.document),
      ...opt("clock", adapters?.clock),
      ...opt("observer", adapters?.observer),
      ...opt("visibility", adapters?.visibility),
      ...opt("confirm", adapters?.confirm),
      dev: readDev,
    };
  }

  function sseDeps(adapters?: PartialAdapters) {
    return {
      // A stream event carries no dirty snapshot, so the server value wins.
      apply: (raw: unknown) => void applier.apply(raw),
      fetch: (request: WireRequest) => void wire.fetch(request),
      dispatch: deps.dispatch,
      pageUrl: (el: Element) => layers.urlFor(el),
      ...opt("document", adapters?.document),
      ...opt("source", adapters?.source),
      ...opt("visibility", adapters?.visibility),
    };
  }

  function wireDeps(adapters?: PartialAdapters) {
    return {
      ...opt("fetch", adapters?.fetch),
      ...opt("navigate", adapters?.navigate),
      // The same reload-once store the asset guard uses.
      ...opt("session", adapters?.session),
      dispatch: deps.dispatch,
      onEnvelope: (
        raw: unknown,
        _response: Response,
        snapshot: number,
        key: string | undefined,
        page: string | undefined,
      ) => {
        const envelope = applier.apply(raw, snapshot, key, page);
        // A csrf meta rotates the token so the next mutation submits the fresh
        // one, not just the forms already in the document.
        if (envelope.csrf) csrf = envelope.csrf;
      },
      version: () => assets.version(),
      csrf: () => csrf,
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
      csrf = next;
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
    ready() {
      assets.seed();
      runMount(document);
      mounted = true;
      triggers.ready();
    },
    _configure(adapters) {
      dev = adapters.dev ?? false;
      if (onlyDev(adapters)) return;
      if (adapters.document !== undefined) dirty.install(adapters.document);
      if (adapters.history !== undefined) history = adapters.history;
      if (adapters.navigate !== undefined) navigate = adapters.navigate;
      // The outgoing registry may still watch the old document's parse, so it is
      // torn down before the replacement takes over.
      assets._reset();
      assets = createAssets(assetsDeps(adapters));
      detachLayers();
      detachTriggers();
      // Stop the old pollers and observers, or they orphan onto the live wire.
      triggers._reset();
      sse._reset();
      layers = createLayers(layerDeps(adapters));
      triggers = createTriggers(triggerDeps(adapters));
      sse = createSse(sseDeps(adapters));
      applier = new Applier(applyDeps(adapters));
      wire = new Wire(wireDeps(adapters));
      detachLayers = layers.install(adapters.document ?? document);
      detachTriggers = triggers.install(adapters.document ?? document);
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
      csrf = undefined;
    },
  };

  return surface;
}
