import { defaultRouterHost } from "./adapters";
import type { BeforeApply, Envelope, Target } from "./apply";
import { CHUNK_TIMEOUT } from "./chunks";
import type { ScriptsChunk } from "./chunks";
import { isPlainActivation } from "./events";
import { between } from "./morph";
import type { Bounds } from "./morph";
import type { PartialSurface } from "./partial";
import {
  CONTENT_TYPE,
  EVENT_STREAM,
  HEADER_ORIGIN,
  STREAM_ACCEPT,
  blank,
  isRecord,
} from "./protocol";
import type { Outcome } from "./wire";

export type Layout = [id: string, digest: string, seal: string];

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

export interface Prefetched {
  raw: Record<string, unknown>;
  response: Response;
}

export interface Turn {
  hold: (hold: () => () => void) => void;
  before: () => void;
  after: (envelope: Envelope, nodes: readonly Element[], ok: boolean) => void;
}

export interface HistoryHost {
  partial: Pick<PartialSurface, "apply" | "layers" | "_headers" | "_parse">;
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
  flush: () => void;
  ready?: ((chunk: "scripts") => Promise<ScriptsChunk>) | undefined;
  nonce?: string | undefined;
}

export interface Keeper {
  leave(key: string, target?: Target): void;
  pop(from: string, to: string, keep: boolean, owner: string): number;
  place(key: string): boolean;
  top(hash: string): boolean;
  flush(): void;
  swap(
    record: Record<string, unknown>,
    op: Record<string, unknown>,
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
  >;
  readonly context: Readonly<Record<string, unknown>>;
  on(event: "ready", listener: () => void): () => void;
  ready?: ((chunk: "scripts") => Promise<ScriptsChunk>) | undefined;
}

interface How extends VisitOptions {
  trigger?: Element;
  pop?: boolean;
}

type Swap = [aim: Target, bounds: Bounds, op: Record<string, unknown>];

const SLOT = "nx:s";
const IDLE = 2e3;
const DROPPED: Outcome = { status: "dropped", applied: false };
const PENDING = "data-next-pending";
const NAVIGATING = "data-next-navigating";
const BUSY = "aria-busy";
const OFF =
  "[data-next-layer],[data-next-merge],[data-next-fetch],[data-next-retry]," +
  "[data-next-router=off]," +
  "[data-next-reload]:not([data-next-zone]):not([data-next-boundary])";

export function markers(root: Document, id: string, seal: string): Bounds | undefined {
  const walk = root.createTreeWalker(root, NodeFilter.SHOW_COMMENT);
  let start: Node | undefined;
  for (let node = walk.nextNode(); node; node = walk.nextNode()) {
    const data = (node as Comment).data;
    if (data === `${SLOT} ${id} ${seal}`) start = node;
    else if (start && data === `/${SLOT} ${id} ${seal}`)
      return start.parentNode === node.parentNode
        ? [node.parentElement as Element, start, node]
        : undefined;
  }
  return undefined;
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
  let cfg: Settings = {};
  let chain: Layout[] = [];
  let key = "";
  let shown = "";
  let snap = true;
  let keeper: Partial<Keeper> = {};
  let pending: unknown;
  let reading: unknown;
  let active: AbortController | undefined;
  let focus: RouterOptions["focus"];
  let announce: RouterOptions["announce"];
  let unmark = (): void => undefined;
  let turns = 0;
  let stop = (): void => undefined;

  const here = (): string => location.pathname + location.search;
  const now = (): string => (host.state() as Partial<Entry> | null)?.key ?? "";
  const mint = (): string => Math.random().toString(36).slice(2);
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
  const load = (src: string | undefined): Promise<unknown> =>
    new Promise((done) => {
      if (!src) return done(0);
      const script = doc.createElement("script");
      script.src = src;
      if (nonce) script.nonce = nonce;
      script.onload = script.onerror = done;
      setTimeout(done, CHUNK_TIMEOUT);
      doc.head.append(script);
    });
  const grab = (): unknown =>
    (pending ??= Promise.all([cfg.history, cfg.intent].map(load)));
  const read = (): unknown => partial._reader ?? (reading ??= load(cfg.reader));
  const says = (response: Response | undefined, word: string): boolean =>
    response?.headers.get("X-Next-Router")?.includes(word) === true;

  const find = (id: string): Bounds | undefined => {
    const seal = chain.find((layout) => layout[0] === id)?.[2];
    return seal === undefined ? undefined : markers(doc, id, seal);
  };
  const region = (target: Target): Bounds | undefined =>
    target.body ? [doc.body, null, null] : find(target.slot ?? "");

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
        "X-Next-Navigate": "1",
        "X-Next-Layouts": declared.join(),
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
    if (!pop && partial._fire("router:before", detail, true) === false) return;
    active?.abort();
    turns += 1;
    const controller = (active = new AbortController());
    const { signal } = controller;
    const replace = how.replace === true || partial.layers.size() > 0;
    const motion = pop ? "back" : replace ? "replace" : "forward";
    const entry = key;
    const area = find(chain.at(-1)?.[0] ?? "")?.[0] ?? doc.body;
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
        const ops = Array.isArray(record.ops) ? (record.ops as unknown[]) : [];
        const keepable = !says(response, "no-snapshot");
        let broken = !isRecord(raw);
        let shaped = Array.isArray(layouts);
        let away = target.href;
        let swap: Swap | undefined;
        for (const op of ops) {
          if (!isRecord(op)) continue;
          const { target: aim, data } = op;
          if (op.op === "visit") {
            broken = true;
            if (typeof op.href === "string") away = op.href;
          }
          if (op.op === "layer.open") shaped ||= cfg.intercept === "links";
          if (
            op.op === "morph" &&
            isRecord(aim) &&
            ("slot" in aim || aim.body === true)
          ) {
            const bounds = region(aim as Target);
            if (bounds) swap = [aim as Target, bounds, op];
            else broken = true;
          }
          if (op.op === "url") {
            op.action = replace || pop || !first ? "replace" : op.action;
            if (typeof op.href === "string") op.href += url.hash;
          }
          if (op.op === "context" && op.replace === true && isRecord(data))
            blank(data, next.context);
        }
        if (signal.aborted || broken || !shaped) {
          if (says(response, "clear")) flush();
          fail(undefined, away);
          resolve(DROPPED);
          return;
        }
        const turn =
          first && swap ? keeper.swap?.(record, swap[2], swap[1], motion) : undefined;
        const swapping = (event: Event): void => {
          const { hold } = (event as CustomEvent<BeforeApply>).detail;
          if (first && !pop) keeper.leave?.(entry, snap ? swap?.[0] : undefined);
          turn?.hold(hold);
          const nodes = swap ? between(swap[1]) : [];
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
                pop ? keeper.place?.(entry) : keeper.top?.(url.hash);
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
      return found && land(found.raw, found.response);
    };
    const stream = async (): Promise<void> => {
      try {
        const bare = keeper.plain?.(path) === true;
        const response = bare
          ? await fetch(target.href, { signal, mode: "same-origin" })
          : await ask(target.href, signal);
        url = new URL(response.url || target.href);
        url.hash = target.hash;
        const at = url.pathname + url.search;
        const type = (response.headers.get("content-type") ?? "")
          .replace(/;.*/, "")
          .trim();
        const streamed = type === EVENT_STREAM;
        const markup = type === "text/html" && (bare || cfg.foreign === "extract");
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
        else if (!bare && type === (cfg.type ?? CONTENT_TYPE))
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
      await Promise.all([
        early(found).catch(fail),
        found?.raw.complete === true || stream(),
      ]);
      if (!signal.aborted) again();
    } finally {
      settle();
      if (active === controller) active = undefined;
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
    const state = host.state() as Partial<Entry> | null | undefined;
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
      const at = keeper.pop?.(from, key, snap, path) ?? 0;
      if (Date.now() - at < (cfg.prefetch_ttl ?? 30) * 1000) {
        active?.abort();
        snap = true;
      } else void go(new URL(location.href), { pop: true, scroll: !at });
    });
    return path;
  };

  const boot = (): void => {
    const context = next.context;
    cfg = (isRecord(context.$router) ? context.$router : {}) as Settings;
    chain = (Array.isArray(context.$layouts) ? context.$layouts : []) as Layout[];
    snap = cfg.snapshot !== false;
    shown = here();
    const state = host.state() as Partial<Entry> | null | undefined;
    const kept = state?.next === 1 ? (state.key ?? "") : "";
    key = kept || mint();
    if (!state || state.next === 1) stamp();
    stop();
    stop = host.listen(on);
    emit("end", { url: location.href, status: "complete", initial: true });
    if (kept) grab();
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
      key = mint();
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
          flush,
          ready: next.ready,
          nonce,
        }),
      };
    },
  };
}
