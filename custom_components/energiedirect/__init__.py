"""The Energiedirect integration."""

from __future__ import annotations

from aiohttp import CookieJar
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import USER_AGENT, EnergiedirectClient
from .const import CONF_COOKIES, CONF_ENTRY_TYPE, ENTRY_TYPE_ACCOUNT, ENTRY_TYPE_PRICES
from .coordinator import (
    DynamicPriceCoordinator,
    EnergiedirectConfigEntry,
    EnergiedirectCoordinator,
)

PLATFORMS = [Platform.SENSOR]


def create_client(hass: HomeAssistant, cookies: str) -> EnergiedirectClient:
    """Client with its own cookie jar, so sessions of different entries don't mix."""
    session = async_create_clientsession(
        hass,
        cookie_jar=CookieJar(),
        headers={"User-Agent": USER_AGENT},
    )
    return EnergiedirectClient(cookies, session)


def entry_type(entry: EnergiedirectConfigEntry) -> str:
    # Entries created before dynamic prices existed are account entries.
    return entry.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_ACCOUNT)


async def async_setup_entry(hass: HomeAssistant, entry: EnergiedirectConfigEntry) -> bool:
    coordinator: EnergiedirectCoordinator | DynamicPriceCoordinator
    if entry_type(entry) == ENTRY_TYPE_PRICES:
        coordinator = DynamicPriceCoordinator(hass, entry)
        await coordinator.async_config_entry_first_refresh()
        coordinator.start_clock()
    else:
        coordinator = EnergiedirectCoordinator(
            hass, entry, create_client(hass, entry.data[CONF_COOKIES])
        )
        await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnergiedirectConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
