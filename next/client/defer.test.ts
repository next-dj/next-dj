import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Applier } from "./apply";
import type { Applied, BeforeApply } from "./apply";
import { createDefer } from "./defer";
import type { DeferHost } from "./defer";
import type { Handlers } from "./kernel";
import type { LayerStack } from "./layers";
import type { Diagnostics } from "./protocol";
import { matching } from "./protocol";
import type { Sent } from "./triggers";
import type { Outcome, WireRequest } from "./wire";

const OK: Outcome = { status: "ok", applied: true };
const DROPPED: Outcome = { status: "dropped", applied: false };
const NETWORK: Outcome = {
  status: "error",
  applied: false,
  error: { kind: "network", error: new Error("offline") },
};
const FLAG = "next:partial:navigated";
const LOAD = 'data-next-defer="load"';
const MANUAL = 'data-next-defer="manual"';
const FETCH = "<button data-next-fetch>Load</button>";
const RETRY = "<button data-next-retry>Retry</button>";
const OTHER = '<input id="other">';

function failure(status: number): Outcome {
  return { status: "error", applied: false, error: { kind: "http", status, body: "" } };
}

let controller: AbortController;

beforeEach(() => {
  controller = new AbortController();
  vi.useFakeTimers();
  sessionStorage.clear();
  history.replaceState(null, "", "/list/?q=1");
  document.head.innerHTML = "";
  document.body.innerHTML = "";
});

afterEach(() => {
  controller.abort();
  vi.useRealTimers();
});

async function drain(): Promise<void> {
  for (let i = 0; i < 10; i++) await Promise.resolve();
}

function settle(zones: string[], status = "ok"): void {
  document.dispatchEvent(
    new CustomEvent("partial:settled", { detail: { zones, boundaries: [], status } }),
  );
}

function byId(id: string): HTMLElement {
  return document.getElementById(id)!;
}

function wrapper(
  id: string,
  attrs = "",
  body = `<p>loading ${id}</p>`,
  state = "idle",
): string {
  return (
    `<div id="${id}" data-next-boundary="${id}" data-next-boundary-key="k-${id}" ` +
    `data-next-token="tok-${id}" data-next-state="${state}" ${attrs}>${body}</div>`
  );
}

function ready(id: string, attrs = "", body = "<p>content</p> tail"): string {
  return (
    `<div id="${id}" data-next-boundary="${id}" data-next-boundary-key="k-${id}" ` +
    `data-next-state="ready" ${attrs}>${body}</div>`
  );
}

function incomplete(id: string, token?: string): string {
  const carried = token === undefined ? "" : `data-next-token="${token}"`;
  return (
    `<div id="${id}" data-next-boundary="${id}" ${carried} ` +
    'data-next-state="error" data-next-error="incomplete"></div>'
  );
}

function harness(
  answer: (request: WireRequest) => Outcome = () => OK,
  diagnostics?: Diagnostics,
) {
  const requests: WireRequest[] = [];
  const watched: [Element, () => Sent][] = [];
  const mounts: [string, (el: Element) => void][] = [];
  const reload = vi.fn();
  const served: Partial<Handlers> = {};
  const hooks = {
    before: [] as ((detail: BeforeApply) => void)[],
    applied: [] as ((applied: Applied) => void)[],
  };
  const layers = {
    urlFor: () => "/list/?q=1",
    resolveZone: (name: string, root: ParentNode) =>
      root.querySelector(`[data-next-zone="${name}"]`),
  } as unknown as LayerStack;
  const partial = {
    fetch: (request: WireRequest) => {
      requests.push(request);
      return Promise.resolve(answer(request));
    },
    layers,
    onMount: (selector: string, callback: (el: Element) => void) => {
      mounts.push([selector, callback]);
      return () => undefined;
    },
  } as unknown as DeferHost["partial"];
  createDefer(
    {
      partial,
      serve: (name, handler) => void Object.assign(served, { [name]: handler }),
      hook: (phase, _order, run) => void (hooks[phase] as unknown[]).push(run),
      watch: (target, send) => void watched.push([target, send]),
      diagnostics: () => diagnostics,
    },
    { signal: controller.signal },
    reload,
  );
  const applier = new Applier({
    dispatch: () => undefined,
    mergeContext: () => undefined,
    document,
    hooks,
  });
  return {
    partial,
    requests,
    watched,
    reload,
    applier,
    fetch: (target: Element | WireRequest) =>
      target instanceof Element ? served.fetch!(target) : partial.fetch(target),
    applied: (detail: Applied) => {
      for (const hook of hooks.applied) hook(detail);
    },
    scan: (root: ParentNode = document) => {
      for (const [selector, callback] of mounts)
        for (const el of matching(root, selector)) callback(el);
    },
  };
}

