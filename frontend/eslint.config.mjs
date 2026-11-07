// Next.js's own rule sets (core-web-vitals + typescript). Since eslint-config-next 16
// they ship as native flat configs, so the FlatCompat layer this file used under 15 is
// gone (eslint-config-next 16 through FlatCompat crashed eslint: "Converting circular
// structure to JSON", plugins.react). Adding this file is what lets the gate's
// frontend-eslint step run at all: without it, eslint 9 exits before inspecting a file,
// and the step was reported SKIPPED -- never measured -- from the day it was declared.
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default [
  { ignores: [".next/**", "node_modules/**", "coverage/**", "next-env.d.ts"] },
  ...nextVitals,
  ...nextTs,
];
