// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { AnimatePresence, motion } from "motion/react";
import { Bot, Check, ChevronDown, FileCode, FolderOpen, GitBranch, LoaderCircle, Plus, Square,
  ArrowUp, ArrowDown, Upload, Download, Play, X, Terminal, FileDiff, RotateCcw, ShieldCheck } from "lucide-react";
import { Button } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import { Textarea } from "@/onecat/ui";
import { api, ApiError, copyText, mutation, newMessageId, refreshData, useQuery, type Message, type Thread } from "./api";
import { CopyButton, ErrorNotice, Field, Modal, format, useText } from "./common";
import { useBrowserState } from "./browser-state";
import { Markdown } from "./markdown";
import { PreviewBoundary } from "./preview-boundary";
import { type Artifact, artifacts, fileArtifact, previewArtifact } from "./artifacts";
import { MOTION, useInterfaceMotion } from "./motion";
import { activeAgent, reduceAgent, latestAgentAnswer, agentItemText, type AgentTask, type AgentItem, type Approval } from "./agent-state";
import { agentCommands, matchingAgentCommands, parseAgentCommand } from "./agent-commands";
import { ModelPicker, ThinkingToggle, type ThinkingSupport } from "./model-controls";
import { AgentStats } from "./agent-stats";
import { PreviewPanel } from "./preview-panel";
type Project = { id: string; name: string; repository?: string };
type AgentStatus = { installed: boolean; sandbox_ready: boolean; sandbox_error?: string;
  version: string; source: string; model: { ready: boolean; name?: string; label?: string; context_window?: number; tool_calling: boolean; thinking?: ThinkingSupport } };

function useAgentTask(id?: string) {
  const [task, setTask] = useState<AgentTask>();
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const resume = (event: Event) => { if ((event as CustomEvent).detail === id) setRevision(value => value + 1); };
    window.addEventListener("onecat:agent-resume", resume);
    return () => window.removeEventListener("onecat:agent-resume", resume);
  }, [id]);
  useEffect(() => {
    setTask(current => current?.id === id ? current : undefined); setError(""); setConnected(false);
    if (!id) return;
    // EventSource reconnects without restarting the actual task. Each connection
    // receives a durable snapshot first, then only changed items/text.
    const events = new EventSource(`/api/agent/tasks/${id}/events`);
    const controller = new AbortController();
    let generation = 0;
    events.onopen = () => { generation++; setConnected(true); setError(""); };
    events.onmessage = event => {
      generation++;
      const update = JSON.parse(event.data);
      setTask(current => reduceAgent(current, update));
      if (update.type === "snapshot" && !activeAgent(update.task?.state) && update.task?.settled !== false) events.close();
      refreshDataForFinish(update);
    };
    events.onerror = () => {
      setConnected(false); setError("reconnecting");
      const observed = ++generation;
      void api<AgentTask>(`/api/agent/tasks/${id}`, { signal: controller.signal }).then(current => {
        if (controller.signal.aborted || generation !== observed) return;
        setTask(current);
        if (!activeAgent(current.state) && current.settled !== false) { events.close(); setError(""); }
      }).catch(error => {
        if (controller.signal.aborted || generation !== observed) return;
        if (error instanceof ApiError && [401, 403, 404].includes(error.status)) events.close();
        setError(error.message);
      });
    };
    return () => { controller.abort(); events.close(); };
  }, [id, revision]);
  return { task: task?.id === id ? task : undefined, error, connected };
}
function refreshDataForFinish(update: { type: string; task?: AgentTask }) {
  if (update.type === "snapshot" && !activeAgent(update.task?.state) && update.task?.settled !== false) refreshData();
}
function label(state: string, t: (zh: string, en: string) => string) {
  const labels: Record<string, [string, string]> = {
    starting: ["准备工作区", "Preparing workspace"], running: ["正在执行", "Working"],
    waiting: ["等待你的回复", "Waiting for you"], stopping: ["正在停止", "Stopping"],
    completed: ["任务完成", "Completed"], failed: ["执行遇到问题", "Task failed"],
    cancelled: ["已停止", "Stopped"], interrupted: ["可以继续", "Ready to resume"],
  };
  return labels[state] ? t(...labels[state]) : state;
}

export function AgentHistory() {
  const t = useText();
  const { task: id } = useSearch({ strict: false }) as { task?: string };
  const [search, setSearch] = useBrowserState("onecat:agent-history-search", "");
  const [query, setQuery] = useState(search), [offset, setOffset] = useState(0);
  useEffect(() => { const timer = setTimeout(() => { setQuery(search); setOffset(0); }, 200); return () => clearTimeout(timer); }, [search]);
  const { data, error } = useQuery<{ items: AgentTask[] }>(`/api/agent/tasks?q=${encodeURIComponent(query)}&offset=${offset}`, 3000);
  const filtered = data?.items;
  return <div className="oc-agent-history">
    <div className="oc-history-heading"><span>{t("最近任务", "Recent tasks")}</span>
      <Button variant="ghost" size="icon-sm" asChild aria-label={t("新任务", "New task")}><Link to="/agent"><Plus /></Link></Button></div>
    <Input className="oc-history-search" value={search} onChange={event => setSearch(event.target.value)} aria-label={t("搜索任务", "Search tasks")} placeholder={t("搜索任务…", "Search tasks…")} />
    <ErrorNotice error={error} />
    {filtered?.map(task => <Link key={task.id} to="/agent" search={{ task: task.id }} className={id === task.id ? "is-active" : ""}>
      {activeAgent(task.state) ? <LoaderCircle className="animate-spin" size={14} /> : <Bot size={14} />}<span>{task.title}</span>
    </Link>)}
    {data && !filtered?.length && search && <p>{t("没有找到任务", "No matching tasks")}</p>}
    {data && !data.items.length && !search && <p>{t("任务和进展会保存在这里", "Your tasks and progress appear here")}</p>}
    {(offset > 0 || data?.items.length === 100) && <div className="oc-actions"><Button size="sm" variant="ghost" disabled={offset === 0} onClick={() => setOffset(n => Math.max(0, n - 100))}>{t("较新任务", "Newer tasks")}</Button><Button size="sm" variant="ghost" disabled={data?.items.length !== 100} onClick={() => setOffset(n => n + 100)}>{t("更早任务", "Older tasks")}</Button></div>}
  </div>;
}

