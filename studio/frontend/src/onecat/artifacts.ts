// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
export type Artifact = {
  index: number;
  language: string;
  source: string;
  complete: boolean;
};
const supported = new Set([
  "html",
  "htm",
  "css",
  "js",
  "javascript",
  "svg",
  "jsx",
  "tsx",
  "react",
]);
export function artifacts(text: string): Artifact[] {
  const result: Artifact[] = [];
  const fence =
    /^(`{3,}|~{3,})([^\n]*)\n([\s\S]*?)(?:\n\1[ \t]*(?=\n|$)|(?![\s\S]))/gm;
  for (const match of text.matchAll(fence)) {
    const info = match[2].trim().split(/\s+/)[0].toLowerCase();
    const language = info.includes(".") ? info.split(".").at(-1)! : info;
    if (supported.has(language))
      result.push({
        index: result.length,
        language,
        source: match[3],
        complete: new RegExp(`\n${match[1]}\\s*$`).test(match[0]),
      });
  }
  return result;
}
export function previewArtifact(
  items: Artifact[],
  index: number,
): Artifact | undefined {
  const item = items[index];
  if (!item) return;
  if (["jsx", "tsx", "react", "svg"].includes(item.language)) return item;
  // Adjacent HTML/CSS/JS fences form one page; another HTML/React/SVG starts a page.
  let begin = index;
  while (
    begin > 0 &&
    !["html", "htm"].includes(items[begin].language) &&
    !["jsx", "tsx", "react", "svg"].includes(items[begin - 1].language)
  )
    begin--;
  let end = begin + 1;
  while (
    end < items.length &&
    ["css", "js", "javascript"].includes(items[end].language)
  )
    end++;
  const page = items.slice(begin, end);
  const source = page
    .map((p) =>
      p.language === "css"
        ? `<style>${p.source}</style>`
        : ["js", "javascript"].includes(p.language)
          ? `<script>${p.source}</script>`
          : p.source,
    )
    .join("\n");
  return {
    ...item,
    language: "html",
    source,
    complete: page.every((p) => p.complete),
  };
}

/** A project source file is already complete; do not reparse it as Markdown. */
export function fileArtifact(path: string, source: string): Artifact | undefined {
  const language = path.split(".").at(-1)?.toLowerCase() || "";
  if (!supported.has(language)) return;
  return previewArtifact([{ index: 0, language, source, complete: true }], 0);
}
