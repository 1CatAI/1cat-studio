# 1.5.1 runtime and serving recipe

Studio filters inherited `VLLM_*`, `PYTHONPATH` and
`ONECAT_VLLM_EXTENSION_DIR` settings before inspecting or starting a runtime.
It also filters inherited `FLASH_QLA_SM70_PREBUILT_EXTENSION_PATH`, which could
otherwise replace the wheel's FlashQLA kernel with a developer's external DSO.
Environment values explicitly configured in the runtime remain in effect.

Runtime inspection reads `qwen38_27b_nvfp4_dflash2` from an installed wheel when
available. The bundled copy is the fallback for older wheels. Recommended
Qwen3.8-27B settings use an 8192-token prefill budget, 2048-token KV blocks,
8192-token mamba blocks, prefix caching and 0.80 memory utilization. The release
owner selected E4M3 as the recommended KV default. Performance and output-quality
qualification remain release gates. The DFlash2 selector uses the same
speculative recipe as the wheel.

New 27B presets automatically select an already downloaded release DFlash2
draft when its configuration/weight hashes match and the verified files remain
unchanged. Missing or modified drafts leave the preset target-only and show a
warning on the model card. Download the qualified draft to obtain the DFlash2
recipe. Existing presets are preserved; an otherwise unchanged release preset
offers an explicit update to pair the newly available draft.

Studio refreshes runtime capability records imported by older versions.
Missing, unreadable or incomplete optional wheel recipes use the bundled
fallback and do not block the core Torch/vLLM import check.

The draft's Hugging Face commit differs from its ModelScope mirror commit.
Studio pins the mirror download to matching configuration and weight hashes,
while the serving recipe retains the upstream revision. Previously verified
copies of the identical payload can be reused.

Presets that exactly match the previous recommendation offer **Update to
recommended settings**. Editing a preset manually prevents this migration
from being offered. Applying an update saves the preset; restart the model to
use it. Names, GPU selections, sampling settings and feature choices remain.
An explicitly pinned draft revision outside the qualified release payload
also prevents the migration from being offered.

When the runtime implements `/v1/sm70/acceleration`, Studio reads the endpoint
with the inference API key after startup and displays enabled/total capability
counts with reasons for disabled paths. An unavailable endpoint hides this
optional display and does not prevent the model from starting.
Counts use the endpoint's `expected_acceleration` list when available. Unrelated
Flash-Next routes and compile-cache diagnostics do not inflate the denominator.
Startup compilation-cache status is displayed separately, including the reported
reason when disabled. A full decode-acceleration count must not hide repeated
startup compilation. Older runtimes without a cache row omit this optional status.

The bundled 1.5.1 URL and checksum currently describe the stage-A candidate.
They must be refreshed to the final wheel after release changes are merged and
rebuilt. The public asset is not published by this work.

After installing the final wheel, regenerate the bundled fallback and catalog
from that wheel instead of editing KV defaults in two repositories:

```bash
python tools/sync_sm70_release_profile.py --runtime-python /path/to/venv/bin/python
```
