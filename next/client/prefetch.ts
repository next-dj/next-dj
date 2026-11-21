import { defaultObserver } from "./adapters";
import { assetLoad } from "./apply";
import { inPlace } from "./events";
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

type Net = Navigator & { connection?: { saveData?: boolean; effectiveType?: string } };

const SKIP =
  "[download],[rel~=external],[data-next-prefetch=off],[data-next-confirm]," +
  "[data-next-action]";
const MODE = "data-next-prefetch";
const VIEWPORT = `[${MODE}=viewport] a[href],a[href][${MODE}=viewport]`;
const RETRY = 30;
const LIMIT = 3;

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

  const fly = (url: string, wish: Wish): void => {
    const controller = (wish[5] = new AbortController());
    const { signal } = controller;
    const key = keyOf(url);
    void host
      .ask(url, signal, { "X-Next-Prefetch": "1" })
      .then(async (response) => {
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
        if (headers.get("X-Next-Router")?.includes("clear")) host.flush();
        const raw = response.ok && !response.redirected && (await read(response));
        if (signal.aborted) return;
        tried.add(url);
        wish[4]?.();
        if (!isRecord(raw) || !Array.isArray(raw.layouts)) return;
        const now = Date.now();
        const life =
          raw.complete === true
            ? (cfg().prefetch_ttl ?? 30)
            : (cfg().prefetch_shell_ttl ?? 300);
        cache.delete(key);
        cache.set(key, [now + 1000 * life, raw, response, now]);
        for (const old of cache.keys())
          if (cache.size > (cfg().prefetch_entries ?? 16)) cache.delete(old);
        warm(raw.assets, wish[0]);
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

  const take = (
    href: string,
  ): Prefetched | Promise<Prefetched | undefined> | undefined => {
    const url = bare(href);
    const wish = wishes.get(url);
    if (wish?.[5]) return wish[2].then(() => take(href));
    if (wish) {
      wishes.delete(url);
      wish[1]();
    }
    const key = keyOf(url);
    const found = cache.get(key);
    const known = host.partial._headers({}).get(HEADER_VERSION);
    cache.delete(key);
    if (!found || found[0] <= Date.now() || (known && known !== found[1].version))
      return undefined;
    return { raw: found[1], response: found[2] };
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

  const aim = (event: Event): void => {
    const target =
      event.target instanceof Element ? event.target.closest("a[href]") : null;
    const urgent = /down|start/.test(event.type);
    if (!urgent && target === aimed) return;
    const link = check(target);
    if (urgent) {
      if (link) intend(link);
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
    take,
    prefetch: (href) => want(href, true),
    drop: () => {
      for (const wish of wishes.values()) wish[5]?.abort();
      cache.clear();
    },
  };
}
