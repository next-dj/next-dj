import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Envelope } from "./apply";
import type { LayerStack } from "./layers";
import { createMore } from "./more";

const PAGE_TWO = "/list/?q=1&page=2";

let controller: AbortController;

beforeEach(() => {
  controller = new AbortController();
  vi.useFakeTimers();
  history.replaceState(null, "", "/list/?q=1");
  document.body.innerHTML = "";
});

afterEach(() => {
  controller.abort();
  vi.useRealTimers();
});

function emit(type: string, detail: unknown): void {
  document.dispatchEvent(new CustomEvent(type, { detail }));
}

function requested(url: string, zone?: string): void {
  emit("partial:before-request", {
    url,
    method: "GET",
    intent: zone === undefined ? {} : { zone },
  });
}

function settle(zones: string[], status = "ok"): void {
  emit("partial:settled", { zones, boundaries: [], status });
}

function rect(height: number, bottom = 0): DOMRect {
  return { height, bottom } as DOMRect;
}

function harness() {
  const applied: unknown[] = [];
  const mounts: [string, (el: Element) => void][] = [];
  const layers = {
    resolveZone: (name: string, root: ParentNode) =>
      root.querySelector(`[data-next-zone="${name}"]`),
  } as unknown as LayerStack;
  const partial = {
    apply: (raw: unknown) => {
      applied.push(raw);
      return raw as Envelope;
    },
    layers,
    onMount: (selector: string, callback: (el: Element) => void) => {
      mounts.push([selector, callback]);
      return () => undefined;
    },
  };
  createMore({ partial }, { signal: controller.signal });
  return {
    applied,
    scan: () => {
      for (const [selector, callback] of mounts)
        for (const el of document.querySelectorAll(selector)) callback(el);
    },
  };
}

