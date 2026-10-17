"""Options flow: menu renders, rename persists, settings save keeps names."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.plugins import ClientSessionGenerator

from custom_components.ellevio_energy.const import CONF_ANLAGE_IDS, DOMAIN, OPT_NAMES

ANLAGE_A = "735999000000000001"
ANLAGE_B = "735999000000000002"
DATA = {
    "email": "user@example.com",
    "password": "not-a-real-password",
    "customer_id": "00000000",
    CONF_ANLAGE_IDS: [ANLAGE_A, ANLAGE_B],
}
OPTIONS: dict[str, Any] = {
    "backfill_days": 365,
    "refetch_days": 3,
    "poll_hours": 3,
    OPT_NAMES: {ANLAGE_A: "Huset"},
}


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, unique_id="00000000", title="Ellevio 00000000", data=DATA, options=OPTIONS
    )


async def test_options_menu_renders(
    recorder_mock: None,
    enable_custom_integrations: None,
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "config", {})
    await hass.async_block_till_done()

    client = await hass_client()
    resp = await client.post(
        "/api/config/config_entries/options/flow", json={"handler": entry.entry_id}
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["type"] == "menu"
    assert set(body["menu_options"]) == {"settings", "rename"}


async def test_rename_sets_and_resets_name(
    recorder_mock: None, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "rename"}
    )
    assert result["step_id"] == "rename"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"installation": ANLAGE_B, "name": "Sommarstugan"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[OPT_NAMES] == {ANLAGE_A: "Huset", ANLAGE_B: "Sommarstugan"}

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "rename"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"installation": ANLAGE_A, "name": "  "}
    )
    assert entry.options[OPT_NAMES] == {ANLAGE_B: "Sommarstugan"}


async def test_settings_save_keeps_names(
    recorder_mock: None, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "backfill_days": 30,
            "refetch_days": 3,
            "poll_hours": 3,
            "supplier_price_per_kwh": 0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["backfill_days"] == 30
    assert entry.options[OPT_NAMES] == {ANLAGE_A: "Huset"}
