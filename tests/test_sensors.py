"""Smoke test: the HA modules import and every sensor yields a sane value.

Needs Home Assistant installed; skipped otherwise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("homeassistant")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).parent / "fixtures"

from custom_components.energiedirect import config_flow, statistics  # noqa: E402,F401
from custom_components.energiedirect.coordinator import AddressData  # noqa: E402
from custom_components.energiedirect.parsers import (  # noqa: E402
    parse_budget_bill,
    parse_consumption,
    parse_tariffs,
)
from custom_components.energiedirect.sensor import SENSORS  # noqa: E402


def _fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _address() -> AddressData:
    address = AddressData(address_id="0001", label="Test 1", contract_group_id="g")
    for name in ("prices_electricity", "prices_gas"):
        for t in parse_tariffs(_fixture(name)):
            address.tariffs[t.energy_type] = t
    address.consumption = parse_consumption(_fixture("consumption"))
    address.budget_bill = parse_budget_bill(_fixture("budgetbill"))
    return address


def test_all_sensors_have_values_and_translations():
    address = _address()
    strings = json.loads(
        (ROOT / "custom_components/energiedirect/strings.json").read_text(encoding="utf-8")
    )["entity"]["sensor"]
    missing = []
    for desc in SENSORS:
        assert desc.translation_key in strings, desc.key
        assert desc.exists_fn(address), desc.key
        if desc.value_fn(address) is None:
            missing.append(desc.key)
        desc.attrs_fn(address)
    # month-to-date sensors depend on today's date; everything else must have a value
    assert all(k.endswith("_this_month") for k in missing), missing


def test_cookie_parsing_and_export():
    from custom_components.energiedirect.api import (
        EnergiedirectClient,
        parse_cookie_string,
    )

    assert parse_cookie_string("Cookie: a=1; b=x=y;\n c = 3 ") == {"a": "1", "b": "x=y", "c": "3"}

    async def run():
        client = EnergiedirectClient("access_token-0=abc; did=xyz")
        try:
            return client.export_cookies()
        finally:
            await client.close()

    import asyncio

    assert asyncio.run(run()) == "access_token-0=abc; did=xyz"


def test_statistic_metadata_has_required_keys():
    from homeassistant.components.recorder.models import StatisticMetaData

    meta = statistics._metadata("energiedirect:gas_cost_0001", "x", "EUR", None)
    assert StatisticMetaData.__required_keys__ <= set(meta)
    assert statistics.statistic_id("electricity", "consumption", "0701") == (
        "energiedirect:electricity_consumption_0701"
    )


def test_dynamic_price_sensors():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from custom_components.energiedirect.parsers import parse_dynamic_prices
    from custom_components.energiedirect.sensor import DYNAMIC_SENSORS

    tz = ZoneInfo("Europe/Amsterdam")
    prices = parse_dynamic_prices(_fixture("dynamic_prices"), tz)
    now = datetime(2026, 11, 17, 0, 30, tzinfo=tz)
    strings = json.loads(
        (ROOT / "custom_components/energiedirect/strings.json").read_text(encoding="utf-8")
    )["entity"]["sensor"]
    values = {}
    for desc in DYNAMIC_SENSORS:
        assert desc.translation_key in strings, desc.key
        slots = prices[desc.kind]
        values[desc.key] = desc.value_fn(slots, now)
        assert values[desc.key] is not None, desc.key
        desc.attrs_fn(slots, now)
    assert values["dynamic_electricity_price"] == 0.3681
    low = values["dynamic_electricity_price_lowest_today"]
    high = values["dynamic_electricity_price_highest_today"]
    assert low <= values["dynamic_electricity_price_average_today"] <= high
    attrs = DYNAMIC_SENSORS[0].attrs_fn(prices["electricity"], now)
    assert len(attrs["today"]) == 24 and len(attrs["tomorrow"]) == 24
