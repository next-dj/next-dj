import { afterEach, describe, expect, it, vi } from "vitest";
import "./next";
import "./entries/defer";

interface NextStatic {
  partial: {
    fetch(request: Element | { url: string }): Promise<{ status: string }>;
    mount(nodes: readonly Element[]): void;
  };
}

const win = globalThis as unknown as { Next: NextStatic };

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

async function drain(): Promise<void> {
  for (let i = 0; i < 20; i++) await Promise.resolve();
}

describe("the defer chunk entry", () => {
  it("lets Next.partial.fetch take an element that is neither a wrapper nor a zone", async () => {
    await expect(win.Next.partial.fetch(document.createElement("p"))).resolves.toEqual({
      status: "dropped",
      applied: false,
    });
  });

  it("lands a scan the runtime hands every mounted wrapper to", async () => {
    const fetch = vi.fn(() => Promise.reject(new TypeError("offline")));
    vi.stubGlobal("fetch", fetch);
    document.body.innerHTML =
      '<div id="b-1" data-next-boundary="b-1" data-next-token="tok" ' +
      'data-next-state="idle" data-next-defer="load"></div>';
    win.Next.partial.mount([document.getElementById("b-1")!]);
    await drain();
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(new URL(url).pathname).toBe(location.pathname);
    expect(new Headers(init.headers).get("X-Next-Boundary")).toBe("tok");
  });
});
