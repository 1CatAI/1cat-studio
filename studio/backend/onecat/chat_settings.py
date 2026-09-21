# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Distinguish an explicit output cap from the old browser's default 1024."""


def normalize(settings):
    result = dict(settings or {})
    limit = result.get("max_tokens")
    mode = result.get("max_tokens_mode")
    if mode not in {None, "auto", "manual"}:
        raise ValueError("Unknown output length mode")
    if mode == "auto" or limit is None or (mode is None and limit == 1024):
        result.update(max_tokens=None, max_tokens_mode="auto")
    else:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Maximum generated tokens must be a positive integer")
        result.update(max_tokens=limit, max_tokens_mode="manual")
    return result
