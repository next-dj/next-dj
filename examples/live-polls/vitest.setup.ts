import { afterEach } from "vitest";

afterEach(() => {
  // Each test owns its own window.Next.context and window.EventSource
  // stub. Clearing both after every test keeps the module-level
  // singletons inside `component.vue` from leaking between cases.
  delete (globalThis as unknown as { Next?: unknown }).Next;
  delete (globalThis as unknown as { EventSource?: unknown }).EventSource;
});
