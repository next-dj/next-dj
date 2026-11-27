// The asset loader and the version safeguard. A registry of URLs already on the
// page is the baseline an envelope manifest is a delta against, so the loaded-asset
// list never travels from client to server. A version mismatch triggers one full
// visit under a reload-once flag, so a stale CDN cannot loop the page.

import { defaultClock, defaultNavigate, defaultSession } from "./adapters";
import { isAsset } from "./apply";
import type { Asset, AssetLoad } from "./apply";
import { ATTR_STREAMED, sameOrigin, scriptNonce } from "./protocol";
import type { PartialError } from "./protocol";
import type { Clock, Navigate } from "./wire";

const RELOAD_FLAG = "next:partial:reloaded";
// Kept apart from RELOAD_FLAG, and held until a page boots on a matching release.
const STALE_FLAG = "next:partial:stale";
const CSS_TIMEOUT_MS = 3000;

type Waiters = ((ok: boolean) => void)[];

/**
 * Insert a stylesheet and signal load, timeout, or error through one callback.
 *
 * A seam so a test drives the outcome without a browser resolving the stylesheet.
 */
export type LinkLoader = (
  url: string,
  nonce: string | undefined,
  done: (ok: boolean) => void,
  clock: Clock,
  timeoutMs: number,
) => void;

/**
 * The minimal session store the reload-once guard needs.
 *
 * Injected so the guard survives environments where sessionStorage throws
 * (private mode, disabled storage) and the harness can drive the flag.
 */
export interface SessionStore {
  get(key: string): string | null;
  set(key: string, value: string): void;
  remove(key: string): void;
}

/** The injectable seams createAssets accepts, each defaulting to a real adapter. */
export interface AssetsDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  document?: Document;
  clock?: Clock;
  // Absent, the default inserts a real <link>.
  loadLink?: LinkLoader;
  // Absent, the default calls location.assign.
  navigate?: Navigate;
  // Absent, the default wraps sessionStorage.
  session?: SessionStore;
  // The bounded CSS wait, so a slow stylesheet does not block ops forever.
  cssTimeoutMs?: number;
}

/** The asset runtime a partial apply drives, backed by a per-page URL registry. */
export interface Assets {
  /** Seed the registry from the assets already present in the document. */
  seed(): void;
  /**
   * Insert missing link-verb assets, done runs once every new sheet loads or fails.
   *
   * With none missing done runs synchronously, or at the end of a running parse.
   */
  loadCss(manifest: readonly Asset[], done: () => void, zones?: string[]): void;
  /**
   * Run the missing script and module verbs after the ops, each source once per page.
   */
  loadJs(manifest: readonly Asset[]): void;
  /** The current asset version known to the client, sent on every request. */
  version(): string;
  /** Take the version the init payload names, the one the first request asserts. */
  pin(version: string): void;
  /**
   * Record whether the server speaks this runtime's release, reloading once if not.
   *
   * A page that still mismatches after the reload reports it and stays put.
   */
  release(current: boolean): void;
  /**
   * Compare the envelope version against the known one, true meaning do not apply.
   *
   * A mismatch starts a full visit under the reload-once flag, or fires partial:error.
   */
  versionMismatch(envelopeVersion: string, url: string, zones?: string[]): boolean;
  /** Clear the reload-once flag once a versioned envelope has applied. */
  acceptVersion(envelopeVersion: string): void;
  /** Harness seam, absent from the shipped bundles. */
  _reset(): void;
}

