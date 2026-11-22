import type { HistoryHost, Settings } from "./router";

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

export function createCurrent(
  host: Pick<HistoryHost, "partial" | "settings">,
  listening: AddEventListenerOptions = {},
): void {
  const doc = document;
  let seen: Settings | undefined;
  let links = "";
  let off = "";
  let match = "";
  let containers = "";
  let shown = "";
  const mark = (event: Event): void => {
    const settings = host.settings();
    if (settings !== seen) {
      seen = settings;
      shown = "";
      const valid = (settings.current ?? []).filter((selector) => {
        try {
          doc.querySelector(selector);
          return true;
        } catch {
          return false;
        }
      });
      const any = (suffix: string): string =>
        valid.map((container) => container + suffix).join();
      containers = any("");
      links = any(" a");
      off = `a[data-next-current=off],${any("[data-next-current=off]")}`;
      match = `a[data-next-match],${any("[data-next-match]")}`;
    }
    if (!containers) return;
    const base = new URL(host.partial.layers.urlFor(doc.body), location.href);
    const { nodes = [] } = (event as CustomEvent<{ nodes?: Element[] }>).detail;
    // Marks follow the URL and the links, so an apply that moves neither leaves them.
    if (
      base.href === shown &&
      !nodes.some((node) => node.closest(containers) ?? node.querySelector(containers))
    )
      return;
    shown = base.href;
    const here: Here = {
      raw: base.pathname,
      path: normalPath(base.pathname),
      params: base.searchParams,
      origin: location.origin,
      homes: settings.homes ?? [],
    };
    for (const link of doc.querySelectorAll(links)) {
      if (link.closest(off)) continue;
      const modes = (link.closest(match)?.getAttribute("data-next-match") ?? "")
        .toLowerCase()
        .split(/\s+/);
      const found = linkMark(
        link.getAttribute("href") ?? "",
        here,
        modes.includes("exact"),
        modes.includes("query"),
      );
      sync(link, "data-next-current", found ?? null);
      sync(link, "aria-current", found === "page" ? found : null);
    }
  };
  doc.addEventListener("partial:applied", mark, listening);
}
