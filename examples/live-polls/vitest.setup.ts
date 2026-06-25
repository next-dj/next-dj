import { afterEach } from "vitest";

afterEach(() => {
  // Each test owns its own window.Next stub. Clearing it keeps the page
  // entry's onMount registration from leaking across cases.
  delete (globalThis as unknown as { Next?: unknown }).Next;
});
