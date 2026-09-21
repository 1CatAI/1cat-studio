// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { readFastApiError } from "./http-error.ts";
import type { Message } from "./api";

export type ChatRun = {
  pending_commit?: boolean;
  id: string;
  thread_id: string;
  message_id: string;
  state: "running" | "completed" | "cancelled" | "failed";
  started_at: number;
  updated_at: number;
  error?: string | null;
};

/** Late list responses cannot resurrect an older run or a settled generation. */
export function latestRun(thread: string | undefined, ...candidates: (ChatRun | undefined)[]) {
  return candidates.filter((run): run is ChatRun => !!run && run.thread_id === thread)
    .reduce<ChatRun | undefined>((latest, run) => {
      if (!latest) return run;
      if (run.id === latest.id && (run.state === "running") !== (latest.state === "running"))
        return run.state === "running" ? latest : run;
      return run.started_at > latest.started_at ||
        (run.started_at === latest.started_at && run.updated_at > latest.updated_at) ? run : latest;
    }, undefined);
}

function delay(signal: AbortSignal, ms: number) {
  return new Promise<void>(resolve => {
    const finish = () => { clearTimeout(timer); signal.removeEventListener("abort", finish); resolve(); };
    const timer = setTimeout(finish, ms);
    signal.addEventListener("abort", finish, { once: true });
    if (signal.aborted) finish();
  });
}

/** Disconnecting this observer never cancels the backend generation. */
export async function observeRun(
  thread: string,
  runId: string,
  signal: AbortSignal,
  onMessage: (message: Message, run: ChatRun) => void,
  onConnection: (reconnecting: boolean) => void,
) {
  const lifetime = new AbortController();
  const abort = () => lifetime.abort();
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) abort();
  let settled = false;
  const accept = (message: Message, run: ChatRun) => {
    if (lifetime.signal.aborted || settled) return;
    if (run.id !== runId) throw Object.assign(new Error("生成已切换，请查看最新回复 / Generation changed; showing the latest reply"), { terminal: true });
    settled = run.state !== "running";
    onMessage(message, run);
  };
  async function reconcile() {
    // A healthy HTTP connection can still lose its final SSE event. Query the
    // durable run independently; silence alone never means the model is done.
    while (!lifetime.signal.aborted) {
      await delay(lifetime.signal, 2000);
      if (lifetime.signal.aborted) return;
      try {
        const response = await fetch(`/api/chat/threads/${thread}/generation`, {
          credentials: "same-origin", cache: "no-store",
          signal: AbortSignal.any([lifetime.signal, AbortSignal.timeout(5000)]),
        });
        if (!response.ok) {
          if ([401, 403, 404].includes(response.status))
            throw Object.assign(new Error(await readFastApiError(response)), { terminal: true });
          continue;
        }
        const { run, message } = await response.json() as { run: ChatRun; message: Message | null };
        if (lifetime.signal.aborted) return;
        if (run.id !== runId)
          throw Object.assign(new Error("生成已切换，请查看最新回复 / Generation changed; showing the latest reply"), { terminal: true });
        if (run.state !== "running") {
          if (!message) throw Object.assign(new Error("回复已结束，但消息不存在 / Generation ended but its message is missing"), { terminal: true });
          accept(message, run);
          return;
        }
      } catch (error) {
        if (lifetime.signal.aborted) return;
        if ((error as { terminal?: boolean }).terminal) throw error;
        // Keep the stream and partial text through transient status failures.
      }
    }
  }
  try {
    await Promise.race([
      readEvents(thread, lifetime.signal, accept, value => { if (!lifetime.signal.aborted && !settled) onConnection(value); }),
      reconcile(),
    ]);
  } finally {
    abort();
    signal.removeEventListener("abort", abort);
  }
}

async function readEvents(
  thread: string,
  signal: AbortSignal,
  onMessage: (message: Message, run: ChatRun) => void,
  onConnection: (reconnecting: boolean) => void,
) {
  while (!signal.aborted) {
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    const cancel = () => { void reader?.cancel().catch(() => {}); };
    try {
      const response = await fetch(`/api/chat/threads/${thread}/events`, {
        credentials: "same-origin", cache: "no-store", signal,
      });
      if (!response.ok) {
        const error = new Error(await readFastApiError(response));
        if (response.status >= 400 && response.status < 500 && ![408, 429].includes(response.status)) throw Object.assign(error, { terminal: true });
        throw error;
      }
      if (!response.body) throw new Error("No response stream");
      reader = response.body.getReader();
      signal.addEventListener("abort", cancel, { once: true });
      if (signal.aborted) return;
      const decoder = new TextDecoder();
      let buffer = "", answer = "", thought = "";
      let message: Message | undefined, run: ChatRun | undefined;
      onConnection(false);
      while (!signal.aborted) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        buffer = buffer.replace(/\r\n/g, "\n");
        let boundary: number;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          const event = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const data = event.split("\n").filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
          if (!data) continue;
          if (data === "[DONE]") {
            if (run && run.state !== "running") return;
            throw new Error("Completion state missing; reconnecting");
          }
          const chunk = JSON.parse(data);
          if (chunk.onecat_snapshot) {
            message = chunk.onecat_snapshot.message;
            run = chunk.onecat_snapshot.run;
            answer = message?.content.filter(p => p.type === "text").map(p => p.text || "").join("") || "";
            thought = message?.content.filter(p => p.type === "reasoning").map(p => p.text || "").join("") || "";
          } else if (chunk.onecat_message) {
            message = chunk.onecat_message;
            run = chunk.onecat_run;
            answer = message?.content.filter(p => p.type === "text").map(p => p.text || "").join("") || "";
            thought = message?.content.filter(p => p.type === "reasoning").map(p => p.text || "").join("") || "";
          } else if (message) {
            const metadata = { ...message.metadata };
            if (chunk.request_id) metadata.request_id = chunk.request_id;
            if (chunk.usage) metadata.usage = chunk.usage;
            if (chunk.onecat_metrics) metadata.timing = chunk.onecat_metrics;
            if (chunk.onecat_live_metrics) metadata.live = chunk.onecat_live_metrics;
            for (const choice of chunk.choices || []) {
              const delta = choice.delta || {};
              answer += delta.content || "";
              thought += delta.reasoning_content || delta.reasoning || "";
              if (thought && !metadata.thinking_started_at) metadata.thinking_started_at = Date.now() / 1000;
              if (answer && metadata.thinking_started_at && metadata.thinking_elapsed_s == null)
                metadata.thinking_elapsed_s = Date.now() / 1000 - Number(metadata.thinking_started_at);
              if (choice.finish_reason) metadata.finish_reason = choice.finish_reason;
            }
            message = { ...message, metadata, content: [
              ...(thought ? [{ type: "reasoning", text: thought }] : []),
              ...(answer ? [{ type: "text", text: answer }] : []),
            ] };
          }
          if (message && run) {
            onMessage(message, run);
            // The durable terminal message is sufficient. Do not wait for an
            // HTTP close or another delimiter to restore the send button.
            if (run.state !== "running") return;
          }
        }
      }
      if (run && run.state !== "running") return;
      throw new Error("Connection ended; reconnecting");
    } catch (error) {
      if (signal.aborted) return;
      if ((error as { terminal?: boolean }).terminal) throw error;
      onConnection(true);
      await delay(signal, 1000);
    } finally {
      signal.removeEventListener("abort", cancel);
      await reader?.cancel().catch(() => {});
      reader?.releaseLock();
    }
  }
}
