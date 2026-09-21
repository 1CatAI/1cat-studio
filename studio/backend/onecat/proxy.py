# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""OpenAI-compatible proxy with backend token timing and request-window telemetry."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
from fastapi import HTTPException, Request
from starlette.responses import JSONResponse, StreamingResponse

from . import db, engine, telemetry
from .request_metrics import TokenTiming, engine_metrics, isolated_metrics, usage_cache

pending: set[asyncio.Task] = set()


def connection() -> tuple[str, dict, dict]:
    state = engine.private_state()
    if state.get("state") != "ready":
        raise HTTPException(503, "No model is ready")
    if db.get("engine", "maintenance"):
        raise HTTPException(503, "Model maintenance or calibration is in progress")
    base = f"http://127.0.0.1:{state['port']}"
    headers = {"Authorization": f"Bearer {state['api_key']}"} if state.get("api_key") else {}
    return base, headers, state


def instance_identity(state: dict) -> tuple:
    return (
        state.get("launch_id") or state.get("instance_id"),
        state.get("pid"),
        state.get("process_created"),
        state.get("profile_id"),
        state.get("port"),
    )


def begin(
    model: str, source: str, expected_port: int, *, expected_instance=None
) -> tuple[str, float]:
    id, now = db.uid(), time.time()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            "SELECT data FROM records WHERE bucket='engine' AND id='active'"
        ).fetchone()
        state = json.loads(active[0]) if active else {}
        locked = conn.execute(
            "SELECT 1 FROM records WHERE bucket='engine' AND id='maintenance'"
        ).fetchone()
        if (
            locked
            or state.get("state") != "ready"
            or state.get("port") != expected_port
            or (
                expected_instance is not None
                and instance_identity(state) != tuple(expected_instance)
            )
        ):
            raise HTTPException(503, "Model maintenance is in progress; retry when ready")
        overlap = (
            conn.execute("SELECT COUNT(*) FROM requests WHERE status IS NULL").fetchone()[0] > 0
        )
        if overlap:
            conn.execute("UPDATE requests SET overlapped=1 WHERE status IS NULL")
        # A new request can contaminate delayed Prometheus flushes from an
        # earlier completion even when their token streams never overlapped.
        conn.execute(
            "UPDATE requests SET metrics=json_set(metrics, '$.window_overlap', json('true')) WHERE status IS NOT NULL AND json_extract(metrics,'$.pending')=1"
        )
        conn.execute(
            "INSERT INTO requests(id,started,model,source,overlapped) VALUES(?,?,?,?,?)",
            (id, now, model, source, int(overlap)),
        )
    db.patch("engine", "active", {"last_request_at": now})
    return id, now


def finish(
    id: str,
    started: float,
    status: int,
    usage: dict,
    first: float | None,
    error=None,
    *,
    elapsed=None,
    metrics=None,
):
    with db.connect() as conn:
        conn.execute(
            "UPDATE requests SET status=?,elapsed=?,ttft=?,prompt_tokens=?,completion_tokens=?,error=?,metrics=? WHERE id=?",
            (
                status,
                elapsed if elapsed is not None else time.time() - started,
                first,
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                str(error)[:500] if error else None,
                json.dumps({**(metrics or {}), **usage_cache(usage)}),
                id,
            ),
        )


