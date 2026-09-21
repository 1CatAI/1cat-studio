# Native creation workbench

`/creative` is the default creation entry. The Generate / Canvas switch keeps
`/canvas` and existing canvas documents accessible. Image and video drafts are
stored independently in the browser; artwork and run state remain server-side.

The workbench chooses the native recipe from the base model and reference roles:
Z-Image Turbo/Base for text-to-image, H3 FL2VA for text or keyframes, and H3 Ref2VA
for reference materials. Dependencies come from the SHA256-verified ModelScope
component catalog. No third-party media API is required for these recipes.

Preparation binds model choice, output specifications, assets, GPU UUIDs and
affected Studio services. A destructive service switch requires confirmation
and is checked again under the hardware operation lock. External GPU work is
left running while the creation waits. Download and load tasks remain associated
with the same persistent `creative_runs` record as the output.

Progress distinguishes model preparation from generation. Stage clocks and real
sampler counts are shown; no guessed overall percentage is displayed. A completed
native task is not a completed Studio artwork until its output passes media
validation and is stored. Cancel requests that arrive after generation finishes
must preserve the result.

Completed cards retain total elapsed time from the persisted task timestamps.
Details separate preparation/waiting from native generation time when the native
result records it. Older results fall back to the recorded generation-and-saving
interval; missing timestamps stay unknown rather than starting another clock.

The existing model picker exposes actual checkpoint/adapter combinations, not a
second panel of sampler knobs. The bundled H3 choices currently bind the verified
Comfy-Org INT8 ConvRot files to the base 20-update configuration or LightX2V Turbo
adapters: FL2VA 4step v1.2 768p, Ref2VA 4step v0.1, and both partitions' 8step v1.0
768p. The expanded model-file section names the source repository and exact files.
H3's native sigma-position count is one greater than the actual update count.

These bundled files do not restrict the picker to INT8. Other configured native
H3 services, including original weights without a transformer override, appear
with their own names, files and input capabilities. Their workflow defaults are
preserved. A task fails explicitly if that configuration changes after preparation.
Unverified variants retain the existing validation notice; file detection and
unit tests do not promote a model to hardware-verified status.

Native implementation: 1CatAI/1Cat-vLLM PR #585. GPU/model validation is recorded
separately from code capability. `creative_validations` entries may only be
promoted after real frontend output validation. Unverified adaptations are
labelled accordingly; image editing is not offered.

## Validation — 2026-09-09

Native media implementation tested at `48b375b84d`, with the ABI-matching SM70
extensions from `b6d91d61ff`. Changes are Python-only; CUDA kernels and GPU power
policies were not changed. Runtime: PyTorch 2.10 / CUDA 12.8, four V100 SXM2 32 GB
boards, with the P400 display device excluded. Image recipes use one V100.

All of the following were submitted from the actual Studio browser, played or
viewed, downloaded, and checked again after refreshing:

| Recipe | Output | Studio run |
| --- | --- | --- |
| Z-Image Turbo | 1024 × 1024 PNG, 8 sampler updates | `17a94274a60a4648884f9f51f4697897` |
| Z-Image Base | 1024 × 1024 PNG, 50 updates, CFG 4 | `2b705070190a4c37b8c58ccfcff1bc49` |
| H3 Turbo 4 text | 1344 × 768, 107 frames, 24 fps, audio | `4a050fab7b8a45508fb75a4dc5e0b228` |
| H3 Turbo 4 keyframes, via canvas | Same video specification | `360c902c3dc5487e8f0becd7ce83cd62` |
| H3 Turbo 4 reference image | Same video specification | `5b3d316bcb5747a48380febca74e89ad` |

Video duration is 4.458333 seconds; the picker labels this approximately 4.5 s.
Z-Image Base took about 10 minutes including loading in the browser acceptance
run. Turbo remains the default image recipe. These checks cover the default
output specifications, not every resolution/duration combination.

The Turbo browser run exercised a missing 467-byte ModelScope configuration:
one submission waited for the previous task, repaired the original component
folder, verified all weights, loaded the native model and generated the image.
It did not download another copy of the existing weights or use Hugging Face.
Image-to-keyframe and image/video-to-canvas reuse preserve the original asset ID.
Keyframes use a centered crop before native submission, keeping their proportions
and leaving source assets unchanged.

Progress on/off comparisons produced identical PNG bytes for Turbo and Base.
Warm H3 repetitions produced identical MP4 bytes, including uncompressed audio;
first-run audio differs from warm runs by at most 5.44e-7, also observed when
reporting stays enabled. Progress reporting does not account for that warmup
variation. Denoise progress is 4 actual updates for H3 Turbo 4.

Reliability checks include queued cancellation from both views, cancelling an
already running video while retaining its result, waiting for an independent
GPU process without interrupting it, manager restart tracking the same native
job ID, native numerical failure, and recovery without duplicate submissions.
The native image queue also has regression tests for corrupt saved records and
terminal disk-write failures, so these do not leave later requests stuck.

Backend creative tests: **57 passed**. Native media tests: **53 passed**, plus
native pre-commit/CI. OneCat frontend type check and production build passed.
Actual browser layout checks cover Chinese/English, light/dark, 320/390/768/1280/
1440 px, offline recovery, retained drafts, rapid detail dialogs and reduced
motion. The second audit additionally checks live detail progress, keyboard
previews and focus restoration, mobile canvas progress, and video asset playback.
Screenshots, browser recordings, complete task records and file hashes are kept
in the private deployment acceptance archive.

`creative_validations` is an evidence registry, separate from runtime import.
A verified entry includes `source_fingerprint`, exact `component_manifests`,
validated `workflows`, run IDs, output specifications and the native source SHA.
Changing the source fingerprint or component manifests invalidates verification.
Do not populate this registry from import checks, fixture tests or file presence.
