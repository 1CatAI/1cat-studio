// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useState } from "react";
import { Download, RefreshCw } from "lucide-react";
import { Button } from "./ui";
import { api, mutation, useQuery } from "./api";
import { Action, bytes, ErrorNotice, Modal, useText } from "./common";

type Status = {
  stage?: string; version?: string; detail?: string; operation?: string;
  downloaded_bytes?: number; total_bytes?: number; bytes_per_second?: number;
};
type Release = {
  current: string; deployment: { mode: string }; latest: string | null;
  available: boolean; verified: boolean; release_id: string | null;
  notes: string; size: number | null; published: string | null;
  supported: boolean; error: string | null;
};
const terminal = new Set(["installed", "failed", "rolled_back"]);

export function StudioUpdates({ dirty }: { dirty: boolean }) {
  const t = useText();
  const query = useQuery<Release>("/api/updates");
  const progress = useQuery<{ current: string; status: Status; blocked_reason: string | null }>("/api/updates/progress", 2000);
  const [checked, setChecked] = useState<Release>();
  const [selected, setSelected] = useState<Release>();
  const release = checked || query.data;
  const available = release?.available && release.latest !== progress.data?.current;
  const status = progress.data?.status;
  const stage = status?.stage;
  const busy = !!stage && !terminal.has(stage);
  const labels: Record<string, string> = {
    queued: t("正在准备更新", "Preparing update"),
    downloading: t("正在下载", "Downloading"),
    preparing: t("正在校验安装包", "Verifying the release"),
    installing: t("正在安装", "Installing"),
    restarting: t("Studio 正在重启", "Restarting Studio"),
    rolling_back: t("正在恢复原版本", "Restoring the previous version"),
    installed: t("更新完成", "Update complete"),
    rolled_back: t("更新未完成，已恢复原版本", "Update failed; previous version restored"),
    failed: t("更新未完成，可以重试", "Update failed; you can retry"),
  };
  const blocked = dirty ? t("请先保存或撤销设置更改。", "Save or discard your settings changes first.") : progress.data?.blocked_reason;
  const total = status?.total_bytes || 0;
  const done = status?.downloaded_bytes || 0;
  return <div className="oc-panel" id="settings-updates">
    <h2>{t("Studio 更新", "Studio updates")}</h2>
    <p className="oc-muted">{t("当前版本", "Current version")} · {progress.data?.current || release?.current || "—"}</p>
    {available && release && <>
      <p>{t("可更新至", "Available release")} <strong>{release.latest}</strong>{release.size ? ` · ${bytes(release.size)}` : ""}</p>
      {release.published && <p className="oc-muted">{new Date(release.published).toLocaleDateString()}</p>}
      {release.notes && <p style={{ whiteSpace: "pre-wrap" }}>{release.notes}</p>}
    </>}
    {release && !available && !release.error && <p>{t("已是最新版本", "You're up to date")}</p>}
    <ErrorNotice error={release?.error || query.error} />
    {release && !release.supported && <p>{t("当前系统暂不支持在线更新。", "Online updates are not supported on this system yet.")}</p>}
    {stage && <div role="status" aria-live="polite">
      <p>{labels[stage] || stage}{status?.version ? ` · ${status.version}` : ""}</p>
      {stage === "downloading" && total > 0 && <>
        <progress aria-label={t("更新下载进度", "Update download progress")} max={total} value={done} style={{ width: "100%", accentColor: "var(--primary)" }} />
        <p className="oc-muted">{bytes(done)} / {bytes(total)}{status?.bytes_per_second ? ` · ${bytes(status.bytes_per_second)}/s` : ""}</p>
      </>}
      {(stage === "failed" || stage === "rolled_back") && <p className="oc-muted">{status?.detail}</p>}
    </div>}
    {progress.error && <p role="status">{busy
      ? t("正在等待 Studio 重新连接…", "Waiting for Studio to reconnect…")
      : t("暂时无法连接 Studio，正在重试…", "Studio is unreachable; retrying…")}</p>}
    {blocked && !busy && <p className="oc-muted">{blocked}</p>}
    <div className="oc-actions oc-spaced">
      <Action variant="outline" disabled={busy} run={async () => {
        setChecked(await api<Release>("/api/updates?force=true"));
        progress.refresh();
      }}><RefreshCw />{t("检查更新", "Check for updates")}</Action>
      {available && release && <Button disabled={busy || !!blocked || !release.verified || !release.supported || !!progress.error} onClick={() => setSelected(release)}>
        <Download />{t("更新 Studio", "Update Studio")}
      </Button>}
      {stage === "installed" && <Button variant="outline" onClick={() => window.location.reload()}>{t("重新加载界面", "Reload Studio")}</Button>}
    </div>
    <Modal open={!!selected} onOpenChange={open => { if (!open) setSelected(undefined); }} title={t("确认更新 Studio", "Confirm Studio update")}>
      <p>{t("更新至", "Update to")} {selected?.latest}</p>
      <p>{t("下载完成后 Studio 会短暂重启。模型文件、推理环境、会话和配置会保留；更新失败会自动恢复原版本。", "Studio will briefly restart after the download. Models, runtimes, conversations and settings are retained. A failed activation restores the previous version.")}</p>
      {selected?.deployment.mode === "source" && <p>{t("当前运行源码版。此次更新会切换到正式安装版，原源码目录会保留。", "This instance runs from source. Updating switches to a managed release and keeps the source directory.")}</p>}
      <Action disabled={!!blocked || busy} run={async () => {
        await mutation("/api/updates/install", { release_id: selected?.release_id, switch_to_release: selected?.deployment.mode === "source" });
        setSelected(undefined);
        progress.refresh();
      }}>{selected?.deployment.mode === "source" ? t("切换到正式版并更新", "Switch to release and update") : t("确认更新", "Install update")}</Action>
    </Modal>
  </div>;
}
