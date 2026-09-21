import { Segments } from "./motion";
import { Phase } from "./motion";
// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { Download, ExternalLink, Play, Search, Settings2 } from "lucide-react";
import { Input } from "@/onecat/ui";
import { Button } from "@/onecat/ui";
import { useBrowserState } from "./browser-state";
import { useQuery, mutation, api, type Profile, type Job, type Engine, type Model } from "./api";
import { Action, Empty, ErrorNotice, bytes, useText } from "./common";
import { DownloadRate } from "./download-rate";
import { ModelCard, ModelIdentity } from "./model-card";

type Support = {
  id: string;
  name: string;
  family: string;
  quantization: string;
  runtime: string;
  hardware: string;
  features: string[];
  note: string;
  evidence: string[];
  catalog_id: string | null;
  downloadable: boolean;
  compatible: boolean;
  reasons: string[];
  recommendations: string[];
  bytes: number | null;
  recommended: Partial<Profile> | null;
  gpu: { count: number; tp: number; pp: number } | null;
  model_id: string | null;
  job:
    | (Job & { state: string; error?: string; cancel_requested?: boolean })
    | null;
};

export type LaunchDefaults = {
  profile: Profile | null;
  reasons: string[];
  recommendations: string[];
  can_create: boolean;
};

export async function modelDefaults(catalogId: string, modelId?: string) {
  return api<LaunchDefaults>(
    `/api/models/launch-defaults?catalog_id=${encodeURIComponent(catalogId)}${modelId ? `&model_id=${encodeURIComponent(modelId)}` : ""}`,
  );
}

