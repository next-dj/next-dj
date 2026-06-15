import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["next/static/next/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["next/static/next/*.ts"],
      exclude: [
        "next/static/next/*.test.ts",
        // adapters.ts holds the DI-adapter seams: the navigation hook
        // (location.assign, unimplemented in jsdom), the clock seam, the default
        // fetch, and the default move (native moveBefore, absent from jsdom).
        // Each is a thin pass-through to a browser global the harness mocks, so
        // it is excluded rather than painted with fake hits.
        "next/static/next/adapters.ts",
      ],
      // Branches carry a small buffer below the measured floor so a single
      // defensive-default branch cannot flip the gate red.
      thresholds: {
        lines: 95,
        branches: 88,
        functions: 90,
        statements: 95,
      },
    },
  },
});
