import { defaultObserver } from "./adapters";
import { assetLoad } from "./apply";
import { inPlace, isPlainActivation } from "./events";
import { CONTENT_TYPE, HEADER_VERSION, isRecord, matching } from "./protocol";
import type { HistoryHost, Keeper, Prefetched } from "./router";
import type { IntersectionAdapter } from "./triggers";

type Wish = [
  urgent: boolean,
  settle: () => void,
  done: Promise<void>,
  el?: Element | undefined,
  stop?: (() => void) | undefined,
  controller?: AbortController,
];

type Cached = [
  expires: number,
  raw: Record<string, unknown>,
  response: Response,
  stored: number,
];

type Press = [
  url: string,
  key: string,
  flight: Promise<Response | undefined>,
  controller: AbortController,
  timer: ReturnType<typeof setTimeout>,
  link: HTMLAnchorElement,
  response?: Response,
];

type Net = Navigator & { connection?: { saveData?: boolean; effectiveType?: string } };

const SKIP =
  "[download],[rel~=external],[data-next-prefetch=off],[data-next-confirm]," +
  "[data-next-action]";
const MODE = "data-next-prefetch";
const PRESS = "data-next-press";
const DEPTH = "X-Next-Prefetch";
const VIEWPORT = `[${MODE}=viewport] a[href],a[href][${MODE}=viewport]`;
const RETRY = 30;
const LIMIT = 3;
const HOLD = 1000;

