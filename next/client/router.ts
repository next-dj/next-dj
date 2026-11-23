import { defaultRouterHost } from "./adapters";
import type {
  BeforeApply,
  ContextPatch,
  Envelope,
  LayerOpenPatch,
  MorphPatch,
  Target,
  UrlPatch,
  VisitPatch,
} from "./apply";
import { CHUNK_ATTEMPTS, appendScript } from "./chunks";
import type { ScriptsChunk } from "./chunks";
import { isPlainActivation } from "./events";
import { locate } from "./layout";
import type { Layout } from "./layout";
import { between } from "./morph";
import type { Bounds } from "./morph";
import type { PartialSurface } from "./partial";
import {
  CONTENT_TYPE,
  EVENT_STREAM,
  HEADER_LAYOUTS,
  HEADER_NAVIGATE,
  HEADER_ORIGIN,
  STREAM_ACCEPT,
  bare,
  blank,
  isRecord,
  mime,
  says,
  uid,
} from "./protocol";
import type { PartialError } from "./protocol";
import type { Outcome } from "./wire";

export interface Entry {
  next: 1;
  kind: "nav" | "layer";
  key: string;
}

export interface RouterHost {
  listen(on: (event: Event) => void): () => void;
  state(): unknown;
  replace(state: Entry, href: string): void;
  assign(href: string): void;
}

export interface VisitOptions {
  replace?: boolean;
  scroll?: boolean;
}

export interface RouterOptions {
  focus?: (nodes: readonly Element[]) => boolean;
  announce?: (text: string) => void;
  host?: RouterHost;
}

export interface Settings {
  scope?: string;
  exclude?: string[];
  foreign?: string;
  prefetch?: string;
  prefetch_delay?: number;
  prefetch_ttl?: number;
  prefetch_shell_ttl?: number;
  prefetch_entries?: number;
  press?: string;
  snapshots?: number;
  snapshot?: boolean;
  progress_delay?: number;
  transitions?: boolean;
  intercept?: string;
  max_layouts?: number;
  current?: string[] | null;
  homes?: string[] | null;
  reader?: string;
  history?: string;
  intent?: string;
  type?: string;
}

/** A request already on its way for a page, its answer never rejecting. */
export interface Flight {
  answer: Promise<Response | undefined>;
  stop: () => void;
}

export interface Prefetched {
  raw?: Record<string, unknown> | undefined;
  response?: Response | undefined;
  flight?: Flight | undefined;
}

export interface Turn {
  hold: (hold: () => () => void) => void;
  before: () => void;
  after: (envelope: Envelope, nodes: readonly Element[], ok: boolean) => void;
}

export interface HistoryHost {
  partial: Pick<PartialSurface, "apply" | "layers" | "_headers" | "_parse" | "_take">;
  settings: () => Settings;
  context: () => Readonly<Record<string, unknown>>;
  region: (target: Target) => Bounds | undefined;
  chain: (next?: Layout[]) => Layout[];
  key: () => string;
  ask: (
    href: string,
    signal: AbortSignal,
    extra?: Record<string, string>,
  ) => Promise<Response>;
  owned: (url: URL, el?: Element) => boolean;
  plain: (path: string) => boolean;
  going: () => string;
  flush: () => void;
  ready?: ((chunk: "scripts") => Promise<ScriptsChunk>) | undefined;
  nonce?: string | undefined;
}

export interface Keeper {
  leave(key: string, target?: Target): void;
  pop(from: string, to: string, keep: boolean, owner: string): number | undefined;
  place(key: string): boolean;
  top(hash: string): boolean;
  flush(): void;
  swap(
    record: Record<string, unknown>,
    op: MorphPatch,
    bounds: Bounds,
    kind: string,
  ): Turn;
  extract(html: string, href: string): Record<string, unknown>;
  foreign(path: string): boolean;
  remember(path: string): void;
  plain(path: string): boolean;
  share(path: string): void;
  form(event: Event): URL | undefined;
  focus: (nodes: readonly Element[]) => boolean;
  announce: (text: string) => void;
  take(href: string): Prefetched | Promise<Prefetched | undefined> | undefined;
  prefetch(href: string): Promise<void>;
  drop(): void;
}