describe("client-trip triggers", () => {
  it("fetches each load wrapper in a request of its own once it mounts", async () => {
    document.body.innerHTML = wrapper("b-1", LOAD) + wrapper("b-2", LOAD);
    const h = harness();
    h.scan();
    expect(h.requests).toEqual([]);
    await drain();
    expect(h.requests).toHaveLength(2);
    expect(h.requests[0]).toEqual({
      url: "/list/?q=1",
      headers: { "X-Next-Zone": "b-1", "X-Next-Boundary": "tok-b-1" },
      queue: "b-1",
      boundaries: [byId("b-1")],
    });
    expect(h.requests[1]!.boundaries).toEqual([byId("b-2")]);
    h.scan();
    await drain();
    expect(h.requests).toHaveLength(2);
  });

  it("holds load wrappers until the document is ready", async () => {
    vi.spyOn(document, "readyState", "get").mockReturnValue("loading");
    document.body.innerHTML = wrapper("b-1", LOAD);
    const h = harness();
    h.scan();
    await drain();
    expect(h.requests).toEqual([]);
    document.dispatchEvent(new Event("DOMContentLoaded"));
    await drain();
    expect(h.requests).toHaveLength(1);
  });

  it.each(["documentElement", "body"] as const)(
    "fetches load wrappers at once on a page streamed with the mark on %s",
    async (root) => {
      vi.spyOn(document, "readyState", "get").mockReturnValue("loading");
      document[root].setAttribute("data-next-streamed", "");
      document.body.innerHTML = wrapper("b-1", LOAD);
      const h = harness();
      h.scan();
      await drain();
      document.body.insertAdjacentHTML("beforeend", wrapper("b-2", LOAD));
      h.scan();
      await drain();
      document.documentElement.removeAttribute("data-next-streamed");
      document.body.removeAttribute("data-next-streamed");
      expect(h.requests.map((request) => request.queue)).toEqual(["b-1", "b-2"]);
    },
  );

  it("registers only for idle wrappers, so one in another state is left alone", async () => {
    document.body.innerHTML = wrapper("b-1", LOAD, "", "error");
    const h = harness();
    h.scan();
    await drain();
    expect(h.requests).toEqual([]);
  });

  it("hands a visible wrapper to the observer and re-arms it only after a lost connection", async () => {
    document.body.innerHTML = wrapper("b-1", 'data-next-defer="visible"');
    const outcomes = [NETWORK, failure(404), OK];
    const h = harness(() => outcomes.shift()!);
    h.scan();
    expect(h.requests).toEqual([]);
    const [target, send] = h.watched[0]!;
    expect(target).toBe(byId("b-1"));
    await expect(send()).resolves.toEqual({ status: "error" });
    await expect(send()).resolves.toEqual({ status: "ok" });
    await expect(send()).resolves.toEqual({ status: "ok" });
    expect(h.requests).toHaveLength(3);
  });

  it("activates a wrapper again once a morph reuses its element for a new token", async () => {
    document.body.innerHTML =
      wrapper("b-1", 'data-next-defer="visible"') + wrapper("b-2", LOAD);
    const h = harness();
    h.scan();
    await drain();
    h.scan();
    await drain();
    expect(h.watched).toHaveLength(1);
    expect(h.requests).toHaveLength(1);
    const [seen, loaded] = [byId("b-1"), byId("b-2")];
    seen.id = "b-3";
    seen.setAttribute("data-next-token", "tok-b-3");
    loaded.setAttribute("data-next-token", "tok-b-4");
    h.scan();
    await drain();
    expect(h.watched.map(([target]) => target)).toEqual([seen, seen]);
    await h.watched[1]![1]();
    expect(h.requests.map((request) => request.headers)).toEqual([
      { "X-Next-Zone": "b-2", "X-Next-Boundary": "tok-b-2" },
      { "X-Next-Zone": "b-2", "X-Next-Boundary": "tok-b-4" },
      { "X-Next-Zone": "b-3", "X-Next-Boundary": "tok-b-3" },
    ]);
  });

  it("loads a manual wrapper from a fetch control inside its loading content", async () => {
    document.body.innerHTML = wrapper(
      "b-1",
      MANUAL,
      '<button data-next-fetch>Load</button><a href="/elsewhere/" data-next-fetch>Load</a>',
    );
    const h = harness();
    h.scan();
    await drain();
    expect(h.requests).toEqual([]);
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    document.querySelector("button")!.dispatchEvent(click);
    const follow = new MouseEvent("click", { bubbles: true, cancelable: true });
    document.querySelector("a")!.dispatchEvent(follow);
    await drain();
    expect([click.defaultPrevented, follow.defaultPrevented]).toEqual([true, true]);
    expect(h.requests.map((request) => request.queue)).toEqual(["b-1", "b-1"]);
  });

  it("ignores a fetch control outside a wrapper and clicks on anything else", async () => {
    document.body.innerHTML = "<button data-next-fetch>Load</button><p>text</p>";
    const h = harness();
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    document.querySelector("button")!.dispatchEvent(click);
    document.querySelector("p")!.click();
    document.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await drain();
    expect(click.defaultPrevented).toBe(false);
    expect(h.requests).toEqual([]);
  });

  it("ignores a click whose target is a text node", async () => {
    document.body.innerHTML = wrapper("b-1", MANUAL, FETCH);
    const h = harness();
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    document.querySelector("button")!.firstChild!.dispatchEvent(click);
    await drain();
    expect(click.defaultPrevented).toBe(false);
    expect(h.requests).toEqual([]);
  });

  it("routes an element passed to fetch and passes a request through", async () => {
    document.body.innerHTML =
      wrapper("b-1", MANUAL) +
      '<section data-next-zone="z"></section><p id="plain"></p>';
    const h = harness();
    await h.fetch(byId("b-1"));
    await h.fetch(document.querySelector("section")!);
    await expect(h.fetch(byId("plain"))).resolves.toEqual(DROPPED);
    await h.fetch({ url: "/other/", zone: "z" });
    const sent = h.requests.map((r) => [r.url, r.zone, r.queue]);
    expect(sent).toEqual([
      ["/list/?q=1", undefined, "b-1"],
      ["/list/?q=1", "z", undefined],
      ["/other/", "z", undefined],
    ]);
  });
});