const bare = (href: string): string => href.replace(/#.*/s, "");

export function createPrefetch(
  host: HistoryHost,
  listening: AddEventListenerOptions = {},
  observer: IntersectionAdapter = defaultObserver(),
): Pick<Keeper, "take" | "prefetch" | "drop"> {
  const doc = document;
  const cfg = host.settings;
  const cache = new Map<string, Cached>();
  const wishes = new Map<string, Wish>();
  const refused = new Set<string>();
  const tried = new Set<string>();
  const seen = new WeakSet<Element>();
  let pause = 0;
  let wait = RETRY;
  let aimed: HTMLAnchorElement | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let pressed: Press | undefined;

  const keyOf = (href: string): string => {
    const chain = host.chain().map((layout) => layout[0] + layout[1]);
    return `${bare(href)} ${chain.join()}`;
  };

  const read = async (response: Response): Promise<unknown> => {
    const type = (response.headers.get("content-type") ?? "").replace(/;.*/, "").trim();
    return type === (cfg().type ?? CONTENT_TYPE)
      ? host.partial._parse(type, await response.text())
      : undefined;
  };

  const warm = (assets: unknown, urgent: boolean): void => {
    const known = new Set(
      [...doc.querySelectorAll<HTMLLinkElement>("link[href]")].map((el) => el.href),
    );
    for (const asset of Array.isArray(assets) ? (assets as unknown[]) : []) {
      if (
        !isRecord(asset) ||
        typeof asset.url !== "string" ||
        !asset.url ||
        assetLoad(asset.kind, asset.load, asset.inline) !== "link"
      )
        continue;
      const link = doc.createElement("link");
      link.rel = urgent ? "preload" : "prefetch";
      link.as = "style";
      link.href = asset.url;
      if (known.has(link.href)) continue;
      known.add(link.href);
      if (host.nonce) link.nonce = host.nonce;
      doc.head.append(link);
    }
  };

  const room = (): boolean => {
    const now = Date.now();
    const stale = now - 1000 * (cfg().prefetch_ttl ?? 30);
    const pointed = aimed ? keyOf(aimed.href) : "";
    const limit = (cfg().prefetch_entries ?? 16) - flights();
    for (const [key, entry] of cache)
      if (
        entry[0] <= now ||
        (cache.size >= limit && entry[3] <= stale && key !== pointed)
      )
        cache.delete(key);
    return cache.size < limit;
  };

  const flights = (): number => {
    let flying = 0;
    for (const wish of wishes.values()) if (wish[5]) flying += 1;
    return flying;
  };

  const heed = (url: string, response: Response): void => {
    const { status, headers } = response;
    const after = Number(headers.get("Retry-After"));
    if (status === 204) refused.add(url);
    if (status === 429 || status > 499) {
      pause = Date.now() + 1000 * (after > 0 ? after : wait);
      if (!(after > 0)) wait = Math.min(wait * 2, 300);
      for (const [queued, held] of wishes)
        if (!held[5]) {
          wishes.delete(queued);
          held[1]();
        }
    } else if (response.ok) wait = RETRY;
  };

  const keep = (
    key: string,
    raw: unknown,
    response: Response,
    urgent: boolean,
  ): boolean => {
    if (!isRecord(raw) || !Array.isArray(raw.layouts)) return false;
    const now = Date.now();
    const life =
      raw.complete === true
        ? (cfg().prefetch_ttl ?? 30)
        : (cfg().prefetch_shell_ttl ?? 300);
    cache.delete(key);
    cache.set(key, [now + 1000 * life, raw, response, now]);
    for (const old of cache.keys())
      if (cache.size > (cfg().prefetch_entries ?? 16)) cache.delete(old);
    warm(raw.assets, urgent);
    return true;
  };

  const release = (kept = true): void => {
    const held = pressed;
    if (!held) return;
    pressed = undefined;
    clearTimeout(held[4]);
    const response = held[6];
    if (!kept || !response || response.redirected) held[3].abort();
    else
      void read(response)
        .then((raw) => keep(held[1], raw, response, true))
        .catch(() => false)
        .then((stored) => stored || held[3].abort());
  };

  const press = (link: HTMLAnchorElement): void => {
    const url = bare(link.href);
    if (pressed?.[0] === url) return;
    release();
    const key = keyOf(url);
    const found = cache.get(key);
    const queued = wishes.get(url);
    if (
      (found && found[0] > Date.now() && found[1].complete === true) ||
      refused.has(url) ||
      pause > Date.now()
    )
      return;
    if (queued && !queued[5]) {
      wishes.delete(url);
      queued[1]();
    }
    const controller = new AbortController();
    const flight = host.ask(url, controller.signal, { [DEPTH]: "full" }).then(
      (response) => {
        heed(url, response);
        if (!response.ok || response.status === 204) return undefined;
        held[6] = response;
        return response;
      },
      () => undefined,
    );
    const held: Press = (pressed = [
      url,
      key,
      flight,
      controller,
      setTimeout(release, HOLD),
      link,
    ]);
  };

  const fly = (url: string, wish: Wish): void => {
    const controller = (wish[5] = new AbortController());
    const { signal } = controller;
    const key = keyOf(url);
    void host
      .ask(url, signal, { [DEPTH]: "1" })
      .then(async (response) => {
        heed(url, response);
        if (response.headers.get("X-Next-Router")?.includes("clear")) host.flush();
        const raw = response.ok && !response.redirected && (await read(response));
        if (signal.aborted) return;
        tried.add(url);
        wish[4]?.();
        keep(key, raw, response, wish[0]);
      })
      .catch(() => undefined)
      .then(() => {
        wishes.delete(url);
        wish[1]();
        pump();
      });
  };

  const pump = (): void => {
    for (;;) {
      let pick: [string, Wish] | undefined;
      for (const entry of wishes)
        if (!entry[1][5] && (entry[1][0] || !pick)) pick = entry;
      const flying = flights();
      if (!pick || flying >= LIMIT) return;
      const [url, wish] = pick;
      if (wish[3]?.isConnected === false) {
        wishes.delete(url);
        wish[4]?.();
        wish[1]();
        continue;
      }
      if (!wish[0] && (flying >= LIMIT - 1 || !room())) return;
      fly(url, wish);
    }
  };

  const want = (
    href: string,
    urgent: boolean,
    el?: Element,
    stop?: () => void,
  ): Promise<void> => {
    const url = bare(href);
    const held = wishes.get(url);
    if (held) {
      if (stop) {
        held[3] = el;
        held[4] = stop;
      }
      if (urgent && !held[5]) {
        held[0] = true;
        wishes.delete(url);
        wishes.set(url, held);
        pump();
      }
      return held[2];
    }
    if (
      pressed?.[0] === url ||
      host.going() === url ||
      !host.owned(new URL(url)) ||
      (cache.get(keyOf(url))?.[0] ?? 0) > Date.now() ||
      refused.has(url) ||
      pause > Date.now()
    )
      return Promise.resolve();
    let settle!: () => void;
    const done = new Promise<void>((resolve) => (settle = resolve));
    wishes.set(url, [urgent, settle, done, el, stop]);
    pump();
    return done;
  };

  const forget = (el: Element | undefined, pointer = false): void => {
    const url = el instanceof HTMLAnchorElement ? bare(el.href) : "";
    const wish = wishes.get(url);
    if (!wish || wish[5] || wish[3] !== el) return;
    if (pointer && wish[4]) wish[0] = false;
    else {
      wishes.delete(url);
      wish[1]();
    }
  };

  const claim = (url: string): Press | undefined => {
    const held = pressed;
    if (held?.[0] !== url) return undefined;
    pressed = undefined;
    clearTimeout(held[4]);
    return held;
  };

  const take = (
    href: string,
    held = claim(bare(href)),
  ): Prefetched | Promise<Prefetched | undefined> | undefined => {
    const url = bare(href);
    const wish = wishes.get(url);
    if (wish?.[5]) return wish[2].then(() => take(href, held));
    if (wish) {
      wishes.delete(url);
      wish[1]();
    }
    const key = keyOf(url);
    const found = cache.get(key);
    const known = host.partial._headers({}).get(HEADER_VERSION);
    cache.delete(key);
    const kept =
      found && found[0] > Date.now() && !(known && known !== found[1].version)
        ? found
        : undefined;
    if (!kept && !held) return undefined;
    return {
      raw: kept?.[1],
      response: kept?.[2],
      flight: held?.[2],
      stop: held && (() => held[3].abort()),
    };
  };

  const check = (el: Element | null): HTMLAnchorElement | undefined =>
    el instanceof HTMLAnchorElement &&
    inPlace(el.getAttribute("target")) &&
    !el.closest(SKIP) &&
    host.owned(new URL(el.href), el)
      ? el
      : undefined;

  const eager = (): boolean => {
    const net = (navigator as Net).connection;
    return (
      cfg().prefetch !== "off" &&
      !doc.hidden &&
      !net?.saveData &&
      !(net?.effectiveType ?? "").includes("2g")
    );
  };

  const intend = (link: HTMLAnchorElement): void => {
    if (eager()) void want(link.href, true, link);
  };

  const full = (event: Event, link: HTMLAnchorElement): boolean => {
    const mode = link.closest(`[${PRESS}]`)?.getAttribute(PRESS) ?? cfg().press;
    return (
      event.type === "pointerdown" &&
      (mode === "all" ||
        (mode !== "off" && (event as PointerEvent).pointerType === "mouse")) &&
      isPlainActivation(event, link) &&
      !host.plain(link.pathname + link.search) &&
      eager()
    );
  };

  const aim = (event: Event): void => {
    const target =
      event.target instanceof Element ? event.target.closest("a[href]") : null;
    const urgent = /down|start/.test(event.type);
    if (!urgent && target === aimed) return;
    const link = check(target);
    if (urgent) {
      if (link && full(event, link)) press(link);
      else if (link) intend(link);
      return;
    }
    clearTimeout(timer);
    aimed = link;
    if (link) timer = setTimeout(intend, 1000 * (cfg().prefetch_delay ?? 0.065), link);
  };

  const leave = (event: Event): void => {
    if (aimed?.contains((event as MouseEvent).relatedTarget as Node | null)) return;
    clearTimeout(timer);
    forget(aimed, true);
    aimed = undefined;
  };

  const watch = (root: ParentNode): void => {
    const all = cfg().prefetch === "viewport";
    if (cfg().prefetch === "off") return;
    for (const el of matching(root, all ? "a[href]" : VIEWPORT)) {
      if (seen.has(el)) continue;
      seen.add(el);
      const mode = el.closest(`[${MODE}]`)?.getAttribute(MODE) ?? cfg().prefetch;
      const link = check(el);
      if (mode !== "viewport" || !link) continue;
      const stop = observer.observe(
        el,
        (visible) => {
          if (tried.has(bare(link.href))) stop();
          else if (!visible) forget(el);
          else if (eager()) void want(link.href, false, el, stop);
        },
        "",
      );
    }
  };

  for (const type of ["pointerover", "focusin", "pointerdown", "touchstart"])
    doc.addEventListener(type, aim, { ...listening, passive: true });
  for (const type of ["pointerout", "focusout"])
    doc.addEventListener(type, leave, listening);
  for (const type of ["pointercancel", "dragstart", "contextmenu", "router:start"])
    doc.addEventListener(type, () => release(), listening);
  doc.addEventListener(
    "pointerup",
    (event) => {
      const { clientX, clientY } = event;
      if (!pressed?.[5].contains(doc.elementFromPoint(clientX, clientY))) release();
    },
    listening,
  );
  doc.addEventListener(
    "partial:applied",
    (event) =>
      (event as CustomEvent<{ nodes?: Element[] }>).detail.nodes?.forEach(watch),
    listening,
  );
  addEventListener(
    "pageshow",
    (event) => {
      if (event.persisted) cache.clear();
    },
    listening,
  );
  watch(doc);

  return {
    take: (href) => take(href),
    prefetch: (href) => want(href, true),
    drop: () => {
      release(false);
      for (const wish of wishes.values()) wish[5]?.abort();
      cache.clear();
    },
  };
}
