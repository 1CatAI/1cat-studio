// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import type { Run } from "../canvas/types";

export function elapsed(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "—";
  const whole = Math.floor(seconds);
  if (whole < 60) return `${whole} s`;
  const minutes = Math.floor(whole / 60);
  const remainder = String(whole % 60).padStart(2, "0");
  return minutes < 60
    ? `${minutes}:${remainder}`
    : `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}:${remainder}`;
}

export function totalSeconds(run: Pick<Run, "state" | "created_at" | "finished_at" | "timing">, now: number): number | null {
  if (run.timing?.total_seconds != null) return run.timing.total_seconds;
  const end = ["completed", "failed", "cancelled"].includes(run.state)
    ? run.finished_at
    : now;
  if (end == null || !Number.isFinite(end) || !Number.isFinite(run.created_at)) return null;
  return end >= run.created_at ? end - run.created_at : null;
}
