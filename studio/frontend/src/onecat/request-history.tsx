// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { type RequestHistory as History, type RequestMetrics, type RequestRecord } from "./api";
import { format, useText } from "./common";
import { modelLabel } from "./model-label";

export function timingSource(
  m: RequestMetrics,
  t: (zh: string, en: string) => string,
) {
  return m.decode_source === "isolated_engine_metrics"
    ? t("vLLM 服务端计时", "vLLM engine timing")
    : m.decode_source === "local_token_stream"
      ? t("服务器本机流观测", "Server-local token stream")
      : t("无可靠计时", "Timing unavailable");
}

function missingMeasurement(
  row: RequestRecord,
  field: "prefill" | "power",
  t: (zh: string, en: string) => string,
) {
  if (row.status == null) return t("生成中", "Running");
  if (row.metrics?.pending) return t("统计中", "Measuring");
  if (field === "power") {
    return row.metrics?.gpu_uuids
      ? t("功率采样不足", "Incomplete power samples")
      : t("未采集功率", "Power not recorded");
  }
  if (row.status !== 200) return t("请求未完成", "Request incomplete");
  if (row.prompt_tokens && row.prompt_tokens === row.metrics?.cached_tokens)
    return t("全部命中缓存", "Fully cached");
  if (row.metrics?.concurrent) return t("并发无法归因", "Concurrent requests");
  return t("无独立计时", "No attributable timing");
}

