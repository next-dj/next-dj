import { describe, expect, it } from "vitest";
import { MAX_TIMEOUT_MS, REQUEST_TIMEOUT_MS, requestTimeout } from "./protocol";

describe("requestTimeout", () => {
  it("keeps a timer-range value and zero, which turns the deadline off", () => {
    expect(requestTimeout(5000)).toBe(5000);
    expect(requestTimeout(0)).toBe(0);
    expect(requestTimeout(MAX_TIMEOUT_MS)).toBe(MAX_TIMEOUT_MS);
  });

  it.each([[undefined], ["5000"], [-1], [Number.NaN], [MAX_TIMEOUT_MS + 1]])(
    "falls back to the default for %s",
    (raw) => {
      expect(requestTimeout(raw)).toBe(REQUEST_TIMEOUT_MS);
    },
  );
});
