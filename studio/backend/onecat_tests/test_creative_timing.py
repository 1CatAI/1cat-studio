# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import pytest
from onecat.creative import timing


def test_completed_duration_remains_available_without_native_metadata():
    run = {"created_at": 100, "started_at": 120, "finished_at": 180}
    assert timing.summary(run) == {
        "total_seconds": 80,
        "preparation_seconds": 20,
        "generation_seconds": 60,
        "generation_source": "studio",
    }


def test_native_timing_is_separate_from_preparation_and_saving():
    run = {
        "created_at": 100,
        "started_at": 120,
        "finished_at": 180,
        "native_result": {"end_to_end_seconds": 55.25},
    }
    assert timing.summary(run)["generation_seconds"] == 55.25
    assert timing.summary(run)["generation_source"] == "native"
    assert timing.summary(run)["total_seconds"] == 80


@pytest.mark.parametrize("bad", [None, "12", True, float("nan"), float("inf"), -2])
def test_missing_or_invalid_times_are_not_invented(bad):
    result = timing.summary(
        {"created_at": 100, "finished_at": bad, "native_result": {"end_to_end_seconds": bad}}
    )
    assert result["total_seconds"] is None
    assert result["generation_seconds"] is None


def test_invalid_order_and_old_native_result_do_not_break_history():
    result = timing.summary(
        {"created_at": 200, "started_at": 150, "finished_at": 100, "native_result": "legacy"}
    )
    assert result["total_seconds"] is None
    assert result["generation_seconds"] is None
