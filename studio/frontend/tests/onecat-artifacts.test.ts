import { strict as assert } from "node:assert";
import { test } from "node:test";
import { modelLabel } from "../src/onecat/model-label.ts";
import { artifacts, previewArtifact } from "../src/onecat/artifacts.ts";

test("multiline JSX is not truncated at a line boundary", () => {
  const code =
    "import React from 'react';\nexport default function App(){\nreturn <div>中文 👨‍👩‍👧</div>\n}";
  const items = artifacts(
    "Here is the example:\n```tsx\n" + code + "\n```\nDone.",
  );
  assert.equal(items[0].source, code);
  assert.equal(items[0].complete, true);
});
test("incomplete streaming fences keep the entire current source", () => {
  const items = artifacts(
    "```jsx\nexport default function App(){\nreturn <div>",
  );
  assert.equal(items[0].complete, false);
  assert.ok(items[0].source.includes("return <div>"));
});
test("HTML and adjacent CSS/JS combine without merging the next page", () => {
  const items = artifacts(
    "```html\n<div id='a'>A</div>\n```\n```css\n#a{color:red}\n```\n```js\ndocument.querySelector('#a').onclick=()=>{};\n```\n```html\n<h1>B</h1>\n```",
  );
  const page = previewArtifact(items, 1)!;
  assert.ok(page.source.includes("<style>#a{color:red}</style>"));
  assert.ok(page.source.includes("<script>"));
  assert.ok(!page.source.includes("<h1>B</h1>"));
  assert.equal(previewArtifact(items, 3)!.source, "<h1>B</h1>");
});

test("short model names preserve weight quantization when reused in presets", () => {
  const name = modelLabel("Qwen3.8-27B-NVFP4-e5m2-unit-kv-bf16-lmhead");
  assert.equal(name, "Qwen3.8-27B · NVFP4");
  assert.equal(modelLabel({ name, quantization: null }), name);
  assert.equal(modelLabel("Qwen3.8-27B · NVFP4 · 省电"), name);
});
