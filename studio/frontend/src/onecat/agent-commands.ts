// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
// These are wired Studio actions or pinned Codex RPCs, not prompts pretending to be commands.
export const agentCommands = [
  ["new", "开始新任务", "Start a new task"],
  ["resume", "打开任务历史", "Open task history"],
  ["plan", "切换计划 / 执行模式", "Switch plan / execution mode"],
  ["review", "审查项目，不修改文件", "Review the project without editing"],
  ["compact", "压缩当前任务上下文", "Compact this task's context"],
  ["skills", "列出项目技能，用 $名称 调用", "List project skills; invoke with $name"],
  ["init", "检查项目并创建 AGENTS.md", "Inspect the project and create AGENTS.md"],
  ["permissions", "选择只读或项目写入", "Choose read-only or workspace write"],
  ["model", "选择并加载已下载模型", "Choose and load a downloaded model"],
  ["status", "查看执行统计与权限", "View statistics and permissions"],
  ["diff", "查看本轮文件修改", "View this turn's changes"],
  ["mention", "浏览并引用项目文件", "Browse and mention project files"],
  ["copy", "复制最近的回复", "Copy the last response"],
  ["export", "导出任务对话", "Export this conversation"],
  ["stop", "停止当前任务", "Stop the current task"],
  ["help", "查看命令和支持范围", "Commands and supported capabilities"],
] as const;
export function parseAgentCommand(text: string) {
  const match = /^\/([a-z]+)(?:\s+([\s\S]*))?$/i.exec(text.trim());
  return match ? { name: match[1].toLowerCase(), argument: (match[2] || "").trim() } : null;
}
export function matchingAgentCommands(text: string) {
  if (!/^\/[a-z]*$/i.test(text)) return [];
  return agentCommands.filter(([name]) => name.startsWith(text.slice(1).toLowerCase()));
}
