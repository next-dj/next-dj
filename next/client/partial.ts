// Assembly of the `Next.partial` surface from the wire and apply modules. The
// core wires its dispatch and context-merge into the applier and the fetch
// layer, and exposes the public surface. Further verbs register behind the same
// surface through its extension points.

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

export interface PartialDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  mergeContext: (data: Record<string, unknown>) => void;
}

// The injectable seams the test harness overrides: the fetch adapter, the
// navigation hook jsdom does not implement, the clock for the timer-bound
// behaviour (CSS-wait timeout, debounce), the CSS loader, the intersection
// geometry, the reload-once session store, and the confirm gate.
export interface PartialAdapters {
  fetch?: FetchAdapter;
  clock?: Clock;
  navigate?: Navigate;
  document?: Document;
  dev?: boolean;
  // The native <dialog> modality, mocked in tests so the layer stack runs
  // without jsdom's missing showModal and focus trap.
  dialog?: DialogAdapter;
  history?: HistoryAdapter;
  // The Back-gesture seam of the intercepting modal lifecycle, mocked in tests
  // so the popstate handler runs without jsdom driving real history.
  popstate?: PopStateAdapter;
  // The CSS loader, the intersection geometry, the reload-once store, and the
  // confirm gate, each absent in jsdom.
  loadLink?: LinkLoader;
  observer?: IntersectionAdapter;
  session?: SessionStore;
  confirm?: ConfirmAdapter;
  cssTimeoutMs?: number;
  // The EventSource seam of the SSE bridge and the visibility seam the bridge
  // shares with the poll triggers, both absent in jsdom: tests drive message,
  // error, and the visibility flip through mocks.
  source?: EventSourceAdapter;
  visibility?: VisibilityAdapter;
}

export interface PartialSurface {
  apply(raw: unknown): Envelope;
  fetch(request: WireRequest): Promise<void>;
  defineOp(name: string, handler: OpHandler): void;
  parseHook(contentType: string, hook: ParseHook): void;
  setCsrf(csrf: CsrfPayload | undefined): void;
  // The re-executable mount registry: the callback runs over the document on
  // `ready`, over every inserted subtree after each apply, and immediately over
  // the current document when registered after `ready`, the replacement for
  // DOMContentLoaded for co-located JS that loads after the inline `_init`.
  // Returns a teardown that unregisters the callback, symmetric with the other
  // install seams, so a plugin can remove its own mount hook.
  onMount(selector: string, callback: (el: Element) => void): () => void;
  // The modal layer stack, exposed so the harness drives open/close/resolve
  // without synthesising a click.
  layers: LayerStack;
  // The SSE bridge, exposed so the harness drives the echo ring, the scan, and
  // the resync without a real EventSource.
  sse: Sse;
  // Run the on-`ready` work: seed the asset registry, mount the initial DOM,
  // fire the batched load zones. The core calls this from `_init`.
  ready(): void;
  // Configure the injectable adapters and rebuild the wire and applier. Tests
  // call this in beforeEach, production wires the real platform globals once.
  // An adapter object carrying nothing but the dev flag only flips the flag.
  _configure(adapters: PartialAdapters): void;
  _reset(): void;
}

// An optional dep entry: an absent adapter contributes no key at all, so the
// built deps object never carries an explicit undefined. This honours
// exactOptionalPropertyTypes, where an optional `key?: T` field rejects an
// assigned undefined.
function opt<K extends string, V>(key: K, value: V | undefined): Partial<Record<K, V>> {
  return value === undefined ? {} : ({ [key]: value } as Record<K, V>);
}

// Whether an adapter object names no seam at all, the shape the inline bootstrap
// passes to open the dev channel. Such a call swaps nothing in, so it must not
// cost a rebuild.
function onlyDev(adapters: PartialAdapters): boolean {
  return Object.keys(adapters).every((key) => key === "dev");
}