async def enrich(id, base, headers, before, usage, state, start, end, status, result):
    """Metrics flush asynchronously in vLLM. Never delay token delivery to wait for them."""
    result = {**result, **usage_cache(usage)}
    try:
        # Wait for a sample beyond the end so integration covers both boundaries.
        await asyncio.sleep(telemetry.INTERVAL * 1.2)
        result.update(telemetry.request_energy(state["profile"]["gpu_uuids"], start, end))
        with db.connect() as conn:
            overlap = conn.execute("SELECT overlapped FROM requests WHERE id=?", (id,)).fetchone()
        result["concurrent"] = bool(overlap and overlap[0])
        if status == 200 and before and not result["concurrent"] and result.get("single_sequence"):
            async with httpx.AsyncClient(trust_env=False) as client:
                deadline = time.monotonic() + 6
                while True:
                    after = await engine_metrics(client, base, headers)
                    count = after.get("request_decode_time_seconds_count", 0) - before.get(
                        "request_decode_time_seconds_count", 0
                    )
                    if count >= 1:
                        with db.connect() as conn:
                            row = conn.execute(
                                "SELECT metrics FROM requests WHERE id=?", (id,)
                            ).fetchone()
                        contaminated = (
                            json.loads(row[0] or "{}").get("window_overlap", False) if row else True
                        )
                        if not contaminated:
                            result.update(isolated_metrics(before, after, usage))
                        else:
                            result["window_overlap"] = True
                        break
                    if time.monotonic() >= deadline or not after:
                        break
                    await asyncio.sleep(0.3)
    except (httpx.HTTPError, ValueError, KeyError):
        # Missing telemetry must not turn a successful model response into an error.
        pass
    finally:
        if result.get("concurrent"):
            result["prefill_reason"] = (
                "并发请求无法独立归因 / Concurrent requests cannot be attributed independently"
            )
        elif result.get("prefill_tokens_s") is None:
            result["prefill_reason"] = (
                "缓存计数缺失、全部命中或统计不可归因 / Missing cache counts, fully cached input or ambiguous engine statistics"
            )
        else:
            result["prefill_reason"] = None
        if result.get("decode_tokens_s") is not None:
            result["decode_reason"] = None
        result["pending"] = False
        with db.connect() as conn:
            conn.execute("UPDATE requests SET metrics=? WHERE id=?", (json.dumps(result), id))
        from .chat_metrics import commit

        await asyncio.to_thread(commit, id, result)


