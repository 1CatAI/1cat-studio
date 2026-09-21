// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { memo } from "react";
import { Gauge, ChevronDown } from "lucide-react";
import { format, useText } from "./common";
import type { AgentTask } from "./agent-state";

const seconds = (value?: number | null) => value == null ? "—" : value > 0 && value < 1 ? `${Math.round(value * 1000)} ms` : value < 60 ? `${value.toFixed(1)} s` : `${Math.floor(value / 60)} m ${Math.floor(value % 60)} s`;
export const AgentStats = memo(function AgentStats({ task, onDetails, details = false }: { task: AgentTask; onDetails: () => void; details?: boolean }) {
  const t = useText(), m = task.metrics, live = task.live_metrics;
  const rate = ["cancelled", "failed", "interrupted"].includes(task.state) ? null : live ? live.decode_tokens_s : m && m.decode_s > 0 ? m.decode_tokens / m.decode_s : null;
  const ttft = live ? live.ttft_s : m && m.ttft_count ? m.ttft_s / m.ttft_count : null;
  const output = task.usage.output_tokens + (live?.output_tokens ?? 0);
  if (!details) return <button type="button" className="oc-agent-stats" onClick={onDetails} aria-label={t("查看任务统计", "Task statistics")} title={t("本机 token 流观测 · 点击查看计时与用量", "Local token stream observation · Timing and usage details")}>
    <Gauge size={13} />{rate != null && <span>{format(rate, 1)} tok/s</span>}
    <span>{task.usage.missing_calls > 0 && !live?.output_tokens ? t("用量详情", "Usage details") : `${format(output, 0)} tokens`}</span><ChevronDown size={12} />
  </button>;
  const rows = [
    [t("轮次 / 模型调用", "Turns / model calls"), `${task.turn_count ?? task.items.filter(i => i.type === "userMessage").length} / ${task.model_calls}`],
    [t("模型请求耗时", "LLM request time"), seconds(m ? m.llm_s + (live?.elapsed_s ?? 0) : null)],
    [t("工具执行耗时", "Tool execution time"), seconds(m?.tool_s)],
    [live ? "TTFT" : t("平均 TTFT", "Average TTFT"), seconds(ttft)],
    [t("本机观测速率", "Locally observed speed"), rate == null ? "—" : `${format(rate, 1)} tok/s`],
    [t("累计输入", "Total input"), format(task.usage.input_tokens, 0)],
    [t("累计输出", "Total output"), format(output, 0)],
    [t("缓存命中", "Cache hits"), task.usage.cache_missing_calls > 0 ? "—" : format(task.usage.cached_tokens, 0)],
  ];
  return <><dl>{rows.map(([label, value]) => <div className="oc-agent-stat-row" key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    {!!task.timing_missing_calls && <p className="oc-agent-note">{t(`早期 ${task.timing_missing_calls} 次调用未记录计时，耗时仅统计后续调用。`, `${task.timing_missing_calls} earlier calls have no timing; durations include later measured calls only.`)}</p>}
    {task.usage.missing_calls > 0 && <p className="oc-agent-note">{t("部分请求未返回用量；上面仅合计已知 token。", "Some requests omitted usage; totals include known tokens only.")}</p>}</>;
});
