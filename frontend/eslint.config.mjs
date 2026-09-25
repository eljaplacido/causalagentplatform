// Next.js's own rule sets (core-web-vitals + typescript), through the flat-config
// compatibility layer ESLint 9 requires. Adding this file is what lets the gate's
// frontend-eslint step run at all: without it, eslint 9 exits before inspecting a file,
// and the step was reported SKIPPED -- never measured -- from the day it was declared.
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

export default [
  { ignores: [".next/**", "node_modules/**", "coverage/**", "next-env.d.ts"] },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
];
