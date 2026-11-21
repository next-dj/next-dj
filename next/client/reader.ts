import type { BeforeApply } from "./apply";
import { createChunks, drain } from "./boundary";
import type { Chunk, ChunkHost, Take } from "./boundary";
import type { PartialSurface } from "./partial";
import { ATTR_ERROR } from "./protocol";
import type { Outcome } from "./wire";

export interface ReaderHost {
  partial: ChunkHost & Pick<PartialSurface, "_reader" | "_now">;
  on(event: "ready", listener: () => void): () => void;
}

const BREAK = /\r\n?|\n/;

export function createReader(
  next: ReaderHost,
  listening: AddEventListenerOptions = {},
): void {
  const { partial } = next;
  let streams = 0;
  let sweep: Take | undefined;

  next.on("ready", () => {
    sweep ??= drain(partial, () => streams > 0, listening);
  });

  partial._reader = async (response, { signal, open, land }) => {
    const body = (
      response.body ??
      new ReadableStream<Uint8Array<ArrayBuffer>>({ start: (ended) => ended.close() })
    ).getReader();
    const decoder = new TextDecoder();
    const grabbed: BeforeApply[] = [];
    const grab = (event: Event): number =>
      grabbed.push((event as CustomEvent<BeforeApply>).detail);
    const filled: string[] = [];
    let idle: () => void;
    const finished = new Promise<void>((resolve) => (idle = () => resolve()));
    let own: BeforeApply | undefined;
    let outcome: Outcome | undefined;
    let since = 0;
    let ended = false;
    let cr = false;
    let rest = "";
    let name = "";
    let data = "";
    const dead = (): boolean =>
      signal.aborted &&
      (outcome === undefined || (signal.reason as Error).name !== "TimeoutError");
    const stop = (): void => {
      void body.cancel().catch(() => undefined);
      if (dead()) idle();
    };
    const mine = (): Element[] => own?.boundaries ?? [];
    const stale = (el: Element): boolean =>
      dead() || partial._busy(el) || partial._stamped(el) > since;
    const take = createChunks(partial, {
      stale,
      scope: () => mine().filter((el) => !stale(el)),
      live: () => !dead(),
      settled: (boundaries, status) => {
        if (status !== "dropped") filled.push(...boundaries);
      },
      idle: () => idle(),
    });
    signal.addEventListener("abort", stop);
    streams += 1;
    try {
      for (let done = false; !done && !ended; ) {
        const read = await body.read().catch((error: unknown) => {
          if (outcome === undefined) throw error;
          return { done: true as const, value: undefined };
        });
        if (dead()) throw signal.reason as Error;
        open();
        done = read.done;
        let text = decoder.decode(read.value, { stream: !done });
        if (cr && text.startsWith("\n")) text = text.slice(1);
        cr = text.endsWith("\r");
        const lines = text.split(BREAK);
        const tail = lines.splice(-1).join("");
        for (const piece of lines) {
          const line = rest + piece;
          rest = "";
          const colon = line.indexOf(":");
          const field = colon < 0 ? line : line.slice(0, colon);
          const value =
            colon < 0 ? "" : line.slice(colon + (line[colon + 1] === " " ? 2 : 1));
          if (field === "event") name = value;
          if (field === "data") data += `${value}\n`;
          if (line !== "") continue;
          const [kind, frame, blank] = [name, data.slice(0, -1), data === ""];
          name = data = "";
          if (blank) continue;
          if (kind === "end") ended = true;
          if (kind !== "patch") continue;
          let parsed: Chunk;
          try {
            parsed = JSON.parse(frame) as Chunk;
          } catch (error) {
            if (outcome) continue;
            return await land(frame, () => {
              throw error;
            });
          }
          if (outcome !== undefined) take(parsed);
          else {
            document.addEventListener("partial:before-apply", grab);
            const landing = land(frame, () => parsed.e);
            document.removeEventListener("partial:before-apply", grab);
            const landed = await landing;
            outcome = landed;
            own = grabbed.find(({ envelope }) => envelope === landed.envelope);
            if (landed.status !== "ok") return landed;
            since = partial._now();
          }
        }
        rest += tail;
      }
      if (outcome === undefined) throw new TypeError("partial stream ended unpatched");
      take(0);
      await finished;
      if (dead()) throw signal.reason as Error;
      const left = mine()
        .filter((el) => el.getAttribute(ATTR_ERROR) === "incomplete")
        .map((el) => el.id);
      if (left.length > 0)
        partial._fire("partial:error", { kind: "incomplete", boundaries: left });
      return { ...outcome, boundaries: filled };
    } finally {
      streams -= 1;
      signal.removeEventListener("abort", stop);
      take(0);
      stop();
      sweep?.();
    }
  };
}
