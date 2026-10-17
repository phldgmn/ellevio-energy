"""Ellevio Energy integration."""

from __future__ import annotations

from datetime import date

import probatio as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import CONF_ANLAGE_IDS, DOMAIN
from .coordinator import EllevioCoordinator

PLATFORMS = [Platform.SENSOR]

SERVICE_REIMPORT = "reimport"
ATTR_START_DATE = "start_date"
ATTR_END_DATE = "end_date"

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_START_DATE): cv.date,
        vol.Optional(ATTR_END_DATE): cv.date,
    }
)


def _remove_stale_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop devices of installations no longer in the entry (e.g. edited outside the UI)."""
    configured = set(entry.data[CONF_ANLAGE_IDS])
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if any(
            domain == DOMAIN and ident not in configured for domain, ident in device.identifiers
        ):
            registry.async_remove_device(device.id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _remove_stale_devices(hass, entry)
    coordinator = EllevioCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _reimport(call: ServiceCall) -> None:
        start: date = call.data[ATTR_START_DATE]
        end: date = call.data.get(ATTR_END_DATE, date.today())
        if end < start:
            msg = "end_date must be on or after start_date"
            raise vol.Invalid(msg)
        await coordinator.async_import(window=(start, end))

    if not hass.services.has_service(DOMAIN, SERVICE_REIMPORT):
        hass.services.async_register(DOMAIN, SERVICE_REIMPORT, _reimport, schema=SERVICE_SCHEMA)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not hass.config_entries.async_loaded_entries(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_REIMPORT)
    return unloaded
