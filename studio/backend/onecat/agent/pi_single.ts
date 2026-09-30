// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
// Pinned OMP exposes `spawns` only on child-agent definitions, not root CLI
// sessions. Its native pre-spawn hook enforces the same policy for a root
// single-agent run, including eval agent() calls, without changing OMP source.
export default function (pi: any) {
  pi.on("before_subagent_spawn", () => ({
    block: true,
    reason: "This Studio task uses single-agent mode (spawns: false). Complete it without subagents.",
  }));
}
