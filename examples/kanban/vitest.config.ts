import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { fs: { strict: false } },
  test: {
    globals: true,
    environment: "jsdom",
    include: ["kanban/**/*.test.{jsx,tsx}"],
    setupFiles: ["./vitest.setup.ts"],
  },
});