describe("{% more %} extras", () => {
  function feed(more: string, after = ""): HTMLElement {
    document.body.innerHTML =
      '<ul data-next-zone="list"><li>a</li><li>b</li></ul>' +
      '<div data-next-zone="list-more"><a href="/list/?q=1&page=2" ' +
      `data-next-target="list,list-more" data-next-merge="append" ${more}>More</a></div>` +
      after;
    return document.querySelector("ul")!;
  }

  function rows(): (string | null)[][] {
    return Array.from(document.querySelectorAll("li")).map((li) => [
      li.textContent,
      li.getAttribute("data-next-batch"),
    ]);
  }

  function append(...names: string[]): void {
    const html = names.map((name) => `<li>${name}</li>`).join("");
    document.querySelector("ul")!.insertAdjacentHTML("beforeend", html);
  }

  function landed(status = "ok", url = PAGE_TWO): void {
    requested(url, "list,list-more");
    if (status === "ok") append("c", "d");
    settle(["list", "list-more"], status);
  }

  const LESS =
    '<div data-next-zone="list-less"><a href="/list/?q=1&page=1&restore=1" ' +
    'data-next-target="list,list-less" data-next-merge="prepend">Less</a></div>';

  it("stamps the rows a list starts with as one batch", () => {
    feed('data-next-url="replace"');
    harness().scan();
    expect(rows()).toEqual([
      ["a", "1"],
      ["b", "1"],
    ]);
  });

  it("splits a restored window by its back tokens and prunes the oldest batch", () => {
    document.body.innerHTML =
      '<ul data-next-zone="list"><li>a</li><li>b</li><li>c</li><li>d</li><li>e</li>' +
      '<li>f</li></ul><div data-next-zone="list-more"><a href="/list/?cursor=WzAsNl0" ' +
      'data-next-param="cursor" data-next-target="list,list-more" ' +
      'data-next-back="WzEsNV0" data-next-backs="WzEsMl0 WzEsNF0" data-next-prune="7">' +
      'More</a></div><div data-next-zone="list-less"><a href="/list/?cursor=WzEsMF0" ' +
      'data-next-target="list,list-less">Less</a></div>';
    vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    harness().scan();
    expect(rows().map(([, batch]) => batch)).toEqual(["1", "1", "2", "2", "3", "3"]);
    requested("/list/?cursor=WzAsNl0", "list,list-more");
    append("g", "h");
    settle(["list", "list-more"]);
    expect(rows()).toEqual([
      ["c", "2"],
      ["d", "2"],
      ["e", "3"],
      ["f", "3"],
      ["g", "4"],
      ["h", "4"],
    ]);
    const less = document.querySelector('[data-next-zone="list-less"] a')!;
    expect(less.getAttribute("href")).toBe(`${location.origin}/list/?cursor=WzEsMl0`);
  });

  it("stamps the rows of a single window as one batch whatever its token", () => {
    document.body.innerHTML =
      '<ul data-next-zone="list"><li>a</li><li>b</li></ul>' +
      '<div data-next-zone="list-more"><a href="/list/?p=WzAsMl0" data-next-param="p" ' +
      'data-next-target="list,list-more" data-next-prune="9">More</a></div>';
    harness().scan();
    expect(rows()).toEqual([
      ["a", "1"],
      ["b", "1"],
    ]);
  });

  it("stamps nothing for a link that is not the more link of a present list", () => {
    document.body.innerHTML =
      '<ul data-next-zone="list"><li>a</li></ul>' +
      '<a href="/list/?page=1" data-next-target="list,list-less" data-next-url="push">x</a>' +
      '<a href="/list/?page=2" data-next-prune="5">x</a>' +
      '<a href="/list/?page=2" data-next-target="gone,gone-more" data-next-url="push">x</a>';
    harness().scan();
    expect(rows()).toEqual([["a", null]]);
  });

  it("stamps a landed batch and writes its token into the address", () => {
    feed('data-next-url="replace"');
    const h = harness();
    h.scan();
    landed();
    expect(rows()).toEqual([
      ["a", "1"],
      ["b", "1"],
      ["c", "2"],
      ["d", "2"],
    ]);
    expect(h.applied).toEqual([
      {
        version: "",
        ops: [
          {
            op: "url",
            href: "/list/?q=1&page=2&restore=1",
            action: "replace",
            quiet: true,
          },
        ],
      },
    ]);
  });

  it("pushes the opaque token under the parameter the link names", () => {
    history.replaceState(null, "", "/list/?q=1&p=WzAsMV0#top");
    document.body.innerHTML =
      '<ul data-next-zone="list"><li>a</li></ul>' +
      '<div data-next-zone="list-more"><a href="/list/?q=1&p=WzAsIjIwMjYtMTEiLDJd" ' +
      'data-next-param="p" data-next-target="list,list-more" data-next-url="push">' +
      "More</a></div>";
    const h = harness();
    h.scan();
    requested("/list/?q=1&p=WzAsIjIwMjYtMTEiLDJd", "list,list-more");
    settle(["list", "list-more"]);
    expect(h.applied).toEqual([
      {
        version: "",
        ops: [
          {
            op: "url",
            href: "/list/?q=1&p=WzAsIjIwMjYtMTEiLDJd&restore=1#top",
            action: "push",
            quiet: true,
          },
        ],
      },
    ]);
  });

  it("tracks no request whose address carries no token", () => {
    document.body.innerHTML =
      '<ul data-next-zone="list"><li>a</li></ul>' +
      '<div data-next-zone="list-more"><a href="/list/?q=1" ' +
      'data-next-target="list,list-more" data-next-url="replace">More</a></div>';
    const h = harness();
    h.scan();
    requested("/list/?q=1", "list,list-more");
    append("b");
    settle(["list", "list-more"]);
    expect(rows()).toEqual([
      ["a", "1"],
      ["b", null],
    ]);
    expect(h.applied).toEqual([]);
  });

  it("prunes whole batches from the top, keeps the viewport, and re-arms the top link", () => {
    const list = feed('data-next-prune="3" data-next-back="1"', LESS);
    const h = harness();
    h.scan();
    vi.spyOn(list, "getBoundingClientRect")
      .mockReturnValueOnce(rect(0, 500))
      .mockReturnValueOnce(rect(0, 440));
    const scroll = vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    const removed = vi.fn();
    list.firstElementChild!.addEventListener("next:removed", removed);
    landed();
    expect(rows()).toEqual([
      ["c", "2"],
      ["d", "2"],
    ]);
    expect(scroll).toHaveBeenCalledWith(0, -60);
    expect(removed).toHaveBeenCalledTimes(1);
    expect(h.applied).toEqual([]);
    const less = document.querySelector('[data-next-zone="list-less"] a')!;
    expect(less.getAttribute("href")).toBe(`${location.origin}/list/?q=1&page=1`);
  });

  it("shows the hidden top link of a list that started at its first window", () => {
    feed(
      'data-next-prune="3" data-next-back="1"',
      '<div data-next-zone="list-less"><div data-next-key="list-less">Start' +
        '<a href="/list/?q=1&page=1" data-next-key="list-less" ' +
        'data-next-target="list,list-less" data-next-merge="prepend" hidden>Less</a>' +
        "</div></div>",
    );
    vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    harness().scan();
    landed();
    const top = document.querySelector('[data-next-zone="list-less"]')!;
    const less = top.querySelector("a")!;
    expect(top.children).toHaveLength(1);
    expect(top.firstElementChild).toBe(less);
    expect(less.hidden).toBe(false);
    expect(less.getAttribute("href")).toBe(`${location.origin}/list/?q=1&page=1`);
  });

  it("prunes without a top link to re-arm", () => {
    feed('data-next-prune="3"');
    const scroll = vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    harness().scan();
    landed();
    expect(rows()).toEqual([
      ["c", "2"],
      ["d", "2"],
    ]);
    expect(scroll).toHaveBeenCalledTimes(1);
  });

  it("leaves the top link alone when the first kept batch has no back token", () => {
    feed('data-next-prune="3"', LESS);
    vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    harness().scan();
    landed();
    expect(rows()).toEqual([
      ["c", "2"],
      ["d", "2"],
    ]);
    const less = document.querySelector('[data-next-zone="list-less"] a')!;
    expect(less.getAttribute("href")).toBe("/list/?q=1&page=1&restore=1");
  });

  it("gives a landed batch its back token, not the one of the link it mounts", () => {
    feed('data-next-prune="3" data-next-back="1"', LESS);
    vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    const h = harness();
    h.scan();
    requested(PAGE_TWO, "list,list-more");
    append("c", "d");
    document.querySelector('[data-next-zone="list-more"]')!.innerHTML =
      '<a href="/list/?q=1&page=3" data-next-target="list,list-more" ' +
      'data-next-back="2" data-next-prune="3">More</a>';
    h.scan();
    settle(["list", "list-more"]);
    expect(rows()).toEqual([
      ["c", "2"],
      ["d", "2"],
    ]);
    const less = document.querySelector('[data-next-zone="list-less"] a')!;
    expect(less.getAttribute("href")).toBe(`${location.origin}/list/?q=1&page=1`);
  });

  it("re-arms the top link with the token that prepended the first kept batch", () => {
    document.body.innerHTML =
      '<div data-next-zone="list-less"><a href="/list/?cursor=up" ' +
      'data-next-param="cursor" data-next-target="list,list-less">Less</a></div>' +
      '<ul data-next-zone="list"><li>c</li><li>d</li></ul>' +
      '<div data-next-zone="list-more"><a href="/list/?cursor=down" ' +
      'data-next-param="cursor" data-next-target="list,list-more" ' +
      'data-next-back="mid" data-next-prune="4">More</a></div>';
    vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    harness().scan();
    requested("/list/?cursor=up", "list,list-less");
    document
      .querySelector("ul")!
      .insertAdjacentHTML("afterbegin", "<li>a</li><li>b</li>");
    settle(["list", "list-less"]);
    const less = document.querySelector('[data-next-zone="list-less"] a')!;
    less.setAttribute("href", "/list/?cursor=upper");
    requested("/list/?cursor=down", "list,list-more");
    append("e", "f");
    settle(["list", "list-more"]);
    expect(rows()).toEqual([
      ["c", "1"],
      ["d", "1"],
      ["e", "3"],
      ["f", "3"],
    ]);
    expect(less.getAttribute("href")).toBe(`${location.origin}/list/?cursor=up`);
  });

  it("prunes nothing within the limit or when one batch holds every row", () => {
    const scroll = vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    feed('data-next-prune="10"');
    harness().scan();
    landed();
    expect(rows()).toHaveLength(4);
    controller.abort();
    controller = new AbortController();
    document.body.innerHTML =
      '<ul data-next-zone="list"><li data-next-batch="2">a</li>' +
      '<li data-next-batch="2">b</li><li data-next-batch="2">c</li></ul>' +
      '<div data-next-zone="list-more">' +
      '<a href="/list/?page=3" data-next-target="list,list-more" data-next-prune="1">x</a>' +
      "</div>";
    harness().scan();
    requested("/list/?page=3", "list,list-more");
    settle(["list", "list-more"]);
    expect(rows()).toHaveLength(3);
    expect(scroll).not.toHaveBeenCalled();
  });

  it("re-fires a failed sentinel after one, two and four seconds, then stops", () => {
    feed('data-next-defer="visible" data-next-url="replace"');
    harness().scan();
    const clicks = vi.fn((event: Event) => event.preventDefault());
    document.querySelector("a")!.addEventListener("click", clicks);
    landed("error");
    vi.advanceTimersByTime(999);
    expect(clicks).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(clicks).toHaveBeenCalledTimes(1);
    landed("error");
    vi.advanceTimersByTime(2000);
    expect(clicks).toHaveBeenCalledTimes(2);
    landed("error");
    vi.advanceTimersByTime(3999);
    expect(clicks).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(1);
    expect(clicks).toHaveBeenCalledTimes(3);
    landed("error");
    vi.advanceTimersByTime(60_000);
    expect(clicks).toHaveBeenCalledTimes(3);
  });

  it("starts the backoff over after a page lands", () => {
    feed('data-next-defer="visible" data-next-url="replace"');
    harness().scan();
    const clicks = vi.fn((event: Event) => event.preventDefault());
    document.querySelector("a")!.addEventListener("click", clicks);
    landed("error");
    landed("error");
    landed();
    landed("error");
    vi.advanceTimersByTime(1000);
    expect(clicks).toHaveBeenCalledTimes(2);
  });

  it("re-fires neither a manual link nor one that left the page", () => {
    feed('data-next-url="replace"');
    harness().scan();
    const clicks = vi.fn((event: Event) => event.preventDefault());
    const link = document.querySelector("a")!;
    link.addEventListener("click", clicks);
    landed("error");
    link.setAttribute("data-next-defer", "visible");
    landed("error");
    link.remove();
    vi.advanceTimersByTime(60_000);
    expect(clicks).not.toHaveBeenCalled();
  });

  it("stamps the rows a top link prepends without touching the address", () => {
    document.body.innerHTML =
      '<ul data-next-zone="list"><li data-next-batch="7">c</li></ul>' +
      '<div data-next-zone="list-less"><a href="/list/?page=2" ' +
      'data-next-target="list,list-less" data-next-url="replace" data-next-prune="1">' +
      "Less</a></div>";
    const h = harness();
    h.scan();
    requested("/list/?page=2", "list,list-less");
    document.querySelector("ul")!.insertAdjacentHTML("afterbegin", "<li>b</li>");
    settle(["list", "list-less"]);
    expect(rows()).toEqual([
      ["b", "1"],
      ["c", "7"],
    ]);
    expect(h.applied).toEqual([]);
  });

  it("stamps the rows a top link prepends into a list that holds no batch", () => {
    document.body.innerHTML =
      '<ul data-next-zone="list"></ul><div data-next-zone="list-less">' +
      '<a href="/list/?page=2" data-next-target="list,list-less">Less</a></div>';
    harness();
    requested("/list/?page=2", "list,list-less");
    document.querySelector("ul")!.insertAdjacentHTML("afterbegin", "<li>b</li>");
    settle(["list", "list-less"]);
    expect(rows()).toEqual([["b", "1"]]);
  });

  it("leaves a dropped request and one for a list that is gone alone", () => {
    feed('data-next-url="replace"');
    const h = harness();
    h.scan();
    landed("dropped");
    append("c");
    expect(rows()[2]).toEqual(["c", null]);
    document.querySelector("ul")!.remove();
    requested(PAGE_TWO, "list,list-more");
    settle(["list", "list-more"]);
    expect(h.applied).toEqual([]);
  });

  it("stamps and prunes for a link that prunes without writing the address", () => {
    feed('data-next-prune="2"');
    vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    const h = harness();
    h.scan();
    expect(rows()[0]).toEqual(["a", "1"]);
    landed();
    expect(rows()).toEqual([
      ["c", "2"],
      ["d", "2"],
    ]);
    expect(h.applied).toEqual([]);
  });

  it("stops a pending re-fire when the runtime resets", () => {
    feed('data-next-defer="visible" data-next-url="replace"');
    harness().scan();
    const clicks = vi.fn((event: Event) => event.preventDefault());
    document.querySelector("a")!.addEventListener("click", clicks);
    landed("error");
    controller.abort();
    vi.advanceTimersByTime(60_000);
    expect(clicks).not.toHaveBeenCalled();
  });

  it("looks up no link for a request that names no sentinel", () => {
    feed('data-next-url="replace"');
    harness().scan();
    const query = vi.spyOn(document, "querySelector");
    requested(PAGE_TWO, "list,other");
    requested(PAGE_TWO, "list");
    expect(query).not.toHaveBeenCalled();
    query.mockRestore();
  });

  it("leaves a live filter that names the sentinel zone alone", () => {
    feed('data-next-prune="1" data-next-url="replace"');
    const scroll = vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    const h = harness();
    requested("/list/?q=2", "list,list-more");
    append("c");
    settle(["list", "list-more"]);
    expect(h.applied).toEqual([]);
    expect(rows()).toEqual([
      ["a", null],
      ["b", null],
      ["c", null],
    ]);
    expect(scroll).not.toHaveBeenCalled();
  });

  it("leaves a request at the link's own URL for other zones alone", () => {
    feed('data-next-prune="1" data-next-url="replace"');
    const h = harness();
    requested(PAGE_TWO, "list,list-more,count");
    append("c");
    settle(["list", "list-more", "count"]);
    expect(h.applied).toEqual([]);
    expect(rows()[2]).toEqual(["c", null]);
  });

  it("tracks only requests that name a sentinel zone holding a link", () => {
    document.body.innerHTML =
      '<ul data-next-zone="list"><li>a</li></ul>' +
      '<div data-next-zone="other"><a href="/list/?page=2" data-next-target="list,other" ' +
      'data-next-url="replace">x</a></div>';
    const h = harness();
    requested(PAGE_TWO);
    settle([]);
    requested(PAGE_TWO, "list,other");
    settle(["list", "other"]);
    requested(PAGE_TWO, "list");
    settle(["list"]);
    requested(PAGE_TWO, "list,list-more");
    settle(["list", "list-more"]);
    expect(h.applied).toEqual([]);
    expect(rows()).toEqual([["a", null]]);
  });
});
