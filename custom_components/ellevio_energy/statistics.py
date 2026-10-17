"""Pure aggregation and running-sum logic. No Home Assistant imports."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class Reading:
    """One API reading. `start` must be timezone-aware."""

    start: datetime
    total: float | None
    status: str | None = None


@dataclass(frozen=True)
class HourlyRow:
    """One hourly import row. `start` is a UTC top-of-hour."""

    start: datetime
    state: float
    sum: float


def _is_usable(reading: Reading) -> bool:
    return reading.total is not None and reading.status != "Missing"


def aggregate_hourly(
    readings: Iterable[Reading], *, step: timedelta = timedelta(minutes=15)
) -> dict[datetime, float]:
    """Bucket readings into complete UTC hours.

    An hour is kept only if every expected sub-step is present and usable.
    Partial or missing hours are dropped; never zero-filled.
    """
    per_hour: dict[datetime, list[Reading]] = {}
    for reading in readings:
        utc = reading.start.astimezone(UTC)
        hour = utc.replace(minute=0, second=0, microsecond=0)
        per_hour.setdefault(hour, []).append(reading)

    expected = int(timedelta(hours=1) / step)
    result: dict[datetime, float] = {}
    for hour, group in per_hour.items():
        steps = {r.start.astimezone(UTC) for r in group if _is_usable(r)}
        if len(steps) != expected or len(group) != expected:
            continue
        result[hour] = sum(r.total for r in group if r.total is not None)
    return result


def build_rows(hourly: dict[datetime, float], *, base_sum: float = 0.0) -> list[HourlyRow]:
    """Turn hourly deltas into cumulative rows, sorted ascending.

    `base_sum` is the cumulative value at the hour before the first row.
    """
    rows: list[HourlyRow] = []
    running = base_sum
    for hour in sorted(hourly):
        running += hourly[hour]
        rows.append(HourlyRow(start=hour, state=hourly[hour], sum=running))
    return rows


def hour_cost(
    kwh: float,
    per_kwh: float,
    fixed_per_month: float,
    hours_in_month: int,
) -> float:
    """Cost of one hour: energy part plus this hour's share of the monthly fixed fee.

    `per_kwh` is the total SEK/kWh (transfer + tax + supplier energy price).
    The fixed fee is spread evenly over all hours of its local month, so the
    month totals match the bill regardless of how many hours were imported.
    """
    return kwh * per_kwh + fixed_per_month / hours_in_month


def cost_rows(
    rows: list[HourlyRow],
    costs: list[float],
    *,
    base_sum: float = 0.0,
) -> list[HourlyRow]:
    """Cost rows from kWh rows and precomputed per-hour costs (same order, same length).

    Same `start` keys as `rows`, so re-imports overwrite the same hours.
    `base_sum` is the stored cost sum before the first row, for continuity.
    """
    if len(costs) != len(rows):
        raise ValueError("costs must match rows one to one")
    out: list[HourlyRow] = []
    running = base_sum
    for row, cost in zip(rows, costs, strict=True):
        running += cost
        out.append(HourlyRow(start=row.start, state=cost, sum=running))
    return out


def window_start(
    last_imported: datetime | None,
    backfill_start: datetime,
    refetch_days: int,
) -> datetime:
    """Earliest UTC instant to fetch from.

    No prior data: backfill start. Otherwise re-fetch `refetch_days` before
    the last imported hour so utility corrections propagate.
    """
    if last_imported is None:
        return backfill_start
    return last_imported - timedelta(days=refetch_days)
