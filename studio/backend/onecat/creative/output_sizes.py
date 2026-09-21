# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""Output canvases and the matching released H3 resolution recipes.

The native engine pins H3 to a 768 short edge with a 768 x 1344 area budget
(``MINIMAX_H3_OUTPUT_SHORT_EDGE`` / ``MINIMAX_H3_OUTPUT_MAX_PIXELS`` in the
engine's ``minimax_h3`` package). Every canvas below is a multiple of 32 and
stays inside that budget, so a request uses the supported path instead of
asking the engine planner to scale an over-budget canvas back down.

A distilled LightX2V adapter is trained at one canvas: the published ``768p``
artifacts at a 768 short edge and the earlier ``v0.1`` artifacts at 544. The
requested canvas therefore selects the adapter distilled for its region, while
the undistilled model stays on the official table.
"""

import math
from pathlib import Path

# Short edges of the official H3 size reference, ascending.
SHORT_EDGES = (352, 416, 480, 544, 608, 640, 672, 736, 768)

# Native engine area budget for one canvas.
MAX_PIXELS = 768 * 1344

# Region boundaries: canvases at or below the small short edge use the 544p
# adapters, canvases at or above the large short edge use the 768p adapters.
SMALL_SHORT_EDGE = 544
LARGE_SHORT_EDGE = 608

# Official canvases, one per supported ratio. Each is the engine planner's own
# output for that ratio, so the recommended canvas is always available exactly.
OFFICIAL_SIZES = (
    (1536, 672),  # 21:9
    (1344, 768),  # 16:9
    (1152, 768),  # 3:2
    (1024, 768),  # 4:3
    (768, 768),   # 1:1
    (768, 1024),  # 3:4
    (768, 1152),  # 2:3
    (768, 1344),  # 9:16
)

# Published 16:9 canvases, kept verbatim: the reference table rounds each axis
# independently, so its ratios drift and recomputing them would drop values.
_PUBLISHED_16_9 = (
    (608, 352),
    (736, 416),
    (864, 480),
    (960, 544),
    (1056, 608),
    (1152, 640),
    (1216, 672),
    (1280, 736),
    (1344, 768),
)


def _align(value):
    return int(math.floor(value / 32 + 0.5)) * 32


def _ladder(width, height):
    """Scale one official canvas down through the published short edges."""
    shortest = min(width, height)
    rows = []
    for short in SHORT_EDGES:
        if short > shortest:
            continue
        factor = short / shortest
        size = [min(_align(width * factor), width), min(_align(height * factor), height)]
        if size[0] * size[1] > MAX_PIXELS or (rows and rows[-1] == size):
            continue
        rows.append(size)
    if rows[-1] != [width, height]:
        rows.append([width, height])
    return rows


def _build():
    rows = []
    for size in OFFICIAL_SIZES:
        rows += (
            [list(item) for item in _PUBLISHED_16_9]
            if size == (1344, 768)
            else _ladder(*size)
        )
    return sorted(rows, key=lambda item: item[0] * item[1])


# Every supported canvas, ascending by area: the full official table.
H3_SIZES = _build()
# Canvases the 544p adapters were distilled for, and the 768p region.
H3_SMALL_SIZES = [size for size in H3_SIZES if min(size) <= SMALL_SHORT_EDGE]
H3_LARGE_SIZES = [size for size in H3_SIZES if min(size) >= LARGE_SHORT_EDGE]

IMAGE_SIZES = [[1024, 1024], [1344, 768], [768, 1344], [1152, 864], [864, 1152]]
IMAGE_SIZES += [[w // 2, h // 2] for w, h in IMAGE_SIZES]

# There is no published 544p Ref2VA 8-step adapter in the verified catalog.
SMALL_H3_RECIPES = {
    ("h3", "fl2va"): "h3-fl2va-turbo4-544p",
    ("h3", "ref2va"): "h3-ref2va-turbo4",
    ("h3-turbo8", "fl2va"): "h3-fl2va-turbo8-544p",
}


def h3_sizes(model, partition, base=False):
    """Sizes the prompt-first flow may ask for.

    ``base`` is the undistilled model, which the official table describes and
    which owns no distilled canvas. Otherwise the small region is offered only
    when a 544p adapter exists, because the requested canvas has to select an
    adapter that was actually distilled for it.
    """
    if base:
        return H3_SIZES
    return H3_SIZES if (model, partition) in SMALL_H3_RECIPES else H3_LARGE_SIZES


def service_sizes(service):
    """Canvas services keep their actual loaded adapter, unlike auto preparation."""
    from . import components
    from .fasth3 import SIZES, is_service

    if is_service(service):
        return SIZES
    if service["kind"] == "image-local":
        return IMAGE_SIZES
    filename = Path(service.get("lora_path") or "").name
    for item in components.catalog():
        if (
            item["id"] in SMALL_H3_RECIPES.values()
            and filename == Path(item["files"][0]["path"]).name
        ):
            # The pre-existing Ref2VA v0.1 recipe also served 768p. Keep it compatible.
            return H3_SMALL_SIZES + (H3_LARGE_SIZES if service.get("partition") == "ref2va" else [])
    return H3_LARGE_SIZES
