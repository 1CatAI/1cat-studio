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
from .decode_metrics import aggregator as decode_aggregator
from .request_metrics import TokenTiming, engine_metrics, isolated_metrics, usage_cache

pending: set[asyncio.Task] = set()
agent_pending: dict[str, set[asyncio.Task]] = {}


async def cancel_agent(task_id):
    tasks = list(agent_pending.get(task_id, ()))
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def track_agent(context):
    if not context:
        return lambda: None
    task_id, task = context["task_id"], asyncio.current_task()
    agent_pending.setdefault(task_id, set()).add(task)

    def release():
        owners = agent_pending.get(task_id)
        if owners is not None:
            owners.discard(task)
            if not owners:
                agent_pending.pop(task_id, None)
    return release


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
    model: str,
    source: str,
    expected_port: int,
    *,
    expected_instance=None,
    agent_context: dict | None = None,
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
        context = agent_context or {}
        metrics = {"agent_engine": context.get("engine"), "agent_task_id": context.get("task_id"),
                   "swarm_id": context.get("swarm_id"), "queue_s": None, "queue_source": "studio_proxy"}
        conn.execute(
            "INSERT INTO requests(id,started,model,source,overlapped,metrics) VALUES(?,?,?,?,?,?)",
            (id, now, model, source, int(overlap), json.dumps(metrics)),
        )
    db.patch("engine", "active", {"last_request_at": now})
    context = agent_context or {}
    bucket = context.get("engine") if source == "agent" else source
    if bucket not in {"pi", "codex"}:
        bucket = "chat" if source == "studio" else "api"
    decode_aggregator.begin(
        id,
        source=source,
        bucket=bucket,
        task_id=context.get("task_id"),
        capacity=int(state.get("profile", {}).get("max_num_seqs") or 1),
    )
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
    context = metrics if metrics is not None else {}
    queue_s = decode_aggregator.queue_seconds(id)
    if queue_s is not None and context.get("queue_s") is None:
        context["queue_s"] = queue_s
        context["queue_source"] = "studio_proxy"
    decode_aggregator.finish(id, usage=usage)
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
                json.dumps({**context, **usage_cache(usage)}),
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
    context = getattr(request.state, "onecat_agent_context", None)
    if source == "studio" and not context:
        # Studio chat runs intentionally survive browser disconnection.
        return await _forward(request, endpoint, source)
    # While waiting for a capacity slot or upstream response headers there is
    # no StreamingResponse yet to observe disconnects. Cancel these calls too.
    await request.body()
    release = track_agent(context)
    generation = asyncio.create_task(_forward(request, endpoint, source))

    async def disconnected():
        while True:
            message = await request.receive()
            if message["type"] == "http.disconnect":
                return
            await asyncio.sleep(0)

    monitor = asyncio.create_task(disconnected())
    try:
        completed, _ = await asyncio.wait([generation, monitor], return_when=asyncio.FIRST_COMPLETED)
        if generation in completed:
            return await generation
        raise asyncio.CancelledError("Model client disconnected")
    finally:
        monitor.cancel()
        if not generation.done():
            generation.cancel()
        await asyncio.gather(generation, monitor, return_exceptions=True)
        release()


