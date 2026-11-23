import { defaultObserver } from "./adapters";
import { assetLoad } from "./apply";
import { inPlace, isPlainActivation } from "./events";
import {
  ACCEPT,
  CONTENT_TYPE,
  HEADER_ORIGIN,
  HEADER_PREFETCH,
  HEADER_VERSION,
  HEADER_ZONE,
  bare,
  isRecord,
  matching,
  mime,
  says,
} from "./protocol";
import type { Flight, HistoryHost, Keeper, Prefetched } from "./router";
import type { IntersectionAdapter } from "./triggers";

interface Wish {
  urgent: boolean;
  settle: () => void;
  done: Promise<void>;
  el?: Element | undefined;
  stop?: (() => void) | undefined;
  controller?: AbortController;
}

interface Cached {
  expires: number;
  raw: Record<string, unknown>;
  response: Response;
  stored: number;
}

interface Press {
  url: string;
  key: string;
  link: HTMLAnchorElement;
  timer: ReturnType<typeof setTimeout>;
  flight: Flight;
  response?: Response;
}

type Net = Navigator & { connection?: { saveData?: boolean; effectiveType?: string } };

const SKIP =
  "[download],[rel~=external],[data-next-prefetch=off],[data-next-confirm]," +
  "[data-next-action]";
