// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { build } from "esbuild";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { writeThirdPartyNotices } from "./third-party-notices.mjs";
const require = createRequire(import.meta.url);
await mkdir("public", { recursive: true });
await writeThirdPartyNotices();
await writeFile("public/license.txt", await readFile("../../LICENSE", "utf8"));
const result = await build({
  entryPoints: ["src/onecat/preview-runtime.ts"],
  bundle: true,
  write: false,
  format: "iife",
  platform: "browser",
  target: "es2022",
  minify: true,
  define: { "process.env.NODE_ENV": '"production"' },
  legalComments: "inline",
});
const tailwind = await readFile(
  require.resolve("@tailwindcss/browser"),
  "utf8",
);
await mkdir("public/preview", { recursive: true });
await writeFile(
  "public/preview/runtime.js",
  result.outputFiles[0].text + "\n" + tailwind,
);
