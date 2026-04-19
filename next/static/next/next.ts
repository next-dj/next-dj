class Next {
  static #context: Record<string, unknown> = {};

  static get context(): Readonly<Record<string, unknown>> {
    return Object.freeze({ ...Next.#context });
  }

  static _init(context: Record<string, unknown>): void {
    Next.#context = context;
  }
}

(window as unknown as { Next: typeof Next }).Next = Next;
