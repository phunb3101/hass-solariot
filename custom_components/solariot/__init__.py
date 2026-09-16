"""Solariot — read your own solar system from the Solariot portal.

Read-only by design: no switch, number or button platform is registered, so
there is no code path through which this integration can change anything on an
inverter. The API key it uses is refused on every write by the server as well.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SolariotApi
from .const import BASE_URL, CONF_API_KEY, DOMAIN
from .coordinator import SolariotCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # BASE_URL, not entry.data — an entry created before the address was fixed
    # still carries whatever was typed then, and must not keep using it.
    api = SolariotApi(
        async_get_clientsession(hass),
        BASE_URL,
        entry.data[CONF_API_KEY],
    )
    coordinator = SolariotCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.start_stream()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: SolariotCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unloaded
