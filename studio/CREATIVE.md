# Creative canvas / 创作画布

The `/canvas` page extends the existing Studio shell, authentication, job workers,
runtime registry, theme and motion preferences. It does not start models on page
load or manager startup, or modify power/clock settings.

## Using the canvas

1. Create a canvas, add text or upload PNG/JPEG/WebP, MP4/WebM, WAV/MP3 references.
2. Add a generation node. Select a workflow then a compatible service, or select
   the service first to match its workflows. Configure services in **创作模型**.
3. Connect a reference's **引用** button to a generation node's **输入** button;
   the inspector also provides a keyboard/touch-accessible reference selector.
   Connection order is the first/last-frame order. Remove connections in the inspector.
4. Explicitly load/connect the selected service, then generate. The result becomes
   another asset on the board. Connect it to another generation to continue editing.
5. Use the task tray to inspect elapsed time, real reported stages, errors and saved
   results. You may close the page or restart the manager while workers continue.

Native video/audio validation requires FFmpeg and FFprobe in the server PATH. Both
are installed on the current eight-GPU deployment. They inspect media on the CPU;
the canvas does not invoke hardware video encoding or GPU power controls.

## Supported adapters and current boundaries

| Adapter | Workflows | Required service |
| --- | --- | --- |
| OpenAI-compatible images | Text-to-image; reference image editing when configured | `/v1/models`, `/v1/images/generations`, optional `/v1/images/edits`; one `b64_json` result |
| Native 1Cat H3 FL2VA | Text, first frame, last frame, first+last frames to video/audio | Native `video serve` with matching partition/model and multipart reference support |
| Native 1Cat H3 Ref2VA | Mixed image/video/audio references to video/audio | A separately loaded Ref2VA checkpoint and matching LoRA; experimental |

