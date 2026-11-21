// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useEffect, useRef, useState } from "react";
import type { Job } from "./api";
import { format, useText } from "./common";

export function DownloadRate({ job, total = job.expected_bytes ?? 0 }: { job: Job; total?: number }) {
  const t = useText();
  const [now, setNow] = useState(Date.now);
  const transferring = job.state === "running" && job.stage === "downloading" && !job.cancel_requested;
  const samples = useRef<{ time: number; bytes: number }[]>([]);
  useEffect(() => { samples.current = []; }, [job.id, transferring]);
  useEffect(() => {
    const time = job.updated_at;
    const bytes = job.downloaded_bytes;
    if (!transferring || time == null || bytes == null) return;
    const window = samples.current.filter(s => time > s.time && time - s.time <= 20 && s.bytes <= bytes);
    samples.current = [...window, { time, bytes }];
  }, [job.id, transferring, job.updated_at, job.downloaded_bytes]);
  useEffect(() => {
    if (!transferring) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [transferring]);
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
  const first = samples.current[0];
  const elapsed = (job.updated_at ?? 0) - (first?.time ?? 0);
  const done = job.downloaded_bytes ?? 0;
  const mean = first && elapsed >= 3 && done > first.bytes ? (done - first.bytes) / elapsed : speed;
  const remaining = fresh && speed && mean && mean > 0 && total > done
    ? Math.ceil((total - done) / mean) : undefined;
  const clock = new Date(now);
  const finish = remaining ? new Date(now + remaining * 1000) : undefined;
  const minutes = remaining ? Math.ceil(remaining / 60) : 0;
  const duration = minutes < 60 ? t(`${minutes} 分钟`, `${minutes} min`)
    : t(`${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`, `${Math.floor(minutes / 60)}h ${minutes % 60}m`);
  return (<>
    <span
      className="oc-download-rate"
      title={
        fresh
          ? t(
              "下载速度每秒更新",
              "Download speed updates every second",
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
    {transferring && done < total && <span className="oc-download-eta">
      {finish && Number.isFinite(finish.getTime()) ? t("预计 ", "Estimated finish ") +
        finish.toLocaleTimeString(undefined, {
          ...(finish.toDateString() !== clock.toDateString() ? { month: "numeric", day: "numeric" } : {}),
          hour: "2-digit", minute: "2-digit", hour12: false,
        }) + t(` 完成（约剩 ${duration}）`, ` (about ${duration} left)`)
        : t("正在估算完成时间…", "Estimating finish time…")}
    </span>}
  </>);
}
