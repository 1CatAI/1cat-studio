import { CreativeModes } from "./creative/modes";
// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { Dialog, DropdownMenu } from "radix-ui";
import { motion } from "motion/react";
import {
  MessageSquare,
  Workflow,
  Boxes,
  Gauge,
  Server,
  Settings,
  Plus,
  LogOut,
  LoaderCircle,
  PanelTop,
  Upload,
  Pin,
  PinOff,
  History,
  X,
  Menu,
  UserRound,
} from "lucide-react";
import { Button } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/onecat/ui";
import { toast } from "sonner";
import {
  useQuery,
  mutation,
  refreshData,
  type Engine,
  type Thread,
  type GPU,
} from "./api";
import { Field, format, useText, Modal, jobLabel, ErrorNotice } from "./common";
import { LaunchProgress } from "./launch-progress";
import { modelLabel, accelerationLabel } from "./model-label";
import { ModelPicker } from "./model-controls";
import { AgentHistory } from "./agent-page";
import {
  ConversationModes,
  useConversationNavigation,
} from "./conversation-modes";
import { useBrowserState } from "./browser-state";
import { MOTION, useInterfaceMotion } from "./motion";

function useMedia(query: string) {
  const [matches, setMatches] = useState(() => matchMedia(query).matches);
  useEffect(() => {
    const media = matchMedia(query),
      update = () => setMatches(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [query]);
  return matches;
}
function Hint({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="right" sideOffset={12}>
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

export function Shell() {
  const t = useText();
  const path = useRouterState({ select: (s) => s.location.pathname });
  const conversation = useConversationNavigation();
  const currentThread = useRouterState({
    select: (s) => (s.location.search as { thread?: string }).thread,
  });
  const { data: engine, refresh: refreshEngine } = useQuery<Engine>(
    "/api/inference/status",
    3000,
  );
  const { data: devices, error: deviceError } = useQuery<{
    gpus: GPU[];
    available: boolean;
  }>("/api/gpu", 4000);
  useEffect(() => {
    refreshEngine();
  }, [path, refreshEngine]);
  const [search, setSearch] = useBrowserState("onecat:history-search", "");
  const [searchQuery, setSearchQuery] = useState("");
  const [historyCount, setHistoryCount] = useState(40);
  useEffect(() => {
    const timer = setTimeout(() => setSearchQuery(search), 250);
    return () => clearTimeout(timer);
  }, [search]);
  const { data: history, error: historyError } = useQuery<{ items: Thread[] }>(
    "/api/chat/threads" +
      (searchQuery ? "?q=" + encodeURIComponent(searchQuery) : ""),
    3000,
  );
  const midnight = new Date().setHours(0, 0, 0, 0);
  const groupOf = (thread: Thread) =>
    thread.pinned
      ? t("置顶", "Pinned")
      : (thread.updatedAt || thread.createdAt) >= midnight
        ? t("今天", "Today")
        : (thread.updatedAt || thread.createdAt) >= midnight - 6 * 86400000
          ? t("近 7 天", "Last 7 days")
          : t("更早", "Earlier");
  const visibleHistory = history?.items.slice(0, historyCount) || [];
  const [importOpen, setImportOpen] = useState(false);
  const measured = devices?.gpus.filter((g) => g.power_w != null) || [];
  const power =
    devices?.available && !deviceError && measured.length
      ? measured.reduce((a, g) => a + g.power_w!, 0)
      : null;
  const nav = [
    {
      ...conversation.target,
      key: "conversation",
      active: !!conversation.mode,
      label: t("对话", "Conversations"),
      icon: MessageSquare,
    },
    { to: "/creative", active: path === "/creative" || path === "/canvas", label: t("创作工作台", "Creative workspace"), icon: Workflow },
    { to: "/models", label: t("模型库", "Models"), icon: Boxes },
    { to: "/performance", label: t("性能与能效", "Performance"), icon: Gauge },
    { to: "/service", label: t("服务", "Service"), icon: Server },
  ] as const;
  const { enabled } = useInterfaceMotion();
  const wide = useMedia("(min-width: 1280px)"),
    mobile = useMedia("(max-width: 639px)");
  const [pinPreference, setPinned] = useBrowserState<unknown>(
    "onecat:history-pinned",
    false,
    true,
  );
  const [openPreference, setHistoryOpen] = useBrowserState<unknown>(
    "onecat:history-open",
    false,
  );
  const pinned = pinPreference === true && wide;
  const historyOpen = pinned || openPreference === true;
  const [mobileOpen, setMobileOpen] = useState(false);
  const historyTrigger = useRef<HTMLButtonElement>(null);
  const menuTrigger = useRef<HTMLButtonElement>(null);
  const pinnedPanel = useRef<HTMLElement>(null);
  const [signingOut, setSigningOut] = useState(false);
  useEffect(() => {
    if (!mobile) setMobileOpen(false);
  }, [mobile]);
  // Neither pinning nor drawer overlays remount the workspace or its draft inputs.
  const closeHistory = () => {
    setHistoryOpen(false);
    if (pinned) {
      setPinned(false);
      historyTrigger.current?.focus({ preventScroll: true });
    }
  };
  const selectedHistory = () => {
    if (!pinned) setHistoryOpen(false);
  };
  const togglePin = () => {
    setPinned(!pinned);
    setHistoryOpen(true);
    if (!pinned)
      requestAnimationFrame(() =>
        pinnedPanel.current?.focus({ preventScroll: true }),
      );
  };
  const historyTitle =
    conversation.location.last === "agent"
      ? t("任务记录", "Task history")
      : t("对话记录", "Chat history");
  const historyContents = (
    <>
      <header className="oc-history-top">
        <strong>{historyTitle}</strong>
        <div>
          {wide && (
            <Hint
              label={
                pinned
                  ? t("取消固定", "Unpin history")
                  : t("固定历史", "Pin history")
              }
            >
              <button
                className="oc-icon-button"
                aria-label={
                  pinned
                    ? t("取消固定", "Unpin history")
                    : t("固定历史", "Pin history")
                }
                aria-pressed={pinned}
                onClick={togglePin}
              >
                {pinned ? <PinOff size={17} /> : <Pin size={17} />}
              </button>
            </Hint>
          )}
          <button
            className="oc-icon-button"
            aria-label={t("关闭历史", "Close history")}
            onClick={closeHistory}
          >
            <X size={19} />
          </button>
        </div>
      </header>
      <div
        className="oc-history-content"
        onClick={(event) => {
          // Links only: pinning, searching and importing must keep the panel open.
          if ((event.target as HTMLElement).closest("a[href]"))
            selectedHistory();
        }}
      >
        {conversation.location.last === "agent" ? (
          <AgentHistory />
        ) : (
          <>
            <div className="oc-history-heading">
              <span>{t("最近对话", "Recent chats")}</span>
              <Button
                asChild
                size="icon-sm"
                variant="ghost"
                aria-label={t("新对话", "New chat")}
              >
                <Link to="/chat" search={{ thread: undefined }}>
                  <Plus />
                </Link>
              </Button>
            </div>
            <Input
              className="oc-history-search"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setHistoryCount(40);
              }}
              aria-label={t("搜索对话", "Search chats")}
              placeholder={t("搜索对话…", "Search chats…")}
            />
            <ErrorNotice error={historyError} />
            <ul className="oc-history-list">
              {visibleHistory.map((thread, index) => (
                <Fragment key={thread.id}>
                  {(index === 0 ||
                    groupOf(visibleHistory[index - 1]) !== groupOf(thread)) && (
                    <li className="oc-history-group">{groupOf(thread)}</li>
                  )}
                  <li className="oc-history-item">
                    <Link
                      to="/chat"
                      search={{ thread: thread.id }}
                      className="oc-history-link"
                      aria-current={
                        path === "/chat" && currentThread === thread.id
                          ? "page"
                          : undefined
                      }
                    >
                      {thread.generation?.state === "running" && (
                        <LoaderCircle
                          size={13}
                          className="animate-spin"
                          aria-label={t("生成中", "Generating")}
                        />
                      )}
                      <span className="oc-history-copy">{thread.title}<small>{new Date(thread.updatedAt || thread.createdAt).toLocaleString(t("zh-CN", "en-US"), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</small></span>
                    </Link>
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      className={
                        "oc-thread-pin" + (thread.pinned ? " is-pinned" : "")
                      }
                      aria-label={
                        thread.pinned
                          ? t("取消置顶", "Unpin conversation")
                          : t("置顶对话", "Pin conversation")
                      }
                      onClick={async () => {
                        try {
                          await mutation(
                            `/api/chat/threads/${thread.id}`,
                            { pinned: !thread.pinned },
                            "PATCH",
                          );
                          refreshData();
                        } catch (error) {
                          toast.error((error as Error).message);
                        }
                      }}
                    >
                      {thread.pinned ? <PinOff size={13} /> : <Pin size={13} />}
                    </Button>
                  </li>
                </Fragment>
              ))}
            </ul>
            {history && !visibleHistory.length && (
              <p className="oc-history-empty">
                {search
                  ? t("没有找到对话", "No matching conversations")
                  : t(
                      "你的对话会保存在这里",
                      "Your conversations will appear here",
                    )}
              </p>
            )}
            {(history?.items.length || 0) > historyCount && (
              <Button
                variant="ghost"
                onClick={() => setHistoryCount((value) => value + 40)}
              >
                {t("更多对话", "More conversations")}
              </Button>
            )}
            <Button
              variant="ghost"
              className="oc-import-chat"
              onClick={() => setImportOpen(true)}
            >
              <Upload />
              {t("导入对话", "Import chat")}
            </Button>
          </>
        )}
      </div>
    </>
  );
  const navigation = (compact: boolean) => (
    <nav
      className={compact ? "oc-nav-capsule" : "oc-mobile-nav"}
      aria-label={t("主导航", "Main navigation")}
    >
      {nav.map((item) => {
        const key = "key" in item ? item.key : item.to;
        const active = "active" in item ? item.active : path === item.to;
        const link = (
          <Link
            to={item.to}
            search={"search" in item ? item.search : {}}
            className="oc-rail-link"
            aria-label={item.label}
            aria-current={active ? "page" : undefined}
            onClick={() => setMobileOpen(false)}
          >
            {active && (
              <motion.span
                className="oc-nav-highlight"
                layoutId={compact ? "onecat-rail" : "onecat-mobile-nav"}
                transition={{
                  duration: enabled ? MOTION.state : 0,
                  ease: MOTION.ease,
                }}
              />
            )}
            <item.icon size={20} strokeWidth={1.7} />
            <span className={compact ? "sr-only" : ""}>{item.label}</span>
          </Link>
        );
        return compact ? (
          <Hint key={key} label={item.label}>
            {link}
          </Hint>
        ) : (
          <Fragment key={key}>{link}</Fragment>
        );
      })}
    </nav>
  );
  const accountItems = (
    <>
      <DropdownMenu.Item asChild>
        <Link to="/settings">
          <Settings size={17} />
          {t("设置", "Settings")}
        </Link>
      </DropdownMenu.Item>
      <DropdownMenu.Item asChild>
        <Link to="/setup">
          <PanelTop size={17} />
          {t("安装引导", "Setup")}
        </Link>
      </DropdownMenu.Item>
      <DropdownMenu.Separator />
      <DropdownMenu.Item
        disabled={signingOut}
        onSelect={async () => {
          if (signingOut) return;
          setSigningOut(true);
          try {
            await mutation("/api/auth/logout");
            window.location.reload();
          } catch (error) {
            toast.error((error as Error).message);
            setSigningOut(false);
          }
        }}
      >
        <LogOut size={17} />
        {t("退出登录", "Sign out")}
      </DropdownMenu.Item>
    </>
  );
  const accountMenu = (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          className="oc-icon-button oc-account-trigger"
          aria-label={t("设置与账户", "Settings and account")}
        >
          {mobileOpen ? <UserRound size={20} strokeWidth={1.7} /> : <Settings size={20} strokeWidth={1.7} />}
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          className="oc-account-menu"
          side="right"
          align="end"
          sideOffset={12}
        >
          {accountItems}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
  return (
    <div className="oc-shell">
      <aside className="oc-rail">
        <Link
          {...conversation.target}
          className="oc-rail-brand"
          aria-label="1Cat Studio"
        >
          <img
            className="oc-brand-logo"
            src="/onecat.jpg?v=20260907b"
            alt=""
            draggable={false}
          />
        </Link>
        {navigation(true)}
        <Hint label={t("历史记录", "History")}>
          <button
            ref={historyTrigger}
            className="oc-icon-button oc-history-trigger"
            aria-label={t("历史记录", "History")}
            aria-expanded={historyOpen}
            aria-controls={pinned ? "oc-history-pinned" : "oc-history-panel"}
            onClick={() =>
              historyOpen ? closeHistory() : setHistoryOpen(true)
            }
          >
            <History size={20} strokeWidth={1.7} />
          </button>
        </Hint>
        <div className="oc-rail-bottom">{accountMenu}</div>
      </aside>
      {pinned && (
        <aside
          id="oc-history-pinned"
          className="oc-history-panel oc-history-pinned"
          aria-label={historyTitle}
          ref={pinnedPanel}
          tabIndex={-1}
        >
          {historyContents}
        </aside>
      )}
      <section className="oc-workspace">
        <header
          className={
            "oc-app-header" +
            (conversation.mode || path === "/canvas" || path === "/creative" ? " oc-conversation-header" : "")
          }
        >
          <div className="oc-header-model">
            <button
              className="oc-mobile-menu oc-icon-button"
              ref={menuTrigger}
              onClick={() => setMobileOpen(true)}
              aria-label={t("打开导航", "Open navigation")}
            >
              <Menu size={20} />
            </button>
            {(path === "/canvas" || path === "/creative") ? (
              <span className="oc-header-tag">
                {t("创作工作台", "Creative workspace")}
              </span>
            ) : (
              <>
                <span
                  className={
                    "oc-status-dot " +
                    (engine?.state === "ready" ? "ready" : "")
                  }
                />
                <ModelPicker agent={conversation.mode === "agent"} />
                {engine?.profile && (
                  <span className="oc-header-tag">
                    {accelerationLabel(engine.profile) ||
                      t("基础推理", "Target only")}
                  </span>
                )}
              </>
            )}
          </div>
          {(path === "/canvas" || path === "/creative") && <CreativeModes canvas={path === "/canvas"} />}
          {conversation.mode && (
            <ConversationModes
              mode={conversation.mode}
              location={conversation.location}
            />
          )}
          <div className="oc-header-stats">
            <span>
              {(path === "/canvas" || path === "/creative")
                ? t("整机 GPU", "All GPUs")
                : engine?.state === "ready"
                  ? t("就绪", "Ready")
                  : jobLabel(engine?.state || "", t) || "—"}
            </span>
            <span
              title={t(
                `全部硬件：${devices?.gpus.length || 0} 张 GPU，其中 ${measured.length} 张提供功率读数`,
                `All hardware: ${devices?.gpus.length || 0} GPUs, ${measured.length} reporting power`,
              )}
            >
              {format(power, 0)} W
              {measured.length < (devices?.gpus.length || 0) && (
                <small className="oc-power-coverage">
                  {" "}
                  ·{" "}
                  {t(
                    `${measured.length} 张有读数`,
                    `${measured.length} reporting`,
                  )}
                </small>
              )}
            </span>
          </div>
        </header>
        {path !== "/canvas" && path !== "/creative" && path !== "/service" && <LaunchProgress compact />}
        <main
          className="oc-main"
          id={conversation.mode ? "oc-conversation-panel" : undefined}
          role={conversation.mode ? "tabpanel" : undefined}
          aria-labelledby={
            conversation.mode ? `oc-mode-${conversation.mode}` : undefined
          }
          tabIndex={conversation.mode ? 0 : undefined}
        >
          <Outlet />
        </main>
      </section>
      <Dialog.Root open={historyOpen && !pinned} onOpenChange={setHistoryOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="oc-drawer-overlay" />
          <Dialog.Content
            id="oc-history-panel"
            className="oc-history-panel oc-history-overlay"
            data-slot="dialog-content"
            aria-describedby={undefined}
            onOpenAutoFocus={(event) => {
              event.preventDefault();
              document
                .querySelector<HTMLInputElement>(".oc-history-overlay input")
                ?.focus({ preventScroll: true });
            }}
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              if (!pinned)
                (mobile ? menuTrigger : historyTrigger).current?.focus({
                  preventScroll: true,
                });
            }}
          >
            <Dialog.Title className="sr-only">{historyTitle}</Dialog.Title>
            {historyContents}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <Dialog.Root open={mobileOpen} onOpenChange={setMobileOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="oc-drawer-overlay" />
          <Dialog.Content
            className="oc-mobile-drawer"
            data-slot="dialog-content"
            aria-describedby={undefined}
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              if (!historyOpen)
                menuTrigger.current?.focus({ preventScroll: true });
            }}
          >
            <header>
              <Dialog.Title>1Cat Studio</Dialog.Title>
              <Dialog.Close
                className="oc-icon-button"
                aria-label={t("关闭导航", "Close navigation")}
              >
                <X size={20} />
              </Dialog.Close>
            </header>
            {navigation(false)}
            <button
              className="oc-mobile-history"
              onClick={() => {
                setMobileOpen(false);
                setHistoryOpen(true);
              }}
            >
              <History size={20} />
              {t("历史记录", "History")}
            </button>
            <footer>
              <Link to="/settings" onClick={() => setMobileOpen(false)}>
                <Settings size={18} />
                {t("设置", "Settings")}
              </Link>
              <Link to="/setup" onClick={() => setMobileOpen(false)}>
                <PanelTop size={18} />
                {t("安装引导", "Setup")}
              </Link>
              {accountMenu}
            </footer>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <Modal
        open={importOpen}
        onOpenChange={setImportOpen}
        title={t("导入对话", "Import conversation")}
      >
        <Field
          label={t(
            "选择包含 messages 数组的 JSON 文件",
            "Choose a JSON file with a messages array",
          )}
        >
          <Input
            type="file"
            accept=".json"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              try {
                await mutation(
                  "/api/chat/import",
                  JSON.parse(await file.text()),
                );
                refreshData();
                setImportOpen(false);
              } catch (error) {
                toast.error((error as Error).message);
              }
            }}
          />
        </Field>
      </Modal>
    </div>
  );
}
