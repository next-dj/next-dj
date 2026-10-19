import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { globSync } from "glob";
import path from "path";

const entries = Object.fromEntries(
  globSync("kanban/**/!(*test).jsx", { cwd: __dirname }).map((file) => [
    path.relative(".", file).replace(".jsx", ""),
    path.resolve(__dirname, file),
  ]),
);

export default defineConfig({
  plugins: [react()],
  root: path.resolve(__dirname),
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
