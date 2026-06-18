import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireRemoved, morph } from "./morph";

function mount(html: string): Element {
  document.body.innerHTML = html;
  return document.body.firstElementChild!;
}

describe("morph node reuse", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("reuses the root node by reference on a tag match", () => {
    const target = mount('<div id="card"><span>old</span></div>');
    const result = morph(target, '<div id="card"><span>new</span></div>');
    expect(result).toBe(target);
    expect(target.textContent).toBe("new");
  });

  it("hard-matches a wrapper without an id through a child id", () => {
    const target = mount('<div class="card"><p id="body">one</p></div>');
    const inner = target.querySelector("#body");
    morph(target, '<div class="card"><p id="body">two</p></div>');
    expect(document.querySelector("#body")).toBe(inner);
    expect(inner!.textContent).toBe("two");
  });

  it("soft-matches same-tag siblings without ids", () => {
    const target = mount("<ul><li>a</li><li>b</li></ul>");
    const first = target.querySelector("li");
    morph(target, "<ul><li>x</li><li>y</li></ul>");
    expect(target.querySelector("li")).toBe(first);
    expect(target.textContent).toBe("xy");
  });

  it("creates new nodes and discards a trailing tail", () => {
    const target = mount("<ul><li>a</li><li>b</li><li>c</li></ul>");
    morph(target, "<ul><li>a</li></ul>");
    expect(target.querySelectorAll("li")).toHaveLength(1);
  });

  it("inserts a new node where a text node sat (nodeType mismatch)", () => {
    const target = mount("<div>plain text</div>");
    morph(target, "<div><span>boxed</span></div>");
    expect(target.querySelector("span")!.textContent).toBe("boxed");
  });

  it("beforeNode false on an insert skips the new node", () => {
    const target = mount("<ul><li>a</li></ul>");
    morph(target, "<ul><li>a</li><li>b</li></ul>", {
      beforeNode: (oldNode) => (oldNode === null ? false : undefined),
    });
    expect(target.querySelectorAll("li")).toHaveLength(1);
  });

  it("returns the target when the new fragment has no element", () => {
    const target = mount('<div id="r">x</div>');
    expect(morph(target, "just text")).toBe(target);
  });

  it("moves a matched node into place through the move adapter", () => {
    const calls: Node[] = [];
    const target = mount('<ul><li id="a">a</li><li id="b">b</li></ul>');
    morph(target, '<ul><li id="b">b</li><li id="a">a</li></ul>', {
      move: (parent, node, before) => {
        calls.push(node);
        parent.insertBefore(node, before);
      },
    });
    expect(calls).toHaveLength(1);
    expect([...target.querySelectorAll("li")].map((li) => li.id)).toEqual(["b", "a"]);
  });

  it("syncs text and comment nodeValue by nodeType", () => {
    const target = mount("<div>old<!--c--></div>");
    morph(target, "<div>new<!--d--></div>");
    expect(target.childNodes[0].nodeValue).toBe("new");
    expect(target.childNodes[1].nodeValue).toBe("d");
  });
});

describe("morph attribute sync", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("adds, updates, and removes attributes in three phases", () => {
    const target = mount('<div id="x" class="a" data-old="1"></div>');
    morph(target, '<div id="x" class="b" data-new="2"></div>');
    expect(target.getAttribute("class")).toBe("b");
    expect(target.getAttribute("data-new")).toBe("2");
    expect(target.hasAttribute("data-old")).toBe(false);
  });

  it("leaves a matching attribute untouched", () => {
    const target = mount('<div id="x" class="same"></div>');
    const spy = vi.spyOn(target, "setAttribute");
    morph(target, '<div id="x" class="same"></div>');
    expect(spy).not.toHaveBeenCalled();
  });

  it("writes a boolean attribute as an empty string", () => {
    const target = mount('<input id="i">');
    morph(target, '<input id="i" disabled>');
    expect(target.getAttribute("disabled")).toBe("");
  });

  it("skips invalid attribute names without throwing", () => {
    const target = mount('<div id="x"></div>');
    expect(() =>
      morph(target, '<div id="x" @change="go" :class="c"></div>'),
    ).not.toThrow();
  });

  it("a cancelled morph-attribute keeps the old value", () => {
    const target = mount('<details id="d" open></details>');
    target.addEventListener("next:morph-attribute", (e) => {
      if ((e as CustomEvent).detail.name === "open") e.preventDefault();
    });
    morph(target, '<details id="d"></details>');
    expect(target.hasAttribute("open")).toBe(true);
  });

  it("a cancelled remove keeps the stale attribute", () => {
    const target = mount('<div id="x" data-stale="1"></div>');
    target.addEventListener("next:morph-attribute", (e) => {
      if ((e as CustomEvent).detail.mutationType === "remove") e.preventDefault();
    });
    morph(target, '<div id="x"></div>');
    expect(target.getAttribute("data-stale")).toBe("1");
  });
});