/** Build an Assets runtime over the given seams. */
export function createAssets(deps: AssetsDeps): Assets {
  const doc = deps.document ?? document;
  const clock = deps.clock ?? defaultClock();
  const loadLink = deps.loadLink ?? nativeLinkLoader(doc);
  const navigate = deps.navigate ?? defaultNavigate();
  const session = deps.session ?? defaultSession();
  const cssTimeout = deps.cssTimeoutMs ?? CSS_TIMEOUT_MS;
  // Every URL inserted or scanned, normalised by urlKey, the dedup key.
  const loaded = new Map<string, Waiters | null>();
  // Copied onto every dynamically inserted asset so CSP keeps allowing them.
  const nonce = scriptNonce(doc);
  let knownVersion = "";

  // The dedup key of a URL asset, one form for both sides of the delta. The DOM
  // reports absolute urls while a manifest carries whatever the server wrote,
  // so resolution goes against baseURI (not location) to make raw strings meet.
  function urlKey(raw: string): string {
    try {
      const url = new URL(raw, doc.baseURI);
      // The query stays in the identity (a versioned asset differs by ?v=
      // alone), the fragment leaves it since it never reaches the network.
      url.hash = "";
      return url.href;
    } catch {
      // The manifest is wire data, so an unparsable url keys as itself.
      return raw;
    }
  }

  // Detach the pending catch-up scan, undefined once the registry is complete.
  // Doubles as the "still watching the parse window" flag.
  let unwatch: (() => void) | undefined;
  // Asset phases waiting for the end of the parse, held so a discarded instance
  // can drop them instead of firing into a document it no longer seeds.
  const deferred = new Set<() => void>();
  // Whether this instance has taken its one baseline scan. A delta can arrive
  // before seed(), and an unseeded registry would re-insert the whole manifest.
  let seeded = false;
  // Set from a catch-up scan until the task that took it ends.
  let scanned = false;

  function seed(): void {
    seeded = true;
    scan();
    // The bootstrap runs above the co-located asset tags, so the first scan
    // sees only a prefix of the document and a catch-up scan finishes it.
    if (doc.readyState !== "loading" || unwatch !== undefined) return;
    const onParsed = (): void => catchUp();
    doc.addEventListener("DOMContentLoaded", onParsed);
    unwatch = () => {
      doc.removeEventListener("DOMContentLoaded", onParsed);
      unwatch = undefined;
    };
  }

  // Re-seed in front of a delta, so the seed prefix is completed with a full scan. A
  // streamed page is still parsing, so its watch stays until the parse ends.
  function catchUp(): void {
    // An unseeded registry takes its one baseline scan here instead.
    if (!seeded) {
      seed();
      return;
    }
    if (unwatch === undefined || scanned) return;
    scan();
    // The CSS and the JS phase of one apply share the scan the first one took.
    scanned = true;
    queueMicrotask(() => (scanned = false));
    if (doc.readyState !== "loading") unwatch();
  }

  // Hold an asset phase back until the document is fully parsed, since a delta
  // taken mid-document counts a not-yet-reached tag as missing and doubles it.
  // A `defer="load"` zone GET fires exactly in that window.
  function whenParsed(run: () => void): void {
    const parsed = (): void => {
      catchUp();
      run();
    };
    if (doc.readyState !== "loading" || doc.querySelector(`[${ATTR_STREAMED}]`)) {
      parsed();
      return;
    }
    const onParsed = (): void => {
      deferred.delete(onParsed);
      parsed();
    };
    deferred.add(onParsed);
    doc.addEventListener("DOMContentLoaded", onParsed, { once: true });
  }

  function scan(): void {
    for (const link of doc.querySelectorAll<HTMLLinkElement>(
      'link[rel="stylesheet"][href]',
    )) {
      claim(urlKey(link.href));
    }
    // A server-rendered module is a <script type="module" src>, so the src
    // attribute seeds it like any other script and it never mounts twice.
    for (const script of doc.querySelectorAll<HTMLScriptElement>("script[src]")) {
      claim(urlKey(script.src));
    }
    // Seed the inline bodies already materialised into the head, keyed by the
    // verb that would have inserted them, so a first zone GET does not re-run.
    for (const style of doc.querySelectorAll<HTMLStyleElement>("style")) {
      claim(inlineKey("link", textOf(style)));
    }
    for (const script of doc.querySelectorAll<HTMLScriptElement>("script:not([src])")) {
      const load = script.type === "module" ? "module" : "script";
      claim(inlineKey(load, textOf(script)));
    }
  }

  // Claim a dedup key for insertion, false when this page already ran it. Taken
  // before the insert, so a doubled manifest entry still inserts once.
  function claim(key: string): boolean {
    if (loaded.has(key)) return false;
    loaded.set(key, null);
    return true;
  }

  // The verb an entry asks for, undefined when it is malformed or its kind has none.
  function verbOf(asset: Asset): AssetLoad | undefined {
    if (!isAsset(asset)) return undefined;
    return asset.load;
  }

  function insertStyle(body: string): void {
    const el = doc.createElement("style");
    el.textContent = body;
    if (nonce !== undefined) el.nonce = nonce;
    doc.head.append(el);
  }

  // async is pinned false so a src element joins the in-order list.
  function makeScript(load: "script" | "module"): HTMLScriptElement {
    const el = doc.createElement("script");
    if (load === "module") el.type = "module";
    el.async = false;
    if (nonce !== undefined) el.nonce = nonce;
    return el;
  }

  function fail(message: string, zones?: string[], url?: string): void {
    deps.dispatch("partial:error", {
      kind: "asset",
      ...(url === undefined ? {} : { url }),
      error: new Error(message),
      ...(zones === undefined ? {} : { zones }),
    } satisfies PartialError);
  }

  // done runs inside the parse gate with the delta, so an envelope that arrives
  // mid-parse applies its ops once the document is fully parsed.
  function loadCss(
    manifest: readonly Asset[],
    done: () => void,
    zones?: string[],
  ): void {
    whenParsed(() => {
      // Inline styles insert synchronously and never join the url delta done waits on.
      const waits: Waiters[] = [];
      const starts: (() => void)[] = [];
      for (const asset of manifest) {
        if (verbOf(asset) !== "link") continue;
        if (asset.inline !== undefined) {
          if (claim(inlineKey("link", asset.inline))) insertStyle(asset.inline);
          continue;
        }
        if (isEmptyUrl(asset)) continue;
        const key = urlKey(asset.url);
        if (!loaded.has(key)) {
          const own: Waiters = [];
          loaded.set(key, own);
          starts.push(() => {
            loadLink(
              asset.url,
              nonce,
              (ok) => {
                if (loaded.get(key) === own) loaded.set(key, null);
                for (const settle of own.splice(0)) settle(ok);
              },
              clock,
              cssTimeout,
            );
          });
        }
        const waiters = loaded.get(key);
        if (waiters) waits.push(waiters);
      }
      if (waits.length === 0) {
        done();
        return;
      }
      // A 404 or timeout on one sheet must not strand the envelope, so each
      // settles on its own and the ops run once the last one resolves.
      let pending = waits.length;
      let errored = false;
      const settle = (ok: boolean): void => {
        if (!ok) errored = true;
        pending -= 1;
        if (pending > 0) return;
        // The ops already ran, partial:error only reports the styling gap.
        if (errored) fail("a stylesheet failed to load", zones);
        done();
      };
      for (const waiters of waits) waiters.push(settle);
      for (const start of starts) start();
    });
  }

  // One pass in manifest order, so module and classic scripts keep the server's order.
  function loadJs(manifest: readonly Asset[]): void {
    whenParsed(() => {
      for (const asset of manifest) {
        const load = verbOf(asset);
        if (load !== "script" && load !== "module") continue;
        if (asset.inline !== undefined) {
          if (!claim(inlineKey(load, asset.inline))) continue;
          const el = makeScript(load);
          el.textContent = asset.inline;
          doc.head.append(el);
          continue;
        }
        if (isEmptyUrl(asset)) continue;
        if (!claim(urlKey(asset.url))) continue;
        const el = makeScript(load);
        el.src = asset.url;
        doc.head.append(el);
      }
    });
  }

  function versionMismatch(
    envelopeVersion: string,
    url: string,
    zones?: string[],
  ): boolean {
    // A page whose payload names no version asserts none, as on the server.
    if (
      envelopeVersion === "" ||
      knownVersion === "" ||
      envelopeVersion === knownVersion
    )
      return false;
    const target = sameOrigin(url, doc);
    if (target === undefined) {
      fail("cross-origin reload refused", zones, url);
      return true;
    }
    if (readFlag()) {
      // A reload already happened and the version still mismatches, so a stale
      // CDN is serving the old bundle. Degrade to plain navigation, no loop.
      clearFlag();
      fail("asset version mismatch after reload", zones, url);
      return true;
    }
    setFlag();
    navigate(target);
    return true;
  }

  // The version is the payload's, never taught by a reply, so only the flag clears.
  function acceptVersion(envelopeVersion: string): void {
    if (envelopeVersion !== "") clearFlag();
  }

  function release(current: boolean): void {
    if (current) session.remove(STALE_FLAG);
    else if (session.get(STALE_FLAG) === "1")
      fail("the runtime is from another release than the server");
    else {
      session.set(STALE_FLAG, "1");
      navigate(doc.location.href);
    }
  }

  function readFlag(): boolean {
    return session.get(RELOAD_FLAG) === "1";
  }

  function setFlag(): void {
    session.set(RELOAD_FLAG, "1");
  }

  function clearFlag(): void {
    session.remove(RELOAD_FLAG);
  }

  const exposed = {
    seed,
    loadCss,
    loadJs,
    version: () => knownVersion,
    pin: (version) => void (knownVersion = version),
    release,
    versionMismatch,
    acceptVersion,
  } satisfies Omit<Assets, "_reset"> as Assets;

  TEST: {
    exposed._reset = () => {
      // A discarded instance must stop watching a document it no longer seeds.
      // seeded stays set, since a rescan here would read that same document.
      unwatch?.();
      for (const onParsed of deferred) {
        doc.removeEventListener("DOMContentLoaded", onParsed);
      }
      deferred.clear();
      loaded.clear();
      knownVersion = "";
    };
  }

  return exposed;
}

