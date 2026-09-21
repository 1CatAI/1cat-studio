import { Phase } from "./motion";
// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { Link } from "@tanstack/react-router";
import { LoaderCircle, Check, Circle, RotateCcw, Square } from "lucide-react";
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
  return (
    <div
      className={compact ? "oc-launch-banner" : "oc-panel oc-launch-progress"}
      aria-live="polite"
    >
      <div className="oc-row">
        <div className="oc-row oc-launch-title">
          {e.state === "loading" && (
            <LoaderCircle className="animate-spin" size={18} />
          )}
          <strong>
            {error
              ? t("连接中断，正在重新同步", "Disconnected; reconnecting")
              : waiting ? jobLabel(e.startup_job?.stage || "queued", t) : name
                ? t(name[1], name[2])
                : e.state === "failed"
                  ? t("启动失败", "Startup failed")
                  : e.state === "stopped"
                    ? t("模型已停止", "Stopped")
                    : t("检查服务状态", "Checking service")}
          </strong>
          {e.state === "loading" && (
            <span className="oc-muted">
              {t("已用", "Elapsed")} {Math.floor(seconds / 60)}:
              {String(seconds % 60).padStart(2, "0")}
            </span>
          )}
          {e.phase_progress && e.state === "loading" && (
            <span>
              {e.phase_progress.done}/{e.phase_progress.total}
            </span>
          )}
        </div>
        <div className="oc-actions">
          {e.actions?.cancel && (
            <Action
              disabled={e.startup_job?.cancel_requested}
              run={() => mutation(`/api/jobs/${e.job_id}/cancel`, {})}
            >
              <Square size={14} />
              {t("取消启动", "Cancel startup")}
            </Action>
          )}
          {e.actions?.stop && !e.actions.cancel && ["loading", "unavailable"].includes(e.state) && (
            <Action run={() => mutation("/api/inference/unload", {})}>
              <Square size={14} />{t("卸载模型", "Unload model")}
            </Action>
          )}
          {e.actions?.retry && e.profile_id && (
            <Action
              run={() =>
                mutation("/api/inference/load", { profile_id: e.profile_id })
              }
            >
              <RotateCcw size={14} />
              {t("重试", "Retry")}
            </Action>
          )}
          {compact && e.detail && <span className="oc-muted">{e.detail}</span>}
          {compact && <Link to="/service">{t("查看详情", "Details")}</Link>}
        </div>
      </div>
      {!compact && (
        <>
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
                "编译和 Graph 捕获可能需要数分钟；显存占用不代表服务已经就绪。",
                "Compilation and graph capture may take several minutes. Allocated memory does not mean the service is ready.",
              )}
            </p>
          )}
          {e.detail && e.state === "loading" && (
            <p className="oc-code oc-launch-detail">{e.detail}</p>
          )}
          <ErrorNotice error={error || e.error} />
        </>
      )}
    </div>
  );
}
