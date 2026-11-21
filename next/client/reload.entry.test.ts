import { afterEach, describe, expect, it, vi } from "vitest";
import "./next";
import "./entries/reload";

interface NextStatic {
  partial: { mount(nodes: readonly Element[]): void };
}

const win = globalThis as unknown as { Next: NextStatic };

afterEach(() => {
  document.dispatchEvent(
    new CustomEvent("partial:settled", {
      detail: { zones: ["z"], boundaries: [], status: "ok" },
    }),
  );
  vi.useRealTimers();
  document.body.innerHTML = "";
});

function reload(): void {
  document.dispatchEvent(
    new CustomEvent("partial:before-request", {
      detail: { url: "/", method: "GET", intent: { zone: "z" } },
    }),
  );
}

describe("the reload chunk entry", () => {
  it("shows the loading content of a boundary in a zone the runtime reloads", () => {
    vi.useFakeTimers();
    document.body.innerHTML =
      '<section data-next-zone="z" data-next-reload="loading">' +
      '<div id="b-1" data-next-boundary="b-1" data-next-state="ready">done</div>' +
      '</section><template data-next-loading="b-1"><i>wait</i></template>';
    win.Next.partial.mount([document.body]);
    reload();
    vi.advanceTimersByTime(150);
    expect(document.getElementById("b-1")!.innerHTML).toBe("<i>wait</i>");
  });

  it("registers its listeners once when the bundle evaluates twice", async () => {
    vi.resetModules();
    await import("./entries/reload");
    vi.useFakeTimers();
    document.body.innerHTML =
      '<section data-next-zone="z" data-next-reload="loading">' +
      '<div id="b-1" data-next-boundary="b-1" data-next-state="ready">done</div>' +
      '</section><template data-next-loading="b-1"><i>wait</i></template>';
    win.Next.partial.mount([document.body]);
    const timers = vi.getTimerCount();
    reload();
    expect(vi.getTimerCount() - timers).toBe(1);
  });
});
