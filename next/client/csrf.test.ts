import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createCsrf, mintCsrf, readCsrf } from "./csrf";
import { createPartial } from "./partial";
import type { PartialSurface } from "./partial";
import { chunkModules, landed } from "./test-doubles";

const CSRF_URL = "/_next/csrf/";
const ENVELOPE_TYPE = "application/vnd.next.patches+json";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function minting(token = "minted") {
  const calls: { url: string; init: RequestInit }[] = [];
  let release: (() => void) | undefined;
  const fetch = (url: string, init: RequestInit): Promise<Response> => {
    calls.push({ url, init });
    return new Promise((resolve) => {
      release = () => resolve(json({ header: "X-CSRFToken", token }));
    });
  };
  return { fetch, calls, release: () => release?.() };
}

describe("readCsrf", () => {
  it("takes the eager and the deferred form", () => {
    expect(readCsrf({ header: "X-CSRFToken", token: "t" })).toEqual({
      header: "X-CSRFToken",
      token: "t",
    });
    expect(readCsrf({ header: "X-CSRFToken", url: CSRF_URL })).toEqual({
      header: "X-CSRFToken",
      url: CSRF_URL,
    });
  });

  it("reads a half-filled or foreign payload as absent", () => {
    expect(readCsrf({ header: "X-CSRFToken" })).toBeUndefined();
    expect(readCsrf({ token: "t" })).toBeUndefined();
    expect(readCsrf("nope")).toBeUndefined();
  });
});

describe("the deferred token", () => {
  it("mints once for concurrent callers, with the request flag and no cache", async () => {
    const { fetch, calls, release } = minting();
    const csrf = createCsrf({ mint: chunkModules.csrf, fetch });
    csrf.set({ header: "X-CSRFToken", url: CSRF_URL });
    const first = csrf.ensure();
    const second = csrf.ensure();
    release();
    expect(await first).toEqual({ header: "X-CSRFToken", token: "minted" });
    expect(await second).toEqual({ header: "X-CSRFToken", token: "minted" });
    expect(calls).toHaveLength(1);
    expect(calls[0]!.url).toBe(`${location.origin}${CSRF_URL}`);
    expect(calls[0]!.init).toMatchObject({
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "X-Next-Request": "1" },
    });
    await csrf.ensure();
    expect(calls).toHaveLength(1);
  });

  it("answers an eager token or an absent payload without a request", async () => {
    const { fetch, calls } = minting();
    const csrf = createCsrf({ mint: chunkModules.csrf, fetch });
    expect(await csrf.ensure()).toBeUndefined();
    csrf.set({ header: "X-CSRFToken", token: "eager" });
    expect(await csrf.ensure()).toEqual({ header: "X-CSRFToken", token: "eager" });
    expect(calls).toEqual([]);
  });

  it("a refused mint resolves empty and the next need asks again", async () => {
    let status = 403;
    let calls = 0;
    const csrf = createCsrf({
      mint: chunkModules.csrf,
      fetch: async () => {
        calls += 1;
        return json({ header: "X-CSRFToken", token: "second" }, status);
      },
    });
    csrf.set({ header: "X-CSRFToken", url: CSRF_URL });
    expect(await csrf.ensure()).toBeUndefined();
    status = 200;
    expect(await csrf.ensure()).toEqual({ header: "X-CSRFToken", token: "second" });
    expect(calls).toBe(2);
  });

  it("a network failure or a body without a token is a refusal", async () => {
    const thrown = createCsrf({
      mint: chunkModules.csrf,
      fetch: () => Promise.reject(new TypeError("offline")),
    });
    thrown.set({ header: "X-CSRFToken", url: CSRF_URL });
    expect(await thrown.ensure()).toBeUndefined();
    const tokenless = createCsrf({
      mint: chunkModules.csrf,
      fetch: async () => json({ header: "X-CSRFToken" }),
    });
    tokenless.set({ header: "X-CSRFToken", url: CSRF_URL });
    expect(await tokenless.ensure()).toBeUndefined();
  });

  it("never sends the request off the page's origin", async () => {
    const { fetch, calls } = minting();
    const csrf = createCsrf({ mint: chunkModules.csrf, fetch });
    csrf.set({ header: "X-CSRFToken", url: "https://attacker.example/csrf/" });
    expect(await csrf.ensure()).toBeUndefined();
    expect(calls).toEqual([]);
  });

  it("a token rotated in while the mint is in flight wins", async () => {
    const { fetch, release } = minting("stale");
    const csrf = createCsrf({ mint: chunkModules.csrf, fetch });
    csrf.set({ header: "X-CSRFToken", url: CSRF_URL });
    const pending = csrf.ensure();
    csrf.set({ header: "X-CSRFToken", token: "rotated" });
    release();
    expect(await pending).toEqual({ header: "X-CSRFToken", token: "rotated" });
    expect(csrf.current()).toEqual({ header: "X-CSRFToken", token: "rotated" });
  });

  it("_reset drops the token and the mint in flight", async () => {
    const { fetch, calls, release } = minting();
    const csrf = createCsrf({ mint: chunkModules.csrf, fetch });
    csrf.set({ header: "X-CSRFToken", url: CSRF_URL });
    const pending = csrf.ensure();
    csrf._reset();
    release();
    expect(await pending).toBeUndefined();
    expect(csrf.current()).toBeUndefined();
    csrf.set({ header: "X-CSRFToken", url: CSRF_URL });
    void csrf.ensure();
    expect(calls).toHaveLength(2);
  });

  it("fetches the csrf chunk first when it has not landed yet", async () => {
    const { fetch, calls, release } = minting();
    const csrf = createCsrf({
      mint: { get: () => undefined, load: () => landed(mintCsrf).load() },
      fetch,
    });
    csrf.set({ header: "X-CSRFToken", url: CSRF_URL });
    const pending = csrf.ensure();
    await Promise.resolve();
    release();
    expect(await pending).toEqual({ header: "X-CSRFToken", token: "minted" });
    expect(calls).toHaveLength(1);
  });

  it("a csrf chunk that cannot load is a refusal the next need retries", async () => {
    const { fetch, calls } = minting();
    const csrf = createCsrf({
      mint: { get: () => undefined, load: () => Promise.resolve(undefined) },
      fetch,
    });
    csrf.set({ header: "X-CSRFToken", url: CSRF_URL });
    expect(await csrf.ensure()).toBeUndefined();
    expect(calls).toEqual([]);
    expect(csrf.current()).toEqual({ header: "X-CSRFToken", url: CSRF_URL });
  });

  it("prefetches on the first focus inside an action form, then stops listening", () => {
    document.body.innerHTML =
      '<p><input id="plain"></p><form data-next-action="u1"><input id="field"></form>';
    const { fetch, calls } = minting();
    const csrf = createCsrf({ mint: chunkModules.csrf, fetch });
    csrf.set({ header: "X-CSRFToken", url: CSRF_URL });
    const detach = csrf.install(document);
    document
      .querySelector("#plain")!
      .dispatchEvent(new FocusEvent("focusin", { bubbles: true }));
    expect(calls).toEqual([]);
    document
      .querySelector("#field")!
      .dispatchEvent(new Event("pointerdown", { bubbles: true }));
    document.dispatchEvent(new Event("pointerdown"));
    expect(calls).toHaveLength(1);
    detach();
  });
});

