// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useState } from "react";
import { Play, Square, FileText, LoaderCircle } from "lucide-react";
import { Progress } from "@/onecat/ui";
import { Action, ErrorNotice, Modal, jobLabel, useText } from "../common";
import { mutation, useQuery } from "../api";
import type { Service } from "./types";
import { runLabel } from "./types";

export function ServiceLifecycle({ service }: { service: Service }) {
  const t = useText();
  const [logOpen, setLogOpen] = useState(false);
  const { data: log, error: logError } = useQuery<{ text: string }>(
    logOpen ? `/api/creative/services/${service.id}/log` : null, 1500,
  );
  const s = service.instance;
  const busy = ["loading", "stopping"].includes(s.state);
  const seconds = Math.floor(s.elapsed_s || 0);
  const local = service.kind === "h3-local" || service.kind === "image-local";
  const [submitError, setSubmitError] = useState("");
  async function submit(action: "load" | "stop") {
    setSubmitError("");
    try { await mutation(`/api/creative/services/${service.id}/${action}`); }
    catch (e) { setSubmitError((e as Error).message); throw e; }
  }
  return <div className="oc-creative-lifecycle">
    <div className="oc-creative-stage" role="status">
      {busy && <LoaderCircle size={14} className="animate-spin" />}
      <strong>{s.cancel_requested ? t("取消中，正在释放模型", "Cancelling and releasing model") : busy && s.phase ? jobLabel(s.phase, t) : runLabel(s.state, t)}</strong>
      {busy && <span>{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}</span>}
    </div>
    {busy && (s.phase_progress ? <>
      <Progress value={s.phase_progress.percent} />
      <small>{s.phase_progress.done} / {s.phase_progress.total}</small>
    </> : <div className="oc-progress-indeterminate" />)}
    {busy && <small className="oc-creative-detail">{s.detail || t("请求已接收；正在等待服务就绪，暂无可量化进度。", "Request received. Waiting for readiness; no measurable progress yet.")}</small>}
    <ErrorNotice error={submitError || s.operation_error || s.error} />
    <div className="oc-creative-controls">
      {!busy && s.state !== "ready" && <Action run={() => submit("load")}><Play size={15} />{local ? t("加载所选模型", "Load selected model") : t("连接所选服务", "Connect service")}</Action>}
      {local && (s.can_stop || busy || s.state === "ready" || s.state === "unavailable") && <Action variant="outline" disabled={s.state === "stopping" || s.cancel_requested} run={() => submit("stop")}>
        <Square size={14} />{s.state === "loading" ? t("取消加载并释放显存", "Cancel load and release memory") : s.state === "stopping" ? t("正在卸载", "Unloading") : t("卸载模型", "Unload model")}
      </Action>}
      {local && <Action variant="ghost" run={async () => { setLogOpen(true); }}><FileText size={14} />{t("加载日志", "Loading log")}</Action>}
    </div>
    <Modal open={logOpen} onOpenChange={setLogOpen} title={t("模型加载日志", "Model loading log")}>
      <ErrorNotice error={logError} />
      <pre className="oc-creative-log">{log?.text || t("暂无日志，任务可能正在排队。", "No logs yet. The task may still be queued.")}</pre>
    </Modal>
  </div>;
}
