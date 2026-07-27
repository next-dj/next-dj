import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { globSync } from "glob";
import path from "path";

// Discover all co-located JSX files in the kanban page tree.
const entries = Object.fromEntries(
  globSync("kanban/**/!(*test).jsx", { cwd: __dirname }).map((file) => [
    path.relative(".", file).replace(".jsx", ""),
    path.resolve(__dirname, file),
  ]),
);

export default defineConfig({
  plugins: [react()],
  root: path.resolve(__dirname),
  // A typed route segment is spelled [int:id], and the dev server refuses any
  // path holding a colon before it consults server.fs.allow. vitest.config.ts
  // carries the same flag.
  server: { fs: { strict: false } },
  build: {
    outDir: "kanban/static/kanban/dist",
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: entries,
      output: {
        format: "es",
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
      },
    },
  },
});
