// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useEffect, useState } from "react";
import { Activity, Cpu, Zap } from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useQuery, type GPU } from "./api";
import { ErrorNotice, format, useText } from "./common";

type Live = {
  available: boolean;
  error?: string;
  timestamp?: number;
  stale: boolean;
  gpus: GPU[];
  gpu_uuids: string[];
  model_gpu_uuids: string[];
  history: {
    timestamp: number;
    power_w: number | null;
    measured_power_w: number | null;
    power_reporting_gpu_count: number;
    gpu_count: number;
    memory_used_mib: number | null;
  }[];
};

export function LiveGPUs() {
  const t = useText();
  const { data, error } = useQuery<Live>("/api/gpu/live", 1000);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const fresh =
    !!data?.available &&
    !!data?.timestamp &&
    !data.stale &&
    !error &&
    now / 1000 - data.timestamp < 3;
  const gpus = data?.gpus || [];
  const measured = gpus.filter((g) => g.power_w != null);
  const modelGPUs = new Set(data?.model_gpu_uuids || []);
  const power =
    fresh && measured.length
      ? measured.reduce((sum, g) => sum + g.power_w!, 0)
      : null;
  function total(
    field: "power_w" | "power_limit_w" | "memory_used_mib" | "memory_total_mib",
  ) {
    return fresh && gpus.length > 0 && gpus.every((g) => g[field] != null)
      ? gpus.reduce((sum, g) => sum + (g[field] ?? 0), 0)
      : null;
  }
  const memory = total("memory_used_mib"),
    capacity = total("memory_total_mib");
  return (
    <section
      className="oc-telemetry"
      aria-label={t("实时 GPU 监测", "Live GPU telemetry")}
    >
      <div className="oc-row">
        <h2>
          <Activity size={18} /> {t("实时监测", "Live telemetry")}
        </h2>
        <span className={fresh ? "oc-live-badge" : "oc-status-warn"}>
          <i />
          {fresh
            ? t("每 0.5 秒采样", "Sampled every 0.5 s")
            : t("数据已暂停", "Data paused")}
          {data?.timestamp && (
            <time>{new Date(data.timestamp * 1000).toLocaleTimeString()}</time>
          )}
        </span>
      </div>
      <ErrorNotice error={error || data?.error} />
      <p className="oc-muted oc-monitor-scope">
        {t("全部硬件", "All hardware")} · {gpus.length} GPU ·{" "}
        {t("当前模型分配", "Assigned to current model")}{" "}
        {gpus.filter((g) => modelGPUs.has(g.uuid)).length} GPU
      </p>
      <div className="oc-telemetry-stats">
        <div>
          <span>
            <Zap size={15} />{" "}
            {measured.length === gpus.length
              ? t("全部 GPU 总功率", "All GPUs · total power")
              : t("已测 GPU 功率", "Measured GPU power")}
          </span>
          <strong data-testid="live-power">
            {format(power, 1)} <small>W</small>
          </strong>
          <p>
            {t("功率上限合计", "Total power limit")}{" "}
            {format(total("power_limit_w"), 0)} W · {gpus.length} GPU
          </p>
          {measured.length < gpus.length && (
            <p data-testid="power-coverage">
              {t("功率读数", "Power readings")} {measured.length}/{gpus.length}{" "}
              GPU ·{" "}
              {t("缺失读数不计入功率", "Missing readings excluded from power")}
            </p>
          )}
        </div>
        <div>
          <span>
            <Cpu size={15} /> {t("已用显存", "Memory used")}
          </span>
          <strong>
            {format(memory == null ? null : memory / 1024, 1)}{" "}
            <small>
              / {format(capacity == null ? null : capacity / 1024, 0)} GiB
            </small>
          </strong>
          <p>
            {t(
              "物理显存占用，包含引擎预分配的 KV 缓存",
              "Physical allocation, including engine-reserved KV cache",
            )}
          </p>
        </div>
      </div>
      <div className="oc-live-chart">
        <ResponsiveContainer width="100%" height={190}>
          <AreaChart
            data={data?.history || []}
            margin={{ top: 12, right: 12, left: 0, bottom: 0 }}
          >
            <defs>
              <linearGradient id="live-power-fill" x1="0" y1="0" x2="0" y2="1">
                <stop
                  offset="0%"
                  stopColor="var(--primary)"
                  stopOpacity={0.25}
                />
                <stop
                  offset="100%"
                  stopColor="var(--primary)"
                  stopOpacity={0.01}
                />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="var(--border)" />
            <XAxis
              dataKey="timestamp"
              type="number"
              domain={["dataMin", "dataMax"]}
              tickFormatter={(v) => new Date(v * 1000).toLocaleTimeString()}
              minTickGap={65}
              tick={{ fontSize: 11 }}
            />
            <YAxis
              domain={[0, "auto"]}
              unit=" W"
              width={60}
              tick={{ fontSize: 11 }}
            />
            <Tooltip
              contentStyle={{
                background: "var(--popover)",
                borderColor: "var(--border)",
                borderRadius: 12,
              }}
              labelFormatter={(v) =>
                new Date(Number(v) * 1000).toLocaleTimeString()
              }
              formatter={(v, _name, item) => [
                `${format(Number(v), 1)} W`,
                `${t("已测 GPU 功率", "Measured GPU power")} · ${item.payload.power_reporting_gpu_count}/${item.payload.gpu_count} GPU`,
              ]}
            />
            <Area
              type="linear"
              dataKey="measured_power_w"
              stroke="var(--primary)"
              fill="url(#live-power-fill)"
              strokeWidth={2}
              isAnimationActive={false}
              connectNulls={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <p className="oc-muted oc-chart-caption">
        {t(
          "最近 2 分钟 · 全部可测 GPU 板卡功率 · 不含 CPU、内存和电源损耗",
          "Last 2 minutes · all reporting GPU boards · excludes CPU, RAM, and PSU losses",
        )}
      </p>
      <div className="oc-gpu-grid">
        {gpus.map((g) => (
          <article className="oc-gpu-card" key={g.uuid}>
            <div className="oc-row">
              <b>
                GPU {g.index}
                {modelGPUs.has(g.uuid) && (
                  <span className="oc-gpu-model-badge">
                    {t("当前模型使用", "Current model")}
                  </span>
                )}
              </b>
              <span>{g.name.replace("Tesla ", "")}</span>
            </div>
            <div className="oc-gpu-power">
              {format(fresh ? g.power_w : null, 1)} <small>W</small>
              <span>
                {t("上限", "Limit")} {format(g.power_limit_w, 0)} W
              </span>
            </div>
            {fresh && g.power_w == null && (
              <p className="oc-muted oc-gpu-power-unavailable">
                {t("此显卡未提供功率读数", "This GPU does not report power")}
              </p>
            )}
            <div className="oc-row oc-gpu-memory">
              <span>{t("显存", "VRAM")}</span>
              <span>
                {format(
                  fresh && g.memory_used_mib != null
                    ? g.memory_used_mib / 1024
                    : null,
                  1,
                )}{" "}
                /{" "}
                {format(
                  g.memory_total_mib == null ? null : g.memory_total_mib / 1024,
                  0,
                )}{" "}
                GiB
              </span>
            </div>
            <meter
              min={0}
              max={g.memory_total_mib || 1}
              value={fresh ? g.memory_used_mib || 0 : 0}
              aria-label={`GPU ${g.index} ${t("显存占用", "memory used")}`}
            />
            <div className="oc-row oc-gpu-footer">
              <span>
                {t("利用率", "Utilization")}{" "}
                {format(fresh ? g.utilization : null, 0)}%
              </span>
              <span>
                {format(fresh ? g.temperature_c : null, 0)} °C ·{" "}
                {format(fresh ? g.graphics_clock_mhz : null, 0)} MHz
              </span>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
