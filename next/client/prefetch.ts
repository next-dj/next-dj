// Link prefetch for the router: intent and viewport wishes under a request limit, the
// full request a press sends ahead of its click, and the cache a visit takes from.

import { defaultObserver } from "./adapters";
import { inPlace, isPlainActivation } from "./events";
import { ORDER_VIEWPORT } from "./kernel";
import {
  ACCEPT,
  ATTR_ACTION,
  ATTR_CONFIRM,
  ATTR_LAYER,
  ATTR_PREFETCH,
  ATTR_PRESS,
  CONTENT_TYPE,
  HEADER_ORIGIN,
  HEADER_PREFETCH,
  HEADER_VERSION,
  HEADER_ZONE,
  PREFETCH_FULL,
  PREFETCH_SHELL,
  ROUTER_CLEAR,
  bare,
  isRecord,
  matching,
  mime,
  says,
} from "./protocol";
import type { Flight, HistoryHost, Prefetched, Prefetcher } from "./router";
import type { IntersectionAdapter } from "./triggers";

// A page the visitor may open next, queued until a request slot frees up. An urgent
// wish (a press, a focus) jumps the queue, a viewport wish waits for a spare slot.
interface Wish {
  urgent: boolean;
  settle: () => void;
  done: Promise<void>;
  el?: Element | undefined;
  stop?: (() => void) | undefined;
  controller?: AbortController;
}

// A prefetched answer kept for the visit, the shell living longer than a full page.
interface Cached {
  expires: number;
  raw: Record<string, unknown>;
  response: Response;
  stored: number;
}

// The full request a pointer press sent, held for the click that usually follows.
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
  `[download],[rel~=external],[${ATTR_PREFETCH}=off],[${ATTR_CONFIRM}],` +
  `[${ATTR_ACTION}]`;
const MODE = ATTR_PREFETCH;
const PRESS = ATTR_PRESS;
const VIEWPORT = `[${MODE}=viewport] a[href],a[href][${MODE}=viewport]`;
// The seconds a 429 or a 5xx pauses prefetching for, doubled on each one in a row.
const RETRY = 30;
// The refused and the fetched URLs a page remembers, the oldest dropped past this many.
const REMEMBERED = 200;
// The prefetches in flight at once, one kept free for an urgent wish.
const LIMIT = 3;
// How long a press waits for its click before its answer goes to the cache.
const HOLD = 1000;

// Remember a URL at the young end of a bounded set.
function remember(urls: Set<string>, url: string): void {
  urls.delete(url);
  urls.add(url);
  for (const old of urls) if (urls.size > REMEMBERED) urls.delete(old);
}

