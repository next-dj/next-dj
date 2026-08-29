// The asset loader and the version safeguard. A registry of URLs already on the
// page is the baseline an envelope manifest is a delta against, so the loaded-asset
// list never travels from client to server. A version mismatch triggers one full
// visit under a reload-once flag, so a stale CDN cannot loop the page.

import {
  defaultClock,
  defaultLinkLoader,
  defaultNavigate,
  defaultSession,
} from "./adapters";
import { assetLoad, isAsset } from "./apply";
import type { Asset, AssetLoad } from "./apply";
import type { PartialError } from "./protocol";
import type { Clock, Navigate } from "./wire";

const RELOAD_FLAG = "next:partial:reloaded";
const CSS_TIMEOUT_MS = 3000;

/**
 * Insert a stylesheet and signal load, timeout, or error through one callback.
 *
 * A seam because jsdom never fires link.onload, so the timeout and error
 * branches are otherwise untestable.
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
   * Insert the missing link-verb assets of a manifest and call done once every
   * new sheet has loaded, errored, or timed out.
   *
   * With none missing done runs synchronously, unless the document is still
   * parsing, where the whole phase waits for the end of the parse.
   */
  loadCss(manifest: readonly Asset[], done: () => void): void;
  /**
   * Run the missing script and module verbs of a manifest after the ops, each
   * URL and each inline body once per page.
   */
  loadJs(manifest: readonly Asset[]): void;
  /** The current asset version known to the client, sent on every request. */
  version(): string;
  /**
   * Compare the envelope version against the known one, true meaning do not
   * apply.
   *
   * A mismatch starts a single full visit under the reload-once flag, or fires
   * partial:error when a reload already happened.
   */
  versionMismatch(envelopeVersion: string, url: string): boolean;
  /** Record the version of an applied envelope and clear the reload-once flag. */
  acceptVersion(envelopeVersion: string): void;
  _reset(): void;
}

