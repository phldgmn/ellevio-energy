"""Diagnostic sensor: latest hour written to statistics, one per installation."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ANLAGE_IDS, DOMAIN
from .coordinator import EllevioCoordinator, display_name


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EllevioCoordinator = entry.runtime_data
    async_add_entities(
        LastImportedSensor(coordinator, entry, anlage_id)
        for anlage_id in entry.data[CONF_ANLAGE_IDS]
    )


class LastImportedSensor(CoordinatorEntity[EllevioCoordinator], SensorEntity):
    """Timestamp of the latest hour imported for one installation."""

    _attr_has_entity_name = True
    _attr_name = "Last imported hour"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: EllevioCoordinator, entry: ConfigEntry, anlage_id: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._anlage_id = anlage_id
        self._attr_unique_id = f"{anlage_id}_last_imported"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, anlage_id)},
            name=display_name(entry, anlage_id),
            manufacturer="Ellevio",
            model="Installation",
            entry_type=None,
        )

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_imported
