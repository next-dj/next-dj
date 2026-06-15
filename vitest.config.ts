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
        // fetch, the default move (native moveBefore, absent from jsdom), the
        // history seam, and the native <dialog> modality (showModal, the focus
        // trap, and the Esc/backdrop/dialog-form dismiss gestures jsdom does not
        // model). Each is a thin pass-through to a browser global the harness
        // mocks, so the file is excluded rather than painted with fake hits.
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
