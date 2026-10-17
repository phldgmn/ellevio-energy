"""Config flow tests against a real HA test instance."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ellevio_energy.api import AuthError, DeliverySite
from custom_components.ellevio_energy.const import (
    CONF_ANLAGE_IDS,
    DOMAIN,
    OPT_NAMES,
)

ANLAGE_A = "735999000000000001"
ANLAGE_B = "735999000000000002"
SITES = [DeliverySite(ANLAGE_A, "Storgatan 1"), DeliverySite(ANLAGE_B, "Lillgatan 2")]

USER_INPUT = {
    "email": "user@example.com",
    "password": "not-a-real-password",
    "customer_id": "00000000",
}

LOGIN = "custom_components.ellevio_energy.config_flow.EllevioClient.async_login"
SITES_FETCH = "custom_components.ellevio_energy.config_flow.EllevioClient.async_get_delivery_sites"
SETUP = "custom_components.ellevio_energy.async_setup_entry"


async def test_user_flow_rejects_bad_login(
    recorder_mock: None, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    with patch(LOGIN, AsyncMock(side_effect=AuthError("rejected"))):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_discovers_then_creates_entry(
    recorder_mock: None, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    with (
        patch(LOGIN, AsyncMock(return_value=None)),
        patch(SITES_FETCH, AsyncMock(return_value=SITES)),
        patch(SETUP, AsyncMock(return_value=True)),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "installations"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ANLAGE_IDS: [ANLAGE_B]}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ANLAGE_IDS] == [ANLAGE_B]
    assert result["options"][OPT_NAMES] == {ANLAGE_B: "Lillgatan 2"}


async def test_user_flow_without_installations_errors(
    recorder_mock: None, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    with (
        patch(LOGIN, AsyncMock(return_value=None)),
        patch(SITES_FETCH, AsyncMock(return_value=[])),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_installations_found"}


async def test_duplicate_customer_aborts(
    recorder_mock: None, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id="00000000", data={}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_adds_and_removes_installations(
    recorder_mock: None, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00000000",
        data={**USER_INPUT, CONF_ANLAGE_IDS: [ANLAGE_A]},
        options={OPT_NAMES: {ANLAGE_A: "Huset"}},
    )
    entry.add_to_hass(hass)
    with (
        patch(LOGIN, AsyncMock(return_value=None)),
        patch(SITES_FETCH, AsyncMock(return_value=SITES)),
        patch(SETUP, AsyncMock(return_value=True)),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "user@example.com", "password": "new-password"}
        )
        assert result["step_id"] == "reconfigure_installations"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ANLAGE_IDS: [ANLAGE_B]}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_ANLAGE_IDS] == [ANLAGE_B]
    assert entry.data["password"] == "new-password"
    # Name of the removed installation is dropped with it.
    assert entry.options[OPT_NAMES] == {ANLAGE_B: "Lillgatan 2"}
