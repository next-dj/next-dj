// Assembly of the `Next.partial` surface from the wire and apply modules. The
// core wires its dispatch and context-merge into the applier and the fetch
// layer, and exposes the public surface. Further verbs register behind the same
// surface through its extension points.

import { Applier } from "./apply";
import type { ApplyDeps, Envelope, HistoryAdapter, OpHandler } from "./apply";
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
import { defaultHistory } from "./adapters";

export interface PartialDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  mergeContext: (data: Record<string, unknown>) => void;
}

// The injectable seams the test harness overrides: the fetch adapter and the
// navigation hook jsdom does not implement. The clock seam is held for the
// timer-bound behaviour (CSS-wait timeout, debounce) so the surface stays
// stable once those handlers register.
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
}

export interface PartialSurface {
  apply(raw: unknown): Envelope;
  fetch(request: WireRequest): Promise<void>;
  defineOp(name: string, handler: OpHandler): void;
  parseHook(contentType: string, hook: ParseHook): void;
  setCsrf(csrf: CsrfPayload | undefined): void;
  // The modal layer stack, exposed so the harness drives open/close/resolve
  // without synthesising a click.
  layers: LayerStack;
  // Configure the injectable adapters and rebuild the wire and applier. Tests
  // call this in beforeEach, production wires the real platform globals once.
  _configure(adapters: PartialAdapters): void;
  _reset(): void;
}

export function createPartial(deps: PartialDeps): PartialSurface {
  let version = "";
  let csrf: CsrfPayload | undefined;

  // The dirty registry: delegated listeners stamp touched fields, wire.ts
  // snapshots the counter at fetch time, the applier consults the predicate.
  const dirty = createDirtyTracker();
  dirty.install(document);

  // The layer stack carries no transport: it asks the wire to GET the body and
  // the host re-GET on accept, indirected through the current binding so a
  // rebuild on _configure keeps the closure live.
  let layers = createLayers(layerDeps());
  let history: HistoryAdapter = defaultHistory();
  let applier = new Applier(applyDeps());
  let wire = new Wire(wireDeps());
  let detachLayers = layers.install(document);

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
      version: () => version,
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
    get layers() {
      return layers;
    },
    _configure(adapters) {
      if (adapters.document !== undefined) dirty.install(adapters.document);
      if (adapters.history !== undefined) history = adapters.history;
      detachLayers();
      layers = createLayers(layerDeps(adapters));
      applier = new Applier(applyDeps(adapters));
      wire = new Wire(wireDeps(adapters));
      detachLayers = layers.install(adapters.document ?? document);
    },
    _reset() {
      wire._reset();
      applier._reset();
      dirty._reset();
      layers._reset();
      version = "";
      csrf = undefined;
    },
  };

  return surface;
}
