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
import { type RequestHistory as History, type RequestMetrics } from "./api";
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

export function RequestHistory({ data }: { data?: History }) {
  const t = useText();
  const last = data?.items.find(
    (r) => r.status === 200 && r.metrics?.decode_tokens_s != null,
  );
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
          "包括聊天与 API 请求。速度按真实 token 数计算，预填充、解码与总耗时分别记录。",
          "Chat and API requests, with actual token counts and separate prefill, decode, and total timing.",
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
              <th>{t("时间 / 模型", "Time / model")}</th>
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
                  <time>{new Date(r.started * 1000).toLocaleString()}</time>
                  <small>{modelLabel(r.metrics?.model_name || r.model)}</small>
                </td>
                <td>
                  {r.source === "studio" ? t("聊天", "Chat") : "API"}
                  <small>
                    {r.status ?? t("生成中", "Running")}
                    {r.metrics?.pending ? " · " + t("统计中", "Measuring") : ""}
                  </small>
                </td>
                <td>
                  {format(r.metrics?.prefill_tokens_s, 1)}
                  <small>
                    tok/s
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
                  {format(r.metrics?.average_gpu_w, 1)} W
                  {r.metrics?.concurrent && (
                    <small>
                      {t("并发期间总功率", "Shared during concurrency")}
                    </small>
                  )}
                </td>
                <td>{format(r.metrics?.energy_wh, 3)} Wh</td>
                <td>{format(r.elapsed, 2)} s</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!data?.items.length && (
        <p className="oc-muted oc-spaced">
          {t(
            "发送一条消息或调用 API 后，这里会出现真实请求记录。",
            "Send a message or call the API to see measured requests here.",
          )}
        </p>
      )}
      <p className="oc-muted oc-chart-caption">
        {t(
          "Prefill 扣除命中缓存的 tokens；Decode 不含预填充时间。功率来自活动 GPU 的 0.5 秒采样，耗电按请求时间窗积分估算；并发时是同期总量，不作为单请求独占耗电。历史未采集项与无法隔离的计时显示 —。",
          "Prefill excludes cached tokens; decode excludes prefill time. Power is sampled every 0.5 s and energy is integrated over the request window. Concurrent energy is shared board consumption. Missing or non-isolatable measurements remain —.",
        )}
      </p>
    </section>
  );
}
