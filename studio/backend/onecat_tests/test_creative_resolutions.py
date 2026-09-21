# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""The published H3 canvas ladder stays inside the native engine's policy.

The engine pins H3 to a 768 short edge with a 768 x 1344 area budget
(``MINIMAX_H3_OUTPUT_SHORT_EDGE`` / ``MINIMAX_H3_OUTPUT_MAX_PIXELS``) and aligns
every canvas to 32 pixels. These tests keep the offered sizes honest against
that contract without loading a model.
"""

import math

# Mirrors the native engine constants. Duplicated on purpose: a silent change
# on either side has to fail here instead of producing an unsupported canvas.
ENGINE_MAX_PIXELS = 768 * 1344

# Mirrors the ratio list the chat and canvas frontends group sizes by, and the
# 6% tolerance they use before falling back to an exact ratio label.
FRONTEND_RATIOS = [(16, 9), (9, 16), (1, 1), (4, 3), (3, 4), (3, 2), (2, 3), (21, 9)]
FRONTEND_TOLERANCE = 0.06


def frontend_ratio(width, height):
    """Reproduce studio/frontend/src/onecat/creative/output-sizes.ts."""
    closest = min(
        FRONTEND_RATIOS,
        key=lambda ratio: abs(math.log(width / height / (ratio[0] / ratio[1]))),
    )
    if abs(width / height / (closest[0] / closest[1]) - 1) < FRONTEND_TOLERANCE:
        return closest
    divisor = math.gcd(width, height)
    return (width // divisor, height // divisor)


def test_every_canvas_is_aligned_and_inside_the_engine_budget():
    from onecat.creative import output_sizes

    for width, height in output_sizes.H3_SIZES:
        assert width % 32 == 0 and height % 32 == 0, (width, height)
        assert width * height <= ENGINE_MAX_PIXELS, (width, height)
        assert min(width, height) >= 256, (width, height)


def test_official_canvas_tops_every_ratio_ladder():
    from onecat.creative import output_sizes

    for size in output_sizes.OFFICIAL_SIZES:
        assert list(size) in output_sizes.H3_SIZES, size
    # The engine's own planner output is the largest canvas of its ratio.
    from collections import defaultdict

    by_ratio = defaultdict(list)
    for width, height in output_sizes.H3_SIZES:
        by_ratio[frontend_ratio(width, height)].append((width, height))
    for size in output_sizes.OFFICIAL_SIZES:
        ratio = frontend_ratio(*size)
        assert max(by_ratio[ratio], key=lambda item: item[0] * item[1]) == size


def test_regions_partition_the_ladder():
    from onecat.creative import output_sizes

    small = {tuple(size) for size in output_sizes.H3_SMALL_SIZES}
    large = {tuple(size) for size in output_sizes.H3_LARGE_SIZES}
    assert not small & large
    assert small | large == {tuple(size) for size in output_sizes.H3_SIZES}
    assert all(min(size) <= output_sizes.SMALL_SHORT_EDGE for size in small)
    assert all(min(size) >= output_sizes.LARGE_SHORT_EDGE for size in large)


def test_every_canvas_groups_into_a_named_ratio():
    from onecat.creative import output_sizes

    stray = [
        (width, height)
        for width, height in output_sizes.H3_SIZES
        if frontend_ratio(width, height) not in FRONTEND_RATIOS
    ]
    assert stray == []


def test_each_named_ratio_offers_a_full_ladder():
    from collections import defaultdict

    from onecat.creative import output_sizes

    counts = defaultdict(int)
    for width, height in output_sizes.H3_SIZES:
        counts[frontend_ratio(width, height)] += 1
    assert set(counts) == set(FRONTEND_RATIOS)
    # 21:9 is capped by the area budget before the last two short edges fit.
    assert min(counts.values()) >= 7


def test_base_model_owns_the_official_table():
    from onecat.creative.generations import H3_VARIANTS, is_base_variant, output_sizes_for
    from onecat.creative import output_sizes

    assert is_base_variant("h3-int8-20step")
    assert not is_base_variant("h3")
    assert not is_base_variant("native:original")
    assert output_sizes_for("h3-int8-20step", "fl2va") == output_sizes.H3_SIZES
    # 960x544 must stay reachable for the undistilled model.
    assert [960, 544] in output_sizes_for("h3-int8-20step", "fl2va")
    assert set(H3_VARIANTS) >= {"h3", "h3-turbo8", "h3-int8-20step"}


def test_distilled_model_without_a_small_adapter_offers_only_the_large_region():
    from onecat.creative import output_sizes
    from onecat.creative.generations import output_sizes_for

    # h3-turbo8 has no published 544p Ref2VA adapter.
    assert output_sizes_for("h3-turbo8", "ref2va") == output_sizes.H3_LARGE_SIZES
    assert [960, 544] not in output_sizes_for("h3-turbo8", "ref2va")
    # ... while its FL2VA side can switch to the 544p adapter.
    assert output_sizes_for("h3-turbo8", "fl2va") == output_sizes.H3_SIZES