describe("morph live properties", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("keeps an active input value untouched", () => {
    const target = mount('<form id="f"><input id="a" name="a" value="server"></form>');
    const input = target.querySelector<HTMLInputElement>("#a")!;
    input.value = "typed";
    input.focus();
    morph(target, '<form id="f"><input id="a" name="a" value="server"></form>');
    expect(input.value).toBe("typed");
  });

  it("syncs an inactive input value and its default twin", () => {
    const target = mount('<form id="f"><input id="a" name="a" value="old"></form>');
    const input = target.querySelector<HTMLInputElement>("#a")!;
    morph(target, '<form id="f"><input id="a" name="a" value="new"></form>');
    expect(input.value).toBe("new");
    expect(input.defaultValue).toBe("new");
  });

  it("syncs a textarea value and its default twin when clean", () => {
    const target = mount(
      '<form id="f"><textarea id="t" name="t">old</textarea></form>',
    );
    const area = target.querySelector<HTMLTextAreaElement>("#t")!;
    morph(target, '<form id="f"><textarea id="t" name="t">new</textarea></form>');
    expect(area.value).toBe("new");
    expect(area.defaultValue).toBe("new");
  });

  it("protects a dirty textarea from the live value sync", () => {
    const target = mount(
      '<form id="f"><textarea id="t" name="t">old</textarea></form>',
    );
    const area = target.querySelector<HTMLTextAreaElement>("#t")!;
    area.value = "typed";
    morph(target, '<form id="f"><textarea id="t" name="t">srv</textarea></form>', {
      isDirty: (el) => el === area,
    });
    expect(area.value).toBe("typed");
  });

  it("keeps an active checkbox checked state", () => {
    const target = mount('<form id="f"><input id="c" type="checkbox" name="c"></form>');
    const box = target.querySelector<HTMLInputElement>("#c")!;
    box.checked = true;
    box.focus();
    morph(target, '<form id="f"><input id="c" type="checkbox" name="c"></form>');
    expect(box.checked).toBe(true);
  });

  it("protects a dirty input from the live value sync", () => {
    const target = mount('<form id="f"><input id="a" name="a" value="old"></form>');
    const input = target.querySelector<HTMLInputElement>("#a")!;
    input.value = "typed";
    morph(target, '<form id="f"><input id="a" name="a" value="new"></form>', {
      isDirty: (el) => el === input,
    });
    expect(input.value).toBe("typed");
  });

  it("never recreates or syncs a file input", () => {
    const target = mount('<form id="f"><input id="a" type="file" name="a"></form>');
    const input = target.querySelector("#a");
    morph(target, '<form id="f"><input id="a" type="file" name="a"></form>');
    expect(target.querySelector("#a")).toBe(input);
  });

  it("syncs checked and selected on inactive controls", () => {
    const target = mount(
      '<form id="f"><input id="c" type="checkbox"><select id="s">' +
        '<option value="x">x</option><option value="y">y</option></select></form>',
    );
    morph(
      target,
      '<form id="f"><input id="c" type="checkbox" checked><select id="s">' +
        '<option value="x">x</option><option value="y" selected>y</option></select></form>',
    );
    expect(target.querySelector<HTMLInputElement>("#c")!.checked).toBe(true);
    expect(target.querySelector<HTMLSelectElement>("#s")!.value).toBe("y");
  });

  it("does not re-select options of a dirty select", () => {
    const target = mount(
      '<form id="f"><select id="s"><option value="x">x</option>' +
        '<option value="y" selected>y</option></select></form>',
    );
    const select = target.querySelector<HTMLSelectElement>("#s")!;
    select.value = "x";
    morph(
      target,
      '<form id="f"><select id="s"><option value="x">x</option>' +
        '<option value="y" selected>y</option></select></form>',
      { isDirty: (el) => el === select },
    );
    expect(select.value).toBe("x");
  });

  it("leaves a dialog open state to the layer surface", () => {
    const target = mount('<dialog id="d" open></dialog>');
    morph(target, '<dialog id="d"></dialog>');
    expect(target.hasAttribute("open")).toBe(true);
  });

  it("keeps a toggled details open across a morph", () => {
    const target = mount('<details id="d" open></details>');
    morph(target, '<details id="d"></details>', { isDirty: () => true });
    expect(target.hasAttribute("open")).toBe(true);
  });
});

