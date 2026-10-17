"""Setup removes devices of installations no longer configured."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ellevio_energy.const import CONF_ANLAGE_IDS, DOMAIN

KEEP = "735999000000000001"
GONE = "735999000000000002"


async def test_setup_removes_device_of_dropped_installation(
    recorder_mock: None, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00000000",
        data={
            "email": "user@example.com",
            "password": "not-a-real-password",
            "customer_id": "00000000",
            CONF_ANLAGE_IDS: [KEEP],
        },
        options={"backfill_days": 30, "refetch_days": 3, "poll_hours": 3},
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    for anlage in (KEEP, GONE):
        registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, anlage)})

    with (
        patch(
            "custom_components.ellevio_energy.coordinator.EllevioCoordinator.async_config_entry_first_refresh",
            AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.ellevio_energy.sensor.async_setup_entry",
            AsyncMock(return_value=None),
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    remaining = {
        ident
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
        for domain, ident in device.identifiers
        if domain == DOMAIN
    }
    assert remaining == {KEEP}
