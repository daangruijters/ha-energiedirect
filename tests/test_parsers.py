"""Tests for the pure parsers (run with: python -m pytest)."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"

_spec = importlib.util.spec_from_file_location(
    "ed_parsers", ROOT / "custom_components" / "energiedirect" / "parsers.py"
)
p = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = p
_spec.loader.exec_module(p)


def _fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_electricity_tariffs():
    (t,) = p.parse_tariffs(_fixture("prices_electricity"))
    assert t.energy_type == "electricity"
    assert t.unit == "kWh"
    assert t.values == {
        p.PRICE_NORMAL: 0.29867,
        p.PRICE_LOW: 0.29504,
        p.SUPPLY_NORMAL: 0.18782,
        p.SUPPLY_LOW: 0.18419,
        p.FEED_IN: -0.15,
        p.FIXED_SUPPLY_DAY: 0.32819,
        p.NETWORK_DAY: 1.30365,
        p.ENERGY_TAX: 0.11085,
        p.TAX_REDUCTION_DAY: -1.72317,
    }
    # future-dated lines are kept as raw items only
    assert any("vanaf 01-01-2027" in i["description"] for i in t.items)


def test_gas_tariffs():
    (t,) = p.parse_tariffs(_fixture("prices_gas"))
    assert t.unit == "m3"
    assert t.values == {
        p.PRICE_NORMAL: 1.4872,
        p.SUPPLY_NORMAL: 0.7604,
        p.FIXED_SUPPLY_DAY: 0.19692,
        p.NETWORK_DAY: 0.72975,
        p.ENERGY_TAX: 0.7268,
    }


def test_consumption():
    cons = p.parse_consumption(_fixture("consumption"))
    elec, gas = cons["electricity"], cons["gas"]
    assert len(elec.hourly) == 2
    assert elec.hourly[-1].cost == 0.23039
    assert elec.daily[-1].returned == 1.292  # negative in the API, positive here
    assert len(gas.hourly) == 1  # the hour without usage is skipped
    assert gas.hourly[0].start.utcoffset().total_seconds() == 3600


def test_month_to_date():
    elec = p.parse_consumption(_fixture("consumption"))["electricity"]
    mtd = p.month_to_date(elec.daily, date(2026, 11, 16))
    assert mtd.consumption == 53.241
    assert mtd.cost == 16.8
    assert p.month_to_date(elec.daily, date(2026, 12, 1)) is None


def test_budget_bill():
    bb = p.parse_budget_bill(_fixture("budgetbill"))
    assert bb["monthly_payment"] == 260
    assert bb["expected_year_costs"] == 4005
    assert bb["expected_settlement"] == 857
    assert bb["terms_left"] == 4

    data = _fixture("budgetbill")
    data["goals"]["JARNoChange"]["expectedJarPayment"]["direction"] = "Receive"
    assert p.parse_budget_bill(data)["expected_settlement"] == -857


def test_dynamic_prices():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Amsterdam")
    prices = p.parse_dynamic_prices(_fixture("dynamic_prices"), tz)
    assert set(prices) == {p.DYNAMIC_ELECTRICITY, p.DYNAMIC_GAS, p.DYNAMIC_FEED_IN}
    elec = prices[p.DYNAMIC_ELECTRICITY]
    assert len(elec) == 72  # yesterday, today, tomorrow
    assert all(a.start < b.start for a, b in zip(elec, elec[1:], strict=False))

    slot = p.slot_at(elec, datetime(2026, 11, 17, 0, 30, tzinfo=tz))
    assert slot.price == 0.3681
    assert slot.price_ex_vat == 0.30421
    assert slot.components == {"market_price": 0.23675, "purchasing_fee": 0.0205, "tax": 0.11085}
    assert len(p.slots_on(elec, date(2026, 11, 17))) == 24
    assert p.slot_at(elec, datetime(2030, 1, 1, tzinfo=tz)) is None


def test_shape_checks_accept_current_responses():
    assert p.check_tariffs(_fixture("prices_electricity")) is None
    assert p.check_tariffs(_fixture("prices_gas")) is None
    assert p.check_consumption(_fixture("consumption")) is None
    assert p.check_budget_bill(_fixture("budgetbill")) is None
    assert p.check_dynamic_prices(_fixture("dynamic_prices")) is None
    address = {
        "supply_address_id": "1",
        "connections": [{"energy_type": "gas", "contract": {"id": "2", "contract_group_id": "3"}}],
    }
    assert p.check_supply_addresses([address]) is None
    assert p.check_supply_addresses([]) is None  # no addresses is not a structure change

    # Tomorrow's gas prices are published later; the key is then left out.
    prices = _fixture("dynamic_prices")
    del prices["prices"][-1]["gas"]
    assert p.check_dynamic_prices(prices) is None


def test_shape_checks_report_changes():
    assert p.check_supply_addresses({"addresses": []}) == "expected a list, got dict"
    assert "supply_address_id" in p.check_supply_addresses([{"id": "1", "connections": []}])

    assert p.check_tariffs({"prices": {}}) == "no price_details"

    consumption = _fixture("consumption")
    consumption["electricity"]["hourlyUsage"] = consumption["electricity"].pop("hourly")
    assert p.check_consumption(consumption) == "electricity without hourly"

    assert p.check_budget_bill({"plan": {}}) == "no budgetBillPlan, expectedYearCosts"

    prices = _fixture("dynamic_prices")
    prices["prices"][0]["electricity"]["tariffs"][0]["price"] = prices["prices"][0][
        "electricity"
    ]["tariffs"][0].pop("totalAmount")
    assert p.check_dynamic_prices(prices) == "electricity slot without totalAmount"
