# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Durable task durations, with native generation time kept distinct from waiting."""

import math


def duration(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def between(start, end):
    start, end = duration(start), duration(end)
    return duration(end - start) if start is not None and end is not None else None


def summary(run):
    total = between(run.get("created_at"), run.get("finished_at"))
    preparation = between(run.get("created_at"), run.get("started_at"))
    result = run.get("native_result")
    native = duration(result.get("end_to_end_seconds")) if isinstance(result, dict) else None
    generation = (
        native if native is not None else between(run.get("started_at"), run.get("finished_at"))
    )
    return {
        "total_seconds": total,
        "preparation_seconds": preparation,
        "generation_seconds": generation,
        "generation_source": "native"
        if native is not None
        else "studio"
        if generation is not None
        else None,
    }
