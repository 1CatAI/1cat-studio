// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useRef, useState } from "react";
import { Brain, ChevronDown, LoaderCircle, Search } from "lucide-react";
import { Link } from "@tanstack/react-router";
import { Button } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import { ErrorNotice, Modal, useText, jobLabel } from "./common";
import { api, mutation, refreshData, useQuery, type Engine, type Job, type Profile, type Model, type Runtime } from "./api";
import { modelLabel } from "./model-label";
import { newProfile, ProfileEditor } from "./models-page";
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
      <span>{engine?.profile ? modelLabel(engine.profile) : t("选择模型", "Select model")}</span><ChevronDown size={14} /></button>
    <Modal open={open && !editor} onOpenChange={setOpen} title={t("选择模型", "Select model")}>
      <div className="oc-model-search"><Search size={16} /><Input aria-label={t("搜索已下载模型", "Search downloaded models")} placeholder={t("搜索模型…", "Search models…")} value={search} onChange={e => setSearch(e.target.value)} /></div>
      <ErrorNotice error={error || loadError || job?.error || ""} />
      {jobId && <div className="oc-model-job" role="status">{applying && <LoaderCircle size={16} className="animate-spin" />}
        <span>{job?.state === "completed" ? t("模型已就绪", "Model ready") : job?.state === "cancelled" ? t("已取消", "Cancelled") : job?.state === "failed" ? t("启动失败", "Launch failed") : jobLabel(job?.stage || "queued", t)}</span>
        {applying && job && <Button size="sm" variant="ghost" onClick={async () => { try { await mutation(`/api/jobs/${job.id}/cancel`); refreshData(); } catch (e) { setError((e as Error).message); } }}>{t("取消启动", "Cancel launch")}</Button>}
      </div>}
      <div className="oc-model-choices">
        {data?.items.filter(m => m.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())).map(m => <div key={m.id} className="oc-model-choice">
          <div className="oc-model-choice-name"><strong title={m.name}>{m.name.replace(/^[A-Za-z0-9_.]+--/, "").replace(/--[0-9]{8,}$/, "")}</strong>
            {!m.can_load && m.reason && <small>{t(m.reason.split(" / ")[0], m.reason.split(" / ")[1] || m.reason)}</small>}
          </div>
          <div className="oc-model-choice-actions">
            <Button size="sm" variant="ghost" disabled={applying} onClick={() => void adjust(m)}>{t("调整", "Adjust")}</Button>
            <Button size="sm" variant="outline" disabled={!m.can_load || applying || (m.active && engine?.state === "ready")} onClick={() => void load(m)}>
              {m.active && engine?.state === "ready" ? t("使用中", "In use") : t("使用", "Use")}
            </Button>
          </div>
        </div>)}
        {data && !data.items.some(m => m.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())) && <p>{t("没有匹配的已下载模型", "No matching downloaded models")}</p>}
        {!data && !loadError && <LoaderCircle className="animate-spin" aria-label={t("读取模型", "Loading models")} />}
      </div>
      <Button variant="ghost" asChild><Link to="/models" onClick={() => setOpen(false)}>{t("管理模型", "Manage models")}</Link></Button>
    </Modal>
    {open && editor && <ProfileEditor key={editor.initial.id || editor.modelId} initial={editor.initial} modelId={editor.modelId} agent={agent}
      savedProfiles={editor.profiles} onChooseProfile={initial => setEditor({ ...editor, initial })}
      onLaunch={result => { setJobId(result.id); refreshData(); }} onClose={() => setEditor(undefined)} />}
  </>;
}
