# Independent Studio source validation — 2026-09-21

Baseline: main `4b7a44245e860703a22bcaeed57cdfe595bcecce` (deployed 0.4.0 source).
The replacement is developed on `refactor/independent-studio` in an isolated
worktree. Production services and user databases are not used by these checks.

## Results

- Backend: **414 passed** with Python 3.12 and automatic GPU actions disabled.
- Frontend: **33 passed** with Node.js 22.16.0; TypeScript and production Vite build passed.
- `check-source.py`: no forbidden imports or application license/header conflicts;
  release source is restricted to the explicit packaging allowlist.
- A source archive was extracted outside Git, its frontend was installed with
  `npm ci --ignore-scripts --offline`, and rebuilt successfully. A fresh Python
  virtualenv was synchronized from `requirements.lock` with hash verification.
  Health, first-account setup, conversation creation/save/read, frontend assets,
  application license and third-party notices passed in that environment.
  `unsloth`, `storage`, `auth`, `utils` and `loggers` are absent from its imports.
- `check-browser.py`: preset editing, hardware gating, streaming generation,
  previews (HTML, React, Lucide, Recharts, Tailwind), sandbox isolation, failure
  recovery, draft preservation, history pinning, mode switching, startup-state
  navigation, English and dark mode passed.
- `check-download-flow.py`: progress and speed, cancellation/retry, reload,
  missing-runtime handling, default profile launch, local defaults and mobile /
  English rendering passed using tiny fixture weights and a CPU-only launcher.
- Light/dark browser snapshots compare chat, model lists, preset dialogs,
  creative, settings, service and download-progress pages at 1500×1000. Layout, control dimensions,
  Markdown palette, code blocks, dialog overlay and spacing were calibrated
  against the baseline. This is not a claim of pixel identity.

- The full Linux x86_64 offline bundle also built successfully, including managed
  Python, locked dependencies, frontend, Codex, uv, the corresponding source and
  licenses. Bundle build results are recorded in the local packaging artifacts.

## Test-harness corrections and compatibility fixes

The browser probe initially measured the status marker before asynchronous
navigation to a newly created conversation completed. Both baseline and new
implementation failed that same assertion. Waiting for the destination thread
before checking its DOM stability preserves the assertion; both versions then
passed. The live-mode-switch fixture likewise now waits for the thread URL.
The download fixture now intercepts the model-switch scheduler as well as the
older job entry point, keeping the documented CPU-only launcher in use.

Existing stored settings include `update_channel`; their strict schema did not,
which made unrelated settings saves fail in the baseline. The schema now
accepts that persisted field. GPU reconciliation unit fixtures explicitly
simulate automatic actions while continuing to mock job creation and hardware.
No host GPU actions were enabled to make tests pass.

New compatibility checks cover a pre-existing password digest generated with
OpenSSL, an older chat schema, unknown data preservation, historical deletion
markers, cross-conversation message-ID collisions and atomic rollback.
Packaging tests cover stale builds, archive reconstruction and escaping symlinks.

## Intentional visible differences

The legal text/link identifies the new application license. Hellix font files
have been removed because redistribution authorization was not available in the
repository; headings use the existing Space Grotesk fallback (OFL-1.1). Some
English heading glyphs consequently differ. Application pages and workflows are
retained; this change does not introduce a new interface design.

## Scope

These checks cover the application and mocked engine boundaries, not live model
quality, real GPU performance, physical hardware control or third-party cloud
availability. Source-overlap checks and retained dependency notices support a
license review but cannot certify copyright ownership or legal enforceability.
The historical AGPL release remains licensed under its historical terms.
