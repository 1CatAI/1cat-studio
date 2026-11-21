import { Segments, useInterfaceMotion } from "./motion";
import { useBrowserState } from "./browser-state";
import { modelLabel, accelerationLabel, adaptiveMtp } from "./model-label";
import { SupportedModels, modelDefaults } from "./supported-models";
// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from "react";
import { Popover } from "radix-ui";
import { Link } from "@tanstack/react-router";
import {
  Download,
  FolderInput,
  Check,
  ChevronRight,
  Play,
  Plus,
  Settings2,
  Copy,
  Trash2,
  FileDown,
  Search,
  MoreHorizontal,
  Power,
} from "lucide-react";
import { Button } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import { Textarea } from "@/onecat/ui";
import { DownloadProgressBar } from "./download-progress";
import { Checkbox } from "@/onecat/ui";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/onecat/ui";
import {
  useQuery,
  copyText,
  mutation,
  api,
  refreshData,
  type Profile,
  type Model,
  type Runtime,
  type GPU,
  type Engine,
  type Settings,
} from "./api";
import {
  Page,
  Field,
  NumberField,
  Action,
  Empty,
  ErrorNotice,
  Modal,
  bytes,
  useText,
  jobLabel,
  saveFile,
} from "./common";

export function newProfile(model: Model, runtimeId: string): Profile {
  return {
    name: modelLabel(model.name),
    catalog_id: model.catalog_id || null,
    tool_calling: false,
    tool_parser: null,
    vision_enabled: false,
    max_images: 4,
    vision_processor_kwargs: {},
    model_path: model.path,
    runtime_id: runtimeId,
    served_model_name: model.name.replace(/[^a-zA-Z0-9_.-]/g, "-"),
    gpu_uuids: [],
    tensor_parallel_size: 1,
    dtype: "half",
    quantization: null,
    kv_cache_dtype: "",
    max_model_len: model.max_context || 32768,
    max_num_batched_tokens: 4096,
    max_num_seqs: 4,
    gpu_memory_utilization: 0.8,
    attention_backend: null,
    enforce_eager: false,
    enable_prefix_caching: true,
    speculative_config: null,
    extra_args: [],
    default_sampling: {
      temperature: 0.7,
      top_p: 0.9,
      max_tokens: null,
      thinking: false,
    },
    hardware_profile: null,
    source: "custom",
  };
}

function AdvancedSettings({ needsGpu, children }: { needsGpu: boolean; children: ReactNode }) {
  const t = useText();
  const id = useId();
  const [expanded, setExpanded] = useState(needsGpu);
  useEffect(() => { if (needsGpu) setExpanded(true); }, [needsGpu]);

  const { enabled } = useInterfaceMotion();
  const reveal = useRef<HTMLDivElement>(null);
  const resize = useRef<Animation | null>(null);
  useEffect(() => () => { resize.current?.cancel(); }, [enabled]);

  function toggle() {
    const content = reveal.current;
    const panel = content?.closest<HTMLElement>(".oc-dialog-fixed");
    if (enabled && content && panel?.animate) {
      // Animate the visible dialog distance, even when the fields are several
      // screens tall and the scroller reaches its max-height early.
      const from = panel.offsetHeight;
      resize.current?.cancel();
      content.style.height = expanded ? "0px" : `${content.firstElementChild?.scrollHeight || 0}px`;
      const to = panel.offsetHeight;
      content.style.removeProperty("height");
      if (from !== to) {
        resize.current = panel.animate([{ height: `${from}px` }, { height: `${to}px` }], {
          duration: 320, easing: "cubic-bezier(.35,0,.2,1)",
        });
      }
    }
    setExpanded(!expanded);
  }

  return <div className="oc-profile-advanced" data-expanded={expanded}>
    <button type="button" className="oc-advanced-toggle" aria-expanded={expanded}
      aria-controls={id} onClick={toggle}>
      <ChevronRight size={15} aria-hidden="true" />{t("高级设置", "Advanced settings")}
    </button>
    <div ref={reveal} id={id} className="oc-advanced-reveal" inert={!expanded}>
      <div className="oc-advanced-fields">{children}</div>
    </div>
  </div>;
}

