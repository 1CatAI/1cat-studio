# Agent mode

The Conversations navigation entry contains a top Chat / Agent mode switch.
Each mode retains its recent history, draft and last selected conversation or task
in the current browser tab. Switching modes does not cancel background work.
Use the chat toolbar's "Create an Agent task from this chat" action to explicitly
include a conversation as task context; ordinary mode switching does not copy it.
Existing /chat?thread= and /agent?task= links remain supported.

Both modes share Studio's model management. Agent offers sibling Codex and PI
engines. Codex uses the unmodified official 0.153.4 app-server; PI uses unmodified
Oh My Pi at commit `061f21ef011c72df891678ce489b02edee676738`, via its official
Python `omp-rpc` client from the same commit (protocol v2). No cloud login is needed.
Neither runtime can read the operator's home or global agent configuration.

## Use

Create a project (empty, upload files/folders, or import a public GitHub HTTPS repository).
Choose a downloaded model from the header or composer; Studio selects its matching
launch recipe (and requires tool calling for Agent). Describe the task,
and inspect activity, files, diffs and optional code preview. A task keeps its own
Codex thread and model instance. Refreshing or leaving the page does not stop it.
Stop terminates only the task's isolated process tree. Continue resumes the saved
Codex thread; Studio restarts mark active work interrupted, with partial results
retained. One task may write a project at a time, and at most two projects run at
once. A Codex turn is limited to 30 minutes / 64 model calls, with explicit failure and
continuation rather than silent truncation. Never infer model quality from the
mock-provider integration tests.

The model gateway translates the Responses subset emitted by the pinned Codex into
vLLM Chat Completions with tool calls. It handles function/custom tools, namespaced
tools, accepted tool outputs and streamed text. Unknown input/tool types and
truncated tool arguments fail explicitly. Model switching is not performed by an
Agent request. Underlying requests appear as source=agent in Studio history and
usage totals. Missing cache counters remain unknown; task elapsed time is never
reported as pure decode throughput. Images in Agent requests, external connectors
and arbitrary model endpoints are outside this first integration.

## Commands and working modes

Type `/` for the keyboard-accessible command menu. Arrow keys select, Tab or Enter
completes the command, a subsequent Enter runs it, and Escape dismisses the menu.
Unknown commands stay in the composer with an explanation rather than reaching the model.

The following native command details describe Codex. PI's adapter-specific
behavior is documented under PI runtime and collaboration below.

| Command | Implementation |
| --- | --- |
| `/plan` | Official `turn/start.collaborationMode`; plan turns mount the project read-only |
| `/review [instructions]` | Official `review/start` with inline delivery and a custom project review target; read-only |
| `/compact` | Official `thread/compact/start`, retaining the task, project files and visible history |
| `/skills` | Official `skills/list` in the project sandbox; works without a loaded model |
| `/init` | Ordinary Codex task to inspect the project and create or improve AGENTS.md |
| `/new`, `/resume`, `/stop` | Existing Studio task lifecycle; stopping uses official turn interruption |
| `/permissions` | Choose workspace write or read-only for the next turn |
| `/model` | Choose an installed target model and load a matching recipe in place |
| `/status` | Current model, task model, context, permissions and measured statistics |
| `/diff`, `/mention` | Project changes and file browser; insert file references into the composer |
| `/copy`, `/export`, `/help` | Raw last response, Markdown task export, supported command list |

Project skills live in `.agents/skills/<name>/SKILL.md` and are invoked with `$name`.
Explicit names are resolved through `skills/list` and attached using official typed
skill inputs. Listing skills preserves the previous model, plan, diff and usage.

While a normal turn runs, enter further instructions and use Add instructions;
Studio sends official `turn/steer` bound to that exact turn ID. Stop remains available.
Read-only/plan permissions and thinking are fixed for the current turn. A completed
or changed turn rejects the supplement and keeps the draft; an uncertain delivery
is not automatically resent. The thinking button controls `enable_thinking` in the
local model's chat template, independently of plan mode. It is available only when
the installed template exposes the switch. Unsupported models explain the limitation.
New tasks opened from task history retain that task's project.

Chat settings and drafts survive mode changes and polling. Generation submissions
are idempotent; rejected requests keep the draft and original messages. Editing or
regenerating only replaces history after the first actual model content arrives;
empty/failed initial streams leave the original conversation intact. Interrupted
streams after content arrives preserve the partial new reply.
Read-only, plan and review runs use both Codex's read-only policy and a read-only
outer bind mount. A command approval cannot grant project writes in these modes.
A normal run can resume the same thread with workspace-write when explicitly chosen.

The composer shows one compact footer: state on the left, observed speed and output
tokens on the right. Full statistics open on demand. LLM time includes model-request
waiting and generation; tool time sums official started/completed event intervals.
TTFT averages observed successful requests. Speed requires complete accepted token IDs,
matching usage, and first/last arrival times; it excludes the first batch and tool time.
It is labeled local observation, never server-only decode. Cache gaps remain unknown,
and interrupted tasks have no completed rate. Context occupancy is separate from
cumulative task token usage. No visual animation participates in measurement.