describe("boundary tokens", () => {
  it.each(["query:_nb", "query"])(
    "puts the token in the query string for the %s carrier",
    async (carrier) => {
      document.body.innerHTML = wrapper("b-1", `data-next-carrier="${carrier}"`);
      const h = harness();
      await h.fetch(byId("b-1"));
      const [sent] = h.requests;
      expect(sent!.url).toBe("/list/?q=1&_nb=tok-b-1");
      expect(sent!.headers).toEqual({ "X-Next-Zone": "b-1" });
      expect(sent!.method).toBeUndefined();
    },
  );

  it("posts the token as a form field for the body carrier", async () => {
    document.body.innerHTML = wrapper("b-1", 'data-next-carrier="body:_nb"');
    const h = harness();
    await h.fetch(byId("b-1"));
    const [sent] = h.requests;
    expect(sent).toMatchObject({ url: "/list/?q=1", method: "POST", abortable: true });
    expect(sent!.headers).toEqual({ "X-Next-Zone": "b-1" });
    expect((sent!.body as URLSearchParams).toString()).toBe("_nb=tok-b-1");
  });

  it("reloads nothing on an expired token when the session store refuses the flag", async () => {
    vi.spyOn(
      Object.getPrototypeOf(sessionStorage) as Storage,
      "getItem",
    ).mockImplementation(() => {
      throw new Error("denied");
    });
    document.body.innerHTML = wrapper("b-1");
    const h = harness(() => failure(409));
    await h.fetch(byId("b-1"));
    expect(h.reload).not.toHaveBeenCalled();
    expect(h.requests).toHaveLength(1);
  });

  it.each([
    ["a posted token", "body:_nb", "/list/?q=1"],
    ["a query token", "query:_nb", "/list/?q=1&_nb=tok-b-1"],
  ])("reloads the page itself once on a 409 to %s", async (_name, carrier, url) => {
    document.body.innerHTML = wrapper("b-1", `data-next-carrier="${carrier}"`);
    const h = harness(() => failure(409));
    await h.fetch(byId("b-1"));
    await h.fetch(byId("b-1"));
    expect(h.requests[0]!.url).toBe(url);
    expect(h.reload).toHaveBeenCalledTimes(1);
    expect(location.pathname + location.search).toBe("/list/?q=1");
  });

  it("marks a refused boundary forbidden and leaves other failures to the core", async () => {
    document.body.innerHTML = wrapper("b-1") + wrapper("b-2");
    const outcomes = [failure(403), failure(500)];
    const h = harness(() => outcomes.shift()!);
    await h.fetch(byId("b-1"));
    await h.fetch(byId("b-2"));
    expect(byId("b-1").getAttribute("data-next-error")).toBe("forbidden");
    expect(byId("b-2").hasAttribute("data-next-error")).toBe(false);
    expect(h.reload).not.toHaveBeenCalled();
  });

  it("reloads the page once for a refused automatic load, then shows the refusal", async () => {
    document.body.innerHTML =
      wrapper("b-1", LOAD) + wrapper("b-2", 'data-next-defer="visible"');
    const h = harness(() => failure(403));
    h.scan();
    await drain();
    expect(h.reload).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(FLAG)).toBe("1");
    expect(byId("b-1").hasAttribute("data-next-error")).toBe(false);
    await h.watched[0]![1]();
    expect(h.reload).toHaveBeenCalledTimes(1);
    expect(byId("b-2").getAttribute("data-next-error")).toBe("forbidden");
  });

  it("shows a refused manual load or retry without reloading the page", async () => {
    document.body.innerHTML =
      wrapper("b-1", MANUAL, FETCH) + wrapper("b-2", "", RETRY, "error");
    const h = harness(() => failure(403));
    h.scan();
    document.querySelector<HTMLElement>("[data-next-fetch]")!.click();
    document.querySelector<HTMLElement>("[data-next-retry]")!.click();
    await drain();
    expect(h.requests).toHaveLength(2);
    expect(h.reload).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(FLAG)).toBeNull();
    for (const id of ["b-1", "b-2"])
      expect(byId(id).getAttribute("data-next-error")).toBe("forbidden");
  });

  it("stores the kind and the retry token an applied op carries on its wrapper", () => {
    document.body.innerHTML = wrapper("b-1") + wrapper("b-2") + wrapper("b-3");
    const h = harness();
    h.applied({
      envelope: {
        version: "",
        assets: [],
        form: null,
        ops: [
          {
            op: "morph",
            target: { boundary: "b-1" },
            s: "error",
            k: "timeout",
            token: "n1",
          },
          { op: "morph", target: { boundary: "b-2" }, s: "error", token: "n2" },
          { op: "morph", target: { boundary: "b-3" }, s: "ready" },
          { op: "morph", target: { zone: "z" } },
          { op: "morph", target: { boundary: "gone" }, token: "x" },
          { op: "url", href: "/" },
        ],
      },
      ok: true,
      nodes: [],
    } as unknown as Applied);
    const read = (id: string) =>
      ["data-next-error", "data-next-token"].map((name) => byId(id).getAttribute(name));
    expect(read("b-1")).toEqual(["timeout", "n1"]);
    expect(read("b-2")).toEqual(["error", "n2"]);
    expect(read("b-3")).toEqual([null, "tok-b-3"]);
  });

  it("marks the wrapper a reload morphed ready or failed by the state its op carries", () => {
    document.body.innerHTML = wrapper("b-1") + wrapper("b-2") + wrapper("b-3");
    const { applier } = harness();
    applier.apply({
      version: "",
      ops: [
        { op: "morph", target: { boundary: "b-1" }, html: "<p>one</p>", s: "ready" },
        {
          op: "morph",
          target: { boundary: "b-2" },
          html: "<p>failed</p>",
          s: "error",
          k: "timeout",
          token: "n2",
        },
        { op: "morph", target: { boundary: "b-3" }, html: "<p>three</p>" },
      ],
    });
    const read = (id: string) =>
      ["data-next-state", "data-next-error", "data-next-token"].map((name) =>
        byId(id).getAttribute(name),
      );
    expect(byId("b-1").innerHTML).toBe("<p>one</p>");
    expect(read("b-1")).toEqual(["ready", null, "tok-b-1"]);
    expect(read("b-2")).toEqual(["error", "timeout", "n2"]);
    expect(read("b-3")).toEqual(["idle", null, "tok-b-3"]);
  });

  it("finds an adopted root by its id once a reload changed its tag", () => {
    document.body.innerHTML =
      '<div id="b-1" data-next-boundary="b-1" data-next-adopted data-next-token="t" ' +
      'data-next-state="loading"><i>wait</i></div>';
    const { applier } = harness();
    applier.apply({
      version: "",
      ops: [
        {
          op: "morph",
          target: { boundary: "b-1" },
          html: '<article class="card">done</article>',
          s: "ready",
          token: "n1",
        },
      ],
    });
    const root = byId("b-1");
    expect(root.tagName).toBe("ARTICLE");
    expect(root.getAttribute("class")).toBe("card");
    expect(root.getAttribute("data-next-state")).toBe("ready");
    expect(root.getAttribute("data-next-token")).toBe("n1");
    expect(root.hasAttribute("data-next-adopted")).toBe(true);
  });

  it("carries the boundary attributes onto a morph alone, and only an element", () => {
    document.body.innerHTML =
      '<div id="b-1" data-next-boundary="b-1" data-next-adopted><i>wait</i></div>' +
      '<div id="b-2" data-next-boundary="b-2" data-next-adopted><i>wait</i></div>' +
      '<div id="b-3" class="kept" data-next-boundary="b-3" data-next-adopted></div>';
    const { applier } = harness();
    applier.apply({
      version: "",
      ops: [
        { op: "morph", target: { boundary: "b-1" }, html: "text only" },
        { op: "replace", target: { boundary: "b-2" }, html: "<p>swapped</p>" },
        { op: "morph", target: { boundary: "b-3" }, html: '<div class="own">x</div>' },
      ],
    });
    expect(byId("b-1").innerHTML).toBe("<i>wait</i>");
    expect(document.getElementById("b-2")).toBeNull();
    expect(document.querySelector("p")!.attributes).toHaveLength(0);
    expect(byId("b-3").getAttribute("class")).toBe("own");
  });

  it("keeps a boundary attribute the root names itself and passes a bare morph by", () => {
    document.body.innerHTML =
      '<div id="b-1" data-next-boundary="b-1" data-next-adopted data-next-state="loading">' +
      "<i>wait</i></div>" +
      '<div id="b-2" data-next-boundary="b-2" data-next-adopted><i>wait</i></div>';
    const { applier } = harness();
    applier.apply({
      version: "",
      ops: [
        {
          op: "morph",
          target: { boundary: "b-1" },
          html: '<div data-next-state="error">failed</div>',
        },
        { op: "morph", target: { boundary: "b-2" } },
      ],
    });
    expect(byId("b-1").getAttribute("data-next-state")).toBe("error");
    expect(byId("b-1").getAttribute("data-next-boundary")).toBe("b-1");
    expect(byId("b-2").innerHTML).toBe("<i>wait</i>");
  });
});

