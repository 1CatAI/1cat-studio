// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { memo, useEffect, useMemo, useState, type ComponentProps, type CSSProperties } from "react";
import {
  CodeBlockContainer, CodeBlockCopyButton, CodeBlockDownloadButton, CodeBlockHeader,
  useIsCodeFenceIncomplete, type ExtraProps,
} from "streamdown";
import type { HighlightOptions, HighlightResult } from "@streamdown/code";
import { backgroundHighlighter } from "./background-highlighter";
import { studioLightTheme, studioDarkTheme } from "./markdown-support";

export const codeThemes: HighlightOptions["themes"] = [studioLightTheme, studioDarkTheme];
export const codeHighlighter = backgroundHighlighter(codeThemes);

type Tokens = HighlightResult["tokens"][number];

const SourceLine = memo(function SourceLine({ text, tokens, numbered }: {
  text: string; tokens?: Tokens; numbered: boolean;
}) {
  // A worker result may cover only a prefix. Its colors can decorate that
  // prefix, but it can never add, hide, or replace characters in the source.
  const prefix = tokens?.map(token => token.content).join("") ?? "";
  return (
    <span className={numbered
      ? "block before:content-[counter(line)] before:inline-block before:[counter-increment:line] before:w-6 before:mr-4 before:text-[13px] before:text-right before:text-muted-foreground/50 before:font-mono before:select-none"
      : "block"}>
      {tokens && prefix && text.startsWith(prefix) ? <>
        {tokens.map((token, i) => {
          const { color = token.color, "background-color": background = token.bgColor, ...style } = token.htmlStyle || {};
          return (
            <span key={i} className="text-[var(--sdm-c,inherit)] dark:text-[var(--shiki-dark,var(--sdm-c,inherit))] bg-[var(--sdm-tbg,transparent)] dark:bg-[var(--shiki-dark-bg,var(--sdm-tbg,transparent))]"
              style={{ ...style, "--sdm-c": color, "--sdm-tbg": background } as CSSProperties}>
              {token.content}
            </span>
          );
        })}
        {text.slice(prefix.length)}
      </> : text || "\n"}
    </span>
  );
});

function SourceCode({ source, language, meta }: { source: string; language: string; meta: string }) {
  const incomplete = useIsCodeFenceIncomplete();
  const text = source.replace(/\n+$/, "");
  const rows = useMemo(() => text.split("\n"), [text]);
  const [colors, setColors] = useState<HighlightResult>();
  useEffect(() => {
    let current = true;
    const deliver = (result: HighlightResult) => { if (current) setColors(result); };
    const cached = codeHighlighter.highlight({ code: text, language: language as HighlightOptions["language"], themes: codeThemes }, deliver);
    if (cached) deliver(cached);
    return () => { current = false; };
  }, [text, language]);
  const numbered = !/\bnoLineNumbers\b/.test(meta);
  const start = Number(meta.match(/\bstartLine=(\d+)/)?.[1]);
  return (
    <CodeBlockContainer language={language} isIncomplete={incomplete}>
      <CodeBlockHeader language={language} />
      <div className="pointer-events-none sticky top-2 z-10 -mt-10 flex h-8 items-center justify-end">
        <div data-streamdown="code-block-actions" className="pointer-events-auto flex shrink-0 items-center gap-2 rounded-md border border-sidebar bg-sidebar/80 px-1.5 py-1">
          <CodeBlockDownloadButton code={source} language={language} /><CodeBlockCopyButton code={source} />
        </div>
      </div>
      <div data-streamdown="code-block-body" data-language={language} className="overflow-x-auto rounded-md text-sm">
        <pre><code className={numbered ? "[counter-reset:line]" : undefined} style={numbered && start > 1 ? { counterReset: `line ${start - 1}` } : undefined}>
          {rows.map((row, i) => <SourceLine key={i} text={row} tokens={colors?.tokens[i]} numbered={numbered} />)}
        </code></pre>
      </div>
    </CodeBlockContainer>
  );
}

// Take the same parsed code node that Streamdown uses, so escaping, nesting
// and fence length all follow the Markdown parser.
export function MarkdownPre({ node, children, ...props }: ComponentProps<"pre"> & ExtraProps) {
  const code = node?.children[0];
  if (node?.children.length === 1 && code?.type === "element" && code.tagName === "code" && code.children.every(child => child.type === "text")) {
    const names = code.properties.className;
    const classes = Array.isArray(names) ? names.join(" ") : String(names ?? "");
    const language = classes.match(/(?:^|\s)language-(\S+)/)?.[1] || "";
    return <SourceCode language={language} meta={String(code.properties.metastring || "")}
      source={code.children.map(child => child.value).join("")} />;
  }
  return <pre {...props}>{children}</pre>;
}
