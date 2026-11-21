import type { HistoryHost } from "./router";

export interface Here {
  raw: string;
  query: string;
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
    const have = new URLSearchParams(here.query);
    const want = new URLSearchParams(rest.join("?"));
    const values = (params: URLSearchParams, name: string): string =>
      JSON.stringify(params.getAll(name).sort());
    for (const name of want.keys())
      if (values(have, name) !== values(want, name)) return undefined;
  }
  const link = normalPath(resolved);
  const current = normalPath(here.raw);
  if (link === current) return "page";
  if (exact || link === "/" || !current.startsWith(`${link}/`)) return undefined;
  return here.homes.includes(link) ? undefined : "ancestor";
}

function put(el: Element, name: string, value: string | null): void {
  if (el.getAttribute(name) === value) return;
  if (value === null) el.removeAttribute(name);
  else el.setAttribute(name, value);
}

export function createCurrent(
  host: Pick<HistoryHost, "partial" | "settings">,
  listening: AddEventListenerOptions = {},
): void {
  const doc = document;
  const mark = (): void => {
    const { current, homes } = host.settings();
    const containers = (current ?? []).filter((selector) => {
      try {
        doc.querySelector(selector);
        return true;
      } catch {
        return false;
      }
    });
    if (containers.length === 0) return;
    const any = (suffix: string): string =>
      containers.map((container) => container + suffix).join();
    const base = new URL(host.partial.layers.urlFor(doc.body), location.href);
    const here: Here = {
      raw: base.pathname,
      query: base.search.slice(1),
      origin: location.origin,
      homes: homes ?? [],
    };
    for (const link of doc.querySelectorAll(any(" a"))) {
      if (link.closest(`a[data-next-current=off],${any("[data-next-current=off]")}`))
        continue;
      const owner = link.closest(`a[data-next-match],${any("[data-next-match]")}`);
      const modes = (owner?.getAttribute("data-next-match") ?? "")
        .toLowerCase()
        .split(/\s+/);
      const found = linkMark(
        link.getAttribute("href") ?? "",
        here,
        modes.includes("exact"),
        modes.includes("query"),
      );
      put(link, "data-next-current", found ?? null);
      put(link, "aria-current", found === "page" ? found : null);
    }
  };
  doc.addEventListener("partial:applied", mark, listening);
}