export function AgentPage() {
  const t = useText(), navigate = useNavigate();
  const { enabled } = useInterfaceMotion();
  const { task: taskId, source } = useSearch({ strict: false }) as { task?: string; source?: string };
  const { task, error: connectionError } = useAgentTask(taskId);
  const { data: projects, error: projectsError } = useQuery<{ items: Project[] }>("/api/agent/projects");
  const { data: status } = useQuery<AgentStatus>("/api/agent/status", 4000);
  const [selected, select] = useBrowserState("onecat:agent-project", "");
  const projectId = taskId ? task?.project_id || "" : selected;
  const project = projects?.items.find(p => p.id === projectId);
  const pendingProjectDraft = useRef<{ id: string; text: string } | null>(null);
  useEffect(() => { if (task?.project_id) select(task.project_id); }, [task?.project_id, select]);
  const [thinking, setThinking] = useBrowserState<boolean | null>("onecat:agent-thinking:" + (taskId || projectId || "new"), null);
  const scope = taskId || projectId || "new";
  const currentScope = useRef(scope); currentScope.current = scope;
  useEffect(() => { currentScope.current = scope; return () => { currentScope.current = "__unmounted__"; }; }, []);
  const [draft, setDraft] = useBrowserState("onecat:agent-draft:" + (taskId || projectId || "new"), "");
  useLayoutEffect(() => {
    const carry = pendingProjectDraft.current;
    if (carry && carry.id === projectId && !taskId) {
      pendingProjectDraft.current = null;
      setDraft(carry.text);
    }
  }, [projectId, taskId, setDraft]);
  const [submission, setSubmission] = useBrowserState<{ text: string; id: string } | null>("onecat:agent-submit:" + (taskId || projectId || "new"), null);
  const { data: sourceChat, error: sourceError } = useQuery<{ thread: Thread; messages: Message[] }>(source ? `/api/chat/threads/${source}` : null);
  const [createOpen, setCreateOpen] = useState(false), [name, setName] = useState(""), [repository, setRepository] = useState("");
  const [error, setError] = useState(""), [busy, setBusy] = useState(false), [creating, setCreating] = useState(false);
  const [uploadNotice, setUploadNotice] = useState("");
  const [notice, setNotice] = useState("");
  const [commandIndex, setCommandIndex] = useState(0), [commandDismissed, setCommandDismissed] = useState(false);
  const [infoPanel, setInfoPanel] = useState<"status" | "help" | "history" | "permissions" | "model" | null>(null);
  const [permission, setPermission] = useState<"workspace-write" | "read-only">("workspace-write");
  const [mode, setMode] = useState<"default" | "plan">("default");
  const input = useRef<HTMLTextAreaElement>(null), permissionInput = useRef<HTMLSelectElement>(null);
  const commands = commandDismissed ? [] : matchingAgentCommands(draft);
  const selectedCommand = Math.min(commandIndex, Math.max(0, commands.length - 1));
  useEffect(() => { setPermission(task?.permission || "workspace-write"); setMode(task?.mode || "default"); }, [taskId, task?.permission, task?.mode]);
  useLayoutEffect(() => {
    const el = input.current;
    if (el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 180) + "px"; }
  }, [draft]);
  useEffect(() => { setInfoPanel(null); setNotice(""); }, [taskId]);
  function chooseCommand(name: string) { setDraft(`/${name} `); setCommandDismissed(true); input.current?.focus(); }

  const [panel, setPanel] = useState<"files" | "diff" | null>(null);
  const [filePath, setFilePath] = useState("");
  const [preview, setPreview] = useState<Artifact>();
  const previewOrigin = useRef<{ item?: string; index?: number; path?: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null), sending = useRef(false);
  const uploadController = useRef<AbortController | null>(null);
  useEffect(() => () => uploadController.current?.abort(), [scope]);
  const folderInput = useRef<HTMLInputElement>(null);
  const filesPanel = useRef<HTMLElement>(null);
  const running = activeAgent(task?.state) || task?.settled === false;
  const canSteer = task?.state === "running" && task?.operation === "turn" && !!task.current_turn;
  const { data: files, error: filesError } = useQuery<{ items: { path: string; bytes: number }[]; truncated: boolean }>(
    projectId && panel === "files" ? `/api/agent/projects/${projectId}/files` : null, running ? 3000 : 0);
  const { data: file, error: fileError } = useQuery<{ path: string; text?: string; binary: boolean }>(
    projectId && filePath ? `/api/agent/projects/${projectId}/file?path=${encodeURIComponent(filePath)}` : null, running ? 2000 : 0);
  const scroll = useRef<HTMLDivElement>(null), bottom = useRef(true);
  const [following, setFollowing] = useState(true);
  const closePreview = useCallback(() => { previewOrigin.current = null; setPreview(undefined); }, []);
  const openPreview = useCallback((candidate: Artifact, item?: string) => { previewOrigin.current = { item, index: candidate.index }; setPanel(null); setPreview(candidate); }, []);
  useEffect(() => { setPanel(null); setFilePath(""); previewOrigin.current = null; setPreview(undefined); setError(""); setUploadNotice(""); bottom.current = true; setFollowing(true); }, [taskId, projectId]);
  useEffect(() => {
    const origin = previewOrigin.current;
    if (!origin) return;
    let candidate: Artifact | undefined;
    if (origin.item) {
      const item = task?.items.find(item => item.id === origin.item);
      if (item?.text) candidate = previewArtifact(artifacts(item.text), origin.index || 0);
    } else if (origin.path && file?.path === origin.path && file.text != null) {
      candidate = fileArtifact(origin.path, file.text);
    }
    if (candidate) setPreview(current => current && (current.source !== candidate!.source || current.complete !== candidate!.complete) ? candidate : current);
  }, [task?.items, file?.text, file?.path]);
  useEffect(() => {
    if (projects && !projects.items.some(p => p.id === selected)) select(projects.items[0]?.id || "");
  }, [projects?.items, selected, select]);
  useEffect(() => {
    if (!panel) return;
    const origin = document.activeElement;
    const frame = requestAnimationFrame(() => filesPanel.current?.focus({ preventScroll: true }));
    return () => {
      cancelAnimationFrame(frame);
      if (origin instanceof HTMLElement && origin.isConnected) origin.focus({ preventScroll: true });
    };
  }, [panel]);
  useEffect(() => {
    const element = scroll.current;
    if (!element) return;
    // The new-task welcome is read from the top; only task output follows the tail.
    if (!taskId) { element.scrollTop = 0; return; }
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      if (bottom.current) frame = requestAnimationFrame(() => { if (bottom.current) element.scrollTop = element.scrollHeight; });
    });
    observer.observe(element);
    if (element.firstElementChild) observer.observe(element.firstElementChild);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [taskId, !!task]);
  const reason = !status ? t("正在检查 Agent…", "Checking Agent…")
    : !status.installed ? t("安装包缺少 Codex 组件，请更新 Studio。", "Codex is missing from this installation. Update Studio.")
    : !status.sandbox_ready ? t("Agent 执行沙箱尚未就绪，请完成安装配置。", "The Agent execution sandbox needs installation setup.")
    : !status.model.ready ? t("启动一个支持工具调用的模型，即可开始任务。", "Start a model with tool calling to begin a task.")
    : !status.model.tool_calling ? t("请在模型启动预设中启用工具调用。", "Enable tool calling in the model's launch profile.")
    : source && !taskId && !sourceChat ? sourceError || t("正在读取背景对话…", "Loading conversation context…") : "";
  async function submit(text = draft) {
    if (sending.current || !text.trim()) return;
    let prompt = text.trim(), operation = "turn";
    const command = parseAgentCommand(prompt);
    setNotice("");
    try {
      if (prompt.startsWith("/")) {
        if (!command || !agentCommands.some(([name]) => name === command.name)) throw new Error(t("这个命令尚未接入。输入 /help 查看可用命令；命令不会作为普通消息发送。", "This command is not connected. Use /help for available commands; it was not sent to the model."));
        switch (command.name) {
          case "help": setInfoPanel("help"); setDraft(""); return;
          case "status": setInfoPanel("status"); setDraft(""); return;
          case "model": setInfoPanel("model"); setDraft(""); return;
          case "permissions":
            if (running) throw new Error(t("本轮权限已固定，结束后可更改。", "Permissions are bound to the running turn; change them after it finishes."));
            if (command.argument) {
              if (!["read-only", "workspace-write"].includes(command.argument)) throw new Error(t("使用 /permissions read-only 或 workspace-write", "Use /permissions read-only or workspace-write"));
              setPermission(command.argument as typeof permission);
            } else setInfoPanel("permissions");
            setDraft(""); return;
          case "plan":
            if (running) throw new Error(t("请先停止当前任务，再切换模式。", "Stop the current task before switching modes."));
            setMode(current => current === "plan" ? "default" : "plan"); setDraft(command.argument); return;
          case "new": if (projectId) select(projectId); setDraft(""); await navigate({ to: "/agent" }); return;
          case "resume": setInfoPanel("history"); setDraft(""); return;
          case "diff": setPreview(undefined); setPanel("diff"); setDraft(""); return;
          case "mention": setPreview(undefined); setPanel("files"); setDraft(""); return;
          case "stop": if (taskId && running) await mutation(`/api/agent/tasks/${taskId}/cancel`); setDraft(""); return;
          case "copy": {
            const answer = task && latestAgentAnswer(task.items);
            if (!answer) throw new Error(t("还没有可复制的回复。", "There is no response to copy yet."));
            await copyText(answer); setNotice(t("已复制最近回复", "Last response copied")); setDraft(""); return;
          }
          case "export": {
            if (!task) throw new Error(t("请先打开一个任务。", "Open a task first."));
            const content = task.items.map(item => `## ${item.type}\n\n${agentItemText(item)}`).join("\n\n");
            const url = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
            const link = document.createElement("a"); link.href = url; link.download = `agent-${task.id}.md`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); setDraft(""); return;
          }
          case "init": prompt = "Inspect this project and create or improve AGENTS.md with accurate project structure, development commands, and testing guidance. Verify commands from the actual files. " + command.argument; break;
          case "review": case "compact": case "skills": operation = command.name; prompt = command.argument || `/${command.name}`; break;
        }
      }
      if (!project || (running && !(canSteer && operation === "turn")) || (reason && operation !== "skills")) throw new Error(reason || t("请先选择项目，并等待当前任务完成。", "Choose a project and wait for the current task to finish."));
    } catch (e) { setError((e as Error).message); return; }
    sending.current = true; setBusy(true); setError("");
    try {
      if (!taskId && sourceChat && operation === "turn") {
        const context = sourceChat.messages.map(m => `${m.role}: ${m.content.filter(p => p.type === "text").map(p => p.text || "").join("")}`).join("\n\n");
        prompt = `${t("背景对话", "Conversation context")}:\n${context.slice(-32000)}\n\n${t("当前任务", "Task")}:\n${prompt}`;
      }
      const requestedThinking = status?.model.thinking?.supported ? thinking : false;
      const identity = JSON.stringify({ prompt, operation, permission, mode, thinking: requestedThinking, turn: running ? task?.current_turn : undefined });
      const requestId = submission?.text === identity ? submission.id : newMessageId();
      setSubmission({ text: identity, id: requestId });
      const result = running
        ? await mutation<AgentTask>(`/api/agent/tasks/${taskId}/steer`, { prompt, request_id: requestId, turn_id: task?.current_turn })
        : await mutation<AgentTask>(taskId ? `/api/agent/tasks/${taskId}/continue` : "/api/agent/tasks",
        taskId ? { prompt, request_id: requestId, operation, permission, mode, thinking: requestedThinking } : { prompt, project_id: projectId, request_id: requestId, source_thread: source || null, operation, permission, mode, thinking: requestedThinking });
      setSubmission(null);
      setDraft(value => value === text ? "" : value);
      refreshData();
      if (currentScope.current !== scope) return; bottom.current = true; setFollowing(true);
      // A resume has the same task id; remount the event observer explicitly.
      if (result.id === taskId) window.dispatchEvent(new CustomEvent("onecat:agent-resume", { detail: taskId }));
      else await navigate({ to: "/agent", search: { task: result.id } });
      refreshData();
    } catch (e) { if (currentScope.current === scope) setError((e as Error).message); }
    finally { sending.current = false; setBusy(false); }
  }
  async function upload(list: FileList | null) {
    if (!list || !projectId || running || busy) return;
    const abort = new AbortController(); uploadController.current = abort;
    setBusy(true); setError(""); setUploadNotice("");
    try {
      const excluded = new Set([".git", ".codex", "node_modules", ".venv", "__pycache__"]);
      const chosen = Array.from(list).filter(file => !(file.webkitRelativePath || file.name).split("/").some(part => excluded.has(part)));
      if (chosen.some(file => file.size > 10 * 1024 * 1024)) throw new Error(t("每个文件最多 10 MiB，请移除较大的文件后重试。", "Each file is limited to 10 MiB. Remove oversized files and retry."));
      for (const file of chosen) {
        const form = new FormData(); form.append("file", file);
        await api(`/api/agent/projects/${projectId}/files?path=${encodeURIComponent(file.webkitRelativePath || file.name)}`, { method: "POST", body: form, signal: abort.signal });
        if (abort.signal.aborted) return;
      }
      if (currentScope.current !== scope) return;
      setUploadNotice(t(`已上传 ${chosen.length} 个文件${list.length > chosen.length ? `，跳过 ${list.length - chosen.length} 个环境或缓存文件` : ""}`, `Uploaded ${chosen.length} files${list.length > chosen.length ? `; skipped ${list.length - chosen.length} environment or cache files` : ""}`));
      setPanel("files"); refreshData();
    } catch (e) { if (!abort.signal.aborted && currentScope.current === scope) setError((e as Error).message); }
    finally { if (uploadController.current === abort) uploadController.current = null; setBusy(false); if (fileInput.current) fileInput.current.value = ""; if (folderInput.current) folderInput.current.value = ""; }
  }
  function previewFile() {
    if (!file?.text || file.path !== filePath) return;
    const candidate = fileArtifact(filePath, file.text);
    if (candidate) { previewOrigin.current = { path: filePath }; setPreview(candidate); setPanel(null); }
  }
  const previewable = /\.(html?|svg|jsx|tsx|js|css)$/i.test(filePath);
  return <div className="oc-agent-workspace">
    <div className={"oc-agent-main" + (!taskId ? " oc-agent-empty" : "")}>
      <div className="oc-agent-toolbar">
        <div className="oc-agent-project-select"><FolderOpen size={17} />
          <select aria-label={t("Agent 项目", "Agent project")} value={projectId} disabled={!!taskId || busy}
            onChange={e => select(e.target.value)}><option value="">{t("选择项目", "Choose a project")}</option>
            {projects?.items.map(p => <option value={p.id} key={p.id}>{p.name}</option>)}</select>
          {!taskId && <Button size="icon-sm" variant="ghost" aria-label={t("新建项目", "New project")} onClick={() => setCreateOpen(true)}><Plus /></Button>}
        </div>
        <div className="oc-agent-toolbar-actions">
          {taskId && <Button variant="ghost" size="sm" asChild><Link to="/agent"><Plus />{t("新任务", "New task")}</Link></Button>}
          <Button variant="ghost" size="sm" disabled={!project} onClick={() => { setPreview(undefined); setPanel(panel === "files" ? null : "files"); }}><FileCode />{t("文件", "Files")}</Button>
          {task?.diff && <Button variant="ghost" size="sm" onClick={() => { setPreview(undefined); setPanel(panel === "diff" ? null : "diff"); }}><FileDiff />{t("修改", "Changes")}</Button>}
        </div>
      </div>
      <div className="oc-agent-scroll" ref={scroll} onWheel={e => { if (e.deltaY < 0) { bottom.current = false; setFollowing(false); } }}
        onTouchStart={() => { bottom.current = false; setFollowing(false); }} onScroll={() => {
        const el = scroll.current!; bottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 70;
        setFollowing(bottom.current);
      }}><div className="oc-agent-content">
        {!taskId && <div className="oc-agent-welcome"><div className="oc-agent-mark">02 / IDEAS INTO ACTION</div>
          <h1>LET’S BUILD.<span>{t("让想法，成为作品。", "Turn ideas into work.")}</span></h1>
          <p>{t("读取项目、修改文件、运行测试，直到交付成果。", "Read the project, edit files, run tests and deliver the result.")}</p>
          {!project && <Button onClick={() => setCreateOpen(true)}><Plus />{t("创建第一个项目", "Create a project")}</Button>}
          <div className="oc-agent-examples">{[
            ["做一个可预览的贪吃蛇游戏", "Build a snake game I can preview"],
            ["检查项目，修复问题并运行测试", "Inspect this project, fix issues and run tests"],
            ["整理项目结构并完善说明文档", "Organize the project and improve its documentation"],
          ].map(([zh, en]) => <button key={en} onClick={() => setDraft(t(zh, en))}>{t(zh, en)}<ArrowUp size={14} /></button>)}</div>
          <small><ShieldCheck size={14} />{t("项目隔离 · 本地模型 · 文件与执行记录保留", "Isolated project · Local model · Saved files and execution history")}</small>
        </div>}
        {taskId && !task && !connectionError && <div className="oc-empty"><LoaderCircle className="animate-spin" /></div>}
        {!!task?.plan?.length && <details className="oc-agent-activity oc-agent-plan" open>
          <summary><Check size={15} /><span>{t("执行计划", "Task plan")}</span><small>{task.plan.filter(step => step.status === "completed").length} / {task.plan.length}</small></summary>
          <ol>{task.plan.map((step, index) => <li key={index} data-state={step.status}>
            {step.status === "completed" ? <Check size={14} /> : step.status === "inProgress" && running ? <LoaderCircle size={14} className="animate-spin" /> : <span className="oc-agent-plan-dot" />}
            <span>{step.step}</span></li>)}</ol>
        </details>}
        {task?.items.map(item => <Activity key={item.id} item={item} running={running && !item.finished}
          onPreview={openPreview} />)}
        {!!task?.changes?.length && !running && <div className="oc-agent-result"><strong>{t("本轮文件修改", "Files changed this turn")}</strong>
          {task.changes.map(change => <button key={change.path} onClick={() => { setPanel(change.kind === "deleted" ? "diff" : "files"); setPreview(undefined); setFilePath(change.path); }}>
            <FileCode size={15} /><span>{change.path}</span><small>{change.kind === "added" ? t("新增", "Added") : change.kind === "deleted" ? t("删除", "Deleted") : t("修改", "Modified")}</small>
          </button>)}</div>}
        {task?.files_truncated && !running && <p className="oc-agent-note">{t("项目超过 2,000 个文件，仅展示已核对的修改。", "This project exceeds 2,000 files; only verified changes are shown.")}</p>}
      </div></div>
      {!following && running && <Button className="oc-agent-follow" size="sm" variant="secondary" onClick={() => { bottom.current = true; setFollowing(true); scroll.current?.scrollTo({ top: scroll.current.scrollHeight }); }}><ArrowDown />{t("查看最新进展", "Latest activity")}</Button>}
      <div className="oc-agent-bottom">
        <ErrorNotice error={error || task?.error || task?.file_error || projectsError} />
        {connectionError && <p role="status">{connectionError === "reconnecting" ? t("连接中断，正在恢复进展…", "Connection lost. Reconnecting to the task…") : connectionError}</p>}
        {task?.approvals.map(approval => <ApprovalCard key={approval.id} approval={approval} taskId={task.id} onError={setError} />)}
        {source && !taskId && <p className="oc-agent-source"><MessageContext />{t("引用对话", "Using conversation")}: {sourceChat?.thread.title || sourceError || t("读取中…", "Loading…")}
          <Button variant="ghost" size="icon-sm" aria-label={t("移除对话背景", "Remove conversation context")} onClick={() => navigate({ to: "/agent" })}><X /></Button></p>}
        {!running && (!project || reason) && <div className="oc-readiness oc-agent-readiness" role="status">
          {!project && <div className="oc-readiness-steps">
            <span data-ready={false}><FolderOpen size={14} />{t("选择项目", "Choose a project")}</span>
            <span data-ready={!!status?.model.ready}>{status?.model.ready ? <Check size={14} /> : <Square size={14} />}{t("模型就绪", "Model ready")}</span>
            <span data-ready={!!status?.model.tool_calling}>{status?.model.tool_calling ? <Check size={14} /> : <Square size={14} />}{t("工具调用", "Tool calling")}</span>
          </div>}
          <div className="oc-actions">
            {!project && <Button type="button" variant="outline" size="sm" onClick={() => setCreateOpen(true)}>{t("创建项目", "Create project")}</Button>}
            {reason && (status && (!status.installed || !status.sandbox_ready)
              ? <Button variant="outline" size="sm" asChild><Link to="/setup">{t("准备运行环境", "Prepare runtime")}</Link></Button>
              : <Button variant="outline" size="sm" onClick={() => setInfoPanel("model")}>{t("选择已下载模型", "Choose downloaded model")}</Button>)}
          </div>
          {reason && <p className="oc-muted">{reason}</p>}
        </div>}
        {task && !running && ["failed", "interrupted", "cancelled"].includes(task.state) && <Button variant="ghost" size="sm" disabled={busy || !!reason} onClick={() => void submit(t("继续刚才的任务，先核实已完成的操作，避免重复修改。", "Continue the previous task. Check completed actions first to avoid duplicate edits."))}><RotateCcw size={14} />{t("继续这个任务", "Resume this task")}</Button>}
        {notice && <p className="oc-agent-note" role="status">{notice}</p>}
        <form className="oc-agent-composer" onSubmit={e => { e.preventDefault(); void submit(); }}>
          {commands.length > 0 && <div className="oc-agent-commands" id="agent-command-list" role="listbox" aria-label={t("斜杠命令", "Slash commands")}>
            {commands.map(([name, zh, en], index) => <button type="button" role="option" aria-selected={index === selectedCommand} id={`agent-command-${name}`} key={name}
              onMouseDown={e => e.preventDefault()} onClick={() => chooseCommand(name)}><strong>/{name}</strong><span>{t(zh, en)}</span></button>)}
          </div>}
          <Textarea ref={input} rows={1} aria-label={t("Agent 任务", "Agent task")} value={draft} maxLength={30000} readOnly={busy}
            aria-autocomplete="list" aria-controls={commands.length ? "agent-command-list" : undefined} aria-activedescendant={commands[selectedCommand] ? `agent-command-${commands[selectedCommand][0]}` : undefined}
            onChange={e => { setDraft(e.target.value); setCommandIndex(0); setCommandDismissed(false); }}
            placeholder={taskId ? t("补充要求，或输入 / 查看命令…", "Add instructions, or type / for commands…") : t("你想完成什么？输入 / 查看命令", "What would you like to build? Type / for commands")}
            onKeyDown={e => {
              if (e.nativeEvent.isComposing) return;
              if (commands.length) {
                if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setCommandDismissed(true); return; }
                if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); const next = (selectedCommand + (e.key === "ArrowDown" ? 1 : commands.length - 1)) % commands.length; setCommandIndex(next); document.getElementById(`agent-command-${commands[next][0]}`)?.scrollIntoView({ block: "nearest" }); return; }
                if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) { e.preventDefault(); chooseCommand(commands[selectedCommand][0]); return; }
              }
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(); }
            }} />
          <div className="oc-agent-compose-actions">
            <input hidden type="file" multiple ref={fileInput} onChange={e => void upload(e.target.files)} />
            <input hidden type="file" multiple ref={element => { folderInput.current = element; element?.setAttribute("webkitdirectory", ""); }} onChange={e => void upload(e.target.files)} />
            <Button type="button" size="icon-sm" variant="ghost" aria-label={t("上传文件", "Upload files")} title={t("上传文件", "Upload files")} disabled={!project || running || busy} onClick={() => fileInput.current?.click()}><Plus size={18} /></Button>
            <button type="button" className="oc-agent-permission" disabled={running || busy} onClick={() => setInfoPanel("permissions")}><ShieldCheck size={14} /><span>{mode === "plan" ? t("计划 · 只读", "Plan · Read only") : (running ? task?.execution_permission : permission) === "read-only" ? t("只读", "Read only") : t("项目写入", "Workspace write")}</span><ChevronDown size={12} /></button>
            <ThinkingToggle value={thinking ?? task?.thinking ?? false} onChange={setThinking} support={status?.model.thinking} disabled={running || busy} />
            <ModelPicker agent compact disabled={running || busy} opened={infoPanel === "model"} onOpenChange={open => setInfoPanel(open ? "model" : null)} />
            {running && canSteer && draft.trim() && <Button type="submit" size="icon" aria-label={t("补充要求", "Add instructions")} disabled={busy}>{busy ? <LoaderCircle className="animate-spin" /> : <ArrowUp />}</Button>}
            {running ? <Button type="button" size="icon" aria-label={t("停止任务", "Stop task")} disabled={task?.state === "stopping"}
              onClick={async () => { try { await mutation(`/api/agent/tasks/${taskId}/cancel`); } catch (e) { setError((e as Error).message); } }}><Square size={17} /></Button>
              : <Button type="submit" size="icon" aria-label={taskId ? t("继续任务", "Continue task") : t("开始任务", "Start task")}
                disabled={busy || !draft.trim() || (!draft.startsWith("/") && (!project || !!reason || (!!taskId && !task)))}>{busy ? <LoaderCircle className="animate-spin" /> : <ArrowUp />}</Button>}
          </div>
        </form>
        <div className="oc-agent-status">
          <span role="status">{task ? <>{running ? <LoaderCircle size={13} className="animate-spin" /> : task.state === "completed" ? <Check size={13} /> : <Square size={12} />}{label(task.state, t)}</> : !reason && project ? t("准备就绪", "Ready") : ""}</span>
          {task && <AgentStats task={task} onDetails={() => setInfoPanel("status")} />}
        </div>
      </div>
    </div>
    <AnimatePresence initial={false}>
      {panel && <motion.aside key="project-panel" ref={filesPanel} tabIndex={-1} aria-label={t("项目文件面板", "Project file panel")} className="oc-agent-files" initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 16 }} transition={{ duration: enabled ? MOTION.panel : 0 }}
        onKeyDown={e => {
          if (e.key === "Escape") { e.preventDefault(); setPanel(null); }
          if (e.key === "Tab" && matchMedia("(max-width:767px)").matches) {
            const controls = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input,select,textarea,[tabindex="0"]'));
            const first = controls[0], last = controls.at(-1);
            if (e.shiftKey && (document.activeElement === first || document.activeElement === e.currentTarget)) { e.preventDefault(); last?.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
          }
        }}>
        <header><strong>{panel === "diff" ? t("本轮修改对比", "Changes this turn") : t("项目文件", "Project files")}</strong>
          <div>{projectId && <Button size="icon-sm" variant="ghost" asChild aria-label={t("下载项目", "Download project")}><a href={`/api/agent/projects/${projectId}/download`} download><Download /></a></Button>}
            <Button size="icon-sm" variant="ghost" aria-label={t("关闭文件面板", "Close files")} onClick={() => setPanel(null)}><X /></Button></div></header>
        {panel === "diff" ? <pre className="oc-agent-diff">{task?.diff || t("本轮没有可展示的文件修改。", "No file changes to display for this turn.")}</pre> : <>
          <div className="oc-agent-file-tools"><Button size="sm" variant="ghost" disabled={!project || running || busy} onClick={() => folderInput.current?.click()}><Upload />{t("上传文件夹", "Upload folder")}</Button>
            <small>{t("单文件 10 MiB · 项目 256 MiB", "10 MiB/file · 256 MiB/project")}</small></div>
          <ErrorNotice error={filesError} />
          {uploadNotice && <p className="oc-agent-note" role="status">{uploadNotice}</p>}
          <div className="oc-agent-file-list">{files?.items.map(f => <button key={f.path} className={filePath === f.path ? "is-active" : ""} onClick={() => setFilePath(f.path)}><FileCode size={14} /><span>{f.path}</span></button>)}
            {files && !files.items.length && <p>{t("上传文件，或让 Agent 创建项目。", "Upload files or let the agent create them.")}</p>}
            {files?.truncated && <p>{t("仅显示前 2,000 个文件", "Showing the first 2,000 files")}</p>}</div>
          {filePath && <div className="oc-agent-file-source"><div><strong>{filePath}</strong><Button size="sm" variant="ghost" onClick={() => { setDraft(current => `${current}${current ? " " : ""}@${filePath} `); setPanel(null); requestAnimationFrame(() => input.current?.focus()); }}>{t("引用", "Mention")}</Button>{previewable && file?.text && file.path === filePath && <Button size="sm" variant="secondary" onClick={previewFile}><Play />{t("预览", "Preview")}</Button>}</div>
            <ErrorNotice error={fileError} />{file?.path === filePath && (file.binary ? <p>{t("二进制文件，请下载项目查看。", "Download the project to view this binary file.")}</p> : <pre>{file.text}</pre>)}</div>}
        </>}
      </motion.aside>}
    </AnimatePresence>
    <AnimatePresence initial={false}>{preview && <PreviewBoundary key={`${taskId}:${preview.index}`} onClose={closePreview}>
      <PreviewPanel artifact={preview} onClose={closePreview} streaming={running} />
    </PreviewBoundary>}</AnimatePresence>
    <Modal open={!!infoPanel && infoPanel !== "model"} onOpenChange={open => { if (!open) setInfoPanel(null); }} title={infoPanel === "help" ? t("Agent 命令", "Agent commands") : infoPanel === "history" ? t("继续任务", "Resume a task") : infoPanel === "permissions" ? t("工作方式", "Working mode") : t("模型与任务信息", "Model and task details")}>
      {infoPanel === "permissions" ? <div className="oc-agent-details">
        <Field label={t("工作模式", "Mode")}><select value={mode} disabled={running || busy} onChange={e => setMode(e.target.value as typeof mode)}><option value="default">{t("执行：读取、修改并测试", "Execute: read, edit and test")}</option><option value="plan">{t("计划：分析与规划，不写入项目", "Plan: analyze and plan without editing")}</option></select></Field>
        <Field label={t("项目权限", "Project permissions")}><select ref={permissionInput} value={mode === "plan" ? "read-only" : permission} disabled={running || busy || mode === "plan"} onChange={e => setPermission(e.target.value as typeof permission)}><option value="workspace-write">{t("项目写入", "Workspace write")}</option><option value="read-only">{t("只读", "Read only")}</option></select></Field>
        <p className="oc-agent-note">{t("仅能访问当前项目，外网关闭。设置在下一轮任务生效。", "Access is limited to this project; networking is off. Changes apply to the next turn.")}</p>
        <Button onClick={() => setInfoPanel(null)}>{t("完成", "Done")}</Button>
      </div> : infoPanel === "history" ? <AgentHistory /> : infoPanel === "help" ? <div className="oc-agent-command-help">
        {agentCommands.map(([name, zh, en]) => <button type="button" key={name} onClick={() => { setInfoPanel(null); chooseCommand(name); }}><code>/{name}</code><span>{t(zh, en)}</span></button>)}
        <p className="oc-agent-note">{t("使用内置官方 Codex App Server。项目读写、命令执行、审查、计划、压缩和技能在项目沙箱中运行。云端账户、外部 MCP / Apps、多 Agent 和任意外网访问尚未接入。", "Uses the bundled official Codex App Server. Files, commands, review, plan, compaction and skills run in the project sandbox. Cloud accounts, external MCP / Apps, multiple agents and unrestricted networking are not connected.")}</p>
      </div> : <div className="oc-agent-details">
        <dl><dt>{t("当前模型", "Current model")}</dt><dd>{status?.model.label || status?.model.name || "—"}</dd>
          {task && <><dt>{t("本任务上次使用", "Last used for this task")}</dt><dd>{task.model_label || task.model}</dd></>}
          <dt>Codex</dt><dd>{task?.codex_version || status?.version || "—"}</dd>
          <dt>{task ? t("本轮执行权限", "Turn permissions") : t("权限", "Permissions")}</dt><dd>{(task ? task.execution_permission : mode === "plan" ? "read-only" : permission) === "read-only" ? t("只读项目 · 外网关闭", "Read-only project · Network off") : t("可写当前项目 · 外网关闭", "Writable project · Network off")}</dd>
          <dt>{t("总上下文", "Context window")}</dt><dd>{format(task?.context_window || status?.model.context_window, 0)} tokens</dd>
          <dt>{t("Agent 上下文预算", "Agent context budget")}</dt><dd>{task?.context_usage?.modelContextWindow ? format(task.context_usage.modelContextWindow, 0) + " tokens" : "—"}</dd>
          <dt>{t("最近上下文用量", "Latest context usage")}</dt><dd>{task?.context_usage?.last ? format(task.context_usage.last.totalTokens, 0) + " tokens" : "—"}</dd>
          <dt>{t("速度来源", "Speed source")}</dt><dd>{t("本机 token 流观测", "Local token stream observation")}</dd>
        </dl>
        {task && <AgentStats task={task} onDetails={() => {}} details />}
        <p className="oc-agent-note">{t("LLM 为模型请求的实际耗时，包含等待和生成；工具耗时来自官方执行事件。TTFT 为成功请求的首 token 平均等待时间。速率只计算有完整 token ID 与计时的输出，排除首批 token 和工具执行；缺少数据或中断时显示 —。输入、输出、缓存为本任务累计用量。Agent 会预留上下文空间，因此预算可能小于模型总上下文。", "LLM time includes request waiting and generation; tool time comes from official execution events. TTFT averages successful requests. Speed uses complete token IDs and arrival times, excluding the first batch and tool time; missing or interrupted data shows —. Token totals are cumulative. Codex reserves context space, so its budget can be smaller than the model context window.")}</p>
        <Button asChild variant="outline"><Link to="/models">{t("选择或加载模型", "Choose or load a model")}</Link></Button>
      </div>}
    </Modal>
    <Modal open={createOpen} onOpenChange={setCreateOpen} title={t("新建 Agent 项目", "New Agent project")}>
      <form onSubmit={async e => {
        e.preventDefault(); if (creating) return; setCreating(true); setError("");
        try { const result = await mutation<Project>("/api/agent/projects", { name, repository: repository.trim() || null }); if (!taskId) pendingProjectDraft.current = { id: result.id, text: draft }; select(result.id); setCreateOpen(false); setName(""); setRepository(""); refreshData(); }
        catch (e) { setError((e as Error).message); } finally { setCreating(false); }
      }}>
        <Field label={t("项目名称", "Project name")}><Input autoFocus value={name} onChange={e => setName(e.target.value)} maxLength={80} required /></Field>
        <Field label={t("GitHub 仓库（可选）", "GitHub repository (optional)")} hint={t("支持公开仓库；留空创建空白项目。", "Public repositories only. Leave empty for a blank project.")}><Input value={repository} onChange={e => setRepository(e.target.value)} placeholder="https://github.com/owner/repository" /></Field>
        <p className="oc-agent-note">{t("文件保存在这台服务器的独立项目目录，可上传文件或下载整个项目。", "Files live in an isolated project on this server. Upload files or download the entire project.")}</p>
        <ErrorNotice error={error} /><Button type="submit" disabled={creating || !name.trim()}>{creating ? <LoaderCircle className="animate-spin" /> : <FolderOpen />}{creating ? t("正在创建…", "Creating…") : t("创建项目", "Create project")}</Button>
      </form>
    </Modal>
  </div>;
}
function MessageContext() { return <GitBranch size={14} />; }

