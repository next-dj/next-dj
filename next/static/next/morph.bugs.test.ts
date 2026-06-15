import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { morph } from "./morph";

function mount(html: string): Element {
  document.body.innerHTML = html;
  return document.body.firstElementChild!;
}

// A registered custom element whose lifecycle callbacks are spies, so a test can
// assert the engine treats it atomically or replaces it whole.
const lifecycle = {
  connected: vi.fn(),
  disconnected: vi.fn(),
  attributeChanged: vi.fn(),
};

class TrackedWidget extends HTMLElement {
  static observedAttributes = ["v"];
  connectedCallback() {
    lifecycle.connected();
  }
  disconnectedCallback() {
    lifecycle.disconnected();
  }
  attributeChangedCallback() {
    lifecycle.attributeChanged();
  }
}

if (!customElements.get("tracked-widget")) {
  customElements.define("tracked-widget", TrackedWidget);
}

beforeEach(() => {
  document.body.innerHTML = "";
  lifecycle.connected.mockClear();
  lifecycle.disconnected.mockClear();
  lifecycle.attributeChanged.mockClear();
});

afterEach(() => {
  document.body.innerHTML = "";
});

interface BugCase {
  name: string;
  before: string;
  after: string;
  setup?: (target: Element) => void;
  // A field carrying user input is dirty by construction, the registry stamps it
  // on the keystroke, so a case that types into a field marks it dirty here.
  dirty?: (target: Element) => (el: Element) => boolean;
  verify: (result: Element, target: Element) => void;
}

