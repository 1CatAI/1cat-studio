# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Workload-controlled board-energy calibration; never equate low W with low energy."""

from __future__ import annotations

import hashlib
import json
import math
import signal
import statistics
import subprocess
import threading
import time
from pathlib import Path

import requests

from . import db, engine, gpu
from .config import state_root
from .jobs import Cancelled, Job
from .schemas import BenchmarkRequest

REFERENCE = Path(__file__).parent / "data" / "v100-reference.json"


def list_results() -> dict:
    history = json.loads(REFERENCE.read_text()) if REFERENCE.exists() else None
    current = engine.private_state()
    results = db.all_records("benchmarks")
    for result in results:
        result["matches_current_profile"] = (
            bool(current.get("identity"))
            and result.get("collector_version") == 2
            and current.get("identity") == result.get("profile_identity")
            and result.get("runtime_identity")
            == db.get("runtimes", current.get("runtime_id", ""), {}).get("capabilities")
        )
    return {
        "items": results,
        "reference": history,
        "reference_status": "historical_reference_requires_exact_workload_match",
    }


def recommend(payload: dict) -> dict:
    record = db.get("benchmarks", payload.get("run_id", ""))
    if (
        not record
        or record.get("state") != "completed"
        or not record.get("hardware_restored")
        or record.get("collector_version") != 2
    ):
        raise ValueError("Select a completed calibration run")
    current = engine.private_state()
    if current.get("identity") != record.get("profile_identity"):
        raise ValueError("This calibration was measured with a different running profile")
    if db.get("runtimes", current.get("runtime_id", ""), {}).get("capabilities") != record.get(
        "runtime_identity"
    ):
        raise ValueError("The inference runtime has changed since this calibration")
    low_prefill = float(payload.get("min_prefill_tokens_s", 0))
    low_decode = float(payload.get("min_decode_tokens_s", 0))
    high_ttft = payload.get("max_ttft_s")
    eligible = [
        r
        for r in record.get("summary", [])
        if r["valid_repeats"] >= 2
        and r["prefill_tokens_s"] >= low_prefill
        and r["decode_tokens_s"] >= low_decode
        and (high_ttft is None or r["ttft_s"] <= float(high_ttft))
    ]
    if not eligible:
        return {"recommendation": None, "reason": "No measured setting meets these constraints"}
    best = min(eligible, key=lambda r: r["total_gpu_j"])
    # Treat means within their combined observed spread as practically tied; prefer faster completion.
    tied = [
        r
        for r in eligible
        if r["total_gpu_j"] - best["total_gpu_j"]
        <= math.hypot(r["total_gpu_j_std"], best["total_gpu_j_std"])
    ]
    selected = min(tied, key=lambda r: r["elapsed_s"])
    return {
        "recommendation": selected,
        "basis": "minimum measured request energy under constraints",
        "near_equal_settings": [r["setting"] for r in tied],
    }


class Meter:
    def __init__(self, uuids):
        import pynvml as nv

        self.nv = nv
        nv.nvmlInit()
        self.handles = [nv.nvmlDeviceGetHandleByUUID(uuid) for uuid in uuids]
        self.counter = True
        try:
            self.energy()
        except nv.NVMLError:
            self.counter = False
        self.samples = []
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def energy(self):
        return sum(self.nv.nvmlDeviceGetTotalEnergyConsumption(h) for h in self.handles) / 1000

    def sample(self):
        while not self.stopped.is_set():
            try:
                watts = sum(self.nv.nvmlDeviceGetPowerUsage(h) for h in self.handles) / 1000
                self.samples.append((time.monotonic(), watts))
            except self.nv.NVMLError:
                pass
            self.stopped.wait(0.2)

    def start(self):
        self.samples.clear()
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.thread.join(timeout=2)
        self.nv.nvmlShutdown()

    def integrated(self, start, end):
        points = [p for p in self.samples if start <= p[0] <= end]
        if len(points) < 2:
            raise ValueError("Not enough power samples to estimate energy")
        return sum((b[0] - a[0]) * (a[1] + b[1]) / 2 for a, b in zip(points, points[1:]))


