"""Diagnostic sensor reports the latest imported hour, loaded through the real platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ellevio_energy.api import GridPrices
from custom_components.ellevio_energy.const import DOMAIN

GRID = GridPrices(fixed_per_month=450.0, transfer_per_kwh=0.26, tax_per_kwh=0.45)
ANLAGE = "735999000000000001"
ENTRY_DATA = {
    "email": "user@example.com",
    "password": "not-a-real-password",
    "customer_id": "00000000",
    "anlage_ids": [ANLAGE],
}
OPTIONS = {"backfill_days": 30, "refetch_days": 3, "poll_hours": 3}


async def test_last_imported_sensor_created_and_unknown_before_import(
    recorder_mock: None, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="00000000", data=ENTRY_DATA, options=OPTIONS)
    entry.add_to_hass(hass)
    with patch(
        "custom_components.ellevio_energy.coordinator.EllevioCoordinator._async_update_data",
        AsyncMock(return_value=None),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.ellevio_energy_last_imported_hour")
    if state is None:
        matches = [s for s in hass.states.async_all("sensor") if "last_imported" in s.entity_id]
        assert len(matches) == 1, [s.entity_id for s in hass.states.async_all("sensor")]
        state = matches[0]
    assert state.state == "unknown"


async def test_last_imported_sensor_reports_latest_hour_after_import(
    recorder_mock: None, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    from datetime import UTC, datetime, timedelta

    from custom_components.ellevio_energy.api import Consumption

    fixed_day = datetime(2026, 10, 10, tzinfo=UTC)
    payload = [
        Consumption(
            start=(fixed_day + timedelta(minutes=15 * i)).isoformat(),
            end="",
            total=0.25,
            status="Estimated",
        )
        for i in range(96)
    ]
    entry = MockConfigEntry(domain=DOMAIN, unique_id="00000000", data=ENTRY_DATA, options=OPTIONS)
    entry.add_to_hass(hass)
    with patch(
        "custom_components.ellevio_energy.coordinator.EllevioCoordinator._async_update_data",
        AsyncMock(return_value=None),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    fetch = AsyncMock(return_value=payload)
    with (
        patch.object(coordinator.client, "async_login", AsyncMock(return_value=None)),
        patch.object(coordinator.client, "async_get_grid_prices", AsyncMock(return_value=GRID)),
        patch.object(coordinator.client, "async_get_consumption", fetch),
    ):
        await coordinator.async_import(window=(fixed_day.date(), fixed_day.date()))
    await hass.async_block_till_done()

    # Last of 24 hourly rows is 23:00 UTC on the imported day.
    assert coordinator.last_imported == fixed_day.replace(hour=23)
