import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { morph } from "./morph";

function mount(html: string): Element {
  document.body.innerHTML = html;
  return document.body.firstElementChild!;
}

beforeEach(() => {
  document.body.innerHTML = "";
});

afterEach(() => {
  document.body.innerHTML = "";
});

describe("input value splits from its default twin", () => {
  it("syncs defaultValue always and the live value only when clean", () => {
    const target = mount('<form id="f"><input id="i" name="i" value="old"></form>');
    const input = target.querySelector<HTMLInputElement>("#i")!;
    morph(target, '<form id="f"><input id="i" name="i" value="srv"></form>');
    expect(input.value).toBe("srv");
    expect(input.defaultValue).toBe("srv");
  });

  it("syncs defaultValue but pins the live value when dirty", () => {
    const target = mount('<form id="f"><input id="i" name="i" value="old"></form>');
    const input = target.querySelector<HTMLInputElement>("#i")!;
    input.value = "typed";
    morph(target, '<form id="f"><input id="i" name="i" value="srv"></form>', {
      isDirty: (el) => el === input,
    });
    expect(input.value).toBe("typed");
    expect(input.defaultValue).toBe("srv");
  });

  it("pins the live value when the field is active", () => {
    const target = mount('<form id="f"><input id="i" name="i" value="old"></form>');
    const input = target.querySelector<HTMLInputElement>("#i")!;
    input.value = "typed";
    input.focus();
    morph(target, '<form id="f"><input id="i" name="i" value="srv"></form>');
    expect(input.value).toBe("typed");
    expect(input.defaultValue).toBe("srv");
  });
});

describe("checkbox checked splits from its default twin", () => {
  it("syncs defaultChecked always and live checked only when clean", () => {
    const target = mount('<form id="f"><input id="c" type="checkbox"></form>');
    const box = target.querySelector<HTMLInputElement>("#c")!;
    morph(target, '<form id="f"><input id="c" type="checkbox" checked></form>');
    expect(box.checked).toBe(true);
    expect(box.defaultChecked).toBe(true);
  });

  it("pins live checked when dirty, still syncing defaultChecked", () => {
    const target = mount('<form id="f"><input id="c" type="checkbox" checked></form>');
    const box = target.querySelector<HTMLInputElement>("#c")!;
    box.checked = false;
    morph(target, '<form id="f"><input id="c" type="checkbox" checked></form>', {
      isDirty: (el) => el === box,
    });
    expect(box.checked).toBe(false);
    expect(box.defaultChecked).toBe(true);
  });
});

describe("textarea value and its text node sync together", () => {
  it("syncs value and defaultValue when clean", () => {
    const target = mount(
      '<form id="f"><textarea id="t" name="t">old</textarea></form>',
    );
    const area = target.querySelector<HTMLTextAreaElement>("#t")!;
    morph(target, '<form id="f"><textarea id="t" name="t">srv</textarea></form>');
    expect(area.value).toBe("srv");
    expect(area.defaultValue).toBe("srv");
    expect(area.textContent).toBe("srv");
  });

  it("pins the live value but tracks defaultValue when dirty", () => {
    const target = mount(
      '<form id="f"><textarea id="t" name="t">old</textarea></form>',
    );
    const area = target.querySelector<HTMLTextAreaElement>("#t")!;
    area.value = "typed";
    morph(target, '<form id="f"><textarea id="t" name="t">srv</textarea></form>', {
      isDirty: (el) => el === area,
    });
    expect(area.value).toBe("typed");
    expect(area.defaultValue).toBe("srv");
  });
});

describe("file input is untouchable", () => {
  it("is never recreated when matched and never has files synced", () => {
    const target = mount('<form id="f"><input id="i" type="file" name="i"></form>');
    const input = target.querySelector("#i")!;
    morph(
      target,
      '<form id="f"><input id="i" type="file" name="i" accept=".png"></form>',
    );
    expect(target.querySelector("#i")).toBe(input);
    expect(input.getAttribute("accept")).toBe(".png");
  });
});

describe("option selected aligns the select value", () => {
  it("syncs each option and lifts the select value when clean", () => {
    const target = mount(
      '<form id="f"><select id="s"><option value="x">x</option>' +
        '<option value="y">y</option></select></form>',
    );
    const select = target.querySelector<HTMLSelectElement>("#s")!;
    const optY = select.querySelector<HTMLOptionElement>('[value="y"]')!;
    morph(
      target,
      '<form id="f"><select id="s"><option value="x">x</option>' +
        '<option value="y" selected>y</option></select></form>',
    );
    expect(select.value).toBe("y");
    expect(optY.defaultSelected).toBe(true);
  });

  it("does not overwrite a user choice on a dirty select", () => {
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
});

describe("details open obeys the dirty rule", () => {
  it("server markup wins when the user has not toggled", () => {
    const target = mount('<details id="d"></details>');
    morph(target, '<details id="d" open></details>');
    expect(target.hasAttribute("open")).toBe(true);
  });

  it("a toggled details keeps the user state", () => {
    const target = mount('<details id="d" open></details>');
    morph(target, '<details id="d"></details>', { isDirty: () => true });
    expect(target.hasAttribute("open")).toBe(true);
  });

  it("a preventDefault on next:morph-attribute opts the open sync out", () => {
    const target = mount('<details id="d" open></details>');
    target.addEventListener("next:morph-attribute", (e) => {
      if ((e as CustomEvent).detail.name === "open") e.preventDefault();
    });
    morph(target, '<details id="d"></details>');
    expect(target.hasAttribute("open")).toBe(true);
  });
});

describe("dialog open is the layer surface, not the morph", () => {
  it("leaves a dialog open attribute alone in both directions", () => {
    const open = mount('<dialog id="d" open></dialog>');
    morph(open, '<dialog id="d"></dialog>');
    expect(open.hasAttribute("open")).toBe(true);
    document.body.innerHTML = "";
    const closed = mount('<dialog id="d"></dialog>');
    morph(closed, '<dialog id="d" open></dialog>');
    expect(closed.hasAttribute("open")).toBe(false);
  });
});

describe("id is read through getAttribute, not the id property", () => {
  it("matches a form clobbered by an input named id", () => {
    const target = mount('<form id="real"><input name="id" value="42"></form>');
    const input = target.querySelector<HTMLInputElement>('[name="id"]')!;
    input.value = "typed";
    morph(target, '<form id="real"><input name="id" value="42"><b>x</b></form>', {
      isDirty: (el) => el === input,
    });
    expect(target.querySelector('[name="id"]')).toBe(input);
    expect(input.value).toBe("typed");
    expect(target.querySelector("b")!.textContent).toBe("x");
  });
});