describe("a mutation on a page with a deferred token", () => {
  let partial: PartialSurface;
  let calls: { url: string; init: RequestInit }[];
  let errors: Record<string, unknown>[];
  let mint: () => Response;

  beforeEach(() => {
    calls = [];
    errors = [];
    mint = () => json({ header: "X-CSRFToken", token: "minted" });
    partial = createPartial({
      ...chunkModules,
      dispatch: (event, detail) => {
        if (event === "partial:error") errors.push(detail);
      },
      mergeContext: () => undefined,
    });
    partial._configure({
      document,
      navigate: () => undefined,
      fetch: async (url, init) => {
        calls.push({ url, init });
        if (url.endsWith(CSRF_URL)) return mint();
        return new Response(
          '{"version":"v1","ops":[],"assets":[],"form":null,' +
            '"csrf":{"header":"X-CSRFToken","token":"rotated"}}',
          { headers: { "content-type": ENVELOPE_TYPE } },
        );
      },
    });
    partial.setCsrf({ header: "X-CSRFToken", url: CSRF_URL });
  });

  afterEach(() => {
    partial._reset();
  });

  function tokenOf(index: number): string | null {
    return new Headers(calls[index]!.init.headers).get("X-CSRFToken");
  }

  it("mints the token first, then rotates it from the envelope", async () => {
    await partial.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    expect(calls.map((c) => new URL(c.url).pathname)).toEqual([
      CSRF_URL,
      "/_next/form/u1/",
    ]);
    expect(tokenOf(1)).toBe("minted");
    await partial.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    expect(calls).toHaveLength(3);
    expect(tokenOf(2)).toBe("rotated");
  });

  it("a safe GET never waits for a token", async () => {
    await partial.fetch({ url: "/list/", zone: "z" });
    expect(calls.map((c) => new URL(c.url).pathname)).toEqual(["/list/"]);
  });

  it("holds the mutation back and reports a csrf error when the mint fails", async () => {
    mint = () => json({}, 500);
    await partial.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    expect(calls).toHaveLength(1);
    expect(errors).toEqual([
      expect.objectContaining({ kind: "csrf", url: "/_next/form/u1/" }),
    ]);
  });

  it("keeps the deferred token across a reconfigure", async () => {
    partial._configure({
      document,
      fetch: async (url, init) => {
        calls.push({ url, init });
        return mint();
      },
    });
    await partial.fetch({ url: "/_next/form/u1/", method: "POST", uid: "u1" });
    expect(new URL(calls[0]!.url).pathname).toBe(CSRF_URL);
  });
});
