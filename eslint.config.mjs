import eslint from "@eslint/js";
import tseslint from "typescript-eslint";
import vitest from "@vitest/eslint-plugin";
import prettier from "eslint-config-prettier";

const TS_SOURCES = ["next/static/next/*.ts", "vitest.config.ts", "eslint.config.mjs"];

export default tseslint.config(
  {
    ignores: [
      "node_modules/**",
      ".venv/**",
      ".uv-cache/**",
      "htmlcov/**",
      "dist/**",
      "docs/_build/**",
      "**/*.min.js",
    ],
  },
  {
    files: TS_SOURCES,
    extends: [eslint.configs.recommended, tseslint.configs.recommended, prettier],
  },
  {
    files: ["next/static/next/*.test.ts"],
    plugins: { vitest },
    rules: vitest.configs.recommended.rules,
  },
);
