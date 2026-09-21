// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowUp,
  Download,
  Film,
  Image as ImageIcon,
  Plus,
  RefreshCw,
  Workflow,
  X,
  LoaderCircle,
  Clock3,
} from "lucide-react";
import { Button } from "@/onecat/ui";
import { Switch } from "@/onecat/ui";
import { api, mutation, refreshData, useQuery } from "../api";
import { Action, ErrorNotice, Modal, bytes, useText } from "../common";
import { useBrowserState } from "../browser-state";
import { MOTION, useInterfaceMotion, Segments } from "../motion";
import {
  id,
  makeNode,
  terminal,
  type Asset,
  type Project,
  type Run,
} from "../canvas/types";
import { RunStatus } from "./status";
import { elapsed, totalSeconds } from "./timing";
import { aspectRatio, sizeForRatio } from "./output-sizes";
import { CreativePrompt, OutputFields, type CreativeModel, type ModelVariant } from "./form";
import { CreationError } from "./error";
import "./style.css";

type Kind = "video" | "image";
type Ref = {
  asset_id: string;
  role: "first" | "last" | "reference";
  asset: Asset;
};
type Draft = {
  model: string;
  prompt: string;
  width: number;
  height: number;
  num_frames: number;
  seed: number;
  references: Ref[];
  fast: boolean;
};
type Model = CreativeModel;
type Prepared = {
  id: string;
  impacts: { kind: string; id: string; name: string }[];
  download_bytes: number;
};
type Pending = { prepared: Prepared; requestKey: string };
const initial = (kind: Kind): Draft => ({
  model: kind === "video" ? "h3-fasth3" : "z-image-turbo",
  prompt: "",
  width: kind === "video" ? 1280 : 1024,
  height: kind === "video" ? 736 : 1024,
  num_frames: kind === "video" ? 120 : 107,
  seed: 42,
  references: [],
  fast: false,
});
const newSeed = () =>
  crypto.getRandomValues(new Uint32Array(1))[0] % 2147483647;
const intent = (draft: Draft) => ({
  model: draft.model,
  prompt: draft.prompt,
  width: draft.width,
  height: draft.height,
  num_frames: draft.num_frames,
  seed: draft.seed,
  fast: draft.fast === true,
  references: draft.references.map(({ asset_id, role }) => ({
    asset_id,
    role,
  })),
});
function restoreDraft(raw: unknown, kind: Kind): Draft {
  const fallback = initial(kind);
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return fallback;
  const value = raw as Partial<Draft>;
  const number = (v: unknown, defaultValue: number) =>
    typeof v === "number" && Number.isFinite(v) ? v : defaultValue;
  return {
    ...fallback,
    model: typeof value.model === "string" && value.model ? value.model : fallback.model,
    prompt: typeof value.prompt === "string" ? value.prompt : "",
    width: number(value.width, fallback.width),
    height: number(value.height, fallback.height),
    num_frames: number(value.num_frames, 107),
    seed: number(value.seed, 42),
    fast: kind === "video" && value.fast === true,
    references:
      kind === "video" && Array.isArray(value.references)
        ? value.references
            .filter(
              (r) =>
                r &&
                typeof r.asset_id === "string" &&
                ["first", "last", "reference"].includes(r.role) &&
                r.asset &&
                typeof r.asset.url === "string",
            )
            .slice(0, 12)
        : [],
  };
}

