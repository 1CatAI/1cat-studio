import { Phase } from "./motion";
// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { Link } from "@tanstack/react-router";
import { LoaderCircle, Check, ChevronRight, Circle, CircleStop, Clock3, Layers3, Radio, RotateCcw, Square } from "lucide-react";
import { Progress } from "@/onecat/ui";
import { Action, ErrorNotice, useText, jobLabel } from "./common";
import { mutation, useQuery, type Engine } from "./api";

const phases = [
  ["checking", "检查配置", "Checking configuration"],
  ["loading_weights", "加载权重", "Loading weights"],
  ["compiling", "编译算子", "Compiling kernels"],
  ["capturing_graphs", "捕获 CUDA Graph", "Capturing CUDA graphs"],
  ["checking_service", "检查服务", "Checking service"],
  ["ready", "模型就绪", "Ready"],
];
export function LaunchProgress({ compact = false }: { compact?: boolean }) {
  const t = useText();
  const { data: e, error } = useQuery<Engine>("/api/inference/status");
  if (
    !e ||
    (compact &&
      !error &&
      !["loading", "unavailable"].includes(e.state))
  )
    return null;
  const phase = e.phase || e.state;
  const current = phases.findIndex((p) => p[0] === phase);
  const name = phases[current];
  const waiting = ["waiting_for_requests", "waiting_for_engine", "releasing_model", "queued", "starting"].includes(e.startup_job?.stage || "");
  const seconds = Math.floor(e.elapsed_s || 0);
  const elapsed = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  const label = error
    ? t("连接中断，正在重新同步", "Disconnected; reconnecting")
    : waiting ? jobLabel(e.startup_job?.stage || "queued", t) : name
      ? t(name[1], name[2])
      : e.state === "failed"
        ? t("启动失败", "Startup failed")
        : e.state === "stopped"
          ? t("模型已停止", "Stopped")
          : t("检查服务状态", "Checking service");
  const actions = <>
    {e.actions?.cancel && (
      <Action variant={compact ? "ghost" : "default"}
        disabled={e.startup_job?.cancel_requested}
        run={() => mutation(`/api/jobs/${e.job_id}/cancel`, {})}
      >
        {compact ? <CircleStop size={14} /> : <Square size={14} />}
        {t("取消启动", "Cancel startup")}
      </Action>
    )}
    {e.actions?.stop && !e.actions.cancel && ["loading", "unavailable"].includes(e.state) && (
      <Action variant={compact ? "ghost" : "default"} run={() => mutation("/api/inference/unload", {})}>
        <Square size={14} />{t("卸载模型", "Unload model")}
      </Action>
    )}
    {e.actions?.retry && e.profile_id && (
      <Action variant={compact ? "ghost" : "default"}
        run={() =>
          mutation("/api/inference/load", { profile_id: e.profile_id })
        }
      >
        <RotateCcw size={14} />
        {t("重试", "Retry")}
      </Action>
    )}
  </>;
  if (compact) {
    const loading = e.state === "loading" && !error;
    const progress = loading && !waiting && e.phase_progress && e.phase_progress.total > 0 ? e.phase_progress : null;
    const percent = progress ? Math.max(0, Math.min(100, progress.percent)) : null;
    return <div className="oc-launch-banner">
      <Link className="oc-launch-summary" to="/service" title={e.detail || t("查看启动详情", "View startup details")}>
        <span className="oc-launch-mark" aria-hidden="true">
          {loading ? <Layers3 size={15} /> : <Radio size={17} />}
          {loading && <svg className="oc-launch-ring" data-indeterminate={percent === null || undefined} viewBox="0 0 36 36">
            <circle className="oc-launch-track" cx="18" cy="18" r="16" />
            <circle cx="18" cy="18" r="16" pathLength="100" strokeDasharray={`${percent ?? 28} 100`} />
          </svg>}
        </span>
        <span className="oc-launch-summary-text">
          <strong role="status">{label}</strong>
          {loading && <small>
            <Clock3 aria-hidden="true" size={12} /><span className="sr-only">{t("已用", "Elapsed")} </span>{elapsed}
            {progress && <span>· {progress.done}/{progress.total}</span>}
          </small>}
        </span>
        <ChevronRight className="oc-launch-chevron" size={15} aria-hidden="true" />
        <span className="sr-only">{t("查看详情", "View details")}</span>
      </Link>
      <div className="oc-launch-tools">{actions}</div>
    </div>;
  }
  return (
    <div
      className="oc-panel oc-launch-progress"
      aria-live="polite"
    >
      <div className="oc-row">
        <div className="oc-row oc-launch-title">
          {e.state === "loading" && (
            <LoaderCircle className="animate-spin" size={18} />
          )}
          <strong>{label}</strong>
          {e.state === "loading" && (
            <span className="oc-muted">
              {t("已用", "Elapsed")} {elapsed}
            </span>
          )}
          {e.phase_progress && e.state === "loading" && (
            <span>
              {e.phase_progress.done}/{e.phase_progress.total}
            </span>
          )}
        </div>
        <div className="oc-actions">
          {actions}
        </div>
      </div>

      <div className="oc-launch-stages">
        {phases.map(([id, zh, en], i) => (
          <span key={id} className={i === current ? "active" : ""}>
            <Phase phase={i < current ? "done" : i === current ? "active" : "next"} className="oc-stage-icon">{i < current ? <Check size={14} /> : <Circle size={12} />}</Phase>
            {t(zh, en)}
          </span>
        ))}
      </div>
      {e.state === "loading" &&
        (e.phase_progress ? (
          <Progress value={e.phase_progress.percent} />
        ) : (
          <div className="oc-progress-indeterminate" />
        ))}
      {e.state === "loading" && (
        <p className="oc-muted">
          {t(
            "编译和 CUDA Graph 捕获可能需要几分钟。服务就绪时会更新状态。",
            "Compilation and CUDA graph capture can take a few minutes. The status updates when the service is ready.",
          )}
        </p>
      )}
      {e.detail && e.state === "loading" && (
        <p className="oc-code oc-launch-detail">{e.detail}</p>
      )}
      <ErrorNotice error={error || e.error} />

    </div>
  );
}
