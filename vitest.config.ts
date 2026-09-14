import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["next/client/**/*.test.ts"],
    // Spies come off the shared globals (console, performance, Element.prototype)
    // after every case, so one genuine failure cannot leak a live spy into the
    // rest of the file and turn a single red into a cascade.
    restoreMocks: true,
    coverage: {
      provider: "v8",
      include: ["next/client/*.ts"],
      exclude: [
        "next/client/*.test.ts",
        // What is left in adapters.ts is a thin hand-off to a browser global jsdom
        // cannot implement, so the file is excluded rather than painted with fake hits.
        "next/client/adapters.ts",
      ],
      // A cumulative 100% across every metric, mirroring the Python core, with no buffer.
      thresholds: {
        lines: 100,
        branches: 100,
        functions: 100,
        statements: 100,
      },
    },
  },
});
