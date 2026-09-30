// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import type { Subagent } from "./agent-state";
import { format, useText } from "./common";

export function SubagentTree({ nodes }: { nodes: Subagent[] }) {
  const t = useText();
  const ids = new Set(nodes.map(node => node.id));
  function render(parent: string | null, ancestors: Set<string>): React.ReactNode {
    return <ul className="oc-agent-subagents">{nodes.filter(node => parent === null ? !node.parent_id || !ids.has(node.parent_id) : node.parent_id === parent).filter(node => !ancestors.has(node.id)).map(node => {
      const path = new Set([...ancestors, node.id]);
      const states: Record<string, string> = { running: t("运行中", "Running"), pending: t("等待中", "Pending"), completed: t("已完成", "Completed"), failed: t("失败", "Failed"), cancelled: t("已取消", "Cancelled"), aborted: t("已停止", "Aborted"), interrupted: t("已中断", "Interrupted") };
      return <li key={node.id}>
        <div className="oc-row"><strong>{node.name || node.id}</strong><span>{states[node.status || ""] || node.status}</span></div>
        <small>{format(node.elapsed_s, 1)} s · {node.request_count ?? 0} {t("请求（PI 原生统计）", "requests (PI statistics)")}</small>
        {(node.progress?.lastIntent || node.progress?.currentTool || node.assignment) && <p>{node.progress?.lastIntent || node.progress?.currentTool || node.assignment}</p>}
        {node.error && <p role="alert">{node.error}</p>}
        {nodes.some(child => child.parent_id === node.id && !path.has(child.id)) && render(node.id, path)}
      </li>;
    })}</ul>;
  }
  return render(null, new Set());
}
