// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import assert from "node:assert/strict";
import test from "node:test";
import { conversationLocation, conversationMode, conversationTarget, rememberConversation } from "../src/onecat/conversation-location.ts";

test("malformed browser storage cannot choose a different route or inject search values", () => {
  for (const value of [null, undefined, [], 7, "https://example.com", { last: "/settings", chat: null, agent: [1] }]) {
    assert.equal(conversationTarget(value).to, "/chat");
    assert.equal(conversationLocation(value).chat.thread, undefined);
  }
  assert.deepEqual(conversationLocation({ last: "agent", chat: { thread: {} }, agent: { task: 9, source: true } }),
    { last: "agent", chat: { thread: undefined }, agent: { task: undefined, source: undefined } });
});

test("chat and Agent remember independent positions and sidebar returns to the last mode", () => {
  let saved = rememberConversation(null, "/chat", { thread: "chat-a" });
  saved = rememberConversation(saved, "/agent", { task: "task-b" });
  saved = rememberConversation(saved, "/models", {});
  assert.deepEqual(conversationTarget(saved), { to: "/agent", search: { task: "task-b", source: undefined } });
  assert.deepEqual(conversationTarget(saved, "chat"), { to: "/chat", search: { thread: "chat-a" } });
  assert.equal(conversationMode("/models"), undefined);
});

test("new conversations clear stale IDs and deep links/back navigation override storage", () => {
  let saved = rememberConversation(null, "/chat", { thread: "old" });
  saved = rememberConversation(saved, "/agent", { task: "old-task", source: "old-source" });
  saved = rememberConversation(saved, "/chat", {});
  assert.deepEqual(conversationTarget(saved), { to: "/chat", search: { thread: undefined } });
  saved = rememberConversation(saved, "/agent", {});
  assert.deepEqual(conversationTarget(saved), { to: "/agent", search: { task: undefined, source: undefined } });
  saved = rememberConversation(saved, "/chat", { thread: "back-target" });
  assert.deepEqual(conversationTarget(saved), { to: "/chat", search: { thread: "back-target" } });
});

test("explicit chat handoff survives switching modes without copying it to ordinary new tasks", () => {
  let saved = rememberConversation(null, "/agent", { source: "chat-context" });
  saved = rememberConversation(saved, "/chat", { thread: "another-chat" });
  assert.deepEqual(conversationTarget(saved, "agent"), { to: "/agent", search: { task: undefined, source: "chat-context" } });
  saved = rememberConversation(saved, "/agent", { task: "created" });
  assert.deepEqual(conversationTarget(saved).search, { task: "created", source: undefined });
  assert.deepEqual(rememberConversation(saved, "/agent", { task: "created" }), saved);
});
