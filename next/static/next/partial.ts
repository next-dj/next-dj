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
import type { DialogAdapter, LayerStack } from "./layers";
import { createAssets } from "./assets";
import type { LinkLoader, SessionStore } from "./assets";
import { createTriggers } from "./triggers";
import type { ConfirmAdapter, IntersectionAdapter } from "./triggers";
import { defaultHistory } from "./adapters";

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
  // The CSS loader, the intersection geometry, the reload-once store, and the
  // confirm gate, each absent in jsdom.
  loadLink?: LinkLoader;
  observer?: IntersectionAdapter;
  session?: SessionStore;
  confirm?: ConfirmAdapter;
  cssTimeoutMs?: number;
}

export interface PartialSurface {
  apply(raw: unknown): Envelope;
  fetch(request: WireRequest): Promise<void>;
  defineOp(name: string, handler: OpHandler): void;
  parseHook(contentType: string, hook: ParseHook): void;
  setCsrf(csrf: CsrfPayload | undefined): void;
  // The re-executable mount registry: the callback runs over the document on
  // `ready` and over every inserted subtree after each apply, the replacement
  // for DOMContentLoaded for co-located JS.
  onMount(selector: string, callback: (el: Element) => void): void;
  // The modal layer stack, exposed so the harness drives open/close/resolve
  // without synthesising a click.
  layers: LayerStack;
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
  // runs the registered selector callbacks and the trigger activation.
  const mounts: { selector: string; callback: (el: Element) => void }[] = [];
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
  };

  let assets = createAssets(assetsDeps());
  let history: HistoryAdapter = defaultHistory();
  let layers = createLayers(layerDeps());
  let triggers = createTriggers(triggerDeps());
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
      mounts.push({ selector, callback });
    },
    get layers() {
      return layers;
    },
    ready() {
      assets.seed();
      runMount(document);
      triggers.ready();
    },
    _configure(adapters) {
      if (adapters.document !== undefined) dirty.install(adapters.document);
      if (adapters.history !== undefined) history = adapters.history;
      assets = createAssets(assetsDeps(adapters));
      detachLayers();
      detachTriggers();
      layers = createLayers(layerDeps(adapters));
      triggers = createTriggers(triggerDeps(adapters));
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
      mounts.length = 0;
      csrf = undefined;
    },
  };

  return surface;
}
