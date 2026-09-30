# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Process-local token-ID measurements and model-capacity admission queue.

Only requests admitted by Studio are counted. The FIFO uses the running model's
max_num_seqs, never PI's child-agent settings. Its queue time is measured at the
proxy boundary; it does not claim to measure vLLM's internal prefill scheduler.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from threading import RLock

BUCKETS = ("pi", "codex", "chat", "api")


@dataclass
class _Request:
    bucket: str
    task_id: str | None
    started: float
    capacity: int
    waiting: bool
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    loop: asyncio.AbstractEventLoop | None = None
    observed: int = 0
    decoded: int = 0
    first_batch: bool = True
    complete_ids: bool = True
    queue_s: float = 0.0
    finished: float | None = None
    last_output: float | None = None

    def wake(self):
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.ready.set)
        else:
            self.ready.set()


class DecodeAggregator:
    def __init__(self, max_age_s=120.0):
        self.max_age_s = max_age_s
        self._lock = RLock()
        self._requests: dict[str, _Request] = {}
        self._samples: deque[tuple[float, str, int, int]] = deque()

    def reset(self):
        with self._lock:
            self._requests.clear()
            self._samples.clear()

    def _prune(self, now):
        cutoff = now - self.max_age_s
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        for key, request in list(self._requests.items()):
            if request.finished is not None and request.finished < cutoff:
                del self._requests[key]

    def begin(self, request_id, *, source, bucket, task_id=None, capacity=1, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            self._prune(now)
            if request_id in self._requests:
                return self._requests[request_id].waiting
            running = sum(r.finished is None and not r.waiting for r in self._requests.values())
            request = _Request(bucket if bucket in BUCKETS else "api", task_id, now,
                               max(1, capacity), running >= max(1, capacity))
            try:
                request.loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
            self._requests[request_id] = request
            if not request.waiting:
                request.ready.set()
            return request.waiting

    async def wait_for_capacity(self, request_id):
        request = self._requests[request_id]
        await request.ready.wait()
        if request.finished is not None:
            raise asyncio.CancelledError("Request ended while queued")

    @staticmethod
    def _token_ids(event):
        total, output, complete = 0, False, True
        for choice in event.get("choices", []):
            ids = choice.get("token_ids")
            delta = choice.get("delta") or {}
            if not (ids or choice.get("text") or any(delta.get(k) for k in
                    ("content", "reasoning_content", "reasoning", "tool_calls"))):
                continue
            output = True
            if (choice.get("index", 0) != 0 or not isinstance(ids, list) or not ids
                    or any(type(token) is not int for token in ids)):
                complete = False
            elif complete:
                total += len(ids)
        return total, output, complete

    def observe(self, request_id, event, now=None):
        now = time.monotonic() if now is None else now
        count, output, complete = self._token_ids(event)
        with self._lock:
            request = self._requests.get(request_id)
            if request is None or request.finished is not None:
                return 0
            usage = event.get("usage") or {}
            if usage.get("completion_tokens") is not None and usage["completion_tokens"] != request.observed + count:
                request.complete_ids = False
                request.last_output = now
            if not output:
                return 0
            request.last_output = now
            request.complete_ids &= complete
            request.observed += count
            decoded = 0
            if request.first_batch:
                request.first_batch = False
            elif request.complete_ids:
                decoded = count
            if count:
                self._samples.append((now, request_id, count, decoded))
            request.decoded += decoded
            self._prune(now)
            return decoded

    def finish(self, request_id, *, usage=None, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            request = self._requests.get(request_id)
            if not request or request.finished is not None:
                return
            if usage and usage.get("completion_tokens") != request.observed:
                request.complete_ids = False
                request.last_output = now
            if request.waiting:
                request.queue_s = max(0, now - request.started)
            request.finished = now
            if request.waiting:
                request.wake()
            running = sum(r.finished is None and not r.waiting for r in self._requests.values())
            for queued in self._requests.values():
                if queued.finished is not None or not queued.waiting:
                    continue
                if running >= queued.capacity:
                    break
                queued.waiting = False
                queued.queue_s = max(0, now - queued.started)
                queued.wake()
                running += 1
            self._prune(now)

    def queue_seconds(self, request_id, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                return None
            return max(0, now - request.started) if request.waiting and request.finished is None else request.queue_s

    def snapshot(self, window_s=2.0, task_id=None, now=None):
        now = time.monotonic() if now is None else now
        window_s = max(0.1, min(float(window_s), 60.0))
        with self._lock:
            self._prune(now)
            cutoff = now - window_s
            requests = {key: r for key, r in self._requests.items() if task_id is None or r.task_id == task_id}
            details = {key: {"decode_tokens": 0, "observed_tokens": 0,
                             "active_requests": 0, "waiting_requests": 0,
                             "quality": "idle", "decode_tokens_s": 0.0} for key in BUCKETS}
            quality_rank = {"idle": 0, "waiting_for_tokens": 1, "complete": 2,
                            "incomplete_token_ids": 3}
            for request in requests.values():
                detail = details[request.bucket]
                quality = "idle"
                if request.finished is None:
                    detail["waiting_requests" if request.waiting else "active_requests"] += 1
                if request.last_output is not None and request.last_output >= cutoff:
                    quality = ("incomplete_token_ids" if not request.complete_ids else
                               "complete" if request.decoded else "waiting_for_tokens")
                elif request.finished is None and not request.waiting:
                    quality = "waiting_for_tokens" if request.first_batch else "complete"
                if request.finished is None and not request.complete_ids:
                    quality = "incomplete_token_ids"
                # A request still in prefill has emitted no decode tokens; it
                # must not hide measured throughput from concurrent streams.
                if quality_rank[quality] > quality_rank[detail["quality"]]:
                    detail["quality"] = quality
            for at, request_id, observed, decoded in self._samples:
                if at >= cutoff and request_id in requests:
                    request = requests[request_id]
                    details[request.bucket]["observed_tokens"] += observed
                    if request.complete_ids:
                        details[request.bucket]["decode_tokens"] += decoded
            for detail in details.values():
                detail["decode_tokens_s"] = None if detail["quality"] in {"incomplete_token_ids", "waiting_for_tokens"} else detail["decode_tokens"] / window_s
            quality = max((d["quality"] for d in details.values()), key=quality_rank.__getitem__)
            total_tokens = sum(d["decode_tokens"] for d in details.values())
            rate = total_tokens / window_s if quality in {"complete", "idle"} else None
            reason = {"incomplete_token_ids": "缺少完整 token ID，聚合速率不可用 / Incomplete token IDs",
                      "waiting_for_tokens": "等待完整 token 流 / Waiting for token stream"}.get(quality)
            return {"at": time.time(), "window_s": window_s, "decode_tokens_s": rate,
                    "active_requests": sum(d["active_requests"] for d in details.values()),
                    "waiting_requests": sum(d["waiting_requests"] for d in details.values()),
                    "observed_tokens": sum(d["observed_tokens"] for d in details.values()),
                    "decode_tokens": total_tokens,
                    "buckets": {"total": rate, **{key: d["decode_tokens_s"] for key, d in details.items()}},
                    "bucket_details": details, "source": "local_token_stream",
                    "queue_source": "studio_proxy", "quality": quality, "reason": reason}


aggregator = DecodeAggregator()
