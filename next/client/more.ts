import { fireRemoved } from "./morph";
import type { PartialSurface } from "./partial";
import { ATTR_ZONE, cssEscape } from "./protocol";
import type { Settled } from "./wire";

const BATCH = "data-next-batch";

export interface MoreHost {
  partial: Pick<PartialSurface, "apply" | "layers" | "onMount">;
}

type Run = [list: string, link: HTMLAnchorElement, param: string, token: string];
type Token = string | null | undefined;

export function createMore(
  { partial }: MoreHost,
  listening: AddEventListenerOptions = {},
): void {
  const doc = document;
  const runs = new Map<string, Run>();
  const failures = new Map<string, number>();
  const backs = new Map<Token, Token>();
  let batch = 0;

  const zoneOf = (name: string): Element | null =>
    partial.layers.resolveZone(name, doc);

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
    scrollBy(0, zone.getBoundingClientRect().bottom - bottom);
    const back = backs.get(stamps[cut]);
    const less = doc.querySelector<HTMLAnchorElement>(
      `[${ATTR_ZONE}="${cssEscape(`${list}-less`)}"] a[href]`,
    );
    if (less === null || !back) return;
    const href = new URL(less.href);
    href.searchParams.set(param, back);
    href.searchParams.delete("restore");
    less.href = href.href;
    if (less.hidden) {
      less.hidden = false;
      (less.parentNode as Element).replaceWith(less);
    }
  }

  function settle(
    sentinel: string,
    [list, link, param, token]: Run,
    status: string,
  ): void {
    const zone = zoneOf(list);
    if (status === "error" && link.hasAttribute("data-next-defer")) {
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
    if (sentinel.endsWith("-less")) {
      const top = zone.querySelector(`:scope>[${BATCH}]`);
      if (top) backs.set(top.getAttribute(BATCH), token);
      stamp(zone, [null]);
      return;
    }
    stamp(zone, [link.getAttribute("data-next-back")]);
    const mode = link.getAttribute("data-next-url");
    const limit = Number(link.getAttribute("data-next-prune"));
    if (mode !== null) {
      const url = new URL(location.href);
      url.searchParams.set(param, token);
      url.searchParams.set("restore", "1");
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
      `[${ATTR_ZONE}="${cssEscape(sentinel)}"] a[data-next-target]`,
    );
    if (
      link?.getAttribute("href") !== url ||
      link.getAttribute("data-next-target") !== zones
    )
      return;
    const param = link.getAttribute("data-next-param") ?? "page";
    const token = new URL(url, location.href).searchParams.get(param);
    if (token !== null)
      runs.set(sentinel, [zones.replace(/,.*/, ""), link, param, token]);
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
  partial.onMount("a[data-next-url],a[data-next-prune]", (link) => {
    const target = link.getAttribute("data-next-target") ?? "";
    const list = target.replace(/,.*/, "");
    const zone = zoneOf(list);
    const sentinel = `${list}-more`;
    if (zone === null || target !== `${list},${sentinel}` || runs.has(sentinel)) return;
    stamp(zone, [null, ...(link.getAttribute("data-next-backs")?.split(" ") ?? [])]);
  });
}
