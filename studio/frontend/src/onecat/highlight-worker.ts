// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { createCodePlugin, type HighlightOptions, type HighlightResult } from "@streamdown/code";

const highlighter = createCodePlugin();
self.onmessage = ({ data }: MessageEvent<{ id: number; options: HighlightOptions }>) => {
  const reply = (result: HighlightResult) => self.postMessage({ id: data.id, result });
  const cached = highlighter.highlight(data.options, reply);
  if (cached) reply(cached);
};
