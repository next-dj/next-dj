import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { isPage, readTable } from "./routes";
import type { Wire } from "./routes";

interface Case {
  href: string;
  owns: boolean;
  page: boolean;
  document?: boolean;
}

// The table and the cases the server suite pins, so both sides decide one fixture.
const GOLDEN = JSON.parse(
  readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), "../../tests/urls/golden/scope.json"),
    "utf-8",
  ),
) as { table: Wire; cases: Case[] };

const at = (href: string): URL => new URL(href, location.origin);

describe("the golden route table", () => {
  const table = readTable(GOLDEN.table);

  it.each(GOLDEN.cases.map((found) => [found.href, found.owns] as const))(
    "decides %s as the server does",
    (href, owns) => {
      expect(isPage(table, at(href))).toBe(owns);
    },
  );

  it("owns every URL a page of the URLconf answers but the ones it loads as documents", () => {
    const pages = GOLDEN.cases.filter((found) => found.page && !found.document);
    expect(pages.length).toBeGreaterThan(0);
    for (const found of pages) expect(isPage(table, at(found.href))).toBe(true);
  });
});

describe("the route decision", () => {
  const wire: Wire = { base: "/", slash: false, routes: ["-a/b/$", "+a/[^/]+/$"] };

  it("owns nothing before the table loads", () => {
    expect(isPage(undefined, at("/a/x/"))).toBe(false);
  });

  it("lets the first matching row decide", () => {
    const table = readTable(wire);
    expect(isPage(table, at("/a/b/"))).toBe(false);
    expect(isPage(table, at("/a/c/"))).toBe(true);
  });

  it("adds no slash while the server redirects none", () => {
    expect(isPage(readTable(wire), at("/a/c"))).toBe(false);
  });

  it("reads the query and the fragment as no part of the route", () => {
    expect(isPage(readTable(wire), at("/a/c/?page=2#top"))).toBe(true);
  });

  it("refuses a path outside the script prefix", () => {
    const table = readTable({ ...wire, base: "/app/" });
    expect(isPage(table, at("/a/c/"))).toBe(false);
    expect(isPage(table, at("/app/a/c/"))).toBe(true);
  });

  it("refuses a path that does not decode", () => {
    expect(isPage(readTable(wire), at("/a/%E0%A4%A/"))).toBe(false);
  });

  it("never matches a path past the longest it reads", () => {
    const table = readTable({ ...wire, routes: ["+.+$"] });
    expect(isPage(table, at(`/${"a".repeat(511)}`))).toBe(true);
    expect(isPage(table, at(`/${"a".repeat(512)}`))).toBe(false);
  });

  it.each(["%00", "%0A", "%0D", "%1F", "%7F", "%C2%85", "%E2%80%A8", "%E2%80%A9"])(
    "refuses a path holding the control character %s",
    (char) => {
      const table = readTable({ ...wire, routes: ["+.+$"] });
      expect(isPage(table, at(`/a${char}b`))).toBe(false);
      expect(isPage(table, at("/a%20b"))).toBe(true);
    },
  );
});
