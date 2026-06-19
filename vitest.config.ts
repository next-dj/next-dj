import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["next/client/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["next/client/*.ts"],
      exclude: [
        "next/client/*.test.ts",
        // adapters.ts holds the DI-adapter seams: the navigation hook
        // (location.assign, unimplemented in jsdom), the clock seam, the default
        // fetch, the default move (native moveBefore, absent from jsdom), the
        // history seam, the native <dialog> modality (showModal, the focus trap,
        // and the Esc/backdrop/dialog-form dismiss gestures jsdom does not
        // model), the IntersectionObserver geometry (jsdom reports no
        // intersections), the CSS link loader (link.onload never fires in
        // jsdom), the reload-once sessionStorage wrapper, and the confirm gate.
        // Each is a thin pass-through to a browser global the harness mocks, so
        // the file is excluded rather than painted with fake hits.
        "next/client/adapters.ts",
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
