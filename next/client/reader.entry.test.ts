import { afterEach, describe, expect, it, vi } from "vitest";
import "./next";
import "./entries/reader";

interface NextStatic {
  _init(context: Record<string, unknown>): void;
  on(event: string, listener: () => void): () => void;
  partial: { _reader?: unknown };
}

const win = globalThis as unknown as { Next: NextStatic };

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("the reader chunk entry", () => {
  it("drains the chunks a streamed document pushed once the runtime is ready", () => {
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
      frames.push(callback),
    );
    document.body.innerHTML =
      '<div id="b-1" data-next-boundary="b-1" data-next-state="loading">wait</div>';
    (window.$nx ??= []).push({
      z: "b-1",
      s: "ready",
      e: { ops: [{ op: "inner", target: { boundary: "b-1" }, html: "one" }] },
    });
    const applied = vi.fn();
    document.addEventListener("partial:applied", applied);
    expect(frames).toHaveLength(0);
    win.Next._init({});
    win.Next._init({});
    for (const callback of frames.splice(0)) callback(0);
    document.removeEventListener("partial:applied", applied);
    expect(document.getElementById("b-1")!.textContent).toBe("one");
    expect(document.getElementById("b-1")!.getAttribute("data-next-state")).toBe(
      "ready",
    );
    expect(applied).toHaveBeenCalledTimes(1);
  });

  it("installs nothing more when the bundle evaluates twice", async () => {
    const reader = win.Next.partial._reader;
    const on = vi.spyOn(win.Next, "on");
    vi.resetModules();
    await import("./entries/reader");
    expect(reader).toBeTypeOf("function");
    expect(win.Next.partial._reader).toBe(reader);
    expect(on).not.toHaveBeenCalled();
  });
});
