"""Config flow: credentials, installation discovery, reauth, options."""

from __future__ import annotations

from typing import Any

import probatio as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import SelectOptionDict

from .api import AuthError, DeliverySite, EllevioClient, EllevioError
from .const import (
    CONF_ANLAGE_IDS,
    CONF_CUSTOMER_ID,
    CONF_EMAIL,
    CONF_PASSWORD,
    DEFAULT_BACKFILL_DAYS,
    DEFAULT_POLL_HOURS,
    DEFAULT_REFETCH_DAYS,
    DOMAIN,
    OPT_BACKFILL_DAYS,
    OPT_NAMES,
    OPT_POLL_HOURS,
    OPT_REFETCH_DAYS,
    OPT_SUPPLIER_PRICE,
)

FIELD_INSTALLATION = "installation"
FIELD_NAME = "name"
MENU_SETTINGS = "settings"
MENU_RENAME = "rename"


def _password_selector() -> selector.TextSelector:
    return selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
    )


def _credentials_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_EMAIL, default=defaults.get(CONF_EMAIL, "")): str,
            vol.Required(CONF_PASSWORD): _password_selector(),
            vol.Required(CONF_CUSTOMER_ID, default=defaults.get(CONF_CUSTOMER_ID, "")): str,
        }
    )


def _site_label(site: DeliverySite) -> str:
    return f"{site.address} ({site.anlage_id})" if site.address else site.anlage_id


def _with_current(sites: list[DeliverySite], current_ids: list[str]) -> list[DeliverySite]:
    """Discovered sites plus configured ones the API no longer lists (so they can be removed)."""
    known = {site.anlage_id for site in sites}
    return [*sites, *(DeliverySite(i, "") for i in current_ids if i not in known)]


def _installations_schema(sites: list[DeliverySite], selected: list[str]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_ANLAGE_IDS, default=selected): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=s.anlage_id, label=_site_label(s)) for s in sites
                    ],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
        }
    )


def _remove_devices(hass: HomeAssistant, entry_id: str, anlage_ids: set[str]) -> None:
    """Drop devices (and their entities) of installations no longer configured."""
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry_id):
        if any(domain == DOMAIN and ident in anlage_ids for domain, ident in device.identifiers):
            registry.async_remove_device(device.id)


class EllevioConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._email = ""
        self._password = ""
        self._customer_id = ""
        self._sites: list[DeliverySite] = []

    async def _discover(
        self, email: str, password: str, customer_id: str
    ) -> tuple[dict[str, str], list[DeliverySite]]:
        """Log in and list consumption installations. Returns (form errors, sites)."""
        client = EllevioClient(async_create_clientsession(self.hass))
        try:
            await client.async_login(email, password)
            sites = await client.async_get_delivery_sites(customer_id=customer_id)
        except AuthError:
            return {"base": "invalid_auth"}, []
        except EllevioError:
            return {"base": "cannot_connect"}, []
        return {}, sites

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_CUSTOMER_ID])
            self._abort_if_unique_id_configured()
            errors, sites = await self._discover(
                user_input[CONF_EMAIL], user_input[CONF_PASSWORD], user_input[CONF_CUSTOMER_ID]
            )
            if not errors and not sites:
                errors = {"base": "no_installations_found"}
            if not errors:
                self._email = user_input[CONF_EMAIL]
                self._password = user_input[CONF_PASSWORD]
                self._customer_id = user_input[CONF_CUSTOMER_ID]
                self._sites = sites
                return await self.async_step_installations()
        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema(user_input or {}),
            errors=errors,
        )

    def _default_names(
        self, ids: list[str], current: dict[str, str] | None = None
    ) -> dict[str, str]:
        """Names for `ids`: keep existing, else discovered address, else nothing (fallback)."""
        known = {s.anlage_id: s.address for s in self._sites}
        current = current or {}
        names = {}
        for i in ids:
            if current.get(i):
                names[i] = current[i]
            elif known.get(i):
                names[i] = known[i]
        return names

    async def async_step_installations(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            ids = list(user_input[CONF_ANLAGE_IDS])
            if not ids:
                errors[CONF_ANLAGE_IDS] = "no_installations"
            else:
                return self.async_create_entry(
                    title=f"Ellevio {self._customer_id}",
                    data={
                        CONF_EMAIL: self._email,
                        CONF_PASSWORD: self._password,
                        CONF_CUSTOMER_ID: self._customer_id,
                        CONF_ANLAGE_IDS: ids,
                    },
                    options={
                        OPT_BACKFILL_DAYS: DEFAULT_BACKFILL_DAYS,
                        OPT_REFETCH_DAYS: DEFAULT_REFETCH_DAYS,
                        OPT_POLL_HOURS: DEFAULT_POLL_HOURS,
                        OPT_NAMES: self._default_names(ids),
                    },
                )
        return self.async_show_form(
            step_id="installations",
            data_schema=_installations_schema(self._sites, [s.anlage_id for s in self._sites]),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit login, then add or remove installations."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, sites = await self._discover(
                user_input[CONF_EMAIL], user_input[CONF_PASSWORD], entry.data[CONF_CUSTOMER_ID]
            )
            if not errors:
                self._email = user_input[CONF_EMAIL]
                self._password = user_input[CONF_PASSWORD]
                self._sites = _with_current(sites, entry.data[CONF_ANLAGE_IDS])
                return await self.async_step_reconfigure_installations()
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=entry.data[CONF_EMAIL]): str,
                    vol.Required(CONF_PASSWORD): _password_selector(),
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure_installations(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            ids = list(user_input[CONF_ANLAGE_IDS])
            if not ids:
                errors[CONF_ANLAGE_IDS] = "no_installations"
            else:
                _remove_devices(
                    self.hass, entry.entry_id, set(entry.data[CONF_ANLAGE_IDS]) - set(ids)
                )
                data = {
                    **entry.data,
                    CONF_EMAIL: self._email,
                    CONF_PASSWORD: self._password,
                    CONF_ANLAGE_IDS: ids,
                }
                names = self._default_names(ids, entry.options.get(OPT_NAMES, {}))
                options = {**entry.options, OPT_NAMES: names}
                return self.async_update_reload_and_abort(entry, data=data, options=options)
        return self.async_show_form(
            step_id="reconfigure_installations",
            data_schema=_installations_schema(self._sites, list(entry.data[CONF_ANLAGE_IDS])),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, _ = await self._discover(
                user_input[CONF_EMAIL], user_input[CONF_PASSWORD], entry.data[CONF_CUSTOMER_ID]
            )
            if not errors:
                data = {
                    **entry.data,
                    CONF_EMAIL: user_input[CONF_EMAIL],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                }
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=entry.data[CONF_EMAIL]): str,
                    vol.Required(CONF_PASSWORD): _password_selector(),
                }
            ),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return EllevioOptionsFlow()


def _number(maximum: int) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=1, max=maximum, mode=selector.NumberSelectorMode.BOX)
    )


class EllevioOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=[MENU_SETTINGS, MENU_RENAME])

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # Merge: saving settings must not drop the other keys (names) in options.
        if user_input is not None:
            return self.async_create_entry(data={**self.config_entry.options, **user_input})
        opts = self.config_entry.options
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(OPT_BACKFILL_DAYS, default=opts[OPT_BACKFILL_DAYS]): _number(3650),
                    vol.Required(OPT_REFETCH_DAYS, default=opts[OPT_REFETCH_DAYS]): _number(30),
                    vol.Required(OPT_POLL_HOURS, default=opts[OPT_POLL_HOURS]): _number(24),
                    vol.Required(
                        OPT_SUPPLIER_PRICE, default=opts.get(OPT_SUPPLIER_PRICE, 0)
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=100, step="any", mode=selector.NumberSelectorMode.BOX
                        )
                    ),
                }
            ),
        )

    async def async_step_rename(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Rename one installation. Empty name resets it to the default."""
        entry = self.config_entry
        if user_input is not None:
            anlage_id = user_input[FIELD_INSTALLATION]
            name = user_input.get(FIELD_NAME, "").strip()
            names = dict(entry.options.get(OPT_NAMES, {}))
            if name:
                names[anlage_id] = name
            else:
                names.pop(anlage_id, None)
            return self.async_create_entry(data={**entry.options, OPT_NAMES: names})

        names_now = entry.options.get(OPT_NAMES, {})
        choices = [
            SelectOptionDict(
                value=anlage_id,
                label=f"{names_now.get(anlage_id) or f'Ellevio {anlage_id[-4:]}'} ({anlage_id})",
            )
            for anlage_id in entry.data[CONF_ANLAGE_IDS]
        ]
        return self.async_show_form(
            step_id="rename",
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_INSTALLATION): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=choices, mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    ),
                    vol.Optional(FIELD_NAME, default=""): str,
                }
            ),
        )