export function createPartial(deps: PartialDeps): PartialSurface {
  let csrf: CsrfPayload | undefined;

  // The dev channel is closure state the applier and the triggers read live. It
  // opens from inside the inline `_init`, where a rebuild would take the CSP
  // nonce off that inline script instead of off the bundle tag and would drop
  // the ops and parse hooks a page registered before the bootstrap.
  let dev = false;
  const readDev = (): boolean => dev;

  // The dirty registry: delegated listeners stamp touched fields, wire.ts
  // snapshots the counter at fetch time, the applier consults the predicate.
  const dirty = createDirtyTracker();
  dirty.install(document);

  // The mount registry, shared by onMount and triggers: every inserted subtree
  // runs the registered selector callbacks and the trigger activation. The
  // `mounted` flag records whether the initial `ready` pass has run, so a
  // late-registered callback catches up over the present document.
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
      // A user-toggled <details> keeps its open state past a patch it never
      // asked for, off the same registry the dirty predicate reads.
      isTouched: (el) => dirty.isTouched(el),
      // The stack satisfies the bridge: the applier resolves zone targets top
      // layer down and routes the layer and toast verbs into it. _configure
      // rebuilds the stack before the applier, so this binding stays live.
      layers,
      history,
      // The visit verb rides the navigation seam, a hard navigation that takes
      // any origin, where the url verb rides history. Defaults to the real
      // location.assign and _configure swaps in the mock, the same as history.
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
      // The layer stack shares the applier's history seam so a modal pushes its
      // honest URL and a close replaces it back through the same channel.
      history,
      ...opt("popstate", adapters?.popstate),
      fetch: (request: { url: string; zone: string }) => wire.fetch(request),
    };
  }

  function triggerDeps(adapters?: PartialAdapters) {
    return {
      fetch: (request: WireRequest) => void wire.fetch(request),
      abort: (zone: string) => wire.abort(zone),
      // A poll tick or lazy activation asks the layer stack for the page that
      // owns its element, so a base-page zone keeps GETting the host URL while
      // a modal layer holds the address bar. The arrow reads the `layers`
      // variable, and _configure rebuilds the stack before the triggers, so
      // the binding stays live the same way applyDeps holds it.
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
      // A stream event carries no per-target dirty snapshot, so it applies with
      // the server value winning, the same as a direct apply.
      apply: (raw: unknown) => void applier.apply(raw),
      fetch: (request: WireRequest) => void wire.fetch(request),
      dispatch: deps.dispatch,
      // A stream subscription captures the page that owns its container from
      // the layer stack, the same live binding as triggerDeps: _configure
      // rebuilds the stack before the bridge, so the arrow stays current.
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
      // The same reload-once store the asset guard uses, so the non-envelope
      // navigate-once flag rides the one session seam the harness overrides.
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
        // The csrf meta rotates the payload token too, so the next mutation
        // submits the fresh token, not just the forms already in the document.
        if (envelope.csrf) csrf = envelope.csrf;
      },
      version: () => assets.version(),
      csrf: () => csrf,
      dirtySnapshot: () => dirty.snapshot(),
      // Every mutating request feeds its ring id to the SSE bridge so the
      // matching stream event is dropped as the client's own echo.
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
      // A co-located script can register after the initial `ready` pass, since
      // it loads after the inline `_init` runs. Catch the callback up over the
      // document already present, mirroring `Next.on("ready")` for late
      // subscribers. It was absent from the `ready` pass, so this runs once.
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
      // An absent flag reads as production, the same default every other absent
      // adapter falls back to.
      dev = adapters.dev ?? false;
      if (onlyDev(adapters)) return;
      if (adapters.document !== undefined) dirty.install(adapters.document);
      if (adapters.history !== undefined) history = adapters.history;
      if (adapters.navigate !== undefined) navigate = adapters.navigate;
      // The outgoing registry may still be watching the old document for the
      // end of its parse, so it is torn down before the replacement takes over.
      assets._reset();
      assets = createAssets(assetsDeps(adapters));
      detachLayers();
      detachTriggers();
      // Stop the old instance's pollers and observers before the rebuild, or
      // they would keep fetching through the live wire as orphans, the same
      // reason the SSE bridge resets here.
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
