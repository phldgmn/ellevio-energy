"""Diagnostics with credentials and customer/installation IDs redacted."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ANLAGE_IDS, CONF_CUSTOMER_ID, CONF_EMAIL, CONF_PASSWORD

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, CONF_CUSTOMER_ID, CONF_ANLAGE_IDS}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    return {
        "data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
    }
