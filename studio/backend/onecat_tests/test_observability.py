# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import asyncio
import json
import sqlite3

import pytest
from onecat import db, engine, power_modes, proxy, telemetry
from onecat.request_metrics import TokenTiming, isolated_metrics


def token_event(ids):
    return {"choices": [{"index": 0, "token_ids": ids, "delta": {"content": "x"}}]}


def test_speculative_batch_and_late_usage_do_not_inflate_decode():
    timing = TokenTiming(10)
    timing.observe(token_event(list(range(8))), 12)
    timing.observe(token_event(list(range(8, 12))), 12.2)
    timing.observe({"choices": [], "usage": {"completion_tokens": 12}}, 17)
    result = timing.summary({"completion_tokens": 12})
    assert result["ttft_s"] == 2
    assert result["decode_tokens_s"] == pytest.approx(20)
    # Missing or inconsistent counts cannot be turned into a speed.
    assert "decode_tokens_s" not in timing.summary({"completion_tokens": 13})
    timing.observe({"choices": [{"delta": {"content": "missing IDs"}}]}, 18)
    assert "decode_tokens_s" not in timing.summary({"completion_tokens": 12})


def test_single_batch_or_multiple_choices_has_no_pure_decode():
    timing = TokenTiming(0)
    timing.observe(token_event([1, 2, 3]), 1)
    assert "decode_tokens_s" not in timing.summary({"completion_tokens": 3})
    timing.observe({"choices": [{"index": 1, "token_ids": [1]}]}, 2)
    assert "decode_tokens_s" not in timing.summary({"completion_tokens": 4})


def test_live_decode_counts_accepted_tokens_not_chunks_or_characters():
    timing = TokenTiming(0)
    assert timing.live(2)["decode_tokens_s"] is None
    timing.observe(token_event(list(range(8))), 3)
    assert timing.live(3)["decode_tokens_s"] is None
    timing.observe(token_event([8, 9, 10, 11]), 3.5)
    assert timing.live(3.5)["decode_tokens_s"] == 8
    assert timing.live(3.5)["output_tokens"] == 12
    assert timing.live(3.5)["ttft_s"] == 3
    # Waiting on metadata is not additional decode time.
    assert timing.live(7)["decode_tokens_s"] == 8
    timing.observe({"choices": [{"delta": {"content": "A very long uncounted chunk"}}]}, 8)
    assert timing.live(8)["decode_tokens_s"] is None
    assert timing.live(8)["output_tokens"] is None
    assert timing.live(8)["reason"] == "incomplete_token_ids"


def test_short_live_reply_does_not_wait_for_a_second_telemetry_tick():
    timing = TokenTiming(0)
    timing.observe(token_event(list(range(8))), 0.055)
    timing.observe(token_event(list(range(8, 68))), 0.304)
    assert timing.live(0.305)["decode_tokens_s"] == pytest.approx(60 / 0.249)
    fast = TokenTiming(0)
    fast.observe(token_event([1]), 0.05)
    fast.observe(token_event([2]), 0.06)
    assert fast.live(0.06)["decode_tokens_s"] is None


@pytest.mark.parametrize("source", ["studio", "api"])
def test_live_metrics_are_framed_between_events_and_private_to_studio(monkeypatch, source):
    import httpx
    from starlette.requests import Request

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(3):
                if index:
                    await asyncio.sleep(0.3)
                event = token_event([index * 4 + i for i in range(4)])
                yield ("data: " + json.dumps(event) + "\n\n").encode()
            yield b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":12}}\n\ndata: [DONE]\n\n'

    async def handler(request):
        return httpx.Response(200, stream=Stream())

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        proxy.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    async def empty(*args):
        return {}

    monkeypatch.setattr(proxy, "engine_metrics", empty)
    monkeypatch.setattr(proxy, "enrich", empty)
    db.put(
        "engine",
        "active",
        {
            "state": "ready",
            "port": 1234,
            "profile": {"name": "test", "served_model_name": "test", "gpu_uuids": []},
        },
    )

    async def run():
        async def receive():
            return {
                "type": "http.request",
                "body": json.dumps(
                    {"model": "test", "messages": [], "stream": True, "return_token_ids": True}
                ).encode(),
            }

        request = Request({"type": "http", "method": "POST", "headers": []}, receive)
        response = await proxy.forward(request, "/v1/chat/completions", source)
        raw = b"".join([chunk async for chunk in response.body_iterator]).decode()
        messages = [
            json.loads(event.removeprefix("data: "))
            for event in raw.split("\n\n")
            if event and event != "data: [DONE]"
        ]
        assert (
            sum(len(c.get("token_ids", [])) for event in messages for c in event.get("choices", []))
            == 12
        )
        live = [event for event in messages if "onecat_live_metrics" in event]
        if source == "studio":
            assert len(live) >= 2
            assert live[-1]["onecat_live_metrics"]["output_tokens"] == 12
            assert live[-1]["onecat_live_metrics"]["decode_tokens_s"] > 0
            assert all(event["request_id"] == response.headers["X-Request-ID"] for event in live)
        else:
            assert not live
        assert raw.endswith("data: [DONE]\n\n")

    asyncio.run(run())