Codex's cloud login, cloud tasks, external Apps/MCP, multi-agent orchestration and
arbitrary network access are not connected by this local integration. CLI commands
that configure a terminal or those capabilities are not listed as working web actions.
Do not describe these bounded commands as full parity with every official Codex surface.

## Execution boundary

A bubblewrap namespace binds only the selected project as /workspace, per-task
Codex state as /codex-home, read-only system binaries and the bundled runtime.
It has its own PID, mount and network namespaces. No host home directory,
GPU devices, host process namespace or management credentials are exposed.
A loopback relay reaches only a per-task Unix socket providing local model requests.
External network and host localhost services are inaccessible. Shell tasks can use
preinstalled CPU tools, but cannot install dependencies from the internet.
Approvals stay within this outer boundary; even an accepted command cannot escape it.
File API operations reject traversal and symlinks using O_NOFOLLOW descriptor walks.
Uploads are atomic and limited to 10 MiB/file and 256 MiB/project (2,000 files).
ZIP export uses the project budget independently of the source-preview limit.
Folder upload skips environment/cache directories and shows a result summary.
Execution plans and completion counts appear with task activity. Project exports omit symlinks,
.git, .codex, node_modules, .venv and __pycache__.

On Ubuntu with restricted unprivileged user namespaces, install-agent-sandbox.sh
adds a dedicated root-owned bubblewrap copy and an AppArmor userns grant only for
that executable. It does not disable the system restriction or change GPU settings.
It is included in the installer. Missing sandbox support blocks execution, with no
unconfined fallback. The first release supports Linux x86_64, matching the bundle.

## Component updates and rollback

The bundled component version and archive checksum are pinned in prepare-agent.py.
Update the pin, regenerate the app-server schema and run the CPU protocol/sandbox
acceptance tests before releasing. Keep the previous Studio bundle and its matching
Codex binary to resume older tasks. Do not silently switch the runtime of an active
task. Existing Studio databases and chats are preserved through additive records.

Tests: backend/onecat_tests/test_agent.py, frontend/tests/onecat-agent.test.ts,
scripts/check-agent.py. Tests use the actual Codex binary with a deterministic
fake model; they must not call a GPU or modify GPU controls.

Preview currently supports standalone HTML/CSS/JS/SVG/React files. Multi-file build
servers, relative module graphs and downloaded dependencies are not yet integrated.


## Capability review (2026-09-09)

