# Experimental FastH3 VSA

Select **Minimax-H3** in the creative workbench.
The **Fast · Experimental** switch is off by default. Off uses the official
Dense Data-Free adapter; on uses the official VSA Data-Free adapter and
`FASTVIDEO_VSA`, top-k 64. Switching adapters requires reloading the service;
the existing preparation and affected-service confirmation flow handles this.
Drafts, retries, saved runs and canvas references retain the selected mode.

This is a separate model workflow. VSA in native PR #583 does **not** support
the existing INT8 ConvRot checkpoint or LightX2V adapters. It requires:

- Original MiniMax-H3 FL2VA weights (13 BF16 shards, about 66.28 GB).
- Shared H3 text encoder, tokenizer, video VAE and audio VAE components.
- `FastVideo/FastVideo-FastH3-4-step-Preview-v1-LoRA`:
  `vsa-datafree/adapter_model.safetensors` (about 5.34 GB), or its
  `dense-datafree/adapter_model.safetensors` control (about 1.49 GB).

All downloads use ModelScope, with manifest and file SHA256 checks. Shared
components are referenced through an immutable directory view, without
duplicating the installed text encoder or VAEs. SM70 computes with FP16;
BF16 here describes the original stored checkpoint. FastH3 uses four API
intervals; LightX2V's four-step adapter uses five sigma positions.

FastH3 currently accepts text-to-video only. The workbench does not offer
reference uploads for this model, and the API rejects reference workflows,
wrong adapter/backend pairs and INT8 overrides before executing them. An
older native runtime cannot enable the switch. The separate lossless dense
execution optimizations remain available to existing INT8 workflows.

## Remote Studio measurements (2026-09-10)

Real browser submissions on the allocated four-card machine, without mocked
APIs: four V100 SXM2 32 GB cards, TP4, 300 W limits, dynamic clocks,
Torch 2.10.0+cu128, CUDA 12.8, Python 3.12.13. Host RAM is 62 GiB;
DiT/text host masters use disk-backed storage and VAEs use shared host memory.
Native source `5e50ef0df7`, Studio inference orchestration `10557b383a`;
the UI regression also covers `7f76ae57f7`.

Both modes use the same original FL2VA shards, prompt, seed 42, 1280×736,
120 requested frames, 24 FPS, four actual denoiser intervals and their
corresponding official Data-Free adapters. Each mode has one excluded cold
warmup followed by three measured UI submissions. The result contains 124
aligned frames (about 5.17 seconds), with audio.

| Median of three warm requests | FA / Dense | Fast / VSA | Speedup |
| --- | ---: | ---: | ---: |
| Complete denoise | 51.58 s | 29.01 s | 1.78× |
| Complete native generation | 122.28 s | 97.20 s | 1.26× |
| Studio submission to saved result | 124.25 s | 99.71 s | 1.25× |

Native generation is **20.5% shorter**; denoising is **43.8% shorter**.
Encoding, weight staging, decoding and saving remain outside the accelerated
attention stage. These measurements do not promise half the complete time
and do not compare the original checkpoint with INT8/LightX2V. Cold model
loading is excluded from this warmed comparison; on this RAM-constrained
host, changing adapters required about 15.7 minutes to prepare the VSA service.
Studio records that preparation separately and retains total elapsed time.

All eight outputs passed actual browser playback/download and complete FFmpeg
decode, dimensions/frame-count/audio checks. All four ranks recorded the
requested backend and four DiT calls. The three measured outputs within each
mode had identical MP4 hashes. This does not imply equivalence between modes:
the adapters and attention differ. Result reuse, sending to canvas, completion
controls and retained timing were checked through the real UI, along with
Chinese/English, light/dark and 320/390/1440 px layouts.

The VSA path inherited from [PR #583](https://github.com/1CatAI/1Cat-vLLM/pull/583)
still **fails its independent FP32 quality gate**. A playable video and a faster
benchmark do not change that result. Fast remains explicitly experimental and
off by default; neither the native AUTO backend nor quality qualification is
promoted. The slower FP32 diagnostic's quality pass cannot qualify the fast path.

Existing INT8 ConvRot, LightX2V 4-step and 8-step entries, downloaded files and
saved service configurations are retained. Original Minimax-H3 is additive.
Choosing a saved image as a first frame switches to a compatible existing H3
workflow; FastH3 currently accepts text-to-video only.