describe("morph atomicity and keep", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("keeps a data-next-keep node and its children untouched", () => {
    const target = mount(
      '<div id="r"><div id="k" data-next-keep><b>live</b></div></div>',
    );
    const keep = target.querySelector("#k")!;
    const child = keep.firstElementChild;
    morph(
      target,
      '<div id="r"><div id="k" data-next-keep class="new"><i>x</i></div></div>',
    );
    expect(keep.firstElementChild).toBe(child);
    expect(keep.hasAttribute("class")).toBe(false);
  });

  it("syncs only attributes on a matched custom element", () => {
    const target = mount(
      '<div id="r"><my-widget id="w" v="1"><b>old</b></my-widget></div>',
    );
    const widget = target.querySelector("#w")!;
    const child = widget.firstElementChild;
    morph(target, '<div id="r"><my-widget id="w" v="2"><i>new</i></my-widget></div>');
    expect(widget.getAttribute("v")).toBe("2");
    expect(widget.firstElementChild).toBe(child);
  });

  it("replaces a custom element whole on a structural mismatch", () => {
    const target = mount('<div id="r"><my-widget id="w"></my-widget></div>');
    const widget = target.querySelector("#w");
    morph(target, '<div id="r"><section id="w"></section></div>');
    expect(target.querySelector("my-widget")).toBeNull();
    expect(target.querySelector("section")).not.toBe(widget);
  });
});

describe("morph modes and root", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("children mode morphs only the children", () => {
    const target = mount('<ul id="l"><li>a</li></ul>');
    morph(target, "<li>a</li><li>b</li>", { mode: "children" });
    expect(target.querySelectorAll("li")).toHaveLength(2);
  });

  it("returns the recreated root when the root tag changes", () => {
    const target = mount('<div id="r">x</div>');
    const result = morph(target, '<section id="r">x</section>');
    expect(result.tagName).toBe("SECTION");
    expect(document.querySelector("#r")).toBe(result);
    expect(document.querySelector("div")).toBeNull();
  });

  it("returns the target when the new content is empty", () => {
    const target = mount('<div id="r">x</div>');
    expect(morph(target, "")).toBe(target);
  });

  it("matches list rows by data-next-key", () => {
    const target = mount(
      '<ul id="l"><li data-next-key="a">a</li><li data-next-key="b">b</li></ul>',
    );
    const rowB = target.querySelector('[data-next-key="b"]');
    morph(
      target,
      '<ul id="l"><li data-next-key="b">b2</li><li data-next-key="a">a2</li></ul>',
    );
    expect(target.querySelector('[data-next-key="b"]')).toBe(rowB);
    expect(rowB!.textContent).toBe("b2");
  });
});

describe("morph next:removed before detach", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  function captureRemoved() {
    const seen: {
      target: Element;
      connected: boolean;
      bubbles: boolean;
      cancelable: boolean;
    }[] = [];
    const listener = (event: Event): void => {
      const target = event.target as Element;
      seen.push({
        target,
        connected: target.isConnected,
        bubbles: event.bubbles,
        cancelable: event.cancelable,
      });
    };
    document.addEventListener("next:removed", listener);
    return {
      seen,
      stop: () => document.removeEventListener("next:removed", listener),
    };
  }

  it("fireRemoved emits a bubbling, non-cancelable event on the node", () => {
    const target = mount('<div id="r"><span id="s">x</span></div>');
    const node = target.querySelector("#s")!;
    const { seen, stop } = captureRemoved();
    fireRemoved(node);
    stop();
    expect(seen).toHaveLength(1);
    expect(seen[0].target).toBe(node);
    expect(seen[0].bubbles).toBe(true);
    expect(seen[0].cancelable).toBe(false);
  });

  it("fires on a discarded trailing child while it is still connected", () => {
    const target = mount('<ul id="l"><li id="a">a</li><li id="b">b</li></ul>');
    const tail = target.querySelector("#b")!;
    const { seen, stop } = captureRemoved();
    morph(target, '<ul id="l"><li id="a">a</li></ul>');
    stop();
    expect(seen).toHaveLength(1);
    expect(seen[0].target).toBe(tail);
    expect(seen[0].connected).toBe(true);
    expect(seen[0].bubbles).toBe(true);
    expect(seen[0].cancelable).toBe(false);
  });

  it("does not fire when onDiscard keeps a node", () => {
    const target = mount('<ul id="l"><li id="a">a</li><li id="b">b</li></ul>');
    const { seen, stop } = captureRemoved();
    morph(target, '<ul id="l"><li id="a">a</li></ul>', { onDiscard: () => false });
    stop();
    expect(seen).toHaveLength(0);
  });

  it("fires on the old root when the root tag changes, before it detaches", () => {
    const target = mount('<div id="r">x</div>');
    const { seen, stop } = captureRemoved();
    morph(target, '<section id="r">x</section>');
    stop();
    expect(seen).toHaveLength(1);
    expect(seen[0].target).toBe(target);
    expect(seen[0].connected).toBe(true);
    expect(seen[0].bubbles).toBe(true);
    expect(seen[0].cancelable).toBe(false);
  });
});