export function RequestHistory({ data }: { data?: History }) {
  const t = useText();
  const last = data?.items.find((r) => r.status === 200);
  const chart =
    data?.items
      .filter((r) => r.metrics?.average_gpu_w != null)
      .slice(0, 60)
      .reverse()
      .map((r, index) => ({
        index,
        label: new Date(r.started * 1000).toLocaleTimeString(),
        ...r.metrics,
      })) || [];
  return (
    <section className="oc-panel oc-request-history">
      <div className="oc-row">
        <h2>{t("请求性能与功耗", "Request performance & power")}</h2>
        <span className="oc-live-badge">
          {data?.active || 0} {t("正在处理", "active")}
        </span>
      </div>
      <p className="oc-muted">
        {t(
          "查看聊天、Agent 和 API 请求的预填充、解码及总耗时。",
          "Review prefill, decode and total times for chat, Agent and API requests.",
        )}
      </p>
      <div className="oc-request-stats">
        <div>
          <span>{t("最近一次 Prefill", "Latest prefill")}</span>
          <strong>
            {format(last?.metrics.prefill_tokens_s, 1)} <small>tok/s</small>
          </strong>
        </div>
        <div>
          <span>{t("最近一次 Decode", "Latest decode")}</span>
          <strong>
            {format(last?.metrics.decode_tokens_s, 2)} <small>tok/s</small>
          </strong>
        </div>
        <div>
          <span>
            {t("请求期间平均 GPU 功率", "Mean GPU power during request")}
          </span>
          <strong>
            {format(last?.metrics.average_gpu_w, 1)} <small>W</small>
          </strong>
        </div>
        <div>
          <span>
            {t("请求期间 GPU 耗电估算", "GPU energy during request · estimate")}
          </span>
          <strong>
            {format(last?.metrics.energy_wh, 3)} <small>Wh</small>
          </strong>
        </div>
      </div>
      {chart.length > 0 && (
        <div className="oc-request-chart">
          <div className="oc-row">
            <h3>{t("历史请求功率", "Power across recent requests")}</h3>
            <span className="oc-muted">
              {t("每个点代表一次请求", "One point per request")}
            </span>
          </div>
          <ResponsiveContainer width="100%" height={190}>
            <AreaChart
              data={chart}
              margin={{ top: 12, right: 12, left: 0, bottom: 0 }}
            >
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <XAxis
                dataKey="index"
                tickFormatter={(v) => chart[v]?.label || ""}
                minTickGap={60}
                tick={{ fontSize: 11 }}
              />
              <YAxis unit=" W" width={65} tick={{ fontSize: 11 }} />
              <Tooltip
                contentStyle={{
                  background: "var(--popover)",
                  borderColor: "var(--border)",
                  borderRadius: 12,
                }}
                labelFormatter={(v) => chart[Number(v)]?.label}
                formatter={(v) => [
                  `${format(Number(v), 1)} W`,
                  t("平均 GPU 功率", "Mean GPU power"),
                ]}
              />
              <Area
                type="linear"
                dataKey="average_gpu_w"
                stroke="var(--primary)"
                strokeWidth={2}
                fill="var(--primary)"
                fillOpacity={0.08}
                dot={{ r: 3 }}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
      <div className="oc-table-wrap">
        <table className="oc-table oc-request-table">
          <thead>
            <tr>
              <th>{t("模型 / 时间", "Model / time")}</th>
              <th>{t("来源 / 状态", "Source / status")}</th>
              <th>Prefill</th>
              <th>Decode</th>
              <th>TTFT</th>
              <th>{t("输入 / 输出", "Input / output")}</th>
              <th>{t("平均功率", "Mean power")}</th>
              <th>{t("耗电估算", "Energy estimate")}</th>
              <th>{t("总耗时", "Total")}</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((r) => (
              <tr key={r.id}>
                <td>
                  <strong className="oc-request-model">{modelLabel(r.metrics?.model_name || r.model)}</strong>
                  <time dateTime={new Date(r.started * 1000).toISOString()}>{new Date(r.started * 1000).toLocaleString()}</time>
                </td>
                <td>
                  {r.source === "chat_title" ? t("对话标题", "Chat title") : r.source === "studio" ? t("聊天", "Chat") : r.source === "agent" ? (r.metrics?.agent_engine === "pi" ? "PI" : r.metrics?.agent_engine === "codex" ? "Codex" : "Agent") : "API"}
                  <small>
                    {r.status ?? t("生成中", "Running")}
                    {r.metrics?.pending ? " · " + t("统计中", "Measuring") : ""}
                  </small>
                </td>
                <td>
                  {format(r.metrics?.prefill_tokens_s, 1)}
                  <small>
                    {r.metrics?.prefill_tokens_s == null ? missingMeasurement(r, "prefill", t) : "tok/s"}
                    {r.metrics?.cached_tokens
                      ? ` · ${t("缓存", "cached")} ${r.metrics.cached_tokens}`
                      : ""}
                  </small>
                </td>
                <td>
                  {format(r.metrics?.decode_tokens_s, 2)}
                  <small>tok/s</small>
                  <small className="oc-timing-source">
                    {timingSource(r.metrics || {}, t)}
                  </small>
                </td>
                <td>{format(r.ttft, 3)} s</td>
                <td>
                  {r.prompt_tokens ?? "—"} / {r.completion_tokens ?? "—"}
                  <small>tokens</small>
                </td>
                <td>
                  {r.metrics?.average_gpu_w == null ? <small>{missingMeasurement(r, "power", t)}</small> : `${format(r.metrics.average_gpu_w, 1)} W`}
                  {r.metrics?.concurrent && (
                    <small>
                      {t("并发期间总功率", "Shared during concurrency")}
                    </small>
                  )}
                </td>
                <td>{r.metrics?.energy_wh == null ? <small>{missingMeasurement(r, "power", t)}</small> : `${format(r.metrics.energy_wh, 3)} Wh`}</td>
                <td>{format(r.elapsed, 2)} s</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!data?.items.length && (
        <p className="oc-muted oc-spaced">
          {t(
            "发送消息或调用 API 后即可查看请求记录。",
            "Send a message or call the API to see request history.",
          )}
        </p>
      )}
      <p className="oc-muted oc-chart-caption">
        {t(
          "Prefill 统计未命中缓存的 token，Decode 单独计时。功率每 0.5 秒采样，耗电按请求时间窗估算。并发请求共享同期 GPU 用量。缺测或无法单独计时的项显示 —。",
          "Prefill uses uncached tokens and decode is timed separately. GPU power is sampled every 0.5 s. Energy covers the request window and is shared by overlapping requests. A dash marks missing or non-isolatable measurements.",
        )}
      </p>
    </section>
  );
}
