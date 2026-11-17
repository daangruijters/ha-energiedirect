"""Import Energiedirect hourly usage and costs as long-term statistics.

Energiedirect publishes usage with a delay of one to two days, so normal
sensors (which record "now") can't place it at the right hour. Instead we write
external statistics that the Energy dashboard can use directly:

    energiedirect:electricity_consumption_<address>   kWh
    energiedirect:electricity_return_<address>        kWh
    energiedirect:electricity_cost_<address>          EUR
    energiedirect:gas_consumption_<address>           m3
    energiedirect:gas_cost_<address>                  EUR
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant

from .const import DOMAIN, ELECTRICITY
from .parsers import Consumption, Usage

_LOGGER = logging.getLogger(__name__)

try:  # HA 2025.4+
    from homeassistant.components.recorder.models import StatisticMeanType
except ImportError:  # pragma: no cover - older HA
    StatisticMeanType = None  # type: ignore[assignment,misc]


def statistic_id(energy_type: str, metric: str, address_id: str) -> str:
    return f"{DOMAIN}:{energy_type}_{metric}_{address_id}".lower()


def _metadata(stat_id: str, name: str, unit: str, unit_class: str | None) -> StatisticMetaData:
    meta: dict[str, Any] = {
        "has_sum": True,
        "name": name,
        "source": DOMAIN,
        "statistic_id": stat_id,
        "unit_of_measurement": unit,
    }
    if StatisticMeanType is not None:
        meta["mean_type"] = StatisticMeanType.NONE
    else:  # pragma: no cover
        meta["has_mean"] = False
    if "unit_class" in getattr(StatisticMetaData, "__annotations__", {}):
        meta["unit_class"] = unit_class
    return meta  # type: ignore[return-value]


async def async_import_statistics(
    hass: HomeAssistant, address_id: str, consumption: Consumption
) -> None:
    """Append new hourly values to the statistics for one energy type."""
    if not consumption.hourly:
        return

    is_elec = consumption.energy_type == ELECTRICITY
    unit = UnitOfEnergy.KILO_WATT_HOUR if is_elec else UnitOfVolume.CUBIC_METERS
    unit_class = "energy" if is_elec else "volume"
    label = "electricity" if is_elec else "gas"

    metrics: list[tuple[str, str, str, str | None, Callable[[Usage], float]]] = [
        ("consumption", f"Energiedirect {label} consumption", unit, unit_class, lambda u: u.consumption),
        ("cost", f"Energiedirect {label} cost", "EUR", None, lambda u: u.cost),
    ]
    if is_elec:
        metrics.insert(
            1,
            ("return", f"Energiedirect {label} return", unit, unit_class, lambda u: u.returned),
        )

    for metric, name, metric_unit, metric_unit_class, value_of in metrics:
        stat_id = statistic_id(consumption.energy_type, metric, address_id)
        last = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 1, stat_id, True, {"sum"}
        )
        last_start: float | None = None
        total = 0.0
        if last.get(stat_id):
            row = last[stat_id][0]
            last_start = row["start"]
            if not isinstance(last_start, (int, float)):  # older HA returned datetime
                last_start = last_start.timestamp()
            total = row.get("sum") or 0.0

        stats: list[StatisticData] = []
        for usage in consumption.hourly:
            if last_start is not None and usage.start.timestamp() <= last_start:
                continue
            value = value_of(usage)
            total += value
            stats.append(StatisticData(start=usage.start, state=value, sum=round(total, 5)))

        if not stats:
            continue
        _LOGGER.debug("Adding %d hours to %s", len(stats), stat_id)
        async_add_external_statistics(
            hass, _metadata(stat_id, name, metric_unit, metric_unit_class), stats
        )
