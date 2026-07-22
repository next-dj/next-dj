import { beforeEach, describe, expect, it, vi } from "vitest";
import { Applier } from "./apply";
import type { ApplyDeps, AssetBridge, Asset, MountRegistry, ZoneFetch } from "./apply";

interface Dispatched {
  event: string;
  detail: Record<string, unknown>;
}

function makeApplier(over: Partial<ApplyDeps> = {}) {
  const dispatched: Dispatched[] = [];
  const applier = new Applier({
    dispatch: (event, detail) => dispatched.push({ event, detail }),
    mergeContext: () => undefined,
    document,
    ...over,
  });
  return { applier, dispatched };
}

function envelope(ops: unknown[], extra: Record<string, unknown> = {}): unknown {
  return { version: "v1", ops, assets: [], form: null, ...extra };
}

// A document whose location is overridden but whose DOM surface delegates to the
// real jsdom document, so the default #here reads our pathname and search while
// the apply pipeline keeps a working createElement and dispatchEvent.
function docAt(pathname: string, search: string): Document {
  return new Proxy(document, {
    get(target, prop, receiver) {
      if (prop === "location") return { pathname, search } as Location;
      const value = Reflect.get(target, prop, receiver);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}

describe("append and prepend dedup", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("appends children to the end of a zone", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li data-next-key="1">a</li></ul>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li data-next-key="2">b</li>',
        },
      ]),
    );
    const keys = Array.from(document.querySelectorAll("li")).map((li) =>
      li.getAttribute("data-next-key"),
    );
    expect(keys).toEqual(["1", "2"]);
  });

  it("replaces an existing row sharing the dedup key instead of duplicating", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li data-next-key="1">old</li></ul>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li data-next-key="1">new</li>',
        },
      ]),
    );
    const rows = document.querySelectorAll("li");
    expect(rows).toHaveLength(1);
    expect(rows[0]!.textContent).toBe("new");
  });

  it("falls back to id when no data-next-key is set", () => {
    document.body.innerHTML = '<ul data-next-zone="rows"><li id="r1">old</li></ul>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        { op: "append", target: { zone: "rows" }, html: '<li id="r1">new</li>' },
      ]),
    );
    expect(document.querySelectorAll("li")).toHaveLength(1);
    expect(document.querySelector("#r1")!.textContent).toBe("new");
  });

  it("prepend dedups by key, replacing the existing row", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li data-next-key="1">old</li></ul>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "prepend",
          target: { zone: "rows" },
          html: '<li data-next-key="1">new</li>',
        },
      ]),
    );
    expect(document.querySelectorAll("li")).toHaveLength(1);
    expect(document.querySelector("li")!.textContent).toBe("new");
  });

  it("appends a keyless row without matching", () => {
    document.body.innerHTML = '<ul data-next-zone="rows"><li>a</li></ul>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([{ op: "append", target: { zone: "rows" }, html: "<li>b</li>" }]),
    );
    expect(document.querySelectorAll("li")).toHaveLength(2);
  });

  it("prepends to the start", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li data-next-key="2">b</li></ul>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "prepend",
          target: { zone: "rows" },
          html: '<li data-next-key="1">a</li>',
        },
      ]),
    );
    const keys = Array.from(document.querySelectorAll("li")).map((li) =>
      li.getAttribute("data-next-key"),
    );
    expect(keys).toEqual(["1", "2"]);
  });

  it("prepends several roots in one html string in source order", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li data-next-key="3">c</li></ul>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "prepend",
          target: { zone: "rows" },
          html: '<li data-next-key="1">a</li><li data-next-key="2">b</li>',
        },
      ]),
    );
    const keys = Array.from(document.querySelectorAll("li")).map((li) =>
      li.getAttribute("data-next-key"),
    );
    expect(keys).toEqual(["1", "2", "3"]);
  });

  it("appends several roots in one html string in source order", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li data-next-key="1">a</li></ul>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li data-next-key="2">b</li><li data-next-key="3">c</li>',
        },
      ]),
    );
    const keys = Array.from(document.querySelectorAll("li")).map((li) =>
      li.getAttribute("data-next-key"),
    );
    expect(keys).toEqual(["1", "2", "3"]);
  });

  it("is a no-op when the merge target is absent from the document", () => {
    const { applier } = makeApplier();
    expect(() =>
      applier.apply(
        envelope([{ op: "append", target: { zone: "gone" }, html: "<li>x</li>" }]),
      ),
    ).not.toThrow();
  });

  it("replaces the first child holding the key when the container repeats it", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows">' +
      '<li data-next-key="1">first</li>' +
      '<li data-next-key="1">second</li>' +
      "</ul>";
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li data-next-key="1">new</li>',
        },
      ]),
    );
    const texts = Array.from(document.querySelectorAll("li")).map(
      (li) => li.textContent,
    );
    expect(texts).toEqual(["new", "second"]);
  });

  it("inserts both rows when one batch repeats a key absent from the container", () => {
    document.body.innerHTML = '<ul data-next-zone="rows"><li>keep</li></ul>';
    const { applier } = makeApplier();
    // The fresh rows sit in the fragment, invisible to the container index, so
    // neither matches the other and both land.
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li data-next-key="9">a</li><li data-next-key="9">b</li>',
        },
      ]),
    );
    const texts = Array.from(document.querySelectorAll("li")).map(
      (li) => li.textContent,
    );
    expect(texts).toEqual(["keep", "a", "b"]);
  });

  it("a later row with a replaced key replaces the row that just landed", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li data-next-key="1">old</li></ul>';
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li data-next-key="1">second</li><li data-next-key="1">third</li>',
        },
      ]),
    );
    const rows = document.querySelectorAll("li");
    expect(rows).toHaveLength(1);
    expect(rows[0]!.textContent).toBe("third");
  });

  it("prefers data-next-key over the id fallback", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li id="k" data-next-key="1">old</li></ul>';
    const { applier } = makeApplier();
    // The container row is indexed under its data-next-key, so an incoming row
    // whose id repeats that element's id is a fresh row, not a match.
    applier.apply(
      envelope([
        { op: "append", target: { zone: "rows" }, html: '<li id="k">by id</li>' },
      ]),
    );
    const texts = Array.from(document.querySelectorAll("li")).map(
      (li) => li.textContent,
    );
    expect(texts).toEqual(["old", "by id"]);
  });

  it("leaves keyless and foreign nodes of the container untouched", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows">' +
      "<li>keyless</li>" +
      '<li class="foreign">third-party</li>' +
      '<li data-next-key="1">old</li>' +
      "</ul>";
    const foreign = document.querySelector(".foreign")!;
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li data-next-key="1">new</li><li>fresh keyless</li>',
        },
      ]),
    );
    const texts = Array.from(document.querySelectorAll("li")).map(
      (li) => li.textContent,
    );
    expect(texts).toEqual(["keyless", "third-party", "new", "fresh keyless"]);
    expect(document.querySelector(".foreign")).toBe(foreign);
  });

  it("keeps focus and typed input in a row the merge does not touch", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows">' +
      '<li data-next-key="1"><input id="kept" /></li>' +
      '<li data-next-key="2"><input id="swapped" /></li>' +
      "</ul>";
    const kept = document.querySelector<HTMLInputElement>("#kept")!;
    const swapped = document.querySelector<HTMLInputElement>("#swapped")!;
    kept.value = "typed";
    swapped.value = "lost";
    kept.focus();
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li data-next-key="2"><input id="swapped" /></li>',
        },
      ]),
    );
    expect(document.querySelector<HTMLInputElement>("#kept")!.value).toBe("typed");
    expect(document.activeElement).toBe(kept);
    // A keyed match is a replace, not a morph, so the swapped row loses its
    // value exactly as it did before the index.
    expect(document.querySelector<HTMLInputElement>("#swapped")!.value).toBe("");
  });

  it("still lands a row whose match a next:removed listener detached", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows">' +
      '<li data-next-key="1">one</li>' +
      '<li data-next-key="2">two</li>' +
      "</ul>";
    const list = document.querySelector("ul")!;
    // next:removed is the documented island unmount hook, so a listener may
    // detach a sibling row while the merge is running.
    list.addEventListener("next:removed", () => {
      document.querySelector('[data-next-key="2"]')?.remove();
    });
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li data-next-key="1">A</li><li data-next-key="2">B</li>',
        },
      ]),
    );
    const texts = Array.from(document.querySelectorAll("li")).map(
      (li) => li.textContent,
    );
    expect(texts).toEqual(["A", "B"]);
  });

  it("replaces a keyed row a next:removed listener added, not duplicating it", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li data-next-key="1">one</li></ul>';
    const list = document.querySelector("ul")!;
    let added = false;
    // An island unmount hook that renders a placeholder row lands a key the
    // index snapshot cannot know about.
    list.addEventListener("next:removed", () => {
      if (added) return;
      added = true;
      const ghost = document.createElement("li");
      ghost.setAttribute("data-next-key", "2");
      ghost.textContent = "ghost";
      list.append(ghost);
    });
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li data-next-key="1">A</li><li data-next-key="2">B</li>',
        },
      ]),
    );
    const texts = Array.from(document.querySelectorAll("li")).map(
      (li) => li.textContent,
    );
    expect(texts).toEqual(["A", "B"]);
    expect(document.querySelectorAll('[data-next-key="2"]')).toHaveLength(1);
  });

  it("lands a row whose match the listener removed on its own next:removed", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows">' +
      '<li data-next-key="1">one</li>' +
      '<li data-next-key="2">two</li>' +
      "</ul>";
    const list = document.querySelector("ul")!;
    list.addEventListener("next:removed", (event) => {
      (event.target as Element).remove();
    });
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li data-next-key="1">A</li>',
        },
      ]),
    );
    // The match left the container before the replace, so the row lands at the
    // edge rather than disappearing into a no-op replaceWith.
    const texts = Array.from(document.querySelectorAll("li")).map(
      (li) => li.textContent,
    );
    expect(texts).toEqual(["two", "A"]);
  });

  it("treats a stale hit as a miss when a listener swaps a row one for one", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows">' +
      '<li data-next-key="1">one</li>' +
      '<li data-next-key="2">two</li>' +
      "</ul>";
    const list = document.querySelector("ul")!;
    let swapped = false;
    // A listener that drops one row and adds another leaves the child count
    // untouched, so only the detachment check catches the stale index entry.
    list.addEventListener("next:removed", () => {
      if (swapped) return;
      swapped = true;
      document.querySelector('[data-next-key="2"]')!.remove();
      const filler = document.createElement("li");
      filler.textContent = "filler";
      list.append(filler);
    });
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li data-next-key="1">A</li><li data-next-key="2">B</li>',
        },
      ]),
    );
    const texts = Array.from(document.querySelectorAll("li")).map(
      (li) => li.textContent,
    );
    expect(texts).toEqual(["A", "filler", "B"]);
  });

  it("does not duplicate a key the listener added while swapping a row one for one", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li id="a">a</li><li id="b">b</li></ul>';
    const list = document.querySelector("ul")!;
    let swapped = false;
    // Dropping one row and adding another leaves the child count untouched, so
    // the key the listener introduced is invisible to every count-based check.
    list.addEventListener("next:removed", () => {
      if (swapped) return;
      swapped = true;
      document.querySelector("#b")!.remove();
      const late = document.createElement("li");
      late.id = "c";
      late.textContent = "listener c";
      list.append(late);
    });
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li id="a">A</li><li id="c">C</li>',
        },
      ]),
    );
    const ids = Array.from(document.querySelectorAll("li")).map((li) => li.id);
    expect(ids).toEqual(["a", "c"]);
    expect(document.querySelector("#c")!.textContent).toBe("C");
  });

  it("replaces a key a listener added before the row that carries it was read", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li data-next-key="1">one</li></ul>';
    const list = document.querySelector("ul")!;
    let added = false;
    list.addEventListener("next:removed", () => {
      if (added) return;
      added = true;
      const ghost = document.createElement("li");
      ghost.setAttribute("data-next-key", "2");
      ghost.textContent = "ghost";
      list.append(ghost);
    });
    const { applier } = makeApplier();
    // The row carrying key 2 is read before any listener has run, so nothing
    // inside the loop can see the key the first fireRemoved is about to add.
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li data-next-key="2">B</li><li data-next-key="1">A</li>',
        },
      ]),
    );
    const texts = Array.from(document.querySelectorAll("li")).map(
      (li) => li.textContent,
    );
    expect(texts).toEqual(["A", "B"]);
    expect(document.querySelectorAll('[data-next-key="2"]')).toHaveLength(1);
  });

  it("lands both repeated rows when the listener removes the late match", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li data-next-key="1">one</li></ul>';
    const list = document.querySelector("ul")!;
    let ghost: Element | undefined;
    // The first unmount hook adds a keyed row, the next one takes that row back
    // out, so the deferred rows find a match that is gone by the time it is
    // replaced and have to stay whole.
    list.addEventListener("next:removed", (event) => {
      if (ghost === undefined) {
        ghost = document.createElement("li");
        ghost.setAttribute("data-next-key", "2");
        ghost.textContent = "ghost";
        list.append(ghost);
        return;
      }
      (event.target as Element).remove();
    });
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html:
            '<li data-next-key="1">A</li>' +
            '<li data-next-key="2">B</li>' +
            '<li data-next-key="2">B2</li>',
        },
      ]),
    );
    const texts = Array.from(document.querySelectorAll("li")).map(
      (li) => li.textContent,
    );
    expect(texts).toEqual(["A", "B", "B2"]);
  });

  it("aims the second row of a repeated key at the replacement, not the ghost", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li data-next-key="1">one</li></ul>';
    const list = document.querySelector("ul")!;
    let added = false;
    // The hook puts its keyed row ahead of the replacement, so a rebuilt index
    // would resolve key 1 to the ghost while the live index still points at the
    // row the merge just landed.
    list.addEventListener("next:removed", () => {
      if (added) return;
      added = true;
      const ghost = document.createElement("li");
      ghost.setAttribute("data-next-key", "1");
      ghost.textContent = "ghost";
      list.prepend(ghost);
    });
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html: '<li data-next-key="1">A</li><li data-next-key="1">B</li>',
        },
      ]),
    );
    const texts = Array.from(document.querySelectorAll("li")).map(
      (li) => li.textContent,
    );
    // B replaces A, the row of its own batch, and the hook's row is left where
    // the hook put it.
    expect(texts).toEqual(["ghost", "B"]);
  });

  it("cascades the deferred rows of one key through the reconcile pass", () => {
    document.body.innerHTML =
      '<ul data-next-zone="rows"><li data-next-key="a">one</li></ul>';
    const list = document.querySelector("ul")!;
    let added = false;
    list.addEventListener("next:removed", () => {
      if (added) return;
      added = true;
      const ghost = document.createElement("li");
      ghost.setAttribute("data-next-key", "b");
      ghost.textContent = "ghost";
      list.append(ghost);
    });
    const { applier } = makeApplier();
    applier.apply(
      envelope([
        {
          op: "append",
          target: { zone: "rows" },
          html:
            '<li data-next-key="a">A</li>' +
            '<li data-next-key="b">first b</li>' +
            '<li data-next-key="b">second b</li>',
        },
      ]),
    );
    const texts = Array.from(document.querySelectorAll("li")).map(
      (li) => li.textContent,
    );
    // Both deferred rows carry key b: the first takes the hook's row, the second
    // takes the first, the same cascade the pass inside the loop performs.
    expect(texts).toEqual(["A", "second b"]);
  });

  it("skips the container index for a batch that carries no keys", () => {
    const rows = Array.from(
      { length: 500 },
      (_, i) => `<li data-next-key="r${i}">row ${i}</li>`,
    ).join("");
    document.body.innerHTML = `<ul data-next-zone="rows">${rows}</ul>`;
    const { applier } = makeApplier();
    const spy = vi.spyOn(Element.prototype, "getAttribute");
    try {
      applier.apply(
        envelope([{ op: "append", target: { zone: "rows" }, html: "<li>fresh</li>" }]),
      );
      // A keyless row matches nothing, so the merge reads the key of the one
      // incoming row and never walks the 500 live children. Only the key reads
      // are counted: the zone selector itself walks the document in jsdom.
      const keyReads = spy.mock.calls.filter((call) => call[0] === "data-next-key");
      expect(keyReads).toHaveLength(1);
    } finally {
      spy.mockRestore();
    }
    expect(document.querySelectorAll("li")).toHaveLength(501);
  });

  it("reads each row's key a bounded number of times, not once per child", () => {
    const rows = Array.from(
      { length: 500 },
      (_, i) => `<li data-next-key="r${i}">row ${i}</li>`,
    ).join("");
    document.body.innerHTML = `<ul data-next-zone="rows">${rows}</ul>`;
    // The batch matches the tail of the list, the worst case for a scan that
    // walks the children from the front for every incoming row.
    const html = Array.from(
      { length: 100 },
      (_, i) => `<li data-next-key="r${400 + i}">fresh ${i}</li>`,
    ).join("");
    const { applier } = makeApplier();
    const spy = vi.spyOn(Element.prototype, "getAttribute");
    // A spy left on the prototype would poison every later test, so it comes off
    // even if the apply throws.
    try {
      applier.apply(envelope([{ op: "append", target: { zone: "rows" }, html }]));
      // A scan per incoming row would cost 500 * 100 reads. The constant is
      // loose on purpose: the assertion is linearity, not a jsdom call count,
      // and the fragment parse and the removal sweep read attributes too.
      expect(spy.mock.calls.length).toBeLessThan(10 * (500 + 100));
    } finally {
      spy.mockRestore();
    }
    // A merge that never ran would also read nothing, so the count alone proves
    // no work: the batch has to have landed on the keyed rows it addressed.
    const rowEls = document.querySelectorAll("li");
    expect(rowEls).toHaveLength(500);
    expect(rowEls[400]!.textContent).toBe("fresh 0");
    expect(rowEls[499]!.textContent).toBe("fresh 99");
  });
});