export default function CreativePage() {
  const t = useText(),
    navigate = useNavigate(),
    { enabled } = useInterfaceMotion();
  const [savedKind, setKind] = useBrowserState<Kind>(
    "onecat:creative-kind",
    "video",
    true,
  );
  const kind = savedKind === "image" ? "image" : "video";
  const [videoDraft, setVideoDraft] = useBrowserState<Draft>(
    "onecat:creative-draft:video",
    initial("video"),
    true,
  );
  const [imageDraft, setImageDraft] = useBrowserState<Draft>(
    "onecat:creative-draft:image",
    initial("image"),
    true,
  );
  const storedDraft = kind === "video" ? videoDraft : imageDraft;
  const setDraft = kind === "video" ? setVideoDraft : setImageDraft;
  const draft = restoreDraft(storedDraft, kind);
  const { data: catalog, error: catalogError } = useQuery<{ models: Model[] }>(
    "/api/creative/catalog",
    10000,
  );
  const {
    data: history,
    error: historyError,
    refresh,
  } = useQuery<{ items: Run[]; next_cursor: string | null }>(
    "/api/creative/generations",
    1500,
  );
  const [older, setOlder] = useState<Run[]>([]),
    [cursor, setCursor] = useState<string | null | undefined>();
  const [submitted, setSubmitted] = useState<Run[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [pending, setPending] = useState<Pending | null>(null),
    [uploading, setUploading] = useState(false);
  const [detail, setDetail] = useState<Run | null>(null);
  const [tracked, setTracked] = useState<Record<string, Run>>({});
  const [now, setNow] = useState(Date.now() / 1000);
  const lock = useRef(false),
    promptRef = useRef<HTMLTextAreaElement>(null),
    alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => {
      alive.current = false;
      clearInterval(timer);
    };
  }, []);
  const partition = draft.references.some((r) => r.role === "reference") ? "ref2va" : "fl2va";
  const models = (catalog?.models || []).filter((m) => m.kind === kind && (!m.partition || m.partition === partition));
  const model = models.find((m) => m.id === draft.model);
  const workflowVariant = model?.variants?.[partition];
  const selectedVariant = (v?: ModelVariant) => v?.resolutions?.[String(Math.min(draft.width, draft.height))] || v;
  const variant = draft.fast && model?.fast_variant ? model.fast_variant : selectedVariant(workflowVariant);
  const outputSizes = workflowVariant?.sizes || model?.sizes;
  const supportedSize = !!outputSizes?.some(([w, h]) => w === draft.width && h === draft.height);
  const downloadBytes = variant?.download_bytes ?? model?.download_bytes ?? 0;
  const records = [
    ...new Map(
      [
        ...older,
        ...submitted,
        ...Object.values(tracked),
        ...(history?.items || []),
      ].map((r) => [r.id, r]),
    ).values(),
  ].sort((a, b) => b.created_at - a.created_at);
  const firstPage = new Set(history?.items.map((r) => r.id));
  const activeOutsidePage = records
    .filter((r) => !terminal(r.state) && !firstPage.has(r.id))
    .map((r) => r.id)
    .sort()
    .join(",");
  useEffect(() => {
    if (!activeOutsidePage) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      const values = await Promise.allSettled(
        activeOutsidePage
          .split(",")
          .map((identity) =>
            api<Run>(`/api/creative/runs/${identity}`, {
              signal: controller.signal,
            }),
          ),
      );
      if (controller.signal.aborted) return;
      setTracked((previous) => {
        const next = { ...previous };
        for (const value of values)
          if (value.status === "fulfilled") next[value.value.id] = value.value;
        return next;
      });
      timer = setTimeout(poll, 1500);
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [activeOutsidePage]);
  const liveDetail =
    detail && (records.find((r) => r.id === detail.id) || detail);
  const nextCursor = cursor === undefined ? history?.next_cursor : cursor;
  const update = (value: Partial<Draft>) =>
    setDraft((previous) => ({ ...restoreDraft(previous, kind), ...value }));
  const canSubmit =
    !!model?.runtime_available &&
    !!model?.hardware_available &&
    !!draft.prompt.trim() &&
    supportedSize &&
    (kind === "image" || !!model?.frames.includes(draft.num_frames)) &&
    (!model?.text_only || draft.references.length === 0) &&
    (!draft.fast || !!model?.fast_available) &&
    !uploading;

  async function commit(value: Pending) {
    const run = await mutation<Run>("/api/creative/generations", {
      preparation_id: value.prepared.id,
      request_key: value.requestKey,
      confirm_switch: value.prepared.impacts.length > 0,
    });
    if (alive.current) {
      setSubmitted((old) => [run, ...old.filter((r) => r.id !== run.id)]);
      setPending(null);
      refresh();
      refreshData();
    }
  }
  async function generate(value: Draft = draft) {
    if (lock.current || !value.prompt.trim() || uploading) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const prepared = await mutation<Prepared>(
        "/api/creative/generations/prepare",
        intent(value),
      );
      const pendingValue = { prepared, requestKey: id() };
      if (!alive.current) return;
      if (prepared.impacts.length) setPending(pendingValue);
      else await commit(pendingValue);
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      lock.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function confirm() {
    if (!pending || lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      await commit(pending);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      lock.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function upload(files: FileList | null, role: Ref["role"]) {
    if (!files?.length || uploading) return;
    if (
      role === "reference" &&
      files.length +
        draft.references.filter((r) => r.role === "reference").length >
        12
    ) {
      setError(
        t(
          "一次最多使用 12 个参考素材。",
          "Use up to 12 reference assets per generation.",
        ),
      );
      return;
    }
    const capturedKind = kind;
    setUploading(true);
    setError("");
    try {
      const additions: Ref[] = [];
      for (const file of Array.from(files)) {
        const body = new FormData();
        body.append("file", file);
        const asset = await api<Asset>("/api/creative/assets", {
          method: "POST",
          body,
        });
        additions.push({ asset_id: asset.id, role, asset });
      }
      // The setter stays bound to the video draft even if a mode is switched during upload.
      if (capturedKind === "video")
        setDraft((previous) => ({
          ...previous,
          references:
            role === "reference"
              ? [
                  ...previous.references.filter((r) => r.role === "reference"),
                  ...additions,
                ]
              : [
                  ...previous.references.filter(
                    (r) => r.role !== role && r.role !== "reference",
                  ),
                  additions[0],
                ],
        }));
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current) setUploading(false);
    }
  }
  function reuse(run: Run, vary = false) {
    const targetKind: Kind = run.workflow.startsWith("image-")
      ? "image"
      : "video";
    const saved = run.intent as Draft | undefined;
    const ordered = [...(saved?.references || [])].sort(
      (a, b) =>
        ({ first: 0, last: 1, reference: 2 })[a.role] -
        { first: 0, last: 1, reference: 2 }[b.role],
    );
    const value: Draft = saved
      ? {
          ...saved,
          references: (run.reference_assets || []).map((asset, i) => ({
            asset_id: asset.id,
            asset,
            role: ordered[i]?.role || "reference",
          })),
        }
      : restoreDraft({ ...initial(targetKind), ...run.parameters, model: run.model_id || initial(targetKind).model, prompt: run.prompt }, targetKind);
    if (vary) value.seed = newSeed();
    (targetKind === "video" ? setVideoDraft : setImageDraft)(value);
    setKind(targetKind);
    if (vary) void generate(value);
    setDetail(null);
    requestAnimationFrame(() => {
      promptRef.current?.focus();
      promptRef.current?.scrollIntoView({
        block: "center",
        behavior: enabled ? "smooth" : "instant",
      });
    });
  }
  function asFirstFrame(asset: Asset) {
    const value = restoreDraft(videoDraft, "video");
    const compatible = catalog?.models.filter((candidate) =>
      candidate.kind === "video" && !candidate.text_only && candidate.partition !== "ref2va",
    ) || [];
    const target = compatible.find((candidate) => candidate.id === value.model)
      || compatible.find((candidate) => candidate.id === "h3")
      || compatible[0];
    if (!target) {
      setError(t("未找到支持首帧的模型，请先查看创作模型。", "No model supports first frames. Check your creative models first."));
      return;
    }
    const sizes = target.variants?.fl2va?.sizes || target.sizes;
    const size = sizes.find(([w, h]) => w === value.width && h === value.height)
      || sizeForRatio(sizes, [value.width, value.height], aspectRatio(value.width, value.height))
      || sizes[0];
    value.model = target.id;
    value.fast = false;
    if (size) [value.width, value.height] = size;
    if (!target.frames.includes(value.num_frames)) value.num_frames = target.frames[0];
    value.references = [
      { asset_id: asset.id, role: "first", asset },
      ...value.references.filter((r) => r.role === "last"),
    ];
    setVideoDraft(value);
    setKind("video");
    promptRef.current?.focus();
  }
  async function toCanvas(run: Run) {
    const nodes = run.assets.map((asset, index) => ({
      ...makeNode(asset.kind, 80 + index * 330, 80),
      asset_id: asset.id,
      title: run.prompt.slice(0, 80),
      origin_run: run.id,
    }));
    const project = await mutation<Project>("/api/creative/projects", {
      title: run.prompt.slice(0, 60),
      nodes,
    });
    try {
      localStorage.setItem("onecat:last-canvas", project.id);
    } catch {
      /* Navigation still succeeds when storage is unavailable. */
    }
    await navigate({ to: "/canvas" });
  }
  return (
    <section className="oc-generation-page">
      <div className="oc-generation-intro">
        <span>1CAT CREATIVE</span>
        <h1>{t("让想法，成为画面。", "Bring your ideas to life.")}</h1>
        <p>
          {t(
            "写下描述，剩下的交给工作台。",
            "Describe your idea. Your workspace handles the rest.",
          )}
        </p>
      </div>
      <form
        className="oc-generation-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void generate();
        }}
      >
        <Segments
          className="oc-generation-kind"
          aria-label={t("生成类型", "Generation type")}
        >
          {(["video", "image"] as const).map((value) => (
            <button
              key={value}
              type="button"
              disabled={uploading}
              aria-pressed={kind === value}
              data-active={kind === value}
              onClick={() => setKind(value)}
            >
              {value === "video" ? <Film size={16} /> : <ImageIcon size={16} />}
              {value === "video" ? t("视频", "Video") : t("图片", "Image")}
            </button>
          ))}
        </Segments>
        <CreativePrompt
          inputRef={promptRef}
          value={draft.prompt}
          onChange={(prompt) => update({ prompt })}
        />
        <div className="oc-generation-references">
          <AnimatePresence initial={false}>
            {draft.references.map((ref) => (
              <motion.div
                key={ref.asset_id + ref.role}
                initial={enabled ? { opacity: 0, scale: 0.94 } : false}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.94 }}
                transition={{ duration: enabled ? MOTION.state : 0 }}
                className="oc-generation-reference"
              >
                {ref.asset.thumbnail_url || ref.asset.kind === "image" ? (
                  <img
                    src={ref.asset.thumbnail_url || ref.asset.url}
                    alt={ref.asset.name}
                  />
                ) : (
                  <Film size={22} />
                )}
                <small>
                  {ref.role === "first"
                    ? t("首帧", "First frame")
                    : ref.role === "last"
                      ? t("尾帧", "Last frame")
                      : t("参考素材", "Reference")}
                </small>
                <button
                  type="button"
                  aria-label={t("移除素材", "Remove reference")}
                  onClick={() =>
                    update({
                      references: draft.references.filter((r) => r !== ref),
                    })
                  }
                >
                  <X size={13} />
                </button>
              </motion.div>
            ))}
          </AnimatePresence>
          {kind === "video" && model && !model.text_only &&
            (["first", "last", "reference"] as const).map((role) => (
              <label key={role} className="oc-generation-upload">
                <Plus size={16} />
                {role === "first"
                  ? t("首帧", "First frame")
                  : role === "last"
                    ? t("尾帧", "Last frame")
                    : t("参考素材", "References")}
                <input
                  type="file"
                  disabled={uploading}
                  multiple={role === "reference"}
                  accept={
                    role === "reference"
                      ? "image/*,video/mp4,audio/*"
                      : "image/*"
                  }
                  onChange={(event) => {
                    void upload(event.target.files, role);
                    event.target.value = "";
                  }}
                />
              </label>
            ))}
          {uploading && <LoaderCircle size={18} className="animate-spin" />}
        </div>
        {draft.references.some((r) => r.role !== "reference") && (
          <p className="oc-generation-reference-note">
            {t(
              "首尾帧会按所选画面比例居中裁切，保留原图比例。",
              "Keyframes are center-cropped to the selected aspect ratio, without stretching.",
            )}
          </p>
        )}
        <div className="oc-generation-options">
          <label className="oc-generation-model-choice">
            <span>{t("模型", "Model")}</span>
            <select
              aria-label={t("模型", "Model")}
              disabled={!catalog}
              value={model?.id || draft.model}
              title={variant ? t(variant.name, variant.name_en) : model?.name}
              onChange={(event) => {
                const next = models.find((m) => m.id === event.target.value);
                const sizes = next?.variants?.[partition]?.sizes || next?.sizes || [];
                const size = sizes.some(([w, h]) => w === draft.width && h === draft.height)
                  ? [draft.width, draft.height] : sizes[0];
                update({ model: event.target.value, fast: false,
                  ...(size ? { width: size[0], height: size[1] } : {}),
                  ...(next?.frames.length && !next.frames.includes(draft.num_frames)
                    ? { num_frames: next.frames[0] } : {}),
                });
              }}
            >
              {!model && <option value={draft.model} disabled>{!catalog ? t("正在读取模型…", "Loading models…") : t("请选择可用模型", "Choose an available model")}</option>}
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.fast_variant ? m.name : m.variants?.[partition] ? t(selectedVariant(m.variants[partition])!.short_name || m.variants[partition].name, selectedVariant(m.variants[partition])!.short_name_en || m.variants[partition].name_en) : m.name}
                </option>
              ))}
            </select>
          </label>
          <OutputFields
            kind={kind}
            value={draft}
            onChange={update}
            model={model}
            legacySizes={outputSizes}
            loading={!catalog}
          />
          {kind === "video" && (
            <div className="oc-generation-fast">
              <label htmlFor="creative-fast">{t("Fast · 实验性", "Fast · Experimental")}</label>
              <Switch id="creative-fast" aria-describedby="creative-fast-note"
                checked={draft.fast}
                disabled={!draft.fast && (!model?.fast_available || draft.references.length > 0)}
                onCheckedChange={(fast) => update({ fast })} />
            </div>
          )}
          <Button
            className="oc-generation-submit"
            type="submit"
            disabled={busy || !canSubmit}
          >
            {busy ? (
              <LoaderCircle size={18} className="animate-spin" />
            ) : (
              <ArrowUp size={18} />
            )}
            {busy
              ? t("准备中", "Preparing")
              : downloadBytes
                ? t("下载并生成", "Download & generate")
                : t("生成", "Generate")}
          </Button>
        </div>
        {kind === "video" && <p id="creative-fast-note" className="oc-generation-fast-note">
          {!catalog ? t("正在读取模型能力…", "Loading model capabilities…") : model?.fast_variant
            ? draft.fast
              ? t("VSA 稀疏注意力 · 原版浮点权重 + 专用 FastH3 适配器。画质验收尚未通过，输出可能有差异；首次使用需要下载并重新加载模型。", "VSA sparse attention · original floating weights + the dedicated FastH3 adapter. Quality acceptance is incomplete; output may differ. First use downloads and reloads the model.")
              : !model.fast_available
                ? t("当前运行环境尚未包含 VSA 内核，更新后可开启 Fast。", "Update the native runtime to enable Fast VSA.")
                : t("FlashAttention · 原始权重 + FastH3 Dense。开启 Fast 切换 VSA 专用适配器，仅支持文生视频。", "FlashAttention · original weights + FastH3 Dense. Fast switches to the dedicated VSA adapter. Text to video only.")
            : t("Fast VSA 需选择 Minimax-H3 原始权重；现有 INT8 / LightX2V 工作流保持不变。", "Select the original Minimax-H3 weights for Fast VSA. INT8 / LightX2V workflows keep their existing execution.")}
        </p>}
        {model?.text_only && draft.references.length > 0 && <p role="alert" className="oc-generation-reference-note">{t("该工作流仅支持文字，请移除参考素材或选择原来的 H3 模型。", "This workflow accepts text only. Remove references or select another H3 model.")}</p>}
        <div className="oc-generation-composer-note">
          <span>
            {t(
              "作品保存在服务器，离开页面也会继续生成。",
              "Your results are saved on the server. Generation continues when you leave.",
            )}
          </span>
          {!!downloadBytes && (
            <span>
              {t("需下载", "Download")}{" "}
              {bytes(downloadBytes)} · ModelScope
            </span>
          )}
        </div>
        {model && !supportedSize && <p role="alert" className="oc-generation-reference-note">{t("当前工作流不支持已选分辨率，请重新选择。", "This workflow does not support the selected resolution. Choose another size.")}</p>}
        {variant && (
          <details className="oc-generation-model-files">
            <summary>{t("模型文件与工作流", "Model files and workflow")}</summary>
            <p>{t(variant.name, variant.name_en)} · {variant.denoise_steps == null ? t("跟随模型工作流", "Model workflow defaults") : `${variant.denoise_steps} ${t("步", "steps")}`}</p>
            {variant.artifacts.map((file) => (
              <div key={file.filename}>
                <span>{file.role === "lora" ? "LoRA" : t("基础权重", "Base weights")} · {file.repository}</span>
                <code>{file.filename}</code>
              </div>
            ))}
          </details>
        )}
        {model && !model.runtime_available && (
          <p className="oc-generation-action-note">
            {t(
              "需要更新原生创作运行环境。",
              "Update the native creative runtime to generate.",
            )}{" "}
            <Link to="/setup">{t("打开安装引导", "Open setup")}</Link>
          </p>
        )}
        {model && !model.hardware_available && (
          <p>
            {t(
              "当前可用硬件不足以运行这个模型。",
              "This model needs more compatible GPU memory.",
            )}
          </p>
        )}
        {model && model.validation !== "verified" && (
          <small className="oc-generation-action-note">
            {t(
              "此原生适配尚待完整验证",
              "Native adaptation awaiting full validation",
            )}
          </small>
        )}
        <ErrorNotice error={error || catalogError} />
      </form>
      <section className="oc-generation-history">
        <div className="oc-generation-history-title">
          <h2>{t("你的作品", "Your creations")}</h2>
          <span>
            {t(
              "图片与视频，随时继续创作",
              "Images and videos, ready for your next idea",
            )}
          </span>
        </div>
        <ErrorNotice error={historyError} />
        {!records.length && (
          <div className="oc-generation-empty">
            <ImageIcon size={28} />
            <p>
              {t(
                "第一件作品，从一个想法开始。",
                "Your first creation starts with an idea.",
              )}
            </p>
          </div>
        )}
        <div className="oc-generation-grid">
          {records.map((run) => (
            <article
              className="oc-generation-card"
              key={run.id}
              data-run-id={run.id}
            >
              {run.assets.length ? (
                <div className="oc-generation-media">
                  {run.assets.map((asset) =>
                    asset.kind === "video" ? (
                      <video
                        key={asset.id}
                        controls
                        preload="metadata"
                        playsInline
                        poster={asset.thumbnail_url}
                        src={asset.url}
                      />
                    ) : (
                      <button key={asset.id} type="button" className="oc-generation-image-button" aria-label={t("查看作品详情", "View creation details")} onClick={() => setDetail(run)}><img
                        src={asset.thumbnail_url || asset.url}
                        alt={run.prompt}
                        loading="lazy"
                      /></button>
                    ),
                  )}
                </div>
              ) : (
                <div className="oc-generation-pending">
                  <RunStatus run={run} now={now} showElapsed={false} />
                  {run.state === "failed" && run.error && (
                    <CreationError error={run.error} />
                  )}
                </div>
              )}
              <div className="oc-generation-card-body">
                <p title={run.prompt}>{run.prompt}</p>
                <div className="oc-generation-meta">
                  <span>{run.model}</span>
                  <button onClick={() => setDetail(run)}>
                    {run.parameters.width} × {run.parameters.height}
                    {!run.workflow.startsWith("image-")
                      ? ` · ${(run.parameters.num_frames / 24).toFixed(1)} s`
                      : ""}
                  </button>
                </div>
                <button className="oc-generation-duration" onClick={() => setDetail(run)} title={t("包含等待、准备模型、生成和保存；点击查看明细", "Includes waiting, preparation, generation and saving. Click for details.")}>
                  <Clock3 size={13} />
                  {terminal(run.state) ? t("总耗时", "Total time") : t("已用时间", "Elapsed")}
                  <span>{elapsed(totalSeconds(run, now))}</span>
                </button>
                {(run.intent as Partial<Draft> | undefined)?.fast && <span className="oc-generation-fast-result">Fast · VSA · {t("实验性", "Experimental")}</span>}
                <div className="oc-generation-card-actions">
                  {run.assets.map((asset) => (
                    <a
                      key={asset.id}
                      href={asset.url + "?download=true"}
                      download
                    >
                      <Download size={15} />
                      {t("下载", "Download")}
                    </a>
                  ))}
                  {run.assets[0]?.kind === "image" && (
                    <button disabled={!catalog} onClick={() => asFirstFrame(run.assets[0])}>
                      {t("作为视频首帧", "Use as first frame")}
                    </button>
                  )}
                  {run.assets.length > 0 && (
                    <Action variant="ghost" run={() => toCanvas(run)}>
                      <Workflow size={14} />
                      {t("发送到画布", "Send to canvas")}
                    </Action>
                  )}
                  <button onClick={() => reuse(run)}>
                    {t("复用参数", "Reuse settings")}
                  </button>
                  {terminal(run.state) ? (
                    <button onClick={() => reuse(run, true)}>
                      <RefreshCw size={14} />
                      {t("再次生成", "Create again")}
                    </button>
                  ) : (
                    <Action
                      variant="ghost"
                      disabled={!!run.cancel_note || !!run.cancel_requested}
                      run={async () => {
                        await mutation(`/api/creative/runs/${run.id}/cancel`);
                        refresh();
                      }}
                    >
                      {run.cancel_note
                        ? t("等待生成结束", "Finishing generation")
                        : t("取消", "Cancel")}
                    </Action>
                  )}
                </div>
              </div>
            </article>
          ))}
        </div>
        {nextCursor != null && (
          <Action
            variant="outline"
            run={async () => {
              const page = await api<{
                items: Run[];
                next_cursor: string | null;
              }>(`/api/creative/generations?before=${nextCursor}`);
              setOlder((previous) => [...previous, ...page.items]);
              setCursor(page.next_cursor);
            }}
          >
            {t("加载更多作品", "Load more creations")}
          </Action>
        )}
      </section>
      <Modal
        open={!!pending}
        onOpenChange={(open) => {
          if (!busy && !open) setPending(null);
        }}
        title={t("切换创作模型", "Switch creative model")}
        returnFocusRef={promptRef}
      >
        <p>
          {t(
            "生成需要释放以下 Studio 服务。当前请求完成后会切换，生成将自动继续。",
            "Generation needs to release these Studio services. The switch waits for active requests, then generation continues automatically.",
          )}
        </p>
        <ul>
          {pending?.prepared.impacts.map((impact) => (
            <li key={impact.id}>{impact.name}</li>
          ))}
        </ul>
        <ErrorNotice error={error} />
        <div className="oc-generation-confirm">
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => setPending(null)}
          >
            {t("返回修改", "Go back")}
          </Button>
          <Button disabled={busy} onClick={() => void confirm()}>
            {busy ? <LoaderCircle size={16} className="animate-spin" /> : null}
            {t("确认切换并生成", "Switch and generate")}
          </Button>
        </div>
      </Modal>
      <Modal
        open={!!detail}
        onOpenChange={(open) => {
          if (!open) setDetail(null);
        }}
        title={t("作品详情", "Creation details")}
      >
        {liveDetail && (
          <>
            {liveDetail.assets.map((asset) =>
              asset.kind === "video" ? (
                <video key={asset.id} controls playsInline src={asset.url} />
              ) : (
                <img
                  key={asset.id}
                  style={{ maxHeight: "55vh", objectFit: "contain" }}
                  src={asset.url}
                  alt={liveDetail.prompt}
                />
              ),
            )}
            <p>{liveDetail.prompt}</p>
            <RunStatus run={liveDetail} now={now} />
            <dl className="oc-generation-details">
              <dt>{t("总耗时", "Total time")}</dt>
              <dd>{elapsed(totalSeconds(liveDetail, now))}</dd>
              <dt>{t("等待与准备", "Waiting and preparation")}</dt>
              <dd>{elapsed(liveDetail.timing?.preparation_seconds)}</dd>
              <dt>{liveDetail.timing?.generation_source === "native" ? t("模型生成耗时", "Native generation time") : t("生成与保存", "Generation and saving")}</dt>
              <dd>{elapsed(liveDetail.timing?.generation_seconds)}</dd>
              <dt>{t("任务 ID", "Task ID")}</dt>
              <dd>{liveDetail.id}</dd>
              <dt>{t("模型", "Model")}</dt>
              <dd>{liveDetail.model}</dd>
              <dt>{t("实际规格", "Actual output")}</dt>
              <dd>
                {liveDetail.assets
                  .map(
                    (a) =>
                      `${a.width} × ${a.height}${a.duration ? ` · ${a.duration.toFixed(3)} s` : ""}`,
                  )
                  .join(", ") || "—"}
              </dd>
              <dt>Seed</dt>
              <dd>{liveDetail.parameters.seed}</dd>
            </dl>
            <CreationError
              error={
                liveDetail.state === "failed" ? liveDetail.error : undefined
              }
            />
          </>
        )}
      </Modal>
    </section>
  );
}
