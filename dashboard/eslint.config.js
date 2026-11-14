import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

// Same guard as @nevysha/ui: styles come from design tokens, never raw colors or arbitrary values.
const RAW_COLOR = String.raw`(#[0-9a-fA-F]{3,8}\b|\b(rgb|rgba|hsl|hsla|oklch|oklab|lab|lch)\()`;
const ARBITRARY = String.raw`\b[a-z-]+-\[[^\]]*(#|px|rem|rgb|hsl|oklch)[^\]]*\]`;
const message = "Use design tokens (bg-surface, text-text-muted, p-4, rounded-md...) instead of raw colors or arbitrary values.";
const tokenRule = [
  { selector: `Literal[value=/${RAW_COLOR}/]`, message },
  { selector: `TemplateElement[value.raw=/${RAW_COLOR}/]`, message },
  { selector: `Literal[value=/${ARBITRARY}/]`, message },
  { selector: `TemplateElement[value.raw=/${ARBITRARY}/]`, message },
];

export default tseslint.config(
  { ignores: ["node_modules"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "@typescript-eslint/consistent-type-imports": "error",
    },
  },
  { files: ["src/**/*.{ts,tsx}"], rules: { "no-restricted-syntax": ["error", ...tokenRule] } },
);
