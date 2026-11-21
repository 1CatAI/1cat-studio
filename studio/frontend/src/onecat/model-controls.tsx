// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useEffect, useId, useRef, useState, type CSSProperties } from "react";
import { Popover } from "radix-ui";
import { ArrowUpRight, Check, ChevronDown, Library, LoaderCircle, Search, SlidersHorizontal } from "lucide-react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useModelDialogTransition } from "./model-card-transition";
import { Button } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import { ErrorNotice, Modal, useText, jobLabel } from "./common";
import { api, mutation, refreshData, useQuery, type Engine, type Job, type Profile, type Model, type Runtime } from "./api";
import { ModelPublisherAvatar } from "./model-card";
import { modelLabel, modelPublisher } from "./model-label";
import { newProfile, ProfileEditor } from "./models-page";
import "./styles/model-controls.css";

export type ThinkingEffort = "low" | "medium" | "high" | "xhigh";
export type ThinkingSupport = { supported: boolean; reason?: string | null; efforts?: ThinkingEffort[]; default_effort?: ThinkingEffort | null };
export function thinkingEffort(support?: ThinkingSupport, wanted?: ThinkingEffort | null) {
  return wanted && support?.efforts?.includes(wanted) ? wanted : support?.default_effort || undefined;
}
// The facets fan out as the model's reasoning depth increases.
const effortDepth = { low: 1, medium: 2, high: 3, xhigh: 4 };

function ThinkingMark({ depth }: { depth: number }) {
  const top = -depth * 1.35;
  return (
    <svg className="oc-thinking-mark" viewBox="0 0 24 24" aria-hidden="true" fill="none"
      stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round">
      {[4, 3, 2, 1].map(layer => (
        <path key={layer} d="m3.5 12 8.5 4.5 8.5-4.5"
          style={{ opacity: layer <= depth ? 1 : 0, transform: `translateY(${top + Math.min(depth, layer) * 2.8}px)` }} />
      ))}
      <path className="oc-thinking-facet" d="m3.5 12 8.5-4.5 8.5 4.5-8.5 4.5Z"
        style={{ transform: `translateY(${top}px)` }} />
    </svg>
  );
}

