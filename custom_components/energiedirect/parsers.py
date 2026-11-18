"""Pure parsing helpers for Mijn Energiedirect API responses (no HA imports)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo
from typing import Any

# --------------------------------------------------------------------- tariffs

# Keys of the parsed tariff dict. Amounts are in EUR incl. VAT, as shown in the portal.
PRICE_NORMAL = "price_normal"  # all-in per kWh/m3 (supply + energy tax)
PRICE_LOW = "price_low"
SUPPLY_NORMAL = "supply_normal"  # variable supply cost per kWh/m3
SUPPLY_LOW = "supply_low"
FEED_IN = "feed_in_compensation"  # per kWh returned above consumption (negative = you receive)
FIXED_SUPPLY_DAY = "fixed_supply_per_day"
NETWORK_DAY = "network_cost_per_day"
ENERGY_TAX = "energy_tax"
TAX_REDUCTION_DAY = "tax_reduction_per_day"


@dataclass
class Tariffs:
    contract_id: str
    energy_type: str
    contract_type: str | None
    proposition: str | None
    unit: str | None  # "kWh" or "m3"
    values: dict[str, float] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)  # every price line, raw


def _unit(raw: str | None) -> str | None:
    return raw.lstrip("/") if raw else None


def parse_tariffs(data: dict[str, Any]) -> list[Tariffs]:
    """Parse /api/billing/prices/v1 into one Tariffs per contract."""
    result: list[Tariffs] = []
    overviews = (data.get("price_details") or {}).get("price_overview") or []
    for overview in overviews:
        for contract in overview.get("price_costs_per_contract") or []:
            periods = contract.get("price_periods") or []
            if not periods:
                continue
            period = periods[0]
            tariffs = Tariffs(
                contract_id=str(contract.get("contract_id")),
                energy_type=contract.get("energy_type"),
                contract_type=contract.get("contract_type"),
                proposition=period.get("proposition_description"),
                unit=None,
            )
            values = tariffs.values

            for total in (period.get("consumption_price") or {}).get("total_consumption_price") or []:
                desc = (total.get("description") or "").lower()
                amount = total.get("amount")
                if amount is None or str(total.get("zone", "1")) != "1":
                    continue
                tariffs.unit = tariffs.unit or _unit(total.get("unit_of_measurement"))
                if "dal" in desc:
                    values.setdefault(PRICE_LOW, amount)
                else:  # normaal, enkel, or gas
                    values.setdefault(PRICE_NORMAL, amount)

            for group in period.get("price_groups") or []:
                group_desc = (group.get("description") or "").lower()
                for price in group.get("prices") or []:
                    desc = (price.get("description") or "").lower()
                    amount = price.get("unit_price")
                    unit = _unit(price.get("unit_of_measurement"))
                    tariffs.items.append(
                        {
                            "group": group.get("description"),
                            "description": price.get("description"),
                            "price": amount,
                            "unit": unit,
                        }
                    )
                    if amount is None:
                        continue
                    if "vanaf" in desc:  # future-dated lines, kept in items only
                        continue
                    if "netbeheer" in group_desc and unit == "dag":
                        values[NETWORK_DAY] = round(values.get(NETWORK_DAY, 0) + amount, 5)
                    elif desc.startswith("variabele leveringskosten"):
                        key = SUPPLY_LOW if "dal" in desc else SUPPLY_NORMAL
                        values.setdefault(key, amount)
                    elif desc.startswith("vaste leveringskosten"):
                        values.setdefault(FIXED_SUPPLY_DAY, amount)
                    elif desc.startswith("teruglevering boven verbruik"):
                        values.setdefault(FEED_IN, amount)
                    elif desc.startswith("energiebelasting") and unit != "dag":
                        values.setdefault(ENERGY_TAX, amount)
                    elif desc.startswith("vermindering energiebelasting"):
                        values.setdefault(TAX_REDUCTION_DAY, amount)
            result.append(tariffs)
    return result


# ----------------------------------------------------------------- consumption


@dataclass
class Usage:
    start: datetime
    consumption: float  # delivered to you (kWh / m3)
    returned: float  # fed back into the grid, positive number
    cost: float  # EUR incl. VAT, fixed + variable
    cost_fixed: float
    cost_variable: float


@dataclass
class Consumption:
    energy_type: str
    ean: str | None
    hourly: list[Usage]
    daily: list[Usage]


def _parse_series(entries: list[dict[str, Any]] | None) -> list[Usage]:
    out: list[Usage] = []
    for entry in entries or []:
        usage = entry.get("usage") or {}
        cons = usage.get("consumptionBreakdown") or {}
        cost = usage.get("costBreakdown") or {}
        if cons.get("consumption") is None and cons.get("total") is None:
            continue  # no data (yet) for this period
        try:
            start = datetime.fromisoformat(entry["startTimeStamp"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(
            Usage(
                start=start,
                consumption=float(cons.get("consumption") or 0),
                returned=abs(float(cons.get("returnSupply") or 0)),
                cost=float(cost.get("total") or 0),
                cost_fixed=float(cost.get("fixed") or 0),
                cost_variable=float(cost.get("variable") or 0),
            )
        )
    out.sort(key=lambda u: u.start)
    return out


def parse_consumption(data: dict[str, Any]) -> dict[str, Consumption]:
    """Parse /api/energy-insights/consumption-data/v2, keyed by energy type."""
    result: dict[str, Consumption] = {}
    for energy_type in ("electricity", "gas"):
        block = data.get(energy_type)
        if not block:
            continue
        result[energy_type] = Consumption(
            energy_type=energy_type,
            ean=block.get("ean"),
            hourly=_parse_series(block.get("hourly")),
            daily=_parse_series(block.get("daily")),
        )
    return result


def month_to_date(daily: list[Usage], today: date) -> Usage | None:
    """Sum the daily entries of the current calendar month."""
    days = [d for d in daily if d.start.year == today.year and d.start.month == today.month]
    if not days:
        return None
    return Usage(
        start=days[0].start,
        consumption=round(sum(d.consumption for d in days), 3),
        returned=round(sum(d.returned for d in days), 3),
        cost=round(sum(d.cost for d in days), 2),
        cost_fixed=round(sum(d.cost_fixed for d in days), 2),
        cost_variable=round(sum(d.cost_variable for d in days), 2),
    )


# ----------------------------------------------------------------- budget bill


def parse_budget_bill(data: dict[str, Any]) -> dict[str, Any]:
    """Flatten /api/eci/getBudgetBillInsightsV1 into the values we expose."""
    plan = data.get("budgetBillPlan") or {}
    expected = data.get("expectedYearCosts") or {}
    no_change = ((data.get("goals") or {}).get("JARNoChange") or {}).get("expectedJarPayment") or {}

    settlement = no_change.get("amount")
    direction = no_change.get("direction")
    sign = -1 if direction == "Receive" else 1
    if settlement is not None:
        settlement = sign * settlement
    # The API's min/max are best/worst case and can come reversed; normalise.
    bounds = sorted(
        sign * v for v in (no_change.get("min"), no_change.get("max")) if v is not None
    )
    settle_min, settle_max = (bounds[0], bounds[-1]) if bounds else (None, None)

    return {
        "monthly_payment": plan.get("currentBudgetBill"),
        "paid_so_far": plan.get("totalInvoicedBudgetBills"),
        "total_payments_this_year": plan.get("totalBudgetBills"),
        "terms_left": plan.get("numberOfTermsLeft"),
        "plan_start": plan.get("startDate"),
        "plan_end": plan.get("endDate"),
        "next_term_due": (plan.get("firstChangeableTerm") or {}).get("dueDate"),
        "expected_year_costs": expected.get("amount"),
        "expected_year_costs_min": expected.get("min"),
        "expected_year_costs_max": expected.get("max"),
        # Positive = you pay at the annual bill, negative = you get money back.
        "expected_settlement": settlement,
        "expected_settlement_min": settle_min,
        "expected_settlement_max": settle_max,
    }


# -------------------------------------------------------------- dynamic prices

DYNAMIC_ELECTRICITY = "electricity"
DYNAMIC_GAS = "gas"
DYNAMIC_FEED_IN = "feed_in"
_DYNAMIC_KEYS = {
    "electricity": DYNAMIC_ELECTRICITY,
    "gas": DYNAMIC_GAS,
    "electricityFeedIn": DYNAMIC_FEED_IN,
}


@dataclass
class PriceSlot:
    start: datetime  # timezone-aware
    end: datetime
    price: float  # EUR incl. VAT (feed-in: what you receive per kWh)
    price_ex_vat: float
    components: dict[str, float] = field(default_factory=dict)  # market price, fee, tax


def parse_dynamic_prices(data: dict[str, Any], tz: tzinfo) -> dict[str, list[PriceSlot]]:
    """Parse the public dynamic price endpoint into sorted slots per price kind.

    The API gives local wall-clock times without offset; ``tz`` is Europe/Amsterdam.
    """
    result: dict[str, list[PriceSlot]] = {key: [] for key in _DYNAMIC_KEYS.values()}
    for day in data.get("prices") or []:
        for api_key, key in _DYNAMIC_KEYS.items():
            for slot in (day.get(api_key) or {}).get("tariffs") or []:
                try:
                    start = datetime.fromisoformat(slot["startDateTime"])
                    end = datetime.fromisoformat(slot["endDateTime"])
                    price = float(slot["totalAmount"])
                except (KeyError, TypeError, ValueError):
                    continue
                if start.tzinfo is None:
                    start = start.replace(tzinfo=tz)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=tz)
                result[key].append(
                    PriceSlot(
                        start=start,
                        end=end,
                        price=price,
                        price_ex_vat=float(slot.get("totalAmountEx") or 0),
                        components={
                            (g.get("type") or g.get("description") or "").lower(): g["amount"]
                            for g in slot.get("groups") or []
                            if g.get("amount") is not None
                        },
                    )
                )
    for slots in result.values():
        slots.sort(key=lambda s: s.start)
    return {key: slots for key, slots in result.items() if slots}


def slot_at(slots: list[PriceSlot], moment: datetime) -> PriceSlot | None:
    """The slot covering ``moment``."""
    for slot in slots:
        if slot.start <= moment < slot.end:
            return slot
    return None


def slots_on(slots: list[PriceSlot], day: date) -> list[PriceSlot]:
    return [s for s in slots if s.start.date() == day]


# ------------------------------------------------------------- shape checks
#
# The APIs are undocumented and can change. These checks look only at the structure the
# parsers rely on (not at the values), so an empty result for a new customer is fine but a
# renamed or moved field is reported. Each returns None when the shape is as expected, or a
# short description of what is missing.

ENDPOINT_SUPPLY_ADDRESSES = "supply_addresses"
ENDPOINT_TARIFFS = "tariffs"
ENDPOINT_CONSUMPTION = "consumption"
ENDPOINT_BUDGET_BILL = "budget_bill"
ENDPOINT_DYNAMIC_PRICES = "dynamic_prices"


def _missing(obj: Any, *keys: str) -> list[str]:
    return [k for k in keys if not isinstance(obj, dict) or k not in obj]


def check_supply_addresses(data: Any) -> str | None:
    if not isinstance(data, list):
        return f"expected a list, got {type(data).__name__}"
    for address in data:
        if missing := _missing(address, "supply_address_id", "connections"):
            return f"address without {', '.join(missing)}"
        for conn in address.get("connections") or []:
            if missing := _missing(conn, "energy_type", "contract"):
                return f"connection without {', '.join(missing)}"
            if conn.get("contract") and (
                missing := _missing(conn["contract"], "id", "contract_group_id")
            ):
                return f"contract without {', '.join(missing)}"
    return None


def check_tariffs(data: Any) -> str | None:
    if missing := _missing(data, "price_details"):
        return f"no {', '.join(missing)}"
    if missing := _missing(data["price_details"], "price_overview"):
        return f"no price_details.{', '.join(missing)}"
    if not parse_tariffs(data):
        return "no tariffs found in price_overview"
    return None


def check_consumption(data: Any) -> str | None:
    if not isinstance(data, dict):
        return f"expected an object, got {type(data).__name__}"
    blocks = {k: v for k, v in data.items() if k in ("electricity", "gas") and v}
    if not blocks:
        return "no electricity or gas block"
    for energy_type, block in blocks.items():
        if missing := _missing(block, "hourly", "daily"):
            return f"{energy_type} without {', '.join(missing)}"
        for entry in (block.get("hourly") or [])[:1]:
            if missing := _missing(entry, "startTimeStamp", "usage"):
                return f"{energy_type}.hourly entry without {', '.join(missing)}"
    return None


def check_budget_bill(data: Any) -> str | None:
    if missing := _missing(data, "budgetBillPlan", "expectedYearCosts"):
        return f"no {', '.join(missing)}"
    return None


def check_dynamic_prices(data: Any) -> str | None:
    if missing := _missing(data, "prices"):
        return f"no {', '.join(missing)}"
    days = data["prices"]
    if not isinstance(days, list) or not days:
        return "no days in prices"
    for day in days:
        # A kind is left out for a day whose prices aren't published yet (e.g. tomorrow's gas).
        if not isinstance(day, dict) or "date" not in day:
            return "day without date"
        present = [k for k in _DYNAMIC_KEYS if day.get(k)]
        if not present:
            return f"day {day['date']} without {', '.join(_DYNAMIC_KEYS)}"
        for api_key in present:
            if not isinstance(day[api_key], dict) or "tariffs" not in day[api_key]:
                return f"{api_key} without tariffs"
            for slot in day[api_key]["tariffs"] or []:
                if missing := _missing(slot, "startDateTime", "endDateTime", "totalAmount"):
                    return f"{api_key} slot without {', '.join(missing)}"
    return None
