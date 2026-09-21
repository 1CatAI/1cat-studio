// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
// Rendering uses the original Streamdown/Shiki distributions and 1Cat adapters.
import {
  memo,
  useEffect,
  useState,
  useRef,
  useMemo,
  useCallback,
  createContext,
  useContext,
  type ComponentProps,
} from "react";
import {
  Streamdown,
  Block,
  defaultRehypePlugins,
  type BlockProps,
  parseMarkdownIntoBlocks,
} from "streamdown";
import { createMathPlugin } from "@streamdown/math";
import { createCodePlugin } from "@streamdown/code";
import {
  studioDarkTheme,
  studioLightTheme,
} from "./markdown-support";
import { safeMarkdownUrl } from "./markdown-support";
import "katex/dist/katex.min.css";
import "streamdown/styles.css";
import { artifacts } from "./artifacts";
import { graphemePlugin } from "./graphemes";
import { blockAnimation } from "./stream-animation";
import { useCodeAnimation } from "./code-animation";
const rehype = [...Object.values(defaultRehypePlugins), graphemePlugin];
import { Button } from "@/onecat/ui";
import { Play } from "lucide-react";
import { useText } from "./common";
import { MarkdownBlockBoundary } from "./markdown-support";
const plugins = {
  code: createCodePlugin({ themes: [studioLightTheme, studioDarkTheme] }),
  math: createMathPlugin({ singleDollarTextMath: true }),
};
const shikiTheme: [typeof studioLightTheme, typeof studioDarkTheme] = [
  studioLightTheme,
  studioDarkTheme,
];
const links = {
  a: ({ href, children, ...props }: ComponentProps<"a">) => (
    <a {...props} href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
};
const PreviewContext = createContext({
  lookup: (_blockIndex: number): number => -1,
  preview: undefined as ((index: number) => void) | undefined,
  animate: false,
  animationSep: "char" as "char" | "word",
  initialBlocks: 0,
});
const PreviewBlock = memo(function PreviewBlock(props: BlockProps) {
  const t = useText();
  const context = useContext(PreviewContext);
  const item = useMemo(
    () => (context.preview ? artifacts(props.content)[0] : undefined),
    [props.content, !!context.preview],
  );
  const index = item ? context.lookup(props.index) : -1;
  const [animation] = useState(() => blockAnimation(context.animationSep));
  const initialText = useRef(props.content);
  const [settledText, setSettledText] = useState("");
  const code = /^\s*(`{3,}|~{3,})/.test(props.content);
  const arriving =
    props.index >= context.initialBlocks ||
    initialText.current !== props.content;
  const root = useRef<HTMLDivElement>(null);
  useCodeAnimation(root, code, context.animate && arriving);
  const animate =
    context.animate && !code && settledText !== props.content && arriving;
  animation.settle(!animate);
  useEffect(() => {
    if (!animate) return;
    const timer = setTimeout(() => setSettledText(props.content), 350);
    return () => clearTimeout(timer);
  }, [props.content, animate]);
  const blockPlugins = useMemo(
    () => [...(props.rehypePlugins || []), animation.rehypePlugin],
    [props.rehypePlugins, animation, animate],
  );
  return (
    <div className="oc-markdown-block" ref={root}>
      {item && index >= 0 && context.preview && (
        <Button
          className="oc-code-preview-button"
          size="sm"
          variant="outline"
          onClick={() => context.preview?.(index)}
        >
          <Play size={14} />
          {t("预览 / 运行", "Preview / Run")}
        </Button>
      )}
      <MarkdownBlockBoundary content={props.content}>
        <Block
          // Streamdown's paragraph memo compares source positions, not changed
          // animation markup. Settle this text block once so expired spans leave
          // the DOM; its wrapper and all code-fence controls retain their identity.
          key={animate ? "arriving" : "settled"}
          {...props}
          animatePlugin={animation}
          rehypePlugins={blockPlugins}
        />
      </MarkdownBlockBoundary>
    </div>
  );
});
export const Markdown = memo(function Markdown({
  text,
  streaming = false,
  onPreview,
  animationSep = "char",
  urlTransform = safeMarkdownUrl,
}: {
  text: string;
  streaming?: boolean;
  onPreview?: (index: number) => void;
  animationSep?: "char" | "word";
  urlTransform?: (url: string) => string | undefined;
}) {
  const [tailAnimating, setTailAnimating] = useState(streaming);
  useEffect(() => {
    if (streaming) {
      setTailAnimating(true);
      return;
    }
    const timer = setTimeout(() => setTailAnimating(false), 350);
    return () => clearTimeout(timer);
  }, [streaming]);
  const [animated, setAnimated] = useState(
    () => localStorage.getItem("onecat_stream_animation") !== "off",
  );
  const [reduced, setReduced] = useState(
    () => matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    const preference = () =>
      setAnimated(localStorage.getItem("onecat_stream_animation") !== "off");
    const motion = () => setReduced(media.matches);
    window.addEventListener("onecat:animation", preference);
    media.addEventListener("change", motion);
    return () => {
      window.removeEventListener("onecat:animation", preference);
      media.removeEventListener("change", motion);
    };
  }, []);
  const initialBlocks = useRef<number | undefined>(undefined);
  if (initialBlocks.current === undefined)
    initialBlocks.current = parseMarkdownIntoBlocks(text).length;
  const previewRef = useRef(onPreview);
  previewRef.current = onPreview;
  const catalog = useMemo(() => {
    const result = new Map<number, number>();
    if (onPreview) {
      let ordinal = 0;
      for (const [block, content] of parseMarkdownIntoBlocks(text).entries()) {
        const found = artifacts(content);
        if (found.length) result.set(block, ordinal);
        ordinal += found.length;
      }
    }
    return result;
  }, [text, !!onPreview]);
  const catalogRef = useRef(catalog);
  catalogRef.current = catalog;
  const lookup = useCallback(
    (blockIndex: number) => catalogRef.current.get(blockIndex) ?? -1,
    [],
  );
  const preview = useCallback(
    (index: number) => previewRef.current?.(index),
    [],
  );
  const animate = (streaming || tailAnimating) && animated && !reduced;
  const resolvedRehype = useMemo(() => {
    if (urlTransform === safeMarkdownUrl) return rehype;
    type Node = { properties?: Record<string, unknown>; children?: Node[] };
    const resolveUrls = () => (tree: Node) => {
      function visit(node: Node) {
        for (const key of ["href", "src"]) {
          const value = node.properties?.[key];
          if (typeof value === "string") node.properties![key] = urlTransform(value);
        }
        node.children?.forEach(visit);
      }
      visit(tree);
    };
    // Relative repository URLs must be resolved before the hardening plugin.
    return [defaultRehypePlugins.raw, defaultRehypePlugins.sanitize, resolveUrls, defaultRehypePlugins.harden, graphemePlugin];
  }, [urlTransform]);
  const context = useMemo(
    () => ({
      lookup,
      preview: onPreview ? preview : undefined,
      animate,
      animationSep,
      initialBlocks: initialBlocks.current!,
    }),
    [lookup, preview, !!onPreview, animate, animationSep],
  );
  return (
    <PreviewContext.Provider value={context}>
      <Streamdown
        BlockComponent={PreviewBlock}
        plugins={plugins}
        rehypePlugins={resolvedRehype}
        mode="streaming"
        parseIncompleteMarkdown={streaming}
        isAnimating={streaming}
        animated={false}
        shikiTheme={shikiTheme}
        urlTransform={urlTransform}
        components={links}
      >
        {text}
      </Streamdown>
    </PreviewContext.Provider>
  );
});
