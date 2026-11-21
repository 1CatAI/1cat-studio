// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { createContext, useContext } from "react";
import { createPortal } from "react-dom";
import { DropdownMenu } from "radix-ui";
import { Bot, FileDown, MoreHorizontal, SlidersHorizontal, SquarePen } from "lucide-react";
import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { Button, TooltipIconButton } from "@/onecat/ui";
import { api } from "./api";
import { saveFile, useText } from "./common";

export const ChatToolsTarget = createContext<HTMLDivElement | null>(null);

type ChatToolbarProps = {
  title: string;
  threadId?: string;
  busy: boolean;
  newChatDisabled: boolean;
  reconnecting: boolean;
  onNew: () => void;
  onSettings: () => void;
  onManage: () => void;
};

export function ChatToolbar({ title, threadId, busy, newChatDisabled, reconnecting, onNew, onSettings, onManage }: ChatToolbarProps) {
  const target = useContext(ChatToolsTarget);
  const t = useText();
  const navigate = useNavigate();
  async function exportChat() {
    try {
      saveFile((title || t("对话", "Chat")) + ".json", await api(`/api/chat/threads/${threadId}/export`));
    } catch (error) {
      toast.error((error as Error).message);
    }
  }
  const actions = [
    ...(threadId ? [
      { icon: Bot, label: t("以此创建 Agent 任务", "Create an Agent task from this chat"), run: () => navigate({ to: "/agent", search: { source: threadId } }) },
      { icon: FileDown, label: t("导出对话", "Export conversation"), run: exportChat },
    ] : []),
    { icon: SlidersHorizontal, label: t("对话参数", "Chat settings"), run: onSettings },
    ...(threadId ? [{ icon: MoreHorizontal, label: t("管理对话", "Manage conversation"), run: onManage, disabled: busy }] : []),
  ];
  return (
    <>
      <header className="oc-chat-toolbar">
        <div className="oc-chat-title-slot" aria-live="polite" aria-atomic="true">
          {title && <h2 key={threadId + ":" + title} className="oc-chat-title" title={title}><span>{title}</span></h2>}
        </div>
        <Button type="button" className="oc-chat-new" onClick={onNew} disabled={newChatDisabled}>
          <SquarePen aria-hidden="true" /><span>{t("新建对话", "New chat")}</span>
        </Button>
      </header>
      {reconnecting && <span className="oc-chat-reconnect" role="status">{t("正在重新连接，后台继续生成", "Reconnecting. Generation continues in the background.")}</span>}
      {target && createPortal(
        <div className="oc-chat-tools" role="group" aria-label={t("对话工具", "Chat tools")}>
          <div className="oc-chat-secondary">
            {actions.map(({ icon: Icon, label, run, disabled }) => (
              <TooltipIconButton key={label} tooltip={label} onClick={run} disabled={disabled}>
                <Icon />
              </TooltipIconButton>
            ))}
          </div>
          <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
              <Button className="oc-chat-more" type="button" variant="ghost" size="icon-sm" aria-label={t("更多对话选项", "More chat options")}>
                <MoreHorizontal aria-hidden="true" />
              </Button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content className="oc-account-menu" align="end" sideOffset={6} collisionPadding={8}>
                {actions.map(({ icon: Icon, label, run, disabled }) => (
                  <DropdownMenu.Item key={label} onSelect={run} disabled={disabled}><Icon size={16} aria-hidden="true" />{label}</DropdownMenu.Item>
                ))}
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </div>,
        target,
      )}
    </>
  );
}
