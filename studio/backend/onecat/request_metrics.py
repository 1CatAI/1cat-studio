# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Request timing uses engine metrics when isolated, otherwise local token arrivals."""

from __future__ import annotations

import math

import httpx

PREFIX = "vllm:"


def usage_cache(usage: dict) -> dict:
    """Per-request usage remains attributable during concurrency and full cache hits."""
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    prompt = usage.get("prompt_tokens")
    if type(cached) is int and type(prompt) is int and 0 <= cached <= prompt:
        return {"cached_tokens": cached, "cache_source": "request_usage"}
    return {}


async def engine_metrics(client, base: str, headers: dict) -> dict:
    try:
        response = await client.get(base + "/metrics", headers=headers, timeout=2)
        response.raise_for_status()
        result = {}
        for line in response.text.splitlines():
            if not line.startswith(PREFIX):
                continue
            key, value = line.rsplit(" ", 1)
            name = key.split("{", 1)[0].removeprefix(PREFIX)
            if name.endswith(("_sum", "_count", "_total")) or name in {
                "num_requests_running",
                "num_requests_waiting",
            }:
                number = float(value)
                if math.isfinite(number):
                    result[name] = result.get(name, 0) + number
        return result
    except (httpx.HTTPError, ValueError):
        return {}


class TokenTiming:
    def __init__(self, started: float):
        self.started = started
        self.first = None
        self.last = None
        self.first_tokens = 0
        self.tokens = 0
        self.complete_ids = True

    def observe(self, event: dict, now: float):
        # Multiple choices have different first/last times; don't turn aggregate
        # throughput into single-sequence decode speed.
        for choice in event.get("choices", []):
            if choice.get("index", 0) != 0:
                self.complete_ids = False
            ids = choice.get("token_ids")
            delta = choice.get("delta", {})
            if not (
                ids
                or choice.get("text")
                or any(
                    delta.get(k)
                    for k in ("content", "reasoning_content", "reasoning", "tool_calls")
                )
            ):
                continue
            count = len(ids) if isinstance(ids, list) else 0
            if self.first is None:
                self.first, self.first_tokens = now, count
            self.last = now
            self.tokens += count
            if not count:
                self.complete_ids = False

    def summary(self, usage: dict) -> dict:
        result = {
            "ttft_s": self.first - self.started if self.first is not None else None,
            "ttft_source": "local_token_stream" if self.first is not None else None,
        }
        if (
            self.complete_ids
            and self.tokens == usage.get("completion_tokens")
            and self.last is not None
            and self.first is not None
            and self.last > self.first
            and self.tokens > self.first_tokens
        ):
            result.update(
                {
                    "decode_tokens_s": (self.tokens - self.first_tokens) / (self.last - self.first),
                    "decode_s": self.last - self.first,
                    "decode_source": "local_token_stream",
                }
            )
        return result

    def live(self, now: float) -> dict:
        result = {
            "output_tokens": self.tokens if self.complete_ids else None,
            "elapsed_s": max(0, now - self.started),
            "ttft_s": self.first - self.started if self.first is not None else None,
            "decode_tokens_s": None,
            "source": "local_token_stream",
            "reason": "waiting_for_tokens",
        }
        if not self.complete_ids:
            result["reason"] = "incomplete_token_ids"
        elif self.first is not None and self.last is not None:
            interval = self.last - self.first
            if interval >= 0.1 and self.tokens > self.first_tokens:
                result["decode_tokens_s"] = (self.tokens - self.first_tokens) / interval
                result["reason"] = None
        return result


def isolated_metrics(before: dict, after: dict, usage: dict) -> dict:
    if not before or not after:
        return {}
    if any(
        snapshot.get("num_requests_running", 0) or snapshot.get("num_requests_waiting", 0)
        for snapshot in (before, after)
    ):
        return {}
    delta = {k: v - before.get(k, 0) for k, v in after.items()}
    if any(
        delta.get(key + "_count") != 1
        for key in ("request_prefill_time_seconds", "request_decode_time_seconds")
    ):
        return {}
    # Reject an unrelated completion or multiple samples, including direct API traffic.
    for key, field in (
        ("request_prompt_tokens", "prompt_tokens"),
        ("request_generation_tokens", "completion_tokens"),
    ):
        if delta.get(key + "_count") != 1 or delta.get(key + "_sum") != usage.get(field):
            return {}
    result = usage_cache(usage)
    if (
        delta.get("time_to_first_token_seconds_count") == 1
        and delta.get("time_to_first_token_seconds_sum", 0) > 0
    ):
        result.update(
            {
                "ttft_s": delta["time_to_first_token_seconds_sum"],
                "ttft_source": "isolated_engine_metrics",
            }
        )
    prefill = delta.get("request_prefill_time_seconds_sum", 0)
    decode = delta.get("request_decode_time_seconds_sum", 0)
    # Cache hits must not inflate the displayed physical prefill throughput.
    cached = result.get("cached_tokens")
    if cached is None and delta.get("prefix_cache_hits_total") == 0:
        cached = 0
        result.update(cached_tokens=0, cache_source="isolated_engine_metrics")
    if prefill > 0 and cached is not None and usage.get("prompt_tokens", 0) > cached:
        result.update(
            {
                "prefill_s": prefill,
                "prefill_tokens_s": (usage["prompt_tokens"] - cached) / prefill,
                "prefill_source": "isolated_engine_metrics",
                "cached_tokens": cached,
            }
        )
    if decode > 0 and usage.get("completion_tokens", 0) > 1:
        result.update(
            {
                "decode_s": decode,
                "decode_tokens_s": (usage["completion_tokens"] - 1) / decode,
                "decode_source": "isolated_engine_metrics",
            }
        )
    return result
