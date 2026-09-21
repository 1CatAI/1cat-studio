// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useEffect } from "react";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import { useBrowserState } from "./browser-state";
import { useText } from "./common";
import { Segments } from "./motion";
import {
  conversationLocation, conversationMode, conversationTarget, rememberConversation,
  type ConversationMode,
} from "./conversation-location";

export function useConversationNavigation() {
  const path = useRouterState({ select: s => s.location.pathname });
  const thread = useRouterState({ select: s => (s.location.search as { thread?: string }).thread });
  const task = useRouterState({ select: s => (s.location.search as { task?: string }).task });
  const source = useRouterState({ select: s => (s.location.search as { source?: string }).source });
  const [saved, setSaved] = useBrowserState<unknown>("onecat:conversation-location", null);
  useEffect(() => {
    if (conversationMode(path)) setSaved((previous: unknown) => rememberConversation(previous, path, { thread, task, source }));
  }, [path, thread, task, source, setSaved]);
  // Deep links take effect in this render, without waiting for persistence.
  const location = rememberConversation(saved, path, { thread, task, source });
  return { mode: conversationMode(path), location, target: conversationTarget(location) };
}

export function ConversationModes({ mode, location }: {
  mode: ConversationMode; location: ReturnType<typeof conversationLocation>;
}) {
  const t = useText(), navigate = useNavigate();
  const modes = ["chat", "agent"] as const;
  const switchMode = (next: ConversationMode) => {
    if (next !== mode) void navigate(conversationTarget(location, next));
  };
  return <Segments className="oc-conversation-modes" role="tablist" aria-label={t("对话模式", "Conversation mode")}>
    {modes.map(value => <button key={value} type="button" role="tab"
      id={`oc-mode-${value}`} aria-controls="oc-conversation-panel"
      aria-selected={mode === value} tabIndex={mode === value ? 0 : -1}
      onClick={() => switchMode(value)}
      onKeyDown={event => {
        if (event.altKey || event.ctrlKey || event.metaKey) return;
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? "chat" : event.key === "End" ? "agent" : value === "chat" ? "agent" : "chat";
        document.getElementById(`oc-mode-${next}`)?.focus();
        switchMode(next);
      }}>
      {value === "chat" ? t("聊天", "Chat") : "Agent"}
    </button>)}
  </Segments>;
}
