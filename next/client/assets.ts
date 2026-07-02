// The asset loader and the version safeguard. A registry of URLs already on the
// page is seeded by scanning the initial DOM on `ready`. The envelope ships a
// full manifest of its rendered targets, the client computes the delta against
// that registry, so the loaded-asset list never travels from client to server.
//
// Two views, two rules: missing CSS is inserted and awaited (bounded) before the
// ops run so there is no FOUC, missing JS runs after the ops so the target DOM
// is already in place. Each URL executes exactly once per page life. A version
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
import { isAsset } from "./apply";
import type { Asset } from "./apply";
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
  // Insert the missing CSS of a manifest and call done once every new sheet has
  // loaded, errored, or timed out. With no missing CSS the callback runs
  // synchronously so the ops apply in the same tick.
  loadCss(manifest: Asset[], done: () => void): void;
  // Run the missing JS of a manifest after the ops, each URL once per page.
  loadJs(manifest: Asset[]): void;
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
  // Every URL inserted or scanned, the dedup key for "execute once per page".
  const loaded = new Set<string>();
  // The nonce remembered from the script that bootstrapped the runtime, copied
  // onto every dynamically inserted asset so CSP keeps allowing them.
  const nonce = rememberNonce(doc);
  let knownVersion = "";

  function seed(): void {
    for (const link of Array.from(
      doc.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"][href]'),
    )) {
      loaded.add(link.href);
    }
    for (const script of Array.from(
      doc.querySelectorAll<HTMLScriptElement>("script[src]"),
    )) {
      loaded.add(script.src);
    }
    // Seed the inline bodies the server already materialised into the head, so
    // the first zone GET of a zone whose inline is up there does not re-insert
    // the <style> or re-execute the <script>. textContent on an element node is
    // a string, never null, so the body reads straight through.
    for (const style of Array.from(doc.querySelectorAll<HTMLStyleElement>("style"))) {
      loaded.add(inlineKey("css", textOf(style)));
    }
    for (const script of Array.from(
      doc.querySelectorAll<HTMLScriptElement>("script:not([src])"),
    )) {
      loaded.add(inlineKey("js", textOf(script)));
    }
  }

  function missing(manifest: Asset[], kind: string): string[] {
    const urls: string[] = [];
    for (const asset of manifest) {
      if (!isAsset(asset) || asset.kind !== kind) continue;
      // An inline asset rides the inline path, never this URL delta: its empty
      // url would otherwise collapse every inline body onto one "" key.
      if (asset.inline !== undefined) continue;
      if (loaded.has(asset.url)) continue;
      // Mark loaded eagerly so a manifest that names the same URL twice, or a
      // re-entrant apply, inserts it exactly once.
      loaded.add(asset.url);
      urls.push(asset.url);
    }
    return urls;
  }

  // The inline bodies of a manifest, deduped by body under a kind-scoped key so
  // the same inline asset inserts once per page and never clashes with a URL.
  function missingInline(manifest: Asset[], kind: string): string[] {
    const bodies: string[] = [];
    for (const asset of manifest) {
      if (!isAsset(asset) || asset.kind !== kind) continue;
      if (asset.inline === undefined) continue;
      const key = inlineKey(kind, asset.inline);
      if (loaded.has(key)) continue;
      loaded.add(key);
      bodies.push(asset.inline);
    }
    return bodies;
  }

  // Insert an inline body with the page nonce so CSP still allows it.
  function insertInline(tag: "style" | "script", body: string): void {
    const el = doc.createElement(tag);
    el.textContent = body;
    if (nonce !== undefined) el.nonce = nonce;
    doc.head.append(el);
  }

  function loadCss(manifest: Asset[], done: () => void): void {
    // Inline styles insert synchronously and need no load gate, so they go in
    // before the URL delta whose loads the done callback waits on.
    for (const body of missingInline(manifest, "css")) {
      insertInline("style", body);
    }
    const urls = missing(manifest, "css");
    if (urls.length === 0) {
      done();
      return;
    }
    // A 404 or timeout on one sheet must not strand the whole envelope: each
    // settles independently, the ops run once the last one resolves.
    let pending = urls.length;
    let errored = false;
    const settle = (ok: boolean): void => {
      if (!ok) errored = true;
      pending -= 1;
      if (pending > 0) return;
      // One 404 after a deploy cannot leave a form response unapplied forever:
      // ops still run, partial:error reports the styling gap.
      if (errored) {
        deps.dispatch("partial:error", {
          kind: "asset",
          error: new Error("a stylesheet failed to load"),
        });
      }
      done();
    };
    for (const url of urls) {
      loadLink(url, nonce, settle, clock, cssTimeout);
    }
  }

  function loadJs(manifest: Asset[]): void {
    for (const body of missingInline(manifest, "js")) {
      insertInline("script", body);
    }
    for (const url of missing(manifest, "js")) {
      const script = doc.createElement("script");
      script.src = url;
      script.async = false;
      if (nonce !== undefined) script.nonce = nonce;
      doc.head.append(script);
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
      });
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
      loaded.clear();
      knownVersion = "";
    },
  };
}

// The dedup key for an inline body, shared by seed and the inline delta so the
// seeded head bodies and the manifest bodies match byte for byte.
function inlineKey(kind: string, body: string): string {
  return `inline:${kind}:${body}`;
}

// The text body of an element. textContent is typed string | null, but for an
// element node the DOM always yields a string, so String() coerces with no
// untestable null branch left for the coverage gate.
function textOf(element: Element): string {
  return String(element.textContent);
}

// The bootstrap script carries the page nonce. document.currentScript is null by
// the time a patch lands, so the value is captured at module evaluation and
// reused for every dynamically inserted asset.
function rememberNonce(doc: Document): string | undefined {
  const current = doc.currentScript;
  const value = current instanceof HTMLElement ? current.nonce : "";
  return value !== undefined && value !== "" ? value : undefined;
}