TOKENIZE_SCRIPT = r"""
import json, sys
from transformers import AutoTokenizer
model, length=sys.argv[1],int(sys.argv[2])
t=AutoTokenizer.from_pretrained(model, trust_remote_code=False)
instruction='\nUsing the reference context above, write a detailed guide to building a reliable local inference service. Explain installation, GPU allocation, model lifecycle, streaming APIs, testing, profiling, observability, failure recovery, and maintenance. Include practical examples and explanations. Write at least 2000 words.\n'
text=t.apply_chat_template([{'role':'user','content':'ONECAT_PADDING_MARKER'+instruction}],tokenize=False,add_generation_prompt=True,enable_thinking=False)
a,b=text.split('ONECAT_PADDING_MARKER',1)
prefix=t.encode(a,add_special_tokens=False);suffix=t.encode(b,add_special_tokens=False)
fill=t.encode(' Reference: reproducible input, consistent configuration, measured power and token throughput. ',add_special_tokens=False)
n=length-len(prefix)-len(suffix)
if n<0: raise ValueError('Prompt length is too small for the test template')
ids=prefix+(fill*((n+len(fill)-1)//len(fill)))[:n]+suffix
print('ONECAT_TOKENS='+json.dumps(ids))
"""


def prepare_tokens(profile: dict, runtime: dict, count: int):
    result = subprocess.run(
        [runtime["python_path"], "-c", TOKENIZE_SCRIPT, profile["model_path"], str(count)],
        env=engine.runtime_env(runtime, profile),
        cwd=runtime.get("working_directory"),
        capture_output=True,
        text=True,
        timeout=120,
    )
    value = next(
        (line[14:] for line in result.stdout.splitlines() if line.startswith("ONECAT_TOKENS=")),
        None,
    )
    if result.returncode or value is None:
        raise ValueError("Tokenizer could not prepare the fixed workload: " + result.stderr[-1200:])
    ids = json.loads(value)
    if len(ids) != count:
        raise ValueError("Tokenizer did not produce the requested prompt length")
    return ids


def metrics(base: str, headers: dict) -> dict:
    response = requests.get(base + "/metrics", headers=headers, timeout=10)
    response.raise_for_status()
    result = {}
    for line in response.text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        name = parts[0].split("{", 1)[0]
        if any(
            key in name
            for key in (
                "request_prefill_time_seconds",
                "request_decode_time_seconds",
                "prefix_cache_hits_total",
            )
        ):
            try:
                result[name] = result.get(name, 0) + float(parts[1])
            except ValueError:
                pass
    return result


