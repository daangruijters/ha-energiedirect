"""Data update coordinator for Energiedirect."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    EnergiedirectAuthError,
    EnergiedirectClient,
    EnergiedirectError,
    fetch_dynamic_prices,
)
from .const import (
    CONF_COOKIES,
    DOMAIN,
    PRICES_TIMEZONE,
    PRICES_UPDATE_INTERVAL,
    UPDATE_INTERVAL,
)
from .parsers import (
    Consumption,
    PriceSlot,
    Tariffs,
    parse_budget_bill,
    parse_consumption,
    parse_dynamic_prices,
    parse_tariffs,
)
from .statistics import async_import_statistics

_LOGGER = logging.getLogger(__name__)

type EnergiedirectConfigEntry = ConfigEntry[EnergiedirectCoordinator | DynamicPriceCoordinator]


@dataclass
class AddressData:
    """Everything we know about one supply address."""

    address_id: str
    label: str
    contract_group_id: str | None = None
    products: dict[str, str] = field(default_factory=dict)
    tariffs: dict[str, Tariffs] = field(default_factory=dict)
    consumption: dict[str, Consumption] = field(default_factory=dict)
    budget_bill: dict[str, Any] | None = None


class EnergiedirectCoordinator(DataUpdateCoordinator[dict[str, AddressData]]):
    """Fetches tariffs, usage and budget bill info for all supply addresses."""

    config_entry: EnergiedirectConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: EnergiedirectConfigEntry,
        client: EnergiedirectClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, AddressData]:
        try:
            data = await self._fetch()
        except EnergiedirectAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EnergiedirectError as err:
            raise UpdateFailed(str(err)) from err
        finally:
            self._store_cookies()

        for address in data.values():
            for consumption in address.consumption.values():
                try:
                    await async_import_statistics(self.hass, address.address_id, consumption)
                except Exception:  # noqa: BLE001 - statistics must never break sensors
                    _LOGGER.exception("Importing %s statistics failed", consumption.energy_type)
        return data

    def _store_cookies(self) -> None:
        """Persist rotated session cookies so a restart can continue the session."""
        cookies = self.client.export_cookies()
        if cookies and cookies != self.config_entry.data.get(CONF_COOKIES):
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, CONF_COOKIES: cookies}
            )

    async def _fetch(self) -> dict[str, AddressData]:
        client = self.client
        await client.ensure_session()

        result: dict[str, AddressData] = {}
        addresses = await client.get_supply_addresses()
        active = [a for a in addresses if a.get("contract_status") == "active"] or addresses
        for raw in active:
            address_id = str(raw["supply_address_id"])
            label = " ".join(
                str(p) for p in (raw.get("street"), raw.get("house_number")) if p
            ).strip()
            address = AddressData(address_id=address_id, label=label.title() or address_id)

            for conn in raw.get("connections") or []:
                contract = conn.get("contract") or {}
                energy_type = conn.get("energy_type")
                if not contract.get("id") or not energy_type:
                    continue
                address.contract_group_id = address.contract_group_id or contract.get(
                    "contract_group_id"
                )
                address.products[energy_type] = contract.get("commodity_product_description")
                prices = await client.get_prices(contract["contract_group_id"], contract["id"])
                for tariffs in parse_tariffs(prices):
                    address.tariffs[tariffs.energy_type] = tariffs

            if address.contract_group_id:
                address.consumption = parse_consumption(
                    await client.get_consumption(address_id, address.contract_group_id)
                )
                try:
                    address.budget_bill = parse_budget_bill(
                        await client.get_budget_bill(address_id, address.contract_group_id)
                    )
                except EnergiedirectError as err:
                    _LOGGER.debug("No budget bill info for %s: %s", address_id, err)

            result[address_id] = address
        return result


class DynamicPriceCoordinator(DataUpdateCoordinator[dict[str, list[PriceSlot]]]):
    """Fetches the public dynamic prices; needs no account."""

    config_entry: EnergiedirectConfigEntry

    def __init__(self, hass: HomeAssistant, entry: EnergiedirectConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_dynamic_prices",
            update_interval=PRICES_UPDATE_INTERVAL,
        )
        self.tz = ZoneInfo(PRICES_TIMEZONE)

    async def _async_update_data(self) -> dict[str, list[PriceSlot]]:
        try:
            raw = await fetch_dynamic_prices(async_get_clientsession(self.hass))
        except EnergiedirectError as err:
            raise UpdateFailed(str(err)) from err
        prices = parse_dynamic_prices(raw, self.tz)
        if not prices:
            raise UpdateFailed("No dynamic prices in the response")
        return prices

    def now(self) -> datetime:
        return datetime.now(self.tz)

    @callback
    def start_clock(self) -> None:
        """Update the sensors at every quarter hour, when a new price slot may start."""

        @callback
        def _tick(_now: datetime) -> None:
            self.async_update_listeners()

        self.config_entry.async_on_unload(
            async_track_time_change(self.hass, _tick, minute=(0, 15, 30, 45), second=1)
        )
