import { LaunchProgress } from "./launch-progress";
import { RequestHistory } from "./request-history";
import { TokenUsage } from "./token-usage";
import { modelLabel } from "./model-label";
import { useBrowserState } from "./browser-state";
// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useState } from "react";
import {
  Copy,
  KeyRound,
  Plus,
  Square,
  RotateCcw,
  FileText,
  X,
  Download,
  Trash2,
} from "lucide-react";
import { Button } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import { Progress } from "@/onecat/ui";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/onecat/ui";
import { DownloadProgressBar } from "@/onecat/download-progress";
import {
  useQuery,
  copyText,
  mutation,
  api,
  type Engine,
  type Job,
  type GPU,
  type RequestHistory as History,
} from "./api";
import {
  Page,
  Field,
  Action,
  Empty,
  ErrorNotice,
  Modal,
  format,
  useText,
  jobLabel,
  saveFile,
} from "./common";

export function ServicePage() {
  const t = useText();
  const { data: engine, error } = useQuery<Engine>(
    "/api/inference/status",
    2000,
  );
  const { data: jobs } = useQuery<{ items: Job[] }>("/api/jobs", 2000);
  const { data: keys } = useQuery<{
    items: { id: string; name: string; prefix: string; created: number }[];
  }>("/api/keys");
  const [days, setDays] = useBrowserState("onecat:service-days", 1);
  const [model, setModel] = useBrowserState("onecat:service-model", "");
  const [source, setSource] = useBrowserState("onecat:service-source", "");
  const [tab, setTab] = useBrowserState("onecat:service-tab", "requests");
  const { data: requests } = useQuery<History>(`/api/requests?days=${days}&model=${encodeURIComponent(model)}&source=${source}`, 2000);
  const { data: devices } = useQuery<{ gpus: GPU[] }>("/api/gpu", 3000);
  const [newKey, setNewKey] = useState(false),
    [keyName, setKeyName] = useState(""),
    [secret, setSecret] = useState(""),
    [logName, setLogName] = useState<string | null>(null);
  const log = useQuery<{ text: string }>(
    logName ? "/api/logs/" + logName : null,
    2000,
  );
  const endpoint = window.location.origin + "/v1";
  return (
    <Page
      title={t("服务", "Service")}
      description={t(
        "实例状态、API 调用、后台任务与运行日志。",
        "Engine status, API traffic, background tasks, and runtime logs.",
      )}
    >
      <ErrorNotice error={error} />
      <div className="oc-panel">
        <div className="oc-row">
          <div>
            <span
              className={
                engine?.state === "ready" ? "oc-status-good" : "oc-muted"
              }
            >
              {engine?.state === "ready"
                ? t("模型就绪", "Ready")
                : jobLabel(engine?.state || "", t) || "—"}
            </span>
            <h2>
              {engine?.profile
                ? modelLabel(engine.profile)
                : t("没有活动模型", "No active model")}
            </h2>
            <p className="oc-muted">
              {engine?.profile
                ? engine.profile.served_model_name +
                  " · TP" +
                  engine.profile.tensor_parallel_size
                : "1Cat-vLLM"}
            </p>
          </div>
          <div className="oc-actions">
            {engine?.actions?.retry && engine.profile_id && <Action run={() => mutation("/api/inference/load", { profile_id: engine.profile_id })}><RotateCcw />{t("重试启动", "Retry startup")}</Action>}
            <Button variant="outline" onClick={() => setLogName("engine")}>
              <FileText />
              {t("日志", "Logs")}
            </Button>
            <Action
              variant="outline"
              disabled={!engine?.actions?.stop}
              run={() => mutation("/api/inference/unload", {})}
            >
              <Square />
              {t("停止模型", "Stop model")}
            </Action>
          </div>
        </div>
        {engine?.maintenance && (
          <p className="oc-status-warn">
            {t(
              "维护任务进行中，暂不接收新请求。",
              "Maintenance is in progress; new requests are paused.",
            )}
          </p>
        )}
        {engine?.error && <div className="oc-service-failure" role="status">
          <p>{t("模型未能启动。查看错误详情与加载日志后重试，或在模型库调整预设。", "The model could not start. Review the error and startup log, then retry or adjust its profile in the model library.")}</p>
          <details><summary>{t("错误详情", "Error details")}</summary><pre className="oc-log">{engine.error}</pre></details>
        </div>}
        <ErrorNotice error={engine?.hardware_recovery?.error} />
        {engine?.state === "loading" && <LaunchProgress />}
        {engine?.state === "ready" && <details className="oc-launch-history"><summary>{t("本次启动记录 · 已就绪", "Startup history · Ready")}</summary><LaunchProgress /></details>}
      </div>
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="requests">
            {t("请求统计", "Requests")}
          </TabsTrigger>
          <TabsTrigger value="api">API</TabsTrigger>
          <TabsTrigger value="tasks">
            {t("后台任务", "Tasks")}{" "}
            {jobs?.items.filter(
              (j) => !["completed", "failed", "cancelled"].includes(j.state),
            ).length || ""}
          </TabsTrigger>
          <TabsTrigger value="devices">{t("设备", "Devices")}</TabsTrigger>
        </TabsList>
        <TabsContent value="requests">
          <TokenUsage days={days} onDaysChange={setDays} model={model} onModelChange={setModel} source={source} onSourceChange={setSource} />
          <p className="oc-muted oc-history-scope">{t(`下方趋势和明细使用相同筛选 · 近 ${days} 天 · 明细最多 200 条`, `Trends and details use the same filters · Last ${days} days · Up to 200 detail rows`)}</p>
          <RequestHistory data={requests?.scope?.days === days && requests?.scope?.model === model && requests?.scope?.source === source ? requests : undefined} />
        </TabsContent>
        <TabsContent value="api">
          <div className="oc-panel">
            <div className="oc-row">
              <h2>{t("接入地址", "API endpoint")}</h2>
              <Button variant="ghost" onClick={() => copyText(endpoint)}>
                <Copy />
                {t("复制", "Copy")}
              </Button>
            </div>
            <code className="oc-endpoint">{endpoint}</code>
            <details>
              <summary>{t("调用示例", "Example request")}</summary>
              <pre className="oc-log">
                {"curl " +
                  endpoint +
                  "/chat/completions \\\n  -H 'Authorization: Bearer YOUR_API_KEY' \\\n  -H 'Content-Type: application/json' \\\n  -d '" +
                  JSON.stringify({
                    model: engine?.profile?.served_model_name || "YOUR_MODEL",
                    messages: [{ role: "user", content: "Hello" }],
                    stream: true,
                  }) +
                  "'"}
              </pre>
            </details>
          </div>
          <div className="oc-panel">
            <div className="oc-row">
              <h2>API Keys</h2>
              <Button variant="outline" onClick={() => setNewKey(true)}>
                <Plus />
                {t("创建密钥", "Create key")}
              </Button>
            </div>
            {keys?.items.length ? (
              keys.items.map((k) => (
                <div className="oc-list-row" key={k.id}>
                  <div>
                    <strong>{k.name}</strong>
                    <p className="oc-muted">
                      {k.prefix}… · {t("仅推理权限", "Inference only")}
                    </p>
                  </div>
                  <Action
                    variant="ghost"
                    run={() =>
                      mutation("/api/keys/" + k.id, undefined, "DELETE")
                    }
                  >
                    <Trash2 />
                    {t("撤销", "Revoke")}
                  </Action>
                </div>
              ))
            ) : (
              <p className="oc-muted oc-spaced">
                {t(
                  "为需要调用模型的应用创建独立密钥。",
                  "Create a separate key for each application using your model.",
                )}
              </p>
            )}
          </div>
        </TabsContent>
        <TabsContent value="tasks">
          {!jobs?.items.length ? (
            <Empty title={t("没有后台任务", "No background tasks")} />
          ) : (
            jobs.items.map((j) => {
              const finished = ["completed", "failed", "cancelled"].includes(
                j.state,
              );
              return (
                <div className="oc-panel" key={j.id}>
                  <div className="oc-row">
                    <div>
                      <h2>{jobLabel(j.stage, t)}</h2>
                      <p className="oc-muted">
                        {jobLabel(j.kind, t)} · {jobLabel(j.state, t)}
                        {j.cancel_requested && !finished
                          ? " · " + t("正在取消", "Cancelling")
                          : ""}
                      </p>
                    </div>
                    <div className="oc-actions">
                      <Button
                        variant="ghost"
                        onClick={() => setLogName("job-" + j.id)}
                      >
                        <FileText />
                        {t("日志", "Log")}
                      </Button>
                      {!finished ? (
                        <Action
                          variant="outline"
                          run={() => mutation("/api/jobs/" + j.id + "/cancel")}
                        >
                          <X />
                          {t("取消", "Cancel")}
                        </Action>
                      ) : j.state !== "completed" ? (
                        <Action
                          variant="outline"
                          run={() => mutation("/api/jobs/" + j.id + "/retry")}
                        >
                          <RotateCcw />
                          {t("重试", "Retry")}
                        </Action>
                      ) : j.result?.download_path ? (
                        <Button variant="outline" asChild>
                          <a href={"/api/jobs/" + j.id + "/download"}>
                            <Download />
                            {t("下载运行包", "Download archive")}
                          </a>
                        </Button>
                      ) : null}
                    </div>
                  </div>
                  <div className="oc-spaced">
                    {j.expected_bytes ? (
                      <DownloadProgressBar
                        progress={{
                          expectedBytes: j.expected_bytes,
                          downloadedBytes: j.downloaded_bytes || 0,
                          fraction:
                            j.state === "completed"
                              ? 1
                              : (j.downloaded_bytes || 0) / j.expected_bytes,
                        }}
                        bytesPerSec={j.bytes_per_second || 0}
                        cancelling={!!j.cancel_requested && !finished}
                      />
                    ) : j.kind === "start_model" ? (
                      <span className="oc-muted">
                        {t("查看上方启动阶段", "See startup stages above")}
                      </span>
                    ) : (
                      <Progress value={j.progress} />
                    )}
                  </div>
                  <ErrorNotice error={j.error} />
                </div>
              );
            })
          )}
        </TabsContent>
        <TabsContent value="devices">
          <div className="oc-model-grid">
            {devices?.gpus.map((d) => (
              <article className="oc-panel" key={d.uuid}>
                <div className="oc-row">
                  <h2>GPU {d.index}</h2>
                  <span className="oc-muted">
                    {format(d.temperature_c, 0)} °C
                  </span>
                </div>
                <p>{d.name}</p>
                <div className="oc-metric-pair">
                  <div>
                    <span>{t("实际功率", "Board power")}</span>
                    <strong>
                      {format(d.power_w, 0)} <small>W</small>
                    </strong>
                  </div>
                  <div>
                    <span>{t("功率上限", "Power limit")}</span>
                    <strong>
                      {format(d.power_limit_w, 0)} <small>W</small>
                    </strong>
                  </div>
                </div>
                <p className="oc-muted">
                  {t("显存", "VRAM")}{" "}
                  {format(
                    d.memory_used_mib == null ? null : d.memory_used_mib / 1024,
                  )}{" "}
                  /{" "}
                  {format(
                    d.memory_total_mib == null
                      ? null
                      : d.memory_total_mib / 1024,
                  )}{" "}
                  GB
                </p>
                <Progress
                  value={
                    d.memory_used_mib != null && d.memory_total_mib
                      ? (100 * d.memory_used_mib) / d.memory_total_mib
                      : 0
                  }
                />
                <p className="oc-muted oc-spaced">
                  {t("核心", "Core")} {d.graphics_clock_mhz ?? "—"} MHz · HBM{" "}
                  {d.memory_clock_mhz ?? "—"} MHz
                </p>
                {d.processes.map((p) => (
                  <p className="oc-muted" key={p.pid}>
                    PID {p.pid} · {p.name}
                  </p>
                ))}
              </article>
            ))}
          </div>
          {!devices?.gpus.length && (
            <Empty title={t("未检测到 NVIDIA GPU", "No NVIDIA GPU detected")} />
          )}
        </TabsContent>
      </Tabs>
      <Modal
        open={newKey}
        onOpenChange={setNewKey}
        title={t("创建推理密钥", "Create inference key")}
      >
        <Field label={t("用途名称", "Application name")}>
          <Input value={keyName} onChange={(e) => setKeyName(e.target.value)} />
        </Field>
        <Action
          disabled={!keyName.trim()}
          run={async () => {
            const r = await mutation<{ key: string }>("/api/keys", {
              name: keyName,
            });
            setSecret(r.key);
            setNewKey(false);
            setKeyName("");
          }}
        >
          <KeyRound />
          {t("创建", "Create")}
        </Action>
      </Modal>
      <Modal
        open={!!secret}
        onOpenChange={(open) => {
          if (!open) setSecret("");
        }}
        title={t("保存你的密钥", "Save your API key")}
        description={t(
          "完整密钥只显示这一次。",
          "The full key is shown only once.",
        )}
      >
        <code className="oc-secret">{secret}</code>
        <Button onClick={() => copyText(secret)}>
          <Copy />
          {t("复制密钥", "Copy key")}
        </Button>
      </Modal>
      <Modal
        open={!!logName}
        onOpenChange={(open) => {
          if (!open) setLogName(null);
        }}
        title={t("运行日志", "Runtime log")}
      >
        <Button
          variant="outline"
          disabled={!log.data?.text}
          onClick={() =>
            saveFile((logName || "engine") + ".log", log.data?.text || "")
          }
        >
          <Download />
          {t("导出日志", "Export log")}
        </Button>
        <ErrorNotice error={log.error} />
        <pre className="oc-log">
          {log.data?.text || t("暂时没有日志。", "No log output yet.")}
        </pre>
      </Modal>
    </Page>
  );
}