def test_isolation_rejects_concurrency_and_excludes_cache():
    before = {"num_requests_running": 0}
    after = {
        "request_prefill_time_seconds_count": 1,
        "request_prefill_time_seconds_sum": 2,
        "request_decode_time_seconds_count": 1,
        "request_decode_time_seconds_sum": 10,
        "request_prompt_tokens_count": 1,
        "request_prompt_tokens_sum": 8192,
        "request_generation_tokens_count": 1,
        "request_generation_tokens_sum": 101,
    }
    usage = {
        "prompt_tokens": 8192,
        "completion_tokens": 101,
        "prompt_tokens_details": {"cached_tokens": 4096},
    }
    result = isolated_metrics(before, after, usage)
    assert result["prefill_tokens_s"] == 2048
    assert result["decode_tokens_s"] == 10
    assert not isolated_metrics({"num_requests_running": 1}, after, usage)
    assert not isolated_metrics(before, {**after, "request_decode_time_seconds_count": 2}, usage)
    assert not isolated_metrics(before, {**after, "request_generation_tokens_sum": 100}, usage)
    assert "prefill_tokens_s" not in isolated_metrics(
        before, after, {**usage, "prompt_tokens_details": {}}
    )


def test_power_window_integrates_boundaries_and_rejects_gaps():
    telemetry.samples.clear()
    for at, watts in [(10, 100), (10.5, 150), (11, 200)]:
        telemetry.samples.append(
            {
                "monotonic": at,
                "timestamp": at,
                "gpus": [
                    {"uuid": "mine", "power_w": watts},
                    {"uuid": "other", "power_w": 999},
                ],
            }
        )
    result = telemetry.request_energy(["mine"], 10.1, 10.9)
    assert result["average_gpu_w"] == pytest.approx(150)
    assert result["energy_wh"] == pytest.approx(120 / 3600)
    assert not telemetry.request_energy(["mine"], 9.9, 10.9)
    assert not telemetry.request_energy(["missing"], 10.1, 10.9)
    telemetry.samples[1]["gpus"][0]["power_w"] = None
    assert not telemetry.request_energy(["mine"], 10.1, 10.9)
    assert telemetry.live(["mine"])["stale"]
    telemetry.samples.clear()


def test_modes_obey_v100_minimum_and_use_actual_applied_setting(monkeypatch):
    devices = [{"uuid": "mine", "name": "Tesla V100-SXM2-32GB", "power_limit_w": 150}]
    monkeypatch.setattr(power_modes.gpu, "selected_devices", lambda uuids: devices)
    checked = []
    monkeypatch.setattr(
        power_modes.gpu, "validate_setting", lambda ids, setting: checked.append(setting)
    )
    db.put("hardware", "mine", {"graphics_clock_mhz": 975})
    assert power_modes.options(["mine"])["active"] == "eco"
    assert power_modes.setting_for("eco", ["mine"])["power_limit_w"] == 150
    assert power_modes.setting_for("balanced", ["mine"])["power_limit_w"] == 185
    assert power_modes.setting_for("performance", ["mine"])["reset_clocks"]
    assert all(setting["power_limit_w"] >= 150 for setting in checked)
    devices[0]["name"] = "Quadro P400"
    with pytest.raises(ValueError):
        power_modes.setting_for("eco", ["mine"])


def test_modes_report_missing_hardware_without_allowing_changes(monkeypatch):
    def missing(uuids):
        raise ValueError("Selected GPUs are unavailable")

    monkeypatch.setattr(power_modes.gpu, "selected_devices", missing)
    result = power_modes.options(["disconnected-v100"])
    assert result["active"] is None
    assert len(result["items"]) == 3
    assert all(not item["available"] and "不可用" in item["reason"] for item in result["items"])
    with pytest.raises(ValueError, match="unavailable"):
        power_modes.setting_for("performance", ["disconnected-v100"])


def test_request_schema_upgrade_preserves_old_history(state):
    path = state / "onecat.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE requests (id TEXT PRIMARY KEY, started REAL, model TEXT, status INTEGER, elapsed REAL, ttft REAL, prompt_tokens INTEGER, completion_tokens INTEGER, error TEXT, source TEXT)"
        )
        conn.execute("INSERT INTO requests(id,started,completion_tokens) VALUES('old',1,123)")
    with db.connect() as conn:
        row = dict(conn.execute("SELECT * FROM requests WHERE id='old'").fetchone())
    assert row["completion_tokens"] == 123
    assert row["metrics"] is None
    assert row["overlapped"] == 0


def test_cancel_during_metric_probe_releases_request(monkeypatch):
    from starlette.requests import Request

    async def cancelled(*args):
        raise asyncio.CancelledError()

    monkeypatch.setattr(proxy, "engine_metrics", cancelled)
    db.put(
        "engine",
        "active",
        {
            "state": "ready",
            "port": 1234,
            "profile": {"name": "test", "served_model_name": "test", "gpu_uuids": []},
        },
    )

    async def run():
        async def receive():
            return {"type": "http.request", "body": json.dumps({"model": "test"}).encode()}

        request = Request({"type": "http", "method": "POST", "headers": []}, receive)
        with pytest.raises(asyncio.CancelledError):
            await proxy.forward(request, "/v1/chat/completions")
        assert engine.active_requests() == 0
        tasks = list(proxy.pending)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run())
