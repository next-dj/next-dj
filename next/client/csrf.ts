// The CSRF token a mutation stamps. A page cached for everyone ships only the minting
// endpoint, so the token is fetched once on first need and kept for the page's life.
// The fetch itself ships in next.csrf.min.js, which only such a page ever loads.

import { defaultFetch } from "./adapters";
import type { LazyModule } from "./chunks";
import { ATTR_ACTION, REQUEST_FLAG, asString, isRecord, sameOrigin } from "./protocol";
import type { CsrfPayload, CsrfSource, FetchAdapter } from "./wire";

/** Fetch a token from its minting endpoint, undefined on any refusal. */
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
  // The mint, held by the csrf chunk.
  mint: LazyModule<CsrfMint>;
}

/** Narrow a wire payload to either form, a half-filled one read as absent. */
export function readCsrf(value: unknown): CsrfPayload | undefined {
  if (!isRecord(value)) return undefined;
  const header = asString(value.header);
  const token = asString(value.token);
  const url = asString(value.url);
  if (header === undefined) return undefined;
  if (token !== undefined) return { header, token };
  return url === undefined ? undefined : { header, url };
}

/** The mint the csrf chunk carries. */
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
    // A network failure or a non-JSON body is a refusal like any other.
  }
  return undefined;
}

export function createCsrf(deps: CsrfDeps): Csrf {
  const fetch = deps.fetch ?? defaultFetch();
  const doc = deps.document ?? document;
  let payload: CsrfPayload | undefined;
  let pending: Promise<CsrfPayload | undefined> | undefined;
  // Bumped by set and _reset, so a mint they overtook cannot write over them.
  let epoch = 0;

  async function mint(url: string): Promise<CsrfPayload | undefined> {
    const started = epoch;
    // A chunk that cannot load is a refusal too, already reported as an asset error.
    const mintWith = deps.mint.get() ?? (await deps.mint.load());
    const minted = await mintWith?.(url, fetch, doc);
    // A token rotated in by an envelope while this was in flight is the fresher one.
    if (started !== epoch) return payload;
    if (minted?.token === undefined) {
      // Forgotten, so the next mutation asks again rather than failing for good.
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
