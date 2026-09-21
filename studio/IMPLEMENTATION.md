> Historical record of the pre-transition implementation. Current source and licensing: [SOURCE_ORIGIN.md](../SOURCE_ORIGIN.md).

# 1Cat Studio v1 implementation and validation

Base: unslothai/unsloth afeb2778f4a7e43dc3a0bdf2d2323b8dba6fd13d.
Owned branch: onecat/studio-v1. The reference checkout is separate.

The product is a Linux/browser inference workbench with one active model. It reuses
upstream UI primitives, sidebar, download progress, Markdown rendering/safeguards,
localization and SQLite conversation storage. The dedicated entrypoint does not
initialize upstream training, RAG, llama.cpp or media services.

The model provider is **ModelScope**, following the user's explicit correction.
There is no Hugging Face model-download fallback in the 1Cat entrypoint.

## Delivery gates

- [x] FastAPI management/auth, upstream chat persistence, profiles and runtimes
- [x] ModelScope search/download/resume/SHA256 validation and local model import
- [x] Automatic runtime installation and actual portable export/import
- [x] Owned systemd/process-group lifecycle, cancellation, logs and idle/autostart policy
- [x] OpenAI API proxy, reported token usage, TTFT and explicitly labelled rates
- [x] GPU telemetry, UUID-scoped root helper, reversible clock/power settings
- [x] Fixed-workload calibration, historical reference, matching and recommendations
- [x] Chinese/English, light/dark, chat/models/performance/service/setup/settings
- [x] Offline manager installer, database backup/restore and version rollback command
- [x] Focused tests, typecheck and production build
- [x] Browser workflows and real V100 integration

## Evidence, 2026-09-05

`pytest -q studio/backend/onecat_tests`: 23 passed. Coverage includes authorization,
CSRF and revocation; chat edit/import/backup/restore; request admission during
maintenance; real HTTP SSE Unicode/usage; owned child-process cancellation; safe
archive extraction; helper initialization/range/rollback; calibration matching and
crash journal recovery; ModelScope file-manifest validation and resume.

Production frontend build and TypeScript checking pass. The original syntax
highlighter produces a large-chunk advisory; its language grammars are lazy assets.
Browser checks pass for ModelScope search, language switching, dark/light themes and
all five content pages at 390px, with no horizontal page overflow or script errors.
Real remote browser tests passed streamed chat, token-rate display, history after
reload, edit-and-resend, rename and navigation. A regression check switches threads
during live generation and confirms that an old stream cannot overwrite the new view. The manager package installed successfully
in a new local prefix and as a remote systemd user service.

The automatic installer downloaded and verified 1Cat-vLLM 1.3.0, installed Python
3.12.14 and Torch 2.10.0+cu128 in an independent environment, and successfully imported
vLLM. Its actual 5.12 GiB offline archive was exported, imported into a second new
prefix and validated. No local development GPU was allocated for these checks.
ModelScope Qwen/Qwen2.5-0.5B-Instruct downloaded and passed every SHA256 check;
repeating the download reused its files and preserved a single library entry. The
remote machine also downloaded this model through ModelScope and verified all files.
Manager upgrade and rollback were exercised in an isolated installation prefix.

Remote YMZX integration used the existing PR417 a09e332bcd environment and
Qwen3.8-27B NVFP4 model with four V100-SXM2-32GB GPUs, TP4, half weights,
fp8_e5m2 KV, FLASH_ATTN_V100, CUDA Graph and max length 262144. The helper allowlist
contains only the four V100 UUIDs. The Quadro P400 is excluded.

Real OpenAI non-streaming inference returned HTTP 200 with usage. Disconnecting a
stream closed the upstream request and recorded status 499; a revoked API key
subsequently received 401. Switching from the existing systemd service to a
Studio-owned service succeeded without a second resident model.

## Calibration

Collector v2: exactly 8192 prompt / 1024 output tokens, greedy seed 42, thinking and
DFlash2/MTP disabled, unique cache salt, one warmup and three measurements per setting.
All measured outputs matched the reference token sequence. Prefill and decode use
isolated server metric deltas; arrivals are retained separately. GPU energy uses NVML
cumulative counters. W below is total average board power over the whole request.

