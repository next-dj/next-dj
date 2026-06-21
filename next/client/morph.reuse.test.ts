import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { morph } from "./morph";

function mount(html: string): Element {
  document.body.innerHTML = html;
  return document.body.firstElementChild!;
}

// A move adapter that records moves and falls back to insertBefore, since the
// native moveBefore is absent from jsdom.
function recordingMove() {
  const moved: Element[] = [];
  return {
    moved,
    move: (parent: ParentNode, node: Node, before: Node | null) => {
      moved.push(node as Element);
      parent.insertBefore(node, before);
    },
  };
}

beforeEach(() => {
  document.body.innerHTML = "";
});

afterEach(() => {
  document.body.innerHTML = "";
});

describe("middle insertion into a keyed list reuses the tail by reference", () => {
  it("keeps b and c the same object when x is inserted by id", () => {
    const target = mount(
      '<ul id="l">' +
        '<li id="a"><input name="a" value="va"></li>' +
        '<li id="b"><input name="b" value="vb"></li>' +
        '<li id="c"><input name="c" value="vc"></li>' +
        "</ul>",
    );
    const b = target.querySelector("#b")!;
    const c = target.querySelector("#c")!;
    const bInput = b.querySelector<HTMLInputElement>("input")!;
    const cInput = c.querySelector<HTMLInputElement>("input")!;
    bInput.value = "typed-b";
    cInput.value = "typed-c";
    const { move, moved } = recordingMove();
    morph(
      target,
      '<ul id="l">' +
        '<li id="a"><input name="a" value="va"></li>' +
        '<li id="x"><input name="x" value="vx"></li>' +
        '<li id="b"><input name="b" value="vb"></li>' +
        '<li id="c"><input name="c" value="vc"></li>' +
        "</ul>",
      { move, isDirty: () => true },
    );
    expect([...target.querySelectorAll("li")].map((li) => li.id)).toEqual([
      "a",
      "x",
      "b",
      "c",
    ]);
    expect(target.querySelector("#b")).toBe(b);
    expect(target.querySelector("#c")).toBe(c);
    expect(b.querySelector<HTMLInputElement>("input")!.value).toBe("typed-b");
    expect(c.querySelector<HTMLInputElement>("input")!.value).toBe("typed-c");
    expect(moved.map((m) => m.id)).not.toContain("b");
    expect(moved.map((m) => m.id)).not.toContain("c");
  });
});

describe("middle insertion into a keyed list reuses the tail by data-next-key", () => {
  it("keeps b and c the same object when x is inserted by key", () => {
    const target = mount(
      '<ul id="l">' +
        '<li data-next-key="a"><input name="a" value="va"></li>' +
        '<li data-next-key="b"><input name="b" value="vb"></li>' +
        '<li data-next-key="c"><input name="c" value="vc"></li>' +
        "</ul>",
    );
    const b = target.querySelector('[data-next-key="b"]')!;
    const c = target.querySelector('[data-next-key="c"]')!;
    b.querySelector<HTMLInputElement>("input")!.value = "typed-b";
    c.querySelector<HTMLInputElement>("input")!.value = "typed-c";
    const { move } = recordingMove();
    morph(
      target,
      '<ul id="l">' +
        '<li data-next-key="a"><input name="a" value="va"></li>' +
        '<li data-next-key="x"><input name="x" value="vx"></li>' +
        '<li data-next-key="b"><input name="b" value="vb"></li>' +
        '<li data-next-key="c"><input name="c" value="vc"></li>' +
        "</ul>",
      { move, isDirty: () => true },
    );
    expect(
      [...target.querySelectorAll("li")].map((li) => li.getAttribute("data-next-key")),
    ).toEqual(["a", "x", "b", "c"]);
    expect(target.querySelector('[data-next-key="b"]')).toBe(b);
    expect(target.querySelector('[data-next-key="c"]')).toBe(c);
    expect(b.querySelector<HTMLInputElement>("input")!.value).toBe("typed-b");
    expect(c.querySelector<HTMLInputElement>("input")!.value).toBe("typed-c");
  });
});