export function ThinkingToggle({ value, effort, onChange, support, disabled }: {
  value: boolean; effort?: ThinkingEffort | null; onChange: (value: boolean, effort?: ThinkingEffort) => void; support?: ThinkingSupport; disabled?: boolean;
}) {
  const t = useText();
  const hintId = useId();
  const levels = support?.supported ? support.efforts || [] : [];
  const chosen = thinkingEffort(support, effort);
  const index = value && levels.length ? Math.max(0, levels.indexOf(chosen!)) + 1 : 0;
  const labels = { low: t("低", "Low"), medium: t("中", "Medium"), high: t("高", "High"), xhigh: t("极高", "Extra high") };
  const names = [t("关闭", "Off"), ...levels.map(level => labels[level])];
  const label = names[index];
  const depth = index ? effortDepth[levels[index - 1]] : 0;
  function select(position: number) { onChange(position > 0, levels[position - 1] || chosen); }

  if (!levels.length) return (
    <button type="button" className="oc-thinking-control" aria-label={t("深度思考", "Thinking")}
      aria-pressed={!!support?.supported && value} disabled={disabled || !support?.supported}
      title={support?.supported ? t("控制下一次请求的思考输出", "Control thinking in the next request") : support?.reason || t("正在检查模型能力", "Checking model capabilities")}
      onClick={() => onChange(!value)}>
      <ThinkingMark depth={support?.supported && value ? 2 : 0} /><span>{t("思考", "Think")}</span>
    </button>
  );

  return <Popover.Root>
    <Popover.Trigger asChild>
      <button type="button" className="oc-thinking-control" aria-label={t("深度思考", "Thinking")}
        aria-pressed={value} aria-description={label} disabled={disabled}>
        <ThinkingMark depth={depth} />
        <span className="oc-thinking-caption" data-reserve={t("思考 · 极高", "Think · Extra high")}>
          <span>{t("思考", "Think")}{value ? ` · ${label}` : ""}</span>
        </span>
        <ChevronDown className="oc-thinking-chevron" size={12} aria-hidden="true" />
      </button>
    </Popover.Trigger>
    <Popover.Portal>
      <Popover.Content className="oc-thinking-popover" side="top" align="start" sideOffset={8} collisionPadding={12}
        aria-label={t("思考强度", "Thinking strength")}>
        <div className="oc-thinking-heading">
          <div className="oc-thinking-emblem" data-active={value}><ThinkingMark depth={depth} /></div>
          <strong>{t("思考强度", "Thinking strength")}</strong>
          <span className="oc-thinking-readout" data-active={value}>
            {names.map((name, position) => <span key={name} aria-hidden={position !== index}>{name}</span>)}
          </span>
        </div>
        <div className="oc-thinking-scale" style={{
          "--effort-progress": index / levels.length,
          "--effort-inset": `${50 / names.length}%`,
          "--effort-stops": names.length,
        } as CSSProperties}>
          <div className="oc-thinking-rail" aria-hidden="true">
            <div className="oc-thinking-fill" />
            {names.map((name, position) => <i key={name} data-filled={position <= index} />)}
            <div className="oc-thinking-cursor" />
          </div>
          <input type="range" className="oc-thinking-slider" min={0} max={levels.length} step={1} value={index}
            aria-label={t("思考强度", "Thinking strength")} aria-valuetext={label} aria-describedby={hintId} disabled={disabled}
            onChange={event => select(Number(event.target.value))} />
          <div className="oc-thinking-levels">{names.map((name, position) =>
            <button key={name} type="button" aria-pressed={index === position} disabled={disabled}
              onClick={() => select(position)}>{name}</button>)}</div>
        </div>
        <p id={hintId}>{t("用于下一次请求。强度越高，通常耗时越长。", "Applies to the next request. Higher effort usually takes longer.")}</p>
      </Popover.Content>
    </Popover.Portal>
  </Popover.Root>;
}
type Choice = { id: string; name: string; active: boolean; can_load: boolean; reason?: string;
  repo_id?: string | null; source?: string | null;
  default_profile_id?: string; profiles: { id: string; name: string }[] };
