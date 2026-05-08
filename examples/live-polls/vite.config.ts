import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { globSync } from "glob";
import path from "path";

// Discover every co-located Vue file in the polls page tree.
const entries = Object.fromEntries(
  globSync("polls/**/!(*test).vue", { cwd: __dirname }).map((file) => [
    path.relative(".", file).replace(".vue", ""),
    path.resolve(__dirname, file),
  ]),
);

export default defineConfig({
  plugins: [vue()],
  root: path.resolve(__dirname),
  build: {
    outDir: "polls/static/polls/dist",
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