const Activity = memo(function Activity({ item, running, onPreview }: { item: AgentItem; running: boolean; onPreview: (artifact: Artifact, item?: string) => void }) {
  const t = useText();
  const [expanded, setExpanded] = useState(false);
  if (item.type === "commandMessage") return <div className="oc-agent-command-message"><Terminal size={13} />{item.text}</div>;
  if (item.type === "contextCompaction") return <p className="oc-agent-note">{item.finished ? t("上下文已压缩，文件与任务记录保留。", "Context compacted; project files and task history retained.") : t("正在压缩上下文…", "Compacting context…")}</p>;
  if (item.type === "enteredReviewMode") return <p className="oc-agent-note">{t("审查项目 · 只读", "Reviewing project · Read only")}</p>;
  if (item.type === "exitedReviewMode") return <div className="oc-agent-answer"><Markdown text={item.review || t("审查完成", "Review completed")} streaming={false} /></div>;
  if (item.type === "plan") return <div className="oc-agent-answer"><strong>{t("建议计划", "Proposed plan")}</strong><Markdown text={item.text || ""} streaming={running} /></div>;
  if (item.type === "userMessage") return <div className="oc-agent-user">{item.text}</div>;
  if (item.type === "agentMessage") return <div className="oc-agent-answer"><Markdown text={item.text || ""} streaming={running}
    onPreview={index => { const candidate = previewArtifact(artifacts(item.text || ""), index); if (candidate) onPreview(candidate, item.id); }} />
    {!running && item.text && <CopyButton value={item.text} />}</div>;
  if (item.type === "reasoning") return <details className="oc-agent-activity" onToggle={e => setExpanded(e.currentTarget.open)}>
    <summary><Bot size={15} /><span>{t("思考过程", "Reasoning")}</span><ChevronDown size={14} /></summary>
    {expanded && <Markdown text={item.text || item.summary?.join("\n") || ""} streaming={running} />}</details>;
  if (item.type === "commandExecution" || item.type === "fileChange") return <details className="oc-agent-activity" onToggle={e => setExpanded(e.currentTarget.open)}>
    <summary>{item.type === "commandExecution" ? <Terminal size={15} /> : <FileDiff size={15} />}<span>{item.type === "commandExecution" ? item.command : t("修改文件", "Editing files")}</span>
      <small>{item.exitCode != null && item.exitCode !== 0 ? t(`退出码 ${item.exitCode}`, `Exit ${item.exitCode}`) : item.status === "completed" ? <Check size={14} /> : item.status === "inProgress" && running ? <LoaderCircle size={14} className="animate-spin" /> : item.status}</small><ChevronDown size={14} /></summary>
    {expanded && <pre>{item.aggregatedOutput || item.changes?.map(change => `${change.path}\n${change.diff || ""}`).join("\n") || t("没有输出", "No output")}</pre>}</details>;
  return null;
});

