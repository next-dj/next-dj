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
  // The EventSource and visibility seams of the SSE bridge, both absent in
  // jsdom: tests drive message, error, and the visibility flip through mocks.
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
  _configure(adapters: PartialAdapters): void;
  _reset(): void;
}

export function createPartial(deps: PartialDeps): PartialSurface {
  let csrf: CsrfPayload | undefined;

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
      for (const el of Array.from(root.querySelectorAll(entry.selector))) {
        entry.callback(el);
      }
      // A subtree root that matches the selector itself is mounted too.
      if (root instanceof Element && root.matches(entry.selector)) {
        entry.callback(root);
      }
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
      document: adapters?.document,
      clock: adapters?.clock,
      loadLink: adapters?.loadLink,
      navigate: adapters?.navigate,
      session: adapters?.session,
      cssTimeoutMs: adapters?.cssTimeoutMs,
    };
  }

  function applyDeps(adapters?: PartialAdapters): ApplyDeps {
    return {
      dispatch: deps.dispatch,
      mergeContext: deps.mergeContext,
      document: adapters?.document,
      dev: adapters?.dev,
      dirtySince: (snapshot) => dirty.isDirtySince(snapshot),
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
      here: () => (adapters?.document ?? document).location.pathname,
    };
  }

  function layerDeps(adapters?: PartialAdapters) {
    return {
      dispatch: deps.dispatch,
      document: adapters?.document,
      dialog: adapters?.dialog,
      // The layer stack shares the applier's history seam so a modal pushes its
      // honest URL and a close replaces it back through the same channel.
      history,
      popstate: adapters?.popstate,
      fetch: (request: { url: string; zone: string }) => wire.fetch(request),
    };
  }

  function triggerDeps(adapters?: PartialAdapters) {
    return {
      fetch: (request: WireRequest) => void wire.fetch(request),
      abort: (zone: string) => wire.abort(zone),
      version: () => assets.version(),
      document: adapters?.document,
      clock: adapters?.clock,
      observer: adapters?.observer,
      confirm: adapters?.confirm,
    };
  }

  function sseDeps(adapters?: PartialAdapters) {
    return {
      // A stream event carries no per-target dirty snapshot, so it applies with
      // the server value winning, the same as a direct apply.
      apply: (raw: unknown) => void applier.apply(raw),
      fetch: (request: WireRequest) => void wire.fetch(request),
      dispatch: deps.dispatch,
      document: adapters?.document,
      source: adapters?.source,
      visibility: adapters?.visibility,
    };
  }

  function wireDeps(adapters?: PartialAdapters) {
    return {
      fetch: adapters?.fetch,
      navigate: adapters?.navigate,
      dispatch: deps.dispatch,
      onEnvelope: (raw: unknown, _response: Response, snapshot: number) => {
        const envelope = applier.apply(raw, snapshot);
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
      if (adapters.document !== undefined) dirty.install(adapters.document);
      if (adapters.history !== undefined) history = adapters.history;
      if (adapters.navigate !== undefined) navigate = adapters.navigate;
      assets = createAssets(assetsDeps(adapters));
      detachLayers();
      detachTriggers();
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
