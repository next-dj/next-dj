import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createCurrent, linkMark, normalPath } from "./current";
import type { Here } from "./current";
import { createPartial } from "./partial";
import type { PartialSurface } from "./partial";
import type { Settings } from "./router";
import { chunkModules } from "./test-doubles";

interface Options {
  page?: string;
  router?: Settings;
}

const ORIGIN = "https://example.com";
const HERE = "/docs/intro/";
const PAGE = "page";
const ANCESTOR = "ancestor";
const NONE = undefined;

const SETTINGS: Settings = {
  current: ["nav", "[role=navigation]", "[data-next-nav]"],
  homes: ["/"],
};

let listening: AbortController;
let surface: PartialSurface;

beforeEach(() => {
  listening = new AbortController();
  history.replaceState(null, "", "/");
  surface = createPartial({
    ...chunkModules,
    dispatch: () => undefined,
    mergeContext: () => undefined,
  });
});

afterEach(() => {
  listening.abort();
  surface._reset();
  document.body.innerHTML = "";
});

function here(path: string, query = "", homes: string[] = []): Here {
  return {
    raw: path.startsWith("/") ? path : `/${path}`,
    query,
    origin: ORIGIN,
    homes,
  };
}

function setup(options: Options = {}): void {
  document.body.innerHTML = options.page ?? "";
  const settings = options.router ?? SETTINGS;
  createCurrent(
    { partial: surface, settings: () => settings },
    { signal: listening.signal },
  );
}

function applied(): void {
  surface.apply({ version: "", ops: [] });
}