describe("middle insertion into a keyless list shifts the tail onto the wrong row", () => {
  it("smears the typed value onto a shifted node and recreates the last position", () => {
    const target = mount(
      '<ul id="l">' +
        '<li><input name="a" value="va"></li>' +
        '<li><input name="b" value="vb"></li>' +
        '<li><input name="c" value="vc"></li>' +
        "</ul>",
    );
    const items = [...target.querySelectorAll("li")];
    const tail = items[2]!;
    const tailInput = tail.querySelector<HTMLInputElement>("input")!;
    tailInput.value = "typed-into-tail";
    const { move, moved } = recordingMove();
    morph(
      target,
      '<ul id="l">' +
        '<li><input name="a" value="va"></li>' +
        '<li><input name="x" value="vx"></li>' +
        '<li><input name="b" value="vb"></li>' +
        '<li><input name="c" value="vc"></li>' +
        "</ul>",
      { move, isDirty: () => true },
    );
    // The structural result is correct: four rows in the requested order.
    const after = [...target.querySelectorAll("li")];
    expect(after).toHaveLength(4);
    expect(after.map((li) => li.querySelector("input")!.name)).toEqual([
      "a",
      "x",
      "b",
      "c",
    ]);
    // A keyless soft match only checks the pointer position, never a forward
    // scan, so old rows are reused in document order and a fresh node fills the
    // final slot. No move was needed because nothing was found further down.
    expect(moved).toHaveLength(0);
    // The original tail node stays connected but slides to the B position, the
    // dirty pin carries the typed value onto a row the server now names "b".
    expect(tail.isConnected).toBe(true);
    expect(after.indexOf(tail)).toBe(2);
    expect(tail.querySelector<HTMLInputElement>("input")!.name).toBe("b");
    expect(tail.querySelector<HTMLInputElement>("input")!.value).toBe(
      "typed-into-tail",
    );
    // The new last row is a fresh node carrying the clean server default, the
    // user's input no longer lives on the row they edited.
    expect(after[3]).not.toBe(tail);
    expect(after[3]!.querySelector<HTMLInputElement>("input")!.value).toBe("vc");
    expect(after.map((li) => li.querySelector("input")!.value)).toEqual([
      "va",
      "vx",
      "typed-into-tail",
      "vc",
    ]);
  });

  it("reuses old rows in document order and grafts a fresh node for the last slot", () => {
    const target = mount('<ul id="l"><li>A</li><li>B</li><li>C</li></ul>');
    const [oldA, oldB, oldC] = [...target.querySelectorAll("li")];
    morph(target, '<ul id="l"><li>A</li><li>X</li><li>B</li><li>C</li></ul>');
    const rows = [...target.querySelectorAll("li")];
    // Soft match at the pointer reuses A for A, then old B for X, old C for B,
    // and creates a brand-new node for the final C. Every old node stays
    // connected, but each carries a different label than before.
    expect(rows[0]).toBe(oldA);
    expect(rows[1]).toBe(oldB);
    expect(rows[2]).toBe(oldC);
    expect(rows[3]).not.toBe(oldA);
    expect(rows[3]).not.toBe(oldB);
    expect(rows[3]).not.toBe(oldC);
    expect(oldA!.isConnected).toBe(true);
    expect(oldB!.isConnected).toBe(true);
    expect(oldC!.isConnected).toBe(true);
    expect(rows.map((r) => r.textContent)).toEqual(["A", "X", "B", "C"]);
  });
});

describe("deletion from the middle of a keyed list", () => {
  it("reuses the surviving tail and discards the removed node", () => {
    const target = mount(
      '<ul id="l">' +
        '<li id="a"><input name="a"></li>' +
        '<li id="b"><input name="b"></li>' +
        '<li id="c"><input name="c"></li>' +
        "</ul>",
    );
    const a = target.querySelector("#a")!;
    const b = target.querySelector("#b")!;
    const c = target.querySelector("#c")!;
    const cInput = c.querySelector<HTMLInputElement>("input")!;
    cInput.value = "typed-c";
    const { move, moved } = recordingMove();
    morph(
      target,
      '<ul id="l"><li id="a"><input name="a"></li><li id="c"><input name="c"></li></ul>',
      { move, isDirty: () => true },
    );
    expect([...target.querySelectorAll("li")].map((li) => li.id)).toEqual(["a", "c"]);
    expect(target.querySelector("#a")).toBe(a);
    expect(target.querySelector("#c")).toBe(c);
    expect(c.querySelector<HTMLInputElement>("input")!.value).toBe("typed-c");
    expect(b.isConnected).toBe(false);
    // c sat further down the scan past the discarded b, so it moved into place.
    expect(moved.map((m) => m.id)).toEqual(["c"]);
  });
});

describe("reordering a keyed list reuses every node and moves it", () => {
  it("keeps all three by reference and routes the shuffle through the move adapter", () => {
    const target = mount(
      '<ul id="l">' +
        '<li id="a"><input name="a"></li>' +
        '<li id="b"><input name="b"></li>' +
        '<li id="c"><input name="c"></li>' +
        "</ul>",
    );
    const a = target.querySelector("#a")!;
    const b = target.querySelector("#b")!;
    const c = target.querySelector("#c")!;
    a.querySelector<HTMLInputElement>("input")!.value = "typed-a";
    b.querySelector<HTMLInputElement>("input")!.value = "typed-b";
    c.querySelector<HTMLInputElement>("input")!.value = "typed-c";
    const { move, moved } = recordingMove();
    morph(
      target,
      '<ul id="l">' +
        '<li id="c"><input name="c"></li>' +
        '<li id="a"><input name="a"></li>' +
        '<li id="b"><input name="b"></li>' +
        "</ul>",
      { move, isDirty: () => true },
    );
    expect([...target.querySelectorAll("li")].map((li) => li.id)).toEqual([
      "c",
      "a",
      "b",
    ]);
    expect(target.querySelector("#a")).toBe(a);
    expect(target.querySelector("#b")).toBe(b);
    expect(target.querySelector("#c")).toBe(c);
    expect(a.querySelector<HTMLInputElement>("input")!.value).toBe("typed-a");
    expect(b.querySelector<HTMLInputElement>("input")!.value).toBe("typed-b");
    expect(c.querySelector<HTMLInputElement>("input")!.value).toBe("typed-c");
    expect(moved.length).toBeGreaterThan(0);
  });
});