const bugTable: BugCase[] = [
  {
    name: "#27 morphing an identical form never touches the input",
    before: '<form id="f"><input id="i" name="i" value="server"></form>',
    after: '<form id="f"><input id="i" name="i" value="server"></form>',
    setup: (target) => {
      const input = target.querySelector<HTMLInputElement>("#i")!;
      input.value = "typed";
    },
    dirty: (target) => {
      const input = target.querySelector<HTMLInputElement>("#i")!;
      return (el) => el === input;
    },
    verify: (result) => {
      const input = result.querySelector<HTMLInputElement>("#i")!;
      expect(input.value).toBe("typed");
    },
  },
  {
    name: "#57 a matched custom element is atomic",
    before:
      '<div id="r"><tracked-widget id="w" v="1"><b>old</b></tracked-widget></div>',
    after: '<div id="r"><tracked-widget id="w" v="2"><i>new</i></tracked-widget></div>',
    setup: () => {
      // The connect from mount counts as a baseline, cleared before the morph.
      lifecycle.connected.mockClear();
      lifecycle.disconnected.mockClear();
    },
    verify: (result, target) => {
      const widget = result.querySelector("#w")!;
      const child = (target as Element).querySelector("#w")!.firstElementChild;
      expect(widget.getAttribute("v")).toBe("2");
      expect(widget.firstElementChild).toBe(child);
      expect(child!.tagName).toBe("B");
      expect(lifecycle.connected).not.toHaveBeenCalled();
      expect(lifecycle.disconnected).not.toHaveBeenCalled();
    },
  },
  {
    name: "#57 a structural mismatch replaces the custom element honestly",
    before: '<div id="r"><tracked-widget id="w"></tracked-widget></div>',
    after: '<div id="r"><section id="w">plain</section></div>',
    setup: () => {
      lifecycle.connected.mockClear();
      lifecycle.disconnected.mockClear();
    },
    verify: (result) => {
      expect(result.querySelector("tracked-widget")).toBeNull();
      expect(result.querySelector("section")!.textContent).toBe("plain");
      expect(lifecycle.disconnected).toHaveBeenCalledTimes(1);
    },
  },
  {
    name: "#138 focus inside the target never changes the morph result",
    before:
      '<form id="f"><input id="i" name="i" value="x"><span id="s">old</span></form>',
    after:
      '<form id="f"><input id="i" name="i" value="y"><span id="s">new</span></form>',
    verify: () => {
      // Morph the same pair twice, once with focus inside and once without, and
      // assert the serialised result is byte-identical.
      const withFocus = mount(
        '<form id="f"><input id="i" name="i" value="x"><span id="s">old</span></form>',
      );
      withFocus.querySelector<HTMLInputElement>("#i")!.focus();
      morph(
        withFocus,
        '<form id="f"><input id="i" name="i" value="y"><span id="s">new</span></form>',
      );
      const focused = withFocus.outerHTML;
      document.body.innerHTML = "";
      const noFocus = mount(
        '<form id="f"><input id="i" name="i" value="x"><span id="s">old</span></form>',
      );
      morph(
        noFocus,
        '<form id="f"><input id="i" name="i" value="y"><span id="s">new</span></form>',
      );
      expect(noFocus.outerHTML).toBe(focused);
    },
  },
  {
    name: "#141 duplicate ids are deterministic",
    before: '<ul id="l"><li id="dup">a</li><li id="dup">b</li></ul>',
    after: '<ul id="l"><li id="dup">a2</li></ul>',
    setup: (target) => {
      (target as Element & { _first?: Element })._first = target.querySelector("#dup")!;
    },
    verify: (result, target) => {
      const first = (target as Element & { _first?: Element })._first;
      expect(result.querySelector("#dup")).toBe(first);
      expect(result.querySelectorAll("li")).toHaveLength(1);
      expect(result.querySelector("#dup")!.textContent).toBe("a2");
    },
  },
  {
    name: "#147 invalid attribute names do not break the morph",
    before: '<div id="x" class="a"></div>',
    after: '<div id="x" class="b" @change="go" :class="c"></div>',
    verify: (result) => {
      expect(result.getAttribute("class")).toBe("b");
    },
  },
  {
    name: "0.7.1 a boolean attribute is written as an empty string",
    before: '<input id="i">',
    after: '<input id="i" disabled>',
    verify: (result) => {
      expect(result.getAttribute("disabled")).toBe("");
      expect(result.getAttribute("disabled")).not.toBe("true");
    },
  },
  {
    name: "0.7.3 numeric ids do not break the id map",
    before: '<div id="r"><span id="123">a</span><span id="0">b</span></div>',
    after: '<div id="r"><span id="123">a2</span><span id="0">b2</span></div>',
    setup: (target) => {
      const holder = target as Element & { _refs?: Record<string, Element> };
      holder._refs = {
        n123: target.querySelector('[id="123"]')!,
        n0: target.querySelector('[id="0"]')!,
      };
    },
    verify: (result, target) => {
      const refs = (target as Element & { _refs?: Record<string, Element> })._refs!;
      expect(result.querySelector('[id="123"]')).toBe(refs.n123);
      expect(result.querySelector('[id="0"]')).toBe(refs.n0);
      expect(refs.n123.textContent).toBe("a2");
      expect(refs.n0.textContent).toBe("b2");
    },
  },
  {
    name: "0.7.4 DOM clobbering does not blind the matcher",
    before: '<form id="real"><input name="id" value="42"></form>',
    after: '<form id="real"><input name="id" value="42"><span>extra</span></form>',
    setup: (target) => {
      const input = target.querySelector<HTMLInputElement>('[name="id"]')!;
      input.value = "typed";
      (target as Element & { _input?: Element })._input = input;
    },
    dirty: (target) => {
      const input = target.querySelector<HTMLInputElement>('[name="id"]')!;
      return (el) => el === input;
    },
    verify: (result, target) => {
      const input = (target as Element & { _input?: Element })._input;
      expect(result.querySelector('[name="id"]')).toBe(input);
      expect((input as HTMLInputElement).value).toBe("typed");
      expect(result.querySelector("span")!.textContent).toBe("extra");
    },
  },
];

