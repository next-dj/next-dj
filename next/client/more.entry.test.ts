import { afterEach, describe, expect, it, vi } from "vitest";
import "./next";
import "./entries/more";

interface NextStatic {
  partial: {
    mount(nodes: readonly Element[]): void;
    onMount(selector: string, callback: (el: Element) => void): () => void;
  };
}

const win = globalThis as unknown as { Next: NextStatic };

const FEED =
  '<ul data-next-zone="list"><li>a</li></ul><div data-next-zone="list-more">' +
  '<a href="/list/?page=2" data-next-target="list,list-more" ' +
  'data-next-url="replace">More</a></div>';

afterEach(() => {
  document.body.innerHTML = "";
});

describe("the more chunk entry", () => {
  it("stamps the rows of a list as the runtime mounts its more link", () => {
    document.body.innerHTML = FEED;
    win.Next.partial.mount([document.body]);
    expect(document.querySelector("li")!.getAttribute("data-next-batch")).toBe("1");
  });

  it("registers nothing more when the bundle evaluates twice", async () => {
    const onMount = vi.spyOn(win.Next.partial, "onMount");
    const listen = vi.spyOn(document, "addEventListener");
    vi.resetModules();
    await import("./entries/more");
    const settled = listen.mock.calls.filter(([type]) => type === "partial:settled");
    onMount.mockRestore();
    listen.mockRestore();
    expect(onMount).not.toHaveBeenCalled();
    expect(settled).toEqual([]);
  });
});