/** Build an Assets runtime over the given seams. */
export function createAssets(deps: AssetsDeps): Assets {
  const doc = deps.document ?? document;
  const clock = deps.clock ?? defaultClock();
  const loadLink = deps.loadLink ?? defaultLinkLoader();
  const navigate = deps.navigate ?? defaultNavigate();
  const session = deps.session ?? defaultSession();
  const cssTimeout = deps.cssTimeoutMs ?? CSS_TIMEOUT_MS;
  // Every URL inserted or scanned, normalised by urlKey, the dedup key.
  const loaded = new Set<string>();
  // Copied onto every dynamically inserted asset so CSP keeps allowing them.
  const nonce = rememberNonce(doc);
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

  // Re-seed in front of a delta whose loader has cleared the parse window, so
  // the seed prefix is completed with a full scan and the watch then drops.
  function catchUp(): void {
    // An unseeded registry takes its one baseline scan here instead.
    if (!seeded) {
      seed();
      return;
    }
    const settled = unwatch;
    if (settled === undefined) return;
    scan();
    settled();
  }

  // Hold an asset phase back until the document is fully parsed, since a delta
  // taken mid-document counts a not-yet-reached tag as missing and doubles it.
  // A `lazy="load"` zone GET fires exactly in that window.
  function whenParsed(run: () => void): void {
    if (doc.readyState !== "loading") {
      run();
      return;
    }
    const onParsed = (): void => {
      deferred.delete(onParsed);
      run();
    };
    deferred.add(onParsed);
    doc.addEventListener("DOMContentLoaded", onParsed, { once: true });
  }

  function scan(): void {
    for (const link of Array.from(
      doc.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"][href]'),
    )) {
      loaded.add(urlKey(link.href));
    }
    // A server-rendered module is a <script type="module" src>, so the src
    // attribute seeds it like any other script and it never mounts twice.
    for (const script of Array.from(
      doc.querySelectorAll<HTMLScriptElement>("script[src]"),
    )) {
      loaded.add(urlKey(script.src));
    }
    // Seed the inline bodies already materialised into the head, keyed by the
    // verb that would have inserted them, so a first zone GET does not re-run.
    for (const style of Array.from(doc.querySelectorAll<HTMLStyleElement>("style"))) {
      loaded.add(inlineKey("link", textOf(style)));
    }
    for (const script of Array.from(
      doc.querySelectorAll<HTMLScriptElement>("script:not([src])"),
    )) {
      const load = script.type === "module" ? "module" : "script";
      loaded.add(inlineKey(load, textOf(script)));
    }
  }

  // Claim a dedup key for insertion, false when this page already ran it. Taken
  // before the insert, so a doubled manifest entry still inserts once.
  function claim(key: string): boolean {
    if (loaded.has(key)) return false;
    loaded.add(key);
    return true;
  }

  // The verb a manifest entry asks for, undefined when the entry is malformed
  // or its kind carries no client verb.
  function verbOf(asset: Asset): AssetLoad | undefined {
    if (!isAsset(asset)) return undefined;
    return assetLoad(asset.kind, asset.load, asset.inline);
  }

  function insertStyle(body: string): void {
    const el = doc.createElement("style");
    el.textContent = body;
    if (nonce !== undefined) el.nonce = nonce;
    doc.head.append(el);
  }

  // async is pinned to false so a src element joins the in-order list instead
  // of running as soon as it is ready.
  function makeScript(load: "script" | "module"): HTMLScriptElement {
    const el = doc.createElement("script");
    if (load === "module") el.type = "module";
    el.async = false;
    if (nonce !== undefined) el.nonce = nonce;
    return el;
  }

  // done rides inside the parse gate with the delta, so an envelope that lands
  // mid-parse applies its ops once the document is whole.
  function loadCss(manifest: readonly Asset[], done: () => void): void {
    whenParsed(() => {
      catchUp();
      // Inline styles insert synchronously and never join the url delta done waits on.
      const urls: string[] = [];
      for (const asset of manifest) {
        if (verbOf(asset) !== "link") continue;
        if (asset.inline !== undefined) {
          if (claim(inlineKey("link", asset.inline))) insertStyle(asset.inline);
          continue;
        }
        if (isEmptyUrl(asset)) continue;
        if (claim(urlKey(asset.url))) urls.push(asset.url);
      }
      if (urls.length === 0) {
        done();
        return;
      }
      // A 404 or timeout on one sheet must not strand the envelope, so each
      // settles on its own and the ops run once the last one resolves.
      let pending = urls.length;
      let errored = false;
      const settle = (ok: boolean): void => {
        if (!ok) errored = true;
        pending -= 1;
        if (pending > 0) return;
        // The ops still run, so a 404 cannot leave a response unapplied.
        // partial:error reports the styling gap.
        if (errored) {
          deps.dispatch("partial:error", {
            kind: "asset",
            error: new Error("a stylesheet failed to load"),
          } satisfies PartialError);
        }
        done();
      };
      for (const url of urls) {
        loadLink(url, nonce, settle, clock, cssTimeout);
      }
    });
  }

  // One pass in manifest order so a module and a classic script insert in the
  // order the server listed them.
  function loadJs(manifest: readonly Asset[]): void {
    whenParsed(() => {
      catchUp();
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

  function versionMismatch(envelopeVersion: string, url: string): boolean {
    if (envelopeVersion === "" || envelopeVersion === knownVersion) return false;
    if (knownVersion === "") {
      // The first envelope teaches the runtime the live version, nothing to be
      // out of sync with yet.
      knownVersion = envelopeVersion;
      return false;
    }
    if (readFlag()) {
      // A reload already happened and the version still mismatches, so a stale
      // CDN is serving the old bundle. Degrade to plain navigation, no loop.
      clearFlag();
      deps.dispatch("partial:error", {
        kind: "asset",
        url,
        error: new Error("asset version mismatch after reload"),
      } satisfies PartialError);
      return true;
    }
    setFlag();
    navigate(url);
    return true;
  }

  function acceptVersion(envelopeVersion: string): void {
    if (envelopeVersion === "") return;
    knownVersion = envelopeVersion;
    clearFlag();
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

  return {
    seed,
    loadCss,
    loadJs,
    version: () => knownVersion,
    versionMismatch,
    acceptVersion,
    _reset() {
      // A discarded instance must stop watching a document it no longer seeds.
      // seeded stays set, since a rescan here would read that same document.
      unwatch?.();
      for (const onParsed of deferred) {
        doc.removeEventListener("DOMContentLoaded", onParsed);
      }
      deferred.clear();
      loaded.clear();
      knownVersion = "";
    },
  };
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

// document.currentScript is null by the time a patch lands, so the bootstrap
// nonce is captured at module evaluation and reused for every inserted asset.
function rememberNonce(doc: Document): string | undefined {
  const current = doc.currentScript;
  const value = current instanceof HTMLElement ? current.nonce : "";
  return value === "" ? undefined : value;
}
