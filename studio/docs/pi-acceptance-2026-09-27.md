# PI/Codex integration acceptance — 2026-09-27

Implementation base: `d415756691e0cb51f4fb72b8761338032d12b483`.
The changes are in the Studio working tree. The checks below distinguish
protocol/UI correctness from GPU throughput; local publication is recorded separately.

## Fixed runtime

- OMP source: `061f21ef011c72df891678ce489b02edee676738` (18.3.2).
- Builder: Bun 1.4.2, Linux x86_64. The upstream checkout remained clean after
  the build; no PI source patches were applied.
- Compiled `omp` SHA256:
  `ba8162a8c780e565865297a430025236583698ea992e8a63f8101886d3654786`.
- Protocol: official `omp-rpc` 0.1.0 client from that commit, RPC v2.
  All vendored Python files match the hashes in `UPSTREAM.json`.
- Native addon: official `pi-natives-linux-x64@18.3.2`, SHA512 pinned in
  `scripts/prepare-pi.py`. The published native package, Rust crates, Cargo
  lock and Bazel inputs match those in the audited OMP commit. Its publication
  source and archive integrity are recorded in `onecat-component.json`.
- Runtime status reports `installed=true`, `ready=true`, with the outer
  bubblewrap sandbox available.

## Completed checks

| Area | Result |
| --- | --- |
| Full backend suite, including Agent, conversation, chat, observations, thinking, usage, packaging, RPC, proxy and Decode contracts | 479 cases passed |
| Actual pinned OMP, official client, bubblewrap and real HTTP proxy, using a deterministic CPU provider | 8 native cases passed (included in the full backend suite) |
| Frontend unit tests | 34 cases passed |
| Frontend production build | Passed; existing large-chunk advisory remains |
| Browser acceptance | Saved engine/mode selection, unavailable PI, Codex mode reset, nested child events, reconnect, cancellation, Decode decay after completion, stale-data rejection, global header status and 320–1440 px layouts passed |
| Compact Decode status | Agent/chat/service/models pages, clickable bucket breakdown, keyboard focus return, mobile popup placement, idle/missing/error states passed |
| Simplified Agent composer | Repeated readiness panel removed; compact model picker, contextual hint, draft preservation, focus and long-model-name layouts passed at 320–1440 px |
| Readiness browser regression | Drafts, project creation, failed model state, empty-filter recovery, profile menu, optional setup, H3 catalog parity, canvas controls and responsive layouts passed |
| Source boundary, changed-file Ruff checks, vendored hashes and whitespace | Passed |

The readiness script now uses the invoking Python interpreter and verifies
H3 preset names/count against the catalog, replacing its stale hard-coded
four-preset expectation. The full script passes.

## Self-audit fixes

- Refreshing or navigating away from a new task preserves its chosen PI/Codex
  engine and collaboration mode. Existing tasks use their own saved settings.
- A failed Agent status refresh blocks starting a task with stale readiness;
  a failed task-metrics refresh shows unavailable data instead of an old speed.
- PI's internal bridge uses the actual CLI listen host/port, including wildcard
  IPv4/IPv6 bindings, rather than assuming the saved port and IPv4 loopback.
- Explicitly rejected PI steering instructions can be retried with the same
  request ID. An unconfirmed RPC timeout still blocks duplicate delivery.
- Restart recovery marks unfinished PI child nodes interrupted and clears the
  stale turn ID while retaining completed children, progress and request counts.
- Cancelling a queued request wakes its capacity waiter immediately.
- Aggregate `observed_tokens` uses the same rolling window as Decode speed;
  the first batch remains observable but excluded from Decode tokens.

The new backend regressions reproduced the original faults before their fixes.
Final regression: 479 backend cases, 34 frontend cases, production build,
PI browser checks and readiness browser checks passed. GPU throughput remains
subject to the separate acceptance below.

## Local publication

The audit fixes were published to the existing local Studio service on
2026-09-27. Before restart, the request/Agent/chat records contained no active
work; the management database and previous frontend entry point were backed up.
Only the Studio management service was restarted.

After restart, local and public health checks passed, both Agent engines reported
ready, and the aggregate endpoint reported idle with zero active/queued requests.
The served entry point and its JS/CSS matched the verified build byte for byte
on both routes. The entry bundle is `index-CKzfipkQ.js`.

The native cases cover:

- Single mode blocks child dispatch through both `task` and `eval agent()`.
- Auto and swarm dispatch native children; capacity 1 queues requests and
  capacity 2 admits two overlapping child streams.
- Session continuation preserves the native session ID.
- Cancellation stops the swarm and releases active/queued proxy requests.
- Project skills are discovered and invoked through native RPC.

The proxy/measurement cases additionally cover task-scoped bearer credentials,
model-instance binding, queued-client disconnection, first-batch exclusion,
duplicate request IDs, missing token IDs, bucket attribution and restart reset.

CI now builds the pinned PI component and installs/checks the sandbox before
running the backend suite. Native acceptance cannot silently skip in that job
because a missing runtime or sandbox fails the preceding readiness step.
This workflow change has been inspected locally; GitHub-hosted CI has not run
as part of this working-tree task.

## Reproduction

From `studio/`, with the project dependencies and pytest-asyncio installed:

```sh
python scripts/prepare-pi.py
PYTHONPATH=backend python -m pytest -q \
  backend/onecat_tests/test_pi_native.py \
  backend/onecat_tests/test_pi_runtime.py \
  backend/onecat_tests/test_pi_proxy.py \
  backend/onecat_tests/test_decode_metrics.py
cd frontend
npm test
npm run build:onecat
cd ..
python scripts/check-pi.py
```

The browser check requires Playwright/Chromium. `ONECAT_BROWSER_EXECUTABLE`
can select an existing Chromium binary. Full regression also includes
`test_agent`, `test_conversation_flow`, `test_observability`, `test_token_usage`,
`test_thinking_controls`, `test_chat_output_limits`, `test_chat_runs`, and
`test_packaging`.

## Remaining GPU acceptance

Actual vLLM throughput is **not yet validated**. During the attempt, other
workloads occupied GPUs 0–3 and held the GPU 4–7 ownership locks. The test
launcher declined the occupied lock before loading a model or issuing requests.
No existing service was stopped or reconfigured.

The local continuation artifacts are under `/tmp/onecat-pi-gpu/`: the isolated
launcher `run.py`, ownership/log files, and a downloaded Qwen3-0.6B model whose
weight SHA256 is
`f47f71177f32bcd101b7573ec9171e6a57f4f4d31148d38e382306f42996874b`.
The temporary launcher targets the installed 1Cat-vLLM 1.3.0 runtime, PyTorch
2.10.0+cu128, CUDA 12.8, GPU 7, FP16, TP1, context 16384, eager execution,
FLASH_ATTN_V100, prefix caching off, thinking off and an explicit 1024-output-token
test budget. It restarts its own service at capacity 1 and 2 and compares one
versus two real PI parent requests. This complements the native same-swarm CPU
checks; it does not establish autonomous delegation quality for production models.

When the ownership locks are free, from `studio/`:

```sh
PYTHONPATH=backend /tmp/onecat-pi-tests/bin/python /tmp/onecat-pi-gpu/run.py
```

Keep measured TTFT, per-request Decode and the two-second aggregate separate.
Production release acceptance still requires the selected production model's
same-swarm capacity-1/capacity-2 run and actual overlapping, complete token-ID
streams. The CPU provider's synthetic timing is never a GPU speed result.