export interface Router {
  visit(url: string, options?: VisitOptions): Promise<void>;
  prefetch(url: string): Promise<void>;
  flush(): void;
  configure(options: RouterOptions): void;
  _install(factory: (host: HistoryHost) => Partial<Keeper>): void;
}

export interface RouterNext {
  partial: Pick<
    PartialSurface,
    | "apply"
    | "setCsrf"
    | "layers"
    | "_fire"
    | "_reader"
    | "_slot"
    | "_pop"
    | "_headers"
    | "_parse"
    | "_sweep"
    | "_take"
  >;
  readonly context: Readonly<Record<string, unknown>>;
  on(event: "ready", listener: () => void): () => void;
  ready?: ((chunk: "scripts") => Promise<ScriptsChunk>) | undefined;
}

interface How extends VisitOptions {
  trigger?: Element;
  pop?: boolean;
}

// The ops a navigation reads or fits, every other op passes through it untouched.
type Steered = ContextPatch | LayerOpenPatch | MorphPatch | UrlPatch | VisitPatch;

type Slotted = [target: Target, bounds: Bounds, op: MorphPatch];

interface Plan {
  slotted?: Slotted;
  away?: string;
  broken: boolean;
  layer: boolean;
}

const IDLE = 2e3;
const DROPPED: Outcome = { status: "dropped", applied: false };
const PENDING = "data-next-pending";
const NAVIGATING = "data-next-navigating";
const BUSY = "aria-busy";
const OFF =
  "[data-next-layer],[data-next-merge],[data-next-fetch],[data-next-retry]," +
  "[data-next-router=off]," +
  "[data-next-reload]:not([data-next-zone]):not([data-next-boundary])";

function plan(
  ops: readonly Steered[],
  region: (target: Target) => Bounds | undefined,
  context: Readonly<Record<string, unknown>>,
  replace: boolean,
  hash: string,
): Plan {
  const found: Plan = { broken: false, layer: false };
  for (const op of ops) {
    if (!isRecord(op)) continue;
    switch (op.op) {
      case "visit":
        found.broken = true;
        if (typeof op.href === "string") found.away = op.href;
        break;
      case "layer.open":
        found.layer = true;
        break;
      case "morph": {
        const { target } = op;
        if (!isRecord(target) || !("slot" in target || target.body === true)) break;
        const bounds = region(target);
        if (bounds) found.slotted = [target, bounds, op];
        else found.broken = true;
        break;
      }
      case "url":
        if (replace) op.action = "replace";
        if (typeof op.href === "string") op.href += hash;
        break;
      case "context":
        if (op.replace === true && isRecord(op.data)) blank(op.data, context);
    }
  }
  return found;
}

