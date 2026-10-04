// The CSRF token a mutation sends. A page in a shared cache ships only the token
// endpoint, so the token is fetched on first use and kept for the life of the page.
// The fetch code ships in next.csrf.min.js, which only such a page loads.

import { defaultFetch } from "./adapters";
import type { LazyModule } from "./chunks";
import { ATTR_ACTION, REQUEST_FLAG, asString, isRecord, sameOrigin } from "./protocol";
import type { CsrfPayload, CsrfSource, FetchAdapter } from "./wire";

/** Fetch a token from its endpoint, resolving undefined on any failure. */
export type CsrfMint = (
  url: string,
  fetch: FetchAdapter,
  doc: Document,
) => Promise<CsrfPayload | undefined>;

export interface Csrf extends CsrfSource {
  set(payload: CsrfPayload | undefined): void;
  /** Prefetch the token on the first focus or press inside an action form. */
  install(doc: Document): () => void;
  _reset(): void;
}

export interface CsrfDeps {
  fetch?: FetchAdapter;
  document?: Document;
  // The token fetch, exported by the csrf chunk.
  mint: LazyModule<CsrfMint>;
}

/** Narrow a wire payload to its token or endpoint form, undefined when incomplete. */
export function readCsrf(value: unknown): CsrfPayload | undefined {
  if (!isRecord(value)) return undefined;
  const header = asString(value.header);
  const token = asString(value.token);
  const url = asString(value.url);
  if (header === undefined) return undefined;
  if (token !== undefined) return { header, token };
  return url === undefined ? undefined : { header, url };
}

/** Fetch a token from a same-origin endpoint, the function the csrf chunk exports. */
export async function mintCsrf(
  url: string,
  fetch: FetchAdapter,
  doc: Document,
): Promise<CsrfPayload | undefined> {
  const target = sameOrigin(url, doc);
  try {
    if (target !== undefined) {
      const response = await fetch(target, {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: { [REQUEST_FLAG]: "1" },
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
  let pending: Promise<CsrfPayload | undefined> | undefined;
  // Incremented by set and _reset, so a fetch started earlier cannot overwrite them.
  let epoch = 0;

  async function mint(url: string): Promise<CsrfPayload | undefined> {
    const started = epoch;
    // A chunk that cannot load counts as a failure, already reported as an asset error.
    const mintWith = deps.mint.get() ?? (await deps.mint.load());
    const minted = await mintWith?.(url, fetch, doc);
    // A token an envelope set while this fetch was in flight is newer and is kept.
    if (started !== epoch) return payload;
    if (minted?.token === undefined) {
      // Cleared, so the next mutation fetches again instead of failing permanently.
      pending = undefined;
      return undefined;
    }
    payload = minted;
    return minted;
  }

  function ensure(): Promise<CsrfPayload | undefined> {
    const url = payload?.url;
    if (payload?.token !== undefined || url === undefined)
      return Promise.resolve(payload);
    pending ??= mint(url);
    return pending;
  }

  return {
    current: () => payload,
    ensure,
    set(next) {
      payload = next;
      pending = undefined;
      epoch += 1;
    },
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
    _reset() {
      payload = undefined;
      pending = undefined;
      epoch += 1;
    },
  };
}