describe("refresh verb", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("refresh re-GETs the named zone", () => {
    const refresh = vi.fn<ZoneFetch>();
    const { applier } = makeApplier({ refresh, here: () => "/page/" });
    applier.apply(envelope([{ op: "refresh", zone: "feed" }]));
    expect(refresh).toHaveBeenCalledWith({
      url: "/page/",
      zone: "feed",
      headers: { "X-Next-Zone": "feed" },
    });
  });

  it("refresh derives the zone from the target when no top-level zone is set", () => {
    const refresh = vi.fn<ZoneFetch>();
    const { applier } = makeApplier({ refresh, here: () => "/page/" });
    applier.apply(envelope([{ op: "refresh", target: { zone: "feed" } }]));
    expect(refresh).toHaveBeenCalledWith({
      url: "/page/",
      zone: "feed",
      headers: { "X-Next-Zone": "feed" },
    });
  });

  it("refresh without any zone is a no-op", () => {
    const refresh = vi.fn<ZoneFetch>();
    const { applier } = makeApplier({ refresh });
    applier.apply(envelope([{ op: "refresh" }]));
    expect(refresh).not.toHaveBeenCalled();
  });

  it("refresh targets the zone's owning page, not the address bar", () => {
    document.body.innerHTML = '<div data-next-zone="feed">x</div>';
    const refresh = vi.fn<ZoneFetch>();
    const el = document.querySelector('[data-next-zone="feed"]')!;
    // A modal holds the address bar (here), so a base-page zone must refresh
    // against the page the layer stack says owns it, not the modal route.
    const layers = {
      resolveZone: () => el,
      resolveSelector: () => null,
      urlFor: () => "/owner/",
      open: () => undefined,
      close: () => undefined,
      toast: () => undefined,
    };
    const { applier } = makeApplier({ refresh, layers, here: () => "/modal/" });
    applier.apply(envelope([{ op: "refresh", zone: "feed" }]));
    expect(refresh).toHaveBeenCalledWith({
      url: "/owner/",
      zone: "feed",
      headers: { "X-Next-Zone": "feed" },
    });
  });

  it("refresh falls back to the current URL when the zone is absent", () => {
    const refresh = vi.fn<ZoneFetch>();
    const layers = {
      resolveZone: () => null,
      resolveSelector: () => null,
      urlFor: () => "/owner/",
      open: () => undefined,
      close: () => undefined,
      toast: () => undefined,
    };
    const { applier } = makeApplier({ refresh, layers, here: () => "/page/" });
    applier.apply(envelope([{ op: "refresh", zone: "gone" }]));
    expect(refresh).toHaveBeenCalledWith({
      url: "/page/",
      zone: "gone",
      headers: { "X-Next-Zone": "gone" },
    });
  });

  it("default here keeps the query of the current URL on a re-GET", () => {
    const refresh = vi.fn<ZoneFetch>();
    const doc = docAt("/feed", "?q=foo");
    const { applier } = makeApplier({ refresh, document: doc });
    applier.apply(envelope([{ op: "refresh", zone: "feed" }]));
    expect(refresh).toHaveBeenCalledWith({
      url: "/feed?q=foo",
      zone: "feed",
      headers: { "X-Next-Zone": "feed" },
    });
  });

  it("default here is the bare path when there is no query", () => {
    const refresh = vi.fn<ZoneFetch>();
    const doc = docAt("/feed", "");
    const { applier } = makeApplier({ refresh, document: doc });
    applier.apply(envelope([{ op: "refresh", zone: "feed" }]));
    expect(refresh).toHaveBeenCalledWith({
      url: "/feed",
      zone: "feed",
      headers: { "X-Next-Zone": "feed" },
    });
  });
});

