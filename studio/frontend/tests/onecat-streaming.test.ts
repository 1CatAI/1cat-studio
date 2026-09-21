import { strict as assert } from "node:assert";
import { test } from "node:test";
import { blockAnimation } from "../src/onecat/stream-animation.ts";
import { streamPaint } from "../src/onecat/stream-paint.ts";

test("settled text is compact and only newly arrived text animates", () => {
  const plugin = blockAnimation();
  assert.notEqual((plugin.rehypePlugin as Function).name, (blockAnimation().rehypePlugin as Function).name);
  const transform = (plugin.rehypePlugin as () => (tree: unknown) => void)();
  function render(value: string, settled: boolean) {
    plugin.setPrevContentLength(plugin.getLastRenderCharCount());
    plugin.settle(settled);
    const tree = { type: "root", children: [{ type: "text", value }] };
    transform(tree);
    return tree as any;
  }
  const original = "已经显示的文字 ".repeat(1000);
  const initial = render(original, true);
  assert.equal(initial.children.length, 1);
  assert.equal(initial.children[0].value, original);
  const updated = render(original + "🌊新文字", false);
  const spans = updated.children.filter((n: any) => n.type === "element");
  assert.equal(spans.map((n: any) => n.children[0].value).join(""), "🌊新文字");
  assert(spans.every((n: any) => n.properties.style.includes("--sd-duration:280ms")));
  const settled = render(original + "🌊新文字", true);
  assert.equal(settled.children.length, 1);
  assert.equal(settled.children[0].value, original + "🌊新文字");
});

test("paint batching preserves all streamed text and flushes the final update", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const frames = new Map<number, FrameRequestCallback>();
  let next = 0;
  globalThis.requestAnimationFrame = (callback) => { frames.set(++next, callback); return next; };
  globalThis.cancelAnimationFrame = (id) => { frames.delete(id); };
  let received = "";
  const painted: string[] = [];
  const paint = streamPaint(() => painted.push(received));
  for (let i = 0; i < 100; i++) { received += "文字🌊"; paint.schedule(); }
  assert.equal(painted.length, 0);
  t.mock.timers.tick(50);
  assert.equal(frames.size, 1);
  [...frames.values()][0](50);
  assert.deepEqual(painted, [received]);
  received += "结尾";
  paint.schedule();
  paint.flush();
  assert.deepEqual(painted, [received.slice(0, -2), received]);
  t.mock.timers.tick(1000);
  assert.equal(frames.size, 0);
});
