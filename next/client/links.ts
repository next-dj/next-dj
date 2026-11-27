// Active links: after each navigation the links that point at the page on screen, or at
// a section above it, are marked current, as the server marked them on a full render.

import { ORDER_CURRENT } from "./kernel";
import type { HistoryHost, Settings } from "./router";
import { ATTR_CURRENT, ATTR_MATCH } from "./protocol";

export interface Here {
  raw: string;
  path: string;
  params: URLSearchParams;
  origin: string;
  homes: readonly string[];
}

const encoder = new TextEncoder();

const canonical = (segment: string): string =>
  segment
    .split(/(%[\da-f]{2})/i)
    .flatMap((part, index) =>
      index % 2 ? [parseInt(part.slice(1), 16)] : [...encoder.encode(part)],
    )
    .map((byte) => {
      const char = String.fromCharCode(byte);
      return /[\w.~!$&'()*+,;=:@-]/.test(char)
        ? char
        : `%${byte.toString(16).toUpperCase().padStart(2, "0")}`;
    })
    .join("");

export function normalPath(location: string): string {
  const kept: string[] = [];
  for (const segment of location.split("/").slice(1)) {
    const folded = segment.toLowerCase().replaceAll("%2e", ".");
    if (folded === "..") kept.pop();
    else if (folded !== ".") kept.push(canonical(segment));
  }
  if (kept.at(-1) === "") kept.pop();
  return `/${kept.join("/")}`;
}

export function linkMark(
  href: string,
  here: Here,
  exact: boolean,
  query: boolean,
): string | undefined {
  const ref = href
    .replace(/^[\0- ]+|[\0- ]+$/g, "")
    .replace(/[\t\n\r]/g, "")
    .replaceAll("\\", "/");
  if (!ref || ref.startsWith("#")) return undefined;
  const [path = "", ...rest] = ref.replace(/#.*/s, "").split("?");
  const scheme = /^[a-z][\da-z+.-]*:/i.exec(path)?.[0];
  const local = (authority: string, name: string): string | undefined => {
    const host = authority.replace(/\/.*/s, "");
    return `${name}://${host}`.toLowerCase() === here.origin
      ? authority.slice(host.length) || "/"
      : undefined;
  };
  let resolved: string | undefined = path;
  if (!path) resolved = here.raw;
  else if (path.startsWith("//"))
    resolved = local(path.slice(2), here.origin.replace(/:.*/s, ""));
  else if (scheme) {
    const tail = path.slice(scheme.length);
    resolved = local(tail.startsWith("//") ? tail.slice(2) : "", scheme.slice(0, -1));
  } else if (!path.startsWith("/"))
    resolved = here.raw.slice(0, here.raw.lastIndexOf("/") + 1) + path;
  if (resolved === undefined) return undefined;
  if (query || !path) {
    const want = new URLSearchParams(rest.join("?"));
    const values = (params: URLSearchParams, name: string): string =>
      JSON.stringify(params.getAll(name).sort());
    for (const name of want.keys())
      if (values(here.params, name) !== values(want, name)) return undefined;
  }
  const link = normalPath(resolved);
  const current = here.path;
  if (link === current) return "page";
  if (exact || link === "/" || !current.startsWith(`${link}/`)) return undefined;
  return here.homes.includes(link) ? undefined : "ancestor";
}

function sync(el: Element, name: string, value: string | null): void {
  if (el.getAttribute(name) === value) return;
  if (value === null) el.removeAttribute(name);
  else el.setAttribute(name, value);
}

// The links a full render marked, so a container holding one was marked whole.
const MARKED = `a[${ATTR_CURRENT}=page],a[${ATTR_CURRENT}=ancestor]`;

// The container selectors of the settings, each one the document accepts.
const valid = (settings: Settings): string[] =>
  (settings.links ?? []).filter((selector) => {
    try {
      document.querySelector(selector);
      return true;
    } catch {
      return false;
    }
  });

/** The active-link containers holding no marked link, those no render could mark. */
export const unmarked = (settings: Settings): Element[] =>
  valid(settings)
    .flatMap((selector) => [...document.querySelectorAll(selector)])
    .filter((el) => el.querySelector(MARKED) === null);

/** The active links pass, run after each apply and once when the chunk installs. */
export interface Links {
  /** Mark the containers the server could not, those a template variable wrote. */
  first(): void;
}

export function createLinks(
  host: Pick<HistoryHost, "hook" | "partial" | "settings">,
): Links {
  const doc = document;
  let seen: Settings | undefined;
  let links = "";
  let off = "";
  let match = "";
  let containers = "";
  let shown = "";

  // The selectors follow the settings, read again whenever the payload changed them.
  const read = (): Settings => {
    const settings = host.settings();
    if (settings === seen) return settings;
    seen = settings;
    shown = "";
    const any = (suffix: string): string =>
      valid(settings)
        .map((container) => container + suffix)
        .join();
    containers = any("");
    links = any(" a");
    off = `a[${ATTR_CURRENT}=off],${any(`[${ATTR_CURRENT}=off]`)}`;
    match = `a[${ATTR_MATCH}],${any(`[${ATTR_MATCH}]`)}`;
    return settings;
  };

  const markLinks = (found: Iterable<Element>, settings: Settings): void => {
    const base = new URL(host.partial.layers.urlFor(doc.body), location.href);
    shown = base.href;
    const here: Here = {
      raw: base.pathname,
      path: normalPath(base.pathname),
      params: base.searchParams,
      origin: location.origin,
      homes: settings.homes ?? [],
    };
    for (const link of found) {
      if (link.closest(off)) continue;
      const modes = (link.closest(match)?.getAttribute(ATTR_MATCH) ?? "")
        .toLowerCase()
        .split(/\s+/);
      const mark = linkMark(
        link.getAttribute("href") ?? "",
        here,
        modes.includes("exact"),
        modes.includes("query"),
      );
      sync(link, ATTR_CURRENT, mark ?? null);
      sync(link, "aria-current", mark === "page" ? mark : null);
    }
  };

  host.hook("applied", ORDER_CURRENT, ({ nodes = [] }) => {
    const settings = read();
    if (!containers) return;
    // Marks follow the URL and the links, so an apply that moves neither leaves them.
    const url = new URL(host.partial.layers.urlFor(doc.body), location.href).href;
    if (
      url === shown &&
      !nodes.some((node) => node.closest(containers) ?? node.querySelector(containers))
    )
      return;
    markLinks(doc.querySelectorAll(links), settings);
  });

  return {
    first() {
      const settings = read();
      // A link of nested containers is marked once.
      const left = unmarked(settings).flatMap((el) => [...el.querySelectorAll("a")]);
      if (left.length > 0) markLinks(new Set(left), settings);
    },
  };
}
