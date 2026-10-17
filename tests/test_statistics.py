"""Unit tests for pure statistics logic (no Home Assistant)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ellevio_energy.api import _amount
from custom_components.ellevio_energy.statistics import (
    Reading,
    aggregate_hourly,
    build_rows,
    cost_rows,
    hour_cost,
    window_start,
)


def _quarters(start: datetime, count: int, total: float = 0.25) -> list[Reading]:
    return [Reading(start + timedelta(minutes=15 * i), total) for i in range(count)]


def test_quarter_hours_sum_into_utc_hour() -> None:
    readings = _quarters(datetime(2026, 10, 16, 0, 0, tzinfo=UTC), 4, total=0.5)
    assert list(aggregate_hourly(readings).values()) == [2.0]


def test_partial_hour_dropped() -> None:
    assert aggregate_hourly(_quarters(datetime(2026, 10, 16, tzinfo=UTC), 3)) == {}


def test_missing_status_drops_hour() -> None:
    readings = _quarters(datetime(2026, 10, 16, tzinfo=UTC), 4)
    readings[2] = Reading(readings[2].start, 0.0, status="Missing")
    assert aggregate_hourly(readings) == {}


def test_dst_fall_back_day_has_25_hours() -> None:
    # Europe/Stockholm 2026-10-25: local midnight CEST = 2026-10-24T22:00Z,
    # next local midnight CET = 2026-10-25T23:00Z -> 25 elapsed hours.
    start = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
    hourly = aggregate_hourly(_quarters(start, 25 * 4))
    assert len(hourly) == 25
    assert all(abs(v - 1.0) < 1e-9 for v in hourly.values())


def test_dst_spring_forward_day_has_23_hours() -> None:
    # Europe/Stockholm 2026-03-29: local midnight CET = 2026-03-28T23:00Z,
    # next local midnight CEST = 2026-03-29T22:00Z -> 23 elapsed hours.
    start = datetime(2026, 3, 28, 23, 0, tzinfo=UTC)
    hourly = aggregate_hourly(_quarters(start, 23 * 4))
    assert len(hourly) == 23


def test_running_sum_continuous_across_two_imports() -> None:
    hourly = {datetime(2026, 10, 16, h, tzinfo=UTC): 1.0 for h in range(0, 4)}
    first = build_rows(hourly)
    second = build_rows({datetime(2026, 10, 16, 4, tzinfo=UTC): 2.0}, base_sum=first[-1].sum)
    assert first[-1].sum == 4.0
    assert second[0].sum == 6.0


def test_overlapping_reimport_corrects_value_and_later_sums() -> None:
    h0 = datetime(2026, 10, 16, 0, tzinfo=UTC)
    original = build_rows({h0: 1.0, h0 + timedelta(hours=1): 1.0})
    # Utility restates hour 0 to 3.0; re-import from base before it (0.0).
    corrected = build_rows({h0: 3.0, h0 + timedelta(hours=1): 1.0}, base_sum=0.0)
    assert original[-1].sum == 2.0
    assert corrected[-1].sum == 4.0
    assert corrected[0].start == original[0].start  # same start -> overwrite


def test_window_start_backfill_when_no_prior_data() -> None:
    backfill = datetime(2025, 10, 1, tzinfo=UTC)
    assert window_start(None, backfill, 3) == backfill


def test_window_start_refetches_before_last_import() -> None:
    last = datetime(2026, 10, 16, 5, tzinfo=UTC)
    assert window_start(last, datetime(2025, 1, 1, tzinfo=UTC), 3) == datetime(
        2026, 10, 13, 5, tzinfo=UTC
    )


def test_hour_cost_adds_fixed_share_per_hour() -> None:
    # 2 kWh at 1.5 SEK/kWh plus 720 SEK fixed spread over 720 hours (1 SEK/hour).
    assert hour_cost(2.0, 1.5, 720.0, 720) == 4.0


def test_cost_rows_continue_across_imports() -> None:
    first = build_rows({datetime(2026, 10, 16, h, tzinfo=UTC): 2.0 for h in range(2)})
    c1 = cost_rows(first, [3.0, 3.0])
    later = build_rows({datetime(2026, 10, 16, 2, tzinfo=UTC): 1.0}, base_sum=first[-1].sum)
    c2 = cost_rows(later, [1.5], base_sum=c1[-1].sum)
    assert c1[-1].sum == 6.0
    assert c2[-1].sum == 7.5


def test_cost_rows_rejects_length_mismatch() -> None:
    rows = build_rows({datetime(2026, 10, 16, 0, tzinfo=UTC): 1.0})
    with pytest.raises(ValueError):
        cost_rows(rows, [1.0, 2.0])


def test_amount_parses_swedish_price_strings() -> None:
    assert _amount("450,00 kr/mån") == 450.0
    assert _amount("26,00 öre/kWh") == 0.26
    assert _amount("45,00 öre/kWh") == 0.45
