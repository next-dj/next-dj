// The `{% more %}` pager: the address bar follows the loaded page, and pruning drops old
// batches off the top while the "less" link learns where to page back to.

import type { Kernel } from "./kernel";
import { fireRemoved } from "./morph";
import {
  ATTR_BACK,
  ATTR_BACKS,
  ATTR_BATCH,
  ATTR_DEFER,
  ATTR_PARAM,
  ATTR_PRUNE,
  ATTR_TARGET,
  ATTR_URL,
  ATTR_ZONE,
  LESS_SUFFIX,
  RESTORE_PARAM,
  cssEscape,
} from "./protocol";
import type { Settled } from "./wire";

const BATCH = ATTR_BATCH;

export type MoreHost = Pick<Kernel, "partial">;

// A `{% more %}` or `{% less %}` request in flight, the list it grows and its page.
interface Run {
  list: string;
  link: HTMLAnchorElement;
  param: string;
  token: string;
}

// The page token a batch of rows came with, null for the first page.
type Token = string | null | undefined;

export function createMore(
  { partial }: MoreHost,
  listening: AddEventListenerOptions = {},
): void {
  const doc = document;
  const runs = new Map<string, Run>();
  // The failed loads of each list in a row, so a deferred sentinel retries with backoff.
  const failures = new Map<string, number>();
  // The page token each batch of rows came with, read back when pruning moves the
  // "less" link to the oldest batch still on screen.
  const backs = new Map<Token, Token>();
  let batch = 0;

  const zoneOf = (name: string): Element | null =>
    partial.layers.resolveZone(name, doc);

  // Mark the rows each token brought, spread evenly when one answer carries several.
  function stamp(zone: Element, tokens: Token[]): void {
    const rows = Array.from(zone.children).filter((row) => !row.hasAttribute(BATCH));
    let at = -1;
    rows.forEach((row, i) => {
      const index = Math.floor((i * tokens.length) / rows.length);
      if (index !== at) {
        at = index;
        backs.set(`${++batch}`, tokens[index]);
      }
      row.setAttribute(BATCH, `${batch}`);
    });
  }

  // Drop whole batches off the top past the limit, keeping the scroll where it was.
  function prune(zone: Element, list: string, param: string, limit: number): void {
    const rows = Array.from(zone.children);
    const stamps = rows.map((row) => row.getAttribute(BATCH));
    let cut = 0;
    for (let i = 1; i < rows.length && rows.length - cut > limit; i++)
      if (stamps[i] !== stamps[i - 1]) cut = i;
    if (cut === 0) return;
    const bottom = zone.getBoundingClientRect().bottom;
    for (const row of rows.slice(0, cut)) {
      fireRemoved(row);
      row.remove();
    }
    // The batches of the pruned rows can no longer be paged back to.
    for (const gone of new Set(stamps.slice(0, cut))) backs.delete(gone);
    scrollBy(0, zone.getBoundingClientRect().bottom - bottom);
    const back = backs.get(stamps[cut]);
    const less = doc.querySelector<HTMLAnchorElement>(
      `[${ATTR_ZONE}="${cssEscape(`${list}${LESS_SUFFIX}`)}"] a[href]`,
    );
    if (less === null || !back) return;
    const href = new URL(less.href);
    href.searchParams.set(param, back);
    href.searchParams.delete(RESTORE_PARAM);
    less.href = href.href;
    if (less.hidden) {
      less.hidden = false;
      (less.parentNode as Element).replaceWith(less);
    }
  }

  function settle(
    sentinel: string,
    { list, link, param, token }: Run,
    status: string,
  ): void {
    const zone = zoneOf(list);
    if (status === "error" && link.hasAttribute(ATTR_DEFER)) {
      const failed = (failures.get(list) ?? 0) + 1;
      failures.set(list, failed);
      if (failed < 4)
        setTimeout(
          () => !listening.signal?.aborted && link.isConnected && link.click(),
          500 * 2 ** failed,
        );
    }
    if (status !== "ok" || zone === null) return;
    failures.delete(list);
    if (sentinel.endsWith(LESS_SUFFIX)) {
      const top = zone.querySelector(`:scope>[${BATCH}]`);
      if (top) backs.set(top.getAttribute(BATCH), token);
      stamp(zone, [null]);
      return;
    }
    stamp(zone, [link.getAttribute(ATTR_BACK)]);
    const mode = link.getAttribute(ATTR_URL);
    const limit = Number(link.getAttribute(ATTR_PRUNE));
    if (mode !== null) {
      const url = new URL(location.href);
      url.searchParams.set(param, token);
      // The value names the pager, so the server reads back that pager alone.
      url.searchParams.set(RESTORE_PARAM, param);
      const href = url.pathname + url.search + url.hash;
      partial.apply({
        version: "",
        ops: [{ op: "url", href, action: mode, quiet: true }],
      });
    }
    if (limit > 0) prune(zone, list, param, limit);
  }

  function onRequest(event: Event): void {
    const { url, intent } = (
      event as CustomEvent<{ url: string; intent: { zone?: string } }>
    ).detail;
    const zones = intent.zone ?? "";
    const sentinel = zones.split(",")[1] ?? "";
    if (!/-(more|less)$/.test(sentinel)) return;
    const link = doc.querySelector<HTMLAnchorElement>(
      `[${ATTR_ZONE}="${cssEscape(sentinel)}"] a[${ATTR_TARGET}]`,
    );
    if (link?.getAttribute("href") !== url || link.getAttribute(ATTR_TARGET) !== zones)
      return;
    const param = link.getAttribute(ATTR_PARAM) ?? "page";
    const token = new URL(url, location.href).searchParams.get(param);
    if (token !== null)
      runs.set(sentinel, { list: zones.replace(/,.*/, ""), link, param, token });
  }

  function onSettled(event: Event): void {
    const { zones, status } = (event as CustomEvent<Settled>).detail;
    for (const name of zones) {
      const run = runs.get(name);
      runs.delete(name);
      if (run !== undefined) settle(name, run, status);
    }
  }

  doc.addEventListener("partial:before-request", onRequest, listening);
  doc.addEventListener("partial:settled", onSettled, listening);
  partial.onMount(`a[${ATTR_URL}],a[${ATTR_PRUNE}]`, (link) => {
    const target = link.getAttribute(ATTR_TARGET) ?? "";
    const list = target.replace(/,.*/, "");
    const zone = zoneOf(list);
    const sentinel = `${list}-more`;
    if (zone === null || target !== `${list},${sentinel}` || runs.has(sentinel)) return;
    stamp(zone, [null, ...(link.getAttribute(ATTR_BACKS)?.split(" ") ?? [])]);
  });
}
