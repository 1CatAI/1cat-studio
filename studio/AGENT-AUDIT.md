# Agent integration acceptance — 2026-09-08

Official Codex 0.153.4 is bundled, with its Apache license and NOTICE. The model
provider in acceptance is deterministic and CPU-only. No GPU inference, model
start/stop, driver, power or clock operation is part of these tests.

Verified against the actual Codex executable:
- Read a broken Python file, edit it, run a correctness assertion, inspect tool
  results on the next model call, then resume the same persisted thread.
- Host home and NVIDIA devices are absent. Host localhost and external network
  are unreachable; only the per-task local-model bridge is reachable.
- Original Codex command approval pauses the task; refusal is returned to Codex.
- Stop cancels inference and the owned subprocess tree; manager shutdown retains
  a resumable Codex thread and partial output.
- Model identity is pinned between calls. Missing tool support blocks start.
- Usage totals equal the mock model's exact token counters, including cache hits.

Protocol and API checks cover function/custom/namespaced tools, Unicode streams,
unsupported content, missing completion, truncated tool arguments, replay,
local reasoning streaming and first-token timing (including reasoning tokens),
request deduplication, project concurrency, auth scopes, path traversal, symlinks,
project upload/export and metadata-only history queries.

Browser checks cover project creation, actual Codex commands, refresh/resume,
leaving a running task, explicit cancellation, manual scrolling, split preview
and script interaction, 320/390 px layouts, keyboard close, Chinese/English,
light/dark and reduced motion. Preview opens only after a user click.

Second audit fixes:
- Replay events copy mutable items so a reconnect cannot duplicate streamed text.
- Completed items retain identity and do not animate again during a later turn.
- A queued follow-scroll frame rechecks the user's current scroll preference.
- An explicitly opened preview follows new source; polling cannot open it.
- Client request IDs persist across retries. A project remains locked until its
  prior task has fully cleaned up; terminal snapshots wait for saved file diffs.
- Project cloning does not hold the global management lock and block Stop.
- Model changes cannot redirect an existing task to a new engine instance.
- Rollback refuses active agents and checkpoints post-upgrade database contents.
- GPU deployment hold skips hardware helper setup and policy recovery.

Local V100 model quality/tool reliability remain UNVERIFIED: the operator has
reserved GPUs for other agents. Integration tests are not a model capability badge.

## Follow-up bug and feature audit — 2026-09-08

The follow-up exercised the real bundled Codex against a deterministic CPU-only
provider, with disposable database/workspaces. It does not validate a V100 model's
coding quality. Initial regression cases reproduced 12 failures before fixes.

Changes verified in this audit:

1. Cancelling immediately after submission settles the task even if its coroutine
   has not started; the project lock is released.
2. Shutdown waits for a cancelled/completed worker's cleanup rather than injecting
   a second cancellation and abandoning process reaping or file-diff persistence.
3. Approval replies serialize per task; duplicate or expired replies are rejected.
   Question replies require matching IDs and nonempty values at the API boundary.
4. Continue clears the previous turn's plan, diff, change list and file error while
   preserving transcript, thread identity and exact accumulated token counters.
5. Exactly 2,000 project files are supported. Replacements remain possible at the
   limit, and the 2,001st file is rejected before writing.
6. Normalized upload paths use the existing file's bytes for quota accounting.
7. Uploads use a sibling temporary file and atomic rename. Failed writes preserve
   the original file and clean up temporary artifacts; successful replacements retain
   the original script execute permissions.
8. Listings use directory descriptors and exclude reserved regular filenames as
   well as directories; a git worktree's `.git` file no longer breaks ZIP export.
9. ZIP exports stream generated files under the 256 MiB project budget, independently
   of the 10 MiB source-view/upload limit.
10. Truncated file listings do not imply deletion; snapshot text-budget omissions
    do not imply modification. Incomplete comparisons are identified in the UI.
11. Cancelled Git imports reap their own subprocess group before deleting partial
    directories. Sandbox permission probes refresh after AppArmor policy changes.
