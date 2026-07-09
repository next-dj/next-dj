import eslint from "@eslint/js";
import tseslint from "typescript-eslint";
import vitest from "@vitest/eslint-plugin";
import prettier from "eslint-config-prettier";

const TS_SOURCES = ["next/client/*.ts", "vitest.config.ts", "eslint.config.mjs"];

export default tseslint.config(
  {
    ignores: [
      "node_modules/**",
      ".venv/**",
      ".uv-cache/**",
      "htmlcov/**",
      "**/coverage/**",
      "dist/**",
      "docs/_build/**",
      "**/*.min.js",
    ],
  },
  {
    files: TS_SOURCES,
    extends: [
      eslint.configs.recommended,
      tseslint.configs.strictTypeChecked,
      tseslint.configs.stylisticTypeChecked,
      prettier,
    ],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "@typescript-eslint/switch-exhaustiveness-check": "error",
      "@typescript-eslint/consistent-type-imports": "error",
      // House idiom: `() => void expr` shorthand for fire-and-forget seams.
      "@typescript-eslint/no-confusing-void-expression": "off",
      "@typescript-eslint/restrict-template-expressions": [
        "error",
        { allowNumber: true },
      ],
      // The window-exposed Next API is a deliberate static-only class.
      "@typescript-eslint/no-extraneous-class": ["error", { allowStaticOnly: true }],
      // Morph hooks return `boolean | void` so implicit-void callbacks fit.
      "@typescript-eslint/no-invalid-void-type": "off",
    },
  },
  {
    files: ["eslint.config.mjs"],
    extends: [tseslint.configs.disableTypeChecked],
  },
  {
    files: ["next/client/*.test.ts"],
    plugins: { vitest },
    rules: {
      ...vitest.configs.recommended.rules,
      // Tests assert presence with !, stub seams with empty and awaitless
      // async functions, and reach into internals the unsafe family flags.
      "@typescript-eslint/no-non-null-assertion": "off",
      "@typescript-eslint/require-await": "off",
      "@typescript-eslint/no-empty-function": "off",
      "@typescript-eslint/no-unnecessary-condition": "off",
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-argument": "off",
      "@typescript-eslint/no-unsafe-member-access": "off",
      "@typescript-eslint/no-unsafe-call": "off",
      "@typescript-eslint/no-unsafe-return": "off",
    },
  },
  {
    files: ["next/client/morph.bugs.test.ts"],
    rules: {
      // The bug checklist drives a table whose assertions live in a `verify`
      // callback, so the rule is taught to count it as an assertion site.
      "vitest/expect-expect": ["error", { assertFunctionNames: ["expect", "verify"] }],
    },
  },
);
