# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Rolling usage totals from recorded requests, independent of history pagination."""

from __future__ import annotations

import time

from . import db


def summary(days: int, *, model: str = "", source: str = "", now: float | None = None) -> dict:
    if days not in (1, 3, 7):
        raise ValueError("Choose 1, 3 or 7 days")
    if source not in {"", "studio", "api"}:
        raise ValueError("Unknown request source")
    until = time.time() if now is None else now
    since = until - days * 86400
    parameters = (since, until, model, model, source, source)
    window = """
            WITH window AS (
              SELECT *, json_extract(metrics, '$.cached_tokens') AS cached,
                json_type(metrics, '$.cached_tokens') AS cache_type
              FROM requests WHERE started>=? AND started<=? AND status IS NOT NULL
                AND source IN ('studio','api')
                AND (?='' OR model=?) AND (?='' OR source=?)
            ), valid AS (
              SELECT *,
                CASE WHEN typeof(prompt_tokens)='integer' AND prompt_tokens>=0
                  THEN prompt_tokens END AS input,
                CASE WHEN typeof(completion_tokens)='integer' AND completion_tokens>=0
                  THEN completion_tokens END AS output,
                CASE WHEN cache_type='integer' AND cached>=0
                  AND typeof(prompt_tokens)='integer' AND cached<=prompt_tokens
                  THEN cached END AS hits
              FROM window
            )
            """
    with db.connect() as conn:
        row = conn.execute(
            window
            + """SELECT count(*) AS requests,
              sum(input) AS input_tokens, count(input) AS input_measured,
              sum(output) AS output_tokens, count(output) AS output_measured,
              sum(hits) AS cached_tokens, count(hits) AS cache_measured,
              sum(CASE WHEN status>=400 THEN 1 ELSE 0 END) AS interrupted_requests,
              sum(CASE WHEN status>=200 AND status<300 THEN 1 ELSE 0 END) AS successful_requests,
              sum(CASE WHEN status=499 THEN 1 ELSE 0 END) AS cancelled_requests
            FROM valid
            """,
            parameters,
        ).fetchone()
        step = {1: 3600, 3: 21600, 7: 86400}[days]
        buckets = {
            int(r["bucket"]): dict(r)
            for r in conn.execute(
                window
                + """
            SELECT min(?, CAST((started-?)/? AS INTEGER)) AS bucket,
              count(*) AS requests, sum(input) AS input_tokens, sum(output) AS output_tokens,
              sum(hits) AS cached_tokens, count(input) AS input_measured,
              count(output) AS output_measured, count(hits) AS cache_measured
            FROM valid GROUP BY bucket
        """,
                (*parameters, days * 86400 // step - 1, since, step),
            )
        }
        active = conn.execute(
            """SELECT count(*) FROM requests WHERE status IS NULL
            AND (?='' OR model=?) AND (?='' OR source=?) AND source IN ('studio','api')""",
            (model, model, source, source),
        ).fetchone()[0]
        model_options = [
            dict(r)
            for r in conn.execute(
                """SELECT model AS id,
            coalesce(max(json_extract(metrics,'$.model_name')),model) AS name
            FROM requests WHERE started>=? AND source IN ('studio','api')
            GROUP BY model ORDER BY max(started) DESC""",
                (until - 7 * 86400,),
            )
        ]
    result = dict(row)
    if not result["requests"]:
        result.update(input_tokens=0, output_tokens=0, cached_tokens=0)
    result["interrupted_requests"] = result["interrupted_requests"] or 0
    result["successful_requests"] = result["successful_requests"] or 0
    result["cancelled_requests"] = result["cancelled_requests"] or 0
    timeline = [
        {
            "at": since + index * step,
            **buckets.get(
                index,
                {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_tokens": 0,
                    "input_measured": 0,
                    "output_measured": 0,
                    "cache_measured": 0,
                },
            ),
        }
        for index in range(days * 86400 // step)
    ]
    return {
        **result,
        "days": days,
        "since": since,
        "until": until,
        "model": model,
        "source": source,
        "active": active,
        "models": model_options,
        "timeline": timeline,
        "bucket_seconds": step,
    }