12. Explicit provider tool_choice values are translated instead of silently becoming
    auto; unknown named choices are rejected.
13. Query results are bound to their URL. Switching project/file cannot show the
    previous project's identically named source. Stale SSE recovery requests cannot
    overwrite newer events, and missing/forbidden tasks stop reconnecting.
14. Chat handoff waits for source context, stale project selections recover, and a
    continued task explains why submission is unavailable when its model is stopped.
15. JS/CSS project previews keep the same executable wrapper during live updates;
    source is not reparsed as Markdown, preserving embedded fences and mixed-case
    filename extensions.
16. Execution plans are visible, folder uploads preserve relative paths and skip
    environment/cache directories, and upload results are shown in place.

Validation: 51 targeted backend tests, 10 frontend reducer/artifact/streaming tests,
TypeScript and production build. Browser coverage includes actual Codex tools,
plan display, refresh/resume/cancel, manual scroll, split preview interaction,
slow cross-project source responses, folder upload exclusions, terminal 404,
320/390 px screens, keyboard close, Chinese/English, dark/light and reduced motion.
Evidence is in `.artifacts/agent-audit-20260908/`. Deployment validation copies the
real database to a separate installation, verifies rollback and preserves GPU hold.

Remaining scope boundaries: no GPU/model-capability validation while GPUs are
reserved; Agent image inputs and external connectors are not integrated. Network
is isolated, so Agent commands cannot fetch arbitrary dependencies. Preview supports
standalone HTML/CSS/JS/SVG/React, not arbitrary multi-file build servers or relative
module graphs. These remain explicit product limitations, not claimed capabilities.


## Conversation flow audit — 2026-09-09

This follow-up keeps the compact composer and checks actual workflows rather than
claiming parity from bundling Codex. Regression cases added this round:

1. A task opened from another project's history now retains that project for New task.
2. File controls remain unavailable until the task's project is hydrated.
3. Late submission responses cannot navigate back after a mode/page change; request IDs survive retries.
4. Uploads are cancelled on leaving their view, and server writes exclude concurrent task start/deletion.
5. Skills listing preserves previous model/context/plan/diff/usage; explicit skill names use typed inputs.
6. Model identity binds launch ID/PID/start time, including the atomic request-admission check.
7. Conflicting reuse of an Agent request ID is rejected rather than returning an unrelated task.
8. Agent history searches the server's full history instead of filtering only the latest 100 tasks.
9. Copy includes the latest plan/review; exported tool output includes the command that produced it.
10. New-turn counts include legacy history, and timing gaps in older tasks are disclosed.
11. Live rates no longer repeatedly announce through the state live region.
12. Chat settings survive mode switches and polling without replacing user edits.
13. New generation submissions retain drafts on rejection and preserve original history until model content arrives.
14. Stale chat history and conflicting generation IDs are rejected; cancelled/empty initial streams preserve content.
15. App-server turn steering is connected with a bound turn ID and explicit uncertain-delivery handling.
16. Dialogs restore their originating focus, including the in-place downloaded model chooser.
17. Thinking is available in both composers and transmitted to the model template; model selection uses downloaded targets and recipes.
18. Compact mobile controls stay on one row after adding thinking and model selection.

The second browser pass reproduced and fixed a late response after component unmount,
missing modal focus restoration, and a mobile wrapping regression. A stream commit
snapshot must also synchronize frontend text accumulators so the first chunk is not lost.
See AGENT.md for the capability matrix and the features that are still not exposed.

Integration also includes the pending chat-completion fix: independent durable status
checks settle lost final SSE events, close observers without cancelling generation,
and prevent a previous observer from clearing the next submission's busy state.
The fault-injection browser run retained exact final code/usage and restored the send
button within 2.9 seconds despite a deliberately half-open connection.

Real four-V100 acceptance used the already running Qwen3.8 source/DFlash2 model, in
a separate project/state directory. Thinking on/off, file read/edit/test, and native
thread continuation passed. No power, clock, driver or model restart was performed
for this acceptance. Reproduction receipts are kept in the flow-audit artifacts.