export function createPrefetch(
  host: HistoryHost,
  listening: AddEventListenerOptions = {},
  observer: IntersectionAdapter = defaultObserver(),
): Prefetcher {
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

  // An answer only fits the layout chain it was asked under, so the chain is in the key.
  const cacheKey = (id: string): string => {
    const chain = host.chain().map((layout) => layout[0] + layout[1]);
    return `${id} ${chain.join()}`;
  };

  // A layer link answers with its zone for the page it opens over, so both join its URL.
  const requestId = (href: string, zone?: string | null): string =>
    zone ? `${bare(href)} ${zone} ${location.pathname + location.search}` : bare(href);

  const linkId = (link: HTMLAnchorElement): string =>
    requestId(link.href, link.getAttribute(ATTR_LAYER));

  const request = (
    id: string,
    signal: AbortSignal,
    depth: string,
  ): Promise<Response> => {
    const [url = "", zone, origin = ""] = id.split(" ");
    return zone
      ? fetch(url, {
          headers: host.headers({
            Accept: ACCEPT,
            [HEADER_ZONE]: zone,
            [HEADER_ORIGIN]: origin,
            [HEADER_PREFETCH]: PREFETCH_FULL,
          }),
          signal,
          mode: "same-origin",
        })
      : host.ask(url, signal, { [HEADER_PREFETCH]: depth });
  };

  // A layer body stays unread for the layer to apply, so its record holds the facts
  // the cache reads. A navigation body is the envelope, kept when it lists layouts.
  const readAnswer = async (id: string, response: Response): Promise<unknown> => {
    const type = mime(response);
    if (!response.ok || response.redirected || type !== (cfg().type ?? CONTENT_TYPE))
      return undefined;
    if (id.includes(" "))
      return { complete: true, version: response.headers.get(HEADER_VERSION) };
    const raw = host.parse(type, await response.text());
    return isRecord(raw) && Array.isArray(raw.layouts) ? raw : undefined;
  };

  const preloadStyles = (assets: unknown, urgent: boolean): void => {
    const known = new Set(
      [...doc.querySelectorAll<HTMLLinkElement>("link[href]")].map((el) => el.href),
    );
    for (const asset of Array.isArray(assets) ? (assets as unknown[]) : []) {
      if (
        !isRecord(asset) ||
        typeof asset.url !== "string" ||
        !asset.url ||
        asset.load !== "link"
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

  const cancelBody = (entry?: Pick<Prefetched, "response">): void =>
    void entry?.response?.body?.cancel().catch(() => undefined);

  const evict = (key: string): void => {
    cancelBody(cache.get(key));
    cache.delete(key);
  };

  const clear = (): void => {
    for (const key of cache.keys()) evict(key);
  };

  const hasRoom = (): boolean => {
    const now = Date.now();
    const stale = now - 1000 * (cfg().prefetch_ttl ?? 30);
    const pointed = aimed ? cacheKey(linkId(aimed)) : "";
    const limit = (cfg().prefetch_entries ?? 16) - inFlight();
    for (const [key, entry] of cache)
      if (
        entry.expires <= now ||
        (cache.size >= limit && entry.stored <= stale && key !== pointed)
      )
        evict(key);
    return cache.size < limit;
  };

  const inFlight = (): number => {
    let flying = 0;
    for (const wish of wishes.values()) if (wish.controller) flying += 1;
    return flying;
  };

  // A 204 refuses this page for good, a 429 or a 5xx backs every prefetch off for its
  // Retry-After or the doubling pause, and drops the wishes still queued.
  const obeyServer = (url: string, response: Response): void => {
    const { status, headers } = response;
    const after = Number(headers.get("Retry-After"));
    if (status === 204) remember(refused, url);
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

  const store = (
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
    preloadStyles(raw.assets, urgent);
    return true;
  };

  // An answer from another asset version would only load hard, so it counts as stale.
  const freshEntry = (found: Cached | undefined): Cached | undefined => {
    const known = host.headers({}).get(HEADER_VERSION);
    return found &&
      found.expires > Date.now() &&
      !(known && known !== found.raw.version)
      ? found
      : undefined;
  };

  // A press that saw no click hands its answer to the cache, or stops it unread.
  const releasePress = (kept = true): void => {
    const held = pressed;
    if (!held) return;
    pressed = undefined;
    clearTimeout(held.timer);
    const { response } = held;
    if (!kept || !response) held.flight.stop();
    else
      void readAnswer(held.url, response)
        .then((raw) => store(held.key, raw, response, true))
        .catch(() => false)
        .then((stored) => stored || held.flight.stop());
  };

  // A press sends the full request at once, so the click that follows finds it under way.
  const startPress = (link: HTMLAnchorElement): void => {
    const url = linkId(link);
    if (pressed?.url === url) return;
    releasePress();
    const key = cacheKey(url);
    const queued = wishes.get(url);
    if (
      freshEntry(cache.get(key))?.raw.complete === true ||
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
      timer: setTimeout(releasePress, HOLD),
      flight: {
        answer: request(url, controller.signal, PREFETCH_FULL).then(
          (response) => {
            obeyServer(url, response);
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

  const send = (url: string, wish: Wish): void => {
    const controller = (wish.controller = new AbortController());
    const { signal } = controller;
    const key = cacheKey(url);
    void request(url, signal, PREFETCH_SHELL)
      .then(async (response) => {
        obeyServer(url, response);
        if (says(response, ROUTER_CLEAR)) host.flush();
        const raw = await readAnswer(url, response);
        if (signal.aborted) return;
        remember(tried, url);
        wish.stop?.();
        if (!store(key, raw, response, wish.urgent)) cancelBody({ response });
      })
      .catch(() => undefined)
      .then(() => {
        wishes.delete(url);
        wish.settle();
        sendWishes();
      });
  };

  // Send the queued wishes while slots are free, urgent first, the last slot kept for one.
  const sendWishes = (): void => {
    for (;;) {
      let pick: [string, Wish] | undefined;
      for (const entry of wishes)
        if (!entry[1].controller && (entry[1].urgent || !pick)) pick = entry;
      const flying = inFlight();
      if (!pick || flying >= LIMIT) return;
      const [url, wish] = pick;
      if (wish.el?.isConnected === false) {
        wishes.delete(url);
        wish.stop?.();
        wish.settle();
        continue;
      }
      if (!wish.urgent && (flying >= LIMIT - 1 || !hasRoom())) return;
      send(url, wish);
    }
  };

  const queueWish = (
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
        sendWishes();
      }
      return held.done;
    }
    if (
      pressed?.url === url ||
      host.going() === url ||
      !host.owned(new URL(url.split(" ")[0] ?? "")) ||
      freshEntry(cache.get(cacheKey(url))) ||
      refused.has(url) ||
      pause > Date.now()
    )
      return Promise.resolve();
    let settle!: () => void;
    const done = new Promise<void>((resolve) => (settle = resolve));
    wishes.set(url, { urgent, settle, done, el, stop });
    sendWishes();
    return done;
  };

  const dropWish = (el: Element | undefined, pointer = false): void => {
    const url = el instanceof HTMLAnchorElement ? linkId(el) : "";
    const wish = wishes.get(url);
    if (!wish || wish.controller || wish.el !== el) return;
    if (pointer && wish.stop) wish.urgent = false;
    else {
      wishes.delete(url);
      wish.settle();
    }
  };

  const claimPress = (url: string): Press | undefined => {
    const held = pressed;
    if (held?.url !== url) return undefined;
    pressed = undefined;
    clearTimeout(held.timer);
    return held;
  };

  // A visit takes the cached answer or the press in flight, never both for one URL, and
  // waits for a prefetch already under way rather than asking again.
  const takeAnswer = (
    url: string,
    held: Press | undefined,
  ): Prefetched | Promise<Prefetched | undefined> | undefined => {
    const wish = wishes.get(url);
    if (touched === url) touched = undefined;
    if (wish?.controller) return wish.done.then(() => takeAnswer(url, held));
    if (wish) {
      wishes.delete(url);
      wish.settle();
    }
    const key = cacheKey(url);
    const kept = freshEntry(cache.get(key));
    if (kept) cache.delete(key);
    else evict(key);
    if (!kept && !held) return undefined;
    return { raw: kept?.raw, response: kept?.response, flight: held?.flight };
  };

  const ownedLink = (el: Element | null): HTMLAnchorElement | undefined =>
    el instanceof HTMLAnchorElement &&
    inPlace(el.getAttribute("target")) &&
    !el.closest(SKIP) &&
    host.owned(new URL(el.href), el.getAttribute(ATTR_LAYER) ? undefined : el)
      ? el
      : undefined;

  const mayPrefetch = (): boolean => {
    const net = (navigator as Net).connection;
    return (
      cfg().prefetch !== "off" &&
      !doc.hidden &&
      !net?.saveData &&
      !(net?.effectiveType ?? "").includes("2g")
    );
  };

  const prefetchIntent = (link: HTMLAnchorElement): void => {
    if (mayPrefetch()) void queueWish(linkId(link), true, link);
  };

  const sendsFull = (event: Event, link: HTMLAnchorElement): boolean => {
    const mode = link.closest(`[${PRESS}]`)?.getAttribute(PRESS) ?? cfg().press;
    return (
      event.type === "pointerdown" &&
      (mode === "all" ||
        (mode !== "off" && (event as PointerEvent).pointerType === "mouse")) &&
      isPlainActivation(event, link) &&
      !host.plain(link.pathname + link.search) &&
      mayPrefetch()
    );
  };

  // A hover or a focus waits out the delay before it wishes, a press or a touch at once.
  // The pointer position tells a real hover from a link scrolled under a resting cursor.
  const onPointer = (event: Event): void => {
    const target =
      event.target instanceof Element ? event.target.closest("a[href]") : null;
    // A press or a touch wishes at once, a hover or a focus after the delay.
    const urgent = event.type === "pointerdown" || event.type === "touchstart";
    const { clientX, clientY, pointerType } = event as PointerEvent;
    const moved = clientX !== x || clientY !== y;
    if (event.type === "pointerover") {
      x = clientX;
      y = clientY;
    }
    if (!urgent && target === aimed) return;
    const link = ownedLink(target);
    if (urgent) {
      if (event.type === "touchstart" || pointerType === "touch")
        touched = link && linkId(link);
      if (link && sendsFull(event, link)) startPress(link);
      else if (link) prefetchIntent(link);
      return;
    }
    clearTimeout(timer);
    aimed = moved ? link : undefined;
    if (aimed)
      timer = setTimeout(prefetchIntent, 1000 * (cfg().prefetch_delay ?? 0.065), aimed);
  };

  // A cancelled touch stops what it started, unless the viewport also wants that page.
  const onCancel = (): void => {
    releasePress();
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

  const onLeave = (event: Event): void => {
    if (aimed?.contains((event as MouseEvent).relatedTarget as Node | null)) return;
    clearTimeout(timer);
    dropWish(aimed, true);
    aimed = undefined;
  };

  const watchViewport = (root: ParentNode): void => {
    const all = cfg().prefetch === "viewport";
    if (cfg().prefetch === "off") return;
    for (const el of matching(root, all ? "a[href]" : VIEWPORT)) {
      if (seen.has(el)) continue;
      seen.add(el);
      const mode =
        el.closest(`[${MODE}]`)?.getAttribute(MODE) ??
        (el.getAttribute(ATTR_LAYER) ? "" : cfg().prefetch);
      const link = mode === "viewport" && ownedLink(el);
      if (!link) continue;
      const stop = observer.observe(
        el,
        (visible) => {
          if (tried.has(linkId(link))) stop();
          else if (!visible) dropWish(el);
          else if (mayPrefetch()) void queueWish(linkId(link), false, el, stop);
        },
        "",
      );
    }
  };

  for (const type of ["pointerover", "focusin", "pointerdown", "touchstart"])
    doc.addEventListener(type, onPointer, { ...listening, passive: true });
  for (const type of ["pointerout", "focusout"])
    doc.addEventListener(type, onLeave, listening);
  doc.addEventListener("pointercancel", onCancel, listening);
  for (const type of ["dragstart", "contextmenu", "router:start"])
    doc.addEventListener(type, () => releasePress(), listening);
  doc.addEventListener(
    "pointerup",
    (event) => {
      const { clientX, clientY } = event;
      if (!pressed?.link.contains(doc.elementFromPoint(clientX, clientY)))
        releasePress();
    },
    listening,
  );
  host.hook("applied", ORDER_VIEWPORT, ({ nodes }) => nodes?.forEach(watchViewport));
  addEventListener(
    "pageshow",
    (event) => {
      if (event.persisted) clear();
    },
    listening,
  );
  watchViewport(doc);

  // A layer takes the answer held for its link, or fetches as it would without one.
  host.serve("take", (href, zone) => {
    const url = requestId(new URL(href, doc.baseURI).href, zone);
    const found = takeAnswer(url, claimPress(url));
    return (
      found &&
      (async (target, init) => {
        const { signal } = init;
        const hit = await found;
        const flight = hit?.flight;
        if (signal?.aborted) {
          flight?.stop();
          cancelBody(hit);
          signal.throwIfAborted();
        }
        if (hit?.response) flight?.stop();
        else signal?.addEventListener("abort", () => flight?.stop());
        return (
          hit?.response ?? (flight && (await flight.answer)) ?? fetch(target, init)
        );
      })
    );
  });

  return {
    take: (href) => {
      const url = bare(href);
      return takeAnswer(url, claimPress(url));
    },
    prefetch: (href) => queueWish(bare(href), true),
    drop: () => {
      releasePress(false);
      for (const wish of wishes.values()) wish.controller?.abort();
      clear();
    },
  };
}
