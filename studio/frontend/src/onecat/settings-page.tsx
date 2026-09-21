import { useInterfaceMotion } from "./motion";
// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import {
  Download,
  Save,
  PackageOpen,
  FileDown,
  ExternalLink,
} from "lucide-react";
import { Button } from "@/onecat/ui";
import { Input } from "@/onecat/ui";
import { Switch } from "@/onecat/ui";
import { useBrowserState } from "./browser-state";
import { GPUControls } from "./gpu-controls";
import { modelLabel } from "./model-label";
import { setLocale } from "@/onecat/locale";
import { toast } from "sonner";
import {
  useQuery,
  mutation,
  type Settings,
  type Profile,
  type Runtime,
  type GPU,
} from "./api";
import {
  Page,
  Field,
  NumberField,
  Action,
  ErrorNotice,
  Modal,
  useText,
  saveFile,
} from "./common";

export function SettingsPage() {
  const t = useText();
  const uiMotion = useInterfaceMotion();
  const { data, error } = useQuery<Settings>("/api/settings");
  const { data: profiles } = useQuery<{ items: Profile[] }>("/api/profiles");
  const { data: runtimes } = useQuery<{ items: Runtime[] }>("/api/runtimes");
  const { data: cap } = useQuery<{
    version: string;
    gpu_control: boolean;
    upstream_commit: string;
  }>("/api/studio/capabilities");
  type Draft = { changes: Partial<Settings> };
  const [draft, setDraft] = useBrowserState<Draft | Settings | undefined>("onecat:settings-draft", undefined);
  const [saved, setSaved] = useState<Settings>();
  const baseline = saved || data;
  // Older versions stored even pristine settings as a full snapshot. Convert
  // that format once, preserving its differences, then store only edits.
  function changesOf(value: typeof draft): Partial<Settings> {
    if (!value) return {};
    if ("changes" in value) return value.changes;
    return Object.fromEntries(Object.entries(value).filter(([key, value]) =>
      key !== "modelscope_token_set" && value !== baseline?.[key as keyof Settings]));
  }
  const changes = changesOf(draft);
  const settings = baseline ? { ...baseline, ...changes } : undefined;
  const [saving, setSaving] = useState(false);
  const [modelscopeToken, setModelscopeToken] = useState(""),
    [backup, setBackup] = useState(""),
    [restore, setRestore] = useState(""),
    [passwordOpen, setPasswordOpen] = useState(false),
    [oldPassword, setOldPassword] = useState(""),
    [newPassword, setNewPassword] = useState("");
  useEffect(() => {
    setSaved(undefined);
    if (data && draft && !("changes" in draft)) setDraft({ changes: changesOf(draft) });
  }, [data]);
  function change<K extends keyof Settings>(key: K, value: Settings[K]) {
    setDraft(previous => {
      const next = { ...changesOf(previous), [key]: value };
      if (value === baseline?.[key]) delete next[key];
      return { changes: next };
    });
  }
  const dirty = Object.keys(changes).some(key => changes[key as keyof Settings] !== baseline?.[key as keyof Settings]);
  const invalid = settings && (
    !Number.isInteger(settings.port) || settings.port < 1024 || settings.port > 65535
      ? t("监听端口必须为 1024–65535 的整数。", "Port must be an integer between 1024 and 65535.")
      : !Number.isInteger(settings.idle_unload_minutes) || settings.idle_unload_minutes < 0 || settings.idle_unload_minutes > 1440
        ? t("空闲卸载时间必须为 0–1440 的整数分钟。", "Idle unload must be an integer between 0 and 1440 minutes.")
        : !settings.model_directory.trim() ? t("请填写模型存储目录。", "Enter a model directory.") : undefined
  );
  async function saveSettings() {
    if (!settings || invalid) return;
    const submitted = { ...changes };
    setSaving(true);
    try {
      const { restart_required, ...values } = await mutation<Settings & { restart_required: boolean }>("/api/settings", submitted, "PUT");
      setSaved(values);
      setDraft(previous => {
        const next = { ...changesOf(previous) };
        for (const key of Object.keys(submitted) as (keyof Settings)[])
          if (next[key] === submitted[key]) delete next[key];
        return { changes: next };
      });
      localStorage.setItem("onecat_theme", values.theme);
      applyTheme(values.theme);
      await setLocale(values.locale);
      toast.success(restart_required
        ? t("设置已保存；监听地址或端口将在重启 Studio 后生效。", "Saved. Listener changes take effect after restarting Studio.")
        : t("设置已保存", "Settings saved"));
    } finally { setSaving(false); }
  }
  return (
    <Page
      title={t("设置", "Settings")}
      description={t(
        "运行环境、访问方式与数据维护。",
        "Runtime environments, access settings, and maintenance.",
      )}

    >
      <nav className="oc-settings-nav" aria-label={t("设置分类", "Settings sections")}>
        {[
          ["appearance", t("外观", "Appearance")], ["models", t("模型与环境", "Models and runtimes")],
          ["policy", t("服务策略", "Service policy")], ["data", t("数据维护", "Data")],
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
                  onChange={(e) =>
                    { change("locale", e.target.value as Settings["locale"]); void setLocale(e.target.value as Settings["locale"]); }
                  }
                >
                  <option value="zh-CN">简体中文</option>
                  <option value="en">English</option>
                </select>
              </Field>
              <Field label={t("主题", "Appearance")}>
                <select
                  value={settings.theme}
                  onChange={(e) => {
                    change("theme", e.target.value as Settings["theme"]);
                    applyTheme(e.target.value);
                  }}
                >
                  <option value="system">{t("跟随系统", "System")}</option>
                  <option value="light">{t("浅色", "Light")}</option>
                  <option value="dark">{t("深色", "Dark")}</option>
                </select>
              </Field>
</div>
            <label className="oc-toggle-row"><Switch checked={uiMotion.preference} onCheckedChange={uiMotion.setPreference} />
              {t("界面动效", "Interface motion")}
            </label>
            <label className="oc-toggle-row"><Switch defaultChecked={localStorage.getItem("onecat_stream_animation") !== "off"}
              onCheckedChange={value => { localStorage.setItem("onecat_stream_animation", value ? "on" : "off"); window.dispatchEvent(new Event("onecat:animation")); }} />
              {t("柔和流式动画", "Soft streaming animation")}
            </label>
            <p className="oc-muted">{t("外观即时预览；保存后应用到下次打开。动画会遵循系统减少动态效果偏好。", "Preview appearance immediately; save for future visits. Animation respects reduced-motion preferences.")}</p>
          </div>
          <div className="oc-panel" id="settings-models">
            <div className="oc-row oc-spaced"><p className="oc-muted">{t("安装、导入和切换运行环境统一在环境管理中完成。", "Install, import and select runtimes in runtime management.")}</p><Button variant="outline" asChild><Link to="/setup">{t("管理运行环境", "Manage runtimes")}</Link></Button></div>
            <h2>{t("模型与下载", "Models and downloads")}</h2>
            <div className="oc-form-grid">
              <Field label={t("模型存储目录", "Model directory")}>
                <Input
                  value={settings.model_directory}
                  onChange={(e) => change("model_directory", e.target.value)}
                />
              </Field>
              <Field label={t("ModelScope 下载源", "ModelScope endpoint")}>
                <Input
                  value={settings.modelscope_endpoint}
                  onChange={(e) =>
                    change("modelscope_endpoint", e.target.value)
                  }
                />
              </Field>
            </div>
            <Field
              label="ModelScope token"
              hint={
                data?.modelscope_token_set
                  ? t(
                      "已配置。留空不会更改现有 token。",
                      "Configured. An empty field does not change the existing token.",
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
            "升级时导入新环境，再修改启动预设的运行环境。旧环境保留，可随时切回。",
            "Import a new runtime and select it in your launch profile. Previous runtimes remain available for rollback.",
          )}
        </p>
      </div>
          <div className="oc-panel" id="settings-policy">
            <h2>{t("服务与资源策略", "Service and resource policy")}</h2>
            <div className="oc-form-grid">
              <Field
                label={t("启动 Studio 时自动加载", "Load when Studio starts")}
              >
                <select
                  value={settings.autostart_profile || ""}
                  onChange={(e) =>
                    change("autostart_profile", e.target.value || null)
                  }
                >
                  <option value="">
                    {t("不自动加载模型", "Do not load a model automatically")}
                  </option>
                  {profiles?.items.map((p) => (
                    <option value={p.id} key={p.id}>
                      {modelLabel(p)}
                    </option>
                  ))}
                </select>
              </Field>
              <NumberField
                label={t(
                  "空闲卸载分钟数（0 为关闭）",
                  "Idle unload minutes (0 = disabled)",
                )}
                value={settings.idle_unload_minutes}
                onChange={(v) => change("idle_unload_minutes", v)}
                min={0}
                max={1440}
              />
              <NumberField
                label={t("Studio 监听端口", "Studio port")}
                value={settings.port}
                onChange={(v) => change("port", v)}
                min={1024}
                max={65535}
              />
              <Field
                label={t("局域网访问", "LAN access")}
                hint={t(
                  "修改后重启生效，访问仍需登录或 API Key。",
                  "Takes effect after restart. Login or an API key is still required.",
                )}
              >
                <Switch
                  checked={settings.host === "0.0.0.0"}
                  onCheckedChange={(v) =>
                    change("host", v ? "0.0.0.0" : "127.0.0.1")
                  }
                />
              </Field>
            </div>
            <Button variant="outline" onClick={() => setPasswordOpen(true)}>
              {t("修改登录密码", "Change password")}
            </Button>
          </div>
        </>
      )}
      <GPUControls />
      <div className="oc-panel" id="settings-data">
        <h2>{t("备份与诊断", "Backup and diagnostics")}</h2>
        <p className="oc-muted">
          {t(
            "备份包含会话、配置与访问凭据数据库，不包含模型权重。",
            "Backups contain conversations, configuration, and credential databases, but not model weights.",
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
              "恢复会替换当前配置与会话，并要求重新登录。请先停止模型和后台任务。",
              "Restore replaces current settings and conversations and requires a new login. Stop the model and background tasks first.",
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
            "采用 1Cat 社区源码许可证；硬件预装销售与换皮销售须另行授权。",
            "Licensed under the 1Cat Community Source License; hardware-bundled and rebranded sales require permission.",
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
      {settings && <div className="oc-settings-savebar">
        <span className={invalid ? "oc-status-warn" : "oc-muted"} role="status">{invalid || (dirty ? t("有未保存的更改 · 草稿已暂存", "Unsaved changes · Draft kept") : t("所有设置已保存", "All settings saved"))}</span>
        <div className="oc-actions">
        {dirty && <Button variant="ghost" disabled={saving} onClick={() => {
          setDraft({ changes: {} });
          if (baseline) { applyTheme(baseline.theme); void setLocale(baseline.locale); }
        }}>{t("撤销更改", "Discard changes")}</Button>}
        <span className={dirty ? "oc-save-pending" : ""}><Action run={saveSettings} disabled={!dirty || saving || !!invalid}>
          <Save />{t("保存设置", "Save settings")}
        </Action></span>
        </div>
      </div>}
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
