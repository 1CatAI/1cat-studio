// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import { setImmediate } from "node:timers/promises";
import { latestRun, observeRun, type ChatRun } from "../src/onecat/chat-run.ts";
import type { Message } from "../src/onecat/api.ts";

const run: ChatRun = { id: "run-1", thread_id: "chat", message_id: "answer", state: "running", started_at: 1, updated_at: 1 };
const message: Message = { id: "answer", threadId: "chat", parentId: "user", role: "assistant", createdAt: 1,
  content: [{ type: "text", text: "已完成🙂" }], metadata: { generation_state: "running" } };
const encode = (event: unknown) => new TextEncoder().encode("data: " + JSON.stringify(event) + "\n\n");

function fixture(t: TestContext) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let state = { run, message: null as Message | null };
  let controller: ReadableStreamDefaultController<Uint8Array>;
  let cancelled = false, checks = 0, connections = 0;
  let statusCode = 200;
  const observed: { message: Message; run: ChatRun }[] = [];
  const abort = new AbortController();
  t.mock.method(globalThis, "fetch", async (url: string) => {
    if (url.endsWith("/generation")) { checks++; return Response.json(state, { status: statusCode }); }
    assert(url.endsWith("/events"));
    connections++;
    return new Response(new ReadableStream<Uint8Array>({
      start(c) { controller = c; c.enqueue(encode({ onecat_snapshot: { run, message } })); },
      cancel() { cancelled = true; },
    }));
  });
  const observing = observeRun("chat", run.id, abort.signal, (message, run) => observed.push({ message, run }), () => {});
  return { observed, abort, observing, checks: () => checks, connections: () => connections, cancelled: () => cancelled,
    send: (event: unknown) => controller.enqueue(encode(event)),
    close: () => controller.close(),
    done: () => controller.enqueue(new TextEncoder().encode("data: [DONE]\n\n")),
    state: (value: typeof state) => { state = value; },
    statusCode: (value: number) => { statusCode = value; },
  };
}

test("a terminal message settles without DONE or an HTTP close", async t => {
  const f = fixture(t);
  await setImmediate();
  f.send({ onecat_message: { ...message, metadata: { usage: { completion_tokens: 44899 } } }, onecat_run: { ...run, state: "completed" } });
  await f.observing;
  assert.equal(f.observed.at(-1)?.run.state, "completed");
  assert.deepEqual(f.observed.at(-1)?.message.metadata?.usage, { completion_tokens: 44899 });
  assert(f.cancelled());
  assert.equal(f.checks(), 0);
});

for (const terminal of ["completed", "cancelled", "failed"] as const) {
  test(`durable ${terminal} state repairs a stream whose final event never arrives`, async t => {
    const f = fixture(t);
    await setImmediate();
    const final = { ...message, content: [{ type: "text", text: "完整回复🙂" }], metadata: {
      generation_state: terminal, usage: { completion_tokens: 44899 }, timing: { decode_tokens_s: 172.269, pending: false },
    } };
    f.state({ run: { ...run, state: terminal }, message: final });
    t.mock.timers.tick(2000);
    await f.observing;
    assert.deepEqual(f.observed.at(-1), { run: { ...run, state: terminal }, message: final });
    await setImmediate();
    assert(f.cancelled());
    const count = f.checks();
    t.mock.timers.tick(10000);
    await setImmediate();
    assert.equal(f.checks(), count);
  });
}

test("long silent reasoning is still running, and aborting the observer never cancels generation", async t => {
  const f = fixture(t);
  let settled = false;
  void f.observing.then(() => { settled = true; });
  for (let i = 0; i < 5; i++) { t.mock.timers.tick(2000); await setImmediate(); }
  assert(f.checks() >= 4);
  assert.equal(settled, false);
  assert(f.observed.every(({ run }) => run.state === "running"));
  f.abort.abort();
  await f.observing;
  await setImmediate();
  assert(f.cancelled());
});

test("an incomplete DONE reconnects instead of inventing a completed state", async t => {
  const f = fixture(t);
  await setImmediate();
  f.done();
  await setImmediate();
  assert(f.observed.every(({ run }) => run.state === "running"));
  t.mock.timers.tick(1000);
  await setImmediate();
  assert.equal(f.connections(), 2);
  f.send({ onecat_message: message, onecat_run: { ...run, state: "completed" } });
  await f.observing;
  assert.equal(f.observed.at(-1)?.run.state, "completed");
});

test("a replacement generation never publishes into the previous answer", async t => {
  const f = fixture(t);
  const rejected = assert.rejects(f.observing, /Generation changed/);
  await setImmediate();
  f.state({ run: { ...run, id: "replacement", state: "completed", started_at: 2 }, message: { ...message, id: "new-answer" } });
  t.mock.timers.tick(2000);
  await rejected;
  assert(f.observed.every(({ run }) => run.id === "run-1"));
});

test("a temporary status failure preserves the stream and recovers on the next check", async t => {
  const f = fixture(t);
  let settled = false;
  void f.observing.then(() => { settled = true; });
  f.statusCode(503);
  t.mock.timers.tick(2000);
  await setImmediate();
  assert.equal(settled, false);
  f.send({ choices: [{ delta: { content: "继续输出" } }] });
  await setImmediate();
  assert.equal(f.observed.at(-1)?.message.content[0].text, "已完成🙂继续输出");
  f.statusCode(200);
  f.state({ run: { ...run, state: "completed" }, message });
  t.mock.timers.tick(2000);
  await f.observing;
  assert.equal(f.observed.at(-1)?.run.state, "completed");
});

test("stale polling cannot replace a newer run or resurrect its running state", () => {
  const completed: ChatRun = { ...run, state: "completed", updated_at: 3 };
  const newer: ChatRun = { ...run, id: "run-2", started_at: 4, updated_at: 4 };
  assert.equal(latestRun("chat", completed, run), completed);
  assert.equal(latestRun("chat", run, completed), completed);
  assert.equal(latestRun("chat", newer, completed, run), newer);
  assert.equal(latestRun("chat", run, { ...newer, thread_id: "elsewhere" }), run);
  assert.equal(latestRun(undefined, run), undefined);
});
