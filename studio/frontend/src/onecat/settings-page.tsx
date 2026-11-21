// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useEffect, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import {
  Download,
  PackageOpen,
  FileDown,
  ExternalLink,
} from "lucide-react";
import { Button } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import { Switch } from "@/onecat/ui";
import { GPUControls } from "./gpu-controls";
import { LogAnalysisSettings } from "./log-analysis";
import { StudioUpdates } from "./studio-updates";
import { setLocale } from "@/onecat/locale";
import { toast } from "sonner";
import {
  useQuery,
  mutation,
  type Settings,
  type Runtime,
  type GPU,
} from "./api";
import {
  Page,
  Field,
  Action,
  ErrorNotice,
  Modal,
  useText,
  saveFile,
} from "./common";

// Order partial writes even when the settings page is reopened during a save.
let settingsWrites = Promise.resolve();

function SettingInput({ value, onCommit, type = "text", min, max, ...label }: {
  id?: string;
  "aria-labelledby"?: string;
  value: string | number;
  onCommit: (raw: string) => void;
  type?: "text" | "url" | "number";
  min?: number;
  max?: number;
}) {
  const [draft, setDraft] = useState<string>();
  return <Input {...label} type={type} required min={min} max={max}
    value={draft ?? String(value)}
    onChange={e => setDraft(e.target.value)}
    onKeyDown={e => {
      if (e.key === "Enter" && !e.nativeEvent.isComposing) e.currentTarget.blur();
    }}
    onBlur={e => {
      if (draft === undefined) return;
      const raw = draft.trim();
      if (!raw || !e.currentTarget.validity.valid) {
        toast.error(e.currentTarget.validationMessage || (document.documentElement.lang === "en"
          ? "Enter a value." : "请填写有效值。"));
      } else if (raw !== String(value)) {
        onCommit(raw);
      }
      setDraft(undefined);
    }} />;
}