| Setting | Average W | Prefill tok/s | Decode tok/s | Wh/request |
|---|---:|---:|---:|---:|
| Dynamic clocks, 300 W/card cap | 805 | 4478 | 77.80 | 3.351 |
| 900 MHz | 433 | 3174 | 59.15 | 2.393 |
| 930 MHz | 437 | 3252 | 60.70 | 2.351 |
| 975 MHz | 452 | 3407 | 62.80 | 2.351 |
| 1020 MHz | 470 | 3543 | 64.73 | 2.367 |

930/975 MHz are effectively tied in this workload; the recommendation logic prefers
the faster qualifying setting when energy means fall within observed measurement
spread. These figures cover GPU boards, not whole-machine power. Historical curves
remain explicitly labelled references and cannot automatically qualify a new model.

Both successful completion and an actual cancellation during the first warmup
restored the four GPUs to 300 W/card and dynamic clocks. Original DFlash2 service
configuration and model weights were preserved. Raw traces and contracts are stored
under the remote Studio data directory's benchmarks/RUN_ID folders.

## Issues found and resolved

- PR417 accepts integer CUDA_VISIBLE_DEVICES entries. Profiles store UUIDs, and
  launch resolves current driver indices. No vLLM source change was necessary.
- A collected transient systemd unit can return exit code 5 when stopped again;
  cleanup now treats an already-exited owned unit as stopped.
- The first collector performed a SQLite schema transaction for every token. Server
  metrics exposed a large consumer delay. Collector v2 checks cancellation at 200ms
  intervals and uses server decode timing; management schema initialization is now
  cached per database inode. The affected first run is excluded from recommendations.
- Direct remote Hugging Face access timed out during early verification. The user
  then selected ModelScope, and that provider replaced the original download path.
- Form labels now attach explicitly to their inputs, including native selects.

## Scope and operational limits

This is a single-user administrator workbench with separate inference API keys.
Existing arbitrary model/runtime combinations still require a compatible vLLM build.
Imported external Conda environments cannot be exported as portable managed runtimes.
The GPU helper can restore its own tracked clock policy; after outside clock changes,
initialize dynamic clocks again. See OPERATIONS.md for setup, access, rollback and
source distribution. No shared vLLM kernel or migration worktree was modified.

## Studio feedback revision · 2026-09-05

Model labels now separate base model and weight quantization from runtime suffixes.
The power page adds V100 power-saver (975 MHz, 150 W driver limit, prior measured
110–120 W target draw), balanced (185 W dynamic), and performance (300 W dynamic)
controls with asynchronous job completion feedback. A shared backend sampler reads
NVML every 0.5 seconds for live per-device power/VRAM and a two-minute trace; stale
values are explicitly unavailable. Request history moves timing and request-window
GPU energy into Service. Engine metric isolation verifies sample and token counts;
concurrent traffic falls back to local token-ID timing, excluding the full first
speculative batch. Cache hits are excluded from measured prefill tokens. Browser
network and message persistence timings are no longer presented as decode speed.
Request-window energy is a sampled estimate, not attributed per request under
concurrency. Old database rows survive the additive metrics migration.

Validation: 30 backend tests, including metric-probe cancellation and token-batch timing, concurrent metric
rejection, cache exclusion, telemetry gaps, V100 minimum-power constraints and
history migration; TypeScript and production frontend build pass.


## Studio 0.2 upgrade · 2026-09-06

This revision adds persistent launch reconciliation and global status polling, real
weight/graph stage counts, cancellation/retry, structured launch profiles, automatic
output length, hardware-gated precision/capabilities, and the versioned verified
ModelScope directory. QUASAR Qwen3.8-27B NVFP4 has a pinned ModelScope manifest and
1.5.0 release/PR evidence. GLM-5.3-Flash NVFP4 is visible under all verified models,
but its eight-V100 TP4/PP2 requirement prevents download/start in the current Studio.
Other family/processor evidence is retained in the audit without promoting an
unverified checkpoint. Runtime 1.5.0 is an explicit side-by-side installation option.

