// The client router. It owns plain same-origin links and GET forms, turns each visit
// into a navigation request that names the layouts on screen, and lands the answer in
// the slot that changed. Snapshots and scroll ship with it, prefetch in the intent chunk.

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
import { unmarked } from "./links";
import { isPlainActivation } from "./events";
import { createHistory } from "./history";
import type { History } from "./history";
import type { Kernel } from "./kernel";
import { ORDER_CLEAR } from "./kernel";
import { layoutsOf, locate } from "./layout";
import type { Layout } from "./layout";
import { between } from "./morph";
import type { Bounds } from "./morph";
import type { PartialSurface } from "./partial";
import {
  ATTR_FETCH,
  ATTR_LAYER,
  ATTR_MERGE,
  ATTR_NAVIGATING,
  ATTR_PENDING,
  ATTR_RETRY,
  ATTR_ROUTER,
  ATTR_SCROLL,
  CONTENT_TYPE,
  EVENT_STREAM,
  HEADER_LAYOUTS,
  HEADER_NAVIGATE,
  HEADER_ORIGIN,
  MAX_LAYOUTS,
  ROUTER_CLEAR,
  ROUTER_NO_SNAPSHOT,
  ROUTER_OFF,
  STREAM_ACCEPT,
  bare,
  blank,
  isRecord,
  mime,
  says,
  uid,
} from "./protocol";
import { isPage, readTable } from "./routes";
import type { Table, Wire } from "./routes";
import { submitUrl } from "./submit";
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

/**
 * The `$router` payload, the `ROUTER` settings next/conf/router.py validated.
 *
 * The server refuses a value outside each union, so the client reads the payload as is.
 */