export function SettingsPage() {
  const t = useText();
  const { data, error, refresh } = useQuery<Settings>("/api/settings");
  const { data: runtimes } = useQuery<{ items: Runtime[] }>("/api/runtimes");
  const { data: cap } = useQuery<{
    version: string;
    gpu_control: boolean;
    upstream_commit: string;
  }>("/api/studio/capabilities");
  const [saved, setSaved] = useState<Settings>();
  const [pending, setPending] = useState<Partial<Settings>>({});
  const versions = useRef<Partial<Record<keyof Settings, number>>>({});
  const baseline = saved ?? data;
  const settings = baseline && { ...baseline, ...pending };
  const [modelscopeToken, setModelscopeToken] = useState(""),
    [backup, setBackup] = useState(""),
    [restore, setRestore] = useState(""),
    [passwordOpen, setPasswordOpen] = useState(false),
    [oldPassword, setOldPassword] = useState(""),
    [newPassword, setNewPassword] = useState("");
  useEffect(() => { setSaved(undefined); }, [data]);
  useEffect(() => {
    let mounted = true;
    void settingsWrites.then(() => { if (mounted) refresh(); });
    return () => { mounted = false; };
  }, [refresh]);

  function change<K extends keyof Settings>(key: K, value: Settings[K]) {
    const version = (versions.current[key] ?? 0) + 1;
    versions.current[key] = version;
    setPending(current => ({ ...current, [key]: value }));
    settingsWrites = settingsWrites.then(async () => {
      try {
        const { restart_required, ...values } = await mutation<Settings & { restart_required: boolean }>(
          "/api/settings", { [key]: value }, "PUT",
        );
        setSaved(values);
        if (key === "theme") {
          applyTheme(values.theme);
          localStorage.setItem("onecat_theme", values.theme);
        }
        if (key === "locale") await setLocale(values.locale);
        if (restart_required) toast.info(t("重启 Studio 后应用新的监听地址或端口。", "Restart Studio to apply the new address or port."));
      } catch (error) {
        toast.error(t("设置未保存", "Setting could not be saved") + " · " + String(error instanceof Error ? error.message : error));
        refresh();
      } finally {
        setPending(current => {
          if (versions.current[key] !== version) return current;
          const next = { ...current };
          delete next[key];
          return next;
        });
      }
    });
  }
  return (
    <Page
      title={t("设置", "Settings")}

    >
      <nav className="oc-settings-nav" aria-label={t("设置分类", "Settings sections")}>
        {[
          ["appearance", t("外观", "Appearance")], ["models", t("模型与环境", "Models and runtimes")],
          ["log-analysis", t("日志分析", "Log analysis")],
          ["policy", t("服务策略", "Service policy")], ["data", t("数据维护", "Data")],
          ["updates", t("Studio 更新", "Studio updates")],
        ].map(([id, label]) => <a key={id} href={"#settings-" + id}>{label}</a>)}
      </nav>
      <ErrorNotice error={error} />
      {settings && (
        <>
          <div className="oc-panel" id="settings-appearance">
            <h2>{t("外观与交互", "Appearance and interaction")}</h2>
            <div className="oc-form-grid">              <Field label={t("界面语言", "Language")}>
                <select
                  value={settings.locale}
                  onChange={e => change("locale", e.target.value as Settings["locale"])}
                >
                  <option value="zh-CN">简体中文</option>
                  <option value="en">English</option>
                </select>
              </Field>
              <Field label={t("主题", "Appearance")}>
                <select
                  value={settings.theme}
                  onChange={e => change("theme", e.target.value as Settings["theme"])}
                >
                  <option value="system">{t("跟随系统", "System")}</option>
                  <option value="light">{t("浅色", "Light")}</option>
                  <option value="dark">{t("深色", "Dark")}</option>
                </select>
              </Field>
</div>
          </div>
          <div className="oc-panel" id="settings-models">
            <div className="oc-row oc-spaced">
              <h2>{t("模型与下载", "Models and downloads")}</h2>
              <Button variant="outline" asChild><Link to="/setup">{t("管理运行环境", "Manage runtimes")}</Link></Button>
            </div>
            <div className="oc-form-grid">
              <Field label={t("模型存储目录", "Model directory")}>
                <SettingInput value={settings.model_directory}
                  onCommit={raw => change("model_directory", raw)} />
              </Field>
              <Field label={t("ModelScope 下载源", "ModelScope endpoint")}>
                <SettingInput type="url" value={settings.modelscope_endpoint}
                  onCommit={raw => change("modelscope_endpoint", raw)} />
              </Field>
            </div>
            <Field
              label="ModelScope token"
              hint={
                data?.modelscope_token_set
                  ? t(
                      "已保存 token。填写新 token 可替换。",
                      "A token is saved. Enter a new one to replace it.",
                    )
                  : t("访问受限模型时需要。", "Required for gated models.")
              }
            >
              <div className="oc-row">
                <Input
                  type="password"
                  value={modelscopeToken}
                  onChange={(e) => setModelscopeToken(e.target.value)}
                  autoComplete="off"
                />
                <Action
                  disabled={!modelscopeToken}
                  variant="outline"
                  run={async () => {
                    await mutation(
                      "/api/settings/modelscope-token",
                      { token: modelscopeToken },
                      "PUT",
                    );
                    setModelscopeToken("");
                  }}
                >
                  {t("保存 token", "Save token")}
                </Action>
              </div>
            </Field>
          </div>
          <LogAnalysisSettings />
      <div className="oc-panel">
        <div className="oc-row">
          <h2>{t("运行包版本", "Runtime versions")}</h2>
          <Button variant="outline" asChild>
            <Link to="/setup">
              <PackageOpen />
              {t("安装或导入", "Install or import")}
            </Link>
          </Button>
        </div>
        {runtimes?.items.map((r) => (
          <div className="oc-list-row" key={r.id}>
            <div>
              <strong>{r.name}</strong>
              <p className="oc-muted">
                vLLM {r.capabilities?.vllm_version} · Torch{" "}
                {r.capabilities?.torch_version}
              </p>
            </div>
            {r.managed && (
              <Action
                variant="outline"
                run={() => mutation("/api/runtimes/" + r.id + "/export")}
                success={t(
                  "正在打包，完成后从服务页下载",
                  "Packing archive. Download it from Service when ready.",
                )}
              >
                <Download />
                {t("导出离线运行包", "Export offline runtime")}
              </Action>
            )}
          </div>
        ))}
        <p className="oc-muted oc-spaced">
          {t(
            "导入新环境后，在模型启动预设中选择要使用的版本。",
            "Import a new runtime, then select the version to use in your launch profile.",
          )}
        </p>
      </div>
          <div className="oc-panel" id="settings-policy">
            <h2>{t("服务与资源策略", "Service and resource policy")}</h2>
            <div className="oc-form-grid">
              <label className="oc-toggle-row">
                <Switch checked={settings.restore_model_on_boot}
                  onCheckedChange={(value) => change("restore_model_on_boot", value)} />
                {t("开机自动加载上次使用的模型", "Restore the last model on boot")}
              </label>
              <Field label={t(
                  "空闲卸载分钟数（0 为关闭）",
                  "Idle unload minutes (0 = disabled)",
                )}>
                <SettingInput type="number" value={settings.idle_unload_minutes} min={0} max={1440}
                  onCommit={raw => change("idle_unload_minutes", Number(raw))} />
              </Field>
              <Field label={t("Studio 监听端口", "Studio port")}>
                <SettingInput type="number" value={settings.port} min={1024} max={65535}
                  onCommit={raw => change("port", Number(raw))} />
              </Field>
            </div>
            <Button variant="outline" onClick={() => setPasswordOpen(true)}>
              {t("修改登录密码", "Change password")}
            </Button>
          </div>
        </>
      )}
      <GPUControls showWhen="incomplete" />
      <StudioUpdates saving={Object.keys(pending).length > 0} />
      <div className="oc-panel" id="settings-data">
        <h2>{t("备份与诊断", "Backup and diagnostics")}</h2>
        <p className="oc-muted">
          {t(
            "备份会保存会话、配置和访问凭据。模型权重需单独备份。",
            "Backups include conversations, settings and credentials. Back up model weights separately.",
          )}
        </p>
        <div className="oc-actions oc-spaced">
          <Action
            variant="outline"
            run={async () =>
              setBackup(
                (await mutation<{ filename: string }>("/api/backup")).filename,
              )
            }
          >
            <FileDown />
            {t("创建备份", "Create backup")}
          </Action>
          {backup && (
            <Button asChild>
              <a href={"/api/backup/" + backup}>
                {t("下载备份", "Download backup")}
              </a>
            </Button>
          )}
          <Action
            variant="outline"
            run={async () => {
              const system = await fetch("/api/system").then((r) => r.json());
              saveFile("onecat-diagnostics.json", {
                version: cap?.version,
                system,
                runtimes: runtimes?.items,
                generated_at: new Date().toISOString(),
              });
            }}
          >
            {t("导出诊断信息", "Export diagnostics")}
          </Action>
        </div>
        <details>
          <summary>{t("恢复备份", "Restore backup")}</summary>
          <Field label={t("服务器上的备份路径", "Backup path on the server")}>
            <Input
              value={restore}
              onChange={(e) => setRestore(e.target.value)}
              placeholder="/path/to/onecat-backup.tar.gz"
            />
          </Field>
          <p className="oc-muted">
            {t(
              "请先停止模型和后台任务。恢复会替换当前配置和会话，完成后请重新登录。",
              "Stop the model and background tasks first. Restoring replaces current settings and conversations. Sign in again when it finishes.",
            )}
          </p>
          <Action
            variant="destructive"
            disabled={!restore}
            run={async () => {
              await mutation("/api/backup/restore", { path: restore });
              window.location.reload();
            }}
          >
            {t("恢复此备份", "Restore this backup")}
          </Action>
        </details>
      </div>
      <div className="oc-panel">
        <h2>1Cat Studio {cap?.version || "0.1.0"}</h2>
        <p className="oc-muted">
          {t(
            "本项目采用 1Cat 社区源码许可证。硬件预装销售与换皮销售须另行授权。",
            "This project uses the 1Cat Community Source License. Hardware-bundled and rebranded sales require permission.",
          )}
        </p>
        <div className="oc-actions oc-spaced">
          <Button asChild variant="outline">
            <a href="/source.tar.gz">
              <FileDown />
              {t("下载对应源码", "Corresponding source")}
            </a>
          </Button>
          <Button asChild variant="ghost">
            <a
              href="/license.txt"
              target="_blank"
              rel="noreferrer"
            >
              <ExternalLink />
              {t("查看许可证", "View license")}
            </a>
          </Button>
        </div>
      </div>
      <Modal
        open={passwordOpen}
        onOpenChange={setPasswordOpen}
        title={t("修改登录密码", "Change password")}
      >
        <Field label={t("当前密码", "Current password")}>
          <Input
            type="password"
            value={oldPassword}
            onChange={(e) => setOldPassword(e.target.value)}
            autoComplete="current-password"
          />
        </Field>
        <Field
          label={t(
            "新密码（至少 8 个字符）",
            "New password (at least 8 characters)",
          )}
        >
          <Input
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            autoComplete="new-password"
          />
        </Field>
        <Action
          disabled={!oldPassword || newPassword.length < 8}
          run={async () => {
            await mutation("/api/auth/password", {
              current_password: oldPassword,
              new_password: newPassword,
            });
            setOldPassword("");
            setNewPassword("");
            window.location.reload();
          }}
        >
          {t("修改密码并重新登录", "Change password and sign in again")}
        </Action>
      </Modal>
    </Page>
  );
}
export function applyTheme(theme: string) {
  const dark =
    theme === "dark" ||
    (theme === "system" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
}