Image attachments are authenticated, persisted, replayable and portable in chat
exports/backups. Current PR417 vision acceptance covers one image per message;
the UI honors that stricter limit. Tool protocol remains for API clients; no tool
execution or Python execution is introduced. DFlash drafts use local paths and the
managed engine forces Hugging Face offline mode.

Streamdown animates new characters with bounded blur transitions, grouped Unicode
graphemes, reduced-motion support and an off switch. HTML/CSS/JS, SVG and single-file
React previews run in an opaque browser iframe with packaged dependencies. The
child CSP blocks resource/network access; the embedding document's CSP additionally
blocks external frame navigation. No generated code executes in the backend.
Incremental previews retain the last successful frame; unknown dependencies and
runtime errors remain inside the preview panel.

Real V100 acceptance on the existing PR417 runtime, TP4, FP16, FP8 E5M2 KV:

- Base, DFlash2 and MTP4 pass streaming/non-streaming tool calls and an image-color
  question. Initial requests revealed a one-image service limit; the capability
  record and UI use that limit.
- All three routes report 64 accepted output token IDs and 64 usage tokens.
  DFlash2 delivered them in 9 token-bearing SSE chunks, MTP4 in 15. These short
  requests validate counting/timing, not a replacement performance/efficiency curve.
- The measured stream arrivals agree with the independently isolated engine decode
  interval. The original profile is restored after the accelerator checks.
- A real cache-hit request reports cached tokens and excludes them from prefill
  throughput. Concurrent requests have no attributed prefill rate and label local
  decode observations. Disconnecting generation records 499 and no decode rate.
- A Studio generation request with automatic output produced **2295 output tokens**,
  confirming removal of the previous 1024 cap.
- Final request statistics hydrate and persist into the matching chat message;
  a late browser save cannot overwrite final metrics with an earlier pending result.

Migration and rollback were exercised using a separate installation prefix and a
copy of the live databases. Profile counts, credentials and conversation messages
were preserved, and rollback restored the original data exactly. The public manager
was upgraded while its existing inference process remained alive. Validation retains
the chosen inference environment and the 185 W/card policy; it does not switch to
1.5.0 or touch the display GPU.

Reproducible gates: `studio/scripts/check-browser.py`,
`studio/scripts/validate-inference.py --accelerators`, and
`studio/scripts/check-request-metrics.py`. GPU scripts are opt-in real workloads.

## Studio 0.2.1 · Preview and output-limit follow-up · 2026-09-06

The reported HTML reply ended with `finish_reason=length` at exactly 1024 output
tokens; its conversation had the legacy unmarked 1024 setting. A separate backed-up
migration and normalization at both chat-settings and Studio-proxy ingress prevent
stale browser tabs from restoring that default. Explicit manual limits and `/v1`
client requests retain their semantics. Truncated replies expose automatic-length
regeneration instead of looking complete.

