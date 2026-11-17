"""Sensors for Energiedirect: contracted tariffs, recent usage, budget bill and dynamic prices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import parsers as p
from .const import (
    DOMAIN,
    ELECTRICITY,
    ENTRY_TYPE_PRICES,
    GAS,
    MANUFACTURER,
    PRICES_UNIQUE_ID,
)
from .coordinator import (
    AddressData,
    DynamicPriceCoordinator,
    EnergiedirectConfigEntry,
    EnergiedirectCoordinator,
)

EUR = "EUR"
EUR_KWH = f"{EUR}/{UnitOfEnergy.KILO_WATT_HOUR}"
EUR_M3 = f"{EUR}/{UnitOfVolume.CUBIC_METERS}"
EUR_DAY = f"{EUR}/d"


@dataclass(frozen=True, kw_only=True)
class EDSensorDescription(SensorEntityDescription):
    value_fn: Callable[[AddressData], Any]
    attrs_fn: Callable[[AddressData], dict[str, Any] | None] = lambda _: None
    exists_fn: Callable[[AddressData], bool] = lambda _: True


# ------------------------------------------------------------------ tariffs


def _tariff(energy: str, key: str) -> Callable[[AddressData], float | None]:
    def fn(data: AddressData) -> float | None:
        tariffs = data.tariffs.get(energy)
        return tariffs.values.get(key) if tariffs else None

    return fn


def _has_tariff(energy: str, key: str) -> Callable[[AddressData], bool]:
    return lambda data: _tariff(energy, key)(data) is not None


def _tariff_attrs(energy: str) -> Callable[[AddressData], dict[str, Any] | None]:
    def fn(data: AddressData) -> dict[str, Any] | None:
        tariffs = data.tariffs.get(energy)
        if not tariffs:
            return None
        return {
            "product": tariffs.proposition,
            "contract_type": tariffs.contract_type,
            "price_lines": tariffs.items,
        }

    return fn


def _tariff_sensors() -> list[EDSensorDescription]:
    specs = [
        (ELECTRICITY, p.PRICE_NORMAL, "electricity_price_normal", EUR_KWH),
        (ELECTRICITY, p.PRICE_LOW, "electricity_price_low", EUR_KWH),
        (ELECTRICITY, p.SUPPLY_NORMAL, "electricity_supply_price_normal", EUR_KWH),
        (ELECTRICITY, p.SUPPLY_LOW, "electricity_supply_price_low", EUR_KWH),
        (ELECTRICITY, p.ENERGY_TAX, "electricity_energy_tax", EUR_KWH),
        (ELECTRICITY, p.FEED_IN, "electricity_feed_in_compensation", EUR_KWH),
        (ELECTRICITY, p.FIXED_SUPPLY_DAY, "electricity_fixed_supply_cost", EUR_DAY),
        (ELECTRICITY, p.NETWORK_DAY, "electricity_network_cost", EUR_DAY),
        (ELECTRICITY, p.TAX_REDUCTION_DAY, "energy_tax_reduction", EUR_DAY),
        (GAS, p.PRICE_NORMAL, "gas_price", EUR_M3),
        (GAS, p.SUPPLY_NORMAL, "gas_supply_price", EUR_M3),
        (GAS, p.ENERGY_TAX, "gas_energy_tax", EUR_M3),
        (GAS, p.FIXED_SUPPLY_DAY, "gas_fixed_supply_cost", EUR_DAY),
        (GAS, p.NETWORK_DAY, "gas_network_cost", EUR_DAY),
    ]
    return [
        EDSensorDescription(
            key=key,
            translation_key=key,
            native_unit_of_measurement=unit,
            suggested_display_precision=5 if unit != EUR_DAY else 4,
            icon="mdi:cash",
            value_fn=_tariff(energy, tkey),
            attrs_fn=_tariff_attrs(energy) if tkey == p.PRICE_NORMAL else (lambda _: None),
            exists_fn=_has_tariff(energy, tkey),
        )
        for energy, tkey, key, unit in specs
    ]


# -------------------------------------------------------------------- usage


def _last_day(energy: str) -> Callable[[AddressData], p.Usage | None]:
    def fn(data: AddressData) -> p.Usage | None:
        cons = data.consumption.get(energy)
        return cons.daily[-1] if cons and cons.daily else None

    return fn


def _mtd(energy: str) -> Callable[[AddressData], p.Usage | None]:
    def fn(data: AddressData) -> p.Usage | None:
        cons = data.consumption.get(energy)
        return p.month_to_date(cons.daily, dt_util.now().date()) if cons else None

    return fn


def _usage_sensor(
    key: str,
    getter: Callable[[AddressData], p.Usage | None],
    field: str,
    unit: str,
    energy: str,
) -> EDSensorDescription:
    is_cost = field.startswith("cost")
    device_class = (
        SensorDeviceClass.MONETARY
        if is_cost
        else SensorDeviceClass.ENERGY if unit == UnitOfEnergy.KILO_WATT_HOUR else SensorDeviceClass.GAS
    )

    def value(data: AddressData) -> float | None:
        usage = getter(data)
        return getattr(usage, field) if usage else None

    def attrs(data: AddressData) -> dict[str, Any] | None:
        usage = getter(data)
        if not usage:
            return None
        out: dict[str, Any] = {"date": usage.start.date().isoformat()}
        if is_cost:
            out["fixed"] = round(usage.cost_fixed, 2)
            out["variable"] = round(usage.cost_variable, 2)
        return out

    return EDSensorDescription(
        key=key,
        translation_key=key,
        native_unit_of_measurement=unit,
        device_class=device_class,
        suggested_display_precision=2 if is_cost else 3,
        value_fn=value,
        attrs_fn=attrs,
        exists_fn=lambda data: energy in data.consumption,
    )


def _usage_sensors() -> list[EDSensorDescription]:
    kwh = UnitOfEnergy.KILO_WATT_HOUR
    m3 = UnitOfVolume.CUBIC_METERS
    out: list[EDSensorDescription] = []
    for period, getter in (("last_day", _last_day), ("this_month", _mtd)):
        out += [
            _usage_sensor(f"electricity_consumption_{period}", getter(ELECTRICITY), "consumption", kwh, ELECTRICITY),
            _usage_sensor(f"electricity_return_{period}", getter(ELECTRICITY), "returned", kwh, ELECTRICITY),
            _usage_sensor(f"electricity_cost_{period}", getter(ELECTRICITY), "cost", EUR, ELECTRICITY),
            _usage_sensor(f"gas_consumption_{period}", getter(GAS), "consumption", m3, GAS),
            _usage_sensor(f"gas_cost_{period}", getter(GAS), "cost", EUR, GAS),
        ]
    return out


def _last_data_date(data: AddressData) -> date | None:
    dates = [c.hourly[-1].start for c in data.consumption.values() if c.hourly]
    return max(dates).date() if dates else None


# -------------------------------------------------------------- budget bill


def _bb(key: str) -> Callable[[AddressData], Any]:
    return lambda data: (data.budget_bill or {}).get(key)


def _bb_date(key: str) -> Callable[[AddressData], datetime | None]:
    def fn(data: AddressData) -> datetime | None:
        raw = (data.budget_bill or {}).get(key)
        return dt_util.parse_datetime(raw) if raw else None

    return fn


def _budget_sensors() -> list[EDSensorDescription]:
    has_bb = lambda data: bool(data.budget_bill)  # noqa: E731
    money = {"native_unit_of_measurement": EUR, "device_class": SensorDeviceClass.MONETARY}
    return [
        EDSensorDescription(
            key="monthly_payment", translation_key="monthly_payment", **money,
            value_fn=_bb("monthly_payment"), exists_fn=has_bb,
            attrs_fn=lambda d: {
                "paid_so_far": _bb("paid_so_far")(d),
                "total_payments_this_year": _bb("total_payments_this_year")(d),
                "plan_start": _bb("plan_start")(d),
                "plan_end": _bb("plan_end")(d),
            },
        ),
        EDSensorDescription(
            key="expected_year_costs", translation_key="expected_year_costs", **money,
            value_fn=_bb("expected_year_costs"), exists_fn=has_bb,
            attrs_fn=lambda d: {
                "min": _bb("expected_year_costs_min")(d),
                "max": _bb("expected_year_costs_max")(d),
            },
        ),
        EDSensorDescription(
            key="expected_settlement", translation_key="expected_settlement", **money,
            value_fn=_bb("expected_settlement"), exists_fn=has_bb,
            attrs_fn=lambda d: {
                "min": _bb("expected_settlement_min")(d),
                "max": _bb("expected_settlement_max")(d),
                "note": "positive = you pay at the annual bill, negative = you get money back",
            },
        ),
        EDSensorDescription(
            key="payment_terms_left", translation_key="payment_terms_left",
            icon="mdi:calendar-clock", value_fn=_bb("terms_left"), exists_fn=has_bb,
        ),
        EDSensorDescription(
            key="next_payment_due", translation_key="next_payment_due",
            device_class=SensorDeviceClass.TIMESTAMP, value_fn=_bb_date("next_term_due"),
            exists_fn=has_bb,
        ),
    ]


SENSORS: list[EDSensorDescription] = [
    *_tariff_sensors(),
    *_usage_sensors(),
    EDSensorDescription(
        key="data_available_until",
        translation_key="data_available_until",
        device_class=SensorDeviceClass.DATE,
        value_fn=_last_data_date,
        exists_fn=lambda data: bool(data.consumption),
    ),
    *_budget_sensors(),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergiedirectConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    if isinstance(coordinator, DynamicPriceCoordinator):
        async_add_entities(
            DynamicPriceSensor(coordinator, description)
            for description in DYNAMIC_SENSORS
            if description.kind in coordinator.data
        )
        return
    async_add_entities(
        EnergiedirectSensor(coordinator, address_id, description)
        for address_id, address in coordinator.data.items()
        for description in SENSORS
        if description.exists_fn(address)
    )


class EnergiedirectSensor(CoordinatorEntity[EnergiedirectCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: EDSensorDescription

    def __init__(
        self,
        coordinator: EnergiedirectCoordinator,
        address_id: str,
        description: EDSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._address_id = address_id
        self._attr_unique_id = f"{address_id}_{description.key}"
        address = coordinator.data[address_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address_id)},
            name=f"Energiedirect {address.label}",
            manufacturer=MANUFACTURER,
            model=", ".join(v for v in address.products.values() if v) or None,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _address(self) -> AddressData | None:
        return (self.coordinator.data or {}).get(self._address_id)

    @property
    def available(self) -> bool:
        return super().available and self._address is not None

    @property
    def native_value(self) -> Any:
        address = self._address
        return self.entity_description.value_fn(address) if address else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        address = self._address
        return self.entity_description.attrs_fn(address) if address else None


# ----------------------------------------------------------- dynamic prices


@dataclass(frozen=True, kw_only=True)
class DynamicSensorDescription(SensorEntityDescription):
    kind: str  # p.DYNAMIC_ELECTRICITY / DYNAMIC_GAS / DYNAMIC_FEED_IN
    value_fn: Callable[[list[p.PriceSlot], datetime], Any]
    attrs_fn: Callable[[list[p.PriceSlot], datetime], dict[str, Any] | None] = (
        lambda _s, _n: None
    )


def _price_list(slots: list[p.PriceSlot]) -> list[dict[str, Any]]:
    return [{"start": s.start.isoformat(), "end": s.end.isoformat(), "price": s.price} for s in slots]


def _current(slots: list[p.PriceSlot], now: datetime) -> float | None:
    slot = p.slot_at(slots, now)
    return slot.price if slot else None


def _next(slots: list[p.PriceSlot], now: datetime) -> float | None:
    slot = p.slot_at(slots, now)
    following = p.slot_at(slots, slot.end) if slot else None
    return following.price if following else None


def _current_attrs(slots: list[p.PriceSlot], now: datetime) -> dict[str, Any]:
    slot = p.slot_at(slots, now)
    today = now.date()
    attrs: dict[str, Any] = {}
    if slot:
        attrs["price_ex_vat"] = slot.price_ex_vat
        attrs.update(slot.components)
    attrs["today"] = _price_list(p.slots_on(slots, today))
    attrs["tomorrow"] = _price_list(p.slots_on(slots, today + timedelta(days=1)))
    return attrs


def _today_extreme(pick: Callable[..., p.PriceSlot]) -> tuple[Callable, Callable]:
    def value(slots: list[p.PriceSlot], now: datetime) -> float | None:
        today = p.slots_on(slots, now.date())
        return pick(today, key=lambda s: s.price).price if today else None

    def attrs(slots: list[p.PriceSlot], now: datetime) -> dict[str, Any] | None:
        today = p.slots_on(slots, now.date())
        if not today:
            return None
        slot = pick(today, key=lambda s: s.price)
        return {"start": slot.start.isoformat(), "end": slot.end.isoformat()}

    return value, attrs


def _today_average(slots: list[p.PriceSlot], now: datetime) -> float | None:
    today = p.slots_on(slots, now.date())
    return round(sum(s.price for s in today) / len(today), 5) if today else None


_low_value, _low_attrs = _today_extreme(min)
_high_value, _high_attrs = _today_extreme(max)


def _dynamic(key: str, kind: str, unit: str, value_fn, attrs_fn=None, icon="mdi:cash-clock"):
    return DynamicSensorDescription(
        key=key,
        translation_key=key,
        kind=kind,
        native_unit_of_measurement=unit,
        suggested_display_precision=5,
        icon=icon,
        value_fn=value_fn,
        attrs_fn=attrs_fn or (lambda _s, _n: None),
    )


DYNAMIC_SENSORS: list[DynamicSensorDescription] = [
    _dynamic("dynamic_electricity_price", p.DYNAMIC_ELECTRICITY, EUR_KWH, _current, _current_attrs),
    _dynamic("dynamic_electricity_price_next", p.DYNAMIC_ELECTRICITY, EUR_KWH, _next),
    _dynamic("dynamic_electricity_price_lowest_today", p.DYNAMIC_ELECTRICITY, EUR_KWH,
             _low_value, _low_attrs, icon="mdi:arrow-collapse-down"),
    _dynamic("dynamic_electricity_price_average_today", p.DYNAMIC_ELECTRICITY, EUR_KWH,
             _today_average, icon="mdi:approximately-equal"),
    _dynamic("dynamic_electricity_price_highest_today", p.DYNAMIC_ELECTRICITY, EUR_KWH,
             _high_value, _high_attrs, icon="mdi:arrow-collapse-up"),
    _dynamic("dynamic_gas_price", p.DYNAMIC_GAS, EUR_M3, _current, _current_attrs),
    _dynamic("dynamic_feed_in_price", p.DYNAMIC_FEED_IN, EUR_KWH, _current, _current_attrs),
]


class DynamicPriceSensor(CoordinatorEntity[DynamicPriceCoordinator], SensorEntity):
    _attr_has_entity_name = True
    # The price lists are large and change daily; keep them out of the recorder.
    _unrecorded_attributes = frozenset({"today", "tomorrow"})
    entity_description: DynamicSensorDescription

    def __init__(
        self, coordinator: DynamicPriceCoordinator, description: DynamicSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{PRICES_UNIQUE_ID}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, ENTRY_TYPE_PRICES)},
            name="Energiedirect dynamic prices",
            manufacturer=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _slots(self) -> list[p.PriceSlot]:
        return (self.coordinator.data or {}).get(self.entity_description.kind) or []

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._slots, self.coordinator.now())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.entity_description.attrs_fn(self._slots, self.coordinator.now())