const MODE = "data-next-prefetch";
const LAYER = "data-next-layer";
const PRESS = "data-next-press";
const VIEWPORT = `[${MODE}=viewport] a[href],a[href][${MODE}=viewport]`;
const RETRY = 30;
const LIMIT = 3;
const HOLD = 1000;

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
  let touched: string | undefined;
  let x = NaN;
  let y = NaN;

  const keyOf = (id: string): string => {
    const chain = host.chain().map((layout) => layout[0] + layout[1]);
    return `${id} ${chain.join()}`;
  };

  // A layer link answers with its zone for the page it opens over, so both join its URL.
  const spot = (href: string, zone?: string | null): string =>
    zone ? `${bare(href)} ${zone} ${location.pathname + location.search}` : bare(href);

  const addr = (link: HTMLAnchorElement): string =>
    spot(link.href, link.getAttribute(LAYER));

  const ask = (id: string, signal: AbortSignal, depth: string): Promise<Response> => {
    const [url = "", zone, origin = ""] = id.split(" ");
    return zone
      ? fetch(url, {
          headers: host.partial._headers({
            Accept: ACCEPT,
            [HEADER_ZONE]: zone,
            [HEADER_ORIGIN]: origin,
            [HEADER_PREFETCH]: "full",
          }),
          signal,
          mode: "same-origin",
        })
      : host.ask(url, signal, { [HEADER_PREFETCH]: depth });
  };

  // A layer body stays unread for the layer to apply, so its record holds the facts
  // the cache reads. A navigation body is the envelope, kept when it lists layouts.
  const read = async (id: string, response: Response): Promise<unknown> => {
    const type = mime(response);
    if (!response.ok || response.redirected || type !== (cfg().type ?? CONTENT_TYPE))
      return undefined;
    if (id.includes(" "))
      return { complete: true, version: response.headers.get(HEADER_VERSION) };
    const raw = host.partial._parse(type, await response.text());
    return isRecord(raw) && Array.isArray(raw.layouts) ? raw : undefined;
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

  const discard = (entry?: Pick<Prefetched, "response">): void =>
    void entry?.response?.body?.cancel().catch(() => undefined);

  const evict = (key: string): void => {
    discard(cache.get(key));
    cache.delete(key);
  };

  const clear = (): void => {
    for (const key of cache.keys()) evict(key);
  };

  const room = (): boolean => {
    const now = Date.now();
    const stale = now - 1000 * (cfg().prefetch_ttl ?? 30);
    const pointed = aimed ? keyOf(addr(aimed)) : "";
    const limit = (cfg().prefetch_entries ?? 16) - flights();
    for (const [key, entry] of cache)
      if (
        entry.expires <= now ||
        (cache.size >= limit && entry.stored <= stale && key !== pointed)
      )
        evict(key);
    return cache.size < limit;
  };

  const flights = (): number => {
    let flying = 0;
    for (const wish of wishes.values()) if (wish.controller) flying += 1;
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
        if (!held.controller) {
          wishes.delete(queued);
          held.settle();
        }
    } else if (response.ok) wait = RETRY;
  };

  const keep = (
    key: string,
    raw: unknown,
    response: Response,
    urgent: boolean,
  ): boolean => {
    if (!isRecord(raw)) return false;
    const now = Date.now();
    const life =
      raw.complete === true
        ? (cfg().prefetch_ttl ?? 30)
        : (cfg().prefetch_shell_ttl ?? 300);
    evict(key);
    cache.set(key, { expires: now + 1000 * life, raw, response, stored: now });
    for (const old of cache.keys())
      if (cache.size > (cfg().prefetch_entries ?? 16)) evict(old);
    warm(raw.assets, urgent);
    return true;
  };

  const fresh = (found: Cached | undefined): Cached | undefined => {
    const known = host.partial._headers({}).get(HEADER_VERSION);
    return found &&
      found.expires > Date.now() &&
      !(known && known !== found.raw.version)
      ? found
      : undefined;
  };

  const release = (kept = true): void => {
    const held = pressed;
    if (!held) return;
    pressed = undefined;
    clearTimeout(held.timer);
    const { response } = held;
    if (!kept || !response) held.flight.stop();
    else
      void read(held.url, response)
        .then((raw) => keep(held.key, raw, response, true))
        .catch(() => false)
        .then((stored) => stored || held.flight.stop());
  };

  const press = (link: HTMLAnchorElement): void => {
    const url = addr(link);
    if (pressed?.url === url) return;
    release();
    const key = keyOf(url);
    const queued = wishes.get(url);
    if (
      fresh(cache.get(key))?.raw.complete === true ||
      (queued?.controller && url.includes(" ")) ||
      refused.has(url) ||
      pause > Date.now()
    )
      return;
    if (queued && !queued.controller) {
      wishes.delete(url);
      queued.settle();
    }
    const controller = new AbortController();
    const held: Press = {
      url,
      key,
      link,
      timer: setTimeout(release, HOLD),
      flight: {
        answer: ask(url, controller.signal, "full").then(
          (response) => {
            heed(url, response);
            const { status } = response;
            if (status === 204 || status === 429 || status > 499) return undefined;
            held.response = response;
            return response;
          },
          () => undefined,
        ),
        stop: () => controller.abort(),
      },
    };
    pressed = held;
  };

  const fly = (url: string, wish: Wish): void => {
    const controller = (wish.controller = new AbortController());
    const { signal } = controller;
    const key = keyOf(url);
    void ask(url, signal, "1")
      .then(async (response) => {
        heed(url, response);
        if (says(response, "clear")) host.flush();
        const raw = await read(url, response);
        if (signal.aborted) return;
        tried.add(url);
        wish.stop?.();
        if (!keep(key, raw, response, wish.urgent)) discard({ response });
      })
      .catch(() => undefined)
      .then(() => {
        wishes.delete(url);
        wish.settle();
        pump();
      });
  };

  const pump = (): void => {
    for (;;) {
      let pick: [string, Wish] | undefined;
      for (const entry of wishes)
        if (!entry[1].controller && (entry[1].urgent || !pick)) pick = entry;
      const flying = flights();
      if (!pick || flying >= LIMIT) return;
      const [url, wish] = pick;
      if (wish.el?.isConnected === false) {
        wishes.delete(url);
        wish.stop?.();
        wish.settle();
        continue;
      }
      if (!wish.urgent && (flying >= LIMIT - 1 || !room())) return;
      fly(url, wish);
    }
  };

  const want = (
    url: string,
    urgent: boolean,
    el?: Element,
    stop?: () => void,
  ): Promise<void> => {
    const held = wishes.get(url);
    if (held) {
      if (stop) {
        held.el = el;
        held.stop = stop;
      }
      if (urgent && !held.controller) {
        held.urgent = true;
        wishes.delete(url);
        wishes.set(url, held);
        pump();
      }
      return held.done;
    }
    if (
      pressed?.url === url ||
      host.going() === url ||
      !host.owned(new URL(url.split(" ")[0] ?? "")) ||
      fresh(cache.get(keyOf(url))) ||
      refused.has(url) ||
      pause > Date.now()
    )
      return Promise.resolve();
    let settle!: () => void;
    const done = new Promise<void>((resolve) => (settle = resolve));
    wishes.set(url, { urgent, settle, done, el, stop });
    pump();
    return done;
  };

  const forget = (el: Element | undefined, pointer = false): void => {
    const url = el instanceof HTMLAnchorElement ? addr(el) : "";
    const wish = wishes.get(url);
    if (!wish || wish.controller || wish.el !== el) return;
    if (pointer && wish.stop) wish.urgent = false;
    else {
      wishes.delete(url);
      wish.settle();
    }
  };

  const claim = (url: string): Press | undefined => {
    const held = pressed;
    if (held?.url !== url) return undefined;
    pressed = undefined;
    clearTimeout(held.timer);
    return held;
  };

  const adopt = (
    url: string,
    held: Press | undefined,
  ): Prefetched | Promise<Prefetched | undefined> | undefined => {
    const wish = wishes.get(url);
    if (touched === url) touched = undefined;
    if (wish?.controller) return wish.done.then(() => adopt(url, held));
    if (wish) {
      wishes.delete(url);
      wish.settle();
    }
    const key = keyOf(url);
    const kept = fresh(cache.get(key));
    if (kept) cache.delete(key);
    else evict(key);
    if (!kept && !held) return undefined;
    return { raw: kept?.raw, response: kept?.response, flight: held?.flight };
  };

  const check = (el: Element | null): HTMLAnchorElement | undefined =>
    el instanceof HTMLAnchorElement &&
    inPlace(el.getAttribute("target")) &&
    !el.closest(SKIP) &&
    host.owned(new URL(el.href), el.getAttribute(LAYER) ? undefined : el)
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
    if (eager()) void want(addr(link), true, link);
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
    const { clientX, clientY, pointerType } = event as PointerEvent;
    const moved = clientX !== x || clientY !== y;
    if (event.type === "pointerover") {
      x = clientX;
      y = clientY;
    }
    if (!urgent && target === aimed) return;
    const link = check(target);
    if (urgent) {
      if (event.type === "touchstart" || pointerType === "touch")
        touched = link && addr(link);
      if (link && full(event, link)) press(link);
      else if (link) intend(link);
      return;
    }
    clearTimeout(timer);
    aimed = moved ? link : undefined;
    if (aimed)
      timer = setTimeout(intend, 1000 * (cfg().prefetch_delay ?? 0.065), aimed);
  };

  const cancel = (): void => {
    release();
    const url = touched ?? "";
    const wish = wishes.get(url);
    touched = undefined;
    if (!wish || wish.stop) return;
    if (wish.controller) wish.controller.abort();
    else {
      wishes.delete(url);
      wish.settle();
    }
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
      const mode =
        el.closest(`[${MODE}]`)?.getAttribute(MODE) ??
        (el.getAttribute(LAYER) ? "" : cfg().prefetch);
      const link = mode === "viewport" && check(el);
      if (!link) continue;
      const stop = observer.observe(
        el,
        (visible) => {
          if (tried.has(addr(link))) stop();
          else if (!visible) forget(el);
          else if (eager()) void want(addr(link), false, el, stop);
        },
        "",
      );
    }
  };

  for (const type of ["pointerover", "focusin", "pointerdown", "touchstart"])
    doc.addEventListener(type, aim, { ...listening, passive: true });
  for (const type of ["pointerout", "focusout"])
    doc.addEventListener(type, leave, listening);
  doc.addEventListener("pointercancel", cancel, listening);
  for (const type of ["dragstart", "contextmenu", "router:start"])
    doc.addEventListener(type, () => release(), listening);
  doc.addEventListener(
    "pointerup",
    (event) => {
      const { clientX, clientY } = event;
      if (!pressed?.link.contains(doc.elementFromPoint(clientX, clientY))) release();
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
      if (event.persisted) clear();
    },
    listening,
  );
  watch(doc);

  // A layer takes the answer held for its link, or fetches as it would without one.
  host.partial._take = (href, zone) => {
    const url = spot(new URL(href, doc.baseURI).href, zone);
    const found = adopt(url, claim(url));
    return (
      found &&
      (async (target, init) => {
        const { signal } = init;
        const hit = await found;
        const flight = hit?.flight;
        if (signal?.aborted) {
          flight?.stop();
          discard(hit);
          signal.throwIfAborted();
        }
        if (hit?.response) flight?.stop();
        else signal?.addEventListener("abort", () => flight?.stop());
        return (
          hit?.response ?? (flight && (await flight.answer)) ?? fetch(target, init)
        );
      })
    );
  };

  return {
    take: (href) => {
      const url = bare(href);
      return adopt(url, claim(url));
    },
    prefetch: (href) => want(bare(href), true),
    drop: () => {
      release(false);
      for (const wish of wishes.values()) wish.controller?.abort();
      clear();
    },
  };
}
