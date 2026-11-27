// The CSRF token a mutation sends. A page that minted no token ships only the endpoint,
// so a mutation reads the CSRF cookie when scripts may, and otherwise fetches the token
// once and keeps it for the life of the page. The fetch code ships in next.csrf.min.js.

import { defaultFetch } from "./adapters";
import type { LazyModule } from "./chunks";
import {
  ATTR_ACTION,
  REQUEST_FLAG,
  asString,
  bounded,
  isRecord,
  sameOrigin,
} from "./protocol";
import type { CsrfPayload, CsrfSource, FetchAdapter } from "./wire";

/** Fetch a token from its endpoint, resolving undefined on any failure. */
export type CsrfMint = (
  url: string,
  fetch: FetchAdapter,
  doc: Document,
  signal?: AbortSignal,
) => Promise<CsrfPayload | undefined>;

export interface Csrf extends CsrfSource {
  set(payload: CsrfPayload | undefined): void;
  cancelPending(): void;
  /** Prefetch the token on the first focus or press inside an action form. */
  install(doc: Document): () => void;
  /** Harness seam, absent from the shipped bundles. */
  _reset(): void;
}

export interface CsrfDeps {
  fetch?: FetchAdapter;
  document?: Document;
  // The token fetch, exported by the csrf chunk.
  mint: LazyModule<CsrfMint>;
  // Told the token a mint brought, since its endpoint may have set a new cookie.
  rotated?: (token: string) => void;
}

/** Narrow a wire payload to its token or endpoint form, undefined when incomplete. */
export function readCsrf(value: unknown): CsrfPayload | undefined {
  if (!isRecord(value)) return undefined;
  const header = asString(value.header);
  const token = asString(value.token);
  const url = asString(value.url);
  const cookie = asString(value.cookie);
  if (header === undefined) return undefined;
  if (token !== undefined) return { header, token };
  if (url === undefined) return undefined;
  return cookie === undefined ? { header, url } : { header, url, cookie };
}

/** Fetch a token from a same-origin endpoint, the function the csrf chunk exports. */
export async function mintCsrf(
  url: string,
  fetch: FetchAdapter,
  doc: Document,
  signal?: AbortSignal,
): Promise<CsrfPayload | undefined> {
  const target = sameOrigin(url, doc);
  try {
    if (target !== undefined) {
      const response = await fetch(target, {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: { [REQUEST_FLAG]: "1" },
        ...(signal === undefined ? {} : { signal }),
      });
      if (response.ok) return readCsrf(await response.json());
    }
  } catch {
    // A network failure or a non-JSON body counts as a failure.
  }
  return undefined;
}

/** Build the token store the wire reads, fetching a deferred token on first use. */
export function createCsrf(deps: CsrfDeps): Csrf {
  const fetch = deps.fetch ?? defaultFetch();
  const doc = deps.document ?? document;
  let payload: CsrfPayload | undefined;
  let pending: Promise<CsrfPayload | undefined | null> | undefined;
  // The signals of the callers waiting on the pending mint, which ends once all abort.
  let waiters: AbortSignal[] = [];
  let controller: AbortController | undefined;
  // Incremented by set and _reset, so a fetch started earlier cannot overwrite them.
  let epoch = 0;

  async function mint(url: string): Promise<CsrfPayload | undefined | null> {
    const started = epoch;
    const { signal } = (controller = new AbortController());
    const mintWith = deps.mint.get() ?? (await deps.mint.load());
    // A chunk that cannot load answers null, since its loader reported it as an asset.
    const minted =
      mintWith === undefined
        ? null
        : started === epoch
          ? await mintWith(url, fetch, doc, signal)
          : undefined;
    // A token an envelope set while this fetch was in flight is newer and is kept.
    if (started !== epoch) return payload;
    // Cleared, so the next mutation fetches again and a late abort cancels nothing.
    pending = undefined;
    if (minted?.token === undefined) return minted === null ? null : undefined;
    payload = minted;
    deps.rotated?.(minted.token);
    return minted;
  }

  // A cookie scripts may read holds the secret Django accepts in the header as well.
  function readCookie(name: string | undefined): string | undefined {
    const prefix = `${name}=`;
    const found =
      name && doc.cookie.split("; ").find((pair) => pair.startsWith(prefix));
    return found ? found.slice(prefix.length) : undefined;
  }

  function ensure(signal?: AbortSignal): Promise<CsrfPayload | undefined | null> {
    const held = payload;
    if (held?.url === undefined || held.token !== undefined)
      return Promise.resolve(held);
    // Read per mutation, so a cookie a login rotated in another tab is the one sent.
    const { header, cookie, url } = held;
    const token = readCookie(cookie);
    if (token) return Promise.resolve({ header, token });
    // An aborted caller starts no mint.
    if (signal?.aborted === true) return bounded(Promise.resolve(payload), signal);
    if (pending === undefined) {
      waiters = [];
      // mint awaits before it touches pending, so the promise is in place by then.
      pending = mint(url);
    }
    const active = pending;
    if (signal === undefined) return active;
    waiters.push(signal);
    signal.addEventListener(
      "abort",
      () => {
        if (pending === active && waiters.every((waiter) => waiter.aborted))
          cancelPending();
      },
      { once: true },
    );
    return bounded(active, signal);
  }

  // Aborting the controller of a mint that already settled is harmless.
  function cancelPending(): void {
    controller?.abort();
    pending = undefined;
    epoch += 1;
  }

  function set(next: CsrfPayload | undefined): void {
    payload = next;
    cancelPending();
  }

  const exposed = {
    current: () => payload,
    ensure,
    cancelPending,
    set,
    install(target) {
      const controller = new AbortController();
      const prefetch = (event: Event): void => {
        const el = event.target;
        if (!(el instanceof Element) || el.closest(`[${ATTR_ACTION}]`) === null) return;
        controller.abort();
        void ensure();
      };
      const options = { capture: true, signal: controller.signal };
      target.addEventListener("focusin", prefetch, options);
      target.addEventListener("pointerdown", prefetch, options);
      return () => controller.abort();
    },
  } satisfies Omit<Csrf, "_reset"> as Csrf;

  TEST: exposed._reset = () => set(undefined);

  return exposed;
}
