// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useState } from "react";
import { RuntimeInstaller } from "./runtime-installer";
import { GPUControls } from "./gpu-controls";
import { Link } from "@tanstack/react-router";
import {
  Cpu,
  FolderInput,
  PackageOpen,
  Check,
  ArrowRight,
} from "lucide-react";
import { Button } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import { Textarea } from "@/onecat/ui";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/onecat/ui";
import { useQuery, mutation, type Runtime, type GPU, type Job } from "./api";
import { Page, Field, Action, ErrorNotice, bytes, useText } from "./common";

export function SetupPage() {
  const t = useText();
  const { data: system, error } = useQuery<{
    platform: string;
    disk_free_bytes: number;
    uv_available: boolean;
    gpu: { gpus: GPU[]; driver?: string; error?: string };
  }>("/api/system");
  const { data: runtimes } = useQuery<{ items: Runtime[] }>(
    "/api/runtimes",
    3000,
  );
  const { data: agent } = useQuery<{ installed: boolean; sandbox_ready: boolean; version: string; source: string }>("/api/agent/status");
  const [name, setName] = useState("1Cat-vLLM"),
    [python, setPython] = useState(""),
    [cwd, setCwd] = useState(""),
    [environment, setEnvironment] = useState("{}"),
    [archive, setArchive] = useState(""),
    [unit, setUnit] = useState("");
  return (
    <Page
      title={t("开始使用 1Cat Studio", "Set up 1Cat Studio")}
    >
      <ErrorNotice error={error} />
      <GPUControls showWhen="incomplete" />
      <div className="oc-steps">
        <span className="active">1 · {t("运行环境", "Runtime")}</span>
        <span>2 · {t("添加模型", "Add model")}</span>
        <span>3 · {t("启动与聊天", "Start & chat")}</span>
      </div>
      <Tabs defaultValue="install">
        <TabsList>
          <TabsTrigger value="install">{t("自动安装", "Install")}</TabsTrigger>
          <TabsTrigger value="import">
            {t("已有环境", "Existing runtime")}
          </TabsTrigger>
          <TabsTrigger value="offline">{t("离线导入", "Offline")}</TabsTrigger>
          <TabsTrigger value="adopt">
            {t("接管已有服务", "Existing service")}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="install"><RuntimeInstaller /></TabsContent>
        <TabsContent value="import">
          <div className="oc-panel">
            <h2>{t("导入已安装的 vLLM", "Import an existing vLLM runtime")}</h2>
            <div className="oc-form-grid">
              <Field label={t("环境名称", "Runtime name")}>
                <Input value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label={t("Python 可执行文件", "Python executable")}>
                <Input
                  value={python}
                  onChange={(e) => setPython(e.target.value)}
                  placeholder="/path/to/environment/bin/python"
                />
              </Field>
              <Field
                label={t("工作目录（可选）", "Working directory (optional)")}
              >
                <Input value={cwd} onChange={(e) => setCwd(e.target.value)} />
              </Field>
            </div>
            <details>
              <summary>
                {t("环境变量（JSON）", "Environment variables (JSON)")}
              </summary>
              <Textarea
                value={environment}
                onChange={(e) => setEnvironment(e.target.value)}
                className="oc-code"
                rows={5}
              />
            </details>
            <Action
              disabled={!python || !name}
              run={() =>
                mutation("/api/runtimes/import", {
                  name,
                  python_path: python,
                  working_directory: cwd || null,
                  environment: JSON.parse(environment),
                })
              }
              success={t("正在验证运行环境", "Validating runtime")}
            >
              <FolderInput />
              {t("验证并导入", "Validate & import")}
            </Action>
          </div>
        </TabsContent>
        <TabsContent value="offline">
          <div className="oc-panel">
            <h2>
              {t("导入完整离线运行包", "Import a complete offline runtime")}
            </h2>
            <Field
              label={t(
                "服务器上的运行包路径",
                "Runtime archive path on the server",
              )}
              hint={t(
                "使用 Studio 导出的 .onecat.tar.gz 运行包，包含 Python 与推理依赖。",
                "Use a Studio-exported .onecat.tar.gz archive containing Python and inference dependencies.",
              )}
            >
              <Input
                value={archive}
                onChange={(e) => setArchive(e.target.value)}
                placeholder="/path/to/runtime.onecat.tar.gz"
              />
            </Field>
            <Action
              disabled={!archive}
              run={() => mutation("/api/runtimes/offline", { path: archive })}
            >
              <PackageOpen />
              {t("导入并验证", "Import & validate")}
            </Action>
          </div>
        </TabsContent>
        <TabsContent value="adopt">
          <div className="oc-panel">
            <h2>
              {t("接管正在运行的 1Cat 服务", "Adopt a running 1Cat service")}
            </h2>
            <p className="oc-muted">
              {t(
                "读取当前服务的模型、环境和启动参数，避免重复加载。",
                "Read the service's model, environment, and launch arguments without loading a second instance.",
              )}
            </p>
            <Field
              label={t("systemd 用户服务名称", "systemd user service name")}
            >
              <Input
                value={unit}
                onChange={(e) => setUnit(e.target.value)}
                placeholder="1cat-vllm-….service"
              />
            </Field>
            <Action
              disabled={!unit}
              run={() => mutation("/api/inference/adopt", { unit })}
              success={t(
                "正在读取现有服务，请查看后台任务进度",
                "Inspecting service; follow progress in Tasks",
              )}
            >
              <FolderInput />
              {t("读取并接管", "Inspect & adopt")}
            </Action>
          </div>
        </TabsContent>
      </Tabs>
      {!!runtimes?.items.length && (
        <div className="oc-panel">
          <h2>{t("已验证的运行环境", "Validated runtimes")}</h2>
          {runtimes.items.map((r) => (
            <div className="oc-list-row" key={r.id}>
              <div>
                <strong>{r.name}</strong>
                <p className="oc-muted">{r.python_path}</p>
              </div>
              <span className="oc-status-good">
                <Check size={15} />
                {r.capabilities?.vllm_version}
              </span>
            </div>
          ))}
          <Button asChild>
            <Link to="/models">
              {t("继续添加模型", "Continue to models")}
              <ArrowRight />
            </Link>
          </Button>
        </div>
      )}
      <details className="oc-panel oc-setup-details"><summary>{t("设备详情与可选组件", "Device details and optional components")}</summary>
      <div className="oc-panel">
        <div className="oc-section-title">
          <Cpu size={19} />
          <h2>{t("这台机器", "This machine")}</h2>
        </div>
        <div className="oc-device-summary">
          {system?.gpu.gpus.length ? (
            system.gpu.gpus.map((d) => (
              <span key={d.uuid}>
                GPU {d.index} · {d.name} ·{" "}
                {d.memory_total_mib == null
                  ? "—"
                  : formatMemory(d.memory_total_mib)}
              </span>
            ))
          ) : (
            <p>
              {t(
                "未检测到 NVIDIA GPU。仍可配置环境和模型库。",
                "No NVIDIA GPU detected. You can still configure runtimes and models.",
              )}
            </p>
          )}
        </div>
        <p className="oc-muted">
          {system?.platform} · {t("可用磁盘", "Free disk")}{" "}
          {system ? bytes(system.disk_free_bytes) : "—"} · {t("驱动", "Driver")}{" "}
          {system?.gpu.driver || "—"}
        </p>
      </div>
      <GPUControls showWhen="ready" />
      <div className="oc-panel oc-agent-component">
        <div><h2>{t("Agent 引擎", "Agent engine")} · Codex {agent?.version || ""}</h2>
          <p className="oc-muted">{agent?.installed && agent.sandbox_ready
            ? t("已随 Studio 安装，项目沙箱已就绪。", "Included with Studio. The project sandbox is ready.")
            : t("Agent 组件或执行沙箱尚未就绪，请完成 Studio 安装配置。", "The Agent component or sandbox needs Studio installation setup.")}</p></div>
        <Button variant="outline" asChild><Link to="/agent">{t("打开 Agent", "Open Agent")}<ArrowRight /></Link></Button>
      </div>
      </details>
    </Page>
  );
}
function formatMemory(mib: number) {
  return (mib / 1024).toFixed(0) + " GB";
}