describe("the path rule matches the server", () => {
  it.each([
    ["/", "/"],
    ["/docs/", "/docs"],
    ["/docs", "/docs"],
    ["/docs/./intro/../", "/docs"],
    ["/../..", "/"],
    ["/blog/caf%c3%a9/", "/blog/caf%C3%A9"],
    ["/blog/café/", "/blog/caf%C3%A9"],
    ["/a%3Db%3Bc/", "/a=b;c"],
    ["/a=b;c", "/a=b;c"],
    ["/a%20b", "/a%20b"],
    ["/files/%ff/", "/files/%FF"],
    ["/x/%zz", "/x/%25zz"],
    ["/%2F%7e", "/%2F~"],
  ])("normalises %s to %s", (location, expected) => {
    expect(normalPath(location)).toBe(expected);
  });

  it.each([
    ["/docs/intro/", HERE, ORIGIN, PAGE],
    ["/docs/intro", HERE, ORIGIN, PAGE],
    ["/docs", HERE, ORIGIN, ANCESTOR],
    ["/docs/", HERE, ORIGIN, ANCESTOR],
    ["/", HERE, ORIGIN, NONE],
    ["/blog", HERE, ORIGIN, NONE],
    ["/docs/intro/more", HERE, ORIGIN, NONE],
    ["/doc", HERE, ORIGIN, NONE],
    ["/docs/intr", HERE, ORIGIN, NONE],
    ["intro", HERE, ORIGIN, NONE],
    ["./", HERE, ORIGIN, PAGE],
    ["../", HERE, ORIGIN, ANCESTOR],
    ["../../", HERE, ORIGIN, NONE],
    ["../../../..", HERE, ORIGIN, NONE],
    ["./more/../", HERE, ORIGIN, PAGE],
    ["?", HERE, ORIGIN, PAGE],
    ["#top", HERE, ORIGIN, NONE],
    ["", HERE, ORIGIN, NONE],
    ["  /docs/intro  ", HERE, ORIGIN, PAGE],
    ["/do\ncs/intro", HERE, ORIGIN, PAGE],
    ["/docs/%69ntro", HERE, ORIGIN, PAGE],
    ["/docs/%2e/intro", HERE, ORIGIN, PAGE],
    ["/docs/%2E%2E/docs/intro", HERE, ORIGIN, PAGE],
    ["/Docs/intro", HERE, ORIGIN, NONE],
    ["/docs/intro?tab=a#f", HERE, ORIGIN, PAGE],
    ["/docs/intro#f", HERE, ORIGIN, PAGE],
    ["https://example.com/docs/intro", HERE, ORIGIN, PAGE],
    ["HTTPS://Example.COM/docs", HERE, ORIGIN, ANCESTOR],
    ["https://example.com", HERE, ORIGIN, NONE],
    ["https://other.com/docs/intro", HERE, ORIGIN, NONE],
    ["http://example.com/docs/intro", HERE, ORIGIN, NONE],
    ["//example.com/docs/", HERE, ORIGIN, ANCESTOR],
    ["//other.com/docs/intro", HERE, ORIGIN, NONE],
    ["\\\\example.com\\docs", HERE, ORIGIN, ANCESTOR],
    ["https:/docs/intro", HERE, ORIGIN, NONE],
    ["mailto:someone@example.com", HERE, ORIGIN, NONE],
    ["javascript:void(0)", HERE, ORIGIN, NONE],
    ["https://example.com/docs/intro", HERE, "", NONE],
    ["//example.com/docs", HERE, "", NONE],
    ["intro", "/docs/intro", ORIGIN, PAGE],
    ["./", "/docs/intro", ORIGIN, ANCESTOR],
    ["other", "/docs/intro", ORIGIN, NONE],
    ["/", "/", ORIGIN, PAGE],
    ["", "/", ORIGIN, NONE],
    ["/docs", "/", ORIGIN, NONE],
    ["?", "/", ORIGIN, PAGE],
    ["/docs", "docs/intro", ORIGIN, ANCESTOR],
    ["/docs", "", ORIGIN, NONE],
    ["/", "", ORIGIN, PAGE],
    ["/%D0%BF%D1%80%D0%B8%D0%B2%D0%B5%D1%82", "/привет/мир/", ORIGIN, ANCESTOR],
    [
      "/%d0%bf%d1%80%d0%b8%d0%b2%d0%b5%d1%82/%D0%BC%D0%B8%D1%80",
      "/привет/мир/",
      ORIGIN,
      PAGE,
    ],
    ["/a b", "/a%20b/", ORIGIN, PAGE],
    ["/files/%ff", "/files/%FF/", ORIGIN, PAGE],
  ])("marks %j at %j on %j as %s", (href, path, origin, expected) => {
    expect(linkMark(href, { ...here(path), origin }, false, false)).toBe(expected);
  });

  it.each([
    ["/docs/intro?tab=a", "tab=a", PAGE],
    ["/docs/intro?tab=a", "tab=b", NONE],
    ["/docs/intro?tab=a", "", NONE],
    ["/docs/intro?tab=a", "tab=a&x=1", PAGE],
    ["/docs/intro?tab=a&tab=b", "tab=b&tab=a", PAGE],
    ["/docs/intro?tab=a&tab=b", "tab=a", NONE],
    ["/docs/intro", "tab=a", PAGE],
    ["/docs/intro?a=1&b=2", "b=2&a=1", PAGE],
    ["/docs/intro?flag", "flag=", PAGE],
    ["/docs/intro?a=", "", NONE],
    ["/docs?tab=a", "tab=a", ANCESTOR],
    ["/docs?tab=a", "tab=b", NONE],
  ])("in the query mode marks %j on ?%s as %s", (href, query, expected) => {
    expect(linkMark(href, here(HERE, query), false, true)).toBe(expected);
  });

  it.each([
    ["?page=2", "page=2", PAGE],
    ["?page=2", "page=3", NONE],
    ["?page=2", "", NONE],
    ["?", "page=3", PAGE],
  ])("compares the query of %j on ?%s in every mode as %s", (href, query, expected) => {
    expect(linkMark(href, here(HERE, query), false, false)).toBe(expected);
  });

  it("drops the ancestor mark in the exact mode", () => {
    expect(linkMark("/docs", here(HERE), true, false)).toBe(NONE);
    expect(linkMark("/docs/intro", here(HERE), true, false)).toBe(PAGE);
    expect(linkMark("/docs?tab=a", here(HERE, "tab=a"), true, true)).toBe(NONE);
  });

  it.each([
    ["/docs/intro", "/docs/intro", [], PAGE],
    ["/", "/", [], PAGE],
    ["/de", "/de", [], PAGE],
    ["/", "/docs", [], NONE],
    ["/doc", "/docs/intro", [], NONE],
    ["/docs/intro/more", "/docs/intro", [], NONE],
    ["/docs", "/docs/intro", [], ANCESTOR],
    ["/de", "/de/blog", ["/de"], NONE],
    ["/de/", "/de/blog/", ["/", "/de", "/en", "/app"], NONE],
    ["/de", "/de/blog/", ["/", "/de", "/en", "/app"], NONE],
    ["/de/", "/de/", ["/", "/de", "/en", "/app"], PAGE],
    ["/de/blog/", "/de/blog/post/", ["/", "/de", "/en", "/app"], ANCESTOR],
    ["/en/", "/de/blog/", ["/", "/de", "/en", "/app"], NONE],
    ["/app/", "/app/docs/", ["/", "/de", "/en", "/app"], NONE],
    ["/app/docs/", "/app/docs/intro/", ["/", "/de", "/en", "/app"], ANCESTOR],
  ])("marks %j at %j with the homes %j as %s", (href, path, homes, expected) => {
    expect(linkMark(href, here(path, "", homes), false, false)).toBe(expected);
  });
});

