// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useRef, useState } from "react";
import { Brain, Check, ChevronDown, LoaderCircle, Play, Search } from "lucide-react";
import { Link } from "@tanstack/react-router";
import { Button } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import { ErrorNotice, Modal, useText, jobLabel } from "./common";
import { mutation, refreshData, useQuery, type Engine, type Job } from "./api";
import { modelLabel } from "./model-label";
import "./styles/model-controls.css";

export type ThinkingSupport = { supported: boolean; reason?: string | null };
export function ThinkingToggle({ value, onChange, support, disabled }: {
  value: boolean; onChange: (value: boolean) => void; support?: ThinkingSupport; disabled?: boolean;
}) {
  const t = useText();
  return <button type="button" className="oc-thinking-control" aria-label={t("深度思考", "Thinking")}
    aria-pressed={!!support?.supported && value} disabled={disabled || !support?.supported}
    title={support?.supported ? t("控制下一次请求的思考输出", "Control thinking in the next request") : support?.reason || t("正在检查模型能力", "Checking model capabilities")}
    onClick={() => onChange(!value)}><Brain size={15} /><span>{t("思考", "Think")}</span></button>;
}
type Choice = { id: string; name: string; active: boolean; can_load: boolean; reason?: string;
  profiles: { id: string; name: string }[] };
export function ModelPicker({ agent = false, compact = false, disabled = false, opened, onOpenChange }: { agent?: boolean; compact?: boolean; disabled?: boolean; opened?: boolean; onOpenChange?: (open: boolean) => void }) {
  const t = useText();
  const [localOpen, setLocalOpen] = useState(false), [search, setSearch] = useState("");
  const open = opened ?? localOpen;
  const setOpen = onOpenChange || setLocalOpen;
  const [error, setError] = useState(""), [pending, setPending] = useState(false);
  const [jobId, setJobId] = useState<string>();
  const [chosenProfiles, setChosenProfiles] = useState<Record<string, string>>({});
  const submitting = useRef(false);
  const { data: engine } = useQuery<Engine>("/api/inference/status");
  const { data, error: loadError } = useQuery<{ items: Choice[] }>(open ? `/api/inference/models?agent=${agent}` : null);
  const { data: jobs } = useQuery<{ items: Job[] }>(open && jobId ? "/api/jobs" : null, 1000);
  const job = jobs?.items.find(item => item.id === jobId);
  const applying = pending || (!!jobId && (!job || !["completed", "failed", "cancelled"].includes(job.state)));
  async function load(choice: Choice) {
    if (submitting.current || applying) return;
    const profileId = chosenProfiles[choice.id] || choice.profiles[0]?.id;
    if (engine?.state === "ready" && choice.active && profileId === engine.profile_id) { setOpen(false); return; }
    submitting.current = true; setPending(true); setError(""); setJobId(undefined);
    try {
      const result = await mutation<Job>("/api/inference/load-model", { model_id: choice.id, profile_id: profileId, agent });
      setJobId(result.id); refreshData();
    } catch (e) { setError((e as Error).message); }
    finally { submitting.current = false; setPending(false); }
  }
  return <>
    <button type="button" className={compact ? "oc-agent-model" : "oc-model-trigger"}
      aria-label={t("选择已下载模型", "Choose downloaded model")} disabled={disabled}
      onClick={() => { setOpen(true); setError(""); }} title={engine?.profile?.name}>
      <span>{engine?.profile ? modelLabel(engine.profile) : t("选择模型", "Select model")}</span><ChevronDown size={14} /></button>
    <Modal open={open} onOpenChange={setOpen} title={t("选择已下载模型", "Choose downloaded model")}
      description={t("直接加载模型，自动匹配配置。对话和草稿会保留。", "Load a model with matching settings. Your conversation and draft are preserved.")}>
      <div className="oc-model-search"><Search size={16} /><Input aria-label={t("搜索已下载模型", "Search downloaded models")} placeholder={t("搜索模型…", "Search models…")} value={search} onChange={e => setSearch(e.target.value)} /></div>
      <ErrorNotice error={error || loadError || job?.error || ""} />
      {jobId && <div className="oc-model-job" role="status">{applying ? <LoaderCircle size={16} className="animate-spin" /> : job?.state === "completed" ? <Check size={16} /> : null}
        <span>{job?.state === "completed" ? t("模型已就绪", "Model ready") : job?.state === "cancelled" ? t("已取消启动", "Launch cancelled") : job?.state === "failed" ? t("启动失败，可以重试", "Launch failed; you can retry") : jobLabel(job?.stage || "queued", t)}</span>
        {applying && job && <Button size="sm" variant="ghost" onClick={async () => { try { await mutation(`/api/jobs/${job.id}/cancel`); refreshData(); } catch (e) { setError((e as Error).message); } }}>{t("取消启动", "Cancel launch")}</Button>}
      </div>}
      <div className="oc-model-choices">
        {data?.items.filter(m => m.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())).map(m => <div key={m.id} className="oc-model-choice">
          <button type="button" disabled={!m.can_load || applying} onClick={() => void load(m)}>
            <span><strong>{m.name}</strong><small>{m.reason || (m.active && engine?.state === "ready" ? t("当前模型", "Current model") : m.profiles[0]?.name || t("自动创建默认配置", "Create default configuration"))}</small></span>
            {m.active && engine?.state === "ready" ? <Check size={18} /> : <Play size={18} />}
          </button>
          {m.profiles.length > 1 && <details><summary>{t("启动选项", "Launch options")}</summary><select aria-label={t(`${m.name} 的启动配置`, `Launch configuration for ${m.name}`)} disabled={applying}
            value={chosenProfiles[m.id] || m.profiles[0].id} onChange={e => setChosenProfiles(old => ({ ...old, [m.id]: e.target.value }))}>{m.profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></details>}
        </div>)}
        {data && !data.items.some(m => m.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())) && <p>{t("没有匹配的已下载模型", "No matching downloaded models")}</p>}
        {!data && !loadError && <LoaderCircle className="animate-spin" aria-label={t("读取模型", "Loading models")} />}
      </div>
      <Button variant="ghost" asChild><Link to="/models" onClick={() => setOpen(false)}>{t("管理与下载模型", "Manage and download models")}</Link></Button>
    </Modal>
  </>;
}