describe("mount and generation", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("fires next:mounted on the touched node and runs the mount registry", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const ran: string[] = [];
    const mount: MountRegistry = { run: (root) => ran.push((root as Element).tagName) };
    const zone = document.querySelector('[data-next-zone="z"]')!;
    const seen: string[] = [];
    zone.addEventListener("next:mounted", () => seen.push("mounted"));
    const { applier } = makeApplier({ mount });
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { zone: "z" },
          html: '<div data-next-zone="z">new</div>',
        },
      ]),
    );
    expect(seen).toEqual(["mounted"]);
    expect(ran).toEqual(["DIV"]);
  });

  it("skips next:mounted on a touched node a later op detached", () => {
    document.body.innerHTML = '<ul data-next-zone="z"></ul>';
    const ran: Element[] = [];
    const mount: MountRegistry = { run: (root) => ran.push(root as Element) };
    const { applier } = makeApplier({ mount });
    // The append marks the new row as touched, then the remove detaches it, so
    // the mount pass sees a disconnected node and skips it.
    applier.apply(
      envelope([
        { op: "append", target: { zone: "z" }, html: '<li id="row">x</li>' },
        { op: "remove", target: { css: "#row" } },
      ]),
    );
    expect(document.querySelector("#row")).toBeNull();
    expect(ran.some((node) => node.id === "row")).toBe(false);
  });

  it("replace mounts every root element, not only the first", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const ran: string[] = [];
    const mount: MountRegistry = { run: (root) => ran.push((root as Element).id) };
    const { applier } = makeApplier({ mount });
    applier.apply(
      envelope([
        {
          op: "replace",
          target: { zone: "z" },
          html: '<p id="a">a</p><p id="b">b</p>',
        },
      ]),
    );
    expect(ran).toEqual(["a", "b"]);
    expect(document.querySelectorAll("p")).toHaveLength(2);
  });

  it("morph mounts the new subtree when the root tag changes", () => {
    document.body.innerHTML = '<div data-next-zone="z"><span>old</span></div>';
    const ran: string[] = [];
    const mount: MountRegistry = { run: (root) => ran.push((root as Element).tagName) };
    const { applier } = makeApplier({ mount });
    // A root-tag change recreates the node, so the mount pass must see the live
    // replacement, not the detached original a stale mark would carry.
    applier.apply(
      envelope([
        {
          op: "morph",
          target: { zone: "z" },
          html: '<section data-next-zone="z"><b>new</b></section>',
        },
      ]),
    );
    expect(ran).toEqual(["SECTION"]);
    expect(document.querySelector("section")!.textContent).toBe("new");
  });

  it("bumps the per-zone generation on each apply", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const { applier } = makeApplier();
    expect(applier.generation("z")).toBe(0);
    applier.apply(envelope([{ op: "inner", target: { zone: "z" }, html: "one" }]));
    applier.apply(envelope([{ op: "inner", target: { zone: "z" }, html: "two" }]));
    expect(applier.generation("z")).toBe(2);
  });
});

describe("asset bridge pipeline", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("gates ops behind the css load and runs js after", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const order: string[] = [];
    const assets: AssetBridge = {
      loadCss: (_m: Asset[], done) => {
        order.push("css");
        done();
      },
      loadJs: () => order.push("js"),
      versionMismatch: () => false,
      acceptVersion: () => undefined,
    };
    const { applier } = makeApplier({ assets });
    applier.apply(envelope([{ op: "inner", target: { zone: "z" }, html: "new" }]));
    expect(order).toEqual(["css", "js"]);
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("new");
  });

  it("skips the apply on a version mismatch", () => {
    document.body.innerHTML = '<div data-next-zone="z">old</div>';
    const assets: AssetBridge = {
      loadCss: (_m: Asset[], done) => done(),
      loadJs: () => undefined,
      versionMismatch: () => true,
      acceptVersion: () => undefined,
    };
    const { applier } = makeApplier({ assets });
    applier.apply(envelope([{ op: "inner", target: { zone: "z" }, html: "new" }]));
    expect(document.querySelector('[data-next-zone="z"]')!.textContent).toBe("old");
  });
});
