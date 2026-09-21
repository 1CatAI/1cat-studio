# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import asyncio
import json

import pytest
from onecat import db, proxy, telemetry
from onecat.request_metrics import isolated_metrics, usage_cache
from onecat.token_usage import summary


def insert(id, at, prompt=100, output=20, cache=50, status=200, source="studio"):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO requests(id,started,prompt_tokens,completion_tokens,metrics,status,source) VALUES(?,?,?,?,?,?,?)",
            (id, at, prompt, output, json.dumps({"cached_tokens": cache}), status, source),
        )


def test_rolling_usage_uses_all_records_and_disjoint_cache_subset():
    now = 1_000_000
    for index in range(250):
        insert(str(index), now - 100, source="api" if index % 2 else "studio")
    insert("day2", now - 2 * 86400)
    insert("day6", now - 6 * 86400)
    insert("old", now - 8 * 86400)
    insert("future", now + 1)
    insert("running", now - 1, status=None)
    insert("benchmark", now - 1, source="benchmark")
    one = summary(1, now=now)
    assert one["requests"] == 250  # Not the paginated latest 200 requests.
    assert (one["input_tokens"], one["output_tokens"], one["cached_tokens"]) == (25000, 5000, 12500)
    assert one["cache_measured"] == 250
    assert summary(3, now=now)["requests"] == 251
    assert summary(7, now=now)["requests"] == 252


def test_usage_filters_and_timeline_have_identical_boundaries():
    now = 1_000_000
    insert("first", now - 86400, source="api")
    insert("last", now, source="api")
    insert("chat", now, source="studio")
    with db.connect() as conn:
        conn.execute("UPDATE requests SET model='chosen' WHERE id IN ('first','last')")
    result = summary(1, model="chosen", source="api", now=now)
    assert result["requests"] == 2
    assert sum(row["input_tokens"] or 0 for row in result["timeline"]) == result["input_tokens"]
    assert sum(row["requests"] for row in result["timeline"]) == result["requests"]
    assert result["successful_requests"] == 2
    assert summary(1, model="missing", now=now)["requests"] == 0


def test_unknown_is_not_zero_and_interrupted_known_usage_is_counted():
    now = 1_000_000
    empty = summary(1, now=now)
    assert empty["cached_tokens"] == 0 and empty["requests"] == 0
    insert("unmeasured", now, prompt=None, output=None, cache=None, status=499)
    unknown = summary(1, now=now)
    assert unknown["cached_tokens"] is None and unknown["input_tokens"] is None
    insert("zero", now, cache=0, status=499)
    insert("bad", now, cache=101)
    insert("negative", now, prompt=-1, output=-1, cache=-1)
    result = summary(1, now=now)
    assert result["cached_tokens"] == 0 and result["cache_measured"] == 1
    assert result["input_tokens"] == 200 and result["input_measured"] == 2
    assert result["output_tokens"] == 40 and result["interrupted_requests"] == 2


def test_request_cache_survives_concurrent_enrichment(monkeypatch):
    insert("full", 10, cache=None)
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 100},
    }
    proxy.finish("full", 10, 200, usage, None, elapsed=1, metrics={"pending": True})
    with db.connect() as conn:
        row = conn.execute("SELECT metrics FROM requests WHERE id='full'").fetchone()
        assert json.loads(row[0])["cached_tokens"] == 100
        conn.execute("UPDATE requests SET overlapped=1 WHERE id='full'")
    monkeypatch.setattr(telemetry, "INTERVAL", 0)
    asyncio.run(
        proxy.enrich(
            "full", "unused", {}, {}, usage, {"profile": {"gpu_uuids": []}}, 10, 11, 200, {}
        )
    )
    with db.connect() as conn:
        row = json.loads(conn.execute("SELECT metrics FROM requests WHERE id='full'").fetchone()[0])
    assert row["concurrent"] and row["cached_tokens"] == 100
    assert row["cache_source"] == "request_usage"
    assert not row["pending"]


def test_fully_cached_input_is_recorded_without_fabricating_prefill_speed():
    after = {
        "request_prefill_time_seconds_count": 1,
        "request_prefill_time_seconds_sum": 1,
        "request_decode_time_seconds_count": 1,
        "request_decode_time_seconds_sum": 2,
        "request_prompt_tokens_count": 1,
        "request_prompt_tokens_sum": 100,
        "request_generation_tokens_count": 1,
        "request_generation_tokens_sum": 20,
    }
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 100},
    }
    result = isolated_metrics({"num_requests_running": 0}, after, usage)
    assert result["cached_tokens"] == 100 and "prefill_tokens_s" not in result
    for invalid in [None, -1, 101, "50", True]:
        assert not usage_cache({**usage, "prompt_tokens_details": {"cached_tokens": invalid}})
        assert "prefill_tokens_s" not in isolated_metrics(
            {"num_requests_running": 0},
            after,
            {**usage, "prompt_tokens_details": {"cached_tokens": invalid}},
        )


def test_usage_route_precedes_request_id_and_validates_days(client):
    assert client.get("/api/requests/usage?days=3").json()["days"] == 3
    assert client.get("/api/requests/usage?days=2").status_code == 400
    with pytest.raises(ValueError):
        summary(0)


def test_json_booleans_are_not_token_counts():
    for id, cached in [("true", True), ("false", False), ("number", 2)]:
        insert(id, 1_000_000, cache=cached)
    result = summary(1, now=1_000_000)
    assert result["cached_tokens"] == 2
    assert result["cache_measured"] == 1


def test_exact_window_boundaries_and_timestamp_index():
    now = 1_000_000
    for id, timestamp in [
        ("since", now - 86400),
        ("outside", now - 86400 - 0.01),
        ("until", now),
        ("future", now + 0.01),
    ]:
        insert(id, timestamp)
    assert summary(1, now=now)["requests"] == 2
    with db.connect() as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM requests WHERE started>=? AND started<=?",
            (now - 86400, now),
        ).fetchall()
    assert any("requests_started" in row[3] for row in plan)