export function SupportedModels({
  onConfigure,
  downloadedOnly = false,
}: {
  onConfigure: (profile: Profile) => void;
  downloadedOnly?: boolean;
}) {
  const t = useText();
  const translate = (text: string) => {
    const separator = text.indexOf(" / ");
    return separator < 0
      ? text
      : t(text.slice(0, separator), text.slice(separator + 3));
  };
  const { data, error } = useQuery<{ items: Support[]; audited_at: string }>(
    "/api/models/supported",
    2000,
  );
  const { data: engine } = useQuery<Engine>("/api/inference/status");
  const { data: models } = useQuery<{ items: Model[] }>("/api/models/list", 5000);
  const [query, setQuery] = useBrowserState("onecat:models-search:" + downloadedOnly, ""),
    [family, setFamily] = useBrowserState("onecat:models-family:" + downloadedOnly, "");
  const [scope, setScope] = useBrowserState("onecat:models-scope", "machine");
  const [publisher, setPublisher] = useBrowserState("onecat:models-publisher:" + downloadedOnly, "");
  const [selected, setSelected] = useState<Support | null>(null);
  const detailTrigger = useRef<HTMLElement | null>(null);
  function openDetails(item: Support, button: HTMLButtonElement) {
    detailTrigger.current = button;
    setSelected(item);
  }
  const all = data?.items || [];
  // The task endpoint includes transfer timestamps and avoids rescanning GPUs.
  const hasDownload = all.some(
    (item) =>
      item.job &&
      !["completed", "failed", "cancelled"].includes(item.job.state),
  );
  const { data: jobs } = useQuery<{ items: Job[] }>(
    hasDownload ? "/api/jobs" : null,
    1000,
  );
  const machineMatches = (item: Support) => item.downloadable && item.reasons.every(reason => reason.startsWith("需要运行环境 / Requires runtime:"));
  const machineCount = all.filter(machineMatches).length;
  const filtered = all.filter(
    (item) =>
      (!downloadedOnly || !!item.model_id) &&
      (downloadedOnly || scope === "all" || machineMatches(item)) &&
      (!family || item.family === family) &&
      (!publisher || item.catalog_id?.split("/")[0] === publisher) &&
      [item.name, item.catalog_id, item.family, item.quantization, ...item.features]
        .join(" ")
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const downloadable = filtered.filter((item) => item.downloadable).sort((a, b) =>
    Number(b.compatible) - Number(a.compatible) || Number(!!b.model_id) - Number(!!a.model_id));
  const references = filtered.filter((item) => !item.downloadable);
  const reasonText = (reason: string) => {
    const runtime = "需要运行环境 / Requires runtime: ";
    if (reason.startsWith(runtime))
      return (
        t("需要运行环境：", "Requires runtime: ") + reason.slice(runtime.length)
      );
    if (reason.startsWith("需要 / Requires "))
      return t("需要 ", "Requires ") + reason.slice("需要 / Requires ".length);
    if (reason.includes("Studio 尚不支持流水线并行"))
      return t(
        "此配方需要流水线并行，暂不能在 Studio 一键启动",
        "This recipe requires pipeline parallelism; one-click launch is not available",
      );
    return translate(reason);
  };
  async function configure(item: Support) {
    const defaults = await modelDefaults(
      item.catalog_id!,
      item.model_id || undefined,
    );
    if (!defaults.profile)
      throw Error(defaults.reasons.map(reasonText).join(" · "));
    onConfigure(defaults.profile);
  }
  return (
    <section className="oc-supported-models">
      <div className="oc-row oc-spaced">
        <div>
          <h2>{downloadedOnly ? t("已下载 · 随时启动", "Downloaded · Ready to launch") : t("为这台机器选择模型", "Find a model for this machine")}</h2>
          <p className="oc-muted">
            {t(
              `${all.filter((item) => item.downloadable).length} 个版本 · ${machineCount} 个适合本机`,
              `${all.filter((item) => item.downloadable).length} versions · ${machineCount} hardware matches`,
            )}
          </p>
        </div>
      </div>
      {!downloadedOnly && <Segments className="oc-segments oc-model-scope" role="group" aria-label={t("适配范围", "Compatibility filter")}>
        <button aria-pressed={scope === "machine"} onClick={() => setScope("machine")}>{t("适合本机", "For this machine")}</button>
        <button aria-pressed={scope === "all"} onClick={() => setScope("all")}>{t("全部支持", "All supported")}</button>
      </Segments>}
      <div className="oc-support-search">
        <Search size={18} />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t(
            "搜索模型、发布者或量化…",
            "Search models, publishers or quantization…",
          )}
        />
        <select
          aria-label={t("模型系列", "Model family")}
          value={family}
          onChange={(e) => setFamily(e.target.value)}
        >
          <option value="">{t("全部系列", "All families")}</option>
          {[...new Set(all.map((item) => item.family))].map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
        <select aria-label={t("模型发布者", "Model publisher")} value={publisher} onChange={event => setPublisher(event.target.value)}>
          <option value="">{t("全部发布者", "All publishers")}</option>
          {[...new Set(all.flatMap(item => item.catalog_id ? [item.catalog_id.split("/")[0]] : []))].sort().map(owner => <option key={owner}>{owner}</option>)}
        </select>
      </div>
      <ErrorNotice error={error} />
      <div className="oc-model-grid oc-support-grid">
        {downloadable.map((item) => {
          const job =
            item.job &&
            !["completed", "failed", "cancelled"].includes(item.job.state)
              ? jobs?.items.find((job) => job.id === item.job?.id) || item.job
              : item.job;
          const downloading =
            !!job && !["completed", "failed", "cancelled"].includes(job.state);
          const isActive = !!engine?.profile && (engine.profile.catalog_id === item.catalog_id ||
            models?.items.some(model => model.id === item.model_id && model.path === engine.profile?.model_path));
          const ready = isActive && engine?.state === "ready";
          const starting = isActive && ["loading", "unavailable"].includes(engine?.state || "");
          const needsRuntime = item.reasons.length > 0 && item.reasons.every(reason => reason.startsWith("需要运行环境 / Requires runtime:"));
          return (
            <article
              className="oc-model-card oc-support-card"
              data-model-id={item.id}
              key={item.id}
            >
              <div className="oc-row">
                <span className="oc-kicker">{item.family}</span>
                <span className={ready || item.compatible ? "oc-status-good" : "oc-status-warn"}>
                  {ready ? t("运行中", "Running") : starting ? t("启动中", "Starting") : item.model_id
                    ? t("已下载", "Downloaded")
                    : t("可下载", "Download available")}
                </span>
              </div>
              <h2><button className="oc-model-name-button" onClick={event => openDetails(item, event.currentTarget)}>{item.name}</button></h2>
              <ModelIdentity repo={item.catalog_id!} onOpen={button => openDetails(item, button)} />
              <div className="oc-support-features">
                {item.features.map((feature) => (
                  <span key={feature}>{translate(feature)}</span>
                ))}
              </div>
              {!item.compatible && !ready && <p className="oc-model-fit">{needsRuntime ? t("需准备运行环境", "Runtime setup needed") : t("需要其他硬件或部署配置", "Different hardware or deployment required")}</p>}
              {!!item.recommendations.length && !ready && (
                <p className="oc-status-warn">
                  {item.recommendations.map(reasonText).join(" · ")}
                </p>
              )}
              <p className="oc-muted">{item.gpu?.count} GPU · {item.quantization} · {item.recommended?.max_model_len ? `${item.recommended.max_model_len / 1024}K ${t("上下文", "context")}` : t("查看配置要求", "See requirements")}</p>
              <details className="oc-default-recipe">
                <summary>
                  <Settings2 size={14} />{" "}
                  {t("配置与验证详情", "Configuration and validation")}
                </summary>
                <p>{item.hardware} · 1Cat-vLLM {item.runtime}</p>
                <p>{translate(item.note)}</p>
                {item.reasons.length > 0 && <p className="oc-status-warn">{item.reasons.map(reasonText).join(" · ")}</p>}
                <div className="oc-support-evidence">{item.evidence.map(link => <a href={link} target="_blank" rel="noreferrer" key={link}>
                  <ExternalLink size={12} />{link.includes("/pull/") ? `PR #${link.split("/").at(-1)}` : link.split("/").at(-1)}
                </a>)}</div>
                <dl>
                  <dt>{t("计算精度", "Compute dtype")}</dt>
                  <dd>FP16</dd>
                  <dt>{t("权重量化", "Weight quantization")}</dt>
                  <dd>{item.quantization}</dd>
                  <dt>{t("并行配置", "Parallelism")}</dt>
                  <dd>
                    TP{item.gpu?.tp} / PP{item.gpu?.pp}
                  </dd>
                  <dt>{t("KV 精度", "KV dtype")}</dt>
                  <dd>{item.recommended?.kv_cache_dtype || "auto"}</dd>
                  <dt>{t("总上下文", "Context length")}</dt>
                  <dd>
                    {item.recommended?.max_model_len?.toLocaleString() ||
                      t("模型默认", "Model default")}
                  </dd>
                  <dt>{t("最大输出", "Max output")}</dt>
                  <dd>{t("自动：跟随模型", "Auto: follow model")}</dd>
                  <dt>{t("加速", "Acceleration")}</dt>
                  <dd>{t("默认关闭", "Off by default")}</dd>
                </dl>
                {item.gpu?.pp === 1 && (
                  <Action variant="outline" run={() => configure(item)}>
                    {t("编辑默认配置", "Edit default settings")}
                  </Action>
                )}
                {!!item.reasons.length && (
                  <Link to="/setup" className="oc-runtime-link">
                    {t("安装 / 导入运行环境", "Install / import runtime")}
                  </Link>
                )}
              </details>
              {(downloading || item.job?.state === "failed" || item.job?.state === "cancelled") && <div className="oc-download-stage"><Phase phase={downloading ? job?.cancel_requested ? "cancelling" : job?.stage || "downloading" : item.model_id ? "downloaded" : item.job?.state || "available"} className="oc-download-phase">
              {downloading && (
                <div className="oc-model-download" role="status">
                  <span>
                    {job?.cancel_requested
                      ? t("正在取消…", "Cancelling…")
                      : job?.stage === "checking_model"
                        ? t("正在校验模型…", "Verifying model…")
                        : t("正在下载…", "Downloading…")}
                  </span>
                  <DownloadRate job={job!} />
                  <progress max={100} value={job?.progress || 0} />
                  <span>
                    {bytes(job?.downloaded_bytes || 0)} /{" "}
                    {bytes(job?.expected_bytes || item.bytes || 0)}
                  </span>
                  <Action
                    variant="ghost"
                    disabled={job?.cancel_requested}
                    run={() => mutation(`/api/jobs/${job!.id}/cancel`)}
                  >
                    {t("取消下载", "Cancel download")}
                  </Action>
                </div>
              )}
              {!downloading && item.job?.state === "failed" && (
                <p role="alert" className="oc-status-warn">
                  {item.job.error}
                </p>
              )}
              {!downloading && item.job?.state === "cancelled" && <span className="oc-muted">{t("下载已取消", "Download cancelled")}</span>}
              </Phase></div>}
              <div className="oc-support-footer">
                <span className="oc-muted">
                  {item.bytes != null
                    ? bytes(item.bytes)
                    : t("按专用配方部署", "Requires a dedicated recipe")}
                </span>
                {ready ? <Button asChild><Link to="/chat">{t("开始聊天", "Chat now")}</Link></Button>
                : starting ? <Button asChild variant="outline"><Link to="/service">{t("查看启动进度", "View startup")}</Link></Button>
                : item.model_id && !item.compatible ? needsRuntime
                  ? <Button asChild variant="outline"><Link to="/setup">{t("准备运行环境", "Set up runtime")}</Link></Button>
                  : <Button variant="outline" onClick={event => { const details = event.currentTarget.closest("article")?.querySelector("details"); if (details) details.open = true; }}>{t("查看配置要求", "View requirements")}</Button>
                : <Action
                  disabled={
                    downloading || !!engine?.maintenance
                  }
                  run={async () => {
                    if (!item.model_id)
                      return mutation("/api/hub/download", {
                        repo_id: item.catalog_id,
                      });
                    const profile = await mutation<Profile>(
                      `/api/models/${item.model_id}/default-profile`,
                    );
                    return mutation("/api/inference/load", {
                      profile_id: profile.id,
                    });
                  }}
                  success={
                    item.model_id
                      ? t("模型正在启动", "Model is starting")
                      : t(
                          "已加入 ModelScope 下载队列",
                          "Added to ModelScope downloads",
                        )
                  }
                >
                  {item.model_id ? <Play /> : <Download />}
                  {item.model_id
                    ? t("使用推荐预设启动", "Start with recommended profile")
                    : t("ModelScope 下载", "Download via ModelScope")}
                </Action>}
              </div>
            </article>
          );
        })}
      </div>
      {data && !downloadable.length && (
        <div className="oc-library-empty">
          <Empty title={scope === "machine" && !downloadedOnly && machineCount === 0 ? t("当前硬件暂未匹配已验证配置", "No verified configuration matches this hardware yet") : t("没有符合筛选条件的配置", "No configurations match these filters")}
            description={scope === "machine" && !downloadedOnly && machineCount === 0 ? t("模型目录仍可浏览和下载。查看全部配置可了解所需显存、显卡数量和运行环境。", "You can still browse and download models. View all configurations for memory, GPU and runtime requirements.") : t("试试清除搜索或切换模型系列。", "Try clearing search or changing the family filter.")} />
          <div className="oc-actions">
            {!downloadedOnly && scope === "machine" && <Button variant="outline" onClick={() => { setScope("all"); setQuery(""); setFamily(""); setPublisher(""); }}>{t("查看全部支持配置", "View all supported configurations")}</Button>}
            {(query || family || publisher) && <Button variant="ghost" onClick={() => { setQuery(""); setFamily(""); setPublisher(""); }}>{t("清除筛选", "Clear filters")}</Button>}
          </div>
        </div>
      )}
      {references.length > 0 && (
        <details className="oc-support-references oc-spaced">
          <summary>
            {t(
              `其他项目支持记录（${references.length}）`,
              `Other project support records (${references.length})`,
            )}
          </summary>
          <p className="oc-muted">
            {t(
              "这些记录尚未对应到可直接下载的完整检查点；可查看项目验证细节。",
              "These records do not yet map to a complete downloadable checkpoint. See the project evidence.",
            )}
          </p>
          {references.map((item) => (
            <article className="oc-panel oc-support-reference" key={item.id}>
              <h3>{translate(item.name)}</h3>
              <p>
                {item.hardware} · {item.runtime}
              </p>
              <p>{translate(item.note)}</p>
              <div className="oc-support-evidence">
                {item.evidence.map((link) => (
                  <a href={link} target="_blank" rel="noreferrer" key={link}>
                    <ExternalLink size={12} />
                    {link.includes("/pull/")
                      ? `PR #${link.split("/").at(-1)}`
                      : link.split("/").at(-1)}
                  </a>
                ))}
              </div>
            </article>
          ))}
        </details>
      )}
      {selected?.catalog_id && <ModelCard key={selected.catalog_id} repo={selected.catalog_id} name={selected.name} quantization={selected.quantization} size={selected.bytes} returnFocusRef={detailTrigger} onClose={() => setSelected(null)} />}
    </section>
  );
}