This is a local-model product integration, not full parity with every Codex UI.
Checked against the pinned 0.153.4 generated schemas and official sources:
[App Server](https://learn.chatgpt.com/docs/app-server),
[CLI commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli), and
[the pinned component](https://github.com/openai/codex/releases/tag/rust-v0.153.4).

| Capability | Studio coverage |
| --- | --- |
| Start, resume, interrupt native threads/turns | Connected; durable task state and project ownership |
| Steer an active turn | Connected this audit; exact turn binding and duplicate protection |
| Read/edit project files, commands, plans and diffs | Connected; tools execute in official Codex, within the outer project sandbox |
| Permission approvals and user questions | Connected; grants cannot escape the outer boundary |
| Plan, review, context compaction | Connected through native RPCs; local model quality still matters |
| Project AGENTS.md and skills | Native project instructions; skill discovery and typed skill invocation connected |
| Slash commands | 16 documented web actions/native RPCs; unknown commands are rejected, not sent as prompts |
| History and search | Studio history, server-side search across older tasks, paginated browsing |
| Model and thinking selection | Installed-model recipes and actual template options; not Codex cloud model selection |
| Usage and timing | Real local request observations; missing historical timing explicitly labeled |
| Thread fork/rollback, task rename/archive | Not exposed in Studio yet; continuation is not a substitute for these |
| External MCP, Apps, plugins, multi-agent, cloud tasks/login, network search | Not integrated; restricted runtime configuration remains explicit |
| Agent image/video input, arbitrary project build servers | Not integrated; chat images and isolated standalone code previews are separate features |
| Terminal-only keymaps, terminal theme/statusline, terminal exit | Not copied into the web UI; browser navigation/settings are separate |

Validation covers native RPC/tool execution with a deterministic provider plus
separate real local-model acceptance. Fixture tests prove integration and isolation,
not that every model reasons, writes code, or uses every tool equally well.


## PI runtime and collaboration

Build the exact audited source with `python studio/scripts/prepare-pi.py` from the
repository root (Bun >= 1.4, Git and network access are needed for the build).
`--source /absolute/checkout` accepts an existing clean checkout of that exact
commit. The binary and MIT license are kept under
`studio/vendor/oh-my-pi/061f21ef011c72df891678ce489b02edee676738/`; a manifest records
the source commit, protocol version, builder version and binary SHA256. Runtime
status rejects a missing, changed or unverified binary. A PATH `omp` is not used.
The native addon is pinned separately to official `pi-natives-linux-x64@18.3.2`
with SHA512 verification. Its npm provenance points to `7853b4e499936f9dcc13c9b64adb55f6b342aabf`;
the native package, Rust crates, Cargo lock and Bazel inputs are identical to the
audited commit. The manifest records this dependency's source and integrity.
The offline packager prepares both engines and refuses to publish without them.
The official Python client is vendored unchanged, with its own license and hashes.

New requests default to `engine=codex, collaboration=single`. For PI:

- `single` prevents native child spawns. This commit exposes `spawns` on child
  definitions/SDK sessions, not as a root CLI flag. Studio's separate, read-only
  `pi_single.ts` extension uses the official `before_subagent_spawn` hook to
  enforce `spawns: false` semantics on both task and eval agent() calls. The
  trusted-extension allowlist makes a failure to load this policy fatal and
  disables ambient extensions in single mode.
- `auto` sets `task.eager=preferred`.
- `swarm` sets `task.eager=always`, with batch/async enabled as mode defaults.
  Explicit native PI configuration remains authoritative for batch/async.

PI's project `.omp/config.yml` and optional Studio-state `agent/pi/config.yml`
control `task.maxConcurrency`, recursion, isolation, patch and merge policy.
Studio does not write these settings or impose a child-agent count limit. Only
two Studio parent tasks may run at once, with one writer per project, as before.
PI does not inherit Codex's 64-call cap. A PI foreground turn and subsequent
background-settlement wait each have a 30-minute operational timeout.

PI sessions persist per Studio task at `agent/tasks/<task-id>/pi/sessions`.
Continue calls official `open_session`, keeping the same task and session; engine
and collaboration selection stay fixed for that task. Cancel sends RPC abort,
stops/reaps the process group, cancels model streams/queued calls, and revokes the
one-hour task token. Restart marks unfinished tasks interrupted for explicit resume.
`prompt_result` is a yield; the adapter also waits for `session_settled` when PI
has background work. Child nodes use native lifecycle/progress events, including
nested parent relationships, native request counts, elapsed time, and errors.
No child percentage or per-child decode speed is inferred from text/usage totals.

PI `/compact` uses native RPC compaction. `/skills` lists the native skill
commands from `get_available_commands`; invoke them with the displayed
`/skill:<name>` command. PI plan and review use explicit instructions plus the
read-only project mount. They do not invoke Codex's collaboration/review RPCs.

The same bubblewrap boundary mounts the project read-only for read-only/plan/review
turns. A task-local loopback relay exposes only the internal OpenAI-compatible model
routes through a private Unix socket. A short-lived bearer token binds every PI
model request to its Studio parent and model instance. Missing PI or a provider
error never selects Codex automatically. Model identity changes require resuming.

## Concurrency and Decode aggregation

Save `Profile.max_num_seqs` (1/2/4/8 presets or a custom value) and reload the model
to change total model-service capacity. Launch has no temporary override. This is
separate from PI's delegation settings. Studio's inference proxy admits up to the
running profile's capacity and queues additional requests FIFO, across PI, Codex,
chat and API. It records actual proxy waiting time, not inferred vLLM scheduler
queue time; `queue_source=studio_proxy` makes this boundary explicit. Direct calls
to a vLLM port outside Studio are outside this measurement.

`GET /api/requests/aggregate?window_s=2&agent_task_id=<optional>` requires admin
access and returns rolling Decode rate, active/waiting requests, observed tokens,
quality/reason, and total/PI/Codex/chat/API buckets. Only full token-ID stream
observations count; each request's first batch is excluded. Missing IDs or usage
mismatches make the affected bucket and total unavailable while retaining valid
other buckets. Idle rates decay to zero; missing data is never estimated from
characters, stream chunks, or engine-wide counters. Samples live only in memory
and clear at Studio startup.

Agent SSE emits `decode_aggregate` on a periodic heartbeat as well as token updates,
so queue counts and decaying rates remain live between model calls. The global
header shows aggregate Decode next to GPU power; clicking the rate reveals
PI/Codex/chat/API buckets and active/queued requests. The PI task card scopes its
rate to that parent.
Request history and usage filters include source `agent`, with engine/task/swarm
attribution and `queue_s` in each request's metrics.

## PI validation

Run the tests in `test_pi_runtime.py`, `test_pi_proxy.py`, `test_pi_native.py`, and `test_decode_metrics.py`
with pytest-asyncio installed, then the existing Agent/observability suites.
`python studio/scripts/check-pi.py` checks browser controls/SSE and responsive
layouts after a frontend build (Playwright required). Its deterministic task double,
the RPC/proxy contract doubles, and `test_pi_native.py`'s CPU provider are **not** GPU throughput or model-quality proof. A release still needs the built pinned OMP against vLLM at capacity 1 and
>=2, confirming model requests overlap and reporting measured aggregate Decode.
