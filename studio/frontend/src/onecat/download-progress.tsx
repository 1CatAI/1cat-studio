// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { Progress } from "./ui";

function quantity(value: number, rate = false) {
  const amount = Number.isFinite(value) ? Math.max(0, value) : 0;
  const units = ["B", "KB", "MB", "GB", "TB"];
  const unit = Math.min(units.length - 1, Math.floor(Math.log10(Math.max(1, amount)) / 3));
  return `${(amount / 1000 ** unit).toFixed(rate || unit === 0 ? 0 : 1)} ${units[unit]}`;
}
export function DownloadProgressBar({ progress, bytesPerSec, cancelling = false }: {
  progress: { expectedBytes: number; downloadedBytes: number; fraction: number };
  bytesPerSec: number;
  cancelling?: boolean;
}) {
  const fraction = Number.isFinite(progress.fraction) ? Math.max(0, Math.min(1, progress.fraction)) : 0;
  return <div className="oc-kit-download" data-cancelling={cancelling || undefined} aria-live="polite">
    <div className="oc-kit-download-track">
      <Progress value={fraction * 100} />
      <span aria-hidden="true" className="oc-kit-download-tip" style={{ left: `${fraction * 100}%` }} />
    </div>
    <div className="oc-kit-download-caption">
      <span>{quantity(progress.downloadedBytes)} / {quantity(progress.expectedBytes)}</span>
      <span>{bytesPerSec > 0 ? `${quantity(bytesPerSec, true)}/s` : ""}</span>
    </div>
  </div>;
}
