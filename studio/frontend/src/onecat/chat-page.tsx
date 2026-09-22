import { AnimatePresence, motion } from "motion/react";
import { Phase, MOTION, useInterfaceMotion } from "./motion";
import { CopyButton } from "./common";
import { timingSource } from "./request-history";
// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import {
  useEffect,
  useRef,
  useState,
  useCallback,
  memo,
} from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import {
  ArrowUp,
  ImagePlus,
  X,
  Square,
  RotateCcw,
  Pencil,
  FileDown,
  SlidersHorizontal,
  ChevronDown,
  LoaderCircle,
  ArrowDown,
  MoreHorizontal,
  Trash2,
  Bot,
} from "lucide-react";
import { Button } from "@/onecat/ui";
import { Textarea } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import { Switch } from "@/onecat/ui";
import { TooltipIconButton } from "@/onecat/ui";
import { latestRun, observeRun, type ChatRun } from "./chat-run";
import { ThinkingToggle, ModelPicker, thinkingEffort, type ThinkingSupport, type ThinkingEffort } from "./model-controls";
import { useBrowserState } from "./browser-state";
import { toast } from "sonner";
import {
  api,
  mutation,
  useQuery,
  newMessageId,
  refreshData,
  type Message,
  type Thread,
  type Engine,
  type RequestMetrics,
  type RequestRecord,
} from "./api";
import {
  Field,
  NumberField,
  Modal,
  ErrorNotice,
  useText,
  format,
  saveFile,
} from "./common";
import { Markdown } from "./markdown";
import { PreviewBoundary } from "./preview-boundary";
import { streamPaint } from "./stream-paint";
import { MessageBoundary } from "./message-boundary";
import { artifacts, previewArtifact } from "./artifacts";
// The panel shell is small; its compiler/runtime is still fetched only on demand.
import { PreviewPanel } from "./preview-panel";

function textOf(message: Message) {
  return message.content
    .filter((p) => p.type === "text")
    .map((p) => p.text || "")
    .join("");
}
function thoughtOf(message: Message) {
  return message.content
    .filter((p) => p.type === "reasoning")
    .map((p) => p.text || "")
    .join("");
}

