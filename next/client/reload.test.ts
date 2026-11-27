import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LayerStack } from "./layers";
import type { BeforeApply } from "./apply";
import { createReload } from "./reload";
import type { ReloadHost } from "./reload";

let controller: AbortController;
let fallbacks: WeakMap<Element, string>;

beforeEach(() => {
  controller = new AbortController();
  fallbacks = new WeakMap();
  vi.useFakeTimers();
  document.head.innerHTML = "";
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

function stashed(name: string, html: string): string {
  return `<template data-next-loading="${name}">${html}</template>`;
}

function rect(height: number): DOMRect {
  return { height } as DOMRect;
}

function byId(id: string): HTMLElement {
  return document.getElementById(id)!;
}

function wrapper(id: string, body: string, state: string): string {
  return (
    `<div id="${id}" data-next-boundary="${id}" data-next-boundary-key="k-${id}" ` +
    `data-next-token="tok-${id}" data-next-state="${state}">${body}</div>`
  );
}

function ready(id: string, attrs = "", body = "<p>content</p> tail"): string {
  return (
    `<div id="${id}" data-next-boundary="${id}" data-next-boundary-key="k-${id}" ` +
    `data-next-state="ready" ${attrs}>${body}</div>`
  );
}

function row(id: string): string {
  return (
    `<tr id="${id}" data-next-boundary="${id}" data-next-boundary-key="k-${id}" ` +
    'data-next-state="ready" data-next-adopted><td>row</td></tr>'
  );
}

function harness() {
  const resolveZone = vi.fn((name: string, root: ParentNode) =>
    root.querySelector(`[data-next-zone="${name}"]`),
  );
  const layers = { resolveZone } as unknown as LayerStack;
  const onMount = (selector: string, callback: (el: Element) => void) => {
    for (const el of document.querySelectorAll(selector)) callback(el);
    return () => undefined;
  };
  const before: ((detail: BeforeApply) => void)[] = [];
  createReload(
    {
      partial: { layers, onMount } as unknown as ReloadHost["partial"],
      fallbacks,
      hook: (_phase, _order, run) =>
        void before.push(run as (detail: BeforeApply) => void),
    },
    { signal: controller.signal },
  );
  const capture = (boundaries: Element[]): void => {
    for (const run of before) run({ boundaries } as unknown as BeforeApply);
  };
  return { resolveZone, capture };
}

describe("a zone reload showing loading content", () => {
  function reloading(inner: string, attrs = ""): HTMLElement {
    document.body.innerHTML =
      `<section data-next-zone="z" data-next-reload="loading" ${attrs}>` +
      `${inner}</section>`;
    return document.querySelector("section")!;
  }

  function live(id: string, html = "<i>wait</i>"): void {
    fallbacks.set(byId(id), html);
  }

  it("swaps each ready boundary to its loading content once the delay passes", () => {
    reloading(ready("b-1"));
    live("b-1");
    vi.spyOn(byId("b-1"), "getBoundingClientRect").mockReturnValue(rect(40));
    harness();
    requested("/list/", "z");
    vi.advanceTimersByTime(149);
    expect(byId("b-1").innerHTML).toBe("<p>content</p> tail");
    vi.advanceTimersByTime(1);
    expect(byId("b-1").innerHTML).toBe("<i>wait</i>");
    expect(byId("b-1").style.minHeight).toBe("40px");
  });

  it("reads every height before it swaps the first boundary", () => {
    reloading(ready("b-1") + ready("b-2") + ready("b-3"));
    const order: string[] = [];
    for (const id of ["b-1", "b-2", "b-3"]) {
      live(id);
      const el = byId(id);
      vi.spyOn(el, "getBoundingClientRect").mockImplementation(() => {
        order.push(`read ${id}`);
        return rect(10);
      });
      const style = el.style;
      vi.spyOn(style, "minHeight", "set").mockImplementation(() => {
        order.push(`write ${id}`);
      });
    }
    harness();
    requested("/list/", "z");
    vi.advanceTimersByTime(150);
    expect(order).toEqual([
      "read b-1",
      "read b-2",
      "read b-3",
      "write b-1",
      "write b-2",
      "write b-3",
    ]);
  });

  it("puts the content back when the reload fails", () => {
    reloading(ready("b-1"));
    live("b-1");
    const content = byId("b-1").firstChild;
    harness();
    requested("/list/", "z");
    vi.advanceTimersByTime(150);
    settle(["z"], "error");
    expect(byId("b-1").firstChild).toBe(content);
    expect(byId("b-1").innerHTML).toBe("<p>content</p> tail");
    expect(byId("b-1").style.minHeight).toBe("");
  });

  it("lets the replaced content go once the reply swapped the boundary", () => {
    reloading(ready("b-1"));
    live("b-1");
    const removed = vi.fn();
    byId("b-1").firstElementChild!.addEventListener("next:removed", removed);
    harness();
    requested("/list/", "z");
    vi.advanceTimersByTime(150);
    byId("b-1").innerHTML = "<p>fresh</p>";
    settle(["z"]);
    expect(byId("b-1").innerHTML).toBe("<p>fresh</p>");
    expect(removed).toHaveBeenCalledTimes(1);
  });

  it("shows nothing when the reply lands before the delay", () => {
    reloading(ready("b-1"));
    live("b-1");
    harness();
    requested("/list/", "z");
    settle(["z"]);
    vi.advanceTimersByTime(1000);
    expect(byId("b-1").innerHTML).toBe("<p>content</p> tail");
  });

  it("reads the delay from the zone", () => {
    reloading(ready("b-1"), 'data-next-delay="1s"');
    live("b-1");
    harness();
    requested("/list/", "z");
    vi.advanceTimersByTime(999);
    expect(byId("b-1").innerHTML).toBe("<p>content</p> tail");
    vi.advanceTimersByTime(1);
    expect(byId("b-1").innerHTML).toBe("<i>wait</i>");
  });

  it("keeps the loading content while a successor request holds the zone", () => {
    const zone = reloading(ready("b-1"));
    live("b-1");
    harness();
    requested("/list/", "z");
    requested("/list/", "z");
    zone.setAttribute("data-next-busy", "");
    vi.advanceTimersByTime(150);
    settle(["z"], "dropped");
    expect(byId("b-1").innerHTML).toBe("<i>wait</i>");
    zone.removeAttribute("data-next-busy");
    settle(["z"], "error");
    expect(byId("b-1").innerHTML).toBe("<p>content</p> tail");
  });

  it("shows the zone skeleton from its stash and restores the zone on failure", () => {
    const zone = reloading(ready("b-1"));
    document.head.innerHTML = stashed("z", "<b>zone skeleton</b>");
    vi.spyOn(zone, "getBoundingClientRect").mockReturnValue(rect(90));
    harness();
    requested("/list/", "z");
    vi.advanceTimersByTime(150);
    expect(zone.innerHTML).toBe("<b>zone skeleton</b>");
    expect(zone.style.minHeight).toBe("90px");
    settle(["z"], "error");
    expect(zone.firstElementChild).toBe(byId("b-1"));
  });

  it("falls back to the boundaries when the zone holds the focus", () => {
    const zone = reloading(ready("b-1") + '<input id="search">');
    document.head.innerHTML = stashed("z", "<b>skeleton</b>");
    live("b-1");
    byId("search").focus();
    harness();
    requested("/list/", "z");
    vi.advanceTimersByTime(150);
    expect(zone.querySelector("b")).toBeNull();
    expect(byId("b-1").innerHTML).toBe("<i>wait</i>");
  });

  it("never replaces a boundary holding the focus or a field the user changed", () => {
    reloading(
      ready("b-focus", "", "<button>go</button>") +
        ready("b-text", "", '<input value="a">') +
        ready("b-check", "", '<input type="checkbox">') +
        ready("b-select", "", "<select><option>a</option><option>b</option></select>") +
        ready(
          "b-clean",
          "",
          '<input value="a"><input type="radio"><select><option>a</option></select>',
        ),
    );
    for (const id of ["b-focus", "b-text", "b-check", "b-select", "b-clean"]) live(id);
    document.querySelector("button")!.focus();
    byId("b-text").querySelector("input")!.value = "typed";
    byId("b-check").querySelector("input")!.checked = true;
    byId("b-select").querySelector("select")!.value = "b";
    harness();
    requested("/list/", "z");
    vi.advanceTimersByTime(150);
    const swapped = ["b-focus", "b-text", "b-check", "b-select", "b-clean"].filter(
      (id) => byId(id).innerHTML === "<i>wait</i>",
    );
    expect(swapped).toEqual(["b-clean"]);
  });

  it("holds no height when the zone opts out", () => {
    reloading(ready("b-1"), 'data-next-reserve="off"');
    live("b-1");
    vi.spyOn(byId("b-1"), "getBoundingClientRect").mockReturnValue(rect(40));
    harness();
    requested("/list/", "z");
    vi.advanceTimersByTime(150);
    expect(byId("b-1").innerHTML).toBe("<i>wait</i>");
    expect(byId("b-1").style.minHeight).toBe("");
  });

  it("skips boundaries without loading content and nested ones", () => {
    reloading(ready("b-bare") + ready("b-outer", "", ready("b-inner")));
    live("b-outer", "<i>outer</i>");
    live("b-inner");
    harness();
    requested("/list/", "z");
    vi.advanceTimersByTime(150);
    expect(byId("b-bare").innerHTML).toBe("<p>content</p> tail");
    expect(byId("b-outer").innerHTML).toBe("<i>outer</i>");
    expect(document.getElementById("b-inner")).toBeNull();
  });

  it("shows the loading content of an adopted root inside the root it keeps", () => {
    const rows = ["b-live", "b-stash", "b-text", "b-none"];
    reloading(`<table><tbody>${rows.map(row).join("")}</tbody></table>`);
    live("b-live", "<td>live</td>");
    document.head.innerHTML =
      stashed("k-b-stash", "<tr><td>skeleton</td></tr>") + stashed("k-b-text", "wait");
    harness();
    requested("/list/", "z");
    vi.advanceTimersByTime(150);
    expect(rows.map((id) => byId(id).innerHTML)).toEqual([
      "<td>live</td>",
      "<td>skeleton</td>",
      "<td>row</td>",
      "<td>row</td>",
    ]);
  });

  it("reads loading content captured before the first swap or kept in a stash", () => {
    reloading(
      wrapper("b-trip", "<i>trip</i>", "idle") +
        ready("b-stash") +
        '<div id="b-keyless" data-next-boundary="b-keyless" data-next-state="loading">' +
        "<i>stream</i></div>",
    );
    document.head.innerHTML = stashed("k-b-stash", "<i>stash</i>");
    harness().capture([byId("b-trip"), byId("b-keyless")]);
    for (const id of ["b-trip", "b-keyless"]) {
      byId(id).setAttribute("data-next-state", "ready");
      byId(id).innerHTML = "<p>done</p>";
    }
    requested("/list/", "z");
    vi.advanceTimersByTime(150);
    expect(["b-trip", "b-stash", "b-keyless"].map((id) => byId(id).innerHTML)).toEqual([
      "<i>trip</i>",
      "<i>stash</i>",
      "<i>stream</i>",
    ]);
  });

  it("captures the first loading content a reply is about to replace, never the rest", () => {
    reloading(
      wrapper("b-1", "<i>first</i>", "loading") +
        ready("b-2", "", "<p>ready</p>") +
        ready("b-3", "data-next-adopted") +
        ready("b-4"),
    );
    live("b-4", "<i>streamed</i>");
    const { capture } = harness();
    const boundaries = ["b-1", "b-2", "b-3", "b-4"].map(byId);
    capture(boundaries);
    byId("b-1").innerHTML = "<i>second</i>";
    capture(boundaries);
    byId("b-1").setAttribute("data-next-state", "ready");
    byId("b-1").innerHTML = "<p>done</p>";
    fallbacks.delete(byId("b-4"));
    requested("/list/", "z");
    vi.advanceTimersByTime(150);
    expect(["b-1", "b-2", "b-3", "b-4"].map((id) => byId(id).innerHTML)).toEqual([
      "<i>first</i>",
      "<p>ready</p>",
      "<p>content</p> tail",
      "<i>streamed</i>",
    ]);
  });

  it("keeps the loading copies of the latest hundred boundaries", () => {
    const ids = Array.from({ length: 101 }, (_, index) => `b-${index}`);
    reloading(ids.map((id) => wrapper(id, `<i>${id}</i>`, "loading")).join(""));
    harness().capture(ids.map(byId));
    for (const id of ids) {
      byId(id).setAttribute("data-next-state", "ready");
      byId(id).innerHTML = "<p>done</p>";
    }
    requested("/list/", "z");
    vi.advanceTimersByTime(150);
    expect(byId("b-0").innerHTML).toBe("<p>done</p>");
    expect(byId("b-1").innerHTML).toBe("<i>b-1</i>");
    expect(byId("b-100").innerHTML).toBe("<i>b-100</i>");
  });

  it("reads the zone names of a request trimmed", () => {
    reloading(ready("b-1"));
    live("b-1");
    harness();
    requested("/list/", "other , z ");
    vi.advanceTimersByTime(150);
    expect(byId("b-1").innerHTML).toBe("<i>wait</i>");
  });

  it("starts no delay for a mounted zone that has since left the page", () => {
    reloading(ready("b-1"));
    const { resolveZone } = harness();
    document.body.innerHTML = "";
    requested("/list/", "z");
    expect(resolveZone).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("resolves only the zones mounted with loading reloads", () => {
    document.body.innerHTML = '<section data-next-zone="plain"></section>';
    const { resolveZone } = harness();
    requested("/list/", "plain,list-more");
    expect(resolveZone).not.toHaveBeenCalled();
  });

  it("stops the pending delays when the runtime resets", () => {
    reloading(ready("b-1"));
    live("b-1");
    harness();
    requested("/list/", "z");
    controller.abort();
    vi.advanceTimersByTime(1000);
    expect(byId("b-1").innerHTML).toBe("<p>content</p> tail");
  });

  it("swaps a boundary whose select has no option selected by default", () => {
    reloading(
      ready("b-1", "", "<select><option>a</option><option>b</option></select>"),
    );
    live("b-1");
    harness();
    requested("/list/", "z");
    vi.advanceTimersByTime(150);
    expect(byId("b-1").innerHTML).toBe("<i>wait</i>");
  });

  it("ignores requests for no zone, an unknown zone, or a zone that keeps content", () => {
    const keep = '<section data-next-zone="keep" data-next-reload="keep">';
    document.body.innerHTML = keep + ready("b-1") + "</section>";
    live("b-1");
    harness();
    requested("/list/");
    requested("/list/", "missing");
    requested("/list/", "keep");
    vi.advanceTimersByTime(1000);
    settle(["keep"]);
    expect(byId("b-1").innerHTML).toBe("<p>content</p> tail");
  });
});

describe("a layer waiting for its answer", () => {
  const SKELETON = '<p class="bone">loading</p>';

  function page(markup = `<template data-next-layer="sheet">${SKELETON}</template>`): {
    opener: HTMLElement;
    root: HTMLElement;
  } {
    document.body.innerHTML =
      `${markup}<a id="o" href="/m/" data-next-layer="sheet">M</a>` +
      '<dialog data-next-dialog><div id="r" data-next-zone="sheet" ' +
      'data-next-busy aria-busy="true"></div></dialog>';
    harness();
    return { opener: byId("o"), root: byId("r") };
  }

  function opened(opener: Element | null, root: Element): void {
    emit("partial:layer-opened", { opener, root });
  }

  it("shows the loading UI of its zone once the delay passes", () => {
    const { opener, root } = page();
    opened(opener, root);
    vi.advanceTimersByTime(149);
    expect(root.innerHTML).toBe("");
    vi.advanceTimersByTime(1);
    expect(root.innerHTML).toBe(SKELETON);
    expect(root.getAttribute("aria-busy")).toBe("true");
  });

  it("waits the delay the opener names", () => {
    const { opener, root } = page();
    opener.setAttribute("data-next-delay", "400ms");
    opened(opener, root);
    vi.advanceTimersByTime(399);
    expect(root.innerHTML).toBe("");
    vi.advanceTimersByTime(1);
    expect(root.innerHTML).toBe(SKELETON);
  });

  it("shows it for a layer the server opens, after the default delay", () => {
    const { root } = page();
    opened(null, root);
    vi.advanceTimersByTime(150);
    expect(root.innerHTML).toBe(SKELETON);
  });

  it("never flashes it once a fast answer filled or released the root", () => {
    const { opener, root } = page();
    opened(opener, root);
    root.innerHTML = "<form>body</form>";
    vi.advanceTimersByTime(150);
    expect(root.innerHTML).toBe("<form>body</form>");
    root.innerHTML = "";
    root.removeAttribute("data-next-busy");
    opened(opener, root);
    vi.advanceTimersByTime(150);
    expect(root.innerHTML).toBe("");
  });

  it("leaves the answer that replaced it in place", () => {
    const { opener, root } = page();
    opened(opener, root);
    vi.advanceTimersByTime(150);
    root.innerHTML = "<form>body</form>";
    settle(["sheet"]);
    expect(root.innerHTML).toBe("<form>body</form>");
  });

  it("empties the root again when the answer fails", () => {
    const { opener, root } = page();
    opened(opener, root);
    vi.advanceTimersByTime(150);
    settle(["sheet"], "error");
    expect(root.innerHTML).toBe("");
  });

  it("shows nothing once the answer settles before the delay", () => {
    const { opener, root } = page();
    opened(opener, root);
    settle(["sheet"], "error");
    vi.advanceTimersByTime(150);
    expect(root.innerHTML).toBe("");
  });

  it("keeps the empty busy root of a zone without a loading UI", () => {
    const { opener, root } = page("");
    opened(opener, root);
    vi.advanceTimersByTime(150);
    expect(root.innerHTML).toBe("");
    settle(["sheet"]);
    expect(root.innerHTML).toBe("");
  });

  it("reads no reload stash of the same name", () => {
    const { opener, root } = page(stashed("sheet", "<p>reload</p>"));
    opened(opener, root);
    vi.advanceTimersByTime(150);
    expect(root.innerHTML).toBe("");
  });

  it("clears its timers when the runtime is torn down", () => {
    const { opener, root } = page();
    opened(opener, root);
    controller.abort();
    vi.advanceTimersByTime(150);
    expect(root.innerHTML).toBe("");
  });
});

describe("an empty layer", () => {
  it("shows no loading UI for a root that names no zone", () => {
    document.body.innerHTML =
      '<template data-next-layer="">x</template><div id="r" data-next-busy></div>';
    harness();
    emit("partial:layer-opened", { opener: null, root: byId("r") });
    vi.advanceTimersByTime(150);
    expect(byId("r").innerHTML).toBe("");
  });
});
