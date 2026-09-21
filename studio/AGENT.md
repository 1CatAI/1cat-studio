# Agent mode

The Conversations navigation entry contains a top Chat / Agent mode switch.
Each mode retains its recent history, draft and last selected conversation or task
in the current browser tab. Switching modes does not cancel background work.
Use the chat toolbar's "Create an Agent task from this chat" action to explicitly
include a conversation as task context; ordinary mode switching does not copy it.
Existing /chat?thread= and /agent?task= links remain supported.

Both modes share Studio's model management. Agent mode
uses the unmodified official Codex 0.153.4 app-server, bundled by prepare-agent.py.
There is no separate Codex login and no access to the operator's CODEX_HOME.

## Use

Create a project (empty, upload files/folders, or import a public GitHub HTTPS repository).
Choose a downloaded model from the header or composer; Studio selects its matching
launch recipe (and requires tool calling for Agent). Describe the task,
and inspect activity, files, diffs and optional code preview. A task keeps its own
Codex thread and model instance. Refreshing or leaving the page does not stop it.
Stop terminates only the task's isolated process tree. Continue resumes the saved
Codex thread; Studio restarts mark active work interrupted, with partial results
retained. One task may write a project at a time, and at most two projects run at
once. A turn is limited to 30 minutes / 64 model calls, with explicit failure and
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
