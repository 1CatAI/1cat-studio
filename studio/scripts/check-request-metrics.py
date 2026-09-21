#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Opt-in real-request checks for cache attribution, concurrency, cancellation and
model-controlled output length. Requires an idle Studio model with >=4096 context.
Run with the installed Studio Python, PYTHONPATH and ONECAT_STUDIO_HOME.
"""

import concurrent.futures
import json
import threading
import time

import httpx
from onecat import auth, db, engine

state = engine.private_state()
if (
    state["state"] != "ready"
    or engine.active_requests()
    or db.get("engine", "maintenance")
    or state.get("profile", {}).get("max_model_len", 0) < 4096
):
    raise SystemExit("An idle model with at least 4096 context tokens is required")
name = "studio-metric-acceptance-" + db.uid()
token = auth.issue(name, "admin", expires=time.time() + 600)
client = httpx.Client(
    base_url=f"http://127.0.0.1:{db.settings()['port']}",
    headers={"Authorization": "Bearer " + token},
    timeout=180,
    trust_env=False,
)
model = state["profile"]["served_model_name"]


def body(prompt, **extra):
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "return_token_ids": True,
        "temperature": 0,
        "max_tokens": 128,
        "chat_template_kwargs": {"enable_thinking": False},
        **extra,
    }


def parse(response):
    response.raise_for_status()
    usage = {}
    count = 0
    for line in response.text.splitlines():
        if line.startswith("data:") and line[5:].strip() != "[DONE]":
            d = json.loads(line[5:])
            usage = d.get("usage") or usage
            count += sum(len(c.get("token_ids") or []) for c in d.get("choices", []))
    assert count == usage["completion_tokens"]
    return response.headers["X-Request-ID"], usage


def metrics(id):
    limit = time.monotonic() + 12
    while True:
        with db.connect() as c:
            r = dict(c.execute("select * from requests where id=?", (id,)).fetchone())
        m = json.loads(r["metrics"] or "{}")
        if r["status"] is not None and not m.get("pending"):
            return r, m
        assert time.monotonic() < limit, (r, m)
        time.sleep(0.3)


report = {}
try:
    time.sleep(8)
    prompt = (
        "Reference notes.\n"
        + ("The quick brown fox jumps over the lazy dog.\n" * 256)
        + "\nReply with a short explanation of this sentence."
    )
    warm = client.post("/v1/chat/completions", json=body(prompt, max_tokens=64))
    wid, _ = parse(warm)
    metrics(wid)
    rid, usage = parse(client.post("/v1/chat/completions", json=body(prompt, max_tokens=64)))
    row, m = metrics(rid)
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    assert cached and cached > 0, (usage, m)
    if m.get("prefill_tokens_s") is not None:
        assert (
            abs(m["prefill_tokens_s"] * m["prefill_s"] - (usage["prompt_tokens"] - cached)) < 1e-5
        )
    report["cache"] = {
        "request_id": rid,
        "prompt_tokens": usage["prompt_tokens"],
        "cached_tokens": cached,
        "metrics": m,
    }
    barrier = threading.Barrier(2)

    def concurrent_request(i):
        barrier.wait()
        return parse(
            client.post(
                "/v1/chat/completions",
                json=body(
                    "Count from 1 to 1000, one number per line. Start with " + str(i),
                    max_tokens=256,
                    ignore_eos=True,
                ),
            )
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        requests = list(pool.map(concurrent_request, [1, 2]))
    rows = [metrics(id) for id, _ in requests]
    assert all(
        m.get("concurrent")
        and m.get("prefill_tokens_s") is None
        and m.get("decode_source") != "isolated_engine_metrics"
        for _, m in rows
    ), rows
    report["concurrency"] = [
        {"id": r["id"], "tokens": r["completion_tokens"], "metrics": m} for r, m in rows
    ]
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json=body("Count from 1 to 100000.", max_tokens=4096, ignore_eos=True),
    ) as response:
        response.raise_for_status()
        cid = response.headers["X-Request-ID"]
        for line in response.iter_lines():
            if line.startswith("data:") and line[5:].strip() != "[DONE]":
                event = json.loads(line[5:])
                if any(c.get("token_ids") for c in event.get("choices", [])):
                    break
    row, m = metrics(cid)
    assert row["status"] == 499 and m.get("decode_tokens_s") is None, (row, m)
    report["cancel"] = {"id": cid, "status": row["status"], "metrics": m}
    rid, usage = parse(
        client.post(
            "/api/inference/generate/stream",
            json=body(
                "Print the integers 1 through 700, one integer per line. No explanation. Do not skip any numbers.",
                max_tokens=None,
                min_tokens=1100,
                stop=["\n601"],
            ),
        )
    )
    assert usage["completion_tokens"] > 1024, usage
    row, m = metrics(rid)
    report["automatic_output"] = {"id": rid, "usage": usage, "metrics": m}
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
finally:
    client.close()
    with db.connect() as c:
        c.execute("delete from credentials where name=?", (name,))