describe("the active-link pass", () => {
  it("marks the links of every container after an apply", () => {
    history.replaceState(null, "", "/docs/intro/?tab=a");
    setup({
      router: { ...SETTINGS, current: ["nav", "[data-x=1a]", "[data-next-nav]"] },
      page:
        '<nav><a id="page" href="/docs/intro">x</a><a id="up" href="/docs/">x</a>' +
        '<a id="root" href="/">x</a>' +
        '<a id="stale" href="/blog/" aria-current="page" ' +
        'data-next-current="page">x</a>' +
        '<a id="bare" aria-current="page">x</a>' +
        '<a id="off" href="/blog/" aria-current="step" data-next-current="off">x</a>' +
        '<a id="query" href="/docs/intro?tab=b" data-next-match="query">x</a></nav>' +
        '<div data-next-nav data-next-match="exact"><a id="exact" href="/docs/">x</a>' +
        '<a id="own" href="/docs/" data-next-match="">x</a></div>' +
        '<nav data-next-current="off"><a id="muted" href="/docs/intro">x</a></nav>' +
        '<p><a id="content" href="/docs/intro">x</a></p>',
    });
    applied();
    applied();
    const mark = (id: string): (string | null)[] => {
      const link = document.getElementById(id)!;
      return [
        link.getAttribute("data-next-current"),
        link.getAttribute("aria-current"),
      ];
    };
    expect(mark("page")).toEqual(["page", "page"]);
    expect(mark("up")).toEqual(["ancestor", null]);
    expect(mark("root")).toEqual([null, null]);
    expect(mark("stale")).toEqual([null, null]);
    expect(mark("bare")).toEqual([null, null]);
    expect(mark("off")).toEqual(["off", "step"]);
    expect(mark("query")).toEqual([null, null]);
    expect(mark("exact")).toEqual([null, null]);
    expect(mark("own")).toEqual(["ancestor", null]);
    expect(mark("muted")).toEqual([null, null]);
    expect(mark("content")).toEqual([null, null]);
  });

  it("uses the path of the base page while a layer is open", () => {
    setup({ page: '<nav><a id="l" href="/docs/">x</a></nav>' });
    history.replaceState(null, "", "/docs/item/");
    vi.spyOn(surface.layers, "urlFor").mockReturnValue("/docs/");
    applied();
    expect(document.getElementById("l")!.getAttribute("aria-current")).toBe("page");
  });

  it.each([
    ["the pass is off", { ...SETTINGS, current: null }],
    ["no container is named", { ...SETTINGS, current: [] }],
    ["no selector parses", { ...SETTINGS, current: ["nav["] }],
  ])("marks nothing when %s", (_name, router) => {
    setup({ router, page: '<nav><a id="l" href="/">x</a></nav>' });
    applied();
    expect(document.getElementById("l")!.hasAttribute("aria-current")).toBe(false);
  });

  it("treats every link as a possible ancestor without the homes", () => {
    history.replaceState(null, "", "/de/blog/");
    setup({
      router: { ...SETTINGS, homes: null },
      page: '<nav><a id="l" href="/de/">x</a></nav>',
    });
    applied();
    expect(document.getElementById("l")!.getAttribute("data-next-current")).toBe(
      "ancestor",
    );
  });
});