describe("a refused token", () => {
  const refused = (status: number, body: string) => (): Outcome => ({
    status: "error",
    applied: false,
    error: { kind: "http", status, body },
  });

  it("hands the reason a DEBUG server gives to the dev diagnostics", async () => {
    document.body.innerHTML = wrapper("b-1", MANUAL) + wrapper("b-2", MANUAL);
    const rejected = vi.fn<Diagnostics["rejected"]>();
    const dev = { rejected } as unknown as Diagnostics;
    const h = harness(refused(403, "refused\nthe token expired\n"), dev);
    await h.fetch(byId("b-1"));
    expect(rejected).toHaveBeenCalledWith(byId("b-1"), "refused\nthe token expired");
    const quiet = harness(refused(409, ""), dev);
    await quiet.fetch(byId("b-2"));
    expect(rejected).toHaveBeenCalledTimes(1);
  });

  it("explains nothing outside the dev channel", async () => {
    document.body.innerHTML = wrapper("b-1", MANUAL);
    const h = harness(refused(403, "refused\n"));
    await expect(h.fetch(byId("b-1"))).resolves.toMatchObject({ status: "error" });
    expect(byId("b-1").hasAttribute("data-next-error-detail")).toBe(false);
  });
});

describe("retry", () => {
  it("reloads the enclosing wrapper that carries a token", async () => {
    document.body.innerHTML =
      '<div data-next-zone="z">' + wrapper("b-1", "", RETRY, "error") + "</div>";
    const h = harness();
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    document.querySelector("button")!.dispatchEvent(click);
    await drain();
    expect(click.defaultPrevented).toBe(true);
    expect(h.requests.map((request) => request.boundaries)).toEqual([[byId("b-1")]]);
  });

  it("reloads the nearest zone when the wrapper has no token", async () => {
    const retrying = ready("b-1", "", RETRY);
    document.body.innerHTML = `<div data-next-zone="z">${retrying}</div>`;
    const h = harness();
    document.querySelector("button")!.click();
    await drain();
    expect(h.requests).toEqual([{ url: "/list/?q=1", zone: "z" }]);
  });

  it("reloads the page when nothing smaller can be addressed", async () => {
    document.body.innerHTML =
      ready("b-1", "", RETRY) + '<a href="/elsewhere/" data-next-retry>Retry</a>';
    const h = harness();
    document.querySelector("button")!.click();
    document.querySelector("a")!.click();
    await drain();
    expect(h.reload).toHaveBeenCalledTimes(2);
    expect(h.requests).toEqual([]);
  });
});