describe("idiomorph bug checklist", () => {
  it.each(bugTable)("$name", ({ before, after, setup, dirty, verify }) => {
    const target = mount(before);
    setup?.(target);
    const result = morph(target, after, dirty ? { isDirty: dirty(target) } : {});
    verify(result, target);
  });

  it("#27 fires no setAttribute on an untouched input", () => {
    const target = mount('<form id="f"><input id="i" name="i" value="server"></form>');
    const input = target.querySelector<HTMLInputElement>("#i")!;
    input.value = "typed";
    const spy = vi.spyOn(input, "setAttribute");
    morph(target, '<form id="f"><input id="i" name="i" value="server"></form>');
    expect(spy).not.toHaveBeenCalled();
  });

  it("#57 syncs an attribute on an atomic custom element without entering it", () => {
    const target = mount(
      '<div id="r"><tracked-widget id="w" v="1"><b>kid</b></tracked-widget></div>',
    );
    lifecycle.connected.mockClear();
    lifecycle.attributeChanged.mockClear();
    morph(target, '<div id="r"><tracked-widget id="w" v="2"></tracked-widget></div>');
    const widget = target.querySelector("#w")!;
    // The attribute is synced, the children are not, the shadow boundary is never
    // crossed, so the only lifecycle that fired is attributeChangedCallback.
    expect(widget.getAttribute("v")).toBe("2");
    expect(widget.querySelector("b")).not.toBeNull();
    expect(lifecycle.attributeChanged).toHaveBeenCalled();
    expect(lifecycle.connected).not.toHaveBeenCalled();
  });

  it("#57 structural mismatch connects the replacement custom element", () => {
    const target = mount('<div id="r"><section id="w"></section></div>');
    lifecycle.connected.mockClear();
    morph(target, '<div id="r"><tracked-widget id="w"></tracked-widget></div>');
    expect(target.querySelector("tracked-widget")).not.toBeNull();
    expect(lifecycle.connected).toHaveBeenCalledTimes(1);
  });

  it("0.7.2 does not re-fire focus or disturb the caret on a reused field", () => {
    const target = mount('<form id="f"><input id="i" name="i" value="hello"></form>');
    const input = target.querySelector<HTMLInputElement>("#i")!;
    input.focus();
    input.setSelectionRange(2, 4);
    const focusSpy = vi.spyOn(input, "focus");
    const rangeSpy = vi.spyOn(input, "setSelectionRange");
    morph(target, '<form id="f"><input id="i" name="i" value="hello"></form>');
    expect(focusSpy).not.toHaveBeenCalled();
    expect(rangeSpy).not.toHaveBeenCalled();
    expect(input.selectionStart).toBe(2);
    expect(input.selectionEnd).toBe(4);
  });

  it("0.7.3 outerHTML morph returns the inserted root on a tag change", () => {
    const target = mount('<div id="r">x</div>');
    const result = morph(target, '<section id="r">x</section>', { mode: "node" });
    expect(result.tagName).toBe("SECTION");
    expect(document.querySelector("#r")).toBe(result);
    expect(document.querySelector("div")).toBeNull();
  });

  it("pantry legacy: a throwing beforeNode leaves no engine container behind", () => {
    const target = mount(
      '<ul id="l"><li id="a">a</li><li id="b">b</li><li id="c">c</li></ul>',
    );
    let seen = 0;
    expect(() =>
      morph(target, '<ul id="l"><li id="a">a2</li><li id="b">b2</li></ul>', {
        beforeNode: () => {
          seen += 1;
          if (seen === 2) throw new Error("boom");
        },
      }),
    ).toThrow("boom");
    // Every node is still either inside the target or removed, no stray wrapper
    // sits at the document root.
    for (const node of Array.from(document.body.children)) {
      expect(node).toBe(target);
    }
    for (const li of Array.from(target.querySelectorAll("li"))) {
      expect(li.closest("#l")).toBe(target);
    }
  });
});

describe("dirty and keep beyond the checklist", () => {
  it("a validation answer for field A never wipes typed input in field B", () => {
    const target = mount(
      '<form id="f">' +
        '<input id="a" name="a" value="server-a">' +
        '<input id="b" name="b" value="server-b">' +
        "</form>",
    );
    const a = target.querySelector<HTMLInputElement>("#a")!;
    const b = target.querySelector<HTMLInputElement>("#b")!;
    // The user typed into B after the request snapshot, so the dirty registry
    // marks only B dirty relative to the response.
    b.value = "typed-into-b";
    const dirty = new Set<Element>([b]);
    morph(
      target,
      '<form id="f">' +
        '<input id="a" name="a" value="A is invalid">' +
        '<input id="b" name="b" value="server-b">' +
        "</form>",
      { isDirty: (el) => dirty.has(el) },
    );
    expect(a.value).toBe("A is invalid");
    expect(b.value).toBe("typed-into-b");
  });

  it("a data-next-keep node survives the morph untouched and may move", () => {
    const target = mount(
      '<div id="r">' +
        '<span id="lead">lead</span>' +
        '<div id="k" data-next-keep v="old"><b>live</b></div>' +
        "</div>",
    );
    const keep = target.querySelector("#k")!;
    const child = keep.firstElementChild;
    morph(
      target,
      '<div id="r">' +
        '<div id="k" data-next-keep v="new"><i>ignored</i></div>' +
        '<span id="lead">lead</span>' +
        "</div>",
    );
    expect(target.querySelector("#k")).toBe(keep);
    expect(keep.firstElementChild).toBe(child);
    expect(keep.getAttribute("v")).toBe("old");
    // The server reordered keep before lead, the move is honoured.
    expect(target.firstElementChild).toBe(keep);
  });
});