// An empty url resolves to the document itself, so such an entry would fetch
// the page as a stylesheet and claim the key of the real asset addressing it.
function isEmptyUrl(asset: Asset): boolean {
  return asset.url === "";
}

// The dedup key for an inline body, scoped by the insertion verb so head bodies
// and manifest bodies match byte for byte across any kind carrying them.
function inlineKey(load: AssetLoad, body: string): string {
  return `inline:${load}:${body}`;
}

function textOf(element: Element): string {
  return element.textContent;
}

/** The default loader, inserting a <link> and racing its load against the timeout. */
export function nativeLinkLoader(doc: Document): LinkLoader {
  return (url, nonce, done, clock, timeoutMs) => {
    const link = doc.createElement("link");
    link.rel = "stylesheet";
    link.href = url;
    if (nonce !== undefined) link.nonce = nonce;
    // A sheet that loads after the timeout already reported failure, and a browser
    // is free to fire load and error both, so the first outcome is the only one.
    let settled = false;
    const finish = (ok: boolean): void => {
      if (settled) return;
      settled = true;
      clock.clearTimeout(timer);
      done(ok);
    };
    link.onload = () => finish(true);
    link.onerror = () => finish(false);
    const timer = clock.setTimeout(() => finish(false), timeoutMs);
    doc.head.append(link);
  };
}
