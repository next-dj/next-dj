// The asset loader and the version safeguard. A registry of URLs already on the
// page is seeded by scanning the DOM on `ready` and again once parsing ends,
// since the bootstrap runs above the co-located tags. The envelope ships a
// full manifest of its rendered targets, the client computes the delta against
// that registry, so the loaded-asset list never travels from client to server.
//
// An asset is inserted by its verb, not by its kind. The server derives the verb
// from the renderer registered for the kind, so a project-named kind still
// reaches the browser as a link, a script, or a module.
//
// Two views, two rules: the link verb is inserted and awaited (bounded) before
// the ops run so there is no FOUC, the script verbs run after the ops so the
// target DOM is already in place. Each URL executes once per page life. A version
// mismatch triggers a single full visit under a reload-once flag so a stale CDN
// cannot loop the page. The link loader, the navigation, and the session store
// are injectable seams: jsdom loads no resources, fires no link.onload, and does
// not implement location.assign.

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

// Insert a stylesheet and signal load, timeout, or error through one callback.
// The real implementation lives behind a seam because jsdom never fires
// link.onload, so the timeout and error branches are otherwise untestable.
export type LinkLoader = (
  url: string,
  nonce: string | undefined,
  done: (ok: boolean) => void,
  clock: Clock,
  timeoutMs: number,
) => void;

// The minimal session store the reload-once guard needs. Injected so the
// harness drives the flag without a real Storage, and so the guard survives
// environments where sessionStorage throws (private mode, disabled storage).
export interface SessionStore {
  get(key: string): string | null;
  set(key: string, value: string): void;
  remove(key: string): void;
}

export interface AssetsDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  document?: Document;
  clock?: Clock;
  // The CSS loader seam. Absent, the default inserts a real <link>.
  loadLink?: LinkLoader;
  // The full-visit navigation for a version mismatch. Absent, the default
  // calls location.assign.
  navigate?: Navigate;
  // The reload-once store. Absent, the default wraps sessionStorage.
  session?: SessionStore;
  // The bounded CSS wait. A test passes a small value, production keeps the
  // default so a slow stylesheet does not block ops forever.
  cssTimeoutMs?: number;
}

export interface Assets {
  // Seed the registry from the assets already present in the document, the
  // baseline against which envelope manifests are a delta.
  seed(): void;
  // Insert the missing link-verb assets of a manifest and call done once every
  // new sheet has loaded, errored, or timed out. With none missing the callback
  // runs synchronously so the ops apply in the same tick.
  loadCss(manifest: readonly Asset[], done: () => void): void;
  // Run the missing script and module verbs of a manifest after the ops, each
  // URL and each inline body once per page.
  loadJs(manifest: readonly Asset[]): void;
  // The current asset version known to the client, sent on every request.
  version(): string;
  // Compare the envelope version against the known one. A mismatch returns true
  // and starts a single full visit under the reload-once flag, or fires
  // partial:error when a reload already happened. true means "do not apply".
  versionMismatch(envelopeVersion: string, url: string): boolean;
  // Record the version of an applied envelope and clear the reload-once flag,
  // since a matching version means the deploy settled.
  acceptVersion(envelopeVersion: string): void;
  _reset(): void;
}