describe("focus after a swap", () => {
  it("moves focus to the wrapper when the reply removed the focused control", async () => {
    document.body.innerHTML = wrapper("b-1", MANUAL, FETCH) + OTHER;
    const h = harness(() => {
      byId("b-1").innerHTML = "<p>done</p>";
      return OK;
    });
    const button = document.querySelector("button")!;
    button.focus();
    button.click();
    await drain();
    expect(h.requests).toHaveLength(1);
    expect(document.activeElement).toBe(byId("b-1"));
    expect(byId("b-1").getAttribute("tabindex")).toBe("-1");
    byId("other").focus();
    expect(byId("b-1").hasAttribute("tabindex")).toBe(false);
  });

  it("keeps a tabindex the wrapper already had", async () => {
    document.body.innerHTML = wrapper("b-1", 'tabindex="0"', FETCH) + OTHER;
    harness(() => {
      byId("b-1").innerHTML = "<p>done</p>";
      return OK;
    });
    const button = document.querySelector("button")!;
    button.focus();
    button.click();
    await drain();
    expect(document.activeElement).toBe(byId("b-1"));
    byId("other").focus();
    expect(byId("b-1").getAttribute("tabindex")).toBe("0");
  });

  it("moves focus to a zone without an id", async () => {
    document.body.innerHTML = `<section data-next-zone="z">${RETRY}</section>`;
    const zone = document.querySelector("section")!;
    harness(() => {
      zone.innerHTML = "<p>done</p>";
      return OK;
    });
    const button = document.querySelector("button")!;
    button.focus();
    button.click();
    await drain();
    expect(document.activeElement).toBe(zone);
  });

  it("leaves focus that was outside the swapped element alone", async () => {
    document.body.innerHTML = wrapper("b-1", "", FETCH) + OTHER;
    harness(() => {
      byId("b-1").innerHTML = "<p>done</p>";
      return OK;
    });
    byId("other").focus();
    document.querySelector("button")!.click();
    await drain();
    expect(document.activeElement).toBe(byId("other"));
    expect(byId("b-1").hasAttribute("tabindex")).toBe(false);
  });
});