export function ProfileEditor({
  initial,
  onClose,
  modelId,
  savedProfiles,
  returnFocusRef,
  transitionRef,
  onChooseProfile,
  onLaunch,
  agent = false,
}: {
  initial: Profile;
  onClose: () => void;
  modelId?: string;
  savedProfiles?: Profile[];
  returnFocusRef?: RefObject<HTMLElement | null>;
  transitionRef?: (element: HTMLDivElement | null) => void;
  onChooseProfile?: (profile: Profile) => void;
  onLaunch?: (job: import("./api").Job) => void;
  agent?: boolean;
}) {
  const t = useText();
  const draftKey = (modelId ? "onecat:model-settings:" : "onecat:profile-draft:") + (initial.id || initial.model_path || "new");
  const [p, setP] = useBrowserState<Profile>(draftKey + ":profile", initial),
    [extras, setExtras] = useBrowserState(draftKey + ":extras", JSON.stringify(initial.extra_args, null, 2)),
    [hardware, setHardware] = useBrowserState(draftKey + ":hardware", initial.hardware_profile ? JSON.stringify(initial.hardware_profile, null, 2) : ""),
    [specOverride, setSpec] = useBrowserState<string | null>(draftKey + ":spec", initial.speculative_config ? JSON.stringify(initial.speculative_config, null, 2) : initial.id ? "" : null);
  const [saving, setSaving] = useState(false);
  const [remember, setRemember] = useState(true);
  const { data: gpus } = useQuery<{ gpus: GPU[] }>("/api/gpu");
  const { data: activeEngine } = useQuery<Engine>("/api/inference/status");
  const { data: runtimes } = useQuery<{ items: Runtime[] }>("/api/runtimes");
  const { data: localModels } = useQuery<{ items: Model[] }>(
    "/api/models/list", 5000,
  );
  const [caps, setCaps] = useState<{
    verified: boolean;
    tool_parser?: string;
    vision: boolean;
    max_images: number;
    accelerators: string[];
    mtp_token_options?: number[];
    default_speculative_config?: Record<string, unknown>;
    default_kv_cache_dtype: string;
    int8_kv_available: boolean;
    draft_repo_id?: string;
    draft_download_revision?: string;
    draft_local_path?: string | null;
    draft_speculative_config?: Record<string, unknown>;
    reason?: string;
    tool_reason?: string;
    vision_reason?: string;
    recommended: Partial<Profile>;
  }>();
  const [capError, setCapError] = useState("");
  const [capRevision, setCapRevision] = useState(0);
  useEffect(() => {
    const refresh = () => setCapRevision(value => value + 1);
    window.addEventListener("online", refresh);
    return () => window.removeEventListener("online", refresh);
  }, []);
  useEffect(() => {
    let active = true;
    setCaps(undefined);
    mutation<typeof caps>("/api/profiles/capabilities", {
      model_path: p.model_path,
      runtime_id: p.runtime_id,
    })
      .then((value) => {
        if (active) {
          setCaps(value);
          setCapError("");
        }
      })
      .catch((error) => {
        if (active) setCapError(error.message);
      });
    return () => {
      active = false;
    };
  }, [p.model_path, p.runtime_id, capRevision]);
  const defaultMtp = caps?.default_speculative_config;
  const spec = specOverride ?? (defaultMtp ? JSON.stringify(defaultMtp, null, 2) : "");
  let specValue: Record<string, unknown> | null = null;
  try {
    specValue = spec.trim() ? JSON.parse(spec) : null;
  } catch {
    /* Raw JSON remains editable and is validated on save. */
  }
  const offersEntMtp = adaptiveMtp(defaultMtp);
  const dynamic = adaptiveMtp(specValue);
  const depthOptions = (caps?.mtp_token_options || [1, 2, 3, 4]).filter(count => !dynamic || count >= 3);
  function chooseDepth(count: number) {
    const next = { ...specValue, num_speculative_tokens: count };
    // A legacy MTP4 profile may omit the policy. Increasing its fixed depth
    // to five must not silently activate the engine's adaptive default.
    const policy = specValue?.sampling_policy as Record<string, unknown> | undefined;
    setSpec(JSON.stringify(offersEntMtp && !dynamic
      ? { ...next, sampling_policy: { ...policy, adaptive_mtp: "off" } } : next, null, 2));
  }
  function chooseAcceleration(method: string) {
    if (method === "off") { setSpec(""); return; }
    if (method === "dflash") {
      retryDraft();
      setSpec(JSON.stringify({ method, kv_cache_dtype: "auto", draft_sample_method: "probabilistic",
        ...caps?.draft_speculative_config, model: caps?.draft_local_path || "" }, null, 2));
      return;
    }
    const mtp = defaultMtp || { method: "mtp", num_speculative_tokens: depthOptions.includes(4) ? 4 : depthOptions[0] || 4 };
    // "auto" enables adaptive depth for MTP5 on V100, so fixed mode must opt out explicitly.
    const chosen = offersEntMtp && method === "mtp"
      ? { ...mtp, sampling_policy: { adaptive_mtp: "off" } } : mtp;
    setSpec(JSON.stringify(chosen, null, 2));
  }
  const draftRepo = caps?.draft_repo_id;
  const revision = caps?.draft_download_revision;
  const draftPath = caps && "draft_local_path" in caps ? caps.draft_local_path : specValue?.model;
  const wantsDraft = specValue?.method === "dflash";
  const needsDraft = wantsDraft && !!caps && !draftPath;
  const [draftId, setDraftId] = useBrowserState<string | null>(
    "onecat:dflash-job:" + p.runtime_id + ":" + p.model_path, null);
  const [draftError, setDraftError] = useState("");
  const { data: jobs, error: pollError } = useQuery<{ items: import("./api").Job[] }>(
    needsDraft && draftId ? "/api/jobs" : null, 1000);
  const job = jobs?.items.find(item => item.id === draftId);
  const checked = useRef("");
  useEffect(() => {
    if (!needsDraft || !draftRepo || !revision || draftId || draftError) return;
    let active = true;
    mutation<import("./api").Job>("/api/hub/download", {
      repo_id: draftRepo, revision, runtime_id: p.runtime_id,
    }).then(result => { if (active) setDraftId(result.id); })
      .catch(error => { if (active) setDraftError(String(error.message || error)); });
    return () => { active = false; };
  }, [needsDraft, draftRepo, revision, draftId, draftError, p.runtime_id, setDraftId]);
  useEffect(() => {
    if (job?.state === "completed" && checked.current !== job.id) {
      checked.current = job.id;
      setCapRevision(value => value + 1);
    }
  }, [job?.id, job?.state]);
  const draftFailed = !!draftError || job?.state === "failed" || job?.state === "cancelled"
    || (!!jobs && !!draftId && !job) || (job?.state === "completed" && needsDraft);
  function retryDraft() {
    checked.current = "";
    setDraftError("");
    setDraftId(null);
  }
  function set<K extends keyof Profile>(key: K, value: Profile[K]) {
    setP((prev) => ({ ...prev, [key]: value }));
  }
  const canSave = !saving && !!caps && !needsDraft && !!p.name && !!p.model_path && !!p.runtime_id && !!p.gpu_uuids.length;
  const activeProfile = activeEngine?.profile_id === p.id ? activeEngine?.profile : undefined;
  const changed = activeProfile ? [
    [t("上下文", "Context"), `${activeProfile.max_model_len / 1024}K`, `${p.max_model_len / 1024}K`],
    [t("加速", "Acceleration"), accelerationLabel(activeProfile) || t("关闭", "Off"),
      accelerationLabel({ speculative_config: specValue }) || t("关闭", "Off")],
  ].filter(([, before, after]) => before !== after) : [];
  async function saveProfile(launch: boolean) {
    setSaving(true);
    try {
      const profile = await mutation<Profile>("/api/profiles", {
        ...p, dtype: undefined, kv_cache_dtype: p.kv_cache_dtype || undefined, speculative_config: specOverride === null ? undefined : wantsDraft ? { ...specValue, model: draftPath } : spec.trim() ? JSON.parse(spec) : null,
        extra_args: JSON.parse(extras), default_sampling: { ...p.default_sampling, max_tokens: null },
        max_num_batched_tokens: undefined, attention_backend: undefined, quantization: undefined,
        tool_calling: undefined, tool_parser: undefined, vision_enabled: undefined,
        enable_prefix_caching: undefined, enforce_eager: undefined,
        hardware_profile: hardware.trim() ? JSON.parse(hardware) : null,
      });
      if (modelId && remember) await mutation(`/api/models/${modelId}/default-profile`, { profile_id: profile.id }, "PUT");
      if (launch) {
        const job = await mutation<import("./api").Job>(modelId ? "/api/inference/load-model" : "/api/inference/load",
          { profile_id: profile.id, ...(modelId ? { model_id: modelId, agent } : {}) });
        onLaunch?.(job);
      }
      for (const suffix of ["profile", "extras", "sampling", "hardware", "spec"])
        sessionStorage.removeItem(draftKey + ":" + suffix);
      onClose();
    } finally { setSaving(false); }
  }
  return (
    <Modal
      returnFocusRef={returnFocusRef}
      footer={<>
        {modelId && <label className="oc-default-profile"><Checkbox checked={remember} onCheckedChange={v => setRemember(v === true)} />{t("设为默认", "Set as default")}</label>}
        <div className="oc-actions">
          <Button variant="outline" onClick={onClose}>{t("取消", "Cancel")}</Button>
          <Action variant="outline" disabled={!canSave} run={() => saveProfile(false)}
            success={t("已保存", "Saved")}>{t("保存", "Save")}</Action>
          <Action disabled={!canSave} run={() => saveProfile(true)}>
            <Play />{activeProfile ? t("重新加载", "Reload") : t("加载模型", "Load model")}
          </Action>
        </div>
      </>}
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={modelId ? modelLabel(initial) : t("模型运行设置", "Model settings")}
    >
      {!!changed.length && <p className="oc-profile-change" role="status">{t("重启后应用", "Apply on restart")}: {changed.map(([label, before, after]) => `${label} ${before} → ${after}`).join(" · ")}</p>}
      <div ref={transitionRef} className="oc-form-grid">
        <NumberField
          label={t("最大上下文 tokens", "Context limit (tokens)")}
          value={p.max_model_len}
          onChange={(v) => set("max_model_len", v)}
          min={512}
        />
        <div className="oc-field oc-concurrency-presets">
          <span className="oc-field-label">{t("服务并发", "Concurrent requests")}</span>
          <div className="oc-actions" role="group" aria-label={t("服务并发", "Concurrent requests")}>
            {Array.from(new Set([1, 2, 4, 8, p.max_num_seqs])).sort((a, b) => a - b).map((value) => (
              <Button key={value} type="button" size="sm" aria-pressed={p.max_num_seqs === value} variant={p.max_num_seqs === value ? "secondary" : "outline"} onClick={() => set("max_num_seqs", value)}>
                <Check size={14} aria-hidden="true" />{value}
              </Button>
            ))}
          </div>
          <small className="oc-muted">{t("模型同时处理的请求数，重新加载后生效。", "Number of requests served at once. Takes effect on reload.")}</small>
        </div>

          <Field label={t("推理加速", "Acceleration")}>
            <select
              value={dynamic ? "entmtp" : String(specValue?.method || "off")}
              onChange={e => chooseAcceleration(e.target.value)}
            >
              <option value="off">{t("关闭", "Off")}</option>
              <option
                value="mtp"
                disabled={!caps?.accelerators.includes("mtp")}
              >
                {offersEntMtp ? `MTP${!dynamic && specValue?.method === "mtp" ? specValue.num_speculative_tokens : 5} · ${t("固定长度", "Fixed depth")}` : "MTP"}
              </option>
              {(offersEntMtp || dynamic) && <option value="entmtp" disabled={!offersEntMtp}>EntMTP · {t("动态长度", "Adaptive depth")}</option>}
              <option
                value="dflash"
                disabled={!caps?.accelerators.includes("dflash")}
              >
                DFlash2
              </option>
            </select>
          </Field>
          {needsDraft && <div role="status">
            <span>{draftRepo || "DFlash2"} · {job ? jobLabel(job.stage, t) : t("准备配套草稿", "Preparing draft")}</span>
            {job && <DownloadProgressBar job={job} />}
            <ErrorNotice error={draftError || pollError || job?.error || (!revision
              ? t("此运行包没有匹配的草稿下载版本。", "No matching draft release is available.")
              : job?.state === "completed" ? t("正在核对已下载的草稿版本。", "Checking the downloaded draft revision.") : "")} />
            {draftFailed && revision && <Button type="button" variant="outline" onClick={retryDraft}>{t("重新下载草稿", "Retry draft download")}</Button>}
          </div>}
          {specValue?.method === "mtp" && (
            <Field
              label={t("MTP 预测 token 数", "MTP speculative tokens")}
              hint={t(
                dynamic ? "动态预测已开启，此处设置每轮最多预测的 token 数。" : "每轮预测的 token 数。",
                dynamic ? "Adaptive depth is on. This is the maximum tokens drafted per round." : "Number of tokens drafted per round.",
              )}
            >
              <Segments className="oc-segments" role="radiogroup" aria-label={t("MTP 预测 token 数", "MTP speculative tokens")}
                onKeyDown={event => {
                  if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
                  event.preventDefault();
                  const options = depthOptions;
                  const current = options.indexOf(Number(specValue.num_speculative_tokens || 4));
                  const next = (current + (event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1) + options.length) % options.length;
                  chooseDepth(options[next]);
                  event.currentTarget.querySelectorAll<HTMLButtonElement>("button")[next]?.focus();
                }}>
                {depthOptions.map(count => (
                  <button key={count} type="button" role="radio"
                    aria-checked={Number(specValue.num_speculative_tokens || 4) === count}
                    tabIndex={Number(specValue.num_speculative_tokens || 4) === count ? 0 : -1}
                    onClick={() => chooseDepth(count)}>
                    {count}
                  </button>
                ))}
              </Segments>
            </Field>
          )}
      </div>
      <ErrorNotice error={capError} />
      {capError && <Button variant="outline" onClick={() => setCapRevision(value => value + 1)}>{t("重新检查模型能力", "Retry capability check")}</Button>}
      <AdvancedSettings needsGpu={p.gpu_uuids.length === 0}>
      <div className="oc-form-grid">
        {!modelId && <Field label={t("方案名称", "Configuration name")}>
          <Input value={p.name} onChange={(e) => set("name", e.target.value)} />
        </Field>}
        <Field label={t("运行环境", "Runtime")}>
          <select
            value={p.runtime_id}
            onChange={(e) => {
              const runtime_id = e.target.value;
              setP(prev => ({ ...prev, runtime_id, kv_cache_dtype: "" }));
              setSpec(null);
              setCaps(undefined);
            }}
          >
            <option value="">
              {t("选择匹配的运行环境", "Choose a compatible runtime")}
            </option>
            {runtimes?.items.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </Field>
        {!modelId && <Field label={t("模型目录", "Model directory")}>
          <select
            value={p.model_path}
            onChange={(e) => {
              setSpec(null);
              setCaps(undefined);
              const model = localModels?.items.find(
                (m) => m.path === e.target.value,
              );
              setP((prev) => ({
                ...prev,
                model_path: model?.path || "",
                kv_cache_dtype: "",
                catalog_id: model?.catalog_id || null,
              }));
            }}
          >
            <option value="">{t("选择本地模型", "Select local model")}</option>
            {p.model_path &&
              !localModels?.items.some((m) => m.path === p.model_path) && (
                <option value={p.model_path}>{p.model_path}</option>
              )}
            {localModels?.items
              .filter((m) => m.role !== "draft")
              .map((m) => (
                <option key={m.id} value={m.path}>
                  {modelLabel(m.name)}
                </option>
              ))}
          </select>
        </Field>}
      </div>
      <div className="oc-profile-gpus">
      <Field
        label={t("运行显卡", "GPUs")}
      >
        <div className="oc-gpu-choices">
          {gpus?.gpus.map((d) => (
            <label key={d.uuid} className="oc-gpu-choice">
              <Checkbox
                checked={p.gpu_uuids.includes(d.uuid)}
                onCheckedChange={(checked) => {
                  const selected = checked
                    ? [...p.gpu_uuids, d.uuid]
                    : p.gpu_uuids.filter((id) => id !== d.uuid);
                  setP((prev) => ({
                    ...prev,
                    gpu_uuids: selected,
                    tensor_parallel_size: Math.max(1, selected.length),
                  }));
                }}
              />
              <span>
                GPU {d.index} · {d.name}
                <small>
                  {d.memory_used_mib == null
                    ? "—"
                    : Math.round(d.memory_used_mib / 1024)}{" "}
                  /{" "}
                  {d.memory_total_mib == null
                    ? "—"
                    : Math.round(d.memory_total_mib / 1024)}{" "}
                  GB
                </small>
              </span>
            </label>
          ))}
        </div>
      </Field>
        <Field label={t("显存使用上限", "GPU memory limit")}>
          <div className="oc-memory-limit">
            <input type="range" min={10} max={98} step={1}
              value={Math.round(p.gpu_memory_utilization * 100)}
              aria-valuetext={`${Math.round(p.gpu_memory_utilization * 100)}%`}
              onChange={e => set("gpu_memory_utilization", Number(e.target.value) / 100)} />
            <output>{Math.round(p.gpu_memory_utilization * 100)}%</output>
          </div>
        </Field>
      </div>
        <div className="oc-form-grid">
        <Field label={t("API 模型名称", "API model name")}>
          <Input
            value={p.served_model_name}
            onChange={(e) => set("served_model_name", e.target.value)}
          />
        </Field>
        <Field label="KV Cache">
          <select
            value={p.kv_cache_dtype || caps?.default_kv_cache_dtype || "auto"}
            onChange={(e) => set("kv_cache_dtype", e.target.value)}
          >
            <option value="auto">Auto</option>
            {(caps?.int8_kv_available || p.kv_cache_dtype === "int8_per_token_head") && <option value="int8_per_token_head">INT8 · Per Token/Head</option>}
            <option value="float16">FP16</option>
            <option value="fp8_e5m2">FP8 E5M2</option>
            <option value="fp8_e4m3">FP8 E4M3</option>
          </select>
        </Field>
        </div>
        <Field
          label={t(
            "推测解码配置（JSON）",
            "Speculative decoding (JSON)",
          )}
          hint={t(
            "可用选项取决于模型和运行环境。开启后可测试能效。",
            "Available options depend on the model and runtime. Measure efficiency with speculation enabled.",
          )}
        >
          <Textarea
            value={spec}
            onChange={(e) => setSpec(e.target.value)}
            rows={5}
            className="oc-code"
          />
        </Field>
        <Field
          label={t(
            "附加启动参数（JSON 数组）",
            "Extra launch arguments (JSON array)",
          )}
        >
          <Textarea
            value={extras}
            onChange={(e) => setExtras(e.target.value)}
            rows={4}
            className="oc-code"
          />
        </Field>
        <Field
          label={t(
            "启动硬件设置（JSON）",
            "Launch hardware settings (JSON)",
          )}
          hint={t(
            "先配置 GPU 控制助手，再设置 power_limit_w、graphics_clock_mhz 或 reset_clocks。",
            "Configure the GPU helper before setting power_limit_w, graphics_clock_mhz or reset_clocks.",
          )}
        >
          <Textarea
            value={hardware}
            onChange={(e) => setHardware(e.target.value)}
            rows={3}
          />
        </Field>
        {!!savedProfiles?.length && onChooseProfile && <Field label={t("已保存方案", "Saved configurations")}>
          <select value={initial.id || ""} onChange={event => {
            const selected = savedProfiles.find(profile => profile.id === event.target.value);
            if (selected) onChooseProfile(selected);
          }}>{savedProfiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select>
        </Field>}
      </AdvancedSettings>
    </Modal>
  );
}

export function ModelsPage() {
  const t = useText();
  const { data: models, error } = useQuery<{ items: Model[] }>(
    "/api/models/list",
    5000,
  );
  const { data: profiles } = useQuery<{ items: Profile[]; recommended_updates?: Record<string, Partial<Profile>> }>("/api/profiles");
  const { data: runtimes } = useQuery<{ items: Runtime[] }>("/api/runtimes");
  const { data: engine } = useQuery<Engine>("/api/inference/status", 3000);
  const { data: settings } = useQuery<Settings>("/api/settings");
  const [importOpen, setImportOpen] = useState(false),
    [path, setPath] = useState(""),
    [editor, setEditor] = useState<Profile | null>(null),
    [command, setCommand] = useState("");
  const [tab, setTab] = useBrowserState("onecat:models-tab", "supported");
  return (
    <Page
      title={t("模型库", "Model library")}
      action={
        <Button variant="outline" onClick={() => setImportOpen(true)}>
          <FolderInput />
          {t("导入本地模型", "Import local model")}
        </Button>
      }
    >
      <ErrorNotice error={error} />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="supported">
            {t("发现模型", "Discover")}
          </TabsTrigger>
          <TabsTrigger value="local">
            {t("已下载", "Downloaded")}
          </TabsTrigger>
          <TabsTrigger value="profiles">
            {t("启动预设", "Launch profiles")}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="supported">
          <SupportedModels onConfigure={setEditor} />
        </TabsContent>
        <TabsContent value="local">
          <SupportedModels onConfigure={setEditor} downloadedOnly />
          {!models?.items.length ? (
            <Empty
              title={t("添加第一个模型", "Add your first model")}
              description={t(
                "导入服务器上的模型目录，或者从 ModelScope 下载。",
                "Import a model directory on the server or download from ModelScope.",
              )}
            />
          ) : (
            <div className="oc-model-grid">
              {models.items.filter(m => !m.catalog_id || m.role === "draft").map((m) => (
                <article className="oc-model-card" key={m.id}>
                  <span className="oc-kicker">
                    {m.model_type || "MODEL"} ·{" "}
                    {String(m.quantization).toUpperCase()}
                  </span>
                  <h2>{modelLabel(m.name)}</h2>
                  <p className="oc-muted oc-path">{m.path}</p>
                  <div className="oc-card-meta">
                    <span>{bytes(m.bytes)}</span>
                    <span>
                      {m.role === "draft"
                        ? t("配套草稿", "Companion draft")
                        : m.verified
                          ? t("已验证", "Verified")
                          : m.local_verified
                            ? t("本机已验证", "Validated locally")
                            : t(
                                "本地导入 · 未验证",
                                "Local import · unverified",
                              )}
                    </span>
                  </div>
                  <div className="oc-actions">
                    <Action
                      disabled={
                        (!m.catalog_id && !runtimes?.items.length) ||
                        m.role === "draft"
                      }
                      run={async () => {
                        if (m.catalog_id) {
                          const defaults = await modelDefaults(
                            m.catalog_id,
                            m.id,
                          );
                          if (!defaults.profile)
                            throw Error(defaults.reasons.join(" · "));
                          setEditor(defaults.profile);
                        } else setEditor(newProfile(m, runtimes!.items[0].id));
                      }}
                    >
                      <Plus />
                      {t("创建启动预设", "Create profile")}
                    </Action>
                    <Action
                      variant="ghost"
                      run={() =>
                        mutation("/api/models/" + m.id, undefined, "DELETE")
                      }
                      success={t(
                        "已从模型库移除，可重新导入本地文件",
                        "Removed from the library. You can import the local files again.",
                      )}
                    >
                      <Trash2 />
                      {t("移除条目", "Remove entry")}
                    </Action>
                  </div>
                </article>
              ))}
            </div>
          )}
          {!runtimes?.items.length && (
            <p className="oc-muted oc-spaced">
              <Link to="/setup">
                {t(
                  "先准备运行环境，再创建启动预设。",
                  "Prepare a runtime before creating a launch profile.",
                )}
              </Link>
            </p>
          )}
        </TabsContent>
        <TabsContent value="profiles">
          <Button
            className="oc-spaced"
            onClick={async () => {
              const model = models?.items.find(m => m.role !== "draft");
              if (model?.catalog_id) {
                try {
                  const defaults = await modelDefaults(model.catalog_id, model.id);
                  if (defaults.profile) { setEditor({ ...defaults.profile, id: undefined, name: modelLabel(model.name) }); return; }
                } catch { /* Local/custom profiles remain available if a catalog recipe is unavailable. */ }
              }
              setEditor(newProfile(model || { id: "", name: t("新建预设", "New profile"), path: "", source: "local", bytes: 0, quantization: "" }, runtimes?.items[0]?.id || ""));
            }}
          >
            <Plus />
            {t("新建预设", "New profile")}
          </Button>
          {!profiles?.items.length ? (
            <Empty
              title={t("还没有启动预设", "No launch profiles yet")}
              description={t(
                "从本地模型创建预设，或者在安装引导中接管已有服务。",
                "Create a profile from a local model, or adopt an existing service in Setup.",
              )}
            />
          ) : (
            profiles.items.map((p) => (
              <article className="oc-panel" key={p.id}>
                <div className="oc-row">
                  <div>
                    <h2>{p.name || modelLabel(p)}</h2>
                    <p className="oc-muted">
                      {p.served_model_name} · TP{p.tensor_parallel_size} ·{" "}
                      {p.max_model_len.toLocaleString()} context ·{" "}
                      {accelerationLabel(p) || t("基础推理", "Target only")}
                    </p>
                    {settings?.autostart_profile === p.id && (
                      <p className="oc-status-good">{t("已选为开机加载预设", "Selected for boot loading")}</p>
                    )}
                  </div>
                  <span
                    className={
                      engine?.profile_id === p.id
                        ? "oc-status-good"
                        : "oc-muted"
                    }
                  >
                    {engine?.profile_id === p.id
                      ? jobLabel(engine?.state || "", t)
                      : t("未运行", "Stopped")}
                  </span>
                </div>
                {engine?.profile_id === p.id && engine?.profile && ["max_model_len", "dtype", "kv_cache_dtype", "tensor_parallel_size", "runtime_id", "speculative_config", "tool_calling", "vision_enabled", "extra_args"].some(key => JSON.stringify(p[key as keyof Profile]) !== JSON.stringify(engine?.profile?.[key as keyof Profile])) &&
                  <p className="oc-profile-change">{t("重启模型后应用已保存的设置。", "Restart the model to apply the saved settings.")}</p>}
                <div className="oc-actions oc-spaced">
                  <Action
                    disabled={!!engine?.maintenance}
                    run={() =>
                      mutation("/api/inference/load", { profile_id: p.id })
                    }
                    success={t("已提交启动任务", "Launch task created")}
                  >
                    <Play />
                    {engine?.profile_id === p.id
                      ? t("重新应用预设", "Apply profile")
                      : t("启动模型", "Start model")}
                  </Action>
                  {p.id && profiles.recommended_updates?.[p.id] && (
                    <Action variant="outline" run={() => mutation(`/api/profiles/${p.id}/recommended`, {})}>
                      {t("一键更新到推荐配置", "Update to recommended settings")}
                    </Action>
                  )}
                  <Button variant="outline" onClick={() => setEditor({ ...p })}>
                    <Settings2 />
                    {t("配置", "Configure")}
                  </Button>
                  <Action
                    variant="outline"
                    disabled={!settings}
                    run={() => mutation("/api/settings", {
                      autostart_profile: settings?.autostart_profile === p.id ? null : p.id,
                    }, "PUT")}
                    success={t("开机加载预设已更新", "Boot loading profile updated")}
                  >
                    <Power />
                    {settings?.autostart_profile === p.id
                      ? t("取消开机加载", "Disable boot loading")
                      : t("开机自动加载", "Load on boot")}
                  </Action>
                  <Popover.Root><Popover.Trigger asChild><Button variant="ghost" aria-label={t("更多预设操作", "More profile actions")}><MoreHorizontal />{t("更多", "More")}</Button></Popover.Trigger><Popover.Portal><Popover.Content className="oc-profile-more" sideOffset={8} align="start">
                  <Button
                    variant="ghost"
                    onClick={() =>
                      setEditor({ ...p, id: undefined, name: p.name + " copy" })
                    }
                  >
                    <Copy />
                    {t("复制", "Duplicate")}
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() => saveFile(p.name + ".json", p)}
                  >
                    <FileDown />
                    {t("导出", "Export")}
                  </Button>
                  <Action
                    variant="ghost"
                    run={async () =>
                      setCommand(
                        (
                          await api<{ command: string }>(
                            "/api/profiles/" + p.id + "/command",
                          )
                        ).command,
                      )
                    }
                  >
                    {t("查看命令", "Show command")}
                  </Action>
                  <Action
                    variant="ghost"
                    disabled={
                      engine?.profile_id === p.id &&
                      !["stopped", "failed"].includes(
                        engine?.state || "stopped",
                      )
                    }
                    run={() =>
                      mutation("/api/profiles/" + p.id, undefined, "DELETE")
                    }
                  >
                    <Trash2 />
                    {t("删除预设", "Delete profile")}
                  </Action>
                  </Popover.Content></Popover.Portal></Popover.Root>
                </div>
              </article>
            ))
          )}
          <Field label={t("导入预设 JSON", "Import profile JSON")}>
            <Input
              type="file"
              accept=".json"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (file) {
                  try {
                    setEditor({
                      ...JSON.parse(await file.text()),
                      id: undefined,
                    });
                  } catch {
                    setCommand(
                      t("文件不是有效的 JSON。", "Invalid JSON file."),
                    );
                  }
                }
              }}
            />
          </Field>
        </TabsContent>

      </Tabs>
      <Modal
        open={importOpen}
        onOpenChange={setImportOpen}
        title={t("导入本地模型", "Import local model")}
      >
        <Field label={t("服务器上的模型目录", "Model directory on the server")}>
          <Input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/path/to/model"
          />
        </Field>
        <Action
          disabled={!path}
          run={async () => {
            await mutation("/api/models/import", { path });
            setImportOpen(false);
            setPath("");
          }}
          success={t("模型已导入", "Model imported")}
        >
          <FolderInput />
          {t("导入", "Import")}
        </Action>
      </Modal>
      {editor && (
        <ProfileEditor key={editor.id || editor.model_path || "new"}
          initial={editor}
          onClose={() => {
            setEditor(null);
            refreshData();
          }}
        />
      )}
      <Modal
        open={!!command}
        onOpenChange={(open) => {
          if (!open) setCommand("");
        }}
        title={t("启动命令", "Launch command")}
      >
        <pre className="oc-log">{command}</pre>
        <Button variant="outline" onClick={() => copyText(command)}>
          <Copy />
          {t("复制", "Copy")}
        </Button>
      </Modal>
    </Page>
  );
}
