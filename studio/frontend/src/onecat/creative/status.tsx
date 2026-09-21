// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { Check, LoaderCircle, AlertCircle } from "lucide-react";
import { useText } from "../common";
import { terminal, type Run } from "../canvas/types";
import { elapsed, totalSeconds } from "./timing";

const stages: Record<string, [string, string]> = {
  queued: ["等待生成", "Queued"],
  waiting_resources: ["等待资源", "Waiting for resources"],
  waiting_for_engine: ["等待模型操作完成", "Waiting for model operation"],
  waiting_for_requests: ["等待当前请求完成", "Waiting for active requests"],
  checking: ["检查运行条件", "Checking requirements"],
  downloading: ["下载组件", "Downloading components"],
  checking_model: ["校验组件", "Verifying components"],
  releasing_model: ["释放前一个模型", "Unloading previous model"],
  loading_weights: ["加载模型", "Loading model"],
  checking_service: ["检查服务", "Checking service"],
  ready: ["服务就绪", "Service ready"],
  submitting: ["提交生成", "Submitting generation"],
  encoding: ["编码输入", "Encoding inputs"],
  denoising: ["生成画面", "Generating frames"],
  staging_model: ["准备生成画面", "Preparing generation"],
  generating: ["正在生成", "Generating"],
  decoding: ["解码", "Decoding"],
  packaging: ["封装视频", "Packaging video"],
  saving: ["保存作品", "Saving artwork"],
  completed: ["已完成", "Completed"],
  failed: ["生成失败", "Generation failed"],
  cancelled: ["已取消", "Cancelled"],
};
export { elapsed } from "./timing";
export function RunStatus({ run, now, showElapsed = true }: { run: Run; now: number; showElapsed?: boolean }) {
  const t = useText(),
    finished = terminal(run.state);
  const stage = finished ? run.state : run.stage;
  const labels = stages[stage] || ["处理中", "Processing"];
  const preparing = [
    "waiting_resources",
    "waiting_for_engine",
    "waiting_for_requests",
    "checking",
    "downloading",
    "checking_model",
    "releasing_model",
    "loading_weights",
    "checking_service",
    "ready",
  ].includes(stage);
  const count =
    stage === "denoising"
      ? run.denoise_progress
      : preparing && run.preparation_progress?.stage === stage
        ? run.preparation_progress.phase_progress
        : undefined;
  const current =
    count && ("completed" in count ? count.completed : count.done);
  const total = count?.total;
  return (
    <div className="oc-creation-progress" data-state={run.state}>
      <div className="oc-creation-progress-heading" role="status">
        {run.state === "completed" ? (
          <Check size={17} />
        ) : run.state === "failed" ? (
          <AlertCircle size={17} />
        ) : !finished ? (
          <LoaderCircle size={17} className="animate-spin" />
        ) : null}
        <strong>{t(...labels)}</strong>
        {showElapsed && <span>{elapsed(totalSeconds(run, now))}</span>}
      </div>
      {!finished && (
        <>
          <div className="oc-generation-stages">
            <span data-active={preparing}>
              {t("准备模型", "Prepare model")}
            </span>
            <i />
            <span data-active={!preparing}>
              {t("生成作品", "Generate artwork")}
            </span>
          </div>
          {total && current != null ? (
            <>
              <progress value={current} max={total} />
              <small>
                {current} / {total}{" "}
                {stage === "denoising" ? t("步", "steps") : t("项", "items")}
              </small>
            </>
          ) : (
            <div className="oc-progress-indeterminate" />
          )}
          {stage === "downloading" &&
          run.preparation_progress?.expected_bytes ? (
            <small>
              {(
                (run.preparation_progress.downloaded_bytes || 0) /
                1024 ** 3
              ).toFixed(1)}{" "}
              /{" "}
              {(run.preparation_progress.expected_bytes / 1024 ** 3).toFixed(1)}{" "}
              GB ·{" "}
              {(
                (run.preparation_progress.bytes_per_second || 0) /
                1024 ** 2
              ).toFixed(1)}{" "}
              MB/s
            </small>
          ) : null}
          <small>
            {t("当前阶段", "Current stage")}{" "}
            {elapsed(now - (run.stage_started_at || run.created_at))} ·{" "}
            {t("更新于", "Updated")}{" "}
            {new Date(
              (run.native_updated_at || run.updated_at || run.created_at) *
                1000,
            ).toLocaleTimeString()}
          </small>
          {(run.detail || run.preparation_progress?.detail) && (
            <p>{run.detail || run.preparation_progress?.detail}</p>
          )}
          {run.connection_lost && (
            <p>
              {t(
                "连接暂时中断，正在恢复任务状态。",
                "Connection interrupted. Reconnecting to the task.",
              )}
            </p>
          )}
        </>
      )}
      {run.cancel_note && <p>{run.cancel_note}</p>}
    </div>
  );
}
