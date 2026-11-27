// The route table of the pages scope: which URLs a page of the server answers, decided
// from the URLconf rows the server lists, so a plain Django view is never fetched.

import { ROUTE_PAGE } from "./protocol";

/** The table as the server encodes it, each row its mark and then its regex. */
export interface Wire {
  base: string;
  slash: boolean;
  routes: string[];
}

export interface Table {
  base: string;
  slash: boolean;
  rows: [page: boolean, regex: RegExp][];
}

// A longer path is never matched, so no row regex reads an unbounded href.
const LONGEST = 512;
// Python and JavaScript read `.` and `$` apart around line breaks, so a path holding a
// control character or a line separator is never a page.
const APART = /[\p{Cc}\u2028\u2029]/u;

export const readTable = (wire: Wire): Table => ({
  ...wire,
  rows: wire.routes.map((row) => [
    row.startsWith(ROUTE_PAGE),
    new RegExp(`^${row.slice(1)}`),
  ]),
});

/**
 * Whether a page answers the URL, the first matching row deciding as the URLconf would.
 *
 * Until the table loads no URL is a page.
 */
export function isPage(table: Table | undefined, url: URL): boolean {
  let path: string;
  try {
    path = decodeURIComponent(url.pathname);
  } catch {
    return false;
  }
  if (
    !table ||
    path.length > LONGEST ||
    APART.test(path) ||
    !path.startsWith(table.base)
  )
    return false;
  path = path.slice(table.base.length);
  const row = (at: string) => table.rows.find((found) => found[1].test(at));
  const found =
    row(path) ?? (table.slash && !path.endsWith("/") ? row(`${path}/`) : undefined);
  return found?.[0] === true;
}