H3 native development evidence: [PR #565](https://github.com/1CatAI/1Cat-vLLM/pull/565)
and its workflow/API documentation, inspected at source revision
`92e8c18e2beda42303268979b89519908740fd1c` plus its then-current API implementation.
**H3 is not included in the 1.5.0 wheel.** Import a development environment containing
`vllm/video/server.py` and `vllm/video/protocol.py` through the existing Setup flow,
or connect an existing H3 service. Studio does not silently upgrade environments.
Image models are provided by the configured image service; this page does not
claim native Qwen-Image, Flux or Wan support merely because a model is listed elsewhere.

Local H3 services accept local model/transformer/LoRA paths and an explicit GPU UUID
set. The shared engine operation lock coordinates loading/stopping with existing
Studio operations. Loading refuses occupied GPUs and never terminates another
service. Only the recorded PID, creation time, argv and process group may be stopped.
The existing per-GPU power policy is untouched. Model and LoRA changes require an
explicit stop/edit/load; changing a node does not hot-swap loaded weights.

## Built-in H3 presets

The creative model panel offers four explicit development recipes: FL2VA base,
FL2VA Turbo 4, Ref2VA base and Ref2VA Turbo 4. Select an inspected H3 runtime;
Studio resolves the matching installed ModelScope components and four V100 32 GB
UUIDs. Missing components have a grouped download action. Base recipes do not
silently enable Turbo. Requests omit scheduler step overrides so native H3 uses
the correct base or adapter schedule.

`POST /api/creative/presets/{id}` creates a saved service without starting it.
An explicit invalid or empty GPU set is rejected. Repeated creation returns the
same service and preserves user changes. Loading remains a separate action and
respects existing GPU ownership checks. Advanced custom services remain available.

## ModelScope components

`backend/onecat/creative/components.json` pins the exact file paths, sizes and SHA256
values from ModelScope as inspected on 2026-09-08. A mutable `master` reference is
accepted only while this manifest still matches, before and after transfer. Each
file is also SHA256-verified. A mismatch stops download instead of mixing revisions.
Only these source-verified development components can use this download endpoint:

- `MiniMax/MiniMax-H3`: shared FL2VA text encoder, tokenizer, processor, video/audio
  VAEs, plus both partitions' transformer configuration (about 77.77 GB).
- `Comfy-Org/MiniMax-H3`: separately selected FL2VA or Ref2VA pruned INT8 ConvRot
  transformer (about 20.97 GB each).
- `lightx2v/Minimax-h3-Turbo`: partition-matched four-step Diffusers LoRA (about
  1.38 GB each). ComfyUI-fused LoRAs are not interchangeable with this loader.

The model's own license still applies. Source availability and checksum verification
are not a full production quality or eight-GPU performance acceptance result.
No model weights are bundled, and no fallback to Hugging Face occurs. Shared
components can be downloaded once and reused by FL2VA and Ref2VA configurations.

## Persistence and measurement contracts

- SQLite buckets: `canvases`, `creative_assets`, `creative_services`,
  `creative_instances`, `creative_runs`, `creative_components`; no destructive migration.
- Optimistic document revisions reject conflicting saves with HTTP 409. Browser
  drafts can be recovered as a new canvas. Saving never overwrites another tab silently.
- Generation waits for edits made during an in-flight save. Correcting an invalid
  field resumes autosave; revision conflicts still require an explicit resolution.
- Generation snapshots freeze prompts, inputs, workflow, service configuration and
  parameters. Request keys and pending-node checks prevent duplicate submissions.
- Workers persist upstream video IDs immediately. Interrupted known video jobs can
  **resume monitoring the original task**. Unknown submission outcomes are not retried
  automatically. A backend that cannot stop a running request is reported honestly;
  Studio continues observing and retains the result.
- Output assets are immutable; client placement uses `placed_runs` to avoid duplicate
  nodes after reload or resurrecting a deleted result. The tray retains original assets.
- Undo removes a result from the board without discarding its task history; polling
  does not place it again. The original asset remains available in the task tray.
- Transient service connection failures remain reconnectable. Stale health results
  cannot overwrite a newer stop or configuration change. A refused or cancelled
  queued stop does not mark a healthy service failed. Stop and generation admission
  share an atomic exclusion boundary; active lifecycle jobs also protect configuration.
- Permanent media and thumbnails live under `attachments/creative`, protected by
  Studio authentication. Existing backups include them; video endpoints support Range.
  Active native staging lives under `jobs/creative-output`, outside permanent backups.
- Denoising progress is shown only for explicit `denoise_progress.completed/total`.
  The native API's generic output-count percentage is not relabeled as denoising.
  Missing stages/steps remain indeterminate, with actual elapsed wall time.
- Power/VRAM come from the selected local boards and are labeled as potentially
  including other work. Remote services without telemetry display no invented power.
  Average W is computed only over contiguous measured intervals; animation timing
  never affects backend measurements.

## Validation

Targeted tests: `PYTHONPATH=studio/backend .venv/bin/pytest
studio/backend/onecat_tests/test_creative.py` (CPU-only mocks for hardware and providers).
Frontend: `npm run typecheck:onecat` and `npm run build:onecat` in `studio/frontend`.
Race regressions: `PYTHONPATH=studio/backend .venv/bin/pytest
studio/backend/onecat_tests/test_creative_audit.py`. After the frontend build, run
`python studio/scripts/check-creative-browser.py` with Playwright, Pillow and the
backend requirements installed. Set `CHROMIUM_EXECUTABLE` when selecting an existing
Chromium. This disposable browser test checks saves during generation, undo/polling,
validation recovery and keyboard interaction using software rendering.
Browser acceptance uses a CPU fixture image/video service and software Chromium;
it covers generation, multipart references, manager restart without resubmission,
asset persistence, same-page preview, dark/light themes and mobile layout.
No real model loading, power/clock mutation or GPU benchmark was performed for this delivery.

The bundle includes the upstream canvas MIT license and corresponding source. Roll
back by selecting the previous installed release; keep the additive SQLite records
and `attachments/creative` directory to preserve creative work.

### Selectable output resolution

The workbench keeps aspect ratio and resolution as separate controls. Changing
orientation keeps the selected resolution tier. H3 Turbo 4/8 text and keyframe
workflows offer aligned 544p and 768p canvases (landscape: 960×544 or 1344×768).
The 544p recipes load the actual LightX2V FL2VA 4step v0.1 / 8step v1.0 adapters
from the SHA256-verified ModelScope catalog; 768p retains its existing adapter.
The model-file details and download size follow that selection. Switching a
resident adapter uses the existing service-impact confirmation.

Ref2VA 4step v0.1 can select 544p, retaining the existing 768p compatibility.
Ref2VA 8step and the base/configured workflows retain their catalog-supported
sizes; unavailable resolutions are not substituted silently. Canvas controls
follow the selected service's actual adapter. Z-Image offers the existing image
sizes plus half-size canvases, including 512×512 and 1024×1024.

Sources: [LightX2V model specifications](https://github.com/ModelTC/Minimax-H3-Turbo#1-model-specs),
[ModelScope adapter catalog](https://modelscope.cn/models/lightx2v/Minimax-h3-Turbo/files).
New resolution/adapter paths remain experimental until their real frontend
acceptance is recorded; existing validation records do not certify new files.