async def forward(request: Request, endpoint: str, source: str = "api"):
    base, headers, state = connection()
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(422, "Request body must be an object")
    alias = state["profile"]["served_model_name"]
    if body.get("model") != alias:
        raise HTTPException(404, f"The active model is {alias}")
    if source == "studio":
        from .attachments import prepare_messages
        from .chat_settings import normalize

        body["messages"] = prepare_messages(body.get("messages", []), state["profile"])
        limit = normalize({k: body[k] for k in ("max_tokens", "max_tokens_mode") if k in body})
        body["max_tokens"] = limit["max_tokens"]
        body.pop("max_tokens_mode", None)
        # Omission delegates generation_config, chat-template and multimodal token
        # accounting to vLLM, which knows the actual remaining context budget.
        if body.get("max_tokens") is None:
            body.pop("max_tokens", None)
    stream = body.get("stream", False)
    requested_ids = body.get("return_token_ids", False)
    if stream:
        body["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}
        body["return_token_ids"] = True
    id, started_wall = begin(alias, source, state["port"])
    client = httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=10), trust_env=False)
    before = {}
    started = time.monotonic()
    timing = TokenTiming(started)

    def record(status, usage, error=None):
        end = time.monotonic()
        result = {
            **timing.summary(usage),
            **usage_cache(usage),
            "elapsed_s": end - started,
            "model_name": state["profile"]["name"],
            "pending": True,
            "single_sequence": body.get("n", 1) == 1,
            **(
                {
                    "chat_thread_id": request.headers.get("X-Onecat-Thread-ID"),
                    "chat_message_id": request.headers.get("X-Onecat-Message-ID"),
                }
                if source == "studio"
                else {}
            ),
            "prefill_reason": "等待可归因的服务端统计 / Awaiting attributable engine statistics",
            "decode_reason": None
            if timing.summary(usage).get("decode_tokens_s")
            else "尚无完整 token 计数及有效时间区间 / Incomplete token counts or timing interval",
            "gpu_uuids": state["profile"]["gpu_uuids"],
        }
        if status != 200:
            result.update(
                {
                    "decode_tokens_s": None,
                    "prefill_tokens_s": None,
                    "decode_source": None,
                    "decode_reason": "请求取消或中断 / Cancelled or interrupted request",
                }
            )
        finish(
            id,
            started_wall,
            status,
            usage,
            result["ttft_s"],
            error,
            elapsed=end - started,
            metrics=result,
        )
        task = asyncio.create_task(
            enrich(id, base, headers, before, usage, state, started, end, status, result)
        )
        pending.add(task)
        task.add_done_callback(pending.discard)
        return result

    try:
        with db.connect() as conn:
            unflushed = conn.execute(
                "SELECT count(*) FROM requests WHERE id<>? AND json_extract(metrics,'$.pending')=1",
                (id,),
            ).fetchone()[0]
        before = await engine_metrics(client, base, headers) if not unflushed else {}
        started = time.monotonic()
        timing = TokenTiming(started)
        upstream = await client.send(
            client.build_request("POST", base + endpoint, json=body, headers=headers), stream=True
        )
    except (httpx.HTTPError, asyncio.CancelledError) as error:
        await client.aclose()
        record(499 if isinstance(error, asyncio.CancelledError) else 502, {}, error)
        if isinstance(error, asyncio.CancelledError):
            raise
        raise HTTPException(502, "The inference service could not be reached") from error
    if upstream.status_code >= 400 or not stream:
        try:
            content = await upstream.aread()
            try:
                data = json.loads(content)
            except ValueError:
                data = {"error": {"message": content.decode(errors="replace")[:1500]}}
            record(upstream.status_code, data.get("usage", {}), data.get("error"))
            return JSONResponse(
                data, status_code=upstream.status_code, headers={"X-Request-ID": id}
            )
        except (httpx.HTTPError, asyncio.CancelledError) as error:
            record(499 if isinstance(error, asyncio.CancelledError) else 502, {}, error)
            raise
        finally:
            await upstream.aclose()
            await client.aclose()

    async def events():
        usage, status, error = {}, 200, None
        done = False
        result = None
        last_live = 0.0
        live_update = None
        try:
            async for line in upstream.aiter_lines():
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        done = True
                        break
                    try:
                        event = json.loads(raw)
                        if event.get("error"):
                            status, error = 502, event["error"]
                        if event.get("usage"):
                            usage = event["usage"]
                        now = time.monotonic()
                        timing.observe(event, now)
                        if source == "studio" and now - last_live >= 0.25 and status == 200:
                            live_update = {
                                "onecat_live_metrics": timing.live(now),
                                "request_id": id,
                            }
                            last_live = now
                        if not requested_ids:
                            for choice in event.get("choices", []):
                                choice.pop("token_ids", None)
                            event.pop("prompt_token_ids", None)
                            line = "data: " + json.dumps(event, ensure_ascii=False)
                    except ValueError:
                        pass
                yield (line + "\n").encode()
                # Insert only between complete SSE events; never split upstream
                # data lines or modify the OpenAI-compatible API stream.
                if not line and live_update is not None:
                    yield ("data: " + json.dumps(live_update) + "\n\n").encode()
                    live_update = None
            if not done and status == 200:
                status, error = 502, "Inference stream ended before completion"
                yield b'data: {"error":{"message":"Inference stream disconnected"}}\n\n'
        except asyncio.CancelledError:
            status = 499
            raise
        except httpx.HTTPError as exc:
            status, error = 502, str(exc)
            yield b'data: {"error":{"message":"Inference stream disconnected"}}\n\n'
        finally:
            result = record(status, usage, error)
            await upstream.aclose()
            await client.aclose()
        if source == "studio":
            yield (
                "data: " + json.dumps({"onecat_metrics": result, "request_id": id}) + "\n\n"
            ).encode()
        yield b"data: [DONE]\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "X-Request-ID": id,
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
