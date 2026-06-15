import { createPartial } from "./partial";
import type { PartialSurface } from "./partial";

type NextEvent = "ready" | "context-updated" | (string & {});
type NextListener = (payload: Record<string, unknown>) => void;
type NextPlugin<T> = (next: typeof Next) => T;

class Next {
  static #context: Record<string, unknown> = {};
  static #listeners: Map<string, Set<NextListener>> = new Map();
  static #ready = false;

  static partial: PartialSurface = createPartial({
    dispatch: (event, payload) => Next.#dispatch(event, payload),
    mergeContext: (data) => Next.#mergeContext(data),
  });

  static get context(): Readonly<Record<string, unknown>> {
    return Object.freeze({ ...Next.#context });
  }

  static _init(context: Record<string, unknown>): void {
    Next.#context = context;
    Next.#ready = true;
    Next.#dispatch("context-updated", context);
    // Seed the asset registry, mount the initial DOM, and fire the batched load
    // zones before `ready` listeners run, so a `ready` handler sees a mounted
    // document.
    Next.partial.ready();
    Next.#dispatch("ready", context);
  }

  static on(event: NextEvent, listener: NextListener): () => void {
    let bucket = Next.#listeners.get(event);
    if (bucket === undefined) {
      bucket = new Set();
      Next.#listeners.set(event, bucket);
    }
    bucket.add(listener);
    if (event === "ready" && Next.#ready) {
      listener({ ...Next.#context });
    }
    return () => {
      bucket!.delete(listener);
    };
  }

  static use<T>(plugin: NextPlugin<T>): T {
    return plugin(Next);
  }

  // The operation `context` and the `csrf` meta merge into the same store
  // that `_init` owns, so context islands see one consistent snapshot.
  static #mergeContext(data: Record<string, unknown>): void {
    Next.#context = { ...Next.#context, ...data };
    Next.#dispatch("context-updated", Next.#context);
  }

  static #dispatch(event: string, payload: Record<string, unknown>): void {
    const bucket = Next.#listeners.get(event);
    if (bucket === undefined) return;
    for (const listener of bucket) {
      listener(payload);
    }
  }
}

(window as unknown as { Next: typeof Next }).Next = Next;
