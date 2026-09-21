// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useEffect, useState } from "react";
import type { Job } from "./api";
import { format, useText } from "./common";

export function DownloadRate({ job }: { job: Job }) {
  const t = useText();
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const speed = job.bytes_per_second;
  const fresh =
    job.state === "running" &&
    job.stage === "downloading" &&
    !job.cancel_requested &&
    job.updated_at != null &&
    Math.max(now, Date.now()) - job.updated_at * 1000 <= 5000 &&
    speed != null &&
    Number.isFinite(speed) &&
    speed >= 0;
  const unit =
    speed != null && speed >= 1024 ** 3
      ? 3
      : speed != null && speed >= 1024 ** 2
        ? 2
        : speed != null && speed >= 1024
          ? 1
          : 0;
  return (
    <span
      className="oc-download-rate"
      title={
        fresh
          ? t(
              "根据实际新增下载字节计算，每秒刷新",
              "Measured from newly downloaded bytes; refreshed every second",
            )
          : t(
              "等待新的下载速率数据",
              "Waiting for a fresh download speed sample",
            )
      }
    >
      {t("下载速率", "Download speed")}{" "}
      {fresh
        ? `${format(speed! / 1024 ** unit, 1)} ${["B/s", "KB/s", "MB/s", "GB/s"][unit]}`
        : "—"}
    </span>
  );
}