export function createAssets(deps: AssetsDeps): Assets {
  const doc = deps.document ?? document;
  const clock = deps.clock ?? defaultClock();
  const loadLink = deps.loadLink ?? defaultLinkLoader();
  const navigate = deps.navigate ?? defaultNavigate();
  const session = deps.session ?? defaultSession();
  const cssTimeout = deps.cssTimeoutMs ?? CSS_TIMEOUT_MS;
  // Every URL inserted or scanned, normalised by urlKey, the dedup key for
  // "execute once per page".
  const loaded = new Set<string>();
  // The nonce remembered from the script that bootstrapped the runtime, copied
  // onto every dynamically inserted asset so CSP keeps allowing them.
  const nonce = rememberNonce(doc);
  let knownVersion = "";

  // The dedup key of a URL asset, one form for both sides of the delta. The DOM
  // reports absolute urls on link.href and script.src while a manifest carries
  // whatever the server wrote, so the raw strings would never meet. Resolution
  // goes against baseURI rather than location so a <base href> counts.
  function urlKey(raw: string): string {
    try {
      const url = new URL(raw, doc.baseURI);
      // The query stays in the identity, a versioned asset differs by ?v= alone.
      // The fragment leaves it, since it never reaches the network and two
      // entries parted only by a # are the same bytes.
      url.hash = "";
      return url.href;
    } catch {
      // The manifest is wire data and can carry anything. An unparsable url
      // keys as itself rather than throwing out of the apply.
      return raw;
    }
  }

  // Detach the pending catch-up scan, undefined once the registry is complete.
  // Doubles as the "still watching the parse window" flag.
  let unwatch: (() => void) | undefined;
  // Whether this instance has taken its one baseline scan. _configure builds a
  // registry it never seeds, so a delta can arrive first and an unseeded
  // registry would read as an empty page and re-insert the whole manifest.
  let seeded = false;

  function seed(): void {
    seeded = true;
    scan();
    // The bootstrap is an inline script the server puts above the co-located
    // asset tags, so the first scan sees only a prefix of the document.
    if (doc.readyState !== "loading" || unwatch !== undefined) return;
    const onParsed = (): void => catchUp();
    doc.addEventListener("DOMContentLoaded", onParsed);
    unwatch = () => {
      doc.removeEventListener("DOMContentLoaded", onParsed);
      unwatch = undefined;
    };
  }

  // Re-seed in front of a delta taken while the document is still arriving. The
  // gate is the pending watch rather than the readyState, since a deferred
  // script runs after the flip to "interactive" but before the event.
  function catchUp(): void {
    // An unseeded registry takes its one baseline scan here, and seed() decides
    // from there whether the parse window is open at all.
    if (!seeded) {
      seed();
      return;
    }
    const settled = unwatch;
    if (settled === undefined) return;
    scan();
    if (doc.readyState !== "loading") settled();
  }

  function scan(): void {
    for (const link of Array.from(
      doc.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"][href]'),
    )) {
      loaded.add(urlKey(link.href));
    }
    // A server-rendered module is a <script type="module" src>, so matching on
    // the src attribute alone seeds it like any other script. A module inserted
    // a second time would mount its island twice.
    for (const script of Array.from(
      doc.querySelectorAll<HTMLScriptElement>("script[src]"),
    )) {
      loaded.add(urlKey(script.src));
    }
    // Seed the inline bodies the server already materialised into the head, so a
    // first zone GET does not re-insert the <style> or re-execute the <script>.
    // The key is the verb that would have inserted the element, the same form
    // the manifest delta computes.
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

  // Claim a dedup key for insertion, false when this page already ran it. The
  // key is taken before the insert, so a manifest naming one asset twice or a
  // re-entrant apply still inserts it once.
  function claim(key: string): boolean {
    if (loaded.has(key)) return false;
    loaded.add(key);
    return true;
  }

  // The verb a manifest entry asks for, or undefined when the entry is malformed
  // or names a kind whose server renderer implies no client verb.
  function verbOf(asset: Asset): AssetLoad | undefined {
    if (!isAsset(asset)) return undefined;
    return assetLoad(asset.kind, asset.load);
  }

  // Insert an inline style with the page nonce so CSP still allows it.
  function insertStyle(body: string): void {
    const el = doc.createElement("style");
    el.textContent = body;
    if (nonce !== undefined) el.nonce = nonce;
    doc.head.append(el);
  }

  // The <script> both script verbs insert. async is pinned to false so a src
  // element joins the in-order list instead of running as soon as it is ready.
  // It orders nothing for an inline classic script, which has nothing to fetch
  // and executes on append, ahead of a src script queued before it.
  function makeScript(load: "script" | "module"): HTMLScriptElement {
    const el = doc.createElement("script");
    if (load === "module") el.type = "module";
    el.async = false;
    if (nonce !== undefined) el.nonce = nonce;
    return el;
  }

  function loadCss(manifest: readonly Asset[], done: () => void): void {
    catchUp();
    // Inline styles insert synchronously and need no load gate, so they go in
    // during this pass and never join the url delta done waits on.
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
    // A 404 or a timeout on one sheet must not strand the whole envelope, so
    // each settles on its own and the ops run once the last one resolves.
    let pending = urls.length;
    let errored = false;
    const settle = (ok: boolean): void => {
      if (!ok) errored = true;
      pending -= 1;
      if (pending > 0) return;
      // The ops still run, so one 404 after a deploy cannot leave a form
      // response unapplied. partial:error reports the styling gap.
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
  }

  // One pass in manifest order rather than a pass per verb, so a module and a
  // classic script insert in the order the server listed them.
  function loadJs(manifest: readonly Asset[]): void {
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
  }

  function versionMismatch(envelopeVersion: string, url: string): boolean {
    if (envelopeVersion === "" || envelopeVersion === knownVersion) return false;
    if (knownVersion === "") {
      // The first envelope of the page teaches the runtime the live version,
      // there is nothing to be out of sync with yet.
      knownVersion = envelopeVersion;
      return false;
    }
    if (readFlag()) {
      // A reload already happened and the version still does not match: a stale
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
      // A discarded instance must not keep watching a document it no longer
      // seeds, or its scan would fire into a page the live registry owns. The
      // seeded flag stays set, since a rescan here would read that same document.
      unwatch?.();
      loaded.clear();
      knownVersion = "";
    },
  };
}

// A url-form entry that spells no address. new URL("", baseURI) resolves to the
// document itself, so the entry would fetch the page as a stylesheet and claim
// the key of a real asset addressing that same url.
function isEmptyUrl(asset: Asset): boolean {
  return asset.url === "";
}

// The dedup key for an inline body, shared by seed and the inline delta so the
// head bodies and the manifest bodies match byte for byte. Scoped by the
// insertion verb rather than the kind, so any kind carrying it still matches.
function inlineKey(load: AssetLoad, body: string): string {
  return `inline:${load}:${body}`;
}

// The text body of an element, a plain string on element nodes.
function textOf(element: Element): string {
  return element.textContent;
}

// The bootstrap script carries the page nonce. document.currentScript is null by
// the time a patch lands, so the value is captured at module evaluation and
// reused for every dynamically inserted asset.
function rememberNonce(doc: Document): string | undefined {
  const current = doc.currentScript;
  const value = current instanceof HTMLElement ? current.nonce : "";
  return value === "" ? undefined : value;
}
