"""reimport service: schema and window validation through the real HA service registry."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ellevio_energy.const import DOMAIN

ENTRY_DATA = {
    "email": "user@example.com",
    "password": "not-a-real-password",
    "customer_id": "00000000",
    "anlage_ids": ["735999000000000001"],
}
OPTIONS = {"backfill_days": 365, "refetch_days": 3, "poll_hours": 3}
IMPORT = "custom_components.ellevio_energy.coordinator.EllevioCoordinator.async_import"


async def _setup(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="00000000", data=ENTRY_DATA, options=OPTIONS)
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.ellevio_energy.coordinator.EllevioCoordinator._async_update_data",
            AsyncMock(return_value=None),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_reimport_registered_and_forwards_window(
    recorder_mock: None, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    await _setup(hass)
    assert hass.services.has_service(DOMAIN, "reimport")
    with patch(IMPORT, AsyncMock(return_value=None)) as mocked:
        await hass.services.async_call(
            DOMAIN,
            "reimport",
            {"start_date": "2026-10-01", "end_date": "2026-10-03"},
            blocking=True,
        )
    mocked.assert_awaited_once_with(window=(date(2026, 10, 1), date(2026, 10, 3)))


async def test_reimport_rejects_end_before_start(
    recorder_mock: None, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    await _setup(hass)
    with pytest.raises(Exception, match="end_date must be on or after start_date"):
        await hass.services.async_call(
            DOMAIN,
            "reimport",
            {"start_date": "2026-10-05", "end_date": "2026-10-01"},
            blocking=True,
        )