describe("incomplete boundaries", () => {
  it("re-requests each incomplete boundary once after a jittered delay", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    document.body.innerHTML =
      incomplete("b-1", "t1") +
      '<section data-next-zone="z">' +
      incomplete("b-2") +
      incomplete("b-3") +
      "</section>" +
      incomplete("b-4");
    const h = harness();
    vi.advanceTimersByTime(999);
    expect(h.requests).toEqual([]);
    vi.advanceTimersByTime(1);
    await drain();
    expect(h.requests.map((request) => request.queue ?? request.zone)).toEqual([
      "b-1",
      "z",
    ]);
    settle([]);
    window.dispatchEvent(new Event("load"));
    vi.advanceTimersByTime(2000);
    expect(h.requests).toHaveLength(2);
  });

  it("looks again after the window load and after each settled request", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const h = harness();
    document.body.innerHTML = incomplete("b-1", "t1");
    window.dispatchEvent(new Event("load"));
    expect(h.requests).toEqual([]);
    vi.advanceTimersByTime(0);
    vi.advanceTimersByTime(1);
    await drain();
    expect(h.requests).toHaveLength(1);
    document.body.insertAdjacentHTML("beforeend", incomplete("b-2", "t2"));
    settle(["z"]);
    vi.advanceTimersByTime(0);
    vi.advanceTimersByTime(1);
    await drain();
    expect(h.requests.map((request) => request.queue)).toEqual(["b-1", "b-2"]);
  });

  it.each([429, 503])("stops re-requesting after a %i", async (status) => {
    vi.spyOn(Math, "random").mockReturnValueOnce(0.1).mockReturnValueOnce(0.9);
    document.body.innerHTML = incomplete("b-1", "t1") + incomplete("b-2", "t2");
    const h = harness(() => failure(status));
    vi.advanceTimersByTime(200);
    await drain();
    vi.advanceTimersByTime(2000);
    expect(h.requests).toHaveLength(1);
    document.body.insertAdjacentHTML("beforeend", incomplete("b-3", "t3"));
    settle([]);
    vi.advanceTimersByTime(2000);
    expect(h.requests).toHaveLength(1);
  });

  it("keeps re-requesting after an answer that is no overload", async () => {
    vi.spyOn(Math, "random").mockReturnValueOnce(0.1).mockReturnValueOnce(0.9);
    document.body.innerHTML = incomplete("b-1", "t1") + incomplete("b-2", "t2");
    const h = harness(() => failure(404));
    vi.advanceTimersByTime(200);
    await drain();
    vi.advanceTimersByTime(2000);
    expect(h.requests).toHaveLength(2);
  });

  it("sends no pending re-request once the runtime resets", () => {
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    document.body.innerHTML = incomplete("b-1", "t1");
    const h = harness();
    controller.abort();
    vi.advanceTimersByTime(2000);
    expect(h.requests).toEqual([]);
  });
});
