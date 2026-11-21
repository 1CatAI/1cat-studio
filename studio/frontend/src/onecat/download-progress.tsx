// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import type { Job } from "./api";
import { DownloadRate } from "./download-rate";
import { useText } from "./common";
import { Progress } from "./ui";

function quantity(value: number) {
  const amount = Number.isFinite(value) ? Math.max(0, value) : 0;
  const units = ["B", "KB", "MB", "GB", "TB"];
  const unit = Math.min(units.length - 1, Math.floor(Math.log10(Math.max(1, amount)) / 3));
  return `${(amount / 1000 ** unit).toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}
export function DownloadProgressBar({ job, expectedBytes }: { job: Job; expectedBytes?: number | null }) {
  const t = useText();
  if (job.stage === "checking_model" && (job.verification || (job.state === "running" && (!job.kind || job.kind === "download_model")))) {
    const v = job.verification;
    const hashed = v && v.files_done === v.total_files;
    const fraction = v && v.total_bytes > 0 ? v.bytes_read / v.total_bytes : 0;
    const percent = v ? hashed ? 100 : Math.min(99.9, fraction * 100) : undefined;
    return <div className="oc-kit-download">
      <Progress value={percent ?? null} aria-label={t("文件校验进度", "File verification progress")} />
      <div className="oc-kit-download-caption">
        <strong>{percent === undefined ? t("等待校验进度…", "Waiting for verification progress…") : percent.toFixed(1) + "%"}</strong>
        {v && <><span>{quantity(v.bytes_read)} / {quantity(v.total_bytes)}</span>
          <span>{t(`已校验 ${v.files_done}/${v.total_files} 个文件`, `${v.files_done}/${v.total_files} files verified`)}</span></>}
      </div>
      {v?.current_file && <small className="oc-verification-file" title={v.current_file}>{v.current_file}</small>}
      {hashed && job.state === "running" && <small>{t("正在核对远端文件清单…", "Checking the remote manifest…")}</small>}
    </div>;
  }
  const total = job.expected_bytes || expectedBytes || 0;
  const done = Math.max(0, job.downloaded_bytes || 0);
  const percent = total > 0 ? Math.min(100, done / total * 100) : undefined;
  const label = percent === undefined ? "—" : (done < total ? Math.min(percent, 99.9) : percent).toFixed(1) + "%";
  return <div className="oc-kit-download" data-cancelling={job.cancel_requested || undefined}>
    <div className="oc-kit-download-track">
      <Progress value={percent ?? null} aria-label={t("下载进度", "Download progress")} />
      {percent !== undefined && <span aria-hidden="true" className="oc-kit-download-tip" style={{ left: `${percent}%` }} />}
    </div>
    <div className="oc-kit-download-caption">
      <strong>{label}</strong>
      <span>{quantity(done)}{total > 0 && ` / ${quantity(total)}`}</span>
      <DownloadRate job={job} total={total} />
    </div>
  </div>;
}
