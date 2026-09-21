# 2026-09-21 deployed source reconciliation

GitHub `main` previously ended at `5c67bcf` (2026-09-11), while the
`0.4.0-dadd5b5343` bundle published on 2026-09-17 included newer source.
This synchronization records that deployed source in Git, including the
existing `0ec6034` commit raising the Qwen3.8-27B NVFP4 preset's recommended
GPU memory utilization to `0.92`.

Before adding this note, all 5,221 regular files in the release's source
archive matched the working tree byte for byte. The archive SHA256 is
`ec828996a1974b8050d3ea733681c4b54de50798fa229b987aed60bfbbdc7e1f`.
The release suffix is a package source hash, not a Git commit identifier.
This reconciliation preserves the deployed implementation; it does not
create or install a new release.

## Included changes

- GPU activation rebinds missing GPU UUIDs in delivered presets and removes
  stale GPU policies. Installation runs activation after GPU authorization.
- Presets can follow an available validated runtime when their original
  runtime is missing or unvalidated. Local model validation records can be
  inherited within a runtime's major/minor family when model files match.
- Update-channel settings, administrator endpoints, and publish/apply scripts
  support detached updates, installation status, and rollback after a failed
  post-install health check. The installer passes the installation prefix to
  the application.
- Startup can schedule the autostart preset from either stopped or failed
  engine state.
- Number fields keep an editable text draft so users can clear an input.

## Known issues retained from the deployed bundle

The following were reproduced during the source audit and remain open:

1. `NumberField` truncates decimal values: editing Temperature to `0.7` or
   Top P to `0.95` results in `0`; GPU memory utilization `0.92` becomes
   `0.1` on blur after minimum-value clamping. This concerns edited values,
   not every previously stored preset.
2. The update applier stops the engine before extraction and installer
   preflight. If installation is rejected because tasks remain active,
   it returns failure without restoring the engine.
3. Resolving a model can persist a replacement runtime before model-weight
   validation fails. Runtime fallback can cross major/minor families when
   no validated runtime from the original family is installed.
4. A resumed download appends a full HTTP 200 response when a server ignores
   Range, causing checksum failure and removal of the partial download.

Runtime validation inheritance is compatibility bookkeeping, not a new
quality or performance acceptance result. Update endpoints and scripts are
present, but the OneCat frontend does not yet expose the update workflow.

H3 prepared-loading work remains separate in Studio Draft PR #28 and
1Cat-vLLM Draft PR #593; it is not part of this deployed source snapshot.

## Validation

- Backend management, conversation, upgrade, packaging, runtime, lifecycle,
  and model-download tests: 75 passed (Python 3.12).
- Existing `tests/onecat-*.test.ts` frontend tests: 31 passed (Node 22.16.0).
- `npm run typecheck:onecat` and `npm run build:onecat`: passed. The build
  reports a non-fatal warning about large output chunks.
- Python syntax checks for the 11 changed/new Python files and
  `git diff --check`: passed.

These checks do not resolve the known issues above or establish GPU inference
performance. No production update, GPU activation, or model generation is
performed as part of the source synchronization.
