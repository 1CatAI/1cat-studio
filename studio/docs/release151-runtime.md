# 1.5.1 runtime and serving recipe

Studio filters inherited `VLLM_*`, `PYTHONPATH` and
`ONECAT_VLLM_EXTENSION_DIR` settings before inspecting or starting a runtime.
Environment values explicitly configured in the runtime remain in effect.

Runtime inspection reads `qwen38_27b_nvfp4_dflash2` from an installed wheel when
available. The bundled copy is the fallback for older wheels. Recommended
Qwen3.8-27B settings use an 8192-token prefill budget, 2048-token KV blocks,
8192-token mamba blocks, prefix caching and 0.80 memory utilization. E5M2 stays
unchanged until the release performance and quality comparison selects a KV
default. The DFlash2 selector uses the same speculative recipe as the wheel.

The draft's Hugging Face commit differs from its ModelScope mirror commit.
Studio pins the mirror download to matching configuration and weight hashes,
while the serving recipe retains the upstream revision. Previously verified
copies of the identical payload can be reused.

Presets that exactly match the previous recommendation offer **Update to
recommended settings**. Editing a preset manually prevents this migration
from being offered. Applying an update saves the preset; restart the model to
use it. Names, GPU selections, sampling settings and feature choices remain.

When the runtime implements `/v1/sm70/acceleration`, Studio reads the endpoint
with the inference API key after startup and displays enabled/total capability
counts with reasons for disabled paths. An unavailable endpoint hides this
optional display and does not prevent the model from starting.

The bundled 1.5.1 URL and checksum currently describe the stage-A candidate.
They must be refreshed to the final wheel after release changes are merged and
rebuilt. The public asset is not published by this work.

After installing the final wheel, regenerate the bundled fallback and catalog
from that wheel instead of editing KV defaults in two repositories:

```bash
python tools/sync_sm70_release_profile.py --runtime-python /path/to/venv/bin/python
```
