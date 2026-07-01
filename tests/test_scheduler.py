"""
tests/test_scheduler.py

Pure unit tests against scheduler.candle_scheduler's pure functions. No
subprocess, no real sleep, no SQLite -- all inputs are fixed, timezone-aware
UTC datetimes.
"""
from datetime import datetime, timezone

from scheduler.candle_scheduler import (
    is_processing_time_abnormal,
    next_m15_close,
    next_wake_time,
)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_next_m15_close_mid_interval():
    assert next_m15_close(utc(2026, 7, 1, 10, 7, 0)) == utc(2026, 7, 1, 10, 15, 0)


def test_next_m15_close_just_before_boundary():
    assert next_m15_close(utc(2026, 7, 1, 10, 14, 59)) == utc(2026, 7, 1, 10, 15, 0)


def test_next_m15_close_exactly_on_boundary_returns_next_one():
    # Exactly on a boundary -> the NEXT boundary, not itself.
    assert next_m15_close(utc(2026, 7, 1, 10, 15, 0)) == utc(2026, 7, 1, 10, 30, 0)


def test_next_m15_close_mid_interval_second_half():
    assert next_m15_close(utc(2026, 7, 1, 10, 29, 0)) == utc(2026, 7, 1, 10, 30, 0)


def test_next_m15_close_day_rollover():
    assert next_m15_close(utc(2026, 7, 1, 23, 58, 0)) == utc(2026, 7, 2, 0, 0, 0)


def test_next_m15_close_ignores_seconds_and_microseconds():
    assert next_m15_close(utc(2026, 7, 1, 10, 7, 33, 500_000)) == utc(2026, 7, 1, 10, 15, 0)


def test_next_wake_time_applies_latency_buffer_additively():
    now = utc(2026, 7, 1, 10, 7, 0)
    assert next_wake_time(now, 45) == utc(2026, 7, 1, 10, 15, 45)


def test_is_processing_time_abnormal_below_threshold():
    assert is_processing_time_abnormal(100_000, 300) is False


def test_is_processing_time_abnormal_above_threshold():
    assert is_processing_time_abnormal(350_000, 300) is True


def test_is_processing_time_abnormal_exactly_at_threshold_is_not_abnormal():
    assert is_processing_time_abnormal(300_000, 300) is False
