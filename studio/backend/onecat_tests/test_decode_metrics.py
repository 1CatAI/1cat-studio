# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import asyncio

import pytest

from onecat.decode_metrics import DecodeAggregator


def event(ids):
    return {"choices": [{"index": 0, "token_ids": ids, "delta": {"content": "x"}}]}


def test_first_batch_buckets_scope_idle_decay_and_restart():
    aggregate = DecodeAggregator()
    for bucket in ("pi", "codex", "chat", "api"):
        aggregate.begin(bucket, source=bucket, bucket=bucket, task_id=bucket, capacity=4, now=0)
        aggregate.observe(bucket, event([1, 2]), now=0.1)
        aggregate.observe(bucket, event([3, 4, 5, 6]), now=0.5)
    result = aggregate.snapshot(2, now=0.6)
    assert result["decode_tokens_s"] == 8
    assert result["buckets"] == {"total": 8, "pi": 2, "codex": 2, "chat": 2, "api": 2}
    assert aggregate.snapshot(2, "pi", now=0.6)["decode_tokens_s"] == 2
    assert result["decode_tokens"] == 16
    assert result["observed_tokens"] == 24
    for bucket in ("pi", "codex", "chat", "api"):
        aggregate.finish(bucket, usage={"completion_tokens": 6}, now=0.7)
    assert aggregate.snapshot(2, now=3)["decode_tokens_s"] == 0
    aggregate.reset()
    assert aggregate.snapshot(2, now=3)["active_requests"] == 0
    assert aggregate.snapshot(2, now=3)["observed_tokens"] == 0


def test_missing_ids_in_one_request_invalidates_total_but_preserves_other_buckets():
    aggregate = DecodeAggregator()
    for bucket in ("pi", "codex"):
        aggregate.begin(bucket, source="agent", bucket=bucket, capacity=2, now=0)
        aggregate.observe(bucket, event([1]), now=0.2)
        aggregate.observe(bucket, event([2]), now=0.3)
    aggregate.observe("pi", {"choices": [{"index": 0, "delta": {"content": "missing"}}]}, now=0.4)
    aggregate.observe("pi", event([3]), now=0.5)
    result = aggregate.snapshot(2, now=0.5)
    assert result["quality"] == "incomplete_token_ids"
    assert result["decode_tokens_s"] is None
    assert result["buckets"]["pi"] is None
    assert result["buckets"]["codex"] == 0.5
    aggregate.finish("pi", usage={"completion_tokens": 4}, now=0.6)
    assert aggregate.snapshot(2, now=0.7)["decode_tokens_s"] is None


def test_request_registration_is_idempotent_and_usage_mismatch_rejects_samples():
    aggregate = DecodeAggregator()
    aggregate.begin("one", source="api", bucket="api", now=0)
    aggregate.observe("one", event([1, 2]), now=0.2)
    aggregate.begin("one", source="api", bucket="api", now=0.3)
    aggregate.observe("one", event([3, 4]), now=0.5)
    assert aggregate.snapshot(2, now=0.5)["decode_tokens_s"] == 1
    assert aggregate.snapshot(2, now=0.5)["active_requests"] == 1
    aggregate.finish("one", usage={"completion_tokens": 9}, now=0.6)
    assert aggregate.snapshot(2, now=0.6)["decode_tokens_s"] is None
    aggregate.observe("one", event([8, 9]), now=0.7)
    assert aggregate.snapshot(2, now=0.7)["observed_tokens"] == 4


@pytest.mark.parametrize("order", [("decoding", "prefill"), ("prefill", "decoding")])
def test_prefill_does_not_hide_other_streams_decode(order):
    aggregate = DecodeAggregator()
    for id in order:
        aggregate.begin(id, source="agent", bucket="pi", capacity=3, now=0)
    aggregate.begin("chat", source="studio", bucket="chat", capacity=3, now=0)
    aggregate.observe("decoding", event([1]), now=.1)
    aggregate.observe("decoding", event([2, 3]), now=.2)
    aggregate.observe("prefill", event([4]), now=.2)
    result = aggregate.snapshot(2, now=.3)
    assert result["decode_tokens_s"] == 1
    assert result["buckets"]["pi"] == 1
    assert result["buckets"]["chat"] is None
    assert result["active_requests"] == 3


@pytest.mark.asyncio
async def test_capacity_queue_measures_wait_and_releases_on_cancel():
    aggregate = DecodeAggregator()
    aggregate.begin("one", source="agent", bucket="pi", capacity=1, now=0)
    aggregate.begin("two", source="agent", bucket="pi", capacity=1, now=0.1)
    waiter = asyncio.create_task(aggregate.wait_for_capacity("two"))
    await asyncio.sleep(0)
    assert not waiter.done()
    assert aggregate.snapshot(2, now=0.5)["waiting_requests"] == 1
    aggregate.finish("one", now=0.8)
    await asyncio.wait_for(waiter, 1)
    assert aggregate.queue_seconds("two") == pytest.approx(0.7)
    assert aggregate.snapshot(2, now=0.8)["active_requests"] == 1
    aggregate.finish("two", now=1)
    assert aggregate.snapshot(2, now=1)["active_requests"] == 0


@pytest.mark.asyncio
async def test_two_admitted_requests_really_overlap_and_double_observed_throughput():
    aggregate = DecodeAggregator()
    entered = set()
    both_entered = asyncio.Event()
    async def stream(id):
        aggregate.begin(id, source="agent", bucket="pi", task_id="swarm", capacity=2, now=0)
        await aggregate.wait_for_capacity(id)
        entered.add(id)
        if len(entered) == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), 1)
        aggregate.observe(id, event([1]), now=0.1)
        aggregate.observe(id, event(list(range(2, 22))), now=0.5)
    await asyncio.gather(stream("a"), stream("b"))
    result = aggregate.snapshot(2, "swarm", now=0.5)
    assert result["active_requests"] == 2
    assert result["waiting_requests"] == 0
    assert result["decode_tokens_s"] == 20  # One stream contributes 20 / 2 = 10.


def test_observed_tokens_use_the_same_rolling_window_as_decode():
    aggregate = DecodeAggregator()
    aggregate.begin("long", source="agent", bucket="pi", now=0)
    aggregate.observe("long", event([1, 2, 3]), now=.1)
    aggregate.observe("long", event([4, 5]), now=.5)
    aggregate.observe("long", event([6]), now=3)
    current = aggregate.snapshot(2, now=3.1)
    assert current["observed_tokens"] == 1
    assert current["decode_tokens"] == 1
    assert current["decode_tokens_s"] == .5
    assert current["bucket_details"]["pi"]["observed_tokens"] == 1
    assert aggregate.snapshot(2, now=5.1)["observed_tokens"] == 0


@pytest.mark.asyncio
async def test_finishing_a_queued_request_wakes_its_capacity_waiter():
    aggregate = DecodeAggregator()
    aggregate.begin("active", source="api", bucket="api", now=0)
    aggregate.begin("cancelled", source="agent", bucket="pi", now=.1)
    waiter = asyncio.create_task(aggregate.wait_for_capacity("cancelled"))
    await asyncio.sleep(0)
    aggregate.finish("cancelled", now=.2)
    finished, _ = await asyncio.wait([waiter], timeout=.2)
    if not finished:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
    assert finished, "Cancellation must release the queued caller without waiting for another request"
    assert waiter.cancelled()
    assert aggregate.snapshot(2, now=.3)["active_requests"] == 1
    assert aggregate.snapshot(2, now=.3)["waiting_requests"] == 0
