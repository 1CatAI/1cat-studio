#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Opt-in V100 acceptance for the current model/runtime; restores the initial profile.

Run in the installed Studio environment with PYTHONPATH=studio/backend and
ONECAT_STUDIO_HOME set. --accelerators tests existing local DFlash plus MTP;
this performs explicit temporary model restarts, never switches runtime versions.
"""

import argparse
import base64
import hashlib
import io
import json
import os
import time
from pathlib import Path

import httpx
import psutil
from onecat import auth, catalog, db, engine
from onecat.jobs import Job
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument("--accelerators", action="store_true")
parser.add_argument("--skip-base", action="store_true")
parser.add_argument("--modes", nargs="+", choices=["dflash", "mtp"], default=["dflash", "mtp"])
parser.add_argument("--max-images", type=int, choices=range(1, 5), default=1)
parser.add_argument("--port", type=int, default=8888)
args = parser.parse_args()
initial = engine.private_state()
if initial.get("state") != "ready" or engine.active_requests() or db.get("engine", "maintenance"):
    raise SystemExit("An idle, ready model is required")
original = db.get("profiles", initial["profile_id"])
folder = Path(original["model_path"])
key = (
    hashlib.sha256((folder / "config.json").read_bytes()).hexdigest() + ":" + original["runtime_id"]
)
results = {
    "runtime_id": original["runtime_id"],
    "model_config_sha256": key.split(":")[0],
    "started_at": time.time(),
    "modes": {},
}
caps = catalog.capabilities(original["model_path"], original["runtime_id"])
caps.update(
    {
        "verified": True,
        "tool_parser": None,
        "vision": False,
        "max_images": args.max_images,
        "accelerators": list(caps.get("accelerators", [])),
        "recommended": {},
        "reason": "本机实际问答及协议验证 / Validated on this installation",
    }
)
token_name = "studio-acceptance-" + db.uid()
token = auth.issue(token_name)
client = httpx.Client(
    base_url=f"http://127.0.0.1:{args.port}",
    headers={"Authorization": "Bearer " + token},
    timeout=180,
    trust_env=False,
)


def remember():
    db.put(
        "model_validation",
        key,
        {
            "model_path": str(folder),
            "files": {
                f.name: [f.stat().st_size, f.stat().st_mtime_ns]
                for f in folder.iterdir()
                if f.suffix in {".safetensors", ".bin", ".json"}
            },
            "capabilities": caps,
            "runtime_id": original["runtime_id"],
            "runtime_family": catalog.runtime_family(original["runtime_id"]),
            "validated_at": time.time(),
            "report": results,
        },
    )


def request(messages, **extra):
    state = engine.private_state()
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": state["profile"]["served_model_name"],
            "messages": messages,
            "temperature": 0,
            "max_tokens": 128,
            "chat_template_kwargs": {"enable_thinking": False},
            **extra,
        },
    )
    response.raise_for_status()
    return response


def protocol_gate():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]
    messages = [{"role": "user", "content": "Use get_weather to find the weather in Paris."}]
    data = request(messages, tools=tools, tool_choice="auto").json()
    call = data["choices"][0]["message"]["tool_calls"][0]["function"]
    assert (
        call["name"] == "get_weather" and "paris" in json.loads(call["arguments"])["city"].lower()
    ), call
    response = request(messages, tools=tools, tool_choice="auto", stream=True)
    names, arguments = "", ""
    for line in response.text.splitlines():
        if not line.startswith("data:") or line[5:].strip() == "[DONE]":
            continue
        for choice in json.loads(line[5:]).get("choices", []):
            for part in choice.get("delta", {}).get("tool_calls", []):
                fn = part.get("function") or {}
                names += fn.get("name") or ""
                arguments += fn.get("arguments") or ""
    assert names == "get_weather" and "paris" in json.loads(arguments)["city"].lower(), (
        names,
        arguments,
    )
    return {"nonstream_tool_call": True, "stream_tool_call": True}


def image_gate():
    image = io.BytesIO()
    Image.new("RGB", (224, 224), "red").save(image, format="PNG")
    part = {
        "type": "image_url",
        "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
        },
    }
    data = request(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is the main color of these images? Answer one word.",
                    },
                    *([part] * args.max_images),
                ],
            }
        ],
        max_tokens=24,
    ).json()
    text = data["choices"][0]["message"]["content"]
    assert "red" in text.lower() or "红" in text, text
    return {"image_question": True, "image_count": args.max_images, "usage": data["usage"]}


def timing_gate():
    # Leave the preceding request's asynchronous metrics flush uncontaminated.
    time.sleep(8)
    state = engine.private_state()
    ids, first_count, usage, chunks = [], None, {}, 0
    begin, first, last = time.monotonic(), None, None
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": state["profile"]["served_model_name"],
            "messages": [
                {
                    "role": "user",
                    "content": "Write a numbered list counting from 1 to 100, one number per line.",
                }
            ],
            "max_tokens": 64,
            "ignore_eos": True,
            "temperature": 0,
            "stream": True,
            "return_token_ids": True,
            "chat_template_kwargs": {"enable_thinking": False},
        },
    ) as response:
        response.raise_for_status()
        request_id = response.headers["X-Request-ID"]
        for line in response.iter_lines():
            if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                continue
            event = json.loads(line[5:])
            usage = event.get("usage") or usage
            for choice in event.get("choices", []):
                part = choice.get("token_ids") or []
                if part:
                    now = time.monotonic()
                    if first is None:
                        first, first_count = now, len(part)
                    last = now
                    ids.extend(part)
                    chunks += 1
    assert len(ids) == usage["completion_tokens"] == 64, (len(ids), usage)
    # Read through the database: the temporary key intentionally has no admin scope.
    deadline = time.monotonic() + 12
    while True:
        with db.connect() as conn:
            row = dict(conn.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone())
        metrics = json.loads(row["metrics"] or "{}")
        if not metrics.get("pending") or time.monotonic() > deadline:
            break
        time.sleep(0.3)
    assert row["completion_tokens"] == 64 and metrics.get("decode_tokens_s", 0) > 0, metrics
    return {
        "request_id": request_id,
        "accepted_tokens": len(ids),
        "sse_token_chunks": chunks,
        "first_chunk_tokens": first_count,
        "client_ttft_s": first - begin,
        "client_observed_decode": (len(ids) - first_count) / (last - first),
        "metrics": metrics,
    }


def launch(profile, experimental=False):
    id = db.uid()
    db.put(
        "jobs",
        id,
        {
            "id": id,
            "kind": "start_model",
            "payload": {"profile_id": profile["id"]},
            "state": "running",
            "stage": "checking",
            "created_at": time.time(),
            "pid": os.getpid(),
            "process_created": psutil.Process().create_time(),
        },
    )
    job = Job(id)
    validate = catalog.validate_features
    if experimental:
        # The opt-in CLI probes an unvalidated accelerator before admitting it.
        # This override is confined to this process and this exact candidate.
        catalog.validate_features = lambda p: p if p["id"] == profile["id"] else validate(p)
    try:
        with engine.maintenance(job):
            value = engine.start(job, profile["id"])
        job.finish(value)
    except BaseException as error:
        job.fail(error)
        raise
    finally:
        catalog.validate_features = validate


temporary = []
try:
    if args.skip_base:
        prior = db.get("model_validation", key, {})
        if (
            not catalog.capabilities(original["model_path"], original["runtime_id"]).get("verified")
            or not prior.get("capabilities", {}).get("tool_parser")
            or not prior.get("capabilities", {}).get("vision")
        ):
            raise ValueError("No completed base validation is available")
        results["modes"].update(prior.get("report", {}).get("modes", {}))
    else:
        results["modes"]["base"] = {**protocol_gate(), **image_gate(), **timing_gate()}
    caps.update({"tool_parser": original.get("tool_parser") or "qwen3_coder", "vision": True})
    remember()
    # The already-running instance just demonstrated these enabled capabilities.
    original.update(
        {
            "tool_calling": True,
            "tool_parser": caps["tool_parser"],
            "vision_enabled": True,
            "max_images": args.max_images,
        }
    )
    db.put("profiles", original["id"], original)
    db.patch("engine", "active", {"profile": original})
    if args.accelerators:
        draft = next(
            (
                p.get("speculative_config")
                for p in db.all_records("profiles")
                if p.get("model_path") == original["model_path"]
                and (p.get("speculative_config") or {}).get("method") == "dflash"
            ),
            None,
        )
        for method, spec in [
            ("dflash", draft),
            ("mtp", {"method": "mtp", "num_speculative_tokens": 4}),
        ]:
            if not spec or method not in args.modes:
                continue
            candidate = {
                **original,
                "id": "acceptance-" + db.uid(),
                "name": "Acceptance " + method,
                "served_model_name": "onecat-acceptance-" + method,
                "max_model_len": 8192,
                "speculative_config": spec,
                "source": "local-validation",
            }
            db.put("profiles", candidate["id"], candidate)
            temporary.append(candidate["id"])
            print("Starting acceptance", method, flush=True)
            try:
                launch(candidate, experimental=True)
                results["modes"][method] = {**protocol_gate(), **image_gate(), **timing_gate()}
                if method not in caps["accelerators"]:
                    caps["accelerators"].append(method)
                remember()
                print("Accepted", method, json.dumps(results["modes"][method]), flush=True)
            except Exception as error:
                results["modes"][method] = {"error": str(error)[-2400:]}
                print("Not admitted", method, str(error)[-800:], flush=True)
        if engine.private_state().get("profile_id") in temporary:
            launch(original)
    results["finished_at"] = time.time()
    remember()
    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)
finally:
    client.close()
    with db.connect() as conn:
        conn.execute("DELETE FROM credentials WHERE name=?", (token_name,))
    if engine.private_state().get("profile_id") in temporary:
        launch(original)
    for id in temporary:
        db.delete("profiles", id)