async def _forward(request: Request, endpoint: str, source: str):
    base, headers, state = connection()
    agent_context = getattr(request.state, "onecat_agent_context", None)
    if agent_context:
        source = "agent"
        identity = agent_context.get("engine_identity")
        if identity is not None and tuple(identity) != instance_identity(state):
            raise HTTPException(409, "模型实例已切换，请继续任务 / Model changed; resume the task")
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
    if agent_context:
        from .agent import tasks as agent_tasks
        from .model_controls import thinking_kwargs

        run = agent_tasks.runs.get(agent_context["task_id"])
        if not run or run.cancel_requested or run.shutdown_requested:
            raise HTTPException(409, "Agent task is no longer running")
        body["chat_template_kwargs"] = {
            **(body.get("chat_template_kwargs") or {}),
            **thinking_kwargs(state["profile"], run.record.get("thinking"), run.record.get("thinking_effort")),
        }
    stream = body.get("stream", False)
    requested_ids = body.get("return_token_ids", False)
    if stream:
        body["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}
        body["return_token_ids"] = True
    id, started_wall = begin(
        alias,
        source,
        state["port"],
        expected_instance=instance_identity(state),
        agent_context=agent_context,
    )
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
            "agent_engine": agent_context.get("engine") if agent_context else None,
            "agent_task_id": agent_context.get("task_id") if agent_context else None,
            "swarm_id": agent_context.get("swarm_id") if agent_context else None,
            "queue_s": None,
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
        if agent_context and agent_context.get("task_id"):
            from .agent import tasks as agent_tasks

            run = agent_tasks.runs.get(agent_context["task_id"])
            if run:
                run.record["model_calls"] = run.record.get("model_calls", 0) + 1
                totals = run.record.setdefault(
                    "usage",
                    {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cached_tokens": 0,
                        "missing_calls": 0,
                        "cache_missing_calls": 0,
                    },
                )
                if usage.get("prompt_tokens") is None or usage.get("completion_tokens") is None:
                    totals["missing_calls"] += 1
                else:
                    totals["input_tokens"] += usage["prompt_tokens"]
                    totals["output_tokens"] += usage["completion_tokens"]
                cached = usage_cache(usage).get("cached_tokens")
                if cached is None:
                    totals["cache_missing_calls"] += 1
                else:
                    totals["cached_tokens"] += cached
                task_metrics = run.record.setdefault(
                    "metrics",
                    {"llm_s": 0, "tool_s": 0, "tool_calls": 0, "ttft_s": 0, "ttft_count": 0, "decode_s": 0, "decode_tokens": 0, "decode_calls": 0},
                )
                task_metrics["llm_s"] += result.get("elapsed_s", 0)
                if result.get("ttft_s") is not None:
                    task_metrics["ttft_s"] += result["ttft_s"]
                    task_metrics["ttft_count"] += 1
                if result.get("decode_s"):
                    task_metrics["decode_s"] += result["decode_s"]
                    task_metrics["decode_tokens"] += max(0, timing.tokens - timing.first_tokens)
                    task_metrics["decode_calls"] += 1
                run.record["live_metrics"] = None
                run.emit({"type": "usage", "usage": dict(totals), "model_calls": run.record["model_calls"], "metrics": task_metrics})
        task = asyncio.create_task(
            enrich(id, base, headers, before, usage, state, started, end, status, result)
        )
        pending.add(task)
        task.add_done_callback(pending.discard)
        return result

    try:
        await decode_aggregator.wait_for_capacity(id)
        with db.connect() as conn:
            unflushed = conn.execute(
                "SELECT count(*) FROM requests WHERE id<>? AND json_extract(metrics,'$.pending')=1",
                (id,),
            ).fetchone()[0]
        before = await engine_metrics(client, base, headers) if not unflushed else {}
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
        release = track_agent(agent_context)
        usage, status, error = {}, 200, None
        done = False
        result = None
        last_live = 0.0
        last_aggregate = 0.0
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
                        decode_aggregator.observe(id, event, now)
                        if agent_context and agent_context.get("task_id"):
                            from .agent import tasks as agent_tasks

                            run = agent_tasks.runs.get(agent_context["task_id"])
                            if run and now - last_aggregate >= 0.25:
                                last_aggregate = now
                                run.record["decode_aggregate"] = decode_aggregator.snapshot(
                                    2, agent_context["task_id"]
                                )
                                run.record["live_metrics"] = timing.live(now)
                                run.emit(
                                    {
                                        "type": "decode_aggregate",
                                        "decode_aggregate": run.record["decode_aggregate"],
                                        "live_metrics": run.record["live_metrics"],
                                    }
                                )
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
            release()
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
