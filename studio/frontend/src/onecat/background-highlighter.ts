// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { createCodePlugin, type CodeHighlighterPlugin, type HighlightOptions, type HighlightResult } from "@streamdown/code";

export function backgroundHighlighter(themes: HighlightOptions["themes"]): CodeHighlighterPlugin {
  const inline = createCodePlugin({ themes });
  const pending = new Map<number, { options: HighlightOptions; deliver: (result: HighlightResult) => void }>();
  let worker: Worker | undefined;
  let unavailable = false;
  let sequence = 0;

  function fallback() {
    unavailable = true;
    worker?.terminate();
    worker = undefined;
    for (const { options, deliver } of pending.values()) {
      const cached = inline.highlight(options, deliver);
      if (cached) deliver(cached);
    }
    pending.clear();
  }

  return {
    ...inline,
    highlight(options, deliver) {
      if (unavailable || !deliver) return inline.highlight(options, deliver);
      const id = ++sequence;
      pending.set(id, { options, deliver });
      try {
        if (!worker) {
          worker = new Worker(new URL("./highlight-worker.ts", import.meta.url), { type: "module" });
          worker.onmessage = ({ data }: MessageEvent<{ id: number; result: HighlightResult }>) => {
            const request = pending.get(data.id);
            pending.delete(data.id);
            request?.deliver(data.result);
          };
          worker.onerror = fallback;
          worker.onmessageerror = fallback;
        }
        worker.postMessage({ id, options });
      } catch {
        fallback();
      }
      return null;
    },
  };
}