export function ChatPage() {
  const t = useText();
  const navigate = useNavigate();
  const { enabled: uiMotion } = useInterfaceMotion();
  const arrived = useRef(new Set<string>());
  const search = useSearch({ strict: false }) as { thread?: string };
  const threadId = search.thread;
  const { data: loaded, error: loadError } = useQuery<{
    thread: Thread;
    messages: Message[];
    generation?: ChatRun;
  }>(threadId ? "/api/chat/threads/" + threadId : null);
  const { data: runs } = useQuery<{ items: ChatRun[] }>("/api/chat/runs", 1500);
  const [activeRun, setActiveRun] = useState<ChatRun>();
  const [reconnecting, setReconnecting] = useState(false);
  const { data: engine } = useQuery<Engine>("/api/inference/status", 3000);
  const { data: options } = useQuery<{ thinking: ThinkingSupport }>("/api/inference/options?profile=" + (engine?.profile_id || ""));
  const [hydratedThread, setHydratedThread] = useState<string>();
  const [input, setInput] = useBrowserState("onecat:chat-draft:" + (threadId || "new"), "");
  const [messages, setMessages] = useState<Message[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [config, setConfig] = useState(false);
  type Settings = { system_prompt: string; temperature: number; top_p: number; max_tokens: number | null; thinking: boolean; thinking_effort?: ThinkingEffort };
  const [settingsDraft, setSettingsDraft] = useBrowserState<Settings | null>("onecat:chat-settings:" + (threadId || "new"), null);
  const settings = settingsDraft || { system_prompt: "", temperature: 0.7, top_p: 0.9, max_tokens: null, thinking: false,
    ...(threadId && loaded?.thread.id === threadId ? loaded.thread.settings : engine?.profile?.default_sampling) } as Settings;
  const { system_prompt: systemPrompt, temperature, top_p: topP, max_tokens: maxTokens, thinking } = settings;
  const setSystemPrompt = (value: string) => setSettingsDraft({ ...settings, system_prompt: value });
  const setTemperature = (value: number) => setSettingsDraft({ ...settings, temperature: value });
  const setTopP = (value: number) => setSettingsDraft({ ...settings, top_p: value });
  const setMaxTokens = (value: number | null) => setSettingsDraft({ ...settings, max_tokens: value });
  const effort = thinkingEffort(options?.thinking, settings.thinking_effort);
  const setThinking = (value: boolean, level?: ThinkingEffort) => setSettingsDraft({ ...settings, thinking: value, thinking_effort: level });
  const [submission, setSubmission] = useBrowserState<{ identity: string; thread: string; assistant: string; user: string; createdAt: number } | null>("onecat:chat-submission:" + (threadId || "new"), null);
  const scopeRef = useRef(threadId); scopeRef.current = threadId;
  const [preview, setPreview] = useState<{
    messageId: string;
    index: number;
  } | null>(null);
  useEffect(() => {
    setPreview(null);
  }, [threadId]);
  const [attachments, setAttachments] = useBrowserState<
    { id: string; name: string }[]
  >("onecat:chat-images:" + (threadId || "new"), []);
  const [uploading, setUploading] = useState(false);
  const uploadController = useRef<AbortController | null>(null);
  useEffect(() => {
    setUploading(false);
    return () => { uploadController.current?.abort(); uploadController.current = null; };
  }, [threadId]);
  const fileInput = useRef<HTMLInputElement>(null);
  const imagesEnabled = !!engine?.profile?.vision_enabled;
  async function uploadImages(files: File[]) {
    if (uploadController.current || !files.length) return;
    if (!imagesEnabled) {
      setError(
        t(
          "当前预设未启用图片理解",
          "Image understanding is not enabled for this profile",
        ),
      );
      return;
    }
    const maximum = Math.min(4, engine?.profile?.max_images || 4);
    if (files.length + attachments.length > maximum) {
      setError(
        t(
          `每条消息最多 ${maximum} 张图片`,
          `At most ${maximum} images per message`,
        ),
      );
      return;
    }
    const abort = new AbortController();
    uploadController.current = abort;
    setUploading(true);
    setError("");
    try {
      for (const file of files) {
        if (
          !["image/png", "image/jpeg", "image/webp"].includes(file.type) ||
          file.size > 10 * 1024 * 1024
        )
          throw new Error(
            t(
              "仅支持 PNG、JPEG、WebP，每张最多 10 MiB",
              "Use PNG, JPEG or WebP, at most 10 MiB each",
            ),
          );
        const body = new FormData();
        body.append("file", file);
        const result = await api<{ id: string; name: string }>(
          "/api/chat/attachments",
          { method: "POST", body, signal: abort.signal },
        );
        if (abort.signal.aborted) return;
        setAttachments((old) => [...old, result]);
      }
    } catch (error) {
      if (!abort.signal.aborted) setError((error as Error).message);
    } finally {
      if (uploadController.current === abort) {
        uploadController.current = null;
        setUploading(false);
      }
    }
  }
  const controller = useRef<AbortController | null>(null),
    runObserver = useRef<AbortController | null>(null),
    running = useRef(false),
    view = useRef<HTMLDivElement>(null),
    contentView = useRef<HTMLDivElement>(null),
    follow = useRef(true),
    lastScrollTop = useRef(0),
    touchY = useRef<number | undefined>(undefined),
    lastThread = useRef<string | undefined>(threadId);
  const [showBottom, setShowBottom] = useState(false),
    [edit, setEdit] = useState<{ index: number; text: string } | null>(null);
  const pauseFollowing = useCallback(() => {
    lastScrollTop.current = view.current?.scrollTop || 0;
    follow.current = false;
    setShowBottom(true);
  }, []);
  const resumeAtBottom = useCallback(() => {
    const node = view.current;
    if (node && node.scrollHeight - node.scrollTop - node.clientHeight < 8) {
      follow.current = true;
      setShowBottom(false);
    }
  }, []);
  useEffect(() => {
    if (lastThread.current !== threadId) {
      controller.current?.abort();
      controller.current = null;
      running.current = false;
      setBusy(false);
      setMessages([]);
      setHydratedThread(undefined);
      setError("");
      setActiveRun(undefined);
      setEdit(null);
      follow.current = true;
      setShowBottom(false);
      lastThread.current = threadId;
    }
  }, [threadId]);
  useEffect(() => {
    if (loaded && loaded.thread.id === threadId) {
      setHydratedThread(threadId);
      setMessages(current => {
        if (!running.current) return loaded.messages;
        const live = current.filter(message => message.threadId === threadId);
        return [...loaded.messages.map(message => live.find(m => m.id === message.id) || message),
          ...live.filter(message => !loaded.messages.some(m => m.id === message.id))];
      });

    }
  }, [loaded, threadId]);
  useEffect(() => () => controller.current?.abort(), []);
  const trackedRun = latestRun(threadId,
    activeRun, runs?.items.find(run => run.thread_id === threadId),
    loaded?.thread.id === threadId ? loaded?.generation : undefined);
  useEffect(() => {
    if (!threadId || !trackedRun) return;
    const observer = new AbortController();
    runObserver.current = observer;
    running.current = trackedRun.state === "running";
    setBusy(running.current);
    let latest: Message | undefined;
    const painter = streamPaint(() => {
      if (!observer.signal.aborted && latest) {
        const message = latest;
        setMessages(old => old.some(m => m.id === message.id)
          ? old.map(m => m.id === message.id ? message : m) : [...old, message]);
      }
    });
    void observeRun(threadId, trackedRun.id, observer.signal, (message, run) => {
      if (observer.signal.aborted) return;
      latest = message;
      setActiveRun(run);
      painter.schedule();
      if (run.state !== "running") {
        painter.flush();
        running.current = false;
        setBusy(false);
        if (run.error && run.state === "failed") setError(run.error);
      }
    }, setReconnecting).catch(error => {
      if (!observer.signal.aborted) setError(error.message);
    }).finally(() => {
      if (!observer.signal.aborted) {
        painter.flush();
        running.current = false;
        setBusy(false);
        setReconnecting(false);
        refreshData();
      }
    });
    return () => {
      observer.abort(); painter.flush();
      if (runObserver.current === observer) runObserver.current = null;
    };
  }, [threadId, trackedRun?.id]);
  useEffect(() => {
    const viewport = view.current,
      content = contentView.current;
    if (!viewport || !content) return;
    let frame: number | undefined;
    function schedule() {
      if (!follow.current || frame !== undefined) return;
      frame = requestAnimationFrame(() => {
        frame = undefined;
        if (follow.current) viewport!.scrollTop = viewport!.scrollHeight;
      });
    }
    // Streamdown and the highlighter commit after the message state update.
    // Follow the final layout, including images, preview resizing and font loads.
    const observer = new ResizeObserver(schedule);
    observer.observe(content);
    observer.observe(viewport);
    schedule();
    return () => {
      observer.disconnect();
      if (frame !== undefined) cancelAnimationFrame(frame);
    };
  }, []);
  const [manageOpen, setManageOpen] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const messageActions = useRef({ messages, generate });
  messageActions.current = { messages, generate };
  const openPreview = useCallback(
    (messageId: string, index: number) => setPreview({ messageId, index }),
    [],
  );
  const editMessage = useCallback(
    (index: number, text: string) => setEdit({ index, text }),
    [],
  );
  const regenerateMessage = useCallback((index: number, automatic = false) => {
    const current = messageActions.current;
    if (automatic) setMaxTokens(null);
    void current.generate(
      current.messages.slice(0, index),
      undefined,
      [],
      automatic ? null : undefined,
    );
  }, []);
  const ready =
    engine?.state === "ready" &&
    !engine.maintenance &&
    (!threadId || hydratedThread === threadId);
  async function generate(base: Message[], userText?: string, images = attachments, outputLimit = maxTokens) {
    if (!ready || running.current) return;
    runObserver.current?.abort();
    running.current = true; setBusy(true); setError(""); follow.current = true; setShowBottom(false);
    const abort = new AbortController(); controller.current = abort;
    const originalScope = threadId;
    let accepted = false;
    try {
      const requestSettings = { system_prompt: systemPrompt, temperature, top_p: topP,
        max_tokens: outputLimit, max_tokens_mode: outputLimit == null ? "auto" : "manual", thinking: !!options?.thinking.supported && thinking, thinking_effort: effort };
      const identity = JSON.stringify({ base, userText, images, settings: requestSettings, model: engine?.profile_id });
      let pending = submission?.identity === identity ? submission : null;
      if (!pending) {
        const id = threadId || (await mutation<Thread>("/api/chat/threads", {
          title: (userText || t("新对话", "New chat")).slice(0, 60), modelId: engine!.profile!.served_model_name,
        })).id;
        pending = { identity, thread: id, assistant: newMessageId(), user: newMessageId(), createdAt: Date.now() };
        setSubmission(pending);
      }
      if (abort.signal.aborted || scopeRef.current !== originalScope) return;
      const id = pending.thread;
      const current = userText === undefined ? base : [...base, {
        id: pending.user, threadId: id, parentId: base.at(-1)?.id || null, role: "user" as const,
        content: [{ type: "text" as const, text: userText }, ...images.map(image => ({ type: "image" as const, attachment_id: image.id, name: image.name }))], createdAt: pending.createdAt,
      }];
      const requestMessages = current.map(m => ({ role: m.role, content: m.content.filter(part => part.type !== "reasoning") }));
      if (systemPrompt) requestMessages.unshift({ role: "system", content: [{ type: "text", text: systemPrompt }] });
      const run = await mutation<ChatRun>(`/api/chat/threads/${id}/generate`, {
        message_id: pending.assistant, messages: current, expected_message_ids: messages.map(m => m.id), settings: requestSettings,
        request: { model: engine!.profile!.served_model_name, messages: requestMessages, temperature, top_p: topP,
          max_tokens: outputLimit, max_tokens_mode: outputLimit == null ? "auto" : "manual",
          chat_template_kwargs: options?.thinking.supported ? { enable_thinking: thinking, ...(thinking && effort ? { reasoning_effort: effort } : {}) } : {} },
      });
      if (abort.signal.aborted && abort.signal.reason === "cancelled") await mutation(`/api/chat/threads/${id}/cancel`);
      refreshData();
      if (abort.signal.aborted || scopeRef.current !== originalScope) return;
      // Keep the draft until the inference server accepts the request. A rejected
      // regeneration leaves the original history untouched on the server.
      const wait = new AbortController();
      const aborted = () => wait.abort(); abort.signal.addEventListener("abort", aborted, { once: true });
      try {
        await observeRun(id, run.id, wait.signal, (_message, update) => {
          if (update.pending_commit === false) wait.abort("accepted");
          else if (update.state !== "running") wait.abort(update.error || "Generation was not accepted");
        }, () => {});
      } finally { abort.signal.removeEventListener("abort", aborted); }
      if (abort.signal.aborted || scopeRef.current !== originalScope) return;
      if (wait.signal.reason !== "accepted") { setSubmission(null); throw new Error(String(wait.signal.reason || "Generation was not accepted")); }
      accepted = true;
      if (userText !== undefined) { setInput(value => value.trim() === userText ? "" : value); setAttachments([]); }
      setSubmission(null);
      arrived.current.add(pending.user);
      lastThread.current = id; setHydratedThread(id); setMessages(current);
      setActiveRun(run);
      if (!threadId) await navigate({ to: "/chat", search: { thread: id } });
      setPreview(selected => selected && !base.some(m => m.id === selected.messageId) ? { ...selected, messageId: pending!.assistant } : selected);
      refreshData();
    } catch (error) {
      if (!abort.signal.aborted && scopeRef.current === originalScope) setError((error as Error).message);
    } finally {
      if (controller.current === abort) { controller.current = null; if (!accepted) { running.current = false; setBusy(false); } }
    }
  }
  async function submit() {
    if (!uploading && (input.trim() || attachments.length))
      await generate(messages, input.trim());
  }
  const title =
    threadId && loaded?.thread.id === threadId
      ? loaded.thread.title
      : t("新对话", "New chat");
  const streamingMessage =
    messages.at(-1)?.role === "assistant" ? messages.at(-1) : undefined;
  return (
    <div className="oc-chat-workspace">
      <div className={"oc-chat-page" + (!messages.length ? " oc-chat-empty" : "")}>
        <div className="oc-chat-toolbar">
          <span>{title}</span>
          {reconnecting && <span role="status" className="oc-muted">{t("正在重新连接，后台继续生成", "Reconnecting; generation continues")}</span>}
          <div>
            {threadId && <TooltipIconButton tooltip={t("以此创建 Agent 任务", "Create an Agent task from this chat")}
              onClick={() => navigate({ to: "/agent", search: { source: threadId } })}><Bot /></TooltipIconButton>}
            <TooltipIconButton
              tooltip={t("导出对话", "Export conversation")}
              onClick={async () => {
                try {
                  if (threadId)
                    saveFile(
                      title + ".json",
                      await api(`/api/chat/threads/${threadId}/export`),
                    );
                } catch (error) {
                  toast.error((error as Error).message);
                }
              }}
            >
              <FileDown />
            </TooltipIconButton>
            <TooltipIconButton
              tooltip={t("对话参数", "Chat settings")}
              onClick={() => setConfig(true)}
            >
              <SlidersHorizontal />
            </TooltipIconButton>
            {threadId && (
              <TooltipIconButton
                tooltip={t("管理对话", "Manage conversation")}
                disabled={busy}
                onClick={() => {
                  setNewTitle(title);
                  setManageOpen(true);
                }}
              >
                <MoreHorizontal />
              </TooltipIconButton>
            )}
          </div>
        </div>
        <div className="oc-conversation-shell">
          <div
            className="oc-conversation"
            ref={view}
          onClickCapture={(event) => {
            const summary = (event.target as Element).closest(
              ".oc-reasoning > summary",
            );
            if (
              summary &&
              !(summary.parentElement as HTMLDetailsElement).open
            ) {
              // Opening thoughts is an explicit reading action. Pause before
              // the native details toggle changes height and the observer runs.
              pauseFollowing();
            }
          }}
          onWheel={(event) => {
            if (event.deltaY < 0) {
              pauseFollowing();
            } else if (event.deltaY > 0) resumeAtBottom();
          }}
          onTouchStart={(event) => {
            touchY.current = event.touches[0]?.clientY;
          }}
          onTouchMove={(event) => {
            const y = event.touches[0]?.clientY;
            if (y != null && touchY.current != null && y > touchY.current) {
              pauseFollowing();
            } else if (
              y != null &&
              touchY.current != null &&
              y < touchY.current
            ) {
              resumeAtBottom();
            }
            touchY.current = y;
          }}
          onPointerDown={(event) => {
            // Dragging the viewport scrollbar is an explicit reading action.
            const node = event.currentTarget;
            if (
              event.target === node &&
              event.clientX >=
                node.getBoundingClientRect().left +
                  node.clientLeft +
                  node.clientWidth
            ) {
              pauseFollowing();
            }
          }}
          onKeyDown={(event) => {
            if (
              ["ArrowUp", "PageUp", "Home"].includes(event.key) ||
              (event.key === " " && event.shiftKey)
            ) {
              pauseFollowing();
            } else if (
              ["ArrowDown", "PageDown", "End", " "].includes(event.key)
            )
              resumeAtBottom();
          }}
          onScroll={() => {
            const node = view.current;
            if (node) {
              // Layout/scroll anchoring can fire scroll events away from the
              // bottom. A small upward gesture must remain paused even within
              // the bottom tolerance; only downward travel restores following.
              const movingDown = node.scrollTop > lastScrollTop.current;
              lastScrollTop.current = node.scrollTop;
              if (
                movingDown &&
                node.scrollHeight - node.scrollTop - node.clientHeight < 8
              )
                follow.current = true;
              setShowBottom(!follow.current);
            }
          }}
          >
            <div ref={contentView} className="oc-conversation-content">
            {!messages.length ? (
              <div className="oc-chat-welcome">
                <div className="oc-welcome-mark">01 / FROM IDEA TO REALITY</div>
                <h1>MAKE IT.<span>{t("用想法，创造。", "Make room for ideas.")}</span></h1>
                <p>
                  {ready
                    ? t(
                        "少一点犹豫，多一点动手。",
                        "A little less hesitation. A little more creation.",
                      )
                    : t(
                        "可以先写下想法，再选择模型开始聊天。",
                        "Draft your idea, then choose a model to begin.",
                      )}
                </p>
                {!ready && (
                  <Button asChild>
                    <Link to="/models">
                      {t("选择并启动模型", "Choose and start a model")}
                    </Link>
                  </Button>
                )}
                {ready && <div className="oc-chat-suggestions">
                  {[
                    [t("写一个网页", "Build a webpage"), t("帮我写一个可以在预览中运行的网页。", "Build a webpage I can run in the preview.")],
                    [t("解释代码", "Explain code"), t("请帮我解释下面这段代码：\n", "Please explain this code:\n")],
                    [t("整理文字", "Organize notes"), t("请把下面的文字整理成清晰的要点：\n", "Organize these notes into clear key points:\n")],
                  ].map(([label, prompt]) => <Button key={label} variant="outline" onClick={() => {
                    setInput(prompt); document.querySelector<HTMLTextAreaElement>(".oc-chat-composer textarea")?.focus();
                  }}>{label}</Button>)}
                </div>}
              </div>
            ) : (
              <div className="oc-message-list">
                {messages.map((m, index) => (
                  <MessageBoundary
                    key={m.id}
                    fallback={
                      <article className="oc-message">
                        <pre className="oc-message-fallback">
                          {m.content
                            .map((part) => part.text || "")
                            .join("\n\n")}
                        </pre>
                      </article>
                    }
                  >
                    <MessageRow
                      key={m.id}
                      message={m}
                      arriving={arrived.current.has(m.id)}
                      index={index}
                      busy={busy}
                      ready={!!ready}
                      streaming={busy && index === messages.length - 1}
                      docked={index === messages.length - 1 && m.role === "assistant"}
                      onPreview={openPreview}
                      onEdit={editMessage}
                      onRegenerate={regenerateMessage}
                    />
                  </MessageBoundary>
                ))}
              </div>
            )}
            </div>
          </div>
          {streamingMessage && (
            <StreamingFooter message={streamingMessage} streaming={busy} ready={!!ready}
              index={messages.length - 1} onRegenerate={regenerateMessage} />
          )}
        </div>
        <div className="oc-composer-area">
          {showBottom && (
            <Button
              variant="outline"
              className="oc-scroll-bottom"
              onClick={() => {
                follow.current = true;
                setShowBottom(false);
                view.current?.scrollTo({
                  top: view.current.scrollHeight,
                  behavior: uiMotion ? "smooth" : "instant",
                });
              }}
            >
              <ArrowDown />
              {t("回到最新", "Jump to latest")}
            </Button>
          )}
          <ErrorNotice error={error || loadError} />
          <form
            className="oc-chat-composer"
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            <div className="oc-image-list">
              <AnimatePresence initial={false}>
              {attachments.map((image) => (
                <motion.div className="oc-image-thumb" key={image.id} layout={uiMotion ? "position" : false} initial={{ opacity: 0, scale: .92 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: .9 }} transition={{ duration: uiMotion ? MOTION.compact : 0 }}>
                  <img
                    src={`/api/chat/attachments/${image.id}`}
                    alt={image.name}
                  />
                  <Button
                    type="button"
                    size="icon"
                    variant="secondary"
                    onClick={() =>
                      setAttachments((items) =>
                        items.filter((i) => i.id !== image.id),
                      )
                    }
                    aria-label={t("移除图片", "Remove image")}
                  >
                    <X size={14} />
                  </Button>
                </motion.div>
              ))}
              </AnimatePresence>
            </div>
            {!ready && <div className="oc-readiness" role="status">
              <span>{engine?.state === "loading" ? t("模型正在准备，草稿会保留。", "Model preparing. Your draft is saved.") : engine?.state === "failed" ? t("模型启动未完成，草稿已保留。", "Model startup failed. Your draft is saved.") : t("模型尚未就绪，可以先写草稿。", "No model is ready. You can still draft a message.")}</span>
              <Link to={engine?.state === "loading" || engine?.state === "failed" ? "/service" : "/models"}>{engine?.state === "loading" || engine?.state === "failed" ? t("查看状态", "View status") : t("准备模型", "Prepare a model")}</Link>
            </div>}
            <Textarea
              onPaste={(event) => {
                const files = [...event.clipboardData.files].filter((f) =>
                  f.type.startsWith("image/"),
                );
                if (files.length) {
                  event.preventDefault();
                  if (!uploading) void uploadImages(files);
                }
              }}
              value={input}
              readOnly={!!controller.current}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                ready
                  ? t("输入消息…", "Message…")
                  : t("先写下想法，模型就绪后即可发送…", "Draft your message; send when a model is ready…")
              }
              rows={1}
              aria-label={t("消息草稿", "Message draft")}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  if (!busy && ready) void submit();
                }
              }}
            />
            <div className="oc-composer-bottom">
              {imagesEnabled && (
                <>
                  <input
                    ref={fileInput}
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    multiple
                    hidden
                    onChange={(e) => {
                      void uploadImages([...(e.target.files || [])]);
                      e.target.value = "";
                    }}
                  />
                  <TooltipIconButton
                    type="button"
                    tooltip={t("添加图片", "Add images")}
                    disabled={busy || uploading || !ready}
                    onClick={() => fileInput.current?.click()}
                  >
                    {uploading ? (
                      <LoaderCircle className="animate-spin" />
                    ) : (
                      <ImagePlus />
                    )}
                  </TooltipIconButton>
                </>
              )}
              <ThinkingToggle value={thinking} effort={effort} onChange={setThinking} support={options?.thinking} disabled={busy} />
              <ModelPicker compact disabled={busy} />
              <Button type={busy ? "button" : "submit"} size="icon" disabled={!busy && (!ready || uploading || (!input.trim() && !attachments.length))}
                aria-label={busy ? t("停止生成", "Stop generation") : t("发送消息", "Send message")}
                onClick={busy ? async () => {
                  controller.current?.abort("cancelled");
                  if (threadId) {
                    try { await mutation(`/api/chat/threads/${threadId}/cancel`); refreshData(); }
                    catch (error) { setError((error as Error).message); }
                  }
                  running.current = false; setBusy(false);
                } : undefined}>
                <Phase phase={busy ? "stop" : "send"} className="oc-action-icon">{busy ? <Square /> : <ArrowUp />}</Phase>
              </Button>
            </div>
          </form>
        </div>
        <Modal
          open={manageOpen}
          onOpenChange={setManageOpen}
          title={t("管理对话", "Manage conversation")}
        >
          <Field label={t("对话名称", "Conversation title")}>
            <Input
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
            />
          </Field>
          <div className="oc-actions">
            <Button
              disabled={!newTitle.trim()}
              onClick={async () => {
                try {
                  await mutation(
                    "/api/chat/threads/" + threadId,
                    { title: newTitle },
                    "PATCH",
                  );
                  refreshData();
                  setManageOpen(false);
                } catch (e) {
                  toast.error((e as Error).message);
                }
              }}
            >
              {t("保存名称", "Save title")}
            </Button>
            <Button
              variant="destructive"
              onClick={async () => {
                try {
                  await mutation(
                    "/api/chat/threads/" + threadId,
                    undefined,
                    "DELETE",
                  );
                  refreshData();
                  setManageOpen(false);
                  setMessages([]);
                  void navigate({ to: "/chat", search: { thread: undefined } });
                } catch (e) {
                  toast.error((e as Error).message);
                }
              }}
            >
              <Trash2 />
              {t("删除此对话", "Delete this conversation")}
            </Button>
          </div>
        </Modal>
        <Modal
          open={config}
          onOpenChange={setConfig}
          title={t("对话参数", "Chat settings")}
          description={t(
            "这些参数对下一次请求生效。",
            "These settings apply to your next request.",
          )}
        >
          <label className="oc-toggle-row">
            <Switch
              defaultChecked={
                localStorage.getItem("onecat_stream_animation") !== "off"
              }
              onCheckedChange={(v) => {
                localStorage.setItem(
                  "onecat_stream_animation",
                  v ? "on" : "off",
                );
                window.dispatchEvent(new Event("onecat:animation"));
              }}
            />
            {t("柔和流式动画", "Soft streaming animation")}
          </label>
          <Field label={t("系统提示词", "System prompt")}>
            <Textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={5}
            />
          </Field>
          <div className="oc-form-grid">
            <NumberField
              label="Temperature"
              value={temperature}
              onChange={setTemperature}
              min={0}
              max={2}
              step={0.05}
            />
            <NumberField
              label="Top P"
              value={topP}
              onChange={setTopP}
              min={0}
              max={1}
              step={0.05}
            />
            <Field
              label={t("最大生成长度", "Maximum generated tokens")}
              hint={t(
                `总上下文 ${engine?.profile?.max_model_len?.toLocaleString() || "—"} tokens；自动由服务分配剩余空间。`,
                `Total context ${engine?.profile?.max_model_len?.toLocaleString() || "—"} tokens; automatic uses the remaining budget.`,
              )}
            >
              <Input
                type="number"
                min={1}
                value={maxTokens ?? ""}
                placeholder={t("自动：跟随模型", "Auto: follow model")}
                onChange={(e) =>
                  setMaxTokens(e.target.value ? Number(e.target.value) : null)
                }
              />
            </Field>
          </div>
          <Button onClick={() => setConfig(false)}>{t("完成", "Done")}</Button>
        </Modal>
        <Modal
          open={!!edit}
          onOpenChange={(open) => {
            if (!open) setEdit(null);
          }}
          title={t("编辑消息", "Edit message")}
          description={t(
            "重新发送会替换这条消息之后的对话。",
            "Resending replaces the conversation after this message.",
          )}
        >
          <Textarea
            value={edit?.text || ""}
            onChange={(e) =>
              setEdit((prev) =>
                prev ? { ...prev, text: e.target.value } : null,
              )
            }
            rows={6}
          />
          <Button
            disabled={!edit?.text.trim() || !ready}
            onClick={() => {
              const value = edit!;
              setEdit(null);
              void generate(
                messages.slice(0, value.index),
                value.text,
                messages[value.index].content
                  .filter((part) => part.type === "image")
                  .map((part) => ({
                    id: part.attachment_id!,
                    name: part.name || "image",
                  })),
              );
            }}
          >
            {t("重新发送", "Resend")}
          </Button>
        </Modal>
      </div>
      <AnimatePresence initial={false}>
      {preview && (
        <PreviewBoundary key={`${preview.messageId}:${preview.index}`} onClose={() => setPreview(null)}>
            <PreviewPanel
              artifact={previewArtifact(
                artifacts(
                  textOf(
                    messages.find((m) => m.id === preview.messageId) ||
                      ({ content: [] } as unknown as Message),
                  ),
                ),
                preview.index,
              )}
              streaming={busy}
              onClose={() => setPreview(null)}
            />
        </PreviewBoundary>
      )}
      </AnimatePresence>
    </div>
  );
}
const MessageRow = memo(function MessageRow({
  message: m,
  arriving,
  index,
  busy,
  ready,
  streaming,
  docked,
  onPreview,
  onEdit,
  onRegenerate,
}: {
  message: Message;
  arriving?: boolean;
  index: number;
  busy: boolean;
  ready: boolean;
  streaming: boolean;
  docked: boolean;
  onPreview: (id: string, index: number) => void;
  onEdit: (index: number, text: string) => void;
  onRegenerate: (index: number, automatic?: boolean) => void;
}) {
  const t = useText();
  const [reasoningOpen, setReasoningOpen] = useBrowserState("onecat:reasoning:" + m.id, false);
  const thought = thoughtOf(m),
    answer = textOf(m);
  const live = m.metadata?.live as
    | {
        decode_tokens_s: number | null;
        output_tokens: number | null;
        reason: string | null;
      }
    | undefined;
  const previewMessage = useCallback(
    (artifactIndex: number) => onPreview(m.id, artifactIndex),
    [m.id, onPreview],
  );
  return (
    <article
      className={
        "oc-message oc-message-" + m.role + (streaming ? " oc-message-streaming" : "")
      }
      data-arriving={arriving || undefined}
      key={m.id}
      aria-busy={streaming || undefined}
    >
      <div className="oc-message-author">
        {m.role === "user" ? t("你", "You") : "1Cat"}
        {streaming && (
          <span className="oc-generation-status" role="status">
            <LoaderCircle className="animate-spin" size={14} />
            {thought && !answer
              ? t("思考中…", "Thinking…")
              : t("正在生成…", "Generating…")}
          </span>
        )}
      </div>
      {!!thought && (
        <details
          className="oc-reasoning"
          open={reasoningOpen}
          onToggle={(event) => setReasoningOpen(event.currentTarget.open)}
        >
          <summary>
            {t("思考过程", "Reasoning")}
            {m.metadata?.thinking_elapsed_s != null && <span className="oc-muted">{format(Number(m.metadata.thinking_elapsed_s), 1)} s</span>}
            <ChevronDown size={14} />
          </summary>
          {reasoningOpen && (
            <Markdown
              text={thought}
              streaming={streaming && !answer}
              animationSep="word"
            />
          )}
        </details>
      )}
      <div className="oc-message-content">
        <div className="oc-image-list">
          {m.content
            .filter((part) => part.type === "image")
            .map((part) => (
              <a
                key={part.attachment_id}
                href={`/api/chat/attachments/${part.attachment_id}`}
                target="_blank"
                rel="noreferrer"
              >
                <img
                  src={`/api/chat/attachments/${part.attachment_id}`}
                  alt={part.name || t("聊天图片", "Chat image")}
                  loading="lazy"
                />
              </a>
            ))}
        </div>
        {answer ? (
          <Markdown
            text={answer}
            onPreview={m.role === "assistant" ? previewMessage : undefined}
            streaming={streaming}
          />
        ) : null}
      </div>
      {streaming || docked ? (
        <div className="oc-message-actions-spacer" aria-hidden="true" />
      ) : (
        <div className="oc-message-actions">
          <CopyButton value={answer} />
          {m.role === "user" ? (
            <TooltipIconButton
              tooltip={t("编辑并重新发送", "Edit and resend")}
              disabled={busy}
              onClick={() => onEdit(index, answer)}
            >
              <Pencil />
            </TooltipIconButton>
          ) : (
            <TooltipIconButton
              tooltip={t("重新生成", "Regenerate")}
              disabled={busy || !ready}
              onClick={() => onRegenerate(index)}
            >
              <RotateCcw />
            </TooltipIconButton>
          )}
          {m.role === "assistant" && (
            <Timing metadata={m.metadata} live={live} streaming={false} />
          )}
        </div>
      )}
      {m.role === "assistant" && m.metadata?.finish_reason === "length" && (
        <div className="oc-output-limit" role="status">
          <span>
            {t(
              "回答达到生成长度限制，内容可能不完整。",
              "The generation limit was reached; this reply may be incomplete.",
            )}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={busy || !ready}
            onClick={() => onRegenerate(index, true)}
          >
            {t("自动长度重新生成", "Regenerate with automatic length")}
          </Button>
        </div>
      )}
      {m.role === "assistant" &&
        !streaming &&
        ["error", "cancelled"].includes(String(m.metadata?.finish_reason)) && (
          <div className="oc-output-limit oc-output-interrupted" role="status">
            {m.metadata?.finish_reason === "cancelled"
              ? t(
                  "生成已停止，以上内容可能不完整。",
                  "Generation stopped; the reply above may be incomplete.",
                )
              : t(
                  "生成中断，已保留收到的内容。可点击重新生成重试。",
                  "Generation was interrupted. Received content was saved; use Regenerate to retry.",
                )}
          </div>
        )}
    </article>
  );
});