function ApprovalCard({ approval, taskId, onError }: { approval: Approval; taskId: string; onError: (e: string) => void }) {
  const t = useText();
  const [busy, setBusy] = useState(false), [answers, setAnswers] = useState<Record<string, string>>({});
  async function respond(decision: string) {
    if (busy) return; setBusy(true);
    try { await mutation(`/api/agent/tasks/${taskId}/approvals/${approval.id}`, { decision,
      answers: approval.method === "item/tool/requestUserInput" ? Object.fromEntries(Object.entries(answers).map(([key, answer]) => [key, { answers: [answer] }])) : null }); }
    catch (e) { onError((e as Error).message); } finally { setBusy(false); }
  }
  const questions = approval.params.questions;
  return <div className="oc-agent-approval"><strong>{questions ? t("需要你的补充", "Your input is needed") : t("确认本次操作", "Approve this action")}</strong>
    {approval.params.reason && <p>{approval.params.reason}</p>}{approval.params.command && <pre>{approval.params.command}</pre>}
    {approval.params.grantRoot && <p>{approval.params.grantRoot}</p>}
    {questions?.map(q => <Field key={q.id} label={q.question}>
      {q.options && <div className="oc-agent-options">{q.options.map(option => <Button key={option.label} size="sm" variant={answers[q.id] === option.label ? "secondary" : "outline"} title={option.description} onClick={() => setAnswers(current => ({ ...current, [q.id]: option.label }))}>{option.label}</Button>)}</div>}
      <Input value={answers[q.id] || ""} onChange={e => setAnswers(current => ({ ...current, [q.id]: e.target.value }))} /></Field>)}
    <div><Button size="sm" disabled={busy || !!questions?.some(q => !answers[q.id]?.trim())} onClick={() => void respond("accept")}>{busy && <LoaderCircle className="animate-spin" />}{questions ? t("提交回复", "Reply") : t("允许这一次", "Allow once")}</Button>
      {!questions && <Button size="sm" variant="ghost" disabled={busy} onClick={() => void respond("decline")}>{t("拒绝", "Decline")}</Button>}</div>
  </div>;
}
