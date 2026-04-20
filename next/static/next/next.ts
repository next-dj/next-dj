type NextEvent = "ready" | "context-updated";
type NextListener = (payload: Record<string, unknown>) => void;
type NextPlugin<T> = (next: typeof Next) => T;

class Next {
  static #context: Record<string, unknown> = {};
  static #listeners: Map<NextEvent, Set<NextListener>> = new Map();

  static get context(): Readonly<Record<string, unknown>> {
    return Object.freeze({ ...Next.#context });
  }

  static _init(context: Record<string, unknown>): void {
    Next.#context = context;
    Next.#dispatch("context-updated", context);
    Next.#dispatch("ready", context);
  }

  static on(event: NextEvent, listener: NextListener): () => void {
    let bucket = Next.#listeners.get(event);
    if (bucket === undefined) {
      bucket = new Set();
      Next.#listeners.set(event, bucket);
    }
    bucket.add(listener);
    return () => {
      bucket!.delete(listener);
    };
  }

  static use<T>(plugin: NextPlugin<T>): T {
    return plugin(Next);
  }

  static #dispatch(event: NextEvent, payload: Record<string, unknown>): void {
    const bucket = Next.#listeners.get(event);
    if (bucket === undefined) return;
    for (const listener of bucket) {
      listener(payload);
    }
  }
}

(window as unknown as { Next: typeof Next }).Next = Next;
