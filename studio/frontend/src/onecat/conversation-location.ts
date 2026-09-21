// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
export type ConversationMode = "chat" | "agent";
export type ConversationLocation = {
  last: ConversationMode;
  chat: { thread?: string };
  agent: { task?: string; source?: string };
};

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const id = (value: unknown) => typeof value === "string" && value.length ? value : undefined;

/** Browser storage is optional and untrusted; only route parameters are remembered. */
export function conversationLocation(value: unknown): ConversationLocation {
  const stored = record(value), chat = record(stored.chat), agent = record(stored.agent);
  return {
    last: stored.last === "agent" ? "agent" : "chat",
    chat: { thread: id(chat.thread) },
    agent: { task: id(agent.task), source: id(agent.source) },
  };
}

export function conversationMode(path: string): ConversationMode | undefined {
  return path === "/chat" ? "chat" : path === "/agent" ? "agent" : undefined;
}

/** The URL wins, including an empty search for a new chat/task and browser history. */
export function rememberConversation(value: unknown, path: string, search: unknown): ConversationLocation {
  const previous = conversationLocation(value), mode = conversationMode(path), params = record(search);
  if (!mode) return previous;
  return {
    ...previous,
    last: mode,
    [mode]: mode === "chat"
      ? { thread: id(params.thread) }
      : { task: id(params.task), source: id(params.source) },
  };
}

export function conversationTarget(value: unknown, mode?: ConversationMode) {
  const saved = conversationLocation(value);
  return (mode || saved.last) === "agent"
    ? { to: "/agent" as const, search: saved.agent }
    : { to: "/chat" as const, search: saved.chat };
}
