// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useRef, useState, type CSSProperties } from "react";
import { Popover } from "radix-ui";
import { Brain, ChevronDown, LoaderCircle, Search } from "lucide-react";
import { Link } from "@tanstack/react-router";
import { Button } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import { ErrorNotice, Modal, useText, jobLabel } from "./common";
import { api, mutation, refreshData, useQuery, type Engine, type Job, type Profile, type Model, type Runtime } from "./api";
import { modelLabel, modelPublisher } from "./model-label";
import { newProfile, ProfileEditor } from "./models-page";
import "./styles/model-controls.css";

export type ThinkingEffort = "low" | "medium" | "high" | "xhigh";
export type ThinkingSupport = { supported: boolean; reason?: string | null; efforts?: ThinkingEffort[]; default_effort?: ThinkingEffort | null };
export function thinkingEffort(support?: ThinkingSupport, wanted?: ThinkingEffort | null) {
  return wanted && support?.efforts?.includes(wanted) ? wanted : support?.default_effort || undefined;
}
export function ThinkingToggle({ value, effort, onChange, support, disabled }: {
  value: boolean; effort?: ThinkingEffort | null; onChange: (value: boolean, effort?: ThinkingEffort) => void; support?: ThinkingSupport; disabled?: boolean;
}) {
  const t = useText();
  const levels = support?.supported ? support.efforts || [] : [];
  const chosen = thinkingEffort(support, effort);
  const index = value ? Math.max(0, levels.indexOf(chosen!)) + 1 : 0;
  const labels = { low: t("低", "Low"), medium: t("中", "Medium"), high: t("高", "High"), xhigh: t("极高", "Extra high") };
  const label = index ? labels[levels[index - 1]] : t("关闭", "Off");
  function select(position: number) { onChange(position > 0, levels[position - 1] || chosen); }
  if (levels.length) return <Popover.Root>
    <Popover.Trigger asChild><button type="button" className="oc-thinking-control" aria-label={t("深度思考", "Thinking")}
      aria-pressed={value} disabled={disabled}><Brain size={15} /><span>{t("思考", "Think")}{value ? ` · ${label}` : ""}</span><ChevronDown size={12} /></button></Popover.Trigger>
    <Popover.Portal><Popover.Content className="oc-thinking-popover" side="top" align="start" sideOffset={10} collisionPadding={16} aria-label={t("思考强度", "Thinking strength")}>
      <div className="oc-thinking-heading"><strong>{t("思考强度", "Thinking strength")}</strong><span>{label}</span></div>
      <input type="range" className="oc-thinking-slider" min={0} max={levels.length} step={1} value={index}
        aria-label={t("思考强度", "Thinking strength")} aria-valuetext={label} disabled={disabled}
        style={{ "--effort-progress": `${index / levels.length * 100}%` } as CSSProperties}
        onChange={event => select(Number(event.target.value))} />
      <div className="oc-thinking-levels">{[t("关闭", "Off"), ...levels.map(level => labels[level])].map((name, position) =>
        <button key={position} type="button" aria-pressed={index === position} disabled={disabled} onClick={() => select(position)}>{name}</button>)}</div>
      <p>{t("用于下一次请求。强度越高，通常耗时越长。", "Applies to the next request. Higher effort usually takes longer.")}</p>
    </Popover.Content></Popover.Portal>
  </Popover.Root>;
  return <button type="button" className="oc-thinking-control" aria-label={t("深度思考", "Thinking")}
    aria-pressed={!!support?.supported && value} disabled={disabled || !support?.supported}
    title={support?.supported ? t("控制下一次请求的思考输出", "Control thinking in the next request") : support?.reason || t("正在检查模型能力", "Checking model capabilities")}
    onClick={() => onChange(!value)}><Brain size={15} /><span>{t("思考", "Think")}</span></button>;
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
  const submitting = useRef(false);
  const { data: engine } = useQuery<Engine>("/api/inference/status");
  const { data, error: loadError } = useQuery<{ items: Choice[] }>(open ? `/api/inference/models?agent=${agent}` : null);
  const { data: jobs } = useQuery<{ items: Job[] }>(open && jobId ? "/api/jobs" : null, 1000);
  const job = jobs?.items.find(item => item.id === jobId);
  const applying = pending || (!!jobId && (!job || !["completed", "failed", "cancelled"].includes(job.state)));
  const publisher = modelPublisher(engine?.model?.repo_id || engine?.profile?.catalog_id);
  const matches = (choice: Choice) => `${choice.name} ${choice.repo_id || ""}`.toLocaleLowerCase().includes(search.toLocaleLowerCase());
  async function load(choice: Choice) {
    if (submitting.current || applying) return;
    if (engine?.state === "ready" && choice.active) { setOpen(false); return; }
    submitting.current = true; setPending(true); setError(""); setJobId(undefined);
    try {
      const result = await mutation<Job>("/api/inference/load-model", { model_id: choice.id, agent });
      setJobId(result.id); refreshData();
    } catch (e) { setError((e as Error).message); }
    finally { submitting.current = false; setPending(false); }
  }
  async function adjust(choice: Choice) {
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
      setEditor({ modelId: choice.id, initial, profiles });
    } catch (e) { setError((e as Error).message); }
    finally { submitting.current = false; setPending(false); }
  }
  return <>
    <button type="button" className={compact ? "oc-agent-model" : "oc-model-trigger"}
      aria-label={t("选择已下载模型", "Choose downloaded model")} disabled={disabled}
      onClick={() => { setOpen(true); setError(""); }}>
      <span>{engine?.profile ? modelLabel(engine.model?.name || engine.profile) : t("选择模型", "Select model")}</span>
      {publisher && <small className="oc-model-publisher-tag" title={engine?.model?.repo_id || engine?.profile?.catalog_id || ""}>{publisher}</small>}
      <ChevronDown size={14} /></button>
    <Modal open={open && !editor} onOpenChange={setOpen} title={t("选择模型", "Select model")}>
      <div className="oc-model-search"><Search size={16} /><Input aria-label={t("搜索已下载模型", "Search downloaded models")} placeholder={t("搜索模型…", "Search models…")} value={search} onChange={e => setSearch(e.target.value)} /></div>
      <ErrorNotice error={error || loadError || job?.error || ""} />
      {jobId && <div className="oc-model-job" role="status">{applying && <LoaderCircle size={16} className="animate-spin" />}
        <span>{job?.state === "completed" ? t("模型已就绪", "Model ready") : job?.state === "cancelled" ? t("已取消", "Cancelled") : job?.state === "failed" ? t("启动失败", "Launch failed") : jobLabel(job?.stage || "queued", t)}</span>
        {applying && job && <Button size="sm" variant="ghost" onClick={async () => { try { await mutation(`/api/jobs/${job.id}/cancel`); refreshData(); } catch (e) { setError((e as Error).message); } }}>{t("取消启动", "Cancel launch")}</Button>}
      </div>}
      <div className="oc-model-choices">
        {data?.items.filter(matches).map(m => <div key={m.id} className="oc-model-choice">
          <div className="oc-model-choice-name"><strong title={m.name}>{modelLabel(m.name)}</strong>
            <small className="oc-model-choice-source" title={m.repo_id || ""}>{modelPublisher(m.repo_id) || t("本地模型", "Local model")}{m.source === "local" && ` · ${t("本地导入", "Local import")}`}</small>
            {!m.can_load && m.reason && <small>{t(m.reason.split(" / ")[0], m.reason.split(" / ")[1] || m.reason)}</small>}
          </div>
          <div className="oc-model-choice-actions">
            <Button size="sm" variant="ghost" disabled={applying} onClick={() => void adjust(m)}>{t("调整", "Adjust")}</Button>
            <Button size="sm" variant="outline" disabled={!m.can_load || applying || (m.active && engine?.state === "ready")} onClick={() => void load(m)}>
              {m.active && engine?.state === "ready" ? t("使用中", "In use") : t("使用", "Use")}
            </Button>
          </div>
        </div>)}
        {data && !data.items.some(matches) && <p>{t("没有匹配的已下载模型", "No matching downloaded models")}</p>}
        {!data && !loadError && <LoaderCircle className="animate-spin" aria-label={t("读取模型", "Loading models")} />}
      </div>
      <Button variant="ghost" asChild><Link to="/models" onClick={() => setOpen(false)}>{t("管理模型", "Manage models")}</Link></Button>
    </Modal>
    {open && editor && <ProfileEditor key={editor.initial.id || editor.modelId} initial={editor.initial} modelId={editor.modelId} agent={agent}
      savedProfiles={editor.profiles} onChooseProfile={initial => setEditor({ ...editor, initial })}
      onLaunch={result => { setJobId(result.id); refreshData(); }} onClose={() => setEditor(undefined)} />}
  </>;
}
