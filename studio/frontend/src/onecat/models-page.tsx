import { Segments } from "./motion";
import { useBrowserState } from "./browser-state";
import { modelLabel, accelerationLabel } from "./model-label";
import { SupportedModels, modelDefaults } from "./supported-models";
// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useEffect, useState } from "react";
import { Popover } from "radix-ui";
import { Link } from "@tanstack/react-router";
import {
  Download,
  FolderInput,
  Play,
  Plus,
  Settings2,
  Copy,
  Trash2,
  FileDown,
  Search,
  MoreHorizontal,
} from "lucide-react";
import { Button } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import { Textarea } from "@/onecat/ui";
import { Switch } from "@/onecat/ui";
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
    kv_cache_dtype: "auto",
    max_model_len: model.max_context || 32768,
    max_num_batched_tokens: 4096,
    max_num_seqs: 1,
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

export function ProfileEditor({
  initial,
  onClose,
  modelId,
  savedProfiles,
  onChooseProfile,
  onLaunch,
  agent = false,
}: {
  initial: Profile;
  onClose: () => void;
  modelId?: string;
  savedProfiles?: Profile[];
  onChooseProfile?: (profile: Profile) => void;
  onLaunch?: (job: import("./api").Job) => void;
  agent?: boolean;
}) {
  const t = useText();
  const draftKey = (modelId ? "onecat:model-settings:" : "onecat:profile-draft:") + (initial.id || initial.model_path || "new");
  const [p, setP] = useBrowserState<Profile>(draftKey + ":profile", initial),
    [extras, setExtras] = useBrowserState(draftKey + ":extras", JSON.stringify(initial.extra_args, null, 2)),
    [sampling, setSampling] = useBrowserState(draftKey + ":sampling", JSON.stringify(initial.default_sampling, null, 2)),
    [hardware, setHardware] = useBrowserState(draftKey + ":hardware", initial.hardware_profile ? JSON.stringify(initial.hardware_profile, null, 2) : ""),
    [spec, setSpec] = useBrowserState(draftKey + ":spec", initial.speculative_config ? JSON.stringify(initial.speculative_config, null, 2) : "");
  const [saving, setSaving] = useState(false);
  const [remember, setRemember] = useState(true);
  const { data: gpus } = useQuery<{ gpus: GPU[] }>("/api/gpu");
  const { data: activeEngine } = useQuery<Engine>("/api/inference/status");
  const { data: runtimes } = useQuery<{ items: Runtime[] }>("/api/runtimes");
  const { data: localModels } = useQuery<{ items: Model[] }>(
    "/api/models/list", 5000,
  );
  const [draftJobId, setDraftJobId] = useBrowserState<string | null>(draftKey + ":download", null);
  const { data: draftJobs } = useQuery<{ items: import("./api").Job[] }>(draftJobId ? "/api/jobs" : null, 1000);
  const draftJob = draftJobs?.items.find(job => job.id === draftJobId);
  const downloadingDraft = !!draftJobId && (!draftJobs || (!!draftJob && !["completed", "failed", "cancelled"].includes(draftJob.state)));
  const [caps, setCaps] = useState<{
    verified: boolean;
    tool_parser?: string;
    vision: boolean;
    max_images: number;
    accelerators: string[];
    mtp_token_options?: number[];
    draft_repo_id?: string;
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
  const bf16 =
    p.gpu_uuids.length > 0 &&
    p.gpu_uuids.every(
      (id) =>
        (gpus?.gpus.find((g) => g.uuid === id)?.compute_capability?.[0] || 0) >=
        8,
    );
  let specValue: Record<string, unknown> | null = null;
  let defaults: Record<string, unknown> = {};
  try {
    specValue = spec.trim() ? JSON.parse(spec) : null;
    defaults = JSON.parse(sampling);
  } catch {
    /* Raw JSON remains editable and is validated on save. */
  }
  function samplingField(key: string, value: unknown) {
    setSampling(JSON.stringify({ ...defaults, [key]: value }, null, 2));
  }
  const drafts =
    localModels?.items.filter((m) =>
      caps?.draft_repo_id
        ? m.repo_id === caps.draft_repo_id
        : m.name.toLowerCase().includes("dflash"),
    ) || [];
  useEffect(() => {
    if (caps?.draft_repo_id && specValue?.method === "dflash" && !specValue.model && drafts[0]?.repo_id === caps.draft_repo_id)
      setSpec(JSON.stringify({ ...specValue, model: drafts[0].path }, null, 2));
  }, [caps?.draft_repo_id, drafts[0]?.path, spec]);
  function set<K extends keyof Profile>(key: K, value: Profile[K]) {
    setP((prev) => ({ ...prev, [key]: value }));
  }
  const canSave = !saving && !!p.name && !!p.model_path && !!p.runtime_id && !!p.gpu_uuids.length
    && !(p.dtype === "bfloat16" && !bf16);
  const activeProfile = activeEngine?.profile_id === p.id ? activeEngine?.profile : undefined;
  const changed = activeProfile ? [
    [t("上下文", "Context"), `${activeProfile.max_model_len / 1024}K`, `${p.max_model_len / 1024}K`],
    [t("加速", "Acceleration"), accelerationLabel(activeProfile) || t("关闭", "Off"),
      specValue?.method === "mtp" ? `MTP · ${specValue.num_speculative_tokens || 4}` : specValue?.method === "dflash" ? "DFlash2" : t("关闭", "Off")],
    [t("工具调用", "Tools"), activeProfile.tool_calling ? t("开启", "On") : t("关闭", "Off"), p.tool_calling ? t("开启", "On") : t("关闭", "Off")],
    [t("图片理解", "Vision"), activeProfile.vision_enabled ? t("开启", "On") : t("关闭", "Off"), p.vision_enabled ? t("开启", "On") : t("关闭", "Off")],
  ].filter(([, before, after]) => before !== after) : [];
  async function saveProfile(launch: boolean) {
    setSaving(true);
    try {
      const profile = await mutation<Profile>("/api/profiles", {
        ...p, speculative_config: spec.trim() ? JSON.parse(spec) : null,
        extra_args: JSON.parse(extras), default_sampling: JSON.parse(sampling),
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
      <div className="oc-form-grid">
        <NumberField
          label={t("最大上下文 tokens", "Context limit (tokens)")}
          value={p.max_model_len}
          onChange={(v) => set("max_model_len", v)}
          min={512}
        />
      </div>
      <details className="oc-profile-advanced" open={p.gpu_uuids.length === 0 ? true : undefined}>
        <summary>{t("高级设置", "Advanced settings")}</summary>
      <div className="oc-form-grid">
        {!modelId && <Field label={t("方案名称", "Configuration name")}>
          <Input value={p.name} onChange={(e) => set("name", e.target.value)} />
        </Field>}
        <Field label={t("运行环境", "Runtime")}>
          <select
            value={p.runtime_id}
            onChange={(e) => set("runtime_id", e.target.value)}
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
              const model = localModels?.items.find(
                (m) => m.path === e.target.value,
              );
              setP((prev) => ({
                ...prev,
                model_path: e.target.value,
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
      <ErrorNotice error={capError} />
      {capError && <Button variant="outline" onClick={() => setCapRevision(value => value + 1)}>{t("重新检查模型能力", "Retry capability check")}</Button>}
      <div className="oc-panel">
        <h3>{t("生成与能力", "Generation and capabilities")}</h3>
        <div className="oc-form-grid">
          <Field
            label={t("最大生成长度", "Maximum generated tokens")}
            hint={t(
              "留空自动跟随模型，并由服务按剩余上下文约束。",
              "Empty follows the model; the engine constrains output to remaining context.",
            )}
          >
            <Input
              type="number"
              min={1}
              value={
                defaults.max_tokens == null ? "" : Number(defaults.max_tokens)
              }
              placeholder={t("自动", "Auto")}
              onChange={(e) =>
                samplingField(
                  "max_tokens",
                  e.target.value ? Number(e.target.value) : null,
                )
              }
            />
          </Field>
          <Field label={t("推理加速", "Acceleration")}>
            <select
              value={String(specValue?.method || "off")}
              onChange={(e) =>
                setSpec(
                  e.target.value === "off"
                    ? ""
                    : JSON.stringify(
                        e.target.value === "dflash"
                          ? {
                              method: "dflash",
                              model: drafts[0]?.path || "",
                              kv_cache_dtype: "auto",
                              draft_sample_method: "probabilistic",
                            }
                          : { method: "mtp", num_speculative_tokens: 4 },
                        null,
                        2,
                      ),
                )
              }
            >
              <option value="off">{t("关闭", "Off")}</option>
              <option
                value="mtp"
                disabled={!caps?.accelerators.includes("mtp")}
              >
                MTP
              </option>
              <option
                value="dflash"
                disabled={!caps?.accelerators.includes("dflash")}
              >
                DFlash2
              </option>
            </select>
          </Field>
          {specValue?.method === "dflash" && (
            <Field label={t("配套草稿模型", "Matched draft model")}>
              <select
                value={String(specValue.model || "")}
                onChange={(e) =>
                  setSpec(
                    JSON.stringify(
                      { ...specValue, model: e.target.value },
                      null,
                      2,
                    ),
                  )
                }
              >
                <option value="">
                  {t("先下载草稿模型", "Download the draft first")}
                </option>
                {specValue.model &&
                !drafts.some((d) => d.path === specValue?.model) ? (
                  <option value={String(specValue.model)}>
                    {String(specValue.model)}
                  </option>
                ) : null}
                {drafts.map((d) => (
                  <option key={d.id} value={d.path}>
                    {modelLabel(d.name)}
                  </option>
                ))}
              </select>
              {caps?.draft_repo_id && !drafts.length && (
                <Action
                  disabled={downloadingDraft}
                  run={async () => {
                    const job = await mutation<import("./api").Job>("/api/hub/download", {
                      repo_id: caps.draft_repo_id,
                      runtime_id: p.runtime_id,
                    });
                    setDraftJobId(job.id);
                  }}
                >
                  {downloadingDraft ? t("草稿模型下载中…", "Downloading draft…") : t(
                    "从 ModelScope 下载配套草稿",
                    "Download matching draft from ModelScope",
                  )}
                </Action>
              )}
              {draftJob && <p className="oc-muted">{jobLabel(draftJob.stage, t)}{draftJob.progress ? ` · ${Math.floor(draftJob.progress)}%` : ""}</p>}
              <ErrorNotice error={draftJob?.error} />
            </Field>
          )}
          {specValue?.method === "mtp" && (
            <Field
              label={t("MTP 预测 token 数", "MTP speculative tokens")}
              hint={t(
                "默认 4；实际加速取决于接受率和工作负载，数量越大不一定越快。",
                "Default: 4. Speedup depends on acceptance and workload; a larger count is not always faster.",
              )}
            >
              <Segments className="oc-segments" role="radiogroup" aria-label={t("MTP 预测 token 数", "MTP speculative tokens")}
                onKeyDown={event => {
                  if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
                  event.preventDefault();
                  const options = caps?.mtp_token_options || [1, 2, 3, 4];
                  const current = options.indexOf(Number(specValue.num_speculative_tokens || 4));
                  const next = (current + (event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1) + options.length) % options.length;
                  setSpec(JSON.stringify({ ...specValue, num_speculative_tokens: options[next] }, null, 2));
                  event.currentTarget.querySelectorAll<HTMLButtonElement>("button")[next]?.focus();
                }}>
                {(caps?.mtp_token_options || [1, 2, 3, 4]).map(count => (
                  <button key={count} type="button" role="radio"
                    aria-checked={Number(specValue.num_speculative_tokens || 4) === count}
                    tabIndex={Number(specValue.num_speculative_tokens || 4) === count ? 0 : -1}
                    onClick={() => setSpec(JSON.stringify({ ...specValue, num_speculative_tokens: count }, null, 2))}>
                    {count}
                  </button>
                ))}
              </Segments>
            </Field>
          )}
        </div>
        <div className="oc-toggle-row">
          <label title={!caps?.tool_parser ? caps?.tool_reason : undefined}>
            <Switch
              checked={!!p.tool_calling}
              disabled={!caps?.tool_parser && !p.tool_calling}
              onCheckedChange={(v) =>
                setP((prev) => ({
                  ...prev,
                  tool_calling: v,
                  tool_parser: caps?.tool_parser || null,
                }))
              }
            />
            {t("工具调用（API）", "Tool calling (API)")}
          </label>
          <label title={!caps?.vision ? caps?.vision_reason : undefined}>
            <Switch
              checked={!!p.vision_enabled}
              disabled={!caps?.vision && !p.vision_enabled}
              onCheckedChange={(v) =>
                setP((prev) => ({
                  ...prev,
                  vision_enabled: v,
                  max_images: Math.min(4, caps?.max_images || 4),
                }))
              }
            />
            {t("图片理解", "Image understanding")}
          </label>
        </div>
      </div>

        <div className="oc-form-grid">
        <Field label={t("API 模型名称", "API model name")}>
          <Input
            value={p.served_model_name}
            onChange={(e) => set("served_model_name", e.target.value)}
          />
        </Field>
        <NumberField
          label={t("每批 token 上限", "Batch token limit")}
          value={p.max_num_batched_tokens}
          onChange={(v) => set("max_num_batched_tokens", v)}
          min={256}
        />
        <NumberField
          label={t("最大并发序列", "Maximum concurrent sequences")}
          value={p.max_num_seqs}
          onChange={(v) => set("max_num_seqs", v)}
          min={1}
        />

        <Field label={t("计算精度", "Compute precision")}>
          <select
            value={p.dtype}
            onChange={(e) => set("dtype", e.target.value)}
          >
            <option value="half">FP16</option>
            <option value="bfloat16" disabled={!bf16}>
              BF16
              {!bf16
                ? t(" · 硬件不支持", " · unavailable on selected GPUs")
                : ""}
            </option>
            <option value="auto">Auto</option>
          </select>
        </Field>
        <Field label="KV Cache">
          <select
            value={p.kv_cache_dtype}
            onChange={(e) => set("kv_cache_dtype", e.target.value)}
          >
            <option value="auto">Auto</option>
            <option value="fp8_e5m2">FP8 E5M2</option>
            <option value="fp8_e4m3">FP8 E4M3</option>
          </select>
        </Field>
          <Field label={t("Attention 后端", "Attention backend")}>
            <Input
              value={p.attention_backend || ""}
              onChange={(e) => set("attention_backend", e.target.value || null)}
              placeholder="Auto / FLASH_ATTN_V100"
            />
          </Field>
          <Field
            label={t(
              "量化后端",
              "Quantization backend",
            )}
          >
            <Input
              value={p.quantization || ""}
              onChange={(e) => set("quantization", e.target.value || null)}
            />
          </Field>
        </div>
      <div className="oc-toggle-row">
        <label>
          <Switch
            checked={p.enable_prefix_caching}
            onCheckedChange={(v) => set("enable_prefix_caching", v)}
          />
          {t("前缀缓存", "Prefix cache")}
        </label>
        <label>
          <Switch
            checked={!p.enforce_eager}
            onCheckedChange={(v) => set("enforce_eager", !v)}
          />
          {t("CUDA Graph / 编译", "CUDA Graph / compilation")}
        </label>
      </div>
        <Field
          label={t(
            "推测解码配置（JSON）",
            "Speculative decoding (JSON)",
          )}
          hint={t(
            "由当前模型与运行版本决定支持范围；开启后需单独验证能效。",
            "Support depends on the model and runtime. Speculation requires separate efficiency validation.",
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
          label={t("默认对话参数（JSON）", "Default chat sampling (JSON)")}
        >
          <Textarea
            value={sampling}
            onChange={(e) => setSampling(e.target.value)}
            rows={4}
          />
        </Field>
        <Field
          label={t(
            "启动硬件设置（JSON）",
            "Launch hardware settings (JSON)",
          )}
          hint={t(
            "使用 power_limit_w、graphics_clock_mhz、reset_clocks；需先配置控制助手。",
            "Use power_limit_w, graphics_clock_mhz, reset_clocks. Requires the GPU helper.",
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
      </details>
    </Modal>
  );
}

export function ModelsPage() {
  const t = useText();
  const { data: models, error } = useQuery<{ items: Model[] }>(
    "/api/models/list",
    5000,
  );
  const { data: profiles } = useQuery<{ items: Profile[] }>("/api/profiles");
  const { data: runtimes } = useQuery<{ items: Runtime[] }>("/api/runtimes");
  const { data: engine } = useQuery<Engine>("/api/inference/status", 3000);
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
                        "已移除模型库条目，文件仍保留",
                        "Library entry removed; files retained",
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
                  <p className="oc-profile-change">{t("已保存的新配置尚未应用；重新启动后生效。", "Saved changes are pending; restart to apply.")}</p>}
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
                  <Button variant="outline" onClick={() => setEditor({ ...p })}>
                    <Settings2 />
                    {t("配置", "Configure")}
                  </Button>
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
