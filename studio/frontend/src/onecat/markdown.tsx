// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
// Rendering uses the original Streamdown/Shiki distributions and 1Cat adapters.
import { useInterfaceMotion } from "./motion";
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
import { codeHighlighter, codeThemes, MarkdownPre } from "./markdown-code";
import { safeMarkdownUrl } from "./markdown-support";
import "katex/dist/katex.min.css";
import "streamdown/styles.css";
import { artifacts } from "./artifacts";
import { graphemePlugin } from "./graphemes";
import { blockAnimation } from "./stream-animation";
import { useCodeAnimation } from "./code-animation";
const rehype = [...Object.values(defaultRehypePlugins), graphemePlugin];
import { Button } from "@/onecat/ui";
import { useText } from "./common";
import { MarkdownBlockBoundary } from "./markdown-support";
const plugins = {
  code: codeHighlighter,
  math: createMathPlugin({ singleDollarTextMath: true }),
};
const components = {
  pre: MarkdownPre,
  a: ({ href, children, ...props }: ComponentProps<"a">) => (
    <a {...props} href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
};
function PreviewAction({ index, open }: { index: number; open: (index: number) => void }) {
  const t = useText();
  return (
    <div className="oc-code-preview-footer">
      <Button type="button" className="oc-code-preview-button" size="sm" variant="outline" onClick={() => open(index)}>
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false">
          <rect x="2.5" y="3.5" width="19" height="17" rx="3.5" stroke="currentColor" strokeWidth="1.7" />
          <path d="M3 8.5h18" stroke="currentColor" strokeWidth="1.5" />
          <circle cx="6" cy="6" r=".85" fill="currentColor" />
          <path d="M10 11.6a.55.55 0 0 1 .83-.48l4.48 2.65a.55.55 0 0 1 0 .95l-4.48 2.65a.55.55 0 0 1-.83-.48z" fill="currentColor" />
        </svg>
        {t("预览 / 运行", "Preview / Run")}
      </Button>
    </div>
  );
}
const PreviewContext = createContext({
  lookup: (_blockIndex: number): number => -1,
  lastPreview: -1,
  preview: undefined as ((index: number) => void) | undefined,
  animate: false,
  paceCode: false,
  animationSep: "char" as "char" | "word",
  initialBlocks: 0,
});
const PreviewBlock = memo(function PreviewBlock(props: BlockProps) {
  const context = useContext(PreviewContext);
  const index = context.lookup(props.index);
  const [animation] = useState(() => blockAnimation(context.animationSep));
  const initialText = useRef(props.content);
  const [settledText, setSettledText] = useState("");
  const code = /^\s*(`{3,}|~{3,})/.test(props.content);
  const arriving =
    props.index >= context.initialBlocks ||
    initialText.current !== props.content;
  const root = useRef<HTMLDivElement>(null);
  useCodeAnimation(root, code, context.paceCode && arriving && props.isIncomplete);
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
      {index >= 0 && index !== context.lastPreview && context.preview && (
        <PreviewAction index={index} open={context.preview} />
      )}
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
  const { enabled: animated } = useInterfaceMotion();
  const initialBlocks = useRef<number | undefined>(undefined);
  if (initialBlocks.current === undefined)
    initialBlocks.current = parseMarkdownIntoBlocks(text).length;
  const previewRef = useRef(onPreview);
  previewRef.current = onPreview;
  const catalog = useMemo(() => {
    const byBlock = new Map<number, number>();
    let ordinal = 0;
    if (onPreview) {
      for (const [block, content] of parseMarkdownIntoBlocks(text).entries()) {
        const found = artifacts(content);
        if (found.length) byBlock.set(block, ordinal);
        ordinal += found.length;
      }
    }
    return { byBlock, lastPreview: ordinal - 1 };
  }, [text, !!onPreview]);
  const catalogRef = useRef(catalog);
  catalogRef.current = catalog;
  const lookup = useCallback(
    (blockIndex: number) => catalogRef.current.byBlock.get(blockIndex) ?? -1,
    [],
  );
  const preview = useCallback(
    (index: number) => previewRef.current?.(index),
    [],
  );
  const animate = (streaming || tailAnimating) && animated;
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
      lastPreview: catalog.lastPreview,
      preview: onPreview ? preview : undefined,
      animate,
      paceCode: streaming && animated,
      animationSep,
      initialBlocks: initialBlocks.current!,
    }),
    [lookup, catalog.lastPreview, preview, !!onPreview, animate, streaming, animated, animationSep],
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
        shikiTheme={codeThemes}
        urlTransform={urlTransform}
        components={components}
      >
        {text}
      </Streamdown>
      {onPreview && catalog.lastPreview >= 0 && (
        <PreviewAction key={catalog.lastPreview} index={catalog.lastPreview} open={preview} />
      )}
    </PreviewContext.Provider>
  );
});