The fresh production bundle did not reproduce the reported React #185 on opening
that transcript. Installed Radix Popper and DismissableLayer did contain the unstable
ref callbacks addressed by [upstream PR3967](https://github.com/radix-ui/primitives/pull/3967). Targeted dependency overrides replace
those callbacks, and per-block/preview boundaries keep rendering failures out of the
chat route. Desktop previews default to a resizable half-width sidebar; narrow
windows use an inline half-height pane instead of an automatic fullscreen overlay.
Regression coverage includes the reported truncated transcript, pane resizing,
continued composer use, and an intentionally failed preview-module download.

Regeneration now transfers the preview selection to the replacement assistant
message, including when the old preview is opened while request setup is pending.
The sandbox supplies frame-local in-memory localStorage/sessionStorage for generated
games without granting origin access. A real DFlash2 reply completed at 4179 output
tokens; its HTML is replayed as a timed stream in browser regression with the preview
open, tooltips exercised, and an unsent composer draft preserved.

## Studio 0.2.2 · Long reasoning and streaming UI

The reported conversation contains 78,848 reasoning characters and 11,046 answer
characters; its request was cancelled when the page failed. The incomplete answer
cannot become runnable merely by reopening a preview. Generation never selects a
preview unless the user has explicitly opened one.

Message rows and Markdown configuration now retain stable identities. Closed
reasoning is not rendered, and opening it renders existing text without animating
the entire history. UI paints coalesce at 50 ms while SSE parsing, saved text and
backend token metrics remain lossless. Periodic saves no longer block stream reads;
the final save waits for any earlier save to finish. The generation indicator stays
in the message header. User scrolling suspends following until returning to the end.

Each Markdown block uses Streamdown's native 280 ms blur animation with its own
prefix counter and unique processor-cache identity. Expired spans become plain
text, with consistent font metrics, instead of accumulating across the transcript.
Code fence controls keep their identity. An additional message boundary prevents
renderer/control errors from unmounting the stream and composer.

The production browser regression replays the exact captured text, opens reasoning,
scrolls upward, types an unsent draft, waits for completion, then explicitly opens a
preview. It verifies identical stored content, live blur animation, an unchanged
indicator node, zero animated spans after settling, no automatic preview, and no
React errors. A stress run recorded 6,572 peak animation spans and 0 after settling;
long-task observations improved substantially over the previous multi-second stalls.
These are browser replay measurements, not vLLM throughput measurements.

Run the regression with `ONECAT_REASONING_FIXTURE=/path/to/conversation.json python
studio/scripts/check-browser.py`; without a fixture it uses synthetic long reasoning.
The supplied conversation fixture is private and is not shipped in the source bundle.

## Studio 0.2.3 · Follow-up UI audit

Six targeted browser scenarios reproduced failures against the immutable 0.2.2
frontend and pass against this revision:

- Classic HTML scripts now execute in the opaque frame's global scope, so inline
  event handlers and successive script blocks share normal browser declarations.
  React/module blocks retain their explicit dependency allowlist.
- Failed preview candidates are disposed immediately. The last successful frame
  retains its error/navigation listeners until replacement or close. Duplicate
  ready messages cannot remove that frame or conceal a newer error.
- A closing Markdown fence updates completeness even when code text is identical;
  invalid completed code displays an error rather than waiting indefinitely.
- Switching chats cancels and detaches the old run immediately. Delayed setup or
  final saves cannot clear a destination draft or finish a newer run's UI state.
- An EOF without the stream completion marker is an interruption, not a successful
  answer. Received text is saved, the reader is released, and a persistent message
  notice explains interrupted or cancelled output after reopening history.

`python studio/scripts/check-ui-audit.py` runs seven CPU-only browser regressions,
including an extra delayed old-request/new-request overlap check. To compare with
an older build, set `ONECAT_FRONTEND_DIST` to its frontend/dist directory. The full
`check-browser.py` replay remains the guard for animation, exact streamed text,
scrolling, manual split preview, sandbox boundaries, themes and mobile layout.
No vLLM runtime, GPU settings, token accounting or database schema changed here.

## Studio 0.2.4 · Streaming code and rolling token usage

New code lines use a 280 ms blur/opacity animation through the Web Animations API.
Completed lines retain their syntax colors and do not replay their animation when
the highlighter refreshes. No character wrappers are added; observers are scoped to
code fences, and animations are released on completion/unmount. Both the animation
preference and reduced-motion setting apply. Copy and persistence keep original code.

Conversation following observes actual content and viewport sizes, including late
Markdown/highlighter layout and preview resizing. Only explicit upward reading or
scrollbar actions pause it; reaching the bottom or Jump to latest restores following.
Layout-generated scroll events no longer silently disable it.

The Service request tab adds rolling 1/3/7-day input, output and prefix-cache totals.
`GET /api/requests/usage?days=1|3|7` aggregates all ended Studio/API requests by start
time using an indexed timestamp query; the 200-row request-history cap does not
apply. Counts use available service usage, including any reported counts from
interrupted requests. Unknown values have per-field coverage, rather than being
presented as zero. Cache hits are a subset of input, never an additional output.
Per-request cache counts are now retained even during concurrency or a full cache
hit, independently of whether prefill/decode timing can be isolated. Old records
without collected cache counts remain explicitly unmeasured.

Validation: `check-code-usage.py` replays a 360-line code response and checks actual
animation, settled older lines, deferred-height following, manual scroll/jump,
animation preferences, exact saved/copied text and token counts. It verifies all
three usage periods, missing-data labels and mobile layout. Backend tests cover
more than 200 records, rolling windows, excluded running requests, invalid/missing
counts, full cache hits and concurrent enrichment. No inference runtime is switched.

## Studio 0.2.5 · Follow-up reading and usage audit

The audit reproduced two scroll regressions against 0.2.4: opening a long reasoning
section jumped to its end, and a two-pixel upward trackpad gesture was classified
as a return to the bottom. Expanding reasoning now pauses following before layout
changes. Resuming depends on downward intent/travel as well as bottom distance;
explicit upward reading remains paused inside the bottom tolerance. Jump to latest
and downward wheel/touch/keyboard gestures still restore following. Native scrollbar
dragging is also covered using Chromium with visible native scrollbars.

Usage aggregation now checks the original JSON numeric type for cached tokens.
SQLite exposes JSON booleans as SQL integers, so checking only the SQL value type
could turn an invalid `true` into one token. Invalid values stay unmeasured. Additional
tests cover exact rolling-window endpoints and the timestamp index query plan.

The three reading scenarios can be run independently with
`ONECAT_UI_AUDIT_ONLY=inspect_reasoning,native_scrollbar_reading,small_upward_gesture
python studio/scripts/check-ui-audit.py`. The full script retains earlier preview,
cross-thread and interrupted-stream regressions. Code animation and token-count
semantics are preserved by the long-code and long-reasoning browser replays.

## Studio 0.2.6 · Live decode, stable controls and discoverable model support

Studio streams now carry request-ID-associated live telemetry between complete SSE
events, at most every 250 ms while new upstream events arrive. Counts come from
actual emitted token IDs, including reasoning; speculative chunks contribute their
accepted/output IDs, never a chunk count or draft count. The cumulative arrival
rate excludes the first batch and its initial wait, and requires a 100 ms interval.
Missing IDs, multiple choices or insufficient timing show no inferred rate. The
header labels this as a server-local stream observation; completion replaces it with
the existing final usage and attributable engine statistics. External OpenAI API
streams do not receive the Studio-only telemetry events.

Preview button lookup previously keyed by exact current source text while Streamdown
could still display a deferred older source. That mismatch repeatedly removed and
reinserted the preview button, shifting the entire action footer. Lookup now uses
the stable Markdown block position and artifact ordinal. A production-build replay
fails the old build and passes this one with zero preview-button removals and stable
preview/copy DOM identities. Identical code blocks with different adjacent CSS also
resolve to their respective preview artifacts.

The model library opens on a searchable Supported models inventory with 16 exact
model/configuration records across Qwen3.5, Qwen3.6, Qwen3.8, GLM and DeepSeek.
Evidence is drawn from releases 0.0.3, 1.2.2, 1.3.0 and 1.5.0 and linked PRs,
including #137, #212, #270, #341, #344, #389, #415, #445 and the post-release
#454/#456/#458/#487/#503 lanes. Post-1.5.0 source requirements are explicitly
identified. PLE, PP and unavailable-runtime requirements stay visible, and support
records without an admitted recipe cannot bypass download validation.

Six additional ModelScope checkpoint manifests (file sizes and SHA256 digests,
including inspected config.json files) cover QuantTrio Qwen3.6 27B/35B AWQ,
QuantTrio Qwen3.5 122B AWQ, and official Qwen3.6 27B/35B FP8 and Qwen3.8 27B FP8.
The full directory holds eight target manifests and one companion-draft manifest.
Only model metadata is preloaded; weights remain on-demand. GGUF and unverified
generic search results remain excluded. Model evidence is not extrapolated to a
different quantization or to every runtime descended from a validated release.

Validation includes live counted batches/missing IDs, private SSE framing and
request identity, final-statistics replacement, duplicate artifact targeting,
stable streaming controls, catalog evidence/manifest gates, and desktop/mobile
inventory filters. Browser streaming replays preserve animation and exact saved and
copied code; no vLLM runtime or GPU clock change is required for this update.

## Studio 0.2.7 · Hardware-wide live monitoring

Live GPU cards, memory totals, header power and the two-minute power history now
cover the full NVML hardware inventory, independently of model assignment. The
API's `gpu_uuids` describes monitored hardware; `model_gpu_uuids` is a separate
annotation for loading, running, stopping and temporarily unavailable services.
Changing a preset or stopping a model does not reinterpret historical samples.
Per-request energy integration and power-mode controls retain their model scope.

Unsupported sensors remain unknown. For the YMZX inventory of four V100s and a
Quadro P400, power is explicitly a measured subtotal with 4/5 coverage; the P400
still has a card and contributes its 2 GiB to the 130 GiB memory capacity. The API
preserves a null `power_w` total when any sensor is missing, while adding separate
`measured_power_w` and sample-level coverage for the chart. Transient driver
exceptions no longer terminate the sampler, and stale readings remain paused.

Validation: 21 targeted telemetry/observability tests pass, including full hardware
with one/no model GPU, partial sensors, history scope, stale samples, driver error
recovery and unchanged request-energy accounting. TypeScript and production build
pass. `check-hardware-monitor.py` validates five persistent card nodes through
1/2/0 model assignments, measured power, total memory, stale/reconnect behavior,
Chinese/English, desktop/mobile and light/dark layouts without GPU inference.

## Wakeable V100 idle clocks · 2026-09-07

For V100 inference processes on Linux driver 580.105.08 or newer, Studio adds
NVIDIA's supported `CUDA_DISABLE_PERF_BOOST=1` launch environment unless the
selected runtime explicitly overrides it. The model and KV cache remain resident;
the driver can choose the 135 MHz idle clock and boost back to 1530 MHz when work
arrives. Fixed-clock power presets still retain their explicit clock policy.

An isolated A/B on YMZX GPU1 used the same 1Cat-vLLM 1.3.0 environment,
Qwen2.5-0.5B model, 26.3 GiB allocation, 47-token input and 160-token output.
After warm-up, baseline versus heuristic-mode median TTFT was 54.4/53.4 ms and
total latency was 561.1/560.4 ms. Heuristic mode returned from 1530 MHz and about
69.5 W to 337 MHz/42.7 W at 11.6 seconds, then 135 MHz/41.7 W at 13.7 seconds.
Baseline remained at 1290 MHz/56.4 W. A production same-prompt check after 16
idle seconds measured the low-clock request at 87.9 ms TTFT / 816.7 ms total and
the immediately following high-clock request at 42.7 ms / 540.5 ms. The policy
therefore preserves peak clocks and steady throughput but pays a cold ramp after
roughly 14 idle seconds. The test service was isolated to GPU1 and its original
150 W automatic-clock policy was restored.

The V100 board exposes only one supported HBM clock (877 MHz) through both NVML
and `nvidia-smi`; its memory and graphics VF-offset ranges are zero. A model-resident
card and an empty card both settle near 41 W at 135/877 MHz, so reducing KV
allocation does not lower this hardware floor. NVLink low-power threshold, link
width and bandwidth-mode queries all return unsupported on NVLink 2.0. NVIDIA's
open kernel module requires Turing's GSP and cannot run Volta; Nouveau does not
provide Volta engine/memory reclocking or the CUDA stack required by vLLM.

## Studio 0.2.8 · Downloadable models and default launch profiles

Frontend follow-up (2026-09-06): model cards display the download worker's measured
byte rate, polling the existing task endpoint every second only during active
downloads. B/s, KB/s, MB/s and GB/s keep low rates readable. Missing or older-than-five-second
samples, cancellation and file verification show “—”; network failures cannot
leave an old rate looking live. The browser regression covers changing rates,
zero/missing samples, frozen requests, cancel/verify phases, mobile and English.
TypeScript and the production build pass. This frontend-only update can be applied
by retaining existing hashed assets and atomically replacing the entry document
after backing it up, without restarting Studio or its active downloads.

Verified ModelScope downloads no longer depend on installed inference runtimes,
GPU count or whether Studio can express the launch topology. Revision, runnable
weight format, exact file manifest, disk capacity and SHA256 validation remain in
force. Launch compatibility is still checked on profile creation/save and startup.
The main model grid now contains eight concrete downloadable checkpoints with
default settings; the eight additional source-only/support records have their own
clearly labelled disclosure, with evidence links instead of disabled download buttons.

Model cards show download progress, cancellation, failure/retry and downloaded
state across refresh/navigation. Admission deduplicates concurrent requests for
the same checkpoint/revision even when callers select different runtimes. A
completed target download creates its default profile when compatible; otherwise
the successful download records why the profile is pending. Defaults select a
documented installed runtime and the required V100 UUIDs, excluding display GPUs,
and carry FP16, KV dtype, context/batch limits and automatic output length. Both
model cards and local-model profile creation use the same backend recipe. Starting
from a card creates/reuses that profile and invokes the existing load operation;
downloading never starts a model or switches an inference environment. User edits
to generated profiles are retained; repurposed profiles cannot launch the wrong
model from the original card. PP2 recipes remain explicitly unavailable for
one-click launch and retain their hardware requirements.

Validation: 80 backend tests pass, including new runtime-independent download,
concurrent admission, default creation, preserved edits, model identity and strict
launch gates. TypeScript/build pass. The CPU-only browser flow exercises download
without a compatible runtime, prefilled settings, cancel/retry, progress across
reload, runtime preparation, default-profile creation and launch argument
validation, local-model defaults, mobile layout and English. Model inventory
regression passes. All eight ModelScope file manifests were fetched online and
matched their pinned entries; no compatibility is inferred for untested runtimes.

The public-site download check transferred approximately 4 GB through ModelScope
with the original inference PID still ready. It exposed a Hub thread-pool shutdown
that could remain stuck after SIGTERM. Cancellation now gives file-transfer jobs
five seconds to exit cooperatively, then reaps only the matching cancelled Studio
download worker, verifying PID creation time and worker arguments. GPU and runtime
installation jobs never use this escalation. Partial files are retained for resume;
the job becomes cancelled only after the process has exited. Additional tests use
a real stubborn CPU subprocess and check inference/install/PID-reuse exclusions.

Resume/retry callbacks replay existing offsets in ModelScope Hub. Download progress
therefore counts retained file extents rather than adding callback sizes to the
initial file size. Complete files, `.incomplete` files and parallel range/merge
files are counted without overlapping bytes, excluding unrelated cache metadata.
Tests replay resume offsets twice and verify that progress and speed remain
unchanged until new bytes are actually written. The child-cancellation test also
handles a child disappearing during its process-status read without a flaky
check-then-read race.

## Startup failure diagnostics · 2026-09-07

Engine failures now select the causal CUDA, NCCL, process-signal or Python exception
from up to the last 4 MiB of the vLLM log instead of persisting only the final 2200
characters. This avoids presenting `Engine core initialization failed` and leaked
shared-memory cleanup warnings without the exception that preceded them. Before a
new launch reuses `engine.log`, Studio stores a per-launch archived copy; failed
attempts are also archived after their cgroup has stopped.

Both persisted errors and log API responses redact the vLLM API key by value and by
common argument/config syntax. This remains effective after a stopped engine has
dropped its in-memory key. Targeted lifecycle tests cover causal extraction,
archive naming and credential removal.

## 1Cat hand-drawn mark · 2026-09-07

The web favicon, sign-in screen and collapsible navigation brand now use the
provided hand-drawn 1Cat JPEG directly. Multiply blending removes its white field
on light surfaces; dark mode inverts the image and uses screen blending so the
original mark remains intact on dark surfaces. Versioned asset URLs prevent an
already-open browser from retaining an earlier icon.

## Source-overlay runtime defaults · 2026-09-07

Runtime inspection now records the installed `1cat-vllm` distribution version in
addition to `vllm.__version__`. This matters for the validated PR417 source overlay:
the imported module reports `dev`, while its installed distribution and Python
environment are 1Cat-vLLM 1.3.0. The exact PR417 config and Python-tree hashes map
that overlay to its 1.3.0 compatibility baseline; unknown source trees cannot
inherit the installed wheel's catalog permissions.

On startup, Studio refreshes older runtime records that lack distribution metadata
and creates missing default profiles for already-downloaded verified checkpoints.
This repairs downloads that previously ended with a false “Requires runtime” reason,
so a compatible card can immediately switch models through its default TP/GPU,
FP16, KV, context and automatic-output settings.

## Stable streaming controls and telemetry · 2026-09-07

Live decode rate and emitted-token count now occupy the same message-footer slot
as the completed request timing. During generation, the active assistant footer
is an absolute overlay owned by the stable conversation shell rather than the
growing Markdown message. A same-height spacer reserves its final message position;
tabular digits and reserved metric width keep token updates from moving the
copy/regenerate controls. The product upgrade below keeps the latest response
controls docked after completion as well; older responses retain their in-message
statistics.

Preview/run controls render before their code block. Growing and re-highlighted
source therefore extends below a stable control instead of pushing the button on
every stream paint. The active markdown block also disables off-screen intrinsic
size substitution, which avoids height corrections when streamed code crosses the
viewport while retaining incremental text and code-line animations.

## Qwen profile capabilities and MTP controls · 2026-09-07

Qwen3.6 and Qwen3.8 verified catalog profiles now use their declared 262,144-token
text context instead of the former 32K placeholder. A backed-up, one-time migration
updates untouched catalog defaults; custom profiles remain unchanged. The exact
Qwen entries expose their qwen3_coder tool parser and single-image multimodal path,
while unsupported model/runtime pairs retain disabled controls with separate tool
and vision reasons.

Selecting MTP in the basic profile editor exposes an explicit 1/2/3/4 speculative
token selector. The backend admits the same four integer values and rejects missing,
boolean or out-of-range values, so raw JSON cannot bypass the UI contract. Long
reasoning keeps its blur transition but groups arriving text by words; answer text
and generated code retain character and line animation. The full browser replay
reduced the synthetic long-reasoning peak from more than 20,000 animation nodes to
988, with no settled animation nodes or generation-status remounts.

## Bundled GPU controls · 0.3.1 · 2026-09-08

The installer configures the included GPU helper automatically and detects local
GPU UUIDs. Root authorization uses sudo or the operating system's desktop prompt;
the manager remains available during authorization. Upgrades preserve existing
allowlists. Configuration validates sudoers before installation, installs root-owned
files atomically and rolls back failed writes. No clocks or power settings change.

Settings and Setup share an activation card with live authorization state, retry
and the detected/authorized GPU counts. The authenticated setup API accepts no
caller-selected paths, commands or passwords. Duplicate clicks reuse one job;
the worker verifies actual helper access before reporting success. Missing sudo,
declined authorization and restricted policies retain explicit states. Manual UUID
and command instructions are removed from the normal UI. Installer, privilege
boundary and browser interaction regressions cover the change.

## Product experience upgrade · 0.3.0 · 2026-09-07

Chat generation now belongs to a backend task with a durable message and bounded
event replay. Browser routes, thread switching, refreshes and network reconnection
resume observation without aborting or duplicating inference. Explicit stop still
cancels; a manager restart preserves partial output and marks interruption clearly.
Existing streaming routes remain compatible and all request timing still comes
from the shared proxy. The latest response toolbar keeps the same docked DOM node
through completion. Drafts, thought expansion, preview width, model filters and
form state survive their relevant navigation boundaries.

Discovery now has three tabs: Discover, Downloaded and Launch profiles. Compatible
hardware recipes appear first; runtime installation and hardware requirements are
distinct from download availability. Compact cards expose one next action and
expandable verification details. Profile editing has fixed actions, basic/advanced
fields, MTP segments, draft download status and pending-versus-running differences.

Power cards share a power-limit headline; measured ranges and matching calibration
results are separate. Service usage and request history share time/model/source
filters, with input/output/cache trends and coverage. Input totals explicitly
include cached input. Completed startup stages collapse. Sidebar grouping/pins,
chat suggestions, actual accelerator labels, settings feedback and responsive
controls complete the shared visual changes while preserving stream animation and
preview isolation. See `PRODUCT-UPGRADE.md` for checks and known limits.
