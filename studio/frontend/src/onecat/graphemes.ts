// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
// Streamdown 2.5 animates code points. Keep joined emoji and combining sequences
// in one shaping/animation box while retaining its incremental timing logic.
type Node = {
  type: string;
  tagName?: string;
  value?: string;
  children?: Node[];
  properties?: Record<string, unknown>;
};
export function graphemePlugin() {
  const segments = new Intl.Segmenter(undefined, { granularity: "grapheme" });
  function visit(node: Node) {
    if (
      !node.children ||
      ["code", "pre", "math", "svg"].includes(node.tagName || "")
    )
      return;
    node.children = node.children.flatMap((child) => {
      if (child.type !== "text" || !child.value) {
        visit(child);
        return [child];
      }
      const parts: Node[] = [];
      let plain = "";
      for (const { segment } of segments.segment(child.value)) {
        if ([...segment].length <= 1) {
          plain += segment;
          continue;
        }
        if (plain) {
          parts.push({ type: "text", value: plain });
          plain = "";
        }
        parts.push({
          type: "element",
          tagName: "span",
          properties: { className: ["oc-grapheme"] },
          children: [{ type: "text", value: segment }],
        });
      }
      if (plain) parts.push({ type: "text", value: plain });
      return parts;
    });
  }
  return (tree: unknown) => visit(tree as Node);
}
