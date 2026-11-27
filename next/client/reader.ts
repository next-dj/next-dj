// The reader of streamed answers: the server-sent frames of a partial or navigation
// stream, and the `$nx` queue of a streamed document, both drained into boundaries.

import { createDrain, drain } from "./drain";
import type { DrainHost, Fill, Take } from "./drain";
import type { BeforeApply } from "./apply";
import type { Kernel } from "./kernel";
import {
  ATTR_ERROR,
  CHUNK_END,
  CHUNK_ENVELOPE,
  EVENT_END,
  EVENT_HELLO,
  EVENT_PATCH,
  PROTOCOL,
} from "./protocol";
import type { Outcome } from "./wire";

export type ReaderHost = DrainHost &
  Pick<Kernel, "now" | "release" | "serve" | "onReady">;

type Body = Pick<ReadableStreamDefaultReader<Uint8Array>, "read">;

const BREAK = /\r\n?|\n/;
const ENDED = Promise.resolve({ done: true as const, value: undefined });

/** Yield the event name and the data of each server-sent event frame of a body. */
export async function* frames(
  body: Body,
): AsyncGenerator<[event: string, data: string]> {
  const decoder = new TextDecoder();
  let cr = false;
  let rest = "";
  let name = "";
  let data = "";
  for (let done = false; !done; ) {
    const read = await body.read();
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
      if (data) yield [name, data.slice(0, -1)];
      name = data = "";
    }
    rest += tail;
  }
}

export function createReader(
  kernel: ReaderHost,
  listening: AddEventListenerOptions = {},
): void {
  let streams = 0;
  let sweep: Take | undefined;

  kernel.onReady(() => {
    sweep ??= drain(kernel, () => streams > 0, listening);
  });

  kernel.serve("read", async (response, { signal, open, land }) => {
    // A body-less answer reads as a stream that ends at once.
    const body = response.body?.getReader();
    const filled: string[] = [];
    let idle: () => void;
    const finished = new Promise<void>((resolve) => (idle = resolve));
    let own: BeforeApply | undefined;
    let outcome: Outcome | undefined;
    let since = 0;
    const dead = (): boolean =>
      signal.aborted &&
      (outcome === undefined || (signal.reason as Error).name !== "TimeoutError");
    const stop = (): void => {
      void body?.cancel().catch(() => undefined);
      if (dead()) idle();
    };
    const mine = (): Element[] => own?.boundaries ?? [];
    const stale = (el: Element): boolean =>
      dead() || kernel.busy(el) || kernel.stamped(el) > since;
    const take = createDrain(kernel, {
      stale,
      scope: () => mine().filter((el) => !stale(el)),
      live: () => !dead(),
      settled: (boundaries, status) => {
        if (status !== "dropped") filled.push(...boundaries);
      },
      idle: () => idle(),
    });
    // A failed read once the page landed only ends the stream, its fills already in.
    const read = async (): Promise<ReadableStreamReadResult<Uint8Array>> => {
      const result = await (body?.read() ?? ENDED).catch((error: unknown) => {
        if (outcome === undefined) throw error;
        return ENDED;
      });
      if (dead()) throw signal.reason as Error;
      open();
      return result;
    };
    signal.addEventListener("abort", stop);
    streams += 1;
    try {
      for await (const [kind, frame] of frames({ read })) {
        if (kind === EVENT_END) break;
        if (kind !== EVENT_PATCH && kind !== EVENT_HELLO) continue;
        let parsed: Fill & { protocol?: unknown };
        try {
          parsed = JSON.parse(frame) as Fill;
        } catch (error) {
          if (outcome === undefined)
            return await land(frame, () => {
              throw error;
            });
          kernel.fire("partial:error", { kind: "parse", body: frame, error });
          continue;
        }
        // A server of another release streams frames this runtime may misread, so the
        // stream ends unpatched and the runtime reloads once.
        if (kind === EVENT_HELLO) {
          if (parsed.protocol === PROTOCOL) continue;
          kernel.release(false);
          break;
        }
        if (outcome !== undefined) take(parsed);
        else {
          // The boundaries of the landed page are the ones the later frames fill.
          const landed = await land(
            frame,
            () => parsed[CHUNK_ENVELOPE],
            (detail: BeforeApply) => (own ??= detail),
          );
          outcome = landed;
          if (landed.status !== "ok") return landed;
          since = kernel.now();
        }
      }
      if (outcome === undefined) throw new TypeError("stream ended unpatched");
      take(CHUNK_END);
      await finished;
      if (dead()) throw signal.reason as Error;
      const left = mine()
        .filter((el) => el.getAttribute(ATTR_ERROR) === "incomplete")
        .map((el) => el.id);
      if (left.length > 0)
        kernel.fire("partial:error", { kind: "incomplete", boundaries: left });
      return { ...outcome, boundaries: filled };
    } finally {
      streams -= 1;
      signal.removeEventListener("abort", stop);
      take(CHUNK_END);
      stop();
      sweep?.();
    }
  });
}
