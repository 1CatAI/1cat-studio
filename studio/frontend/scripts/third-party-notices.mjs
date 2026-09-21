// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

// npm sometimes omits license files present in the canonical repository.
// The supplemental copies record their origin in third_party/frontend/sources.json.
const supplemental = {
  "react-remove-scroll-bar": "react-remove-scroll-bar",
  "remark-math": "remark-math",
  "rehype-katex": "remark-math",
  "victory-vendor": "victory-vendor",
};
async function licenseFiles(folder, depth = 0) {
  const result = [];
  for (const entry of await readdir(folder, { withFileTypes: true })) {
    if (entry.isFile() && /^(licen[cs]e|copying|notice)([.-]|$)/i.test(entry.name)) {
      result.push(path.join(folder, entry.name));
    } else if (entry.isDirectory() && depth < 2 && entry.name !== "node_modules") {
      result.push(...await licenseFiles(path.join(folder, entry.name), depth + 1));
    }
  }
  return result;
}

export async function writeThirdPartyNotices() {
  const lock = JSON.parse(await readFile("package-lock.json", "utf8"));
  const sections = ["1Cat Studio frontend third-party notices\n\nThese components retain their own licenses.\nDOMPurify is used under its Apache-2.0 option.\n"];
  const seen = new Set();
  for (const [folder, entry] of Object.entries(lock.packages)) {
    if (!folder || entry.dev) continue;
    let pkg;
    try { pkg = JSON.parse(await readFile(path.join(folder, "package.json"), "utf8")); }
    catch (error) { if (error.code === "ENOENT" && entry.optional) continue; throw error; }
    if (pkg.version !== entry.version) throw new Error(`Install the locked version of ${pkg.name}`);
    const identity = `${pkg.name}@${pkg.version}`;
    if (seen.has(identity)) continue;
    seen.add(identity);
    const files = await licenseFiles(folder);
    const texts = await Promise.all(files.map(file => readFile(file, "utf8")));
    if (supplemental[pkg.name]) texts.push(await readFile(`../third_party/frontend/${supplemental[pkg.name]}.LICENSE`, "utf8"));
    if (!texts.length && pkg.name.startsWith("@radix-ui/")) {
      texts.push(await readFile("node_modules/radix-ui/LICENSE", "utf8"));
    }
    if (!texts.length) throw new Error(`Missing third-party license for ${identity}`);
    sections.push(`${identity}\nLicense: ${pkg.license ?? entry.license ?? "See notice"}\n\n${[...new Set(texts)].join("\n\n")}`);
  }
  await writeFile("public/third-party-notices.txt", sections.join("\n\n--------------------\n\n"));
  console.log(`Retained license notices for ${seen.size} frontend dependencies`);
}