export function ModelPicker({ agent = false, compact = false, disabled = false, opened, onOpenChange }: { agent?: boolean; compact?: boolean; disabled?: boolean; opened?: boolean; onOpenChange?: (open: boolean) => void }) {
  const t = useText();
  const [localOpen, setLocalOpen] = useState(false), [search, setSearch] = useState("");
  const open = opened ?? localOpen;
  const setOpen = onOpenChange || setLocalOpen;
  const [error, setError] = useState(""), [pending, setPending] = useState(false);
  const [jobId, setJobId] = useState<string>();
  const [editor, setEditor] = useState<{ modelId: string; initial: Profile; profiles: Profile[] }>();
  const trigger = useRef<HTMLButtonElement>(null);
  const adjustButton = useRef<HTMLButtonElement>(null);
  const { setDetail: setEditorDetail, dismiss: dismissEditor } = useModelDialogTransition(
    adjustButton, () => setEditor(undefined), "button",
  );
  const manageAfterClose = useRef(false);
  const navigate = useNavigate();
  const { setDetail, dismiss } = useModelDialogTransition(trigger, () => {
    setOpen(false);
    if (manageAfterClose.current) {
      manageAfterClose.current = false;
      void navigate({ to: "/models" });
    }
  });
  const [requested, setRequested] = useState<string>();
  const submitting = useRef(false);
  const { data: engine, error: engineError } = useQuery<Engine>("/api/inference/status");
  const { data, error: loadError, refresh: refreshChoices } = useQuery<{ items: Choice[] }>(open ? `/api/inference/models?agent=${agent}` : null);
  const { data: jobs, error: jobError } = useQuery<{ items: Job[] }>(open && jobId ? "/api/jobs" : null, 1000);
  useEffect(() => {
    if (open && !engineError) refreshChoices();
  }, [open, engine?.profile_id, engine?.state, engine?.profile?.tool_calling, engineError, refreshChoices]);
  const job = jobs?.items.find(item => item.id === jobId);
  const applying = pending || (!!jobId && (!job || !["completed", "failed", "cancelled"].includes(job.state)));
  const publisher = modelPublisher(engine?.model?.repo_id || engine?.profile?.catalog_id);
  const matches = (choice: Choice) => `${choice.name} ${choice.repo_id || ""}`.toLocaleLowerCase().includes(search.toLocaleLowerCase());
  const inUse = (choice: Choice) => !engineError && engine?.state === "ready"
    && choice.profiles.some(profile => profile.id === engine.profile_id)
    && (!agent || !!engine.profile?.tool_calling);
  const visibleChoices = data?.items.filter(matches);
  const choicesRef = useRef<HTMLDivElement>(null);
  const [searchHeight, setSearchHeight] = useState(0);
  function changeSearch(value: string) {
    if (!search && value) setSearchHeight(choicesRef.current?.clientHeight || 0);
    setSearch(value);
  }
  async function load(choice: Choice) {
    if (submitting.current || applying || engineError || loadError) return;
    if (inUse(choice)) { dismiss(); return; }
    submitting.current = true; setPending(true); setRequested(choice.id); setError(""); setJobId(undefined);
    try {
      const result = await mutation<Job>("/api/inference/load-model", { model_id: choice.id, agent });
      setJobId(result.id); refreshData();
    } catch (e) { setError((e as Error).message); }
    finally { submitting.current = false; setPending(false); }
  }
  async function adjust(choice: Choice, button: HTMLButtonElement) {
    if (submitting.current || applying) return;
    submitting.current = true; setPending(true); setError("");
    try {
      const [saved, models, runtimes] = await Promise.all([
        api<{ items: Profile[] }>("/api/profiles"), api<{ items: Model[] }>("/api/models/list"),
        api<{ items: Runtime[] }>("/api/runtimes"),
      ]);
      const profiles = choice.profiles.map(item => saved.items.find(profile => profile.id === item.id)).filter((p): p is Profile => !!p);
      let initial = profiles[0];
      if (!initial) {
        const model = models.items.find(item => item.id === choice.id);
        if (!model) throw new Error(t("模型不存在", "Model not found"));
        initial = model.catalog_id && choice.can_load
          ? await mutation<Profile>(`/api/models/${model.id}/default-profile`)
          : newProfile(model, engine?.profile?.runtime_id || runtimes.items[0]?.id || "");
      }
      if (button.isConnected) {
        adjustButton.current = button;
        setEditor({ modelId: choice.id, initial, profiles });
      }
    } catch (e) { setError((e as Error).message); }
    finally { submitting.current = false; setPending(false); }
  }
  return <>
    <button ref={trigger} type="button" aria-haspopup="dialog" aria-expanded={open} className={compact ? "oc-agent-model" : "oc-model-trigger"}
      aria-label={t("选择已下载模型", "Choose downloaded model")} disabled={disabled}
      onClick={() => { setOpen(true); setError(""); }}>
      {!compact && <span className="oc-header-model-avatar" aria-hidden="true">
        <ModelPublisherAvatar repo={engine?.profile?.catalog_id || (engine?.model?.source === "modelscope" ? engine.model.repo_id : null)} />
        <span className={"oc-status-dot" + (!engineError && engine?.state === "ready" ? " ready" : "")} />
      </span>}
      <span>{engine?.profile ? modelLabel(engine.model?.name || engine.profile) : t("选择模型", "Select model")}</span>
      {publisher && <small className="oc-model-publisher-tag" title={engine?.model?.repo_id || engine?.profile?.catalog_id || ""}>{publisher}</small>}
      <ChevronDown size={14} /></button>
    {open && <Modal open onOpenChange={next => { if (!next && !editor) dismiss(); }} returnFocusRef={trigger} title={t("选择模型", "Select model")}>
      <div ref={setDetail} className="oc-model-search"><Search size={16} /><Input aria-label={t("搜索已下载模型", "Search downloaded models")} placeholder={t("搜索模型…", "Search models…")} value={search} onChange={e => changeSearch(e.target.value)} /></div>
      <ErrorNotice error={error || loadError || engineError || jobError || job?.error || ""} />
      {jobId && <div className="oc-model-job" role="status">{applying && <LoaderCircle size={16} className="animate-spin" />}
        <span>{jobError ? t("正在恢复启动状态…", "Reconnecting to launch progress…") : job?.state === "completed" ? t("模型已就绪", "Model ready") : job?.state === "cancelled" ? t("已取消", "Cancelled") : job?.state === "failed" ? t("启动失败", "Launch failed") : jobLabel(job?.stage || "queued", t)}</span>
        {applying && job && <Button size="sm" variant="ghost" onClick={async () => { try { await mutation(`/api/jobs/${job.id}/cancel`); refreshData(); } catch (e) { setError((e as Error).message); } }}>{t("取消启动", "Cancel launch")}</Button>}
      </div>}
      <div ref={choicesRef} className="oc-model-choices"
        style={search && searchHeight ? { minHeight: `min(${searchHeight}px, 44dvh)` } : undefined}>
        {visibleChoices?.map(choice => {
          const active = inUse(choice);
          const starting = applying && requested === choice.id;
          const [name, quantization] = modelLabel(choice.name).split(" · ");
          const publisherName = modelPublisher(choice.repo_id);
          return <div key={choice.id} className="oc-model-choice" data-active={active || undefined} data-loading={starting || undefined}>
            <ModelPublisherAvatar repo={choice.source === "modelscope" ? choice.repo_id : active ? engine?.profile?.catalog_id : null} />
            <div className="oc-model-choice-name">
              <strong title={choice.name}>{name}</strong>
              <div className="oc-model-choice-source">
                <span title={choice.repo_id || undefined}>{publisherName || t("本地模型", "Local model")}</span>
                {choice.source === "local" && publisherName && <span>{t("本地导入", "Local import")}</span>}
                {quantization && <span>{quantization}</span>}
              </div>
            </div>
            <div className="oc-model-choice-actions">
              {active ? <span className="oc-model-in-use"><Check size={14} aria-hidden="true" />{t("使用中", "In use")}</span>
                : <Button size="sm" variant="outline" disabled={!choice.can_load || applying || !!engineError || !!loadError} onClick={() => void load(choice)}>
                  {starting ? <LoaderCircle size={14} className="animate-spin" aria-label={t("正在启动", "Starting")} /> : t("使用", "Use")}
                </Button>}
              <Button size="sm" variant="ghost" className="oc-model-adjust" aria-label={t("调整", "Adjust") + " · " + name}
                title={t("调整", "Adjust")} disabled={applying} onClick={event => void adjust(choice, event.currentTarget)}><SlidersHorizontal size={16} /></Button>
            </div>
            {!choice.can_load && choice.reason && <small className="oc-model-choice-reason">{t(choice.reason.split(" / ")[0], choice.reason.split(" / ")[1] || choice.reason)}</small>}
          </div>;
        })}
        {visibleChoices?.length === 0 && <p className="oc-model-choice-empty">{t("没有匹配的已下载模型", "No matching downloaded models")}</p>}
        {!data && !loadError && <div className="oc-model-choice-empty"><LoaderCircle size={18} className="animate-spin" aria-label={t("读取模型", "Loading models")} /></div>}
      </div>
      <div className="oc-model-picker-footer"><Button variant="ghost" asChild><Link to="/models" onClick={event => {
        if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        manageAfterClose.current = true;
        dismiss();
      }}><Library size={15} />{t("管理模型", "Manage models")}<ArrowUpRight size={13} /></Link></Button></div>
      {editor && <ProfileEditor key={editor.initial.id || editor.modelId} initial={editor.initial} modelId={editor.modelId} agent={agent}
        returnFocusRef={adjustButton} transitionRef={setEditorDetail} savedProfiles={editor.profiles} onChooseProfile={initial => setEditor({ ...editor, initial })}
        onLaunch={result => { setJobId(result.id); refreshData(); }} onClose={dismissEditor} />}
    </Modal>}
  </>;
}