def measure(job, profile, prompt, count, meter, folder, label, repeat, reference):
    state = engine.private_state()
    base = f"http://127.0.0.1:{state['port']}"
    headers = {"Authorization": f"Bearer {state['api_key']}"} if state.get("api_key") else {}
    before = metrics(base, headers)
    payload = {
        "model": profile["served_model_name"],
        "prompt": prompt,
        "max_tokens": count,
        "temperature": 0,
        "seed": 42,
        "stream": True,
        "return_token_ids": True,
        "stream_options": {"include_usage": True},
        "cache_salt": db.uid(),
    }
    start = time.monotonic()
    first, last, first_count, first_energy = None, None, 0, None
    e0 = meter.energy() if meter.counter else None
    ids, text, arrivals, usage = [], [], [], {}
    finish_reason = None
    last_cancel_check = start
    with requests.post(
        base + "/v1/completions", headers=headers, json=payload, stream=True, timeout=(10, 120)
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines(chunk_size=None):
            # Poll at a bounded interval; a SQLite transaction per token would
            # slow the consumer and contaminate both elapsed time and energy.
            now = time.monotonic()
            if now - last_cancel_check >= 0.2:
                job.check_cancelled()
                last_cancel_check = now
            if not line.startswith(b"data: ") or line[6:] == b"[DONE]":
                continue
            event = json.loads(line[6:])
            if event.get("usage"):
                usage = event["usage"]
            for choice in event.get("choices", []):
                tokens = choice.get("token_ids") or []
                if tokens:
                    now = time.monotonic()
                    ids.extend(tokens)
                    arrivals.append([len(ids), now - start])
                    if first is None:
                        first, first_count = now, len(ids)
                        first_energy = meter.energy() if meter.counter else None
                    last = now
                text.append(choice.get("text", ""))
                finish_reason = choice.get("finish_reason") or finish_reason
    end = time.monotonic()
    e1 = meter.energy() if meter.counter else None
    trace = {"token_ids": ids, "arrivals": arrivals, "text": "".join(text), "usage": usage}
    (folder / f"{label}-{repeat}.json").write_text(json.dumps(trace, ensure_ascii=False))
    if len(ids) != count or usage.get("prompt_tokens") != len(prompt) or finish_reason != "length":
        raise ValueError(
            "Output length or prompt length differs from the fixed contract; trace retained"
        )
    if (
        not first
        or not last
        or last <= first
        or len(set(ids)) < min(10, count // 4)
        or "\ufffd" in trace["text"]
    ):
        raise ValueError("Output health or token timing check failed; trace retained")
    if reference is not None and ids != reference:
        raise ValueError(
            "Output tokens changed under the same deterministic workload; trace retained"
        )
    count_key = "vllm:request_prefill_time_seconds_count"
    deadline = time.monotonic() + 10
    after = metrics(base, headers)
    while after.get(count_key, 0) - before.get(count_key, 0) < 1 and time.monotonic() < deadline:
        time.sleep(0.25)
        after = metrics(base, headers)
    delta = {key: val - before.get(key, 0) for key, val in after.items()}
    prefill = delta.get("vllm:request_prefill_time_seconds_sum")
    if delta.get(count_key) != 1 or not prefill or prefill <= 0:
        raise ValueError(
            "Cannot isolate this request's prefill time; concurrent traffic or missing metrics"
        )
    if delta.get("vllm:prefix_cache_hits_total", 0) != 0:
        raise ValueError("Prefix cache hit changed the workload; this sample is invalid")
    decode = delta.get("vllm:request_decode_time_seconds_sum")
    if delta.get("vllm:request_decode_time_seconds_count") != 1 or not decode or decode <= 0:
        raise ValueError("Cannot isolate this request's server decode time")
    total = e1 - e0 if meter.counter else meter.integrated(start, end)
    pre_energy = first_energy - e0 if meter.counter else meter.integrated(start, first)
    row = {
        "repeat": repeat,
        "elapsed_s": end - start,
        "ttft_s": first - start,
        "prefill_s": prefill,
        "prefill_tokens_s": len(prompt) / prefill,
        "decode_tokens_s": (len(ids) - 1) / decode,
        "observed_decode_tokens_s": (len(ids) - first_count) / (last - first),
        "decode_timing_source": "isolated_server_decode_metric",
        "total_gpu_j": total,
        "average_total_gpu_w": total / (end - start),
        "pre_first_token_gpu_j": pre_energy,
        "request_energy_wh": total / 3600,
        "tokens_per_j": len(ids) / total,
        "output_hash": hashlib.sha256(json.dumps(ids).encode()).hexdigest(),
        "energy_method": "nvml_counter" if meter.counter else "sampled_power_estimate",
        "server_metric_deltas": delta,
        "valid": True,
    }
    return row, ids


def benchmark(job: Job):
    spec = BenchmarkRequest.model_validate(job.payload)
    profile = db.get("profiles", spec.profile_id)
    if not profile:
        raise ValueError("Profile not found")
    if profile["max_model_len"] < spec.prompt_tokens + spec.output_tokens:
        raise ValueError("The configured context limit is smaller than the benchmark workload")
    # The first release's strict deterministic gate applies to target-only inference.
    # Speculative decoding requires a separately validated timing/quality contract.
    if profile.get("speculative_config"):
        raise ValueError(
            "Create a target-only profile with DFlash2/MTP disabled for this calibration"
        )
    if not gpu.control_available():
        raise ValueError("Install the GPU control helper before calibrating")
    if db.get("engine", "hardware_recovery"):
        raise ValueError("Recover the interrupted calibration's GPU settings first")
    current = engine.private_state()
    if current.get("profile_id") != spec.profile_id or current.get("state") != "ready":
        engine.start(job, spec.profile_id)
    engine.drain(job)
    runtime = db.get("runtimes", profile["runtime_id"])
    devices = gpu.selected_devices(profile["gpu_uuids"])
    previous = gpu.capture_settings(profile["gpu_uuids"])
    power = min(d["default_power_limit_w"] for d in devices)
    if spec.candidates:
        candidates = [s.model_dump() for s in spec.candidates]
    elif all(d["compute_capability"] == [7, 0] for d in devices):
        supported = set.intersection(*(set(d["supported_graphics_clocks_mhz"]) for d in devices))
        candidates = [{"power_limit_w": power, "reset_clocks": True}] + [
            {"power_limit_w": power, "graphics_clock_mhz": clock}
            for clock in (900, 930, 975, 1020)
            if clock in supported
        ]
    else:
        low = max(d["power_min_w"] for d in devices)
        high = min(d["power_max_w"] for d in devices)
        candidates = [
            {"power_limit_w": round(high - (high - low) * i / 4), "reset_clocks": True}
            for i in range(5)
        ]
    if len(candidates) < 2:
        raise ValueError("At least two supported hardware settings are required")
    for candidate in candidates:
        gpu.validate_setting(profile["gpu_uuids"], candidate)
    folder = state_root() / "benchmarks" / job.id
    folder.mkdir()
    job.update("preparing_workload", 1)
    prompt = prepare_tokens(profile, runtime, spec.prompt_tokens)
    (folder / "prompt-token-ids.json").write_text(json.dumps(prompt))
    contract = {
        "id": job.id,
        "profile": profile,
        "profile_identity": engine.identity(profile),
        "runtime_identity": runtime["capabilities"],
        "workload": spec.model_dump(),
        "gpu_names": [d["name"] for d in devices],
        "gpu_uuids": profile["gpu_uuids"],
        "driver": gpu.snapshot().get("driver"),
        "sampling": "greedy, seed=42, thinking=false",
        "prefix_cache": "unique cache_salt per request",
        "energy_scope": "GPU boards only",
        "created_at": time.time(),
        "collector_version": 2,
        "decode_timing_source": "isolated_server_decode_metric",
        "state": "running",
        "summary": [],
    }
    db.put("benchmarks", job.id, contract)
    (folder / "contract.json").write_text(json.dumps(contract, indent=2))
    (folder / "restore-hardware.json").write_text(json.dumps(previous))
    meter = Meter(profile["gpu_uuids"])
    meter.start()
    rows, reference = [], None

    def interrupted(signum, frame):
        raise Cancelled(f"Calibration interrupted by signal {signum}")

    old_signal = signal.signal(signal.SIGTERM, interrupted)
    try:
        for index, setting in enumerate(candidates):
            job.update(
                "applying_test_setting",
                index / len(candidates) * 100,
                candidate=index + 1,
                candidate_count=len(candidates),
            )
            gpu.apply(profile["gpu_uuids"], setting)
            label = f"setting-{index}"
            job.update("warming_up", index / len(candidates) * 100)
            _, warm_ids = measure(
                job, profile, prompt, spec.output_tokens, meter, folder, label, "warmup", reference
            )
            if reference is None:
                reference = warm_ids
            for repeat in range(spec.repeats):
                job.update(
                    "measuring",
                    (index + (repeat + 1) / (spec.repeats + 1)) / len(candidates) * 100,
                    candidate=index + 1,
                    repeat=repeat + 1,
                )
                row, _ = measure(
                    job,
                    profile,
                    prompt,
                    spec.output_tokens,
                    meter,
                    folder,
                    label,
                    repeat,
                    reference,
                )
                row["setting"] = setting
                rows.append(row)
                with (folder / "results.jsonl").open("a") as output:
                    output.write(json.dumps(row) + "\n")
            group = [r for r in rows if r["setting"] == setting]
            fields = [
                "elapsed_s",
                "ttft_s",
                "prefill_tokens_s",
                "decode_tokens_s",
                "total_gpu_j",
                "average_total_gpu_w",
                "request_energy_wh",
                "tokens_per_j",
            ]
            result = {
                "setting": setting,
                "valid_repeats": len(group),
                **{key: statistics.mean(r[key] for r in group) for key in fields},
                "total_gpu_j_std": statistics.stdev(r["total_gpu_j"] for r in group),
            }
            contract["summary"].append(result)
            db.put("benchmarks", job.id, contract)
        contract["state"] = "completed"
        db.put("benchmarks", job.id, contract)
    except BaseException as error:
        contract.update(
            {
                "state": "cancelled" if isinstance(error, Cancelled) else "failed",
                "error": str(error),
            }
        )
        db.put("benchmarks", job.id, contract)
        raise
    finally:
        meter.stop()
        (folder / "power-samples.json").write_text(json.dumps(meter.samples))
        signal.signal(signal.SIGTERM, old_signal)
        try:
            gpu.restore(previous)
            (folder / "restore-hardware.json").unlink(missing_ok=True)
            db.patch("benchmarks", job.id, {"hardware_restored": True})
        except Exception as error:
            db.patch(
                "benchmarks", job.id, {"hardware_restored": False, "restore_error": str(error)}
            )
            raise
    return {"run_id": job.id, "artifact_directory": str(folder), "hardware_restored": True}
