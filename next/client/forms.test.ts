import { afterEach, expect, it, vi } from "vitest";
import { createForms } from "./forms";

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = "";
  history.replaceState(null, "", "/");
});

it("debounces on the platform clock and writes the bar through the history", () => {
  vi.useFakeTimers();
  document.body.innerHTML =
    '<form action="/list/" data-next-target="list">' +
    '<input name="q" data-next-trigger="input" data-next-debounce="50"></form>';
  const fetch = vi.fn();
  const detach = createForms({ fetch, abort: vi.fn() }).install(document);
  const input = document.querySelector("input")!;
  input.value = "a";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  expect(fetch).not.toHaveBeenCalled();
  vi.advanceTimersByTime(50);
  expect(fetch).toHaveBeenCalledWith({ url: "/list/?q=a", zone: "list" });
  expect(location.pathname + location.search).toBe("/list/?q=a");
  detach();
});