describe("morph hooks and events", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("a cancelled next:morph-element skips the pair", () => {
    const target = mount('<div id="r"><span id="s">old</span></div>');
    target.addEventListener("next:morph-element", (e) => {
      if ((e.target as Element).id === "s") e.preventDefault();
    });
    morph(target, '<div id="r"><span id="s">new</span></div>');
    expect(target.querySelector("#s")!.textContent).toBe("old");
  });

  it("beforeNode false skips a pair, onDiscard false keeps a node", () => {
    const target = mount('<ul id="l"><li id="a">a</li><li id="b">b</li></ul>');
    morph(target, '<ul id="l"><li id="a">a2</li></ul>', {
      onDiscard: () => false,
    });
    expect(target.querySelector("#b")).not.toBeNull();
  });

  it("fires afterNode for a morphed pair", () => {
    const target = mount('<div id="r">x</div>');
    const after = vi.fn();
    morph(target, '<div id="r">y</div>', { afterNode: after });
    expect(after).toHaveBeenCalled();
  });

  it("warns on a data-next-keep node without an id", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const target = mount('<div id="r"><span data-next-keep>x</span></div>');
    morph(target, '<div id="r"><span data-next-keep class="c">y</span></div>');
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("warns on a node carrying both data-next-key and id", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const target = mount('<ul id="l"><li id="x" data-next-key="x">x</li></ul>');
    morph(target, '<ul id="l"><li id="x" data-next-key="x">y</li></ul>');
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("does not restore focus when no focus was lost", () => {
    const target = mount('<form id="f"><input id="a" name="a"></form>');
    const input = target.querySelector<HTMLInputElement>("#a")!;
    input.focus();
    const spy = vi.spyOn(input, "focus");
    morph(target, '<form id="f"><input id="a" name="a"></form>');
    expect(spy).not.toHaveBeenCalled();
  });

  it("restores focus and caret when a moved field loses it", () => {
    const target = mount(
      '<ul id="l"><li id="a"><input id="ia" name="a" value="hello"></li>' +
        '<li id="b">b</li></ul>',
    );
    const input = target.querySelector<HTMLInputElement>("#ia")!;
    input.focus();
    input.setSelectionRange(1, 3);
    morph(
      target,
      '<ul id="l"><li id="b">b</li>' +
        '<li id="a"><input id="ia" name="a" value="hello"></li></ul>',
      {
        move: (parent, node, before) => parent.insertBefore(node, before),
      },
    );
    expect(document.activeElement).toBe(input);
    expect(input.selectionStart).toBe(1);
    expect(input.selectionEnd).toBe(3);
  });

  it("accepts an already-parsed element as new content", () => {
    const target = mount('<div id="r"><span>old</span></div>');
    const replacement = document.createElement("div");
    replacement.id = "r";
    replacement.innerHTML = "<span>new</span>";
    const result = morph(target, replacement);
    expect(result).toBe(target);
    expect(target.textContent).toBe("new");
  });

  it("is deterministic on duplicate ids, reusing the first in document order", () => {
    const target = mount('<ul id="l"><li id="dup">a</li><li id="dup">b</li></ul>');
    const first = target.querySelector("#dup");
    expect(() => morph(target, '<ul id="l"><li id="dup">a2</li></ul>')).not.toThrow();
    expect(target.querySelector("#dup")).toBe(first);
    expect(target.querySelectorAll("li")).toHaveLength(1);
  });
});
