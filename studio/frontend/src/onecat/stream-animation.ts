// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { createAnimatePlugin } from "streamdown";

type Node = {
  type: string;
  value?: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: Node[];
};
export const softAnimation = {
  animation: "blurIn",
  duration: 280,
  easing: "cubic-bezier(.2,.7,.2,1)",
  sep: "char" as const,
  stagger: 2,
};

// Retain Streamdown's token-prefix accounting and animation. Flatten expired
// animation spans before React sees them so old text costs ordinary text nodes.
export function blockAnimation(sep: "char" | "word" = "char") {
  const native = createAnimatePlugin({ ...softAnimation, sep });
  let settled = false;
  function compact(node: Node) {
    if (!node.children) return;
    const children: Node[] = [];
    for (let child of node.children) {
      compact(child);
      if (
        child.tagName === "span" &&
        child.properties?.["data-sd-animate"] &&
        (settled || /--sd-duration:0ms/.test(String(child.properties.style)))
      ) {
        child = {
          type: "text",
          value: child.children?.map((part) => part.value || "").join("") || "",
        };
      }
      const previous = children.at(-1);
      if (child.type === "text" && previous?.type === "text")
        previous.value = (previous.value || "") + (child.value || "");
      else children.push(child);
    }
    node.children = children;
  }
  const original = native.rehypePlugin;
  const rehypePlugin = () => {
    const transform = (original as () => (tree: unknown) => void)();
    return (tree: unknown) => {
      transform(tree);
      compact(tree as Node);
    };
  };
  // Streamdown keys its processor cache by function.name. Keep the native
  // unique name; sharing this wrapper's name would share another block's state.
  Object.defineProperty(rehypePlugin, "name", {
    value: (original as Function).name,
  });
  return {
    ...native,
    settle(value: boolean) {
      settled = value;
    },
    rehypePlugin: rehypePlugin as typeof original,
  };
}
