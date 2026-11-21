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
import { RuntimeLogActions } from "./log-analysis";
import {
  useQuery,
  copyText,
  mutation,
  api,
  type Engine,
  type Job,
  type RequestHistory as History,
} from "./api";
import {
  Page,
  Field,
  Action,
  Empty,
  ErrorNotice,
  Modal,
  useText,
  jobLabel,
} from "./common";

const routineJobs = new Set(["start_model", "apply_gpu_settings"]);

export function ServicePage() {
  const t = useText();
  const { data: engine, error } = useQuery<Engine>(
    "/api/inference/status",
    2000,
  );
  const { data: jobs } = useQuery<{ items: Job[] }>("/api/jobs", 2000);
  const [showPastFailures, setShowPastFailures] = useState(false);
  const latestSuccess = new Map<string, number>();
  for (const job of jobs?.items || []) {
    if (routineJobs.has(job.kind) && job.state === "completed" && job.updated_at) {
      latestSuccess.set(job.kind, Math.max(latestSuccess.get(job.kind) || 0, job.updated_at));
    }
  }
  const pastFailures = (jobs?.items || []).filter(job =>
    job.state === "failed" && job.updated_at != null && job.updated_at < (latestSuccess.get(job.kind) || 0));
  const visibleJobs = (jobs?.items || []).filter(job =>
    !(routineJobs.has(job.kind) && job.state === "completed") &&
    (showPastFailures || !pastFailures.includes(job)));
  const { data: keys } = useQuery<{
    items: { id: string; name: string; prefix: string; created: number }[];
  }>("/api/keys");
  const [days, setDays] = useBrowserState("onecat:service-days", 1);
  const [model, setModel] = useBrowserState("onecat:service-model", "");
  const [source, setSource] = useBrowserState("onecat:service-source", "");
  const [tab, setTab] = useBrowserState("onecat:service-tab", "requests");
  const { data: requests } = useQuery<History>(`/api/requests?days=${days}&model=${encodeURIComponent(model)}&source=${source}`, 2000);
  const [newKey, setNewKey] = useState(false),
    [keyName, setKeyName] = useState(""),
    [secret, setSecret] = useState(""),
    [logName, setLogName] = useState<string | null>(null);
  const log = useQuery<{ text: string }>(
    logName ? "/api/logs/" + logName : null,
    2000,
  );
  const currentEndpoint = window.location.origin + "/v1";
  const lanEndpoints = engine?.lan_api_urls || [];
  const endpoint = lanEndpoints[0] || currentEndpoint;
  return (
    <Page
      title={t("服务", "Service")}
    >
      <ErrorNotice error={error} />
      <div className="oc-panel">
        <div className="oc-row">
          <div>
            <span
              className={
                !error && engine?.state === "ready" ? "oc-status-good" : "oc-muted"
              }
            >
              {error ? t("连接中断", "Disconnected") : engine?.state === "ready"
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
        {engine?.state === "ready" && engine.acceleration && (
          <div>
            <details className="oc-launch-history">
              <summary>{t("加速：已启用", "Acceleration enabled:")} {engine.acceleration.enabled}/{engine.acceleration.total}</summary>
              {Object.entries(engine.acceleration.paths).filter(([, row]) => !row.enabled).map(([name, row]) => (
                <p className="oc-status-warn" key={name}>{name}: {row.reason}</p>
              ))}
            </details>
            {engine.acceleration.compile_cache && (
              <p className={engine.acceleration.compile_cache.enabled ? undefined : "oc-status-warn"}>
                {t("启动编译缓存：", "Startup compilation cache: ")}
                {engine.acceleration.compile_cache.enabled ? t("已启用", "Enabled") : t("已关闭", "Disabled")}
                {engine.acceleration.compile_cache.reason && ` (${engine.acceleration.compile_cache.reason})`}
              </p>
            )}
          </div>
        )}
        {engine?.maintenance && (
          <p className="oc-status-warn">
            {t(
              "维护中，新请求已暂停。",
              "Maintenance in progress. New requests are paused.",
            )}
          </p>
        )}
        {engine?.error && <div className="oc-service-failure" role="status">
          <p>{t("模型启动失败。查看加载日志后重试，或在模型库调整预设。", "The model failed to start. Check the startup log before retrying, or adjust the profile in the model library.")}</p>
          <details><summary>{t("错误详情", "Error details")}</summary><pre className="oc-log">{engine.error}</pre></details>
        </div>}
        <ErrorNotice error={engine?.hardware_recovery?.error} />
        {engine?.state === "loading" && <LaunchProgress />}
        {engine?.state === "ready" && <details className="oc-launch-history"><summary>{t("本次启动记录 · 已就绪", "Startup history · Ready")}</summary><LaunchProgress /></details>}
      </div>
      <Tabs value={tab === "devices" ? "requests" : tab} onValueChange={setTab}>
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
        </TabsList>
        <TabsContent value="requests">
          <TokenUsage days={days} onDaysChange={setDays} model={model} onModelChange={setModel} source={source} onSourceChange={setSource} />
          <p className="oc-muted oc-history-scope">{t(`下方趋势和明细使用相同筛选 · 近 ${days} 天 · 明细最多 200 条`, `Trends and details use the same filters · Last ${days} days · Up to 200 detail rows`)}</p>
          <RequestHistory data={requests?.scope?.days === days && requests?.scope?.model === model && requests?.scope?.source === source ? requests : undefined} />
        </TabsContent>
        <TabsContent value="api">
          <div className="oc-panel">
            <div className="oc-row">
              <h2>{lanEndpoints.length ? t("局域网接入地址", "LAN API endpoint") : t("接入地址", "API endpoint")}</h2>
              <Button variant="ghost" onClick={() => copyText(endpoint)}>
                <Copy />
                {t("复制", "Copy")}
              </Button>
            </div>
            <code className="oc-endpoint">{endpoint}</code>
            {!lanEndpoints.length && engine && (
              <p className="oc-muted oc-spaced">
                {engine.listen_host === "127.0.0.1" || engine.listen_host === "::1"
                  ? t("当前启动参数仅允许本机访问。", "The service is bound to loopback.")
                  : t("当前监听地址没有可列出的局域网 IPv4 地址。", "No LAN IPv4 endpoint is available for this listener.")}
              </p>
            )}
            {lanEndpoints.slice(1).map((url) => (
              <code className="oc-endpoint" key={url}>{url}</code>
            ))}
            {endpoint !== currentEndpoint && (
              <p className="oc-muted oc-spaced">
                {t("当前页面地址：", "Current page address: ")}
                <code>{currentEndpoint}</code>
              </p>
            )}
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
                      {k.prefix}… · {t("推理权限", "Inference access")}
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
          {pastFailures.length > 0 && (
            <Button variant="ghost" aria-expanded={showPastFailures} onClick={() => setShowPastFailures(!showPastFailures)}>
              {showPastFailures ? t("收起历史失败记录", "Hide past failures") : t("查看历史失败记录", "Show past failures")} ({pastFailures.length})
            </Button>
          )}
          {!visibleJobs.length ? (
            <Empty title={t("当前没有待处理的后台任务", "No pending background tasks")} />
          ) : (
            visibleJobs.map((j) => {
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
                        {finished && j.updated_at && (
                          <> · <time dateTime={new Date(j.updated_at * 1000).toISOString()}>{new Date(j.updated_at * 1000).toLocaleString()}</time></>
                        )}
                        {j.cancel_requested && !finished
                          ? " · " + t("正在取消", "Cancelling")
                          : ""}
                      </p>
                    </div>
                    <div className="oc-actions">
                      <Button
                        variant="ghost"
                        onClick={() => setLogName(j.analysis_source ?? "job-" + j.id)}
                      >
                        <FileText />
                        {j.analysis_source ? t("原始日志", "Source log") : t("日志", "Log")}
                      </Button>
                      {!finished ? (
                        <Action
                          variant="outline"
                          run={() => mutation("/api/jobs/" + j.id + "/cancel")}
                        >
                          <X />
                          {t("取消", "Cancel")}
                        </Action>
                      ) : j.state !== "completed" && j.kind !== "analyze_log" ? (
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
                      <DownloadProgressBar job={j} />
                    ) : j.kind === "start_model" ? (!finished && (
                      <span className="oc-muted">
                        {t("查看上方启动阶段", "See startup stages above")}
                      </span>
                    )) : (
                      <Progress value={j.progress} />
                    )}
                  </div>
                  <ErrorNotice error={j.error} />
                </div>
              );
            })
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
        {logName && <RuntimeLogActions key={logName} source={logName} text={log.data?.text || ""} />}
        <ErrorNotice error={log.error} />
        <pre className="oc-log">
          {log.data?.text || t("暂时没有日志。", "No log output yet.")}
        </pre>
      </Modal>
    </Page>
  );
}
