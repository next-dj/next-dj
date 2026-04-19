import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["next/static/next/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["next/static/next/*.ts"],
      exclude: ["next/static/next/*.test.ts"],
    },
  },
});
