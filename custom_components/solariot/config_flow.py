"""Config flow: server address + API key, checked before anything is stored."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SolariotApi, SolariotAuthError, SolariotConnectionError
from .const import BASE_URL, CONF_API_KEY, CONF_BASE_URL, DOMAIN


class SolariotConfigFlow(ConfigFlow, domain=DOMAIN):
    """Validate at the door.

    A flow that accepts any key and finds out later produces an integration with
    no entities and no explanation, which is the worst of both: the user thinks
    it worked and has nothing to look at.
    """

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}

        if user_input is not None:
            base_url = BASE_URL
            api_key = user_input[CONF_API_KEY].strip()
            api = SolariotApi(async_get_clientsession(self.hass), base_url, api_key)
            try:
                me = await api.async_get_me()
            except SolariotAuthError:
                errors["base"] = "invalid_auth"
            except SolariotConnectionError as err:
                errors["base"] = "cannot_connect"
                description_placeholders["error"] = str(err)
            else:
                # A valid key with nothing behind it is NOT an error, and must
                # not be reported as one — but it does need its own words, or
                # the user goes hunting for a problem with the key.
                if not me.get("deviceCount"):
                    errors["base"] = "no_devices"
                else:
                    await self.async_set_unique_id(f"{base_url}::{me.get('username')}")
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Solariot ({me.get('displayName') or me.get('username')})",
                        data={CONF_BASE_URL: base_url, CONF_API_KEY: api_key},
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            description_placeholders={**description_placeholders, "server": BASE_URL},
            errors=errors,
        )
