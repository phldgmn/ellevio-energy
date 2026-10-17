"""Import path against the real recorder: writes external statistics and checks continuity."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.components.recorder import (
    get_instance,  # type: ignore[attr-defined]  # HA re-exports without __all__
)
from homeassistant.components.recorder.statistics import (
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ellevio_energy.api import Consumption, GridPrices
from custom_components.ellevio_energy.const import DOMAIN
from custom_components.ellevio_energy.coordinator import EllevioCoordinator, statistic_id

GRID = GridPrices(fixed_per_month=450.0, transfer_per_kwh=0.26, tax_per_kwh=0.45)
ANLAGE = "735999000000000001"
# Fixed "yesterday" so the test never depends on the wall clock.
FIXED_DAY = datetime(2026, 10, 10, tzinfo=UTC)
ENTRY_DATA = {
    "email": "user@example.com",
    "password": "not-a-real-password",
    "customer_id": "00000000",
    "anlage_ids": [ANLAGE],
}
OPTIONS = {"backfill_days": 30, "refetch_days": 3, "poll_hours": 3}


def _day(start: datetime, total: float) -> list[Consumption]:
    """96 quarter-hours for one UTC day, each `total` kWh."""
    return [
        Consumption(
            start=(start + timedelta(minutes=15 * i)).isoformat(),
            end=(start + timedelta(minutes=15 * (i + 1))).isoformat(),
            total=total,
            status="Estimated",
        )
        for i in range(96)
    ]


async def _run_import(hass: HomeAssistant, payload: list[Consumption]) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="00000000", data=ENTRY_DATA, options=OPTIONS)
    entry.add_to_hass(hass)
    coordinator = EllevioCoordinator(hass, entry)

    async def _slice(*, start: date, end: date, **_: object) -> list[Consumption]:
        # Return only readings whose local-date falls in [start, end], like the real chunked API.
        return [c for c in payload if start <= datetime.fromisoformat(c.start).date() <= end]

    fixed_now = FIXED_DAY + timedelta(days=2)  # 1-day lag window covers FIXED_DAY
    with (
        patch("custom_components.ellevio_energy.coordinator._now", return_value=fixed_now),
        patch.object(coordinator.client, "async_login", AsyncMock(return_value=None)),
        patch.object(coordinator.client, "async_get_grid_prices", AsyncMock(return_value=GRID)),
        patch.object(coordinator.client, "async_get_consumption", side_effect=_slice),
    ):
        await coordinator.async_import(window=None)
    # Queued external-statistics writes complete on the recorder, then flush.
    await hass.async_block_till_done()
    await get_instance(hass).async_block_till_done()


async def test_first_import_writes_exact_hourly_sums(
    recorder_mock: None, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    # 96 quarters x 0.25 kWh = 24 kWh per UTC day -> 24 hourly rows of 1.0 kWh.
    # Use yesterday so the coordinator's ~1-day lag window includes it.
    yesterday = FIXED_DAY
    await _run_import(hass, _day(yesterday, 0.25))

    stat_id = statistic_id(ANLAGE)
    rec = get_instance(hass)
    last = await rec.async_add_executor_job(get_last_statistics, hass, 1, stat_id, False, {"sum"})
    assert last[stat_id][0]["sum"] == 24.0

    rows = await rec.async_add_executor_job(
        statistics_during_period,
        hass,
        yesterday,
        yesterday + timedelta(days=1),
        {stat_id},
        "hour",
        None,
        {"state", "sum"},
    )
    assert len(rows[stat_id]) == 24
    assert all(row["state"] == 1.0 for row in rows[stat_id])

    # Cost: 1 kWh/h x (0.26 transfer + 0.45 tax) + fixed 450 / 24 imported hours.
    cost_id = f"{DOMAIN}:cost_{ANLAGE}"
    cost = await rec.async_add_executor_job(
        statistics_during_period,
        hass,
        yesterday,
        yesterday + timedelta(days=1),
        {cost_id},
        "hour",
        None,
        {"state", "sum"},
    )
    expected_hour = 1.0 * (0.26 + 0.45) + 450.0 / 24
    assert len(cost[cost_id]) == 24
    last_sum = cost[cost_id][-1]["sum"]
    assert last_sum is not None
    assert abs(last_sum - 24 * expected_hour) < 1e-6


async def test_reimport_is_idempotent_and_propagates_correction(
    recorder_mock: None, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    yesterday = FIXED_DAY
    await _run_import(hass, _day(yesterday, 0.25))
    # Same window again: must not double the sum.
    await _run_import(hass, _day(yesterday, 0.25))

    stat_id = statistic_id(ANLAGE)
    rec = get_instance(hass)
    last = await rec.async_add_executor_job(get_last_statistics, hass, 1, stat_id, False, {"sum"})
    assert last[stat_id][0]["sum"] == 24.0

    # Utility restates the day at 0.5 kWh/quarter -> 48 kWh; sum must follow.
    await _run_import(hass, _day(yesterday, 0.5))
    last = await rec.async_add_executor_job(get_last_statistics, hass, 1, stat_id, False, {"sum"})
    assert last[stat_id][0]["sum"] == 48.0
