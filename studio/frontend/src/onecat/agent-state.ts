// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
export type AgentItem = {
  id: string; type: string; text?: string; status?: string; command?: string; finished?: boolean;
  aggregatedOutput?: string; exitCode?: number | null; summary?: string[]; review?: string;
  changes?: { path: string; diff?: string; kind?: { type?: string } }[];
};
export type Approval = {
  id: string; method: string;
  params: { command?: string; reason?: string; grantRoot?: string;
    questions?: { id: string; question: string; options?: { label: string; description: string }[] }[] };
};
export type AgentTask = {
  id: string; project_id: string; title: string; state: string; model: string;
  created_at: number; updated_at: number; elapsed_s: number; error?: string;
  items: AgentItem[]; approvals: Approval[]; model_calls: number;
  usage: { input_tokens: number; output_tokens: number; cached_tokens: number;
    missing_calls: number; cache_missing_calls: number };
  diff?: string; changes?: { path: string; kind: string }[];
  file_error?: string;
  files_truncated?: boolean;
  settled?: boolean;
  model_label?: string; context_window?: number; codex_version?: string;
  permission?: "workspace-write" | "read-only"; mode?: "default" | "plan"; turn_count?: number;
  thinking?: boolean; thinking_effort?: "low" | "medium" | "high" | "xhigh" | null; current_turn?: string; operation?: string;
  execution_permission?: "workspace-write" | "read-only";
  timing_missing_calls?: number;
  metrics?: { llm_s: number; tool_s: number; tool_calls: number; ttft_s: number; ttft_count: number;
    decode_s: number; decode_tokens: number; decode_calls: number };
  live_metrics?: { output_tokens?: number | null; elapsed_s: number; ttft_s?: number | null;
    decode_tokens_s?: number | null; reason?: string | null } | null;
  context_usage?: { modelContextWindow?: number | null; last: { totalTokens: number }; total: { totalTokens: number } };
  plan?: { step: string; status: string }[];
};
export type AgentEvent = { type: string; task?: AgentTask; events?: AgentEvent[];
  item?: AgentItem; id?: string; field?: string; delta?: string } & Partial<AgentTask>;
export const activeAgent = (state?: string) => ["starting", "running", "waiting", "stopping"].includes(state || "");

export function reduceAgent(task: AgentTask | undefined, event: AgentEvent): AgentTask | undefined {
  if (event.type === "snapshot") return event.task;
  if (event.type === "batch") return (event.events || []).reduce<AgentTask | undefined>(reduceAgent, task);
  if (!task) return task;
  if (event.type === "item" && event.item) {
    const exists = task.items.some(item => item.id === event.item!.id);
    return { ...task, items: exists ? task.items.map(item => item.id === event.item!.id ? event.item! : item) : [...task.items, event.item] };
  }
  if (event.type === "delta") {
    if (event.field !== "text" && event.field !== "aggregatedOutput") return task;
    const field = event.field;
    return { ...task, items: task.items.map(item => item.id === event.id
      ? { ...item, [field]: (item[field] || "") + (event.delta || "") } : item) };
  }
  const { type: _, ...fields } = event;
  return { ...task, ...fields };
}

export function agentItemText(item: AgentItem): string {
  return [item.text, item.review, item.command, item.aggregatedOutput,
    item.changes?.map(change => `${change.path}\n${change.diff || ""}`).join("\n")].filter(Boolean).join("\n\n");
}
export function latestAgentAnswer(items: AgentItem[]): string | undefined {
  return items.slice().reverse().filter(item => ["agentMessage", "plan", "exitedReviewMode"].includes(item.type)).map(agentItemText).find(Boolean);
}