export interface Settings {
  scope?: "all" | "opt_in";
  routes?: string | null;
  exclude?: string[];
  foreign?: "hard" | "extract";
  prefetch?: "intent" | "viewport" | "off";
  prefetch_delay?: number;
  prefetch_ttl?: number;
  prefetch_shell_ttl?: number;
  prefetch_entries?: number;
  press?: "mouse" | "all" | "off";
  snapshots?: number;
  snapshot?: boolean;
  progress?: boolean;
  progress_delay?: number;
  transitions?: boolean;
  intercept?: "opt_in" | "links";
  links?: string[] | null;
  homes?: string[] | null;
  boundaries?: string;
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

/** The view transition and the slot swap around one landing envelope. */
export interface Turn {
  hold: (hold: () => () => void) => void;
  before: () => void;
  after: (envelope: Envelope, nodes: readonly Element[], ok: boolean) => void;
}

/** The seams the router hands its history and the intent chunk. */
export interface HistoryHost extends Pick<
  Kernel,
  "headers" | "parse" | "serve" | "hook" | "scripts" | "nonce"
> {
  partial: Pick<PartialSurface, "apply" | "layers">;
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
}

/** The prefetch of the intent chunk, the cache a visit takes its answer from. */
export interface Prefetcher {
  take(href: string): Prefetched | Promise<Prefetched | undefined> | undefined;
  prefetch(href: string): Promise<void>;
  drop(): void;
}

export type IntentFactory = (host: HistoryHost) => Prefetcher;

export interface Router {
  visit(url: string, options?: VisitOptions): Promise<void>;
  prefetch(url: string): Promise<void>;
  flush(): void;
  configure(options: RouterOptions): void;
}

export type RouterKernel = Pick<
  Kernel,
  | "partial"
  | "context"
  | "nonce"
  | "onReady"
  | "scripts"
  | "load"
  | "module"
  | "serve"
  | "served"
  | "hook"
  | "fire"
  | "headers"
  | "arm"
  | "parse"
  | "sweep"
>;

interface How extends VisitOptions {
  trigger?: Element;
  pop?: boolean;
}

// The ops a navigation reads or fits, every other op passes through it untouched.
type Steered = ContextPatch | LayerOpenPatch | MorphPatch | UrlPatch | VisitPatch;

interface Slotted {
  target: Target;
  bounds: Bounds;
  op: MorphPatch;
}

interface Plan {
  slotted?: Slotted;
  away?: string;
  broken: boolean;
  layer: boolean;
}

// What the router keeps between visits.
interface State {
  cfg: Settings;
  // The layouts on screen, outer first, which a navigation declares to the server.
  chain: Layout[];
  // The key of the history entry on screen, the one its snapshot and scroll live under.
  key: string;
  // The path the router last committed, so a pop to the same document is a hash move.
  shown: string;
  // Whether the page on screen may be snapshotted when the visitor leaves it.
  snap: boolean;
  // The visit in flight, aborted by the next one, and its bare URL, which a prefetch of
  // the same page leaves alone.
  active: AbortController | undefined;
  bound: string;
  // Counts visits, so a pop queued before a newer visit does not run after it.
  turns: number;
  // Clears the progress marks of the visits since the last commit.
  unmark: () => void;
  focus: RouterOptions["focus"];
  announce: RouterOptions["announce"];
  prefetcher: Prefetcher | undefined;
  // The route table of the pages scope, undefined until it has loaded.
  table: Table | undefined;
}

// The parts of the router every step of a visit reads.
interface Kit {
  kernel: RouterKernel;
  state: State;
  history: History;
  seams: HistoryHost;
  host: () => RouterHost;
}

// One soft visit in flight, its target and what it learnt so far.
interface Visit {
  target: URL;
  how: How;
  // The URL the answer came from, a redirect moving it off the target.
  url: URL;
  controller: AbortController;
  signal: AbortSignal;
  replace: boolean;
  motion: string;
  // The entry the visit leaves, whose snapshot and scroll it saves.
  from: string;
  status: string;
  lands: number;
  // A scroll target below the end of a still streaming page is tried once more at the end.
  retryScroll: () => unknown;
  clearProgress: () => void;
}

const IDLE = 2000;
const DROPPED: Outcome = { status: "dropped", applied: false };
const PENDING = ATTR_PENDING;
const NAVIGATING = ATTR_NAVIGATING;
const BUSY = "aria-busy";
// The links the router leaves alone, the ones Speculation Rules leaves to it among them.
const OFF = `[${ATTR_LAYER}],[${ATTR_MERGE}],[${ATTR_FETCH}],[${ATTR_RETRY}],${ROUTER_OFF}`;

/** The default progress bar, shown while `<html>` holds the navigating mark. */
export const PROGRESS =
  `html[${ATTR_NAVIGATING}]::before{content:"";position:fixed;inset:0 0 auto;` +
  "z-index:2147483647;height:var(--next-progress-height,3px);" +
  "background:var(--next-progress-color,LinkText);transform-origin:0 0;" +
  "pointer-events:none;animation:next-progress var(--next-progress-duration,8s) " +
  "cubic-bezier(.1,.7,.2,1) both}" +
  "@keyframes next-progress{from{transform:scaleX(.1)}to{transform:scaleX(.95)}}" +
  `@media (prefers-reduced-motion:reduce){html[${ATTR_NAVIGATING}]::before` +
  "{animation:none;transform:none}}";

const currentPath = (): string => location.pathname + location.search;

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
        if (bounds) found.slotted = { target, bounds, op };
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

const emitRouter = (kit: Kit, name: string, detail: Record<string, unknown>): void =>
  void kit.kernel.fire(`router:${name}`, detail);

function leaveFor(kit: Kit, href: string): void {
  const url = new URL(href, document.baseURI);
  if (/^https?:$/.test(url.protocol)) kit.host().assign(url.href);
}

function flush(kit: Kit): void {
  kit.history.flush();
  kit.state.prefetcher?.drop();
}

const region = (kit: Kit, target: Target): Bounds | undefined =>
  locate(document, kit.state.chain, target);

async function loadTable(kit: Kit): Promise<void> {
  const { routes } = kit.state.cfg;
  if (!routes) return;
  try {
    const response = await fetch(routes, { mode: "same-origin" });
    kit.state.table = readTable((await response.json()) as Wire);
  } catch {
    // Without its table the router leaves every link to the browser.
  }
}

function owned(kit: Kit, url: URL, el?: Element): boolean {
  const { cfg, table } = kit.state;
  const inside = !!el?.closest(`[${ATTR_ROUTER}]`);
  return (
    url.origin === location.origin &&
    !(url.hash && url.pathname + url.search === currentPath()) &&
    !el?.closest(OFF) &&
    ("routes" in cfg
      ? inside || isPage(table, url)
      : !el || cfg.scope !== "opt_in" || inside) &&
    !cfg.exclude?.some((excluded) => url.pathname.startsWith(excluded)) &&
    !kit.history.foreign(url.pathname + url.search)
  );
}

// A navigation names the layouts on screen, so the server sends only the slot that
// changed. The declaration stays under the 1 KB the server reads of the header.
function requestPage(
  kit: Kit,
  href: string,
  signal: AbortSignal,
  extra: Record<string, string> = {},
): Promise<Response> {
  const { cfg, chain } = kit.state;
  const declared = chain.slice(0, MAX_LAYOUTS).map(([id, digest]) => `${id}.${digest}`);
  while (declared.join().length > 1024) declared.pop();
  return fetch(href, {
    headers: kit.kernel.headers({
      [HEADER_NAVIGATE]: "1",
      [HEADER_LAYOUTS]: declared.join(),
      [HEADER_ORIGIN]: currentPath(),
      ...(cfg.boundaries || kit.kernel.served("read") ? { Accept: STREAM_ACCEPT } : {}),
      ...extra,
    }),
    signal,
    mode: "same-origin",
  });
}

// The reader serves a streamed answer, fetched by URL when the page did not declare it.
const loadReader = (kit: Kit): unknown =>
  kit.kernel.served("read") ?? kit.kernel.load("boundaries", kit.state.cfg.boundaries);

// The prefetch of the intent chunk, built once its bundle has evaluated.
const intent = (kit: Kit): Prefetcher | undefined =>
  (kit.state.prefetcher ??= kit.kernel.module("intent")?.(kit.seams));

// The prefetch is an enhancement, so a visit never waits for its chunk.
async function loadIntent(kit: Kit): Promise<Prefetcher | undefined> {
  await kit.kernel.load("intent", kit.state.cfg.intent);
  return intent(kit);
}

// The innermost slot is what changes on screen, so it alone is marked busy, once the
// visit outlives the progress delay so a quick one shows no bar.
function markProgress(kit: Kit, trigger: Element | undefined): void {
  const { state } = kit;
  const area =
    region(kit, { slot: state.chain.at(-1)?.[0] ?? "" })?.[0] ?? document.body;
  const html = document.documentElement;
  trigger?.setAttribute(PENDING, "");
  const timer = setTimeout(
    () => {
      html.setAttribute(NAVIGATING, "");
      area.setAttribute(BUSY, "true");
    },
    (state.cfg.progress_delay ?? 0.15) * 1000,
  );
  const previous = state.unmark;
  state.unmark = () => {
    previous();
    clearTimeout(timer);
    trigger?.removeAttribute(PENDING);
    html.removeAttribute(NAVIGATING);
    area.removeAttribute(BUSY);
  };
}

function visitHard(
  kit: Kit,
  visit: Visit,
  error?: unknown,
  href = visit.target.href,
): void {
  if (visit.signal.aborted) return;
  if (error !== undefined) emitRouter(kit, "error", { url: visit.target.href, error });
  visit.status = "hard";
  visit.controller.abort();
  leaveFor(kit, href);
}

// Record what a landed navigation envelope says about the page now on screen.
function commit(kit: Kit, record: Record<string, unknown>, keepable: boolean): void {
  const { state } = kit;
  state.chain = (record.layouts as [string, string, string | null][]).map(
    ([id, digest, responseId], index) => [
      id,
      digest,
      responseId ?? state.chain[index]?.[2] ?? "",
    ],
  );
  if (record.shared === true) kit.history.share(currentPath());
  state.snap = keepable;
  state.shown = currentPath();
  state.key = entryKey(kit);
  kit.kernel.sweep();
}

// Move scroll and focus for the first envelope of a visit, as a full load would.
function arrive(kit: Kit, visit: Visit, nodes: readonly Element[]): void {
  const { state, history } = kit;
  const { pop, scroll } = visit.how;
  const restoreScroll = (): boolean =>
    pop ? history.place(visit.from) : history.top(visit.url.hash);
  if (scroll !== false && !restoreScroll()) visit.retryScroll = restoreScroll;
  if (pop || scroll === false || !(state.focus ?? history.focus)(nodes))
    (state.announce ?? history.announce)(document.title);
  visit.clearProgress();
  emitRouter(kit, "commit", { url: visit.url.href });
}

// Land one envelope of the visit. The first one swaps the slot and moves focus and
// scroll, a later one (the full page behind a prefetched shell) only fills it.
function landEnvelope(
  kit: Kit,
  visit: Visit,
  raw: unknown,
  response: Response,
  inspect?: (detail: BeforeApply) => void,
): Promise<Outcome> {
  const { kernel, state, history } = kit;
  const { pop } = visit.how;
  return new Promise((resolve) => {
    const first = visit.lands++ === 0;
    const record: Record<string, unknown> = isRecord(raw) ? raw : {};
    const { layouts } = record;
    const keepable = !says(response, ROUTER_NO_SNAPSHOT);
    const { slotted, away, broken, layer } = plan(
      Array.isArray(record.ops) ? (record.ops as Steered[]) : [],
      (target) => region(kit, target),
      kernel.context(),
      visit.replace || pop === true || !first,
      visit.target.hash,
    );
    const shaped = Array.isArray(layouts) || (layer && state.cfg.intercept === "links");
    if (visit.signal.aborted || broken || !isRecord(raw) || !shaped) {
      if (says(response, ROUTER_CLEAR)) flush(kit);
      // Only a DEBUG server names the script that made it send the page to load.
      if (isRecord(record.dropped))
        console.warn(
          "[next] a script this navigation could not run loads the page instead",
          record.dropped,
        );
      visitHard(kit, visit, undefined, away);
      resolve(DROPPED);
      return;
    }
    try {
      const turn =
        first && slotted
          ? history.swap(record, slotted.op, slotted.bounds, visit.motion)
          : undefined;
      kernel.partial.apply(raw, {
        response,
        owner: pop ? visit.target.pathname + visit.target.search : undefined,
        isCurrent: () => !visit.signal.aborted,
        // The slot is saved and the transition held before the ops replace it.
        inspect: (detail) => {
          const { hold } = detail;
          if (first && !pop)
            history.leave(visit.from, state.snap ? slotted?.target : undefined);
          turn?.hold(hold);
          const nodes = slotted ? between(slotted.bounds) : [];
          emitRouter(kit, "before-swap", { url: visit.url.href, layouts, hold, nodes });
          inspect?.(detail);
        },
        before: turn?.before,
        done: ({ envelope, ok, nodes = [] }) => {
          turn?.after(envelope, nodes, ok);
          if (!ok) {
            visitHard(kit, visit, new Error("not applied"));
            return resolve(DROPPED);
          }
          resolve({ status: "ok", applied: true, envelope });
          if (!Array.isArray(layouts)) return;
          if (envelope.csrf) {
            kernel.partial.setCsrf(envelope.csrf);
            flush(kit);
          }
          commit(kit, record, keepable);
          if (first) arrive(kit, visit, nodes);
        },
      });
    } catch (error) {
      visitHard(kit, visit, error);
      resolve(DROPPED);
    }
  });
}

// The page itself: a pressed flight's answer, a request, or a plain document for a URL a
// shared cache may hold, each read as the envelope, stream or markup it is.
async function fetchPage(kit: Kit, visit: Visit, found?: Prefetched): Promise<void> {
  const { kernel, state, history } = kit;
  const { target, signal } = visit;
  const path = target.pathname + target.search;
  try {
    const plainly = history.plain(path);
    if (plainly) found?.flight?.stop();
    const response = plainly
      ? await fetch(target.href, { signal, mode: "same-origin" })
      : ((found?.flight && (await found.flight.answer)) ??
        (await requestPage(kit, target.href, signal)));
    visit.url = new URL(response.url || target.href);
    visit.url.hash = target.hash;
    const at = visit.url.pathname + visit.url.search;
    const type = mime(response);
    const streamed = type === EVENT_STREAM;
    const markup = type === "text/html" && (plainly || state.cfg.foreign === "extract");
    if (streamed) await loadReader(kit);
    const reader = streamed ? kernel.served("read") : undefined;
    if (!response.ok) visitHard(kit, visit);
    else if (reader)
      await reader(response, {
        signal,
        open: () => undefined,
        land: (_body, parse, inspect) =>
          landEnvelope(kit, visit, parse(), response, inspect),
      });
    else if (!plainly && type === (state.cfg.type ?? CONTENT_TYPE))
      await landEnvelope(
        kit,
        visit,
        kernel.parse(type, await response.text()),
        response,
      );
    else if (markup) {
      history.share(path);
      await landEnvelope(
        kit,
        visit,
        history.extract(await response.text(), at),
        response,
      );
    } else {
      if (!streamed) history.remember(at);
      visitHard(kit, visit);
    }
  } catch (error) {
    visitHard(kit, visit, error);
  }
}

// One soft visit: the prefetched or pressed answer if there is one, the page request
// otherwise, landed through the applier with the history swap around it. Any answer it
// cannot land soft ends in a hard visit of the same URL.
async function navigate(kit: Kit, target: URL, how: How = {}): Promise<void> {
  const { kernel, state } = kit;
  const { trigger, pop } = how;
  const hit = pop ? undefined : intent(kit)?.take(target.href);
  const detail = { url: target.href, trigger, cached: hit !== undefined };
  // A vetoed or an overtaken visit stops a pressed flight, even one still on its way.
  const drop = (): void =>
    void Promise.resolve(hit).then((found) => found?.flight?.stop());
  if (!pop && !kernel.fire("router:before", detail, true)) return drop();
  state.active?.abort();
  state.turns += 1;
  const controller = (state.active = new AbortController());
  const { signal } = controller;
  signal.addEventListener("abort", drop);
  state.bound = bare(target.href);
  const replace = how.replace === true || kernel.partial.layers.size() > 0;
  // A server that never answers costs a hard visit, not a page stuck navigating.
  const disarm = kernel.arm(controller);
  const visit: Visit = {
    target,
    how,
    url: target,
    controller,
    signal,
    replace,
    motion: pop ? "back" : replace ? "replace" : "forward",
    from: state.key,
    status: "complete",
    lands: 0,
    retryScroll: () => 0,
    clearProgress: () => {
      disarm();
      if (state.active !== controller) return;
      state.unmark();
      state.unmark = () => undefined;
    },
  };
  signal.addEventListener("abort", () => {
    if ((signal.reason as Error).name !== "TimeoutError") return;
    emitRouter(kit, "error", { url: target.href, error: signal.reason });
    visit.status = "hard";
    leaveFor(kit, target.href);
  });
  while (kernel.partial.layers.size() > 0)
    kernel.partial.layers.close({ dismiss: true, reason: "navigate" });
  emitRouter(kit, "start", { url: target.href });
  markProgress(kit, trigger);
  try {
    const found = hit instanceof Promise ? await hit : hit;
    const whole = found?.raw?.complete === true;
    if (whole) found.flight?.stop();
    // A kept shell lands at once while the page request runs beside it.
    const { raw, response } = found ?? {};
    await Promise.all([
      raw && response && landEnvelope(kit, visit, raw, response),
      whole || fetchPage(kit, visit, found),
    ]);
    if (!signal.aborted) visit.retryScroll();
  } finally {
    visit.clearProgress();
    if (state.active === controller) {
      state.active = undefined;
      state.bound = "";
    }
    emitRouter(kit, "end", {
      url: visit.url.href,
      status:
        signal.aborted && visit.status === "complete" ? "superseded" : visit.status,
    });
  }
}

const entryKey = (kit: Kit): string =>
  (kit.host().state() as Partial<Entry> | null | undefined)?.key ?? "";

const stampEntry = (kit: Kit): void =>
  kit.host().replace({ next: 1, kind: "nav", key: kit.state.key }, location.href);

// The runtime asks the router first on popstate. A pop it owns restores the snapshot or
// revisits the entry, and a hash move on the same document only scrolls.
function popped(kit: Kit): string | undefined {
  const { state, history } = kit;
  const entry = kit.host().state() as Partial<Entry> | null | undefined;
  const from = state.key;
  if (entry?.next === 1) state.key = entry.key ?? "";
  if (currentPath() === state.shown) {
    if (location.hash) history.top(location.hash);
    return undefined;
  }
  if (entry?.next !== 1) return undefined;
  const path = (state.shown = currentPath());
  const turn = state.turns;
  queueMicrotask(() => {
    if (turn !== state.turns) return;
    const at = history.pop(from, state.key, state.snap, path);
    if (at !== undefined && Date.now() - at < (state.cfg.prefetch_ttl ?? 30) * 1000) {
      state.active?.abort();
      state.snap = true;
    } else
      void navigate(kit, new URL(location.href), {
        pop: true,
        scroll: at === undefined,
      });
  });
  return path;
}

export function createRouter(
  kernel: RouterKernel,
  routerHost: RouterHost = defaultRouterHost(),
  listening: AddEventListenerOptions = {},
  makeHistory: (
    seams: HistoryHost,
    listening: AddEventListenerOptions,
  ) => History = createHistory,
): Router {
  const doc = document;
  let host = routerHost;
  let stop = (): void => undefined;
  let styled = false;
  const state: State = {
    cfg: {},
    chain: [],
    key: "",
    shown: "",
    snap: true,
    active: undefined,
    bound: "",
    turns: 0,
    unmark: () => undefined,
    focus: undefined,
    announce: undefined,
    prefetcher: undefined,
    table: undefined,
  };
  const seams: HistoryHost = {
    partial: kernel.partial,
    headers: kernel.headers,
    parse: kernel.parse,
    serve: kernel.serve,
    hook: kernel.hook,
    scripts: kernel.scripts,
    nonce: kernel.nonce,
    settings: () => state.cfg,
    context: kernel.context,
    region: (target) => region(kit, target),
    chain: (list) => (state.chain = list ?? state.chain),
    key: () => state.key,
    ask: (href, signal, extra) => requestPage(kit, href, signal, extra),
    owned: (url, el) => owned(kit, url, el),
    plain: (path) => kit.history.plain(path),
    going: () => state.bound,
    flush: () => flush(kit),
  };
  const kit: Kit = {
    kernel,
    state,
    seams,
    history: makeHistory(seams, listening),
    host: () => host,
  };

  const onActivation = (event: Event): void => {
    const el = event.target instanceof Element ? event.target : null;
    let trigger = el;
    let url: URL | undefined;
    if (event.type === "submit") url = submitUrl(event);
    else {
      trigger = el?.closest("a[href]") ?? null;
      if (
        trigger instanceof HTMLAnchorElement &&
        isPlainActivation(event, trigger) &&
        !trigger.matches("[rel~=external]")
      )
        url = new URL(trigger.href);
    }
    if (!url || !trigger || !owned(kit, url, trigger)) return;
    event.preventDefault();
    const scroll = !trigger.closest(`[${ATTR_SCROLL}=preserve]`);
    void navigate(kit, url, { trigger, scroll });
  };

  const refreshKey = (): void => {
    if (kernel.partial.layers.size() === 0) state.key = entryKey(kit) || state.key;
  };

  // The progress bar rides the router, so a page the router does not serve has none.
  const style = (): void => {
    if (styled || !state.cfg.progress) return;
    styled = true;
    const sheet = doc.createElement("style");
    if (kernel.nonce) sheet.nonce = kernel.nonce;
    sheet.textContent = PROGRESS;
    doc.head.append(sheet);
  };

  const boot = (): void => {
    const context = kernel.context();
    state.cfg = (isRecord(context.$router) ? context.$router : {}) as Settings;
    state.chain = layoutsOf(doc);
    state.snap = state.cfg.snapshot !== false;
    state.shown = currentPath();
    const entry = host.state() as Partial<Entry> | null | undefined;
    const kept = entry?.next === 1 ? (entry.key ?? "") : "";
    state.key = kept || uid();
    if (!entry || entry.next === 1) stampEntry(kit);
    style();
    // A bundle that landed before the router booted is built at once.
    intent(kit);
    // A reload of an entry the router wrote returns to its scroll, once more after the
    // load when the page was still too short.
    if (kept && !kit.history.place(kept))
      addEventListener("load", () => kit.history.place(kept), { once: true });
    stop();
    stop = host.listen(onActivation);
    void loadTable(kit);
    emitRouter(kit, "end", { url: location.href, status: "complete", initial: true });
    // The intent chunk marks the active links of the containers no render marked, so a
    // page holding one fetches it at once rather than once idle.
    if (unmarked(state.cfg).length > 0) void loadIntent(kit);
    const idle = (): void => {
      void loadIntent(kit);
      void loadReader(kit);
    };
    void (
      (window as Partial<Window>).requestIdleCallback?.(idle, { timeout: IDLE }) ??
      setTimeout(idle)
    );
  };

  kernel.serve("slot", (target) => region(kit, target));
  kernel.serve("pop", () => popped(kit));
  kernel.hook("before", ORDER_CLEAR, ({ response }) => {
    if (!says(response, ROUTER_CLEAR)) return;
    flush(kit);
    state.snap = false;
  });
  doc.addEventListener(
    "partial:before-request",
    (event) => {
      if ((event as CustomEvent<{ method: string }>).detail.method !== "GET")
        flush(kit);
    },
    listening,
  );
  doc.addEventListener("partial:layer-accepted", refreshKey, listening);
  doc.addEventListener("partial:layer-dismissed", refreshKey, listening);
  addEventListener(
    "hashchange",
    () => {
      if (host.state()) return;
      state.key = uid();
      stampEntry(kit);
    },
    listening,
  );
  kernel.onReady(boot);
  // The harness tears a router down between cases, a page keeps its one router.
  TEST: listening.signal?.addEventListener("abort", () => stop());

  return {
    visit(href, options = {}) {
      const url = new URL(href, doc.baseURI);
      if (owned(kit, url)) return navigate(kit, url, options);
      leaveFor(kit, url.href);
      return Promise.resolve();
    },
    async prefetch(href) {
      await (await loadIntent(kit))?.prefetch(new URL(href, doc.baseURI).href);
    },
    flush: () => flush(kit),
    configure(options) {
      state.focus = options.focus ?? state.focus;
      state.announce = options.announce ?? state.announce;
      if (options.host) {
        stop();
        host = options.host;
        stop = host.listen(onActivation);
      }
    },
  };
}
