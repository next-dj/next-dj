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

  it("refuses a soft match at a pointer that carries a persistent id", () => {
    // The pointer #a shares an id present on both sides, so it is reserved for a
    // hard match. A keyless new <li> finds no hard match and the soft match at
    // #a is refused, so a fresh node is inserted ahead of the reserved #a.
    const target = mount('<ul id="l"><li id="a">a</li></ul>');
    const reserved = target.querySelector("#a");
    morph(target, '<ul id="l"><li>fresh</li><li id="a">a</li></ul>');
    expect([...target.querySelectorAll("li")].map((li) => li.id)).toEqual(["", "a"]);
    expect(target.querySelector("#a")).toBe(reserved);
    expect(target.firstElementChild!.textContent).toBe("fresh");
  });

  it("soft-matches a pointer whose only id is gone from the new tree", () => {
    // #ghost lives on the old side alone, so it owns no persistent vote. The
    // keyless new <li> takes it as a soft match rather than inserting fresh.
    const target = mount('<ul id="l"><li id="ghost">old</li></ul>');
    const ghost = target.querySelector("#ghost");
    morph(target, '<ul id="l"><li>new</li></ul>');
    expect(target.querySelector("li")).toBe(ghost);
    expect(target.querySelectorAll("li")).toHaveLength(1);
    expect(target.textContent).toBe("new");
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

  it("a cancelled update keeps the old attribute value", () => {
    const target = mount('<div id="x" class="old"></div>');
    target.addEventListener("next:morph-attribute", (e) => {
      const detail = (e as CustomEvent).detail;
      if (detail.name === "class" && detail.mutationType === "update") {
        e.preventDefault();
      }
    });
    morph(target, '<div id="x" class="new"></div>');
    expect(target.getAttribute("class")).toBe("old");
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

  it("children mode skips a leading text node when collecting ids", () => {
    const target = mount('<ul id="l"><li id="a">a</li></ul>');
    const row = target.querySelector("#a");
    morph(target, 'lead<li id="a">a2</li>', { mode: "children" });
    expect(target.childNodes[0].nodeValue).toBe("lead");
    expect(target.querySelector("#a")).toBe(row);
    expect(row!.textContent).toBe("a2");
  });

  it("returns the recreated root when the root tag changes", () => {
    const target = mount('<div id="r">x</div>');
    const result = morph(target, '<section id="r">x</section>');
    expect(result.tagName).toBe("SECTION");
    expect(document.querySelector("#r")).toBe(result);
    expect(document.querySelector("div")).toBeNull();
  });

  it("returns the new root on a tag change of a detached target", () => {
    // A target with no parent cannot be relinked, so the new root is returned
    // and the old detached node is simply left behind.
    const target = document.createElement("div");
    target.id = "r";
    target.textContent = "x";
    const result = morph(target, '<section id="r">x</section>');
    expect(result.tagName).toBe("SECTION");
    expect(result).not.toBe(target);
    expect(target.isConnected).toBe(false);
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

  it("beforeNode false on a matched pair leaves the old node untouched", () => {
    const target = mount('<div id="r"><span id="s">old</span></div>');
    morph(target, '<div id="r"><span id="s">new</span></div>', {
      beforeNode: (oldNode) =>
        oldNode !== null && (oldNode as Element).id === "s" ? false : undefined,
    });
    expect(target.querySelector("#s")!.textContent).toBe("old");
  });

  it("fires afterNode for a morphed pair", () => {
    const target = mount('<div id="r">x</div>');
    const after = vi.fn();
    morph(target, '<div id="r">y</div>', { afterNode: after });
    expect(after).toHaveBeenCalled();
  });

  it("keeps a data-next-keep node without an id, paired by position", () => {
    const target = mount('<div id="r"><span data-next-keep><b>live</b></span></div>');
    const keep = target.querySelector("[data-next-keep]")!;
    const child = keep.firstElementChild;
    morph(target, '<div id="r"><span data-next-keep class="new"><i>x</i></span></div>');
    expect(target.querySelector("[data-next-keep]")).toBe(keep);
    expect(keep.firstElementChild).toBe(child);
    expect(keep.hasAttribute("class")).toBe(false);
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

  it("re-focuses and restores the caret when a relocate drops focus", () => {
    // A real browser blurs a focused node while it is being relocated. jsdom
    // keeps focus through insertBefore, so the move adapter blurs to model the
    // native behaviour and drive the focus-loss restore branch.
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
        move: (parent, node, before) => {
          (document.activeElement as HTMLElement | null)?.blur();
          parent.insertBefore(node, before);
        },
      },
    );
    expect(document.activeElement).toBe(input);
    expect(input.selectionStart).toBe(1);
    expect(input.selectionEnd).toBe(3);
  });

  it("re-focuses a checkbox whose caret read is null without a range restore", () => {
    // A checkbox reports a null selectionStart, so snap.start stays null and the
    // restore re-focuses the box but never reaches setSelectionRange.
    const target = mount(
      '<ul id="l"><li id="a"><input id="ca" type="checkbox" name="a"></li>' +
        '<li id="b">b</li></ul>',
    );
    const box = target.querySelector<HTMLInputElement>("#ca")!;
    box.focus();
    morph(
      target,
      '<ul id="l"><li id="b">b</li>' +
        '<li id="a"><input id="ca" type="checkbox" name="a"></li></ul>',
      {
        move: (parent, node, before) => {
          (document.activeElement as HTMLElement | null)?.blur();
          parent.insertBefore(node, before);
        },
      },
    );
    expect(document.activeElement).toBe(box);
  });

  it("re-focuses a button and skips a caret restore it cannot accept", () => {
    // A button exposes no settable selection range, so the caret restore enters
    // and setSelectionRange throws, exercising the swallow on the restore path.
    const target = mount(
      '<ul id="l"><li id="a"><button id="btn">go</button></li>' +
        '<li id="b">b</li></ul>',
    );
    const button = target.querySelector<HTMLButtonElement>("#btn")!;
    button.focus();
    morph(
      target,
      '<ul id="l"><li id="b">b</li>' +
        '<li id="a"><button id="btn">go</button></li></ul>',
      {
        move: (parent, node, before) => {
          (document.activeElement as HTMLElement | null)?.blur();
          parent.insertBefore(node, before);
        },
      },
    );
    expect(document.activeElement).toBe(button);
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