const StreamingFooter = memo(function StreamingFooter({
  message,
  streaming,
  ready,
  index,
  onRegenerate,
}: {
  message: Message;
  streaming: boolean;
  ready: boolean;
  index: number;
  onRegenerate: (index: number) => void;
}) {
  const t = useText();
  const live = message.metadata?.live as
    | {
        decode_tokens_s: number | null;
        output_tokens: number | null;
        reason: string | null;
      }
    | undefined;
  return (
    <div className="oc-stream-footer-layer">
      <div className="oc-stream-footer-align">
        <div className="oc-message-actions oc-stream-actions">
          <CopyButton value={textOf(message)} />
          <TooltipIconButton tooltip={t("重新生成", "Regenerate")} disabled={streaming || !ready} onClick={() => onRegenerate(index)}>
            <RotateCcw />
          </TooltipIconButton>
          <div className="oc-response-metrics"><Phase phase={streaming ? "live" : "final"}><Timing metadata={message.metadata} live={live} streaming={streaming} /></Phase></div>
        </div>
      </div>
    </div>
  );
});

function Timing({
  metadata,
  live,
  streaming,
}: {
  metadata?: Record<string, unknown>;
  live?: {
    decode_tokens_s: number | null;
    output_tokens: number | null;
    reason: string | null;
  };
  streaming: boolean;
}) {
  const t = useText();
  const m = metadata as
    | {
        usage?: { completion_tokens?: number; prompt_tokens?: number };
        timing?: RequestMetrics;
        request_id?: string;
        finish_reason?: string;
      }
    | undefined;
  const { data, refresh } = useQuery<RequestRecord>(
    m?.request_id ? "/api/requests/" + m.request_id : null,
  );
  useEffect(() => {
    if (!data?.metrics?.pending) return;
    const timer = setTimeout(refresh, 1000);
    return () => clearTimeout(timer);
  }, [data, refresh]);
  if (streaming)
    return (
      <span
        className="oc-live-decode"
        title={t(
          "按服务端实际输出 token 计数与到达时间累计计算，包含思考输出，排除首批 token 与等待首批的时间。生成中为本机流观测，结束后回填最终统计。",
          "Cumulative server-local token arrival rate, including reasoning and excluding the first batch and its wait. Final statistics replace this live observation after completion.",
        )}
      >
        {t("实时", "Live")}{" "}
        <strong>
          {live?.decode_tokens_s == null
            ? "—"
            : live.decode_tokens_s.toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}
        </strong>{" "}
        tok/s
        <small>
          {t("本机流观测", "Local stream")}
          {live?.output_tokens != null
            ? ` · ${live.output_tokens.toLocaleString()} tokens`
            : ""}
        </small>
      </span>
    );
  const timing = data?.metrics || m?.timing;
  if (!timing) return null;
  return (
    <details className="oc-message-timing">
      <summary>
        {timing.decode_tokens_s != null
          ? format(timing.decode_tokens_s, 2) +
            " tok/s · Decode" +
            (timing.decode_source === "local_token_stream"
              ? t("（本机流观测）", " (local stream)")
              : "")
          : format(timing.elapsed_s) + " s"}
      </summary>
      <span>
        {timingSource(timing, t)} · TTFT {format(timing.ttft_s, 3)} s · Prefill{" "}
        {format(timing.prefill_tokens_s, 1)} tok/s · {t("输入", "Input")}{" "}
        {data?.prompt_tokens ?? m?.usage?.prompt_tokens ?? "—"} tokens ·{" "}
        {t("输出", "Output")}{" "}
        {data?.completion_tokens ?? m?.usage?.completion_tokens ?? "—"} tokens
        {m?.finish_reason === "cancelled" ? " · " + t("已停止", "Stopped") : ""}
        {timing.decode_reason && (
          <span className="block">Decode: {timing.decode_reason}</span>
        )}
        {timing.prefill_reason && (
          <span className="block">Prefill: {timing.prefill_reason}</span>
        )}
      </span>
    </details>
  );
}