export function createRouter(
  next: RouterNext,
  host: RouterHost = defaultRouterHost(),
  listening: AddEventListenerOptions = {},
): Router {
  const { partial } = next;
  const doc = document;
  const html = doc.documentElement;
  const nonce = (doc.currentScript as HTMLScriptElement | null)?.nonce;
  const loads = new Map<string, Promise<unknown>>();
  let cfg: Settings = {};
  let chain: Layout[] = [];
  let key = "";
  let shown = "";
  let snap = true;
  let keeper: Partial<Keeper> = {};
  let active: AbortController | undefined;
  let bound = "";
  let focus: RouterOptions["focus"];
  let announce: RouterOptions["announce"];
  let unmark = (): void => undefined;
  let turns = 0;
  let failures = 0;
  let stop = (): void => undefined;

  const here = (): string => location.pathname + location.search;
  const entry = (): Partial<Entry> | null | undefined =>
    host.state() as Partial<Entry> | null | undefined;
  const now = (): string => entry()?.key ?? "";
  const stamp = (): void => host.replace({ next: 1, kind: "nav", key }, location.href);
  const emit = (name: string, detail: Record<string, unknown>): void =>
    void partial._fire(`router:${name}`, detail);
  const flush = (): void => {
    keeper.flush?.();
    keeper.drop?.();
  };
  const assign = (href: string): void => {
    const url = new URL(href, doc.baseURI);
    if (/^https?:$/.test(url.protocol)) host.assign(url.href);
  };
  // The attempts are shared, since a host that fails one chunk fails them all.
  const load = (src: string | undefined): Promise<unknown> | undefined => {
    if (!src) return undefined;
    const loading =
      loads.get(src) ??
      new Promise((done) =>
        appendScript(doc, src, nonce, (loaded, el) => {
          if (!loaded) {
            el.remove();
            if (++failures < CHUNK_ATTEMPTS) loads.delete(src);
            partial._fire("partial:error", {
              kind: "asset",
              url: src,
              error: new Error("router chunk failed"),
            } satisfies PartialError);
          }
          done(loaded);
        }),
      );
    loads.set(src, loading);
    return loading;
  };
  const grab = (): Promise<unknown> =>
    Promise.all([load(cfg.history), load(cfg.intent)]);
  const read = (): unknown => partial._reader ?? load(cfg.reader);

  const region = (target: Target): Bounds | undefined => locate(doc, chain, target);

  const owned = (url: URL, el?: Element): boolean =>
    url.origin === location.origin &&
    !(url.hash && url.pathname + url.search === here()) &&
    !el?.closest(OFF) &&
    (!el || cfg.scope !== "opt-in" || el.closest("[data-next-router]") !== null) &&
    !cfg.exclude?.some((excluded) => url.pathname.startsWith(excluded)) &&
    !keeper.foreign?.(url.pathname + url.search);

  const ask = (
    href: string,
    signal: AbortSignal,
    extra: Record<string, string> = {},
  ): Promise<Response> => {
    const declared = chain
      .slice(0, cfg.max_layouts ?? 32)
      .map(([id, digest]) => `${id}.${digest}`);
    while (declared.join().length > 1024) declared.pop();
    return fetch(href, {
      headers: partial._headers({
        [HEADER_NAVIGATE]: "1",
        [HEADER_LAYOUTS]: declared.join(),
        [HEADER_ORIGIN]: here(),
        ...(cfg.reader || partial._reader ? { Accept: STREAM_ACCEPT } : {}),
        ...extra,
      }),
      signal,
      mode: "same-origin",
    });
  };
  const go = async (target: URL, how: How = {}): Promise<void> => {
    const { trigger, pop, scroll } = how;
    const path = target.pathname + target.search;
    const hit = pop ? undefined : keeper.take?.(target.href);
    const detail = { url: target.href, trigger, cached: hit !== undefined };
    // A vetoed or an overtaken visit stops a pressed flight, even one still on its way.
    const drop = (): void =>
      void Promise.resolve(hit).then((found) => found?.flight?.stop());
    if (!pop && partial._fire("router:before", detail, true) === false) return drop();
    active?.abort();
    turns += 1;
    const controller = (active = new AbortController());
    const { signal } = controller;
    signal.addEventListener("abort", drop);
    bound = bare(target.href);
    const replace = how.replace === true || partial.layers.size() > 0;
    const motion = pop ? "back" : replace ? "replace" : "forward";
    const from = key;
    const area = region({ slot: chain.at(-1)?.[0] ?? "" })?.[0] ?? doc.body;
    const loading = grab();
    let url = target;
    let status = "complete";
    let lands = 0;
    let again = (): unknown => 0;
    const settle = (): void => {
      if (active !== controller) return;
      unmark();
      unmark = () => undefined;
    };
    const fail = (error?: unknown, href = target.href): void => {
      if (signal.aborted) return;
      if (error !== undefined) emit("error", { url: target.href, error });
      status = "hard";
      controller.abort();
      assign(href);
    };

    const land = (raw: unknown, response: Response): Promise<Outcome> =>
      new Promise((resolve) => {
        const first = lands++ === 0;
        const record: Record<string, unknown> = isRecord(raw) ? raw : {};
        const { layouts } = record;
        const keepable = !says(response, "no-snapshot");
        const { slotted, away, broken, layer } = plan(
          Array.isArray(record.ops) ? (record.ops as Steered[]) : [],
          region,
          next.context,
          replace || pop === true || !first,
          target.hash,
        );
        const shaped = Array.isArray(layouts) || (layer && cfg.intercept === "links");
        if (signal.aborted || broken || !isRecord(raw) || !shaped) {
          if (says(response, "clear")) flush();
          fail(undefined, away);
          resolve(DROPPED);
          return;
        }
        const [aim, bounds, morph] = slotted ?? [];
        const turn =
          first && bounds && morph
            ? keeper.swap?.(record, morph, bounds, motion)
            : undefined;
        const swapping = (event: Event): void => {
          const { hold } = (event as CustomEvent<BeforeApply>).detail;
          if (first && !pop) keeper.leave?.(from, snap ? aim : undefined);
          turn?.hold(hold);
          const nodes = bounds ? between(bounds) : [];
          emit("before-swap", { url: url.href, layouts, hold, nodes });
        };
        doc.addEventListener("partial:before-apply", swapping);
        try {
          partial.apply(raw, {
            response,
            owner: pop ? path : undefined,
            isCurrent: () => !signal.aborted,
            before: turn?.before,
            done: ({ envelope, ok, nodes = [] }) => {
              turn?.after(envelope, nodes, ok);
              if (!ok) {
                fail(new Error("not applied"));
                return resolve(DROPPED);
              }
              resolve({ status: "ok", applied: true, envelope });
              if (!Array.isArray(layouts)) return;
              chain = (layouts as [string, string, string | null][]).map(
                ([id, digest, seal], index) => [
                  id,
                  digest,
                  seal ?? chain[index]?.[2] ?? "",
                ],
              );
              if (envelope.csrf) {
                partial.setCsrf(envelope.csrf);
                flush();
              }
              if (record.shared === true) keeper.share?.(here());
              snap = keepable;
              shown = here();
              key = now();
              partial._sweep();
              if (!first) return;
              const place = (): boolean | undefined =>
                pop ? keeper.place?.(from) : keeper.top?.(url.hash);
              if (scroll !== false && place() === false) again = place;
              const speak = announce ?? keeper.announce;
              if (pop || scroll === false || !(focus ?? keeper.focus)?.(nodes))
                speak?.(doc.title);
              settle();
              emit("commit", { url: url.href });
            },
          });
        } catch (error) {
          fail(error);
          resolve(DROPPED);
        } finally {
          doc.removeEventListener("partial:before-apply", swapping);
        }
      });

    while (partial.layers.size() > 0)
      partial.layers.close({ dismiss: true, reason: "navigate" });
    emit("start", { url: target.href });
    trigger?.setAttribute(PENDING, "");
    const timer = setTimeout(
      () => {
        html.setAttribute(NAVIGATING, "");
        area.setAttribute(BUSY, "true");
      },
      (cfg.progress_delay ?? 0.15) * 1000,
    );
    const previous = unmark;
    unmark = () => {
      previous();
      clearTimeout(timer);
      trigger?.removeAttribute(PENDING);
      html.removeAttribute(NAVIGATING);
      area.removeAttribute(BUSY);
    };
    const early = async (found?: Prefetched): Promise<unknown> => {
      await loading;
      const { raw, response } = found ?? {};
      return raw && response && land(raw, response);
    };
    const stream = async (found?: Prefetched): Promise<void> => {
      try {
        const plainly = keeper.plain?.(path) === true;
        if (plainly) found?.flight?.stop();
        const response = plainly
          ? await fetch(target.href, { signal, mode: "same-origin" })
          : ((found?.flight && (await found.flight.answer)) ??
            (await ask(target.href, signal)));
        url = new URL(response.url || target.href);
        url.hash = target.hash;
        const at = url.pathname + url.search;
        const type = mime(response);
        const streamed = type === EVENT_STREAM;
        const markup = type === "text/html" && (plainly || cfg.foreign === "extract");
        if (streamed) await read();
        const reader = streamed ? partial._reader : undefined;
        await loading;
        if (!response.ok) fail();
        else if (reader)
          await reader(response, {
            signal,
            open: () => undefined,
            land: (_body, parse) => land(parse(), response),
          });
        else if (!plainly && type === (cfg.type ?? CONTENT_TYPE))
          await land(partial._parse(type, await response.text()), response);
        else if (keeper.extract && markup) {
          keeper.share?.(path);
          await land(keeper.extract(await response.text(), at), response);
        } else {
          if (!streamed) keeper.remember?.(at);
          fail();
        }
      } catch (error) {
        fail(error);
      }
    };
    try {
      const found = hit instanceof Promise ? await hit : hit;
      const whole = found?.raw?.complete === true;
      if (whole) found.flight?.stop();
      await Promise.all([early(found).catch(fail), whole || stream(found)]);
      if (!signal.aborted) again();
    } finally {
      settle();
      if (active === controller) {
        active = undefined;
        bound = "";
      }
      emit("end", {
        url: url.href,
        status: signal.aborted && status === "complete" ? "superseded" : status,
      });
    }
  };

  const on = (event: Event): void => {
    const el = event.target instanceof Element ? event.target : null;
    let trigger = el;
    let url: URL | undefined;
    if (event.type === "submit") url = keeper.form?.(event);
    else {
      trigger = el?.closest("a[href]") ?? null;
      if (
        trigger instanceof HTMLAnchorElement &&
        isPlainActivation(event, trigger) &&
        !trigger.matches("[rel~=external]")
      )
        url = new URL(trigger.href);
    }
    if (!url || !trigger || !owned(url, trigger)) return;
    event.preventDefault();
    const scroll = !trigger.closest("[data-next-scroll=preserve]");
    void go(url, { trigger, scroll });
  };

  const fresh = (): void => {
    if (partial.layers.size() === 0) key = now() || key;
  };

  partial._slot = region;
  partial._pop = () => {
    const state = entry();
    const from = key;
    if (state?.next === 1) key = state.key ?? "";
    if (here() === shown) {
      if (location.hash) keeper.top?.(location.hash);
      return undefined;
    }
    if (state?.next !== 1) return undefined;
    const path = (shown = here());
    const turn = turns;
    queueMicrotask(() => {
      if (turn !== turns) return;
      const at = keeper.pop?.(from, key, snap, path);
      if (at !== undefined && Date.now() - at < (cfg.prefetch_ttl ?? 30) * 1000) {
        active?.abort();
        snap = true;
      } else void go(new URL(location.href), { pop: true, scroll: at === undefined });
    });
    return path;
  };

  const boot = (): void => {
    const context = next.context;
    cfg = (isRecord(context.$router) ? context.$router : {}) as Settings;
    chain = (Array.isArray(context.$layouts) ? context.$layouts : []) as Layout[];
    snap = cfg.snapshot !== false;
    shown = here();
    const state = entry();
    const kept = state?.next === 1 ? (state.key ?? "") : "";
    key = kept || uid();
    if (!state || state.next === 1) stamp();
    stop();
    stop = host.listen(on);
    emit("end", { url: location.href, status: "complete", initial: true });
    if (kept) void grab();
    const idle = (): void => void [grab(), read()];
    void (
      (window as Partial<Window>).requestIdleCallback?.(idle, { timeout: IDLE }) ??
      setTimeout(idle)
    );
  };

  doc.addEventListener(
    "partial:before-request",
    (event) => {
      if ((event as CustomEvent<{ method: string }>).detail.method !== "GET") flush();
    },
    listening,
  );
  doc.addEventListener(
    "partial:before-apply",
    (event) => {
      if (says((event as CustomEvent<BeforeApply>).detail.response, "clear")) {
        flush();
        snap = false;
      }
    },
    listening,
  );
  doc.addEventListener("partial:layer-accepted", fresh, listening);
  doc.addEventListener("partial:layer-dismissed", fresh, listening);
  addEventListener(
    "hashchange",
    () => {
      if (host.state()) return;
      key = uid();
      stamp();
    },
    listening,
  );
  const off = next.on("ready", boot);
  listening.signal?.addEventListener("abort", () => {
    off();
    stop();
  });

  return {
    visit(href, options = {}) {
      const url = new URL(href, doc.baseURI);
      if (owned(url)) return go(url, options);
      assign(url.href);
      return Promise.resolve();
    },
    async prefetch(href) {
      await grab();
      await keeper.prefetch?.(new URL(href, doc.baseURI).href);
    },
    flush,
    configure(options) {
      focus = options.focus ?? focus;
      announce = options.announce ?? announce;
      if (options.host) {
        stop();
        host = options.host;
        stop = host.listen(on);
      }
    },
    _install(factory) {
      keeper = {
        ...keeper,
        ...factory({
          partial,
          settings: () => cfg,
          context: () => next.context,
          region,
          chain: (list) => (chain = list ?? chain),
          key: () => key,
          ask,
          owned,
          plain: (path) => keeper.plain?.(path) === true,
          going: () => bound,
          flush,
          ready: next.ready,
          nonce,
        }),
      };
    },
  };
}
