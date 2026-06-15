// Assembly of the `Next.partial` surface from the wire and apply modules. The
// core wires its dispatch and context-merge into the applier and the fetch
// layer, and exposes the public surface. Further verbs register behind the same
// surface through its extension points.

import { Applier } from "./apply";
import type { ApplyDeps, Envelope, OpHandler } from "./apply";
import { Wire } from "./wire";
import type {
  Clock,
  CsrfPayload,
  FetchAdapter,
  Navigate,
  ParseHook,
  WireRequest,
} from "./wire";

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
}

export interface PartialSurface {
  apply(raw: unknown): Envelope;
  fetch(request: WireRequest): Promise<void>;
  defineOp(name: string, handler: OpHandler): void;
  parseHook(contentType: string, hook: ParseHook): void;
  setCsrf(csrf: CsrfPayload | undefined): void;
  // Configure the injectable adapters and rebuild the wire and applier. Tests
  // call this in beforeEach, production wires the real platform globals once.
  _configure(adapters: PartialAdapters): void;
  _reset(): void;
}

export function createPartial(deps: PartialDeps): PartialSurface {
  let version = "";
  let csrf: CsrfPayload | undefined;

  let applier = new Applier(applyDeps());
  let wire = new Wire(wireDeps());

  function applyDeps(adapters?: PartialAdapters): ApplyDeps {
    return {
      dispatch: deps.dispatch,
      mergeContext: deps.mergeContext,
      document: adapters?.document,
      dev: adapters?.dev,
    };
  }

  function wireDeps(adapters?: PartialAdapters) {
    return {
      fetch: adapters?.fetch,
      navigate: adapters?.navigate,
      dispatch: deps.dispatch,
      onEnvelope: (raw: unknown) => {
        const envelope = applier.apply(raw);
        // The csrf meta rotates the payload token too, so the next mutation
        // submits the fresh token, not just the forms already in the document.
        if (envelope.csrf) csrf = envelope.csrf;
      },
      version: () => version,
      csrf: () => csrf,
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
    _configure(adapters) {
      applier = new Applier(applyDeps(adapters));
      wire = new Wire(wireDeps(adapters));
    },
    _reset() {
      wire._reset();
      applier._reset();
      version = "";
      csrf = undefined;
    },
  };

  return surface;
}
